"""
Q1 产品确认节点：前置路由（主体提取 + 主体向量匹配 + 可答范围判定）

- LLM 结构化判断（仿 Jev Choice，判定规则固定在提示词，路由在代码中）
- 主体向量匹配：kb_rag_item_names 检索最相近主体，score >= 阈值才绑定
"""

import json
import re

from kb_rag.config import get_config
from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.prompt import PRODUCT_CONFIRM
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.embedding_utils import embed_query
from kb_rag.utils.llm_utils import chat
from kb_rag.utils.milvus_utils import get_milvus_client, search_item_names

logger = setup_logger("query.confirm")


class NodeProductConfirm(QueryNodeBase):
    """Q1 产品确认节点"""

    name = "node_product_confirm"

    ITEM_MATCH_MIN_SCORE = 0.60   # 主体向量匹配最低相似度

    def process(self, state: QueryGraphState):
        question = state.get("question")
        if not question:
            raise ValueError("question 不能为空")

        # 步骤0：用户显式限定文档 = 明确意图，跳过拒答路由直接检索（可答门控兜底）
        if state.get("filter_doc_name"):
            logger.info(f"【产品确认】已限定文档 {state['filter_doc_name']!r}，跳过路由直接检索")
            return {"item_name": "", "confirm_decision": "search", "confirm_reason": "用户指定文档范围"}

        # 步骤1：列出知识库已有主体
        items = self._list_items()

        # 步骤2：LLM 结构化判断
        llm_result = self._confirm_with_llm(question, items)

        # 步骤3：主体向量匹配（库内最近主体）
        item_name = ""
        extracted = llm_result.get("extracted_item") or ""
        if extracted:
            hits = search_item_names(embed_query(extracted), top_k=1)
            if hits and hits[0]["score"] >= self.ITEM_MATCH_MIN_SCORE:
                item_name = hits[0]["item_name"]

        decision = llm_result.get("decision") or "search"
        if decision not in ("search", "refuse"):
            decision = "search"

        reason = llm_result.get("reason") or ""
        logger.info(f"【产品确认】decision={decision}, extracted={extracted!r}, matched={item_name!r}, reason={reason}")

        return {
            "item_name": item_name,
            "confirm_decision": decision,
            "confirm_reason": reason,
        }

    def _list_items(self) -> list:
        """拉取知识库已入库主体清单"""
        try:
            cfg = get_config()
            client = get_milvus_client()
            rows = client.query(
                cfg.item_name_collection,
                filter="",
                output_fields=["item_name"],
                limit=100,
            )
            items = sorted({r["item_name"] for r in rows if r.get("item_name")})
            logger.info(f"知识库主体清单: {items}")
            return items
        except Exception as e:
            logger.warning(f"主体清单获取失败（忽略）：{e}")
            return []

    def _confirm_with_llm(self, question: str, items: list) -> dict:
        default = {"decision": "search", "extracted_item": "", "reason": "LLM 异常，默认放行检索"}
        try:
            prompt = PRODUCT_CONFIRM.format(items="\n".join(items) or "（空）", question=question)
            answer = chat(
                [{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=512,
                disable_thinking=True,  # 判别类任务，关思考提速
            )
            answer = re.sub(r"<think>.*?</think>", "", answer or "", flags=re.DOTALL).strip()
            # 提取 JSON（容忍 markdown 代码块包裹）
            match = re.search(r"\{.*\}", answer, flags=re.DOTALL)
            if not match:
                logger.warning(f"产品确认输出无法解析：{answer[:200]}")
                return default
            parsed = json.loads(match.group(0))
            return {
                "decision": str(parsed.get("decision", "")).strip().lower(),
                "extracted_item": str(parsed.get("extracted_item", "")).strip(),
                "reason": str(parsed.get("reason", "")).strip(),
            }
        except Exception as e:
            logger.error(f"产品确认 LLM 调用失败：{e}")
            return default
