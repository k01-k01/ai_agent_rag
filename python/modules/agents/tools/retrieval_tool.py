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


def _build_context(chunks: list[ChunkResult], max_tokens: int = 3000) -> str:
    """
    将检索到的 chunks 构建为 LLM 上下文。
    
    优化：
    1. 按文档分组排序，同一文档的 chunk 聚在一起，LLM 阅读更连贯
    2. 带 token 估算截断保护，避免上下文溢出
    
    Args:
        chunks: 检索结果列表（已按 Reranker 分数降序）
        max_tokens: 最大 token 数（默认 3000），超出后截断
    
    Returns:
        格式化后的上下文字符串
    """
    from collections import OrderedDict
    
    # 简单估算 token 数：1 个中文字 ≈ 2 tokens，1 个非中文字 ≈ 0.3 tokens
    def estimate_tokens(text: str) -> int:
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return chinese_chars * 2 + int(other_chars * 0.3)
    
    # 第一步：按文档分组（保持文档间按最高分 chunk 排序）
    # 使用 OrderedDict 保持首次出现的顺序
    doc_groups = OrderedDict()
    for chunk in chunks:
        key = (chunk.knowledge_base_name, chunk.document_name)
        if key not in doc_groups:
            doc_groups[key] = []
        doc_groups[key].append(chunk)
    
    # 第二步：构建上下文，组内保持 Reranker 分数降序
    context_parts = []
    total_tokens = 0
    part_index = 0
    
    for (kb_name, doc_name), doc_chunks in doc_groups.items():
        for chunk in doc_chunks:
            part = (
                f"[来源 {part_index + 1}: {kb_name} / {doc_name}]\n"
                f"{chunk.content}"
            )
            part_tokens = estimate_tokens(part)
            
            # 检查是否超出 token 限制
            if total_tokens + part_tokens > max_tokens:
                remaining = max_tokens - total_tokens
                if remaining > 50:  # 至少保留有意义的长度
                    # 截断当前 chunk 的内容
                    truncated_content = chunk.content[:remaining * 2]  # 粗略截断
                    context_parts.append(
                        f"[来源 {part_index + 1}: {kb_name} / {doc_name}]\n"
                        f"{truncated_content}...[已截断]"
                    )
                break  # 超出限制，停止添加更多 chunk
            
            context_parts.append(part)
            total_tokens += part_tokens
            part_index += 1
        else:
            continue  # 内层循环正常结束（未 break），继续外层循环
        break  # 内层循环 break 了，也跳出外层循环
    
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
