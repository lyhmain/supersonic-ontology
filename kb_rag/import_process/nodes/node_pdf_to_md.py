"""
PDF 转 Markdown 节点：调用 GPUStack 本地 MinerU（异步任务 API）

流程：POST /tasks → 轮询 GET /tasks/{id} → GET /tasks/{id}/result 下载 ZIP → 解压
ZIP 布局兼容两种：{名称}/hybrid_auto/{名称}.md（本地 MinerU）/ full.md（云端 API）
"""

import shutil
import time
import zipfile
from pathlib import Path
from zipfile import ZipFile

import requests

from kb_rag.config import get_config
from kb_rag.import_process.base import NodeBase
from kb_rag.import_process.state import ImportGraphState
from kb_rag.tool.logger import setup_logger

logger = setup_logger("import.pdf_to_md")


class NodePDFToMD(NodeBase):
    """PDF 转 Markdown 节点：PDF 结构化解析（MinerU）"""

    name = "node_pdf_to_md"

    # 轮询配置
    POLL_INTERVAL = 5       # 轮询间隔（秒）
    POLL_TIMEOUT = 900      # 最大超时（秒）

    def process(self, state: ImportGraphState):
        # 步骤1：校验输入参数
        pdf_path_obj, output_dir_obj = self._step1_validate_paths(state)

        # 步骤2：提交 MinerU 异步任务并轮询完成
        task_id = self._step2_submit_task(pdf_path_obj)

        # 步骤3：下载结果 ZIP 并解压，定位 markdown
        md_path = self._step3_download_and_extract(task_id, output_dir_obj, pdf_path_obj.stem)

        return {"md_path": md_path}

    # ------------------------------------------------------------------
    def _step1_validate_paths(self, state: ImportGraphState):
        pdf_path = state.get("pdf_path")
        local_dir = state.get("local_dir")
        if not pdf_path:
            raise ValueError("请指定PDF文件路径")
        if not local_dir:
            raise ValueError("请指定输出目录")

        pdf_path_obj = Path(pdf_path)
        output_dir_obj = Path(local_dir)
        if not pdf_path_obj.exists():
            raise FileNotFoundError(f"指定的PDF文件不存在：{pdf_path}")
        if not output_dir_obj.exists():
            output_dir_obj.mkdir(parents=True, exist_ok=True)
        return pdf_path_obj, output_dir_obj

    def _mineru(self) -> tuple[str, dict]:
        cfg = get_config()
        base = cfg.mineru_gpustack_base
        if not base:
            raise ValueError("请在 .env 配置 MINERU_GPUSTACK_BASE")
        headers = {"Authorization": f"Bearer {cfg.mineru_api_token}"} if cfg.mineru_api_token else {}
        return base.rstrip("/"), headers

    def _step2_submit_task(self, pdf_path_obj: Path) -> str:
        """提交 MinerU 异步解析任务"""
        base, headers = self._mineru()
        with open(pdf_path_obj, "rb") as f:
            resp = requests.post(
                f"{base}/tasks",
                headers=headers,
                files={"files": (pdf_path_obj.name, f, "application/pdf")},
                data={
                    "return_images": "true",
                    "return_md": "true",
                    "response_format_zip": "true",
                },
                timeout=120,
            )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"MinerU 提交任务失败：{resp.status_code} {resp.text[:300]}")
        task_id = resp.json().get("task_id")
        if not task_id:
            raise RuntimeError(f"MinerU 未返回 task_id：{resp.text[:300]}")
        logger.info(f"MinerU 任务已提交：{task_id}")
        return task_id

    def _poll_task(self, task_id: str) -> None:
        """轮询任务直至 completed"""
        base, headers = self._mineru()
        start = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > self.POLL_TIMEOUT:
                raise TimeoutError(f"MinerU 任务超时（>{self.POLL_TIMEOUT}s）：{task_id}")
            try:
                resp = requests.get(f"{base}/tasks/{task_id}", headers=headers, timeout=15)
            except Exception as e:
                logger.warning(f"轮询网络异常：{e}，{self.POLL_INTERVAL}s 后重试")
                time.sleep(self.POLL_INTERVAL)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"MinerU 轮询失败：{resp.status_code} {resp.text[:200]}")
            data = resp.json()
            status = (data.get("status") or data.get("state") or "").lower()
            if status in ("completed", "done", "success"):
                logger.info(f"MinerU 任务完成，耗时 {elapsed:.1f}s：{task_id}")
                return
            if status in ("failed", "error"):
                raise RuntimeError(f"MinerU 任务失败：{data.get('error') or data}")
            logger.info(f"MinerU 解析中（{status or 'pending'}），已耗时 {elapsed:.1f}s")
            time.sleep(self.POLL_INTERVAL)

    def _fix_zip_name(self, info: zipfile.ZipInfo) -> str:
        """修复 ZIP 内中文文件名乱码（非 UTF-8 标志位时按 cp437→gbk 还原）"""
        if info.flag_bits & 0x800:
            return info.filename
        try:
            return info.filename.encode("cp437").decode("gbk")
        except Exception:
            return info.filename

    def _step3_download_and_extract(self, task_id: str, output_dir_obj: Path, file_title: str) -> str:
        """下载结果 ZIP、解压（修正中文文件名）、定位 markdown 并重命名"""
        self._poll_task(task_id)

        base, headers = self._mineru()
        resp = requests.get(f"{base}/tasks/{task_id}/result", headers=headers, timeout=120)
        if resp.status_code != 200:
            raise RuntimeError(f"MinerU 结果下载失败：{resp.status_code}")

        # 保存 ZIP
        zip_save_path = output_dir_obj / f"{file_title}.zip"
        zip_save_path.write_bytes(resp.content)
        logger.info(f"MinerU 结果已保存：{zip_save_path} ({len(resp.content)} bytes)")

        # 解压（删除旧目录保证幂等）
        unzip_dir = output_dir_obj / file_title
        if unzip_dir.exists():
            shutil.rmtree(unzip_dir)
        unzip_dir.mkdir(parents=True, exist_ok=True)

        md_name_in_zip = None
        with ZipFile(zip_save_path, "r") as zf:
            for info in zf.infolist():
                fixed = self._fix_zip_name(info)
                target = unzip_dir / fixed
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                lower = fixed.lower()
                # 记录主 markdown（排除 images 目录内的文件）
                if lower.endswith(".md") and "/images/" not in fixed.replace("\\", "/").lower():
                    if md_name_in_zip is None or fixed.count("/") < md_name_in_zip.count("/"):
                        md_name_in_zip = fixed

        if not md_name_in_zip:
            raise FileNotFoundError(f"MinerU 结果 ZIP 中未找到 markdown：{zip_save_path}")

        src_md = unzip_dir / md_name_in_zip
        md_path = output_dir_obj / f"{file_title}.md"
        if src_md.resolve() != md_path.resolve():
            shutil.move(str(src_md), md_path)
        logger.info(f"Markdown 定位完成：{md_path}")

        # images 目录重定位：若与 md 同级的 images 不存在，则把解压目录中的 images 移过来
        images_dst = output_dir_obj / "images"
        if not images_dst.exists():
            for cand in unzip_dir.rglob("images"):
                if cand.is_dir():
                    shutil.move(str(cand), images_dst)
                    logger.info(f"images 目录重定位：{cand} -> {images_dst}")
                    break

        return str(md_path.resolve())
