"""
Chat Agent - 纯对话功能
使用 DeepSeek API，支持流式输出和多轮对话
"""
import json
import logging
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)


class ChatAgent:
    """聊天 Agent，使用 DeepSeek API 进行纯对话"""

    def __init__(self):
        if not DEEPSEEK_API_KEY:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. "
                "Please configure it in .env file."
            )
        self.client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_API_BASE,
        )
        self.model = DEEPSEEK_MODEL
        logger.info(f"ChatAgent initialized with model: {self.model}")

    def _build_messages(
        self, message: str, history: list[dict] | None = None
    ) -> list[dict]:
        """构建 OpenAI 格式的 messages"""
        system_prompt = (
            "你是一个友好的AI助手，名叫小R。你是个人RAG知识库系统的智能对话助手。\n\n"
            "你的特点：\n"
            "1. 用中文回答用户的问题，语气自然友好\n"
            "2. 对于日常闲聊、一般性知识问答、创意写作、代码编写等任务，直接回答\n"
            "3. 如果用户询问关于知识库/文档内容的问题，引导用户选择知识库并使用RAG模式\n"
            "4. 回答简洁清晰，避免过于冗长\n"
            "5. 如果不知道答案，如实告知，不要编造信息\n\n"
            "请开始对话吧！"
        )
        messages = [{"role": "system", "content": system_prompt}]

        if history:
            # 只取最近 20 轮对话（40 条消息），避免超出上下文窗口
            recent_history = history[-40:]
            for turn in recent_history:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": message})
        return messages

    async def stream_chat(
        self,
        message: str,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式聊天
        使用 DeepSeek API 的流式接口
        """
        if history is None:
            history = []

        messages = self._build_messages(message, history)

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                max_tokens=2048,
                temperature=0.7,
                top_p=0.9,
            )

            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content

        except Exception as e:
            logger.error(f"DeepSeek API stream error: {e}")
            yield f"\n\n[调用 DeepSeek API 时出错: {str(e)}]"

    async def chat_sync(self, message: str, history: list[dict] | None = None) -> str:
        """同步聊天（非流式）"""
        if history is None:
            history = []

        messages = self._build_messages(message, history)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                max_tokens=2048,
                temperature=0.7,
                top_p=0.9,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error(f"DeepSeek API sync error: {e}")
            return f"[调用 DeepSeek API 时出错: {str(e)}]"


# 全局单例
_chat_agent: ChatAgent | None = None


def get_chat_agent() -> ChatAgent:
    """获取 ChatAgent 单例"""
    global _chat_agent
    if _chat_agent is None:
        _chat_agent = ChatAgent()
    return _chat_agent
