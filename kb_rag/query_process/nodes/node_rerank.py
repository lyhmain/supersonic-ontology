"""
Q6 重排节点：自适应三通道

- DashScope 云端重排（qwen3.7-text-rerank）：配置 DASHSCOPE_API_KEY 即自动启用，毫秒级
- HTTP reranker（GPUStack /v1/reranker）：部署了 reranker 模型时自动启用，毫秒级
- LLM pointwise 打分（GLM 关思考，并发 ×4）：兜底通道

断崖截断：排序后相邻分差 > 1 处截断，丢弃 0 分候选；全低分时留空集由答案节点门控处理。
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from kb_rag.config import get_config
from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.reranker_utils import dashscope_rerank, detect_dashscope, detect_http_reranker, http_rerank, llm_score_one

logger = setup_logger("query.rerank")


class NodeRerank(QueryNodeBase):
    """Q6 重排节点"""

    name = "node_rerank"

    CLIFF_GAP = 1.0     # 断崖阈值：相邻分差
    MAX_CONTEXT = 6     # 截断后最多保留条数
    MAX_WORKERS = 4     # LLM 打分并发线程数

    def process(self, state: QueryGraphState):
        question = state.get("question")
        if not question:
            raise ValueError("question 不能为空")
        fused = state.get("fused") or []
        if not fused:
            logger.warning("【重排】无候选，跳过")
            return {"reranked": []}

        # 步骤1：选择通道并打分（优先级：DashScope 云端 > HTTP reranker > LLM 打分）
        mode = get_config().reranker_mode
        if mode == "dashscope" or (mode == "auto" and detect_dashscope()):
            self._score_by_dashscope(question, fused)
        elif mode == "http" or (mode == "auto" and detect_http_reranker() is not None):
            self._score_by_http(question, fused)
        else:
            self._score_by_llm(question, fused)

        # 步骤2：排序
        ranked = sorted(fused, key=lambda x: x["rerank_score"], reverse=True)
        logger.info(f"【重排】得分: {[x['rerank_score'] for x in ranked]}")

        # 步骤3：断崖截断
        kept: List[Dict] = []
        for i, item in enumerate(ranked):
            if item["rerank_score"] <= 0:
                break
            if i >= self.MAX_CONTEXT:
                break
            if i > 0 and (ranked[i - 1]["rerank_score"] - item["rerank_score"]) > self.CLIFF_GAP:
                logger.info(f"【重排】断崖截断于第 {i} 位（分差 {ranked[i-1]['rerank_score'] - item['rerank_score']}）")
                break
            kept.append(item)

        # 步骤4：重编引用序号
        for i, item in enumerate(kept, start=1):
            item["index"] = i

        logger.info(f"【重排】保留 {len(kept)}/{len(ranked)} 条进入上下文")
        return {"reranked": kept}

    def _score_by_dashscope(self, question: str, fused: List[Dict]) -> None:
        """百炼云端重排通道（分数归一化到 0-3 语义档位，与 HTTP 通道同阈值）"""
        docs = [item.get("chunk_text") or "" for item in fused]
        try:
            results = dashscope_rerank(question, docs, top_k=len(docs))
        except Exception as e:
            logger.warning(f"DashScope rerank 失败（{e}），回退 HTTP/LLM 通道")
            if detect_http_reranker():
                self._score_by_http(question, fused)
            else:
                self._score_by_llm(question, fused)
            return

        for item in fused:
            item["rerank_score"] = 0
        for r in results:
            score = r["score"]
            item = fused[r["index"]]
            item["rerank_score"] = 3 if score >= 0.7 else 2 if score >= 0.45 else 1 if score >= 0.25 else 0
            item["rerank_raw"] = round(score, 4)
        logger.info(f"【重排】DashScope 原始分: {[round(r['score'], 3) for r in results]}")

    def _score_by_http(self, question: str, fused: List[Dict]) -> None:
        """HTTP reranker 通道：单次调用全量精排（分数归一化到 0-3 语义档位）"""
        docs = [item.get("chunk_text") or "" for item in fused]
        try:
            results = http_rerank(question, docs, top_k=len(docs))
        except Exception as e:
            logger.warning(f"HTTP rerank 失败（{e}），回退 LLM 打分")
            self._score_by_llm(question, fused)
            return

        for item in fused:
            item["rerank_score"] = 0
        for r in results:
            score = r["score"]  # 0~1 连续相关度
            item = fused[r["index"]]
            # 归一化：>=0.7 记 3（直接回答）、0.45~0.7 记 2、0.25~0.45 记 1、其余 0
            item["rerank_score"] = 3 if score >= 0.7 else 2 if score >= 0.45 else 1 if score >= 0.25 else 0
            item["rerank_raw"] = round(score, 4)

    def _score_by_llm(self, question: str, fused: List[Dict]) -> None:
        """LLM 打分通道（并发，关思考）"""
        with ThreadPoolExecutor(max_workers=self.MAX_WORKERS) as pool:
            scores = list(pool.map(lambda item: llm_score_one(question, item.get("chunk_text") or ""), fused))
        for item, score in zip(fused, scores):
            item["rerank_score"] = score
