"""导入流程节点基类（沿用原版）"""

from abc import ABC, abstractmethod

from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("import.node")


class NodeBase(ABC):

    name: str = "node_base"

    def __init__(self):
        """强制子类设置 name"""
        if self.name == "node_base":
            raise ValueError(f"{self.__class__.__name__} 必须设置 name 属性")

    def __call__(self, state: ImportGraphState):
        try:
            logger.info(f"--- {self.name} 开始执行 ---")
            result = self.process(state)
            logger.info(f"--- {self.name} 执行完成 ---")
            return result
        except Exception as e:
            logger.error(f"{self.name} 执行失败：{e}", exc_info=True)
            raise

    @abstractmethod
    def process(self, state: ImportGraphState):
        """节点的核心处理逻辑"""
        pass
