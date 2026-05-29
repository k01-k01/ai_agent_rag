"""
RAG Agent - 检索增强生成
实现完整混合检索流程：
1. 全文搜索（pg_trgm）→ Top 50
2. 向量检索（pgvector HNSW）→ Top 50
3. RRF 融合（k=60）
4. Reranker 精排（bge-reranker-v2-m3）→ Top 10
5. LLM 生成回答（DeepSeek API）
"""
import json
import logging
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL
from modules.retrieval.hybrid_retriever import get_hybrid_retriever, ChunkResult

logger = logging.getLogger(__name__)


class RagAgent:
    """RAG Agent，使用知识库检索增强生成"""

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
        self.retriever = get_hybrid_retriever()
        logger.info(f"RagAgent initialized with model: {self.model}")

    def _build_context(self, chunks: list[ChunkResult]) -> str:
        """
        将检索到的 chunks 构建为 LLM 上下文

        Args:
            chunks: 检索到的 Top 10 ChunkResult 列表

        Returns:
            格式化的上下文字符串
        """
        context_parts = []
        for i, chunk in enumerate(chunks, 1):
            context_parts.append(
                f"[来源 {i}: {chunk.knowledge_base_name} / {chunk.document_name}]\n"
                f"{chunk.content}"
            )
        return "\n\n".join(context_parts)

    def _build_rag_prompt(self, message: str, context: str) -> list[dict]:
        """构建 RAG 提示词"""
        system_prompt = (
            '你是一个基于知识库的AI助手。请严格遵守以下规则：\n\n'
            '1. **必须基于上下文回答**：你只能使用下面提供的"上下文信息"来回答问题。\n'
            '2. **上下文相关性检查**：在回答前，先判断上下文信息是否与用户问题相关。\n'
            '3. **不知道就说不知道**：如果上下文中没有足够信息来回答问题，'
            '请直接说"根据提供的知识库内容，无法回答该问题"，不要编造答案。\n'
            '4. **禁止使用自身知识**：严禁使用你预训练阶段学到的知识来回答问题，'
            '只能使用下面提供的上下文信息。\n'
            '5. **引用来源**：回答时请引用具体的来源文档名称。\n\n'
            '上下文信息：\n'
            f'{context}'
        )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message},
        ]

    async def stream_chat(
        self,
        message: str,
        knowledge_base_id: str | None = None,
        history: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式 RAG 回答 - 完整混合检索流程

        1. 全文搜索 + 向量检索
        2. RRF 融合
        3. Reranker 精排
        4. LLM 生成回答

        Args:
            message: 用户问题
            knowledge_base_id: 知识库 ID（可选，为 None 时检索所有知识库）
            history: 对话历史
        """
        yield f"🔍 正在检索知识库...\n\n"

        # ========== 混合检索 ==========
        try:
            top_chunks, sources = await self.retriever.retrieve(
                query=message,
                knowledge_base_id=knowledge_base_id,
            )
        except Exception as e:
            logger.error(f"Hybrid retrieval error: {e}")
            yield f"\n\n[检索过程出错: {str(e)}]"
            return

        if not top_chunks:
            yield "未找到相关文档内容。请尝试其他问题或上传更多文档。\n\n"
            # 即使没有检索结果，也调用 LLM 尝试回答
            context = "未找到相关文档内容。"
        else:
            # 构建上下文
            context = self._build_context(top_chunks)
            yield f"✅ 已检索到 {len(top_chunks)} 个相关片段\n\n"

        # ========== 发送检索来源信息（通过特殊格式让 main.py 识别并转发） ==========
        if sources:
            yield f"__SOURCES__:{json.dumps(sources, ensure_ascii=False)}\n\n"

        # ========== LLM 生成回答 ==========
        messages = self._build_rag_prompt(message, context)

        # 如果有对话历史，插入到 system 和 user 之间
        if history:
            # 插入历史消息（保留最近的 10 条）
            for hist_msg in history[-10:]:
                if hist_msg["role"] in ("user", "assistant"):
                    messages.insert(
                        -1,  # 在最后一条 user 消息之前插入
                        {"role": hist_msg["role"], "content": hist_msg["content"]},
                    )

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                max_tokens=2048,
                temperature=0.3,
                top_p=0.9,
            )

            async for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content

        except Exception as e:
            logger.error(f"DeepSeek API stream error (RAG): {e}")
            yield f"\n\n[调用 DeepSeek API 时出错: {str(e)}]"


# 全局单例
_rag_agent: RagAgent | None = None


def get_rag_agent() -> RagAgent:
    """获取 RagAgent 单例"""
    global _rag_agent
    if _rag_agent is None:
        _rag_agent = RagAgent()
    return _rag_agent
