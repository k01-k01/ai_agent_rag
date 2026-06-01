"""
Summarize Tool - 文档总结工具（Map-Reduce 分层总结）
供 Agent 调用，对知识库中的文档内容进行总结。

核心流程：
1. 精确匹配文档名 → 获取 document_id
2. 按 document_id 获取该文档的所有 chunk（不遗漏、不混入）
3. 如果 chunk 少（≤5），直接总结
4. 如果 chunk 多，采用 Map-Reduce 分层总结：
   - Map：将 chunk 分组，每组分别生成局部总结
   - Reduce：将局部总结合并，生成更高层次的总结
   - 递归直到生成最终总结
"""
import json
import logging
from typing import Optional

from langchain_core.tools import tool
from openai import AsyncOpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL
from db_pool import get_db_pool
from modules.agents.tools.retrieval_tool import get_current_knowledge_base_id

logger = logging.getLogger(__name__)

# 总结参数
CHUNKS_PER_GROUP = 5       # 每组最多 5 个 chunk
MAX_DIRECT_SUMMARY = 5     # chunk ≤5 时直接总结，不分层
MAX_DEPTH = 3              # 最大分层深度，防止无限递归

# DeepSeek 客户端（单例）
_client = None


def _get_client() -> AsyncOpenAI:
    """获取或创建 DeepSeek 客户端（单例模式）"""
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_API_BASE,
        )
    return _client


# ========== 提示词 ==========

SUMMARIZE_SYSTEM_PROMPT = "你是一个专业的文档总结助手。你的任务是根据文档内容，生成简洁、准确、全面的总结。"

SUMMARIZE_GROUP_PROMPT = """请总结以下文档片段（第 {group_index} 组，共 {total_groups} 组）：

文档名称：{document_name}

内容：
{content}

请生成该组文档片段的总结，要求：
1. 提取关键信息、主要观点和重要细节
2. 保持客观，不要添加原文没有的内容
3. 语言简洁明了
4. 如果该组内容包含多个主题，请分别列出
5. 用中文回答"""

SUMMARIZE_MERGE_PROMPT = """以下是文档 "{document_name}" 的多个局部总结（第 {level} 层合并，共 {total_parts} 个部分）：

{parts}

请将这些局部总结合并为一份更完整的总结，要求：
1. 整合所有部分的关键信息，去除重复内容
2. 按逻辑顺序组织内容
3. 保持客观准确
4. 语言简洁明了
5. 用中文回答"""

SUMMARIZE_FINAL_PROMPT = """以下是文档 "{document_name}" 的完整总结内容：

{content}

请基于以上内容，生成一份最终的精炼总结，要求：
1. 概括文档的核心主题和目的
2. 列出主要的关键要点（3-8个）
3. 每个要点用一句话概括
4. 语言简洁明了
5. 用中文回答

格式：
## 文档总结：{document_name}

**核心主题：** <一句话概括>

**关键要点：**
• <要点1>
• <要点2>
..."""


