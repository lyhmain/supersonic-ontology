"""
Embedding 工具：GPUStack Qwen3-VL-8B-embedding（4096 维，dense only）

稀疏向量由 Milvus 2.5 BM25 Function 基于入库文本自动生成（见 milvus_utils）。
"""

from typing import List, Optional

from openai import OpenAI

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.embedding")

_client: Optional[OpenAI] = None


def get_embed_client() -> OpenAI:
    global _client
    if _client is None:
        cfg = get_config()
        _client = OpenAI(base_url=cfg.embed_api_base, api_key=cfg.embed_api_key)
    return _client


def embed_texts(texts: List[str], batch_size: Optional[int] = None) -> List[List[float]]:
    """
    批量文本向量化

    :param texts: 文本列表
    :return: 与输入等长的向量列表（维度 EMBEDDING_DIM）
    """
    cfg = get_config()
    client = get_embed_client()
    batch = batch_size or cfg.embedding_batch_size
    vectors: List[List[float]] = []
    for i in range(0, len(texts), batch):
        part = texts[i : i + batch]
        resp = client.embeddings.create(model=cfg.embed_model, input=part)
        part_vecs = [item.embedding for item in resp.data]
        if len(part_vecs) != len(part):
            raise RuntimeError(f"Embedding 返回数量不匹配: 期望 {len(part)} 实际 {len(part_vecs)}")
        vectors.extend(part_vecs)
        logger.debug(f"Embedding 进度: {min(i + batch, len(texts))}/{len(texts)}")
    dim = len(vectors[0]) if vectors else 0
    if vectors and dim != cfg.embedding_dim:
        raise RuntimeError(f"Embedding 维度不符: 期望 {cfg.embedding_dim} 实际 {dim}")
    return vectors


def embed_query(text: str) -> List[float]:
    """单条查询向量化"""
    return embed_texts([text])[0]
