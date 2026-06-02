"""
运行 migration_009：给 documents 表增加 toc_status 字段
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_pool import get_db_pool


async def run():
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS toc_status VARCHAR(50) DEFAULT 'pending'"
        )
        print("Migration 009 applied successfully: added toc_status column")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
