"""
导入向量库节点：切片数据持久化到 Milvus kb_rag_chunks

适配点（相对原版）：
- Schema 对齐 M0 建好的 kb_rag_chunks：chunk_text（BM25 输入）/ dense / doc_name / title_path / item_name
- 稀疏向量由 BM25 Function 服务端自动生成，行数据不含 sparse 字段
- 幂等：按 doc_name 先删后插（仅限 kb_rag_ 前缀集合）
"""

import json
from typing import Any, Dict, List

from kb_rag.config import get_config
from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.milvus_utils import get_milvus_client

logger = setup_logger("import.milvus")


class NodeImportMilvus(NodeBase):
    """导入向量库节点：数据持久化"""

    name = "node_import_milvus"

    def process(self, state: ImportGraphState):
        # 步骤1：输入校验
        file_title, chunks = self._step1_check_input(state)

        # 步骤2：幂等清理同文档旧数据
        self._step2_clean_old_data(file_title)

        # 步骤3：构造行数据并批量插入
        self._step3_insert_data(file_title, chunks)

        return {"chunks": chunks}

    def _step1_check_input(self, state: Dict[str, Any]):
        file_title = state.get("file_title")
        if not file_title:
            raise ValueError("file_title不能为空")

        chunks = state.get("chunks")
        if not chunks:
            raise ValueError("chunks不能为空")
        if not isinstance(chunks, list):
            raise ValueError("chunks数据类型不正确")

        first = chunks[0]
        if "dense" not in first:
            raise ValueError("数据中缺失 dense 字段（向量化节点未执行？）")
        if "content" not in first:
            raise ValueError("数据中缺失 content 字段")

        return file_title, chunks

    def _step2_clean_old_data(self, file_title: str) -> None:
        from kb_rag.utils.milvus_utils import delete_by_doc_name

        delete_by_doc_name(file_title)

    def _step3_insert_data(self, file_title: str, chunks: List[Dict]) -> None:
        cfg = get_config()
        client = get_milvus_client()

        rows = []
        for chunk in chunks:
            content = chunk.get("content") or ""
            # chunk_text 为 BM25 输入字段，超长截断（Schema max_length=8192）
            chunk_text = content[:8000]
            title = chunk.get("title") or "无标题"
            parent = chunk.get("parent_title") or title
            title_path = title if parent == title else f"{parent} / {title}"

            rows.append({
                "chunk_text": chunk_text,
                "dense": chunk["dense"],
                "doc_name": file_title,
                "title_path": title_path[:2000],
                "item_name": chunk.get("item_name") or "",
                # 动态字段：保留原版切片元数据便于溯源
                "title": title[:400],
                "part": int(chunk.get("part") or 0),
            })

        # 分批插入（避免单次 payload 过大）
        batch = 32
        inserted = 0
        for i in range(0, len(rows), batch):
            res = client.insert(collection_name=cfg.chunks_collection, data=rows[i : i + batch])
            inserted += res.get("insert_count", 0) if isinstance(res, dict) else batch

        logger.info(f"切片入库完成：{inserted}/{len(rows)} 条 -> {cfg.chunks_collection} (doc={file_title})")


if __name__ == "__main__":
    # 单节点调试：读取切片备份 JSON（需已含 dense 向量）
    path = input("chunks json path: ").strip()
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    NodeImportMilvus()({"file_title": "debug", "chunks": data})
