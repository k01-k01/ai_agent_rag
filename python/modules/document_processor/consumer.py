"""
Redis Streams 消费者 - 监听 Java document-service 发送的文档处理任务
实现文档解析、语义切分、向量化、索引构建完整流程
"""
import json
import asyncio
import logging

import sys
import os

# 确保 python 根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import redis.asyncio as aioredis

import config
from db_pool import get_db_pool
from modules.document_processor.parser import parse_text
from modules.document_processor.chunker import chunk_document
from modules.document_processor.embedder import generate_embeddings_batch
from modules.document_processor.summarizer import generate_document_summary

logger = logging.getLogger(__name__)

# Redis Stream key
STREAM_KEY = "documents:processing"
# Consumer group
GROUP_NAME = "python-doc-processor"
CONSUMER_NAME = "consumer-1"


async def process_document_task(task: dict) -> dict:
    """
    处理单个文档任务 - 完整流程：

    1. 解析文档内容
    2. 语义切分（Semantic Chunking）
    3. 生成向量嵌入（BGE-M3）
    4. 存入 PostgreSQL chunks 表
    5. 更新文档状态

    task 格式（Java 侧发送的 camelCase 字段）：
    {
        "documentId": "uuid",
        "filePath": "/path/to/file.pdf",
        "fileType": "pdf",
        "knowledgeBaseId": "uuid",
        "fileName": "example.pdf"
    }
    """
    doc_id = task.get("documentId")
    file_path = task.get("filePath")
    file_type = task.get("fileType", "")
    kb_id = task.get("knowledgeBaseId")
    file_name = task.get("fileName", "unknown")

    logger.info(f"Processing document: {doc_id}, file: {file_name}")

    db_pool = await get_db_pool()

    try:
        # 1. 更新文档状态为 processing
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET status = 'processing' WHERE id = $1",
                doc_id,
            )

        # 2. 解析文档内容
        content = parse_text(file_path, file_type)
        logger.info(f"Document {doc_id} parsed, content length: {len(content)} characters")

        if not content or not content.strip():
            raise ValueError("Document content is empty after parsing")

        # 2.5 生成文档摘要（使用 DeepSeek API）
        # 摘要生成失败不影响主流程，summary 为 None 时跳过
        summary = None
        try:
            summary = await generate_document_summary(content, file_name)
        except Exception as e:
            logger.warning(f"Summary generation failed for document {doc_id}: {e}")

        # 3. 语义切分
        metadata = {
            "document_id": doc_id,
            "knowledge_base_id": kb_id,
            "file_name": file_name,
            "file_type": file_type,
        }
        chunks = chunk_document(content, metadata=metadata)
        logger.info(f"Document {doc_id} split into {len(chunks)} chunks")

        if not chunks:
            raise ValueError("No chunks generated from document")

        # 4. 生成向量嵌入（批量）
        chunk_texts = [chunk["content"] for chunk in chunks]
        embeddings = generate_embeddings_batch(chunk_texts)
        logger.info(f"Generated {len(embeddings)} embeddings for document {doc_id}")

        # 5. 批量存入 chunks 表
        async with db_pool.acquire() as conn:
            # 准备批量数据
            chunk_records = []
            for chunk, embedding in zip(chunks, embeddings):
                chunk_meta = json.dumps(chunk["metadata"], ensure_ascii=False)
                embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
                chunk_records.append((kb_id, doc_id, chunk["content"], chunk_meta, embedding_str))

            # 批量插入（单次网络往返）
            await conn.executemany(
                """
                INSERT INTO chunks (knowledge_base_id, document_id, content, metadata, embedding)
                VALUES ($1, $2, $3, $4::jsonb, $5::vector)
                """,
                chunk_records,
            )

            # 6. 更新文档状态为 completed，同时存入摘要（如果有）
            if summary:
                await conn.execute(
                    "UPDATE documents SET status = 'completed', summary = $1 WHERE id = $2",
                    summary, doc_id,
                )
            else:
                await conn.execute(
                    "UPDATE documents SET status = 'completed' WHERE id = $1",
                    doc_id,
                )

        logger.info(f"Document {doc_id} processed successfully: {len(chunks)} chunks indexed")
        return {"status": "completed", "document_id": doc_id, "chunks_count": len(chunks)}

    except Exception as e:
        logger.error(f"Failed to process document {doc_id}: {e}")
        # 更新文档状态为 error
        try:
            db_pool = await get_db_pool()
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE documents SET status = 'error' WHERE id = $1",
                    doc_id,
                )
        except Exception as db_err:
            logger.error(f"Failed to update error status for {doc_id}: {db_err}")

        return {"status": "error", "document_id": doc_id, "error": str(e)}


async def start_consumer():
    """
    启动 Redis Streams 消费者，持续监听文档处理任务。
    """
    redis = aioredis.from_url(f"redis://{config.REDIS_HOST}:{config.REDIS_PORT}")

    try:
        # 创建消费者组（如果不存在）
        try:
            await redis.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        logger.info(f"Starting document processing consumer on stream '{STREAM_KEY}'...")

        while True:
            try:
                # 从 stream 读取消息
                results = await redis.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_KEY: ">"},
                    count=1,
                    block=5000,  # 5秒阻塞等待
                )

                if not results:
                    continue

                for stream_name, messages in results:
                    for message_id, fields in messages:
                        logger.info(f"Received message {message_id}: {fields}")
                        try:
                            # 解析任务（Java 侧使用 JSON 序列化，需要反序列化去掉多余引号）
                            task = {}
                            for key, value in fields.items():
                                task[key.decode()] = json.loads(value.decode())
                            # 处理任务
                            await process_document_task(task)
                            # 确认消息
                            await redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                        except Exception as e:
                            logger.error(f"Failed to process message {message_id}: {e}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Consumer loop error: {e}, retrying in 5s...")
                await asyncio.sleep(5)

    except asyncio.CancelledError:
        logger.info("Consumer cancelled, shutting down...")
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(start_consumer())
