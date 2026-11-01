"""Q5 结果融合节点：多路召回客户端 RRF 融合去重"""

from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("query.fusion")


class NodeFusion(QueryNodeBase):
    """Q5 结果融合节点（RRF）"""

    name = "node_fusion"

    RRF_K = 60          # RRF 常数
    FUSED_TOP_K = 10    # 融合后保留候选数

    def process(self, state: QueryGraphState):
        routes = [
            ("hybrid", state.get("hybrid_hits") or []),
            ("hyde", state.get("hyde_hits") or []),
            ("web", state.get("web_hits") or []),
        ]

        # 以 chunk 内容为唯一键去重，RRF 累计得分
        scores: dict = {}
        metas: dict = {}
        for route_name, hits in routes:
            for rank, hit in enumerate(hits, start=1):
                key = (hit.get("chunk_text") or "")[:200]
                if not key:
                    continue
                scores.setdefault(key, 0.0)
                scores[key] += 1.0 / (self.RRF_K + rank)
                if key not in metas:
                    metas[key] = hit

        fused = []
        for key, rrf_score in sorted(scores.items(), key=lambda x: x[1], reverse=True)[: self.FUSED_TOP_K]:
            item = dict(metas[key])
            item["route"] = metas[key].get("route") or "web"
            item["fused_score"] = rrf_score
            fused.append(item)

        for i, item in enumerate(fused, start=1):
            item["index"] = i  # 答案引用编号 [i]

        routes_desc = {name: len(hits) for name, hits in routes}
        logger.info(f"【融合】各路召回 {routes_desc} → 融合去重后 {len(fused)} 条")
        return {"fused": fused}
