"""
Milvus 工具：连接管理、集合创建（稠密 4096 + BM25 内置稀疏）、混合检索

- 集合统一 kb_rag_ 前缀，与同实例上的 data-agent 系列隔离
- 稀疏向量：Milvus 2.5 BM25 Function 基于 chunk_text 自动生成
"""

from typing import Dict, List, Optional

from pymilvus import DataType, Function, FunctionType, MilvusClient

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.milvus")

_client: Optional[MilvusClient] = None


def get_milvus_client() -> MilvusClient:
    """Milvus 客户端（单例）"""
    global _client
    if _client is None:
        cfg = get_config()
        _client = MilvusClient(
            uri=cfg.milvus_uri,
            user=cfg.milvus_user,
            password=cfg.milvus_password,
        )
        logger.info(f"Milvus 已连接: {cfg.milvus_uri}")
    return _client


def ensure_collections():
    """
    确保两个集合存在（幂等）：

    - kb_rag_chunks：知识切片（dense 4096 + BM25 sparse + 元数据）
    - kb_rag_item_names：文档主体（商品名）及其向量
    """
    cfg = get_config()
    client = get_milvus_client()

    # ---------- chunks 集合 ----------
    if not client.has_collection(cfg.chunks_collection):
        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("chunk_text", DataType.VARCHAR, max_length=8192, enable_analyzer=True,
                         analyzer_params={"type": "chinese"})
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=cfg.embedding_dim)
        schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("doc_name", DataType.VARCHAR, max_length=512)
        schema.add_field("title_path", DataType.VARCHAR, max_length=2048)
        schema.add_field("item_name", DataType.VARCHAR, max_length=512, default_value="")
        # BM25：由 chunk_text 自动生成稀疏向量（分词器已配置在字段上）
        schema.add_function(
            Function(
                name="bm25_sparse",
                function_type=FunctionType.BM25,
                input_field_names=["chunk_text"],
                output_field_names=["sparse"],
            )
        )
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="dense", index_type="AUTOINDEX", metric_type=cfg.milvus_metric_type)
        index_params.add_index(field_name="sparse", index_type="AUTOINDEX", metric_type="BM25")
        client.create_collection(
            cfg.chunks_collection, schema=schema, index_params=index_params
        )
        logger.info(f"集合已创建: {cfg.chunks_collection} (dense={cfg.embedding_dim} + BM25 sparse)")
    else:
        logger.info(f"集合已存在: {cfg.chunks_collection}")

    # ---------- item_names 集合 ----------
    if not client.has_collection(cfg.item_name_collection):
        schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("item_name", DataType.VARCHAR, max_length=512)
        schema.add_field("dense", DataType.FLOAT_VECTOR, dim=cfg.embedding_dim)
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="dense", index_type="AUTOINDEX", metric_type=cfg.milvus_metric_type)
        client.create_collection(
            cfg.item_name_collection, schema=schema, index_params=index_params
        )
        logger.info(f"集合已创建: {cfg.item_name_collection} (dense={cfg.embedding_dim})")
    else:
        logger.info(f"集合已存在: {cfg.item_name_collection}")


def insert_chunks(rows: List[Dict]) -> int:
    """批量写入切片（dense 由 GPUStack 生成，sparse 由 BM25 Function 自动生成）"""
    cfg = get_config()
    client = get_milvus_client()
    res = client.insert(collection_name=cfg.chunks_collection, data=rows)
    count = res.get("insert_count", 0) if isinstance(res, dict) else 0
    logger.info(f"写入切片 {count} 条 -> {cfg.chunks_collection}")
    return count


def hybrid_search_chunks(
    query_text: str,
    query_dense: List[float],
    top_k: int = 10,
    item_name: Optional[str] = None,
) -> List[Dict]:
    """
    混合检索：dense 向量 + BM25 稀疏（RRF 融合，服务端完成）

    :param item_name: 可选，按文档主体过滤
    :return: [{"chunk_text", "doc_name", "title_path", "item_name", "score"}]
    """
    cfg = get_config()
    client = get_milvus_client()

    filter_expr = f'item_name == "{item_name}"' if item_name else ""
    results = client.search(
        collection_name=cfg.chunks_collection,
        data=[query_dense],
        anns_field="dense",
        limit=top_k,
        filter=filter_expr,
        output_fields=["chunk_text", "doc_name", "title_path", "item_name"],
        search_params={"metric_type": cfg.milvus_metric_type},
    )
    hits = results[0] if results else []
    out = []
    for hit in hits:
        entity = hit.get("entity", {}) if isinstance(hit, dict) else hit.entity
        score = hit.get("distance") if isinstance(hit, dict) else hit.distance
        # 本部署 COSINE 的 distance 即相似度（越大越相似，自检索≈0.99）
        out.append(
            {
                "chunk_text": entity.get("chunk_text"),
                "doc_name": entity.get("doc_name"),
                "title_path": entity.get("title_path"),
                "item_name": entity.get("item_name"),
                "score": score,
            }
        )
    logger.info(f"混合检索命中 {len(out)} 条 (top_k={top_k}, filter={filter_expr or '无'})")
    return out


