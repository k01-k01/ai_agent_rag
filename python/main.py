import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 文档处理 consumer 的全局 task 引用
_consumer_task: asyncio.Task | None = None

# 异步任务并发控制信号量
_cache_semaphore = asyncio.Semaphore(10)


async def _cached_write_with_semaphore(coro):
    """在信号量控制下执行缓存写入协程"""
    async with _cache_semaphore:
        await coro


def _keyword_pre_check(message: str, knowledge_base_id: str | None = None) -> str | None:
    """
    关键词前置检查（快速路径，约 1ms）
    
    在调用 LLM 路由之前，先通过关键词匹配快速判断路由类型。
    
    Args:
        message: 用户消息
        knowledge_base_id: 知识库 ID（可选）
    
    Returns:
        "rag" 或 "chat"（关键词匹配成功），None（无法确定，需要 LLM 判断）
    """
    # 问候语 → 直接返回 chat
    greetings = ["你好", "您好", "hi", "hello", "hey", "嗨", "早上好", "下午好", "晚上好", "再见", "拜拜", "bye"]
    message_lower = message.lower().strip()
    if any(greeting in message_lower for greeting in greetings) and len(message) < 20:
        return "chat"
    
    # 明确的知识库关键词 → 直接返回 rag
    rag_keywords = [
        "根据知识库", "知识库中", "知识库里的", "在知识库",
        "根据文档", "文档中", "文档里的", "在文档",
        "检索知识库", "搜索知识库", "查询知识库",
        "知识库", "文档内容",
    ]
    if any(keyword in message_lower for keyword in rag_keywords):
        return "rag"
    
    # 无法确定，需要 LLM 判断
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 生命周期管理：启动时预加载模型 + 启动 Redis Streams 消费者，关闭时取消。
    """
    global _consumer_task

    # ========== 预加载 AI 模型（避免首次请求时实时加载） ==========
    # 1. 预加载 BGE-M3 嵌入模型
    try:
        from modules.document_processor.embedder import _get_model
        logger.info("Preloading BGE-M3 embedding model...")
        # 在子线程中执行，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _get_model)
        logger.info("BGE-M3 embedding model preloaded successfully")
    except Exception as e:
        logger.error(f"Failed to preload BGE-M3 model: {e}")

    # 2. 预加载 Reranker 模型
    try:
        from modules.retrieval.hybrid_retriever import _get_reranker_model
        logger.info("Preloading Reranker model...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _get_reranker_model)
        logger.info("Reranker model preloaded successfully")
    except Exception as e:
        logger.error(f"Failed to preload Reranker model: {e}")

    # 启动文档处理消费者
    try:
        from modules.document_processor.consumer import start_consumer
        _consumer_task = asyncio.create_task(start_consumer())
        logger.info("Document processing consumer started")
    except Exception as e:
        logger.error(f"Failed to start document consumer: {e}")

    yield

    # 关闭消费者
    if _consumer_task:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            logger.info("Document processing consumer cancelled")


app = FastAPI(
    title="RAG Python Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "python-backend"}


async def _stream_chat_response(
    message: str,
    knowledge_base_id: str | None = None,
    conversation_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    核心聊天流式生成逻辑
    1. 检查二级缓存（语义匹配）
    2. 未命中时：路由判断（rag / chat）
    3. 调用对应 Agent 生成流式回答
    4. 保存对话历史到数据库
    5. 异步写入两级缓存
    """
    from modules.agents.router import get_router
    from modules.agents.chat_agent import get_chat_agent
    from modules.agents.rag_agent import get_rag_agent
    from modules.cache.semantic_cache import get_semantic_cache
    from modules.conversation.manager import get_conversation_manager

    conv_manager = get_conversation_manager()

    # 获取或创建 conversation_id
    is_new_conversation = False
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        is_new_conversation = True

    # 获取对话历史（从数据库）
    conv_data = await conv_manager.get_conversation(conversation_id)
    history = []
    if conv_data:
        # 从数据库消息中构建历史记录
        for msg in conv_data.get("messages", []):
            history.append({
                "role": msg["role"],
                "content": msg["content"],
            })
    else:
        # 对话不存在，创建新对话
        is_new_conversation = True

    # 如果是新对话，先创建对话记录（标题用第一条消息的前30字）
    if is_new_conversation:
        title = message[:30] + ("..." if len(message) > 30 else "")
        created_id = await conv_manager.create_conversation(title)
        if created_id:
            conversation_id = created_id
            logger.info(f"Created new conversation: {conversation_id}, title: {title}")

    # ========== 二级缓存检查（pgvector 语义匹配） ==========
    semantic_cache = get_semantic_cache()
    cached_entry = await semantic_cache.find_similar_question(message, knowledge_base_id)

    if cached_entry is not None:
        # 从缓存中获取之前存储的 agent_type，默认为 "chat"
        cached_agent_type = cached_entry.get("agent_type", "chat") or "chat"
        cached_sources = cached_entry.get("sources")
        logger.info(
            f"L2 cache HIT! similarity={cached_entry['similarity']:.4f}, "
            f"agent_type={cached_agent_type}, "
            f"has_sources={cached_sources is not None}, "
            f"returning cached answer (length: {len(cached_entry['answer'])})"
        )

        # ========== 异步通知 Java 写入 L1 缓存（Redis，按知识库 ID 隔离） ==========
        asyncio.create_task(
            _cached_write_with_semaphore(
                semantic_cache.notify_java_set_cache(
                    message,
                    cached_entry["answer"],
                    cached_agent_type,
                    cached_sources,
                    knowledge_base_id,
                )
            )
        )

        # 发送 agent 类型事件
        yield {"event": "agent", "data": json.dumps({"type": "agent", "content": cached_agent_type})}

        # 发送 conversation_id 事件
        yield {
            "event": "conversation_id",
            "data": json.dumps({"type": "conversation_id", "content": conversation_id}),
        }

        # 发送缓存命中提示
        cache_hint = "📦 [命中二级缓存 - 语义匹配]\n\n"
        yield {"event": "message", "data": json.dumps({"type": "text", "content": cache_hint})}

        # 模拟流式输出缓存答案
        async for event in semantic_cache.simulate_stream_from_cache(cached_entry["answer"]):
            yield event

        # 保存到数据库（批量操作，单次事务）
        await conv_manager.add_messages_batch(conversation_id, [
            {"role": "user", "content": message},
            {"role": "assistant", "content": cached_entry["answer"],
             "agent_type": cached_agent_type, "sources": cached_sources},
        ])

        return

    logger.debug(f"L2 cache MISS for message: '{message[:50]}...', proceeding to router")

    # ========== 关键词前置检查（快速路径，约 1ms） ==========
    # 先做关键词匹配，命中则直接确定路由，避免调用 LLM
    route_type = _keyword_pre_check(message, knowledge_base_id)

    # 关键词检查无法确定时，才调用 LLM 路由判断
    if route_type is None:
        try:
            router = get_router()
            route_type = await router.route(message, knowledge_base_id)
        except Exception as e:
            logger.error(f"Router error: {e}, defaulting to chat")
            route_type = "chat"
    else:
        logger.info(f"Keyword pre-check determined route: {route_type}")

    # 发送 agent 类型事件
    yield {"event": "agent", "data": json.dumps({"type": "agent", "content": route_type})}

    # 发送 conversation_id 事件
    yield {
        "event": "conversation_id",
        "data": json.dumps({"type": "conversation_id", "content": conversation_id}),
    }

    try:
        if route_type == "rag":
            agent = get_rag_agent()
            full_response = ""
            sources_data = None
            async for chunk in agent.stream_chat(message, knowledge_base_id, history):
                if chunk.startswith("__SOURCES__:"):
                    sources_json = chunk[len("__SOURCES__:"):].strip()
                    try:
                        sources_data = json.loads(sources_json)
                        yield {
                            "event": "sources",
                            "data": json.dumps({"type": "sources", "content": sources_json}),
                        }
                        logger.info(f"Sent {len(sources_data)} sources to frontend")
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse sources JSON: {e}")
                    continue

                if chunk.startswith("🔍") or chunk.startswith("✅"):
                    yield {"event": "message", "data": json.dumps({"type": "text", "content": chunk})}
                    continue

                full_response += chunk
                yield {"event": "message", "data": json.dumps({"type": "text", "content": chunk})}
        else:
            agent = get_chat_agent()
            full_response = ""
            async for chunk in agent.stream_chat(message, history):
                full_response += chunk
                yield {"event": "message", "data": json.dumps({"type": "text", "content": chunk})}

        # ========== 保存对话历史到数据库（批量操作，单次事务） ==========
        await conv_manager.add_messages_batch(conversation_id, [
            {"role": "user", "content": message},
            {"role": "assistant", "content": full_response,
             "agent_type": route_type, "sources": sources_data if route_type == "rag" else None},
        ])

        # ========== 异步写入两级缓存（受信号量控制并发） ==========
        if full_response:
            rag_sources = sources_data if route_type == "rag" else None
            asyncio.create_task(
                _cached_write_with_semaphore(
                    semantic_cache.set_cached_answer(message, full_response, route_type, rag_sources, knowledge_base_id)
                )
            )
            asyncio.create_task(
                _cached_write_with_semaphore(
                    semantic_cache.notify_java_set_cache(message, full_response, route_type, rag_sources, knowledge_base_id)
                )
            )

        # 发送完成事件
        yield {"event": "done", "data": json.dumps({"type": "done"})}

    except Exception as e:
        logger.error(f"Chat generation error: {e}")
        yield {
            "event": "error",
            "data": json.dumps({"type": "error", "content": f"生成回答时出错: {str(e)}"}),
        }


