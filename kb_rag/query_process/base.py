"""检索流程节点基类"""

from abc import ABC, abstractmethod

from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("query.node")


class QueryNodeBase(ABC):

    name: str = "query_node_base"

    def __init__(self):
        if self.name == "query_node_base":
            raise ValueError(f"{self.__class__.__name__} 必须设置 name 属性")

    def __call__(self, state: QueryGraphState):
        try:
            logger.info(f"--- {self.name} 开始执行 ---")
            result = self.process(state)
            logger.info(f"--- {self.name} 执行完成 ---")
            return result
        except Exception as e:
            logger.error(f"{self.name} 执行失败：{e}", exc_info=True)
            raise

    @abstractmethod
    def process(self, state: QueryGraphState):
        """节点的核心处理逻辑"""
        pass
