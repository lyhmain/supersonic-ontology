"""Q2 向量搜索节点：服务端混合检索（dense + BM25，RRF 融合）"""

from typing import Tuple

from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.embedding_utils import embed_query
from kb_rag.utils.milvus_utils import hybrid_search_full

logger = setup_logger("query.search")


class NodeVectorSearch(QueryNodeBase):
    """Q2 向量搜索节点"""

    name = "node_vector_search"

    TOP_K = 10

    def process(self, state: QueryGraphState) -> Tuple:
        question = state.get("question")
        if not question:
            raise ValueError("question 不能为空")

        item_name = state.get("item_name") or None
        doc_name = state.get("filter_doc_name") or None
        hits = hybrid_search_full(question, embed_query(question), top_k=self.TOP_K, item_name=item_name, doc_name=doc_name)

        # 无主体绑定时兜底：去掉过滤再检索一次（宁多勿漏，重排阶段再筛）
        if not hits and (item_name or doc_name):
            logger.warning(f"按主体 {item_name!r}/文档 {doc_name!r} 无命中，放宽过滤重检")
            hits = hybrid_search_full(question, embed_query(question), top_k=self.TOP_K, item_name=None, doc_name=None)

        return {"hybrid_hits": hits}
