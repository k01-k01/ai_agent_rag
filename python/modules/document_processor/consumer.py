"""
Redis Streams 消费者 - 监听 Java document-service 发送的文档处理任务
实现文档解析、语义切分、向量化、索引构建完整流程

优化（2026-06-02）：
1. 多文档并行处理：一次读取多条消息，使用 asyncio.gather 并发处理
2. 文档入库与目录生成真正并行：解析文档后，使用 asyncio.gather 同时启动入库和目录生成两个独立任务
3. 引入并发信号量，控制最大并发数避免资源耗尽
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
from modules.document_processor.summarizer_v6 import generate_document_guide

logger = logging.getLogger(__name__)

# Redis Stream key
STREAM_KEY = "documents:processing"
# Consumer group
GROUP_NAME = "python-doc-processor"
CONSUMER_NAME = "consumer-1"

# 并发控制：最大同时处理的文档数
# BGE-M3 模型批量推理本身有 batch_size 控制，但多个文档同时处理仍会竞争 GPU/CPU 资源
# 设为 3 可在吞吐和资源消耗之间取得平衡
MAX_CONCURRENT_DOCUMENTS = 3
_doc_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOCUMENTS)

# 每次从 Redis Stream 批量读取的消息数
BATCH_SIZE = 5


async def _update_document_summary(doc_id: str, content: str, file_name: str, file_type: str, file_path: str) -> None:
    """
    后台任务：异步生成文档目录并更新到数据库。
    与主流程解耦，不阻塞文档入库完成。
    独立更新 toc_status 字段，与 status 互不干扰。
    """
    db_pool = await get_db_pool()
    try:
        # 标记目录生成中
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET toc_status = 'processing' WHERE id = $1",
                doc_id,
            )
        logger.info(f"TOC generation started for document {doc_id}")

        summary = await generate_document_guide(
            content=content,
            file_name=file_name,
            file_type=file_type,
            file_path=file_path,
        )
        logger.info(f"TOC generated for document {doc_id}: {len(summary)} chars")

        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET summary = $1, toc_status = 'completed' WHERE id = $2",
                summary, doc_id,
            )
        logger.info(f"TOC saved to database for document {doc_id}, toc_status=completed")
    except Exception as e:
        logger.warning(f"TOC generation failed for document {doc_id}: {e}")
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE documents SET toc_status = 'error' WHERE id = $1",
                    doc_id,
                )
            logger.info(f"TOC status set to error for document {doc_id}")
        except Exception as db_err:
            logger.error(f"Failed to update toc_status for {doc_id}: {db_err}")


async def _indexing_task(doc_id: str, kb_id: str, file_name: str, file_type: str, file_path: str, content: str) -> dict:
    """
    任务A：文档入库任务 - 切分 → 向量化 → 存库 → status=completed
    与目录生成任务完全独立并行执行
    """
    db_pool = await get_db_pool()
    try:
        # 1. 语义切分
        metadata = {
            "document_id": doc_id,
            "knowledge_base_id": kb_id,
            "file_name": file_name,
            "file_type": file_type,
        }
        chunks = chunk_document(content, metadata=metadata)
        logger.info(f"[Indexing] Document {doc_id} split into {len(chunks)} chunks")

        if not chunks:
            raise ValueError("No chunks generated from document")

        # 2. 生成向量嵌入（批量）- 在线程池中执行，避免阻塞事件循环
        # BGE-M3 的 model.encode() 是同步 PyTorch 推理，会阻塞事件循环
        # 使用 run_in_executor 将其放到默认线程池执行，让其他协程可以并行运行
        chunk_texts = [chunk["content"] for chunk in chunks]
        loop = asyncio.get_event_loop()
        embeddings = await loop.run_in_executor(None, generate_embeddings_batch, chunk_texts)
        logger.info(f"[Indexing] Generated {len(embeddings)} embeddings for document {doc_id}")

        # 3. 批量存入 chunks 表 + 更新文档状态为 completed
        async with db_pool.acquire() as conn:
            chunk_records = []
            for chunk, embedding in zip(chunks, embeddings):
                chunk_meta = json.dumps(chunk["metadata"], ensure_ascii=False)
                embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
                chunk_records.append((kb_id, doc_id, chunk["content"], chunk_meta, embedding_str))

            await conn.executemany(
                """
                INSERT INTO chunks (knowledge_base_id, document_id, content, metadata, embedding)
                VALUES ($1, $2, $3, $4::jsonb, $5::vector)
                """,
                chunk_records,
            )

            await conn.execute(
                "UPDATE documents SET status = 'completed' WHERE id = $1",
                doc_id,
            )

        logger.info(f"[Indexing] Document {doc_id} completed: {len(chunks)} chunks indexed")
        return {"status": "completed", "document_id": doc_id, "chunks_count": len(chunks)}

    except Exception as e:
        logger.error(f"[Indexing] Failed for document {doc_id}: {e}")
        try:
            db_pool = await get_db_pool()
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE documents SET status = 'error' WHERE id = $1",
                    doc_id,
                )
        except Exception as db_err:
            logger.error(f"[Indexing] Failed to update error status for {doc_id}: {db_err}")
        return {"status": "error", "document_id": doc_id, "error": str(e)}


async def process_document_task(task: dict) -> dict:
    """
    处理单个文档任务 - 真正的并行执行：

    1. 解析文档内容
    2. 同时并行启动两个独立任务：
       ├─ 任务A：切分 → 向量化 → 存库 → status=completed
       └─ 任务B：生成目录 → toc_status=completed
    3. 两个任务各自独立更新状态，互不干扰

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
        # 1. 更新文档状态为 processing（同时初始化 toc_status 为 processing）
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE documents SET status = 'processing', toc_status = 'processing' WHERE id = $1",
                doc_id,
            )

        # 2. 解析文档内容（这是前置步骤，两个任务都依赖解析结果）
        content = parse_text(file_path, file_type)
        logger.info(f"Document {doc_id} parsed, content length: {len(content)} characters")

        if not content or not content.strip():
            raise ValueError("Document content is empty after parsing")

        # 3. 真正并行执行：同时启动入库任务和目录生成任务
        # 使用 asyncio.gather 让两个任务完全独立并行，各自更新自己的状态
        indexing_coro = _indexing_task(doc_id, kb_id, file_name, file_type, file_path, content)
        toc_coro = _update_document_summary(doc_id, content, file_name, file_type, file_path)

        results = await asyncio.gather(
            indexing_coro,
            toc_coro,
            return_exceptions=True,
        )

        # 汇总结果
        indexing_result = results[0]
        toc_result = results[1]

        if isinstance(indexing_result, Exception):
            logger.error(f"Document {doc_id} indexing failed: {indexing_result}")
            return {"status": "error", "document_id": doc_id, "error": str(indexing_result)}

        if isinstance(toc_result, Exception):
            logger.warning(f"Document {doc_id} TOC generation failed (indexing succeeded): {toc_result}")

        logger.info(f"Document {doc_id} fully processed: indexing={indexing_result.get('status')}, toc={'completed' if not isinstance(toc_result, Exception) else 'error'}")
        return indexing_result

    except Exception as e:
        logger.error(f"Failed to process document {doc_id}: {e}")
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

    优化说明：
    - 使用 BATCH_SIZE（=5）批量读取消息，减少网络往返
    - 使用 asyncio.gather 并发处理多个文档，提高吞吐量
    - 使用 _doc_semaphore 控制最大并发数，避免资源耗尽
    - 批量确认消息（xack），减少 ACK 开销
    """
    redis = aioredis.from_url(f"redis://{config.REDIS_HOST}:{config.REDIS_PORT}")

    try:
        # 确保 stream 存在：先检查 key 是否存在，不存在则创建空 stream
        stream_exists = await redis.exists(STREAM_KEY)
        if not stream_exists:
            # 发送一个空消息来创建 stream
            await redis.xadd(STREAM_KEY, {"init": "1"}, maxlen=1)
            logger.info(f"Created stream '{STREAM_KEY}' with init message")

        # 创建消费者组（如果不存在）
        try:
            await redis.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        except aioredis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        logger.info(
            f"Starting document processing consumer on stream '{STREAM_KEY}', "
            f"batch_size={BATCH_SIZE}, max_concurrent={MAX_CONCURRENT_DOCUMENTS}"
        )

        while True:
            try:
                # 从 stream 批量读取消息
                results = await redis.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={STREAM_KEY: ">"},
                    count=BATCH_SIZE,
                    block=5000,  # 5秒阻塞等待
                )

                if not results:
                    continue

                for stream_name, messages in results:
                    if not messages:
                        continue

                    # 解析所有消息为 (message_id, task) 对
                    pending_tasks = []
                    message_ids = []

                    for message_id, fields in messages:
                        try:
                            task = {}
                            for key, value in fields.items():
                                task[key.decode()] = json.loads(value.decode())

                            # 跳过初始化消息（用于创建 stream 的占位消息）
                            if "init" in task:
                                logger.info(f"Skipping init message {message_id}")
                                await redis.xack(STREAM_KEY, GROUP_NAME, message_id)
                                continue

                            pending_tasks.append(task)
                            message_ids.append(message_id)
                        except Exception as e:
                            logger.error(f"Failed to parse message {message_id}: {e}")
                            # 解析失败的消息单独确认，避免阻塞后续消息
                            await redis.xack(STREAM_KEY, GROUP_NAME, message_id)

                    if not pending_tasks:
                        continue

                    logger.info(f"Batch received {len(pending_tasks)} messages, starting concurrent processing")

                    # 并发处理所有文档（受信号量控制）
                    async def _process_with_semaphore(task):
                        async with _doc_semaphore:
                            return await process_document_task(task)

                    results_list = await asyncio.gather(
                        *[_process_with_semaphore(task) for task in pending_tasks],
                        return_exceptions=True,
                    )

                    # 记录处理结果
                    success_count = 0
                    for i, result in enumerate(results_list):
                        if isinstance(result, Exception):
                            logger.error(f"Document processing failed: {result}")
                        elif result.get("status") == "completed":
                            success_count += 1

                    # 批量确认所有已处理的消息
                    if message_ids:
                        await redis.xack(STREAM_KEY, GROUP_NAME, *message_ids)
                        logger.info(
                            f"Batch completed: {success_count}/{len(pending_tasks)} succeeded, "
                            f"{len(pending_tasks) - success_count} failed, "
                            f"{len(message_ids)} messages acknowledged"
                        )

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
