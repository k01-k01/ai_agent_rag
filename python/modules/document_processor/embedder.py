"""
向量化模块 - 使用 BGE-M3 模型生成 1024 维向量
"""
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

# 全局单例，避免重复加载模型
_model = None
# 优先使用 CUDA (GPU)，其次 CPU
import torch
_device = "cuda" if torch.cuda.is_available() else "cpu"



def _get_model():
    """获取或创建 BGE-M3 模型（单例模式）"""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            import config

            logger.info(f"Loading BGE-M3 model from {config.BGE_M3_MODEL_PATH}")
            _model = SentenceTransformer(
                config.BGE_M3_MODEL_PATH,
                device=_device,
            )
            # 设置模型为评估模式
            _model.eval()
            logger.info(f"BGE-M3 model loaded successfully, device: {_device}")
        except Exception as e:
            logger.error(f"Failed to load BGE-M3 model: {e}")
            raise
    return _model


def generate_embedding(text: str) -> List[float]:
    """
    生成单个文本的向量嵌入。

    Args:
        text: 输入文本

    Returns:
        1024 维向量（float 列表）
    """
    if not text or not text.strip():
        logger.warning("Empty text provided for embedding, returning zero vector")
        return [0.0] * 1024

    model = _get_model()
    try:
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
        raise


def generate_embeddings_batch(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """
    批量生成文本向量嵌入。

    Args:
        texts: 输入文本列表
        batch_size: 批处理大小

    Returns:
        向量列表，每个向量为 1024 维 float 列表
    """
    if not texts:
        return []

    # 过滤空文本
    valid_texts = []
    valid_indices = []
    for i, t in enumerate(texts):
        if t and t.strip():
            valid_texts.append(t)
            valid_indices.append(i)

    if not valid_texts:
        return [[0.0] * 1024 for _ in range(len(texts))]

    model = _get_model()
    try:
        embeddings = model.encode(
            valid_texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        # 将结果映射回原始顺序
        result = [[0.0] * 1024 for _ in range(len(texts))]
        for idx, emb in zip(valid_indices, embeddings):
            result[idx] = emb.tolist()
        return result
    except Exception as e:
        logger.error(f"Failed to generate embeddings batch: {e}")
        raise
