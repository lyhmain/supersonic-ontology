"""
Q4 网络搜索节点：百炼联网搜索（可选模块）

未配置 DASHSCOPE_API_KEY 或 DASHSCOPE_BASE_URL 时自动跳过（PRD v1.1：接口留适配器）。
搜索失败不阻塞主流程，仅记录告警并返回空集。
"""

from typing import Dict

from kb_rag.config import get_config
from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.websearch_utils import web_search

logger = setup_logger("query.web")


class NodeWebSearch(QueryNodeBase):
    """Q4 网络搜索节点（可选）"""

    name = "node_web_search"

    MAX_WEB_HITS = 5   # 进入融合的网页条数上限

    def process(self, state: QueryGraphState):
        cfg = get_config()
        if not (cfg.dashscope_api_key and cfg.dashscope_base_url):
            logger.info("【网络搜索】未配置百炼（DASHSCOPE_API_KEY），跳过")
            return {"web_hits": []}

        question = state.get("question") or ""
        try:
            results = web_search(question, top_k=self.MAX_WEB_HITS)
        except Exception as e:
            logger.warning(f"【网络搜索】搜索失败，跳过：{e}")
            return {"web_hits": []}

        # 与 Milvus 命中同构（chunk_text 为融合去重键；title_path 存网页链接）
        web_hits: list = []
        for r in results:
            text = ((r.get("title") or "") + "\n" + (r.get("snippet") or "")).strip()
            if not text:
                continue
            web_hits.append({
                "chunk_text": text,
                "doc_name": "网络搜索",
                "title_path": r.get("url") or "",
                "item_name": "",
                "score": None,
                "route": "web",
            })

        logger.info(f"【网络搜索】获取 {len(web_hits)} 条网页来源")
        return {"web_hits": web_hits}
