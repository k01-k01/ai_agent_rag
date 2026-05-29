"""
为已有文档重新生成摘要 - 遍历所有 status='completed' 的文档，
使用最新的提示词重新解析文档内容并调用 DeepSeek API 生成摘要。

用法：
    conda activate ai-knowledge-system2
    cd d:\project\ai-rag\demo1 - 副本 - 副本 (2) - 副本
    python python/scripts/backfill_summaries.py
"""
import asyncio
import logging
import sys
import os
from dotenv import load_dotenv

# 加载 .env 文件（在项目根目录）
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
load_dotenv(_env_path)

# 确保 python 目录在路径中（脚本在 python/scripts/，需要加入 python/）
_script_dir = os.path.dirname(os.path.abspath(__file__))
_python_dir = os.path.dirname(_script_dir)  # python/
sys.path.insert(0, _python_dir)

import config
from db_pool import get_db_pool
from modules.document_processor.parser import parse_text
from modules.document_processor.summarizer import generate_document_summary

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def backfill_summaries():
    """为所有已完成的文档重新生成摘要"""
    db_pool = await get_db_pool()

    # 查询所有已完成的文档（不管是否已有摘要），用新提示词重新生成
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, file_path, file_type
            FROM documents
            WHERE status = 'completed'
            ORDER BY created_at DESC
            """
        )

    if not rows:
        logger.info("没有已完成的文档。")
        return

    logger.info(f"找到 {len(rows)} 个已完成的文档，将使用最新提示词重新生成摘要")

    success_count = 0
    fail_count = 0

    for row in rows:
        doc_id = row["id"]
        file_name = row["name"]
        file_path = row["file_path"]
        file_type = row["file_type"] or ""

        logger.info(f"正在处理: {file_name} (id={doc_id})")

        try:
            # 解析文档内容
            if not file_path or not os.path.exists(file_path):
                logger.warning(f"文件不存在，跳过: {file_path}")
                fail_count += 1
                continue

            content = parse_text(file_path, file_type)
            if not content or not content.strip():
                logger.warning(f"文档内容为空，跳过: {file_name}")
                fail_count += 1
                continue

            # 生成摘要
            summary = await generate_document_summary(content, file_name)
            if not summary:
                logger.warning(f"摘要生成失败: {file_name}")
                fail_count += 1
                continue

            # 更新数据库
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE documents SET summary = $1 WHERE id = $2",
                    summary, doc_id,
                )

            logger.info(f"✅ 摘要已更新: {file_name}")
            success_count += 1

        except Exception as e:
            logger.error(f"❌ 处理失败 {file_name}: {e}")
            fail_count += 1

    logger.info(f"\n===== 补摘要完成 =====")
    logger.info(f"成功: {success_count}, 失败: {fail_count}, 总计: {len(rows)}")


if __name__ == "__main__":
    asyncio.run(backfill_summaries())
