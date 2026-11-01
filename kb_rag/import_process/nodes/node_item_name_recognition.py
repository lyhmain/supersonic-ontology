"""
主体识别节点：LLM 识别文档主体（商品名等）+ 主体向量入库

适配点（相对原版）：
- LLM 改用 GLM 网关（kb_rag.utils.llm_utils.chat）
- 主体向量改用 GPUStack Embedding（仅 dense，sparse 由 BM25 服务端生成）
- 主体集合 kb_rag_item_names（dense 4096；file_title 存动态字段）
"""

import re
from typing import Dict, List, Tuple

from kb_rag.config import get_config
from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.prompt import NAME_RECOGNITION
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.embedding_utils import embed_texts
from kb_rag.utils.llm_utils import chat
from kb_rag.utils.milvus_utils import escape_milvus_string, get_milvus_client

logger = setup_logger("import.item_name")


class NodeItemNameRecognition(NodeBase):
    """主体识别节点：主体识别与标签提取"""

    name = "node_item_name_recognition"

    ITEM_NAME_CHUNK_K = 5    # 取前 k 个切片用于识别
    MAX_CHARS = 2500         # 上下文总字符限制

    def process(self, state: ImportGraphState):
        # 步骤1：提取并校验输入
        file_title, chunks = self._step1_get_inputs(state)

        # 步骤2：构建识别上下文
        context = self._step2_build_context(chunks)

        # 步骤3：调用 LLM 识别主体名称
        item_name = self._step3_call_llm(file_title, context)

        # 步骤4：回填主体名称到切片
        chunks = self._step4_update_chunks(chunks, item_name)

        # 步骤5：生成主体向量并入库（幂等）
        self._step5_save_to_milvus(file_title, item_name)

        logger.info(f"主体名称识别完成: {item_name}")
        return {"chunks": chunks, "item_name": item_name}

    # ------------------------------------------------------------------
    def _step1_get_inputs(self, state: ImportGraphState) -> Tuple[str, List[Dict]]:
        file_title = state.get("file_title")
        if not file_title:
            raise ValueError("标题不能为空")
        chunks = state.get("chunks")
        if not chunks:
            raise ValueError("文本片段不能为空")
        if not isinstance(chunks, list):
            raise ValueError("文本片段必须是列表")
        return file_title, chunks

    def _step2_build_context(self, chunks: List[Dict]) -> str:
        total_chars = 0
        parts: List[str] = []
        for idx, chunk in enumerate(chunks[: self.ITEM_NAME_CHUNK_K], start=1):
            piece = f"【切片{idx}】\n标题：{chunk.get('title')}\n内容：{chunk.get('content')}"
            parts.append(piece)
            total_chars += len(piece)
            if total_chars > self.MAX_CHARS:
                logger.warning("总字符数超过限制，已截断")
                break
        return "\n\n".join(parts).strip()[: self.MAX_CHARS]

    def _step3_call_llm(self, file_title: str, context: str) -> str:
        try:
            prompt = NAME_RECOGNITION.format(file_title=file_title, context=context)
            messages = [
                {"role": "system", "content": "你是商品识别专家，只输出识别到的字符串即可"},
                {"role": "user", "content": prompt},
            ]
            item_name = (chat(messages) or "").strip()
            item_name = re.sub(r"\s+", "", item_name)
            # 剥中英文引号:LLM 偶尔输出字面量 "" / '' —— 剥完为空则回退文件名
            item_name = item_name.strip('"').strip("'").strip("“”").strip()
            return item_name or file_title
        except Exception as e:
            logger.error(f"大模型调用失败：{e}", exc_info=True)
            return file_title  # 兜底：返回文件名

    def _step4_update_chunks(self, chunks: List[Dict], item_name: str) -> List[Dict]:
        for chunk in chunks:
            chunk["item_name"] = item_name
        return chunks

    def _step5_save_to_milvus(self, file_title: str, item_name: str) -> None:
        """主体向量入库（幂等：按 file_title 删除旧记录）"""
        try:
            cfg = get_config()
            client = get_milvus_client()
            vectors = embed_texts([item_name])
            file_title_safe = escape_milvus_string(file_title)

            # 幂等删除（file_title 存于动态字段）
            client.delete(cfg.item_name_collection, filter=f'file_title == "{file_title_safe}"')

            client.insert(
                collection_name=cfg.item_name_collection,
                data=[{"item_name": item_name, "file_title": file_title, "dense": vectors[0]}],
            )
            logger.info(f"主体向量已入库：{item_name} ({cfg.item_name_collection})")
        except Exception as e:
            logger.error(f"主体向量入库失败：{e}", exc_info=True)
