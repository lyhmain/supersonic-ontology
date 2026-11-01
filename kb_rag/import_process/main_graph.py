"""
知识库导入工作流（LangGraph 编排，沿用原版拓扑）

入口 → (PDF: MinerU解析) → 图片处理 → 切片 → 主体识别 → 向量化 → 入库 → END
"""

from typing import Optional

from langgraph.constants import END
from langgraph.graph import StateGraph

from kb_rag.import_process.nodes.node_document_split import NodeDocumentSplit
from kb_rag.import_process.nodes.node_embedding import NodeEmbedding
from kb_rag.import_process.nodes.node_entry import NodeEntry
from kb_rag.import_process.nodes.node_import_milvus import NodeImportMilvus
from kb_rag.import_process.nodes.node_item_name_recognition import NodeItemNameRecognition
from kb_rag.import_process.nodes.node_md_img import NodeMDImg
from kb_rag.import_process.nodes.node_pdf_to_md import NodePDFToMD
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("import.graph")


class KBImportWorkflow:
    """知识库导入工作流类"""

    def __init__(self):
        self.workflow = StateGraph(ImportGraphState)
        self._init_nodes()
        self._register_nodes()
        self._setup_routes()
        self._compiled_app: Optional[object] = None

    def _init_nodes(self):
        self.node_entry = NodeEntry()
        self.node_pdf_to_md = NodePDFToMD()
        self.node_md_img = NodeMDImg()
        self.node_document_split = NodeDocumentSplit()
        self.node_item_name_recognition = NodeItemNameRecognition()
        self.node_embedding = NodeEmbedding()
        self.node_import_milvus = NodeImportMilvus()

    def _register_nodes(self):
        self.workflow.add_node("node_entry", self.node_entry)
        self.workflow.add_node("node_pdf_to_md", self.node_pdf_to_md)
        self.workflow.add_node("node_md_img", self.node_md_img)
        self.workflow.add_node("node_document_split", self.node_document_split)
        self.workflow.add_node("node_item_name_recognition", self.node_item_name_recognition)
        self.workflow.add_node("node_embedding", self.node_embedding)
        self.workflow.add_node("node_import_milvus", self.node_import_milvus)

    def _route_after_entry(self, state: ImportGraphState) -> str:
        if state.get("is_md_read_enabled"):
            return "md_read"
        elif state.get("is_pdf_read_enabled"):
            return "pdf_read"
        return END

    def _setup_routes(self):
        self.workflow.set_entry_point("node_entry")
        self.workflow.add_conditional_edges(
            "node_entry",
            self._route_after_entry,
            {"md_read": "node_md_img", "pdf_read": "node_pdf_to_md", END: END},
        )
        self.workflow.add_edge("node_pdf_to_md", "node_md_img")
        self.workflow.add_edge("node_md_img", "node_document_split")
        self.workflow.add_edge("node_document_split", "node_item_name_recognition")
        self.workflow.add_edge("node_item_name_recognition", "node_embedding")
        self.workflow.add_edge("node_embedding", "node_import_milvus")
        self.workflow.add_edge("node_import_milvus", END)

    def compile(self):
        if not self._compiled_app:
            self._compiled_app = self.workflow.compile()
        return self._compiled_app

    def run(self, init_state: ImportGraphState, stream: bool = False):
        if not self._compiled_app:
            self.compile()
        if stream:
            return self._compiled_app.stream(init_state)
        return self._compiled_app.invoke(init_state)

    @classmethod
    def create_and_run(cls, init_state: ImportGraphState, stream: bool = False):
        return cls().run(init_state, stream)
