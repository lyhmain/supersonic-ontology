"""入口节点：任务分发（沿用原版）"""

import os
from os.path import splitext

from pydantic import ValidationError

from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("import.entry")


class NodeEntry(NodeBase):
    """入口节点：任务分发"""

    name = "node_entry"

    def process(self, state: ImportGraphState):
        logger.info(f"【{self.name}】程序的入口节点")

        # 1. 参数非空校验
        local_file_path = state.get("local_file_path")
        if not local_file_path:
            raise ValueError("请指定文件路径")

        # 2. 提取文件名称
        file_title = splitext(os.path.basename(local_file_path))[0]

        # 3. 文件类型检查
        if local_file_path.endswith(".pdf"):
            logger.info(f"PDF文件检查：{local_file_path}")
            return {
                "is_pdf_read_enabled": True,
                "pdf_path": local_file_path,
                "file_title": file_title,
            }

        elif local_file_path.endswith(".md"):
            logger.info(f"MD文件检查：{local_file_path}")
            return {
                "is_md_read_enabled": True,
                "md_path": local_file_path,
                "file_title": file_title,
            }

        else:
            current_type = local_file_path[local_file_path.rfind(".") + 1:]
            raise ValidationError(f"不支持的文件类型：{current_type}")
