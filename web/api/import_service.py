"""
导入服务 API

- GET  /import.html                    导入前端页面
- POST /api/import                     上传文件，后台执行导入流水线，返回 task_id
- GET  /api/import/status/{task_id}    轮询任务进度（节点级 done_list）
- GET  /api/import/docs                已导入文档列表（doc_name + 切片数）
- DELETE /api/import/docs/{doc_name}   删除文档（含全部切片）
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from kb_rag.config import get_config
from kb_rag.import_process.main_graph import KBImportWorkflow
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.minio_utils import guess_content_type
from web.task_utils import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PROCESSING,
    add_done_task,
    create_task,
    get_task,
    update_task_status,
)

logger = setup_logger("web.import")

router = APIRouter(prefix="/api/import", tags=["import"])

# 允许上传的扩展名
ALLOWED_EXTS = {".pdf", ".md"}


def _run_import_task(task_id: str, import_file_path: str, local_dir: str):
    """后台执行导入流水线（LangGraph stream 模式，逐节点上报进度）"""
    try:
        update_task_status(task_id, TASK_STATUS_PROCESSING)
        workflow = KBImportWorkflow()
        for event in workflow.run(
            {
                "task_id": task_id,
                "local_file_path": import_file_path,
                "local_dir": local_dir,
            },
            stream=True,
        ):
            for node_name in event:
                add_done_task(task_id, node_name)
        update_task_status(task_id, TASK_STATUS_COMPLETED)
    except Exception as e:
        logger.error(f"[{task_id}] 导入流程失败：{e}", exc_info=True)
        update_task_status(task_id, TASK_STATUS_FAILED, error=str(e))


@router.post("")
async def import_file(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """上传 PDF/MD 文件并启动导入任务"""
    cfg = get_config()

    filename = Path(file.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型：{ext}（允许 {sorted(ALLOWED_EXTS)}）")

    # 保存上传文件至 数据根目录/uploads/
    upload_dir = Path(cfg.data_root_dir) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    save_path = upload_dir / filename
    with open(save_path, "wb") as f:
        f.write(await file.read())
    logger.info(f"文件已保存：{save_path}")

    task_id = f"import_{uuid.uuid4().hex[:12]}"
    create_task(task_id)
    background_tasks.add_task(_run_import_task, task_id, str(save_path.resolve()), cfg.data_root_dir)

    return {"message": "导入任务已提交", "task_id": task_id, "filename": filename}


@router.get("/status/{task_id}")
async def import_status(task_id: str):
    """查询导入任务进度"""
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return task


@router.get("/docs")
async def list_docs():
    """已导入文档列表（聚合切片数）"""
    from collections import Counter

    from kb_rag.utils.milvus_utils import get_milvus_client

    client = get_milvus_client()
    rows = client.query(
        "kb_rag_chunks",
        filter='doc_name != ""',
        output_fields=["doc_name"],
        limit=16384,
    )
    counts = Counter(r["doc_name"] for r in rows)
    return {
        "items": [
            {"doc_name": name, "chunks": count}
            for name, count in sorted(counts.items(), key=lambda x: -x[1])
        ],
        "total_docs": len(counts),
        "total_chunks": sum(counts.values()),
    }


@router.delete("/docs/{doc_name}")
async def delete_doc(doc_name: str):
    """删除文档（白名单防护：禁止 milvus- 前缀等系统集合操作）"""
    from kb_rag.utils.milvus_utils import delete_by_doc_name

    if not doc_name.strip():
        raise HTTPException(status_code=400, detail="doc_name 不能为空")
    delete_by_doc_name(doc_name.strip())
    return {"message": f"已删除：{doc_name}"}
