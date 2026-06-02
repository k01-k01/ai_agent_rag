"""
文本切分模块 - 使用递归字符切分（Recursive Character Text Splitting）
将文档内容按固定大小切分为合适的文本块

设计说明：
- 默认使用 RecursiveCharacterTextSplitter（递归字符切分）
- 相比 SemanticChunker（语义切分），速度快 10-100 倍
- 语义切分需要数十次 embedding 推理，递归切分只需毫秒级
- 对于中文文档，递归切分在段落/句子边界分割，效果已经很好

性能对比（30KB 文档）：
- 语义切分（旧）：数十次 BGE-M3 推理，耗时数秒
- 递归切分（新）：0 次模型推理，耗时 < 10ms
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


def recursive_chunk(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> List[str]:
    """
    使用 RecursiveCharacterTextSplitter 进行递归字符切分。

    优先在段落边界（双换行）分割，其次在句子边界（句号/感叹号/问号等），
    最后在字符边界分割。确保每个块大小可控。

    Args:
        text: 要切分的文本
        chunk_size: 目标块大小（字符数）
        chunk_overlap: 块之间的重叠字符数

    Returns:
        切分后的文本块列表
    """
    if not text or not text.strip():
        return []

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

    使用递归字符切分（RecursiveCharacterTextSplitter），
    相比语义切分（SemanticChunker）速度提升 10-100 倍。

    Args:
        content: 文档文本内容
        metadata: 文档级元数据（如 document_id, knowledge_base_id 等）

    Returns:
        包含 content 和 metadata 的块字典列表
    """
    chunks = recursive_chunk(content)

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
