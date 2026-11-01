"""
Markdown 图片处理节点：多模态图片理解（GPUStack Qwen3.6-VL）

适配点（相对原版）：
- VLM 调用改用 kb_rag.utils.llm_utils.vl_chat（GPUStack）
- MinIO 改用 kb_rag.utils.minio_utils（自建桶 knowledge-base）
"""

import base64
import json
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Tuple

from minio.deleteobjects import DeleteObject

from kb_rag.config import get_config
from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.prompt import IMAGE_SUMMARY
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.llm_utils import vl_chat
from kb_rag.utils.minio_utils import get_minio_client, guess_content_type

logger = setup_logger("import.md_img")


class NodeMDImg(NodeBase):
    """MarkDown 图片处理节点：多模态图片理解"""

    name = "node_md_img"

    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
    RATE_LIMIT_MAX_REQUESTS = 10   # 滑动窗口限速：60s 内最多 10 次
    RATE_LIMIT_WINDOW = 60

    def process(self, state: ImportGraphState):
        # 步骤1：获取 md 内容、文件路径、图片目录
        md_content, md_path_obj, images_dir = self._step1_get_content(state)
        if not images_dir.exists():
            logger.info(f"【图片处理】图片目录不存在：{images_dir}，跳过")
            return {"md_content": md_content}

        # 步骤2：扫描 md 中引用的图片
        images = self._step2_scan_images(md_content, images_dir)
        if not images:
            logger.info("【图片处理】无图片需要处理，跳过")
            return {"md_content": md_content}

        # 步骤3：生成图片摘要（VLM，带限速）
        file_title = md_path_obj.stem
        summaries = self._step3_generate_summaries(file_title, images)

        # 步骤4：上传 MinIO 并替换 md 中引用
        new_md_content = self._step4_upload_and_replace(file_title, images, summaries, md_content)

        # 步骤4.5：备份新 md 文件
        new_md_path = self._step5_backup_new_md_file(state["md_path"], new_md_content)

        return {"md_content": new_md_content, "md_path": new_md_path}

    # ------------------------------------------------------------------
    def _step1_get_content(self, state: ImportGraphState):
        md_path = state.get("md_path")
        if not md_path:
            raise ValueError("请指定MD文件路径")
        md_path_obj = Path(md_path)
        if not md_path_obj.exists():
            raise FileNotFoundError(f"指定的MD文件不存在：{md_path}")
        with open(md_path, "r", encoding="utf-8") as f:
            md_content = f.read()
        images_dir = md_path_obj.parent / "images"
        return md_content, md_path_obj, images_dir

    def _step2_scan_images(self, md_content: str, images_dir: Path) -> List[Tuple[str, str, Tuple[str, str]]]:
        images = []
        for image_file in os.listdir(images_dir):
            file_ext = Path(image_file).suffix.lower()
            if file_ext not in self.IMAGE_EXTENSIONS:
                logger.warning(f"【图片处理】无效图片后缀：{file_ext}")
                continue
            context = self._find_image_in_md(md_content, image_file)
            if not context:
                logger.warning(f"【图片处理】图片未被MD引用：{image_file}")
                continue
            images.append((image_file, str(images_dir / image_file), context))
        logger.info(f"【图片处理】共扫描到 {len(images)} 张被引用图片")
        return images

    def _find_image_in_md(self, md_content: str, image_file: str, content_len: int = 100):
        pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r"\)")
        match = pattern.search(md_content)
        if not match:
            return None
        start, end = match.span()
        pre_text = md_content[max(0, start - content_len):start]
        post_text = md_content[end:min(end + content_len, len(md_content))]
        return pre_text, post_text

    def _step3_generate_summaries(self, file_title: str, images) -> Dict[str, str]:
        summaries = {}
        window: Deque[float] = deque()
        for image_file, image_path, context in images:
            self._apply_rate_limit(window)
            summaries[image_file] = self._summarize_image(image_path, file_title, context)
        return summaries

    def _apply_rate_limit(self, window: Deque[float]):
        """滑动窗口限速器（沿用原版思路）"""
        now = time.time()
        while window and now - window[0] >= self.RATE_LIMIT_WINDOW:
            window.popleft()
        if len(window) >= self.RATE_LIMIT_MAX_REQUESTS:
            sleep_for = self.RATE_LIMIT_WINDOW - (now - window[0])
            if sleep_for > 0:
                logger.info(f"【限速】等待 {sleep_for:.1f}s")
                time.sleep(sleep_for)
            now = time.time()
            while window and now - window[0] >= self.RATE_LIMIT_WINDOW:
                window.popleft()
        window.append(now)

    @staticmethod
    def _strip_think(text: str) -> str:
        """思考型模型：去除 <think>...</think> 推理段"""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    def _summarize_image(self, image_path: str, file_title: str, context: Tuple[str, str]) -> str:
        try:
            with open(image_path, "rb") as f:
                image_base64 = base64.b64encode(f.read()).decode("utf-8")

            prompt = IMAGE_SUMMARY.format(file_title=file_title, context=context)
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    ],
                }
            ]
            answer = vl_chat(messages, max_tokens=512)
            summary = self._strip_think(answer or "").replace("\n", "").replace("\r", "")
            return summary or "图片描述"
        except Exception as e:
            logger.error(f"【模型调用】图片摘要失败：{image_path}，原因：{e}")
            return "图片描述"

    # ------------------------------------------------------------------
    def _step4_upload_and_replace(self, file_title, images, summaries, md_content) -> str:
        cfg = get_config()
        upload_dir = f"images/{file_title}".replace(" ", "")

        self._clean_minio_directory(upload_dir)

        urls = {}
        for image_file, image_path, _ in images:
            object_name = f"{upload_dir}/{image_file}"
            url = self._upload_to_minio(image_path, object_name, guess_content_type(image_path))
            urls[image_file] = url

        # 替换 md 中的引用：![原描述](本地路径) -> ![摘要](URL)
        for image_file in summaries:
            summary = summaries[image_file]
            url = urls[image_file]
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r"\)")
            md_content = pattern.sub(lambda x: f"![{summary}]({url})", md_content)

        logger.info(f"【图片处理】{len(summaries)} 张图片上传并替换完成，前缀：{cfg.minio_bucket}/{upload_dir}")
        return md_content

    def _clean_minio_directory(self, upload_dir: str) -> None:
        """幂等清理（非核心业务，失败不影响主流程）；仅限自建桶内 images/ 前缀"""
        try:
            cfg = get_config()
            client = get_minio_client()
            objects = client.list_objects(cfg.minio_bucket, upload_dir, recursive=True)
            delete_list = [DeleteObject(obj.object_name) for obj in objects]
            if delete_list:
                for err in client.remove_objects(cfg.minio_bucket, delete_list):
                    logger.error(f"删除失败: {err}")
        except Exception as e:
            logger.error(f"MinIO清理失败: {e}")

    def _upload_to_minio(self, image_path: str, object_name: str, content_type: str) -> str:
        cfg = get_config()
        client = get_minio_client()
        client.fput_object(cfg.minio_bucket, object_name, image_path, content_type=content_type)
        return f"{cfg.get_minio_base_url()}/{cfg.minio_bucket}/{object_name}"

    def _step5_backup_new_md_file(self, origin_md_path: str, new_md_content: str) -> str:
        new_md_file_name = os.path.splitext(origin_md_path)[0] + "_new.md"
        with open(new_md_file_name, "w", encoding="utf-8") as f:
            f.write(new_md_content)
        logger.info(f"处理后MD文件已保存：{new_md_file_name}")
        return new_md_file_name