@app.get("/api/chat/stream")
async def chat_stream(
    message: str = Query(..., description="用户消息"),
    knowledge_base_id: str | None = Query(None, description="知识库ID"),
    conversation_id: str | None = Query(None, description="对话ID"),
):
    """
    SSE 流式聊天接口
    前端通过 EventSource 或 fetch + ReadableStream 调用
    """
    return EventSourceResponse(
        _stream_chat_response(message, knowledge_base_id, conversation_id)
    )


@app.post("/api/chat/stream")
async def chat_stream_post(
    request: dict,
):
    """
    POST 方式的 SSE 流式聊天接口
    请求体: {"message": "...", "knowledge_base_id": "...", "conversation_id": "..."}
    """
    message = request.get("message", "")
    knowledge_base_id = request.get("knowledge_base_id")
    conversation_id = request.get("conversation_id")

    if not message:
        return {"error": "message is required"}

    return EventSourceResponse(
        _stream_chat_response(message, knowledge_base_id, conversation_id)
    )


# ==================== 对话管理 API ====================


@app.get("/api/conversations")
async def list_conversations():
    """
    获取所有对话列表，按更新时间倒序排列。
    """
    from modules.conversation.manager import get_conversation_manager
    conv_manager = get_conversation_manager()
    conversations = await conv_manager.list_conversations()
    return {"conversations": conversations}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """
    获取单个对话及其所有消息。
    """
    from modules.conversation.manager import get_conversation_manager
    conv_manager = get_conversation_manager()
    conv_data = await conv_manager.get_conversation(conversation_id)
    if not conv_data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv_data


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """
    删除对话（级联删除所有消息）。
    """
    from modules.conversation.manager import get_conversation_manager
    conv_manager = get_conversation_manager()
    success = await conv_manager.delete_conversation(conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True, "message": "Conversation deleted"}


