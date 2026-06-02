"""
对话管理器 - 对话持久化存储
使用 PostgreSQL 存储对话历史和对话列表，支持 CRUD 操作。
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

# 北京时间 (UTC+8)
BEIJING_TZ = timezone(timedelta(hours=8))

from db_pool import get_db_pool

logger = logging.getLogger(__name__)



class ConversationManager:
    """对话管理器，负责对话的持久化存储"""

    async def list_conversations(self) -> list[dict]:
        """
        获取所有对话列表，按更新时间倒序排列。

        Returns:
            list[dict]: 对话列表，每个元素包含 id, title, created_at, updated_at, message_count
        """
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT c.id, c.title, c.created_at, c.updated_at,
                           COUNT(cm.id) AS message_count
                    FROM conversations c
                    LEFT JOIN conversation_messages cm ON c.id = cm.conversation_id
                    GROUP BY c.id
                    ORDER BY c.updated_at DESC
                    LIMIT 100
                    """
                )
                return [
                    {
                        "id": str(row["id"]),
                        "title": row["title"],
                        "created_at": row["created_at"].replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ).isoformat() if row["created_at"] else None,
                        "updated_at": row["updated_at"].replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ).isoformat() if row["updated_at"] else None,
                        "message_count": row["message_count"],
                    }
                    for row in rows
                ]
        except Exception as e:
            logger.error(f"Error listing conversations: {e}")
            return []

    async def get_conversation(self, conversation_id: str) -> Optional[dict]:
        """
        获取单个对话及其所有消息。

        Args:
            conversation_id: 对话 ID

        Returns:
            dict: 包含对话信息和消息列表，如果不存在返回 None
        """
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                # 获取对话信息
                conv_row = await conn.fetchrow(
                    "SELECT id, title, created_at, updated_at FROM conversations WHERE id = $1",
                    conversation_id,
                )
                if not conv_row:
                    return None

                # 获取消息列表（只加载最近 40 条，覆盖 chat_agent 和 rag_agent 的最大需求）
                msg_rows = await conn.fetch(
                    """
                    SELECT id, role, content, agent_type, sources, created_at
                    FROM conversation_messages
                    WHERE conversation_id = $1
                    ORDER BY created_at ASC, id ASC, CASE WHEN role = 'user' THEN 0 ELSE 1 END ASC
                    LIMIT 40
                    """,
                    conversation_id,
                )

                messages = []
                for row in msg_rows:
                    msg = {
                        "id": str(row["id"]),
                        "role": row["role"],
                        "content": row["content"],
                        "timestamp": row["created_at"].replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ).isoformat() if row["created_at"] else None,
                    }
                    if row["agent_type"]:
                        msg["agent_type"] = row["agent_type"]
                    if row["sources"]:
                        try:
                            msg["sources"] = json.loads(row["sources"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    messages.append(msg)

                return {
                    "id": str(conv_row["id"]),
                    "title": conv_row["title"],
                    "created_at": conv_row["created_at"].replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ).isoformat() if conv_row["created_at"] else None,
                    "updated_at": conv_row["updated_at"].replace(tzinfo=timezone.utc).astimezone(BEIJING_TZ).isoformat() if conv_row["updated_at"] else None,
                    "messages": messages,
                }
        except Exception as e:
            logger.error(f"Error getting conversation {conversation_id}: {e}")
            return None

    async def create_conversation(self, title: str = "新对话") -> Optional[str]:
        """
        创建新对话。

        Args:
            title: 对话标题

        Returns:
            str: 新对话的 ID，失败返回 None
        """
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO conversations (title) VALUES ($1) RETURNING id",
                    title,
                )
                conv_id = str(row["id"])
                logger.info(f"Created conversation: {conv_id}, title: {title}")
                return conv_id
        except Exception as e:
            logger.error(f"Error creating conversation: {e}")
            return None

    async def update_title(self, conversation_id: str, title: str) -> bool:
        """
        更新对话标题。

        Args:
            conversation_id: 对话 ID
            title: 新标题

        Returns:
            bool: 是否成功
        """
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE conversations SET title = $1, updated_at = NOW() WHERE id = $2",
                    title,
                    conversation_id,
                )
                return result == "UPDATE 1"
        except Exception as e:
            logger.error(f"Error updating conversation title {conversation_id}: {e}")
            return False

    async def delete_conversation(self, conversation_id: str) -> bool:
        """
        删除对话（级联删除所有消息）。

        Args:
            conversation_id: 对话 ID

        Returns:
            bool: 是否成功
        """
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM conversations WHERE id = $1",
                    conversation_id,
                )
                return result == "DELETE 1"
        except Exception as e:
            logger.error(f"Error deleting conversation {conversation_id}: {e}")
            return False

    async def add_messages_batch(
        self,
        conversation_id: str,
        messages: list[dict],
    ) -> bool:
        """
        批量添加消息到对话（单次事务）。

        将多条消息合并为一次数据库事务，减少网络往返。
        同时更新对话的 updated_at 时间。

        Args:
            conversation_id: 对话 ID
            messages: 消息列表，每个元素包含:
                - role: str (必需)
                - content: str (必需)
                - agent_type: Optional[str]
                - sources: Optional[list]

        Returns:
            bool: 是否成功
        """
        if not messages:
            logger.warning("Empty messages list, skipping batch insert")
            return False

        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for i, msg in enumerate(messages):
                        sources_json = json.dumps(msg.get("sources"), ensure_ascii=False) if msg.get("sources") else None
                        # 为每条消息显式设置 created_at，确保 user 消息在 assistant 消息之前
                        # 使用 NOW() + i * INTERVAL '1 microsecond' 保证同一批消息的时间戳严格递增
                        await conn.execute(
                            """
                            INSERT INTO conversation_messages (conversation_id, role, content, agent_type, sources, created_at)
                            VALUES ($1, $2, $3, $4, $5, NOW() + ($6 || ' microseconds')::INTERVAL)
                            """,
                            conversation_id,
                            msg["role"],
                            msg["content"],
                            msg.get("agent_type"),
                            sources_json,
                            str(i),  # 第0条（user）用 NOW()，第1条（assistant）用 NOW() + 1微秒
                        )
                    # 批量更新一次 updated_at
                    await conn.execute(
                        "UPDATE conversations SET updated_at = NOW() WHERE id = $1",
                        conversation_id,
                    )
            logger.info(f"Batch added {len(messages)} messages to conversation {conversation_id}")
            return True
        except Exception as e:
            logger.error(f"Error batch adding messages to conversation {conversation_id}: {e}")
            return False


# 全局单例
_manager: Optional[ConversationManager] = None


def get_conversation_manager() -> ConversationManager:
    """获取 ConversationManager 单例"""
    global _manager
    if _manager is None:
        _manager = ConversationManager()
    return _manager
