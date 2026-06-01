"""
Retrieval Tool - 知识库检索工具
将现有的混合检索逻辑封装为 LangChain Tool，供 Agent 调用。

知识库 ID 传递机制：
由于 LangGraph 内部自动处理 tool call 的执行，我们无法在 tool_call_chunks
阶段修改参数。因此使用全局变量 _current_knowledge_base_id 来传递
knowledge_base_id，在 agent.stream() 开始时设置，在工具调用时读取。
"""
import json
import logging
from typing import Optional

from langchain_core.tools import tool

from modules.retrieval.hybrid_retriever import get_hybrid_retriever, ChunkResult

logger = logging.getLogger(__name__)

# 全局变量：用于在 agent.stream() 和 tool 之间传递 knowledge_base_id
# 在 agent.stream() 开始时设置，在 retrieve_knowledge 工具调用时读取
_current_knowledge_base_id: Optional[str] = None


def set_current_knowledge_base_id(kb_id: Optional[str]) -> None:
    """设置当前知识库 ID（由 agent.stream() 调用）"""
    global _current_knowledge_base_id
    _current_knowledge_base_id = kb_id


def get_current_knowledge_base_id() -> Optional[str]:
    """获取当前知识库 ID（由 retrieve_knowledge 工具调用）"""
    return _current_knowledge_base_id


def _build_context(chunks: list[ChunkResult]) -> str:
    """将检索到的 chunks 构建为 LLM 上下文"""
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        context_parts.append(
            f"[来源 {i}: {chunk.knowledge_base_name} / {chunk.document_name}]\n"
            f"{chunk.content}"
        )
    return "\n\n".join(context_parts)


@tool
async def retrieve_knowledge(
    query: str,
    knowledge_base_id: Optional[str] = None,
) -> str:
    """
    从知识库中检索与问题相关的文档内容。
    当用户询问知识库中的具体信息、文档内容、或者需要根据已有文档来回答问题时使用此工具。
    
    Args:
        query: 用户的查询问题
        knowledge_base_id: 知识库 ID（可选，不传则检索所有知识库）
    
    Returns:
        检索到的文档内容，包含来源信息
    """
    # 如果调用时没有传 knowledge_base_id，尝试使用全局变量中的值
    effective_kb_id = knowledge_base_id or get_current_knowledge_base_id()
    
    logger.info(
        f"Tool 'retrieve_knowledge' called with query: '{query[:50]}...' "
        f"(kb_id={effective_kb_id or 'all'}, "
        f"explicit_arg={knowledge_base_id or 'not_provided'})"
    )
    
    retriever = get_hybrid_retriever()
    
    try:
        top_chunks, sources = await retriever.retrieve(
            query=query,
            knowledge_base_id=effective_kb_id,
        )
    except Exception as e:
        logger.error(f"Hybrid retrieval error in tool: {e}")
        return f"[检索过程出错: {str(e)}]"
    
    if not top_chunks:
        logger.info("Tool 'retrieve_knowledge': no results found")
        return "未找到相关文档内容。"
    
    # 构建上下文
    context = _build_context(top_chunks)
    
    # 将 sources 信息编码到返回结果中（通过特殊标记）
    sources_json = json.dumps(sources, ensure_ascii=False)
    
    result = (
        f"检索到 {len(top_chunks)} 个相关文档片段：\n\n"
        f"{context}\n\n"
        f"__SOURCES__:{sources_json}"
    )
    
    logger.info(f"Tool 'retrieve_knowledge': returned {len(top_chunks)} chunks")
    return result
