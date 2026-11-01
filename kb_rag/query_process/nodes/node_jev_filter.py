"""
Q6.5 Jev 段落过滤节点：重排之后、生成之前的有边界判定

- 对重排候选做四问判定（注入 / 矛盾 / 相关 / 证据），按固定顺序阈值路由
- 产出 证据块 / 冲突块 两组上下文，供答案节点双块生成
- fail-open：判定基座不可用时全部按原样放行（不因判定层故障拒绝服务）
- 开关：JEV_FILTER_ENABLED（默认 true；消融实验可关）
"""

import os

from kb_rag.config import get_config
from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.jev_templates import filter_passages
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("query.jev_filter")


class NodeJevFilter(QueryNodeBase):
    """Jev 段落过滤节点"""

    name = "node_jev_filter"

    def process(self, state: QueryGraphState):
        if os.getenv("JEV_FILTER_ENABLED", "true").lower() != "true":
            reranked = state.get("reranked") or []
            return {"evidence": reranked, "conflicts": [],
                    "jev_stats": {"enabled": False, "in": len(reranked),
                                  "evidence": len(reranked), "conflict": 0, "dropped": 0}}

        question = state.get("question")
        reranked = state.get("reranked") or []
        if not question or not reranked:
            return {"evidence": reranked, "conflicts": [],
                    "jev_stats": {"enabled": False, "in": len(reranked),
                                  "evidence": len(reranked), "conflict": 0, "dropped": 0}}

        result = filter_passages(question, reranked)

        # 全部被丢弃时的兜底：只在【非注入】候选里留最高分一条交给答案节点门控；
        # 若全部疑似注入，则不保留（evidence 为空 → 答案节点门控直接拒答）
        if not result["evidence"] and not result["conflicts"] and reranked:
            from kb_rag.query_process.jev_templates import PASSAGE_FILTER_THRESHOLDS
            safe = [it for it in reranked
                    if (it.get("jev") or {}).get("injection", 0) <= PASSAGE_FILTER_THRESHOLDS["injection_max"]]
            if safe:
                top = sorted(safe, key=lambda x: x.get("rerank_score", 0), reverse=True)[0]
                top["jev"] = dict(top.get("jev") or {}, route="include", keep_top_fallback=True)
                result["evidence"] = [top]
                logger.info("【Jev过滤】全部丢弃，按门控兜底保留最高分 1 条（已排除注入候选）")
            else:
                logger.warning("【Jev过滤】全部候选疑似提示注入，不保留任何内容，交由门控拒答")

        return {"evidence": result["evidence"], "conflicts": result["conflicts"],
                "jev_stats": result["stats"]}
