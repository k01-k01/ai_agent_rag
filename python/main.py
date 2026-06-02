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
from config import get_current_llm_config, update_llm_config
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
    核心聊天流式生成逻辑（基于 LangGraph Agent）
    1. 检查二级缓存（语义匹配）
    2. 未命中时：LangGraph Agent 自主决策（检索知识库 or 直接回答）
    3. Agent 流式输出思考过程和回答
    4. 保存对话历史到数据库
    5. 异步写入两级缓存
    """
    from modules.agents.agent import get_agent
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

    logger.debug(f"L2 cache MISS for message: '{message[:50]}...', proceeding to Agent")

    # 发送 conversation_id 事件
    yield {
        "event": "conversation_id",
        "data": json.dumps({"type": "conversation_id", "content": conversation_id}),
    }

    # ========== LangGraph Agent 自主决策 ==========
    agent = get_agent()
    full_response = ""
    sources_data = None
    agent_type = "chat"  # 默认类型

    try:
        async for event in agent.stream(message, knowledge_base_id, conversation_id):
            event_type = event.get("type")

            if event_type == "thinking":
                # LLM 的真实思考过程（决定调用工具前的推理）
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "thinking", "content": event["content"]}),
                }

            elif event_type == "tool_call":
                # Agent 决定调用工具
                tool_name = event.get("tool", "")
                tool_args = event.get("args", {})

                # 转发 tool_call 事件给前端（用于展示工具调用记录）
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "type": "tool_call",
                        "content": tool_name,
                        "args": tool_args,
                    }),
                }

                if tool_name == "retrieve_knowledge":
                    agent_type = "rag"
                    yield {
                        "event": "message",
                        "data": json.dumps({"type": "agent", "content": "🔧 retrieve_knowledge"}),
                    }
                elif tool_name == "summarize_document":
                    agent_type = "rag"
                    yield {
                        "event": "message",
                        "data": json.dumps({"type": "agent", "content": "📝 summarize_document"}),
                    }
                elif tool_name == "generate_questions":
                    agent_type = "rag"
                    yield {
                        "event": "message",
                        "data": json.dumps({"type": "agent", "content": "❓ generate_questions"}),
                    }
                elif tool_name == "get_current_datetime":
                    yield {
                        "event": "message",
                        "data": json.dumps({"type": "agent", "content": "🕐 get_current_datetime"}),
                    }

            elif event_type == "tool_result":
                # 工具执行结果（不直接发送给前端，Agent 会基于此生成回答）
                pass

            elif event_type == "evaluation":
                # LLM 对工具结果的评估思考（展示给前端看 Agent "评估了什么"）
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "evaluation", "content": event["content"]}),
                }

            elif event_type == "observation":
                # Agent 观察工具执行结果（展示给前端看 Agent "看到了什么"）
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "observation", "content": event["content"]}),
                }

            elif event_type == "sources":
                # 检索来源信息
                sources_data = event.get("content")
                # sources_data 是 Python 对象（由 agent.py 中的 json.loads 解析）
                # 序列化为 JSON 字符串，前端通过 JSON.parse(event.content) 解析为数组
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "type": "sources",
                        "content": json.dumps(sources_data, ensure_ascii=False),
                    }),
                }
                logger.info(f"Sent {len(sources_data)} sources to frontend")



            elif event_type == "text":
                # Agent 生成的文本内容
                content = event["content"]
                full_response += content
                yield {
                    "event": "message",
                    "data": json.dumps({"type": "text", "content": content}),
                }

            elif event_type == "error":
                # Agent 出错
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "type": "error",
                        "content": event["content"],
                    }),
                }
                return

        # ========== 保存对话历史到数据库（批量操作，单次事务） ==========
        if full_response:
            await conv_manager.add_messages_batch(conversation_id, [
                {"role": "user", "content": message},
                {"role": "assistant", "content": full_response,
                 "agent_type": agent_type, "sources": sources_data},
            ])

            # ========== 异步写入两级缓存（受信号量控制并发） ==========
            asyncio.create_task(
                _cached_write_with_semaphore(
                    semantic_cache.set_cached_answer(
                        message, full_response, agent_type, sources_data, knowledge_base_id
                    )
                )
            )
            asyncio.create_task(
                _cached_write_with_semaphore(
                    semantic_cache.notify_java_set_cache(
                        message, full_response, agent_type, sources_data, knowledge_base_id
                    )
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
    
    if not knowledge_base_id:
        return {"error": "knowledge_base_id is required"}

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


# ==================== 缓存管理 API ====================


@app.post("/api/cache/l2/clear")
async def clear_l2_cache():
    """
    清空所有缓存（一级 Redis + 二级 pgvector）。
    先清空 Java 端的一级缓存（Redis），再清空二级缓存（pgvector cache_entries 表），
    确保清空期间进入的请求不会命中任何缓存，一定会走 Agent 重新生成。
    """
    from db_pool import get_db_pool
    import config as _config

    l1_cleared = False
    l2_cleared = False
    errors = []

    try:
        # 第一步：先清空 Java 端的一级缓存（Redis），避免后续请求命中 L1
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(f"{_config.JAVA_CHAT_SERVICE_URL}/api/chat/cache/l1/clear")
                if response.status_code == 200:
                    logger.info("Java L1 cache cleared successfully")
                    l1_cleared = True
                else:
                    err_msg = f"Java L1 cache clear returned status={response.status_code}"
                    logger.warning(err_msg)
                    errors.append(err_msg)
        except Exception as notify_err:
            err_msg = f"Failed to notify Java to clear L1 cache: {notify_err}"
            logger.warning(err_msg)
            errors.append(err_msg)

        # 第二步：再清空二级缓存（pgvector），确保后续请求到 Python 端也不会命中 L2
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM cache_entries")
            logger.info("L2 cache (pgvector) cleared successfully")
            l2_cleared = True

        if l1_cleared and l2_cleared:
            return {"success": True, "message": "一级缓存（Redis）和二级缓存（pgvector）已全部清空"}
        else:
            detail = "缓存清空不完全"
            if not l1_cleared:
                detail += "，一级缓存（Redis）清空失败"
            if not l2_cleared:
                detail += "，二级缓存（pgvector）清空失败"
            if errors:
                detail += f"：{'；'.join(errors)}"
            raise HTTPException(status_code=500, detail=detail)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        raise HTTPException(status_code=500, detail=f"清空缓存失败: {str(e)}")


# ==================== LLM 配置 API ====================


@app.get("/api/llm/config")
async def get_llm_config():
    """
    获取当前 LLM 配置信息（provider 名称和模型名）。
    """
    config = get_current_llm_config()
    return {
        "provider": config["provider"],
        "model": config["model"],
    }


@app.put("/api/llm/config")
async def update_llm_config_api(request: dict):
    """
    更新 DeepSeek LLM 配置。
    请求体: {
        "api_key": "可选的新 API Key",
        "api_base": "可选的新 API Base URL",
        "model": "可选的新 Model 名称"
    }
    """
    api_key = request.get("api_key")
    api_base = request.get("api_base")
    model = request.get("model")
    
    # 至少需要提供一个要更新的字段
    if api_key is None and api_base is None and model is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'api_key', 'api_base', or 'model' must be provided."
        )
    
    try:
        config = update_llm_config(api_key, api_base, model)
        logger.info(f"LLM config updated: model={config['model']}, api_base={config['api_base']}")
        return {
            "success": True,
            "provider": config["provider"],
            "model": config["model"],
            "message": "DeepSeek 配置已更新"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
