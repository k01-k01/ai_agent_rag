"""
二级语义缓存模块（pgvector 语义匹配）

功能：
1. 使用 BGE-M3 将用户问题向量化
2. 在 pgvector 中检索相似历史问题（余弦相似度 > 0.85）
3. 命中时按每5字符拆分答案小块，流式返回缓存答案
4. 写入缓存（问题和答案对）
"""
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from config import (
    CACHE_SIMILARITY_THRESHOLD,
    JAVA_CHAT_SERVICE_URL,
)
from db_pool import get_db_pool
from modules.document_processor.embedder import generate_embedding

logger = logging.getLogger(__name__)

# 模拟流式输出时每块字符数
CHUNK_SIZE = 5

# 模块级全局 httpx 客户端（复用连接池）
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http_client() -> httpx.AsyncClient:
    """获取或创建复用的 httpx 客户端"""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=5.0)
        logger.info("Created global httpx client for Java cache notification")
    return _http_client


class SemanticCache:
    """二级语义缓存"""

    def __init__(self):
        self.similarity_threshold = CACHE_SIMILARITY_THRESHOLD
        logger.info(f"SemanticCache initialized with threshold: {self.similarity_threshold}")

    async def find_similar_question(self, question: str, knowledge_base_id: str | None = None) -> Optional[dict]:
        """
        在 pgvector 中检索与用户问题语义相似的历史问题。

        Args:
            question: 用户问题
            knowledge_base_id: 知识库 ID（可选），用于按知识库过滤缓存

        Returns:
            如果找到相似度 > threshold 的缓存条目，返回 {"question": ..., "answer": ..., "agent_type": ..., "sources": ..., "similarity": ...}
            否则返回 None
        """
        try:
            # 生成问题向量
            embedding = generate_embedding(question)
            if not embedding or all(v == 0.0 for v in embedding):
                logger.warning("Empty embedding generated for question: {}", question)
                return None

            # 向量格式化为 pgvector 可接受的格式
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            # 查询相似历史问题（按知识库 ID 过滤）
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                if knowledge_base_id:
                    row = await conn.fetchrow(
                        """
                        SELECT question, answer, agent_type, sources,
                               1 - (embedding <=> $1::vector) AS similarity
                        FROM cache_entries
                        WHERE expires_at > NOW()
                          AND (knowledge_base_id = $3::uuid OR knowledge_base_id IS NULL)
                          AND 1 - (embedding <=> $1::vector) >= $2
                        ORDER BY
                            CASE WHEN knowledge_base_id = $3::uuid THEN 0 ELSE 1 END,
                            similarity DESC
                        LIMIT 1
                        """,
                        embedding_str,
                        self.similarity_threshold,
                        knowledge_base_id,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT question, answer, agent_type, sources,
                               1 - (embedding <=> $1::vector) AS similarity
                        FROM cache_entries
                        WHERE expires_at > NOW()
                          AND knowledge_base_id IS NULL
                          AND 1 - (embedding <=> $1::vector) >= $2
                        ORDER BY similarity DESC
                        LIMIT 1
                        """,
                        embedding_str,
                        self.similarity_threshold,
                    )

            if row:
                similarity = float(row["similarity"])
                agent_type = row.get("agent_type", "chat") or "chat"
                sources_raw = row.get("sources")
                sources = None
                if sources_raw:
                    try:
                        sources = json.loads(sources_raw) if isinstance(sources_raw, str) else sources_raw
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(f"Failed to parse sources JSON from cache: {sources_raw}")
                logger.info(
                    f"L2 cache HIT! similarity={similarity:.4f}, "
                    f"agent_type={agent_type}, "
                    f"has_sources={sources is not None}, "
                    f"cached question='{row['question'][:50]}...'"
                )
                return {
                    "question": row["question"],
                    "answer": row["answer"],
                    "agent_type": agent_type,
                    "sources": sources,
                    "similarity": similarity,
                }
            else:
                logger.debug(f"L2 cache MISS for question: '{question[:50]}...'")
                return None

        except Exception as e:
            logger.error(f"Error finding similar question in cache: {e}")
            return None

    async def set_cached_answer(self, question: str, answer: str, agent_type: str = "chat", sources: Optional[list] = None, knowledge_base_id: str | None = None) -> None:
        """
        将问题和答案写入二级缓存（pgvector）。

        Args:
            question: 用户问题
            answer: 完整答案
            agent_type: 生成答案的 agent 类型（'rag' 或 'chat'）
            sources: 检索来源信息列表（可选）
            knowledge_base_id: 知识库 ID（可选），用于按知识库隔离缓存
        """
        if not question or not answer:
            logger.warning("Empty question or answer, skipping cache write")
            return

        try:
            # 生成问题向量
            embedding = generate_embedding(question)
            if not embedding or all(v == 0.0 for v in embedding):
                logger.warning("Empty embedding generated, skipping cache write")
                return

            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            # 将 sources 序列化为 JSON 字符串
            sources_json = json.dumps(sources, ensure_ascii=False) if sources else None

            # 写入数据库
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO cache_entries (question, answer, agent_type, sources, knowledge_base_id, embedding, expires_at)
                    VALUES ($1, $2, $3, $4, $5::uuid, $6::vector, NOW() + INTERVAL '24 hours')
                    """,
                    question,
                    answer,
                    agent_type,
                    sources_json,
                    knowledge_base_id,
                    embedding_str,
                )

            logger.info(
                f"L2 cache SET: question='{question[:50]}...', "
                f"agent_type={agent_type}, has_sources={sources is not None}, "
                f"knowledge_base_id={knowledge_base_id or 'None'}, "
                f"answer length={len(answer)}"
            )

        except Exception as e:
            logger.error(f"Error setting cached answer: {e}")

    async def notify_java_set_cache(self, question: str, answer: str, agent_type: str = "chat", sources: Optional[list] = None, knowledge_base_id: str | None = None) -> None:
        """
        通知 Java chat-service 将答案写入 Redis 一级缓存。

        Args:
            question: 用户问题
            answer: 完整答案
            agent_type: 生成答案的 agent 类型（'rag' 或 'chat'）
            sources: 检索来源信息列表（可选）
            knowledge_base_id: 知识库 ID（可选），用于按知识库隔离一级缓存
        """
        try:
            url = f"{JAVA_CHAT_SERVICE_URL}/api/chat/cache/set"
            payload = {
                "question": question,
                "answer": answer,
                "agent_type": agent_type,
            }
            if sources:
                payload["sources"] = json.dumps(sources, ensure_ascii=False)
            if knowledge_base_id:
                payload["knowledge_base_id"] = knowledge_base_id

            client = await _get_http_client()
            response = await client.post(url, json=payload)

            if response.status_code == 200:
                logger.info(
                    f"Java L1 cache notified successfully "
                    f"(agent_type={agent_type}, has_sources={sources is not None}, "
                    f"knowledge_base_id={knowledge_base_id or 'None'})"
                )
            else:
                logger.warning(
                    f"Java L1 cache notification failed: "
                    f"status={response.status_code}, body={response.text}"
                )

        except Exception as e:
            logger.error(f"Error notifying Java cache: {e}")


    async def simulate_stream_from_cache(
        self, answer: str
    ) -> AsyncGenerator[dict, None]:
        """
        模拟流式输出：将完整答案按每5字符拆分为小块，依次 yield。

        Args:
            answer: 完整答案

        Yields:
            SSE 事件字典，格式为 {"event": "message", "data": json_str}
        """
        if not answer:
            yield {"event": "done", "data": json.dumps({"type": "done"})}
            return

        length = len(answer)
        start = 0

        while start < length:
            end = min(start + CHUNK_SIZE, length)
            chunk = answer[start:end]

            yield {
                "event": "message",
                "data": json.dumps({"type": "text", "content": chunk}),
            }

            start = end

            # 模拟流式输出的微小延迟
            await asyncio.sleep(0.05)

        # 发送完成事件
        yield {"event": "done", "data": json.dumps({"type": "done"})}


# 全局单例
_semantic_cache: Optional[SemanticCache] = None


def get_semantic_cache() -> SemanticCache:
    """获取 SemanticCache 单例"""
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
