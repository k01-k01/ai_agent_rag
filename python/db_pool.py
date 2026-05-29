"""
全局共享数据库连接池

所有模块统一从此模块获取数据库连接，避免各自创建独立连接池。
"""
import logging
from typing import Optional

import asyncpg

from config import DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_db_pool() -> asyncpg.Pool:
    """获取或创建全局共享数据库连接池"""
    global _pool
    if _pool is None:
        dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        _pool = await asyncpg.create_pool(
            dsn,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        logger.info(f"Global database pool created (min=2, max=20)")
    return _pool


async def close_db_pool():
    """关闭全局数据库连接池"""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Global database pool closed")
