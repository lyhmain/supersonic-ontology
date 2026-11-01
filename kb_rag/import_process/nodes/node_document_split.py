"""文档切片节点：智能文档切片（沿用原版算法）"""

import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("import.split")


class NodeDocumentSplit(NodeBase):
    """文档切分节点：按标题结构化切片 + 长切短合"""

    name = "node_document_split"

    DEFAULT_MAX_CONTENT_LENGTH = 500   # 超长触发二次切分
    MIN_CONTENT_LENGTH = 100           # 短合并阈值
    DEFAULT_WINDOW_OVERLAP = 50        # 二次切分 overlap

    def process(self, state: ImportGraphState):
        content, file_title = self._step1_get_inputs(state)

        sections, title_count, lines_count = self._step2_split_by_titles(content, file_title)

        sections = self._step3_refine_chunks(sections)

        self._step4_print_stats(lines_count, sections)
        self._step5_backup(state, sections)

        return {"chunks": sections}

    # ------------------------------------------------------------------
    def _step1_get_inputs(self, state: ImportGraphState) -> Tuple[str, str]:
        file_title = state.get("file_title")
        if not file_title:
            raise ValueError("文件标题不能为空")
        md_content = state.get("md_content")
        if not md_content:
            raise ValueError("文件内容不能为空")
        return md_content, file_title

    def _step2_split_by_titles(self, content, file_title) -> Tuple[List[Dict[str, str]], int, int]:
        title_pattern = r"^\s*#{1,6}\s+.+"

        in_code_block = False
        code_fence = None
        current_lines = []
        sections = []
        current_title = ""
        title_count = 0

        def _flush_section():
            if not current_lines:
                return
            nonlocal title_count
            title_count += 1
            sections.append({
                "file_title": file_title,
                "title": current_title or "无标题",
                "content": "\n".join(current_lines),
            })

        content = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = content.split("\n")
        for line in lines:
            stripped_line = line.strip()

            # 代码围栏识别（``` 或 ~~~，支持多字符围栏）
            code_match = re.match(r"^(`{3,}|~{3,})", stripped_line)
            if code_match:
                marker = code_match.group(1)
                if not in_code_block:
                    in_code_block = True
                    code_fence = marker
                elif code_fence == marker:
                    in_code_block = False
                    code_fence = None

            is_valid_title = not in_code_block and re.match(title_pattern, stripped_line)
            if is_valid_title:
                _flush_section()
                current_title = stripped_line
                current_lines = [current_title]
            else:
                current_lines.append(stripped_line)

        _flush_section()
        logger.info(f"文档粗切完成：标题 {title_count} 个，章节 {len(sections)} 个，共 {len(lines)} 行")

        return sections, title_count, len(lines)

    def _step3_refine_chunks(self, sections: List[Dict[str, str]]) -> List[Dict[str, str]]:
        refined_split = []
        for sec in sections:
            refined_split.extend(self._split_long_section(sec))
        logger.info(f"切分超长章节完成，共 {len(refined_split)} 个 chunk")

        final_sections = self._merge_short_sections(refined_split)
        logger.info(f"合并短章节完成，共 {len(final_sections)} 个 chunk")

        for sec in final_sections:
            if not sec.get("parent_title"):
                sec["parent_title"] = sec.get("title")
            if not sec.get("part"):
                sec["part"] = 0

        return final_sections

    def _split_long_section(self, section: Dict[str, str]) -> List[Dict[str, str]]:
        content = section.get("content")
        if len(content) <= self.DEFAULT_MAX_CONTENT_LENGTH:
            return [section]

        # 含 HTML 表格等特殊内容时不切，保持完整
        if "<table" in content.lower():
            return [section]

        title = section.get("title")
        prefix = f"{title}\n\n"
        available_len = self.DEFAULT_MAX_CONTENT_LENGTH - len(prefix)
        if available_len <= 0:
            logger.warning(f"章节标题过长，无法二次切分：{title}")
            return [section]

        body = content
        if title and body.startswith(title):
            body = body[body.find(title) + len(title):].lstrip()

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=available_len,
            chunk_overlap=self.DEFAULT_WINDOW_OVERLAP,
            separators=["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", " "],
        )

        sub_sections = []
        for idx, chunk in enumerate(splitter.split_text(body), start=1):
            text = chunk.strip()
            if not text:
                continue
            sub_sections.append({
                "title": f"{title} - {idx}",
                "content": f"{prefix}{text}",
                "parent_title": title,
                "part": idx,
                "file_title": section.get("file_title"),
            })

        logger.info(f"章节{title}【二次切分】完成，共 {len(sub_sections)} 个子章节")
        return sub_sections

    def _merge_short_sections(self, sections: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not sections:
            return []

        merged_sections = []
        current_chunk = None

        for sec in sections:
            if current_chunk is None:
                current_chunk = sec
                continue

            is_current_short = len(current_chunk["content"]) < self.MIN_CONTENT_LENGTH
            is_same_parent = current_chunk.get("parent_title") == sec.get("parent_title")

            if is_current_short and is_same_parent:
                parent_title = sec.get("parent_title", "")
                next_content = sec.get("content")
                if parent_title and next_content.startswith(parent_title):
                    next_content = next_content[len(parent_title):].lstrip()
                current_chunk["content"] += "\n\n" + next_content
                if "part" in sec:
                    current_chunk["part"] = sec["part"]
                logger.info(f"合并短Chunk：{current_chunk.get('parent_title')} → 累计 {len(current_chunk['content'])} 字")
            else:
                merged_sections.append(current_chunk)
                current_chunk = sec

        if current_chunk is not None:
            merged_sections.append(current_chunk)

        return merged_sections

    # ------------------------------------------------------------------
    def _step4_print_stats(self, lines_count: int, sections: List[Dict[str, str]]) -> None:
        logger.info("-" * 30 + " 文档切分统计 " + "-" * 30)
        logger.info(f"MD 原始文本总行数：{lines_count}")
        logger.info(f"最终生成 Chunk 数量：{len(sections)}")

    def _step5_backup(self, state: ImportGraphState, sections: List[Dict[str, str]]) -> None:
        try:
            backup_path = Path(state.get("local_dir") or ".") / state.get("file_title") / "chunks.json"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            with open(backup_path, "w", encoding="utf-8") as f:
                json.dump(sections, f, ensure_ascii=False, indent=2)
            logger.info(f"Chunk 结果备份成功：{backup_path}")
        except Exception as e:
            logger.error(f"Chunk 结果备份失败：{e}")
