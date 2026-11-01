"""拒绝节点：产品确认判定为范围外时，礼貌拒绝并写入历史"""

from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.history_utils import add_message

logger = setup_logger("query.refuse")

REFUSE_TEXT = (
    "抱歉，这个问题超出了知识库的服务范围。"
    "本知识库聚焦已收录产品/技术文档的用法、故障与参数等问题，暂无法回答闲聊或无关话题。"
)


class NodeRefuse(QueryNodeBase):
    """拒绝节点"""

    name = "node_refuse"

    def process(self, state: QueryGraphState):
        session_id = state.get("session_id") or ""
        question = state.get("question") or ""
        reason = state.get("confirm_reason") or ""

        if session_id:
            add_message(session_id, "user", question)
            add_message(session_id, "assistant", REFUSE_TEXT, refs=[])

        logger.info(f"【拒绝】reason={reason}")
        return {"answer": REFUSE_TEXT, "refs": []}
