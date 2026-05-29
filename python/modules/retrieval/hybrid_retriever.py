"""
混合检索器 - 实现 RAG Agent 的完整检索流程

流程：
1. 全文搜索（pg_trgm）→ Top 50
2. 向量检索（pgvector HNSW）→ Top 50
3. RRF 融合（k=60）
4. Reranker 精排（bge-reranker-v2-m3）→ Top 10
"""
import asyncio
import json
import logging
from typing import List, Optional

from config import (
    RERANKER_MODEL_PATH,
)
from db_pool import get_db_pool
from modules.document_processor.embedder import generate_embedding

logger = logging.getLogger(__name__)

# 检索参数
FULLTEXT_TOP_K = 50       # 全文搜索返回 Top 50
VECTOR_TOP_K = 50         # 向量检索返回 Top 50
RRF_K = 60                # RRF 融合参数 k
RERANK_TOP_K = 10          # Reranker 精排后返回 Top 10

# Reranker 模型（全局单例）
_reranker_model = None



def _get_reranker_model():
    """
    获取或创建 Reranker 模型（单例模式）
    使用 bge-reranker-v2-m3 模型
    """
    global _reranker_model
    if _reranker_model is None:
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch

            logger.info(f"Loading reranker model from {RERANKER_MODEL_PATH}")
            tokenizer = AutoTokenizer.from_pretrained(RERANKER_MODEL_PATH)
            model = AutoModelForSequenceClassification.from_pretrained(RERANKER_MODEL_PATH)
            model.eval()

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model.to(device)

            _reranker_model = {
                "model": model,
                "tokenizer": tokenizer,
                "device": device,
            }
            logger.info(f"Reranker model loaded successfully, device: {device}")
        except Exception as e:
            logger.error(f"Failed to load reranker model: {e}")
            raise
    return _reranker_model


class ChunkResult:
    """检索结果块"""
    def __init__(
        self,
        chunk_id: str,
        content: str,
        document_name: str,
        knowledge_base_name: str,
        score: float = 0.0,
    ):
        self.chunk_id = chunk_id
        self.content = content
        self.document_name = document_name
        self.knowledge_base_name = knowledge_base_name
        self.score = score

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "document_name": self.document_name,
            "knowledge_base_name": self.knowledge_base_name,
            "score": self.score,
        }


