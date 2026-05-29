"""
文本切分模块 - 使用 LangChain 的语义切分（Semantic Chunking）
将文档内容按语义边界切分为合适的文本块

优化：复用 embedder.py 中已在 GPU 上的 BGE-M3 模型，
避免独立加载 HuggingFaceBgeEmbeddings（强制 CPU）造成的内存浪费和性能损失。
"""
import logging
from typing import List

from langchain_core.embeddings import Embeddings

from modules.document_processor.embedder import generate_embedding

logger = logging.getLogger(__name__)

# 全局缓存，避免重复创建适配器
_embeddings = None


class SharedBgeEmbeddings(Embeddings):
    """
    适配器：将 embedder.py 的 SentenceTransformer 包装为 LangChain Embeddings 接口
    
    使得 chunker.py 可以复用 embedder.py 中已经在 GPU 上的 BGE-M3 模型，
    避免独立加载 HuggingFaceBgeEmbeddings（强制 CPU）造成的 2.2GB 内存浪费。
    """

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """嵌入文档列表"""
        return [generate_embedding(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        """嵌入查询文本"""
        return generate_embedding(text)


def _get_embeddings():
    """获取或创建共享的 BGE-M3 嵌入模型适配器（复用 embedder.py 的 GPU 模型）"""
    global _embeddings
    if _embeddings is None:
        try:
            _embeddings = SharedBgeEmbeddings()
            logger.info("Using shared BGE-M3 model from embedder.py (GPU)")
        except Exception as e:
            logger.warning(f"Failed to create shared embeddings: {e}, falling back to RecursiveCharacterTextSplitter")
            _embeddings = None
    return _embeddings



def semantic_chunk(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """
    使用语义切分将文本切分为块。

    优先使用 SemanticChunker（基于嵌入的语义边界检测），
    如果不可用则回退到 RecursiveCharacterTextSplitter。

    Args:
        text: 要切分的文本
        chunk_size: 目标块大小（字符数）
        chunk_overlap: 块之间的重叠字符数

    Returns:
        切分后的文本块列表
    """
    if not text or not text.strip():
        return []

    embeddings = _get_embeddings()

    if embeddings:
        try:
            return _semantic_chunk_with_embeddings(text, embeddings)
        except Exception as e:
            logger.warning(f"Semantic chunking failed: {e}, falling back to recursive splitter")

    return _recursive_chunk(text, chunk_size, chunk_overlap)


def _semantic_chunk_with_embeddings(text: str, embeddings) -> List[str]:
    """使用 SemanticChunker 进行语义切分"""
    try:
        from langchain_experimental.text_splitter import SemanticChunker

        splitter = SemanticChunker(
            embeddings=embeddings,
            breakpoint_threshold_type="percentile",  # 使用百分位数断点
            breakpoint_threshold_amount=70,  # 百分位阈值
        )
        chunks = splitter.split_text(text)
        logger.info(f"Semantic chunking produced {len(chunks)} chunks")
        return chunks
    except ImportError:
        logger.warning("langchain-experimental not installed, falling back to recursive splitter")
        raise


def _recursive_chunk(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """使用 RecursiveCharacterTextSplitter 进行递归字符切分（回退方案）"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter


    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
        length_function=len,
    )
    chunks = splitter.split_text(text)
    logger.info(f"Recursive chunking produced {len(chunks)} chunks")
    return chunks


def chunk_document(content: str, metadata: dict = None) -> List[dict]:
    """
    将文档内容切分为带元数据的块。

    Args:
        content: 文档文本内容
        metadata: 文档级元数据（如 document_id, knowledge_base_id 等）

    Returns:
        包含 content 和 metadata 的块字典列表
    """
    chunks = semantic_chunk(content)

    result = []
    for i, chunk_text in enumerate(chunks):
        chunk_meta = dict(metadata or {})
        chunk_meta["chunk_index"] = i
        result.append({
            "content": chunk_text,
            "metadata": chunk_meta,
        })

    logger.info(f"Document chunked into {len(result)} pieces")
    return result