@app.post("/api/conversations/save_message")
async def save_conversation_message(request: dict):
    """
    保存对话消息（供 Java 端缓存命中时调用）。
    当一级缓存命中时，Java 端直接返回缓存结果，不会调用 Python 的 stream 接口，
    所以需要这个独立的接口来保存对话历史。

    请求体:
    {
        "conversation_id": "可选，不传则创建新对话",
        "message": "用户消息",
        "answer": "助手回答",
        "agent_type": "rag 或 chat",
        "sources": "可选的检索来源 JSON 字符串"
    }
    """
    from modules.conversation.manager import get_conversation_manager

    conversation_id = request.get("conversation_id")
    message = request.get("message", "")
    answer = request.get("answer", "")
    agent_type = request.get("agent_type", "chat")
    sources_str = request.get("sources")

    if not message or not answer:
        raise HTTPException(status_code=400, detail="message and answer are required")

    conv_manager = get_conversation_manager()

    # 如果没有 conversation_id，创建新对话
    if not conversation_id:
        title = message[:30] + ("..." if len(message) > 30 else "")
        created_id = await conv_manager.create_conversation(title)
        if created_id:
            conversation_id = created_id
            logger.info(f"Created new conversation via save_message: {conversation_id}")
        else:
            raise HTTPException(status_code=500, detail="Failed to create conversation")

    # 解析 sources
    sources = None
    if sources_str:
        try:
            sources = json.loads(sources_str) if isinstance(sources_str, str) else sources_str
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Failed to parse sources: {sources_str}")

    # 保存消息（批量操作，单次事务）
    await conv_manager.add_messages_batch(conversation_id, [
        {"role": "user", "content": message},
        {"role": "assistant", "content": answer, "agent_type": agent_type, "sources": sources},
    ])

    logger.info(f"Saved conversation messages: conv={conversation_id}, agent_type={agent_type}")
    return {"success": True, "conversation_id": conversation_id}


@app.put("/api/conversations/{conversation_id}/title")
async def update_conversation_title(conversation_id: str, request: dict):
    """
    更新对话标题。
    请求体: {"title": "新标题"}
    """
    title = request.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    from modules.conversation.manager import get_conversation_manager
    conv_manager = get_conversation_manager()
    success = await conv_manager.update_title(conversation_id, title)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True, "message": "Title updated"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