class HybridRetriever:
    """混合检索器：全文搜索 + 向量检索 + RRF 融合 + Reranker 精排"""

    async def fulltext_search(
        self,
        query: str,
        knowledge_base_id: Optional[str] = None,
        top_k: int = FULLTEXT_TOP_K,
    ) -> List[ChunkResult]:
        """
        全文搜索 - 使用 pg_trgm 三元组模糊匹配

        使用 pg_trgm 的 similarity() 函数进行中文模糊匹配，
        天然支持中文，无需额外安装中文分词插件。

        Args:
            query: 用户查询文本
            knowledge_base_id: 可选的知识库 ID 过滤
            top_k: 返回结果数量

        Returns:
            ChunkResult 列表
        """
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                if knowledge_base_id:
                    rows = await conn.fetch(
                        """
                        SELECT
                            c.id,
                            c.content,
                            c.metadata,
                            d.name AS document_name,
                            kb.name AS knowledge_base_name,
                            similarity(c.content, $1) AS rank_score
                        FROM chunks c
                        JOIN documents d ON c.document_id = d.id
                        JOIN knowledge_bases kb ON c.knowledge_base_id = kb.id
                        WHERE c.knowledge_base_id = $2::uuid
                          AND similarity(c.content, $1) > 0
                        ORDER BY rank_score DESC
                        LIMIT $3
                        """,
                        query,
                        knowledge_base_id,
                        top_k,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT
                            c.id,
                            c.content,
                            c.metadata,
                            d.name AS document_name,
                            kb.name AS knowledge_base_name,
                            similarity(c.content, $1) AS rank_score
                        FROM chunks c
                        JOIN documents d ON c.document_id = d.id
                        JOIN knowledge_bases kb ON c.knowledge_base_id = kb.id
                        WHERE similarity(c.content, $1) > 0
                        ORDER BY rank_score DESC
                        LIMIT $2
                        """,
                        query,
                        top_k,
                    )

            results = []
            for row in rows:
                results.append(ChunkResult(
                    chunk_id=str(row["id"]),
                    content=row["content"],
                    document_name=row["document_name"],
                    knowledge_base_name=row["knowledge_base_name"],
                    score=float(row["rank_score"]),
                ))

            logger.info(
                f"Fulltext search returned {len(results)} results "
                f"(kb_id={knowledge_base_id or 'all'})"
            )
            return results

        except Exception as e:
            logger.error(f"Fulltext search error: {e}")
            return []

    async def vector_search(
        self,
        query: str,
        knowledge_base_id: Optional[str] = None,
        top_k: int = VECTOR_TOP_K,
    ) -> List[ChunkResult]:
        """
        向量检索 - 使用 pgvector HNSW 索引

        将用户问题用 BGE-M3 向量化，在 pgvector 中执行余弦相似度检索。

        Args:
            query: 用户查询文本
            knowledge_base_id: 可选的知识库 ID 过滤
            top_k: 返回结果数量

        Returns:
            ChunkResult 列表
        """
        try:
            # 生成查询向量
            embedding = generate_embedding(query)
            if not embedding or all(v == 0.0 for v in embedding):
                logger.warning("Empty embedding generated for vector search query")
                return []

            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

            pool = await get_db_pool()
            async with pool.acquire() as conn:
                if knowledge_base_id:
                    rows = await conn.fetch(
                        """
                        SELECT
                            c.id,
                            c.content,
                            c.metadata,
                            d.name AS document_name,
                            kb.name AS knowledge_base_name,
                            1 - (c.embedding <=> $1::vector) AS similarity
                        FROM chunks c
                        JOIN documents d ON c.document_id = d.id
                        JOIN knowledge_bases kb ON c.knowledge_base_id = kb.id
                        WHERE c.knowledge_base_id = $2::uuid
                          AND c.embedding IS NOT NULL
                        ORDER BY c.embedding <=> $1::vector
                        LIMIT $3
                        """,
                        embedding_str,
                        knowledge_base_id,
                        top_k,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT
                            c.id,
                            c.content,
                            c.metadata,
                            d.name AS document_name,
                            kb.name AS knowledge_base_name,
                            1 - (c.embedding <=> $1::vector) AS similarity
                        FROM chunks c
                        JOIN documents d ON c.document_id = d.id
                        JOIN knowledge_bases kb ON c.knowledge_base_id = kb.id
                        WHERE c.embedding IS NOT NULL
                        ORDER BY c.embedding <=> $1::vector
                        LIMIT $2
                        """,
                        embedding_str,
                        top_k,
                    )

            results = []
            for row in rows:
                results.append(ChunkResult(
                    chunk_id=str(row["id"]),
                    content=row["content"],
                    document_name=row["document_name"],
                    knowledge_base_name=row["knowledge_base_name"],
                    score=float(row["similarity"]),
                ))

            logger.info(
                f"Vector search returned {len(results)} results "
                f"(kb_id={knowledge_base_id or 'all'})"
            )
            return results

        except Exception as e:
            logger.error(f"Vector search error: {e}")
            return []

    def rrf_fusion(
        self,
        fulltext_results: List[ChunkResult],
        vector_results: List[ChunkResult],
        k: int = RRF_K,
    ) -> List[ChunkResult]:
        """
        RRF（Reciprocal Rank Fusion）融合

        使用标准 RRF 公式：score = 1 / (k + rank)
        将全文搜索和向量检索的结果融合排序。

        Args:
            fulltext_results: 全文搜索结果
            vector_results: 向量检索结果
            k: RRF 参数（默认 60）

        Returns:
            融合后的 ChunkResult 列表（按 RRF 分数降序）
        """
        # 构建 chunk_id -> (rrf_score, ChunkResult) 的映射
        rrf_scores: dict[str, tuple[float, ChunkResult]] = {}

        # 处理全文搜索结果
        for rank, result in enumerate(fulltext_results):
            score = 1.0 / (k + rank)
            rrf_scores[result.chunk_id] = (score, result)

        # 处理向量检索结果
        for rank, result in enumerate(vector_results):
            if result.chunk_id in rrf_scores:
                # 已存在，累加 RRF 分数
                existing_score, existing_result = rrf_scores[result.chunk_id]
                rrf_scores[result.chunk_id] = (
                    existing_score + 1.0 / (k + rank),
                    existing_result,
                )
            else:
                score = 1.0 / (k + rank)
                rrf_scores[result.chunk_id] = (score, result)

        # 按 RRF 分数降序排序
        sorted_results = sorted(
            rrf_scores.values(),
            key=lambda x: x[0],
            reverse=True,
        )

        # 设置融合后的分数
        fused_results = []
        for rrf_score, result in sorted_results:
            result.score = rrf_score
            fused_results.append(result)

        logger.info(
            f"RRF fusion: {len(fulltext_results)} fulltext + {len(vector_results)} vector "
            f"→ {len(fused_results)} fused results"
        )
        return fused_results

    def rerank(
        self,
        query: str,
        candidates: List[ChunkResult],
        top_k: int = RERANK_TOP_K,
    ) -> List[ChunkResult]:
        """
        Reranker 精排 - 使用 bge-reranker-v2-m3 模型

        对 RRF 融合后的候选结果进行精排，返回 Top K。

        Args:
            query: 用户查询文本
            candidates: RRF 融合后的候选结果
            top_k: 返回结果数量

        Returns:
            精排后的 Top K ChunkResult 列表
        """
        if not candidates:
            return []

        try:
            reranker = _get_reranker_model()
            model = reranker["model"]
            tokenizer = reranker["tokenizer"]
            device = reranker["device"]

            import torch

            # 构建 query-document 对
            pairs = [[query, cand.content] for cand in candidates]

            # Tokenize
            inputs = tokenizer(
                pairs,
                padding=True,
                truncation=True,
                return_tensors="pt",
                max_length=512,
            ).to(device)

            # 推理
            with torch.no_grad():
                outputs = model(**inputs)
                scores = outputs.logits.squeeze(-1).cpu().numpy()

            # 为每个候选结果设置 reranker 分数
            for i, candidate in enumerate(candidates):
                candidate.score = float(scores[i])

            # 按 reranker 分数降序排序
            reranked = sorted(candidates, key=lambda x: x.score, reverse=True)

            # 取 Top K
            top_results = reranked[:top_k]

            logger.info(
                f"Reranker: {len(candidates)} candidates → {len(top_results)} results"
            )
            return top_results

        except Exception as e:
            logger.error(f"Reranker error: {e}, falling back to RRF order")
            # 如果 Reranker 失败，直接返回 RRF 排序的前 top_k 个
            return candidates[:top_k]

    async def retrieve(
        self,
        query: str,
        knowledge_base_id: Optional[str] = None,
    ) -> tuple[List[ChunkResult], List[dict]]:
        """
        完整混合检索流程

        1. 全文搜索 → Top 50
        2. 向量检索 → Top 50
        3. RRF 融合
        4. Reranker 精排 → Top 10

        Args:
            query: 用户查询文本
            knowledge_base_id: 可选的知识库 ID 过滤

        Returns:
            (top_chunks, sources) 元组
            - top_chunks: Top 10 ChunkResult 列表（用于 LLM 上下文）
            - sources: 检索来源信息列表（用于前端展示）
        """
        logger.info(
            f"Starting hybrid retrieval for query: '{query[:50]}...' "
            f"(kb_id={knowledge_base_id or 'all'})"
        )

        # 1. + 2. 全文搜索 + 向量检索（并行执行）
        fulltext_results, vector_results = await asyncio.gather(
            self.fulltext_search(query, knowledge_base_id),
            self.vector_search(query, knowledge_base_id),
        )
        logger.info(f"Fulltext search: {len(fulltext_results)} results, Vector search: {len(vector_results)} results")

        # 3. RRF 融合
        fused_results = self.rrf_fusion(fulltext_results, vector_results)
        logger.info(f"RRF fusion: {len(fused_results)} results")

        if not fused_results:
            logger.warning("No results from hybrid retrieval")
            return [], []

        # 4. Reranker 精排
        top_chunks = self.rerank(query, fused_results)
        logger.info(f"Reranker: top {len(top_chunks)} results")

        # 构建检索来源信息
        sources = []
        for chunk in top_chunks:
            sources.append({
                "title": f"{chunk.knowledge_base_name} / {chunk.document_name}",
                "content": chunk.content[:200],  # 截取前 200 字符作为摘要
                "score": round(chunk.score, 4),
            })

        return top_chunks, sources


# 全局单例
_hybrid_retriever: Optional[HybridRetriever] = None


def get_hybrid_retriever() -> HybridRetriever:
    """获取 HybridRetriever 单例"""
    global _hybrid_retriever
    if _hybrid_retriever is None:
        _hybrid_retriever = HybridRetriever()
    return _hybrid_retriever