async def _find_document(document_name: str, knowledge_base_id: Optional[str]) -> Optional[dict]:
    """
    根据文档名精确查找文档。
    先尝试精确匹配，再尝试模糊匹配。

    Args:
        document_name: 文档名（如 "GROUP RPT.md"）
        knowledge_base_id: 知识库 ID

    Returns:
        文档信息 dict {"id": ..., "name": ...}，未找到返回 None
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        if knowledge_base_id:
            # 先尝试精确匹配
            row = await conn.fetchrow(
                """
                SELECT id, name FROM documents
                WHERE knowledge_base_id = $1::uuid AND name = $2
                LIMIT 1
                """,
                knowledge_base_id,
                document_name,
            )
            if not row:
                # 再尝试模糊匹配（文件名包含查询词）
                row = await conn.fetchrow(
                    """
                    SELECT id, name FROM documents
                    WHERE knowledge_base_id = $1::uuid AND name ILIKE $2
                    LIMIT 1
                    """,
                    knowledge_base_id,
                    f"%{document_name}%",
                )
        else:
            row = await conn.fetchrow(
                """
                SELECT id, name FROM documents
                WHERE name = $1
                LIMIT 1
                """,
                document_name,
            )
            if not row:
                row = await conn.fetchrow(
                    """
                    SELECT id, name FROM documents
                    WHERE name ILIKE $1
                    LIMIT 1
                    """,
                    f"%{document_name}%",
                )

    if row:
        return {"id": str(row["id"]), "name": row["name"]}
    return None


async def _get_document_chunks(document_id: str) -> list[dict]:
    """
    获取文档的所有 chunk，按创建时间排序。

    Args:
        document_id: 文档 ID

    Returns:
        chunk 列表 [{"id": ..., "content": ..., "index": ...}]
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, content, created_at
            FROM chunks
            WHERE document_id = $1::uuid
            ORDER BY created_at ASC
            """,
            document_id,
        )

    chunks = []
    for i, row in enumerate(rows):
        chunks.append({
            "id": str(row["id"]),
            "content": row["content"],
            "index": i + 1,
        })

    logger.info(f"Found {len(chunks)} chunks for document_id={document_id}")
    return chunks


async def _summarize_group(
    chunks: list[dict],
    document_name: str,
    group_index: int,
    total_groups: int,
) -> str:
    """
    对一组 chunk 生成局部总结（Map 阶段）。

    Args:
        chunks: 一组 chunk 列表
        document_name: 文档名
        group_index: 当前组索引
        total_groups: 总组数

    Returns:
        局部总结文本
    """
    content_parts = []
    for chunk in chunks:
        content_parts.append(f"[片段 {chunk['index']}]\n{chunk['content']}")

    content = "\n\n".join(content_parts)

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": SUMMARIZE_GROUP_PROMPT.format(
                        group_index=group_index,
                        total_groups=total_groups,
                        document_name=document_name,
                        content=content,
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        summary = response.choices[0].message.content.strip()
        logger.info(
            f"Group summary (group {group_index}/{total_groups}): "
            f"{len(summary)} chars for {len(chunks)} chunks"
        )
        return summary
    except Exception as e:
        logger.error(f"Failed to summarize group {group_index}: {e}")
        # 如果 LLM 调用失败，直接拼接原文作为降级方案
        return f"[组 {group_index} 总结失败，原文内容]\n{content[:2000]}"


async def _merge_summaries(
    summaries: list[str],
    document_name: str,
    level: int,
) -> str:
    """
    合并多个局部总结（Reduce 阶段）。

    Args:
        summaries: 局部总结列表
        document_name: 文档名
        level: 当前合并层级

    Returns:
        合并后的总结
    """
    parts_text = ""
    for i, summary in enumerate(summaries, 1):
        parts_text += f"--- 第 {i} 部分 ---\n{summary}\n\n"

    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": SUMMARIZE_MERGE_PROMPT.format(
                        document_name=document_name,
                        level=level,
                        total_parts=len(summaries),
                        parts=parts_text,
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        merged = response.choices[0].message.content.strip()
        logger.info(
            f"Merge summary (level {level}): {len(merged)} chars "
            f"from {len(summaries)} parts"
        )
        return merged
    except Exception as e:
        logger.error(f"Failed to merge summaries at level {level}: {e}")
        # 降级：直接拼接
        return "\n\n".join(summaries)


async def _generate_final_summary(
    content: str,
    document_name: str,
) -> str:
    """
    生成最终的精炼总结。

    Args:
        content: 完整总结内容
        document_name: 文档名

    Returns:
        最终总结
    """
    try:
        client = _get_client()
        response = await client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SUMMARIZE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": SUMMARIZE_FINAL_PROMPT.format(
                        document_name=document_name,
                        content=content,
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        final = response.choices[0].message.content.strip()
        logger.info(
            f"Final summary for '{document_name}': {len(final)} chars"
        )
        return final
    except Exception as e:
        logger.error(f"Failed to generate final summary: {e}")
        return content


async def _map_reduce_summarize(
    chunks: list[dict],
    document_name: str,
    depth: int = 1,
) -> str:
    """
    Map-Reduce 分层总结核心逻辑。

    Args:
        chunks: chunk 列表
        document_name: 文档名
        depth: 当前递归深度

    Returns:
        总结文本
    """
    # 如果 chunk 数量少，直接总结
    if len(chunks) <= MAX_DIRECT_SUMMARY or depth >= MAX_DEPTH:
        if len(chunks) <= MAX_DIRECT_SUMMARY:
            logger.info(
                f"Direct summary: {len(chunks)} chunks (depth={depth})"
            )
        else:
            logger.info(
                f"Max depth reached (depth={MAX_DEPTH}), "
                f"summarizing {len(chunks)} chunks directly"
            )
        return await _summarize_group(
            chunks=chunks,
            document_name=document_name,
            group_index=1,
            total_groups=1,
        )

    # Map 阶段：将 chunk 分组，每组分别生成局部总结
    groups = []
    for i in range(0, len(chunks), CHUNKS_PER_GROUP):
        groups.append(chunks[i:i + CHUNKS_PER_GROUP])

    total_groups = len(groups)
    logger.info(
        f"Map phase (depth={depth}): {len(chunks)} chunks → "
        f"{total_groups} groups"
    )

    group_summaries = []
    for i, group in enumerate(groups, 1):
        summary = await _summarize_group(
            chunks=group,
            document_name=document_name,
            group_index=i,
            total_groups=total_groups,
        )
        group_summaries.append(summary)

    # Reduce 阶段：如果只有一组总结，直接返回
    if len(group_summaries) == 1:
        return group_summaries[0]

    # 多组总结需要合并
    # 如果合并后的数量仍然超过阈值，递归继续合并
    if len(group_summaries) > MAX_DIRECT_SUMMARY:
        logger.info(
            f"Reduce phase (depth={depth}): {len(group_summaries)} summaries "
            f"→ recursive merge"
        )
        merged = await _map_reduce_summarize(
            chunks=[{"id": "", "content": s, "index": i}
                    for i, s in enumerate(group_summaries, 1)],
            document_name=document_name,
            depth=depth + 1,
        )
    else:
        logger.info(
            f"Reduce phase (depth={depth}): merging {len(group_summaries)} summaries"
        )
        merged = await _merge_summaries(
            summaries=group_summaries,
            document_name=document_name,
            level=depth,
        )

    return merged


@tool
async def summarize_document(
    query: str,
    knowledge_base_id: Optional[str] = None,
) -> str:
    """
    对知识库中指定的文档进行总结。
    当用户要求"总结一下"、"概括"、"归纳"某篇文档时使用此工具。
    会精确匹配文档名，获取该文档的所有内容，然后生成总结。

    Args:
        query: 需要总结的文档名称或主题（如 "GROUP RPT.md"）
        knowledge_base_id: 知识库 ID（可选，不传则检索所有知识库）

    Returns:
        文档的完整总结
    """
    # 如果调用时没有传 knowledge_base_id，尝试使用全局变量中的值
    effective_kb_id = knowledge_base_id or get_current_knowledge_base_id()

    logger.info(
        f"Tool 'summarize_document' called with query: '{query}' "
        f"(kb_id={effective_kb_id or 'all'})"
    )

    # 第一步：精确查找文档
    document = await _find_document(query, effective_kb_id)
    if not document:
        logger.info(f"Document not found for query: '{query}'")
        return (
            f"未找到与「{query}」匹配的文档。\n\n"
            f"提示：请确保文档名称正确，例如「GROUP RPT.md」。"
        )

    document_id = document["id"]
    document_name = document["name"]
    logger.info(f"Found document: '{document_name}' (id={document_id})")

    # 第二步：获取该文档的所有 chunk
    chunks = await _get_document_chunks(document_id)
    if not chunks:
        logger.info(f"No chunks found for document '{document_name}'")
        return f"文档「{document_name}」中没有找到可总结的内容片段。"

    # 第三步：Map-Reduce 分层总结
    logger.info(
        f"Starting Map-Reduce summarization for '{document_name}': "
        f"{len(chunks)} chunks"
    )

    merged_summary = await _map_reduce_summarize(
        chunks=chunks,
        document_name=document_name,
    )

    # 第四步：生成最终精炼总结
    final_summary = await _generate_final_summary(
        content=merged_summary,
        document_name=document_name,
    )

    # 构建 sources 信息（用于前端展示）
    # 根据 chunk 数量决定描述方式
    if len(chunks) <= MAX_DIRECT_SUMMARY:
        summary_method = "直接总结"
    else:
        summary_method = "Map-Reduce 分层总结"
    sources = [{
        "title": document_name,
        "content": f"文档共 {len(chunks)} 个片段，使用 {summary_method} 生成",
        "score": 1.0,
    }]

    # 将 sources 信息编码到返回结果中
    sources_json = json.dumps(sources, ensure_ascii=False)

    result = (
        f"已对文档「{document_name}」完成总结（共 {len(chunks)} 个内容片段）：\n\n"
        f"{final_summary}\n\n"
        f"__SOURCES__:{sources_json}"
    )

    logger.info(
        f"Tool 'summarize_document' completed for '{document_name}': "
        f"{len(chunks)} chunks, {len(final_summary)} chars summary"
    )
    return result
