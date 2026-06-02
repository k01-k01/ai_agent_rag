"""
Question Guide Tool - 提问导读工具
供 Agent 调用，当用户询问"关于某篇文章可以问什么问题"时，
生成覆盖文档全貌的高质量提问导读。

核心流程：
1. 精确匹配文档名 → 获取 document_id
2. 按 document_id 获取该文档的所有 chunk（不遗漏、不混入）
3. 如果 chunk 少（≤20），直接 1 次 LLM 调用生成导读
4. 如果 chunk 多（>20），采用「主题提取 → 生成导读」两步：
   - 主题提取：将 chunk 分组，每组提取核心主题（轻量，输出短）
   - 生成导读：基于主题列表，一次生成覆盖全面的提问导读
"""
import asyncio
import json
import logging
from typing import Optional

from langchain_core.tools import tool
from openai import AsyncOpenAI

from config import get_current_llm_config
from db_pool import get_db_pool
from modules.agents.tools.retrieval_tool import get_current_knowledge_base_id

logger = logging.getLogger(__name__)

# 参数配置
CHUNKS_PER_GROUP = 10       # 每组最多 10 个 chunk（主题提取用）
MAX_DIRECT_GUIDE = 20       # chunk ≤20 时直接生成，不分步

# LLM 客户端（单例）
_client = None


def _get_client() -> AsyncOpenAI:
    """获取或创建 LLM 客户端（单例模式）"""
    global _client
    llm_config = get_current_llm_config()
    if _client is None:
        _client = AsyncOpenAI(
            api_key=llm_config["api_key"],
            base_url=llm_config["api_base"],
        )
        logger.info(f"QuestionGuide client initialized with provider: {llm_config['provider']}")
    return _client


def _get_current_model() -> str:
    """获取当前 LLM 模型名"""
    return get_current_llm_config()["model"]


# ========== 提示词 ==========

SYSTEM_PROMPT = "你是一个专业的文档分析助手。你的任务是根据文档内容，生成高质量的提问导读，帮助用户快速了解可以从哪些角度向文档提问。"

TOPIC_EXTRACT_PROMPT = """请提取以下文档片段的核心主题（第 {group_index} 组，共 {total_groups} 组）：

文档名称：{document_name}

内容：
{content}

请提取该组文档片段的核心主题和关键信息点，要求：
1. 每个片段用 1-2 句话概括其核心内容
2. 提取关键的数据、结论、观点等
3. 保持客观，不要添加原文没有的内容
4. 用中文回答

输出格式（简洁）：
- [片段索引] 核心主题：<一句话概括>
"""

GUIDE_PROMPT = """以下是文档 "{document_name}" 的核心内容主题列表：

{topics}

请基于以上内容，生成一份高质量的提问导读，帮助用户快速了解可以从哪些角度向这篇文档提问。

要求：
1. 问题必须覆盖文档的**所有重要内容**，确保用户看了导读就不需要再去看原文
2. 按文档的实际主题/章节维度组织问题，而不是按通用分类
3. 每个问题都应该是**可以直接向 AI 提问的完整问题**，具体且有针对性
4. 问题要有层次感：既有基础理解类问题，也有深度分析类问题
5. 每个维度下至少 2-3 个问题
6. 用中文回答

输出格式：
## 关于《{document_name}》的提问导读

### 📌 <主题维度一>
- <可以直接提问的完整问题>
- <可以直接提问的完整问题>

### 📌 <主题维度二>
- <可以直接提问的完整问题>
- <可以直接提问的完整问题>

...（根据文档实际内容继续）
"""


