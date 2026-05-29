"""
Router Agent - 路由判断
使用 LLM 语义理解判断问题类型，决定路由到 RAG Agent 还是 Chat Agent。
"""
import json
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableSequence

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

RouteType = Literal["rag", "chat"]

# 路由判断的系统提示词
ROUTER_SYSTEM_PROMPT = """你是一个智能路由判断器。你的任务是判断用户的问题是否需要检索知识库来回答。

需要路由到 RAG（检索知识库）的情况：
- 问题涉及具体文档内容、知识库中的信息
- 问题包含"根据知识库"、"知识库中"、"根据文档"、"文档里写了什么"、"知识库中有哪些"等短语
- 问题需要引用特定资料或数据来回答
- 问题关于之前上传的文档内容
- 问题询问某个概念在知识库中的定义、流程、说明（如"知识库中rag流程是什么"）
- 用户明确提到了"知识库"这个词，且问题与知识库中的内容相关

需要路由到 Chat（纯对话）的情况：
- 日常闲聊、问候、自我介绍
- 一般性知识问答（不需要特定文档的常识性问题，且没有提到"知识库"）
- 创意写作、头脑风暴
- 代码编写、翻译、数学计算等通用任务
- 询问天气、时间、新闻等实时信息（虽然我无法获取，但属于对话范畴）

重要判断原则：
- 如果用户的问题中明确提到了"知识库"或"根据知识库"，这强烈表明用户期望从知识库中检索信息，应该路由到 RAG
- 即使问题是在询问某个概念（如"什么是RAG"），只要用户加了"根据知识库"前缀，就说明他想看自己知识库里的相关内容，应该路由到 RAG

请只返回一个单词："rag" 或 "chat"，不要返回其他任何内容。"""

ROUTER_HUMAN_TEMPLATE = """用户问题：{question}

请判断这个问题应该路由到 RAG Agent（检索知识库）还是 Chat Agent（纯对话）。"""


class RouterAgent:
    """路由 Agent，使用 LLM 语义理解判断问题类型"""

    def __init__(self):
        if not DEEPSEEK_API_KEY:
            raise ValueError(
                "DEEPSEEK_API_KEY is not set. "
                "Please configure it in .env file."
            )

        self.llm = ChatOpenAI(
            model=DEEPSEEK_MODEL,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_API_BASE,
            temperature=0.1,  # 低温度，保证判断的确定性
            max_tokens=10,    # 只需要返回一个单词
        )

        # 构建路由 prompt 链
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", ROUTER_SYSTEM_PROMPT),
            ("human", ROUTER_HUMAN_TEMPLATE),
        ])

        self.chain: RunnableSequence = self.prompt | self.llm | StrOutputParser()

        logger.info(f"RouterAgent initialized with model: {DEEPSEEK_MODEL}")

    async def route(self, message: str, knowledge_base_id: str | None = None) -> RouteType:
        """
        判断问题应该路由到 RAG Agent 还是 Chat Agent。

        使用 LLM 语义理解判断：
        - 如果指定了知识库，在 prompt 中额外提示
        - 否则根据问题语义判断

        Args:
            message: 用户问题
            knowledge_base_id: 知识库 ID（可选）

        Returns:
            "rag" 或 "chat"
        """
        try:
            # 构建带上下文的判断
            context_question = message
            if knowledge_base_id:
                context_question = (
                    f"[用户已选择知识库(ID: {knowledge_base_id})]\n"
                    f"用户问题：{message}"
                )

            result = await self.chain.ainvoke({"question": context_question})
            result = result.strip().lower()

            # 验证返回结果
            if result in ("rag", "chat"):
                logger.info(f"Router decision: {result} (question: {message[:50]}...)")
                return result  # type: ignore

            # 如果返回了意外结果，记录警告并降级
            logger.warning(
                f"Router returned unexpected result: '{result}', "
                f"defaulting based on knowledge_base_id"
            )

        except Exception as e:
            logger.error(f"Router LLM call failed: {e}, falling back to default logic")

        # 降级策略：有 knowledge_base_id 则 rag，否则 chat
        if knowledge_base_id:
            logger.info(f"Router fallback: rag (knowledge_base_id provided)")
            return "rag"
        logger.info(f"Router fallback: chat (no knowledge_base_id)")
        return "chat"


# 全局单例
_router_agent: RouterAgent | None = None


def get_router() -> RouterAgent:
    """获取 RouterAgent 单例"""
    global _router_agent
    if _router_agent is None:
        _router_agent = RouterAgent()
    return _router_agent
