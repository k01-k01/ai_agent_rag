"""
文档摘要生成模块 - 使用 DeepSeek API 生成文档摘要
在文档入库时同步调用，生成 3-5 个可提问方向的导读
"""
import logging
from openai import AsyncOpenAI

import config

logger = logging.getLogger(__name__)

# 初始化 DeepSeek 异步客户端
_client = None


def _get_client() -> AsyncOpenAI:
    """获取或创建 DeepSeek 客户端（单例模式）"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_API_BASE,
        )
    return _client


# 摘要提示词 - 目的是让用户了解该文档可以问哪些问题
SUMMARY_SYSTEM_PROMPT = "你是一个专业的文档导读助手。你的任务不是概括文档内容，而是分析文档后，告诉用户这个文档涉及哪些可以提问的方向，帮助用户快速了解能问什么问题。"

SUMMARY_USER_PROMPT = """请分析以下文档内容，站在用户提问的角度，生成一份"可提问方向"的导读。

要求：
1. 列出3-5个该文档涉及的可提问方向或关键词
2. 每个方向用一句话说明可以问什么，以"• "开头
3. 重点提炼：文档中涉及的名词术语、流程步骤、操作方法、配置项、概念解释等用户可能会问的内容
4. 语言简洁明了，直接列出，不要额外说明

示例格式：
• 名词解释：可以询问文档中涉及的专业术语含义
• 操作流程：可以询问具体的步骤和操作方法
• 配置参数：可以询问相关的配置项和参数说明

文档标题：{file_name}

文档内容（前8000字）：
{content}

请生成该文档的可提问方向导读（3-5个方向）："""


async def generate_document_summary(content: str, file_name: str) -> str | None:
    """
    使用 DeepSeek API 生成文档摘要。

    Args:
        content: 文档全文内容
        file_name: 文档文件名

    Returns:
        摘要文本（3-5个要点），如果生成失败则返回 None
    """
    # 截取前 8000 字符，避免 token 消耗过大
    truncated_content = content[:8000] if len(content) > 8000 else content

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": SUMMARY_USER_PROMPT.format(
                        file_name=file_name,
                        content=truncated_content,
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=500,
        )
        summary = response.choices[0].message.content.strip()
        logger.info(
            f"Generated summary for '{file_name}': {summary[:80]}..."
        )
        return summary

    except Exception as e:
        logger.error(f"Failed to generate summary for '{file_name}': {e}")
        return None
