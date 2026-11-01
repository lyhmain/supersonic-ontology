"""
知识库检索问答工作流（LangGraph 编排）

产品确认 →（search）→ 混合检索 → HyDE → 网络搜索(可选) → RRF融合 → LLM重排 → Jev段落过滤 → 答案输出 → END
         └→（refuse）→ 拒绝节点 → END
"""

from typing import Optional

from langgraph.constants import END
from langgraph.graph import StateGraph

from kb_rag.query_process.nodes.node_answer import NodeAnswer
from kb_rag.query_process.nodes.node_fusion import NodeFusion
from kb_rag.query_process.nodes.node_hyde import NodeHyDE
from kb_rag.query_process.nodes.node_jev_filter import NodeJevFilter
from kb_rag.query_process.nodes.node_product_confirm import NodeProductConfirm
from kb_rag.query_process.nodes.node_refuse import NodeRefuse
from kb_rag.query_process.nodes.node_rerank import NodeRerank
from kb_rag.query_process.nodes.node_vector_search import NodeVectorSearch
from kb_rag.query_process.nodes.node_web_search import NodeWebSearch
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("query.graph")


class KBQueryWorkflow:
    """知识库检索问答工作流类"""

    def __init__(self):
        self.workflow = StateGraph(QueryGraphState)
        self._init_nodes()
        self._register_nodes()
        self._setup_routes()
        self._compiled_app: Optional[object] = None

    def _init_nodes(self):
        self.node_product_confirm = NodeProductConfirm()
        self.node_refuse = NodeRefuse()
        self.node_vector_search = NodeVectorSearch()
        self.node_hyde = NodeHyDE()
        self.node_web_search = NodeWebSearch()
        self.node_fusion = NodeFusion()
        self.node_rerank = NodeRerank()
        self.node_jev_filter = NodeJevFilter()
        self.node_answer = NodeAnswer()

    def _register_nodes(self):
        self.workflow.add_node("node_product_confirm", self.node_product_confirm)
        self.workflow.add_node("node_refuse", self.node_refuse)
        self.workflow.add_node("node_vector_search", self.node_vector_search)
        self.workflow.add_node("node_hyde", self.node_hyde)
        self.workflow.add_node("node_web_search", self.node_web_search)
        self.workflow.add_node("node_fusion", self.node_fusion)
        self.workflow.add_node("node_rerank", self.node_rerank)
        self.workflow.add_node("node_jev_filter", self.node_jev_filter)
        self.workflow.add_node("node_answer", self.node_answer)

    def _route_after_confirm(self, state: QueryGraphState) -> str:
        return "refuse" if state.get("confirm_decision") == "refuse" else "search"

    def _setup_routes(self):
        self.workflow.set_entry_point("node_product_confirm")
        self.workflow.add_conditional_edges(
            "node_product_confirm",
            self._route_after_confirm,
            {"refuse": "node_refuse", "search": "node_vector_search"},
        )
        self.workflow.add_edge("node_refuse", END)
        self.workflow.add_edge("node_vector_search", "node_hyde")
        self.workflow.add_edge("node_hyde", "node_web_search")
        self.workflow.add_edge("node_web_search", "node_fusion")
        self.workflow.add_edge("node_fusion", "node_rerank")
        self.workflow.add_edge("node_rerank", "node_jev_filter")
        self.workflow.add_edge("node_jev_filter", "node_answer")
        self.workflow.add_edge("node_answer", END)

    def compile(self):
        if not self._compiled_app:
            self._compiled_app = self.workflow.compile()
        return self._compiled_app

    def run(self, init_state: QueryGraphState, stream: bool = False):
        if not self._compiled_app:
            self.compile()
        if stream:
            return self._compiled_app.stream(init_state)
        return self._compiled_app.invoke(init_state)

    @classmethod
    def create_and_run(cls, init_state: QueryGraphState, stream: bool = False):
        return cls().run(init_state, stream)