async def _find_document(document_name: str, knowledge_base_id: Optional[str]) -> Optional[dict]:
    """
    根据文档名查找文档（复用 summarize_tool 的查找逻辑）。

    查找策略（逐级 fallback）：
    1. 精确匹配（name = query）
    2. ILIKE 模糊匹配（name ILIKE '%query%'）
    3. pg_trgm 全文搜索文档名（similarity(name, query) > 0.1）
    4. 用 query 去检索 chunks，从检索结果中提取文档名

    Args:
        document_name: 文档名或描述
        knowledge_base_id: 知识库 ID

    Returns:
        文档信息 dict {"id": ..., "name": ...}，未找到返回 None
    """
    pool = await get_db_pool()
    async with pool.acquire() as conn:
        # 策略 1：精确匹配
        if knowledge_base_id:
            row = await conn.fetchrow(
                """
                SELECT id, name FROM documents
                WHERE knowledge_base_id = $1::uuid AND name = $2
                LIMIT 1
                """,
                knowledge_base_id,
                document_name,
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

        if row:
            logger.info(f"Found document by exact match: '{row['name']}'")
            return {"id": str(row["id"]), "name": row["name"]}

        # 策略 2：ILIKE 模糊匹配
        if knowledge_base_id:
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
                WHERE name ILIKE $1
                LIMIT 1
                """,
                f"%{document_name}%",
            )

        if row:
            logger.info(f"Found document by ILIKE match: '{row['name']}'")
            return {"id": str(row["id"]), "name": row["name"]}

        # 策略 3：pg_trgm 全文搜索文档名
        if knowledge_base_id:
            row = await conn.fetchrow(
                """
                SELECT id, name,
                       similarity(name, $2) AS sim_score
                FROM documents
                WHERE knowledge_base_id = $1::uuid
                  AND similarity(name, $2) > 0.1
                ORDER BY sim_score DESC
                LIMIT 1
                """,
                knowledge_base_id,
                document_name,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT id, name,
                       similarity(name, $1) AS sim_score
                FROM documents
                WHERE similarity(name, $1) > 0.1
                ORDER BY sim_score DESC
                LIMIT 1
                """,
                document_name,
            )

        if row:
            logger.info(
                f"Found document by pg_trgm similarity: '{row['name']}' "
                f"(score={float(row['sim_score']):.3f})"
            )
            return {"id": str(row["id"]), "name": row["name"]}

        # 策略 4：用 query 去检索 chunks，从检索结果中提取文档名
        logger.info(
            f"Trying chunk retrieval to find document for query: '{document_name}'"
        )
        try:
            from modules.retrieval.hybrid_retriever import get_hybrid_retriever
            retriever = get_hybrid_retriever()
            top_chunks, _ = await retriever.retrieve(
                query=document_name,
                knowledge_base_id=knowledge_base_id,
            )
            if top_chunks:
                first_chunk = top_chunks[0]
                doc_name = first_chunk.document_name
                if knowledge_base_id:
                    row = await conn.fetchrow(
                        """
                        SELECT id, name FROM documents
                        WHERE knowledge_base_id = $1::uuid AND name = $2
                        LIMIT 1
                        """,
                        knowledge_base_id,
                        doc_name,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT id, name FROM documents
                        WHERE name = $1
                        LIMIT 1
                        """,
                        doc_name,
                    )
                if row:
                    logger.info(
                        f"Found document via chunk retrieval: '{row['name']}'"
                    )
                    return {"id": str(row["id"]), "name": row["name"]}
        except Exception as e:
            logger.warning(f"Chunk retrieval fallback failed: {e}")

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


async def _extract_topics(
    chunks: list[dict],
    document_name: str,
    group_index: int,
    total_groups: int,
) -> str:
    """
    对一组 chunk 提取核心主题（轻量操作，输出简短）。

    Args:
        chunks: 一组 chunk 列表
        document_name: 文档名
        group_index: 当前组索引
        total_groups: 总组数

    Returns:
        主题提取文本（简短）
    """
    content_parts = []
    for chunk in chunks:
        content_parts.append(f"[片段 {chunk['index']}]\n{chunk['content']}")

    content = "\n\n".join(content_parts)

    try:
        client = _get_client()
        model = _get_current_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": TOPIC_EXTRACT_PROMPT.format(
                        group_index=group_index,
                        total_groups=total_groups,
                        document_name=document_name,
                        content=content,
                    ),
                },
            ],
            temperature=0.3,
            max_tokens=512,  # 输出短，速度快
        )
        topics = response.choices[0].message.content.strip()
        logger.info(
            f"Topic extraction (group {group_index}/{total_groups}): "
            f"{len(topics)} chars for {len(chunks)} chunks"
        )
        return topics
    except Exception as e:
        logger.error(f"Failed to extract topics for group {group_index}: {e}")
        # 降级：直接输出片段索引和内容前 200 字
        fallback = []
        for chunk in chunks:
            fallback.append(f"[片段 {chunk['index']}] {chunk['content'][:200]}")
        return "\n".join(fallback)


async def _generate_guide(
    topics: str,
    document_name: str,
) -> str:
    """
    基于主题列表生成提问导读。

    Args:
        topics: 主题列表文本
        document_name: 文档名

    Returns:
        提问导读文本
    """
    try:
        client = _get_client()
        model = _get_current_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": GUIDE_PROMPT.format(
                        document_name=document_name,
                        topics=topics,
                    ),
                },
            ],
            temperature=0.5,  # 适当提高温度，让问题更有创造性
            max_tokens=2048,
        )
        guide = response.choices[0].message.content.strip()
        logger.info(
            f"Generated question guide for '{document_name}': {len(guide)} chars"
        )
        return guide
    except Exception as e:
        logger.error(f"Failed to generate question guide: {e}")
        return f"生成提问导读时出错: {str(e)}"


async def _generate_guide_direct(
    chunks: list[dict],
    document_name: str,
) -> str:
    """
    直接基于 chunks 生成提问导读（chunk 少时使用，1 次 LLM 调用）。

    将 chunks 内容拼接后，直接让 LLM 生成导读。
    """
    content_parts = []
    for chunk in chunks:
        content_parts.append(f"[片段 {chunk['index']}]\n{chunk['content']}")

    content = "\n\n".join(content_parts)

    try:
        client = _get_client()
        model = _get_current_model()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": GUIDE_PROMPT.format(
                        document_name=document_name,
                        topics=content,
                    ),
                },
            ],
            temperature=0.5,
            max_tokens=2048,
        )
        guide = response.choices[0].message.content.strip()
        logger.info(
            f"Direct guide for '{document_name}': {len(guide)} chars "
            f"from {len(chunks)} chunks"
        )
        return guide
    except Exception as e:
        logger.error(f"Failed to generate direct guide: {e}")
        return f"生成提问导读时出错: {str(e)}"


@tool
async def generate_questions(
    query: str,
    knowledge_base_id: Optional[str] = None,
) -> str:
    """
    为知识库中的指定文档生成提问导读。
    当用户询问"关于某篇文章可以问什么问题"、"有什么问题可以问"、"提问方向"等时使用此工具。
    会精确匹配文档名，获取该文档的所有内容，然后生成覆盖文档全貌的提问导读。

    Args:
        query: 需要生成提问导读的文档名称或主题（如 "GROUP RPT.md"）
        knowledge_base_id: 知识库 ID（可选，不传则检索所有知识库）

    Returns:
        文档的提问导读
    """
    # 如果调用时没有传 knowledge_base_id，尝试使用全局变量中的值
    effective_kb_id = knowledge_base_id or get_current_knowledge_base_id()

    logger.info(
        f"Tool 'generate_questions' called with query: '{query}' "
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
        return f"文档「{document_name}」中没有找到可分析的内容片段。"

    # 第三步：根据 chunk 数量选择策略
    logger.info(
        f"Generating question guide for '{document_name}': "
        f"{len(chunks)} chunks"
    )

    if len(chunks) <= MAX_DIRECT_GUIDE:
        # chunk 少，直接生成（1 次 LLM 调用）
        logger.info(f"Direct guide mode: {len(chunks)} chunks")
        guide = await _generate_guide_direct(chunks, document_name)
        guide_method = "直接生成"
    else:
        # chunk 多，先提取主题再生成（最多 2 次 LLM 调用）
        logger.info(f"Two-step guide mode: {len(chunks)} chunks")

        # 分组提取主题（并行执行）
        groups = []
        for i in range(0, len(chunks), CHUNKS_PER_GROUP):
            groups.append(chunks[i:i + CHUNKS_PER_GROUP])

        total_groups = len(groups)
        logger.info(
            f"Topic extraction phase: {len(chunks)} chunks → "
            f"{total_groups} groups (parallel)"
        )

        # 并行提取所有组的主题
        topic_list = await asyncio.gather(*[
            _extract_topics(
                chunks=group,
                document_name=document_name,
                group_index=i,
                total_groups=total_groups,
            )
            for i, group in enumerate(groups, 1)
        ])

        # 合并主题列表
        all_topics = "\n\n".join([
            f"--- 第 {i} 组 ---\n{topic}"
            for i, topic in enumerate(topic_list, 1)
        ])

        # 生成提问导读
        guide = await _generate_guide(all_topics, document_name)
        guide_method = "主题提取 + 生成"

    # 构建 sources 信息（用于前端展示）
    sources = [{
        "title": document_name,
        "content": f"文档共 {len(chunks)} 个片段，使用「{guide_method}」方式生成提问导读",
        "score": 1.0,
    }]

    # 将 sources 信息编码到返回结果中
    sources_json = json.dumps(sources, ensure_ascii=False)

    result = (
        f"已为文档「{document_name}」生成提问导读（共 {len(chunks)} 个内容片段，"
        f"使用「{guide_method}」方式）：\n\n"
        f"{guide}\n\n"
        f"__SOURCES__:{sources_json}"
    )

    logger.info(
        f"Tool 'generate_questions' completed for '{document_name}': "
        f"{len(chunks)} chunks, {len(guide)} chars guide"
    )
    return result
