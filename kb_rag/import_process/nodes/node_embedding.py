"""
向量化节点：GPUStack Qwen3-VL-8B-embedding（dense 4096）

适配点（相对原版 BGE-M3 双向量）：仅生成稠密向量；
稀疏向量由 Milvus BM25 Function 基于入库文本在服务端自动生成。
"""

from typing import Dict, List

from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.embedding_utils import embed_texts

logger = setup_logger("import.embedding")


class NodeEmbedding(NodeBase):
    """向量化节点：切片批量稠密向量化"""

    name = "node_embedding"

    BATCH_SIZE = 4  # GPUStack 批大小（保守，避免网关超时）

    def process(self, state: ImportGraphState):
        chunks = self._step1_validate_input(state)
        output_data = self._step2_generate_embeddings(chunks)
        return {"chunks": output_data}

    def _step1_validate_input(self, state: ImportGraphState) -> List[Dict]:
        chunks = state.get("chunks")
        if not chunks:
            raise ValueError("chunks不能为空")
        if not isinstance(chunks, list):
            raise ValueError("chunks数据类型不正确")
        return chunks

    def _step2_generate_embeddings(self, chunks: List[Dict[str, str]]) -> List[Dict]:
        output_data = []
        for i in range(0, len(chunks), self.BATCH_SIZE):
            batch_chunks = chunks[i : i + self.BATCH_SIZE]
            # item_name 前置拼接，增强主体语义（沿用原版思路）
            texts = [f"{chunk['item_name']}\n{chunk['content']}" for chunk in batch_chunks]
            dense_vectors = embed_texts(texts)
            for j, chunk in enumerate(batch_chunks):
                chunk["dense"] = dense_vectors[j]
                output_data.append(chunk)
            logger.info(f"向量化进度: {min(i + self.BATCH_SIZE, len(chunks))}/{len(chunks)}")
        return output_data