def escape_milvus_string(value: str) -> str:
    """转义 Milvus filter 表达式中的特殊字符（引号、反斜杠）"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def hybrid_search_full(
    query_text: str,
    query_dense: List[float],
    top_k: int = 10,
    item_name: Optional[str] = None,
    doc_name: Optional[str] = None,
) -> List[Dict]:
    """
    服务端混合检索：稠密向量 + BM25 全文（sparse），服务端 RRF 融合

    :param query_text: 原始查询文本（BM25 Function 服务端转稀疏向量）
    :param item_name: 可选，按文档主体过滤
    :param doc_name: 可选，按文档名过滤（前端文件目录点选）
    """
    from pymilvus import AnnSearchRequest, RRFRanker

    cfg = get_config()
    client = get_milvus_client()

    conds = []
    if item_name:
        conds.append(f'item_name == "{escape_milvus_string(item_name)}"')
    if doc_name:
        conds.append(f'doc_name == "{escape_milvus_string(doc_name)}"')
    filter_expr = " && ".join(conds)

    dense_req = AnnSearchRequest(
        data=[query_dense],
        anns_field="dense",
        param={"metric_type": cfg.milvus_metric_type},
        limit=top_k,
    )
    sparse_req = AnnSearchRequest(
        data=[query_text],  # BM25 Function：直接传原始文本，服务端转换
        anns_field="sparse",
        param={"metric_type": "BM25"},
        limit=top_k,
    )
    try:
        results = client.hybrid_search(
            collection_name=cfg.chunks_collection,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(60),
            limit=top_k,
            filter=filter_expr or None,
            output_fields=["chunk_text", "doc_name", "title_path", "item_name"],
        )
    except Exception as e:
        # 兜底：服务端混合检索不可用时退化为纯稠密检索
        logger.warning(f"hybrid_search 不可用（{e}），退化为纯稠密检索")
        return hybrid_search_chunks(query_text, query_dense, top_k, item_name)

    hits = results[0] if results else []
    out = []
    for hit in hits:
        entity = hit.get("entity", {}) if isinstance(hit, dict) else hit.entity
        score = hit.get("distance") if isinstance(hit, dict) else hit.distance
        out.append(
            {
                "chunk_text": entity.get("chunk_text"),
                "doc_name": entity.get("doc_name"),
                "title_path": entity.get("title_path"),
                "item_name": entity.get("item_name"),
                "score": score,  # RRF 融合分（无绝对意义，仅排序）
                "route": "hybrid",
            }
        )
    logger.info(f"服务端混合检索命中 {len(out)} 条 (top_k={top_k}, filter={filter_expr or '无'})")
    return out


def search_item_names(query_dense: List[float], top_k: int = 3) -> List[Dict]:
    """主体匹配：在 kb_rag_item_names 中检索最相近的已入库主体"""
    cfg = get_config()
    client = get_milvus_client()
    results = client.search(
        collection_name=cfg.item_name_collection,
        data=[query_dense],
        anns_field="dense",
        limit=top_k,
        output_fields=["item_name", "file_title"],
        search_params={"metric_type": cfg.milvus_metric_type},
    )
    hits = results[0] if results else []
    out = []
    for hit in hits:
        entity = hit.get("entity", {}) if isinstance(hit, dict) else hit.entity
        score = hit.get("distance") if isinstance(hit, dict) else hit.distance
        out.append(
            {
                "item_name": entity.get("item_name"),
                "file_title": entity.get("file_title"),
                "score": score,  # 本部署 COSINE distance 即相似度
            }
        )
    logger.info(f"主体匹配命中: {[(o['item_name'], round(o['score'], 4)) for o in out]}")
    return out


def delete_by_doc_name(doc_name: str) -> None:
    """按文档名删除切片（幂等导入），仅允许操作 kb_rag_ 前缀集合"""
    cfg = get_config()
    assert cfg.chunks_collection.startswith("kb_rag_"), "只允许操作 kb_rag_ 前缀集合"
    client = get_milvus_client()
    client.delete(cfg.chunks_collection, filter=f'doc_name == "{doc_name}"')
    logger.info(f"已删除文档切片: {doc_name}")
