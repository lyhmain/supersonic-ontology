"""
KB-RAG 知识库插件 Web 入口

启动：
    uv run uvicorn web.app:app --host 0.0.0.0 --port 8000
或：
    uv run python -m web.app

页面：
    http://localhost:8000/import.html   导入页
    http://localhost:8000/chat.html     聊天页
文档：
    http://localhost:8000/docs          Swagger
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from kb_rag.tool.logger import setup_logger
from kb_rag.utils.history_utils import ensure_table
from web.api import import_service, query_service

logger = setup_logger("web.app")

app = FastAPI(
    title="KB-RAG 知识库插件 API",
    description="知识库导入与检索问答服务(JEV+RAG)",
)


@app.on_event("startup")
def _startup():
    """启动时确保 PG 历史表与反馈表存在（幂等）"""
    try:
        ensure_table()
    except Exception as e:
        logger.warning(f"PG 建表失败（服务仍可启动）: {e}")

# 跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(import_service.router)
app.include_router(query_service.router)


# ---------- 页面路由 ----------
def _page(filename: str) -> FileResponse:
    page_path = Path(__file__).absolute().parent / "page" / filename
    if not page_path.exists():
        raise HTTPException(status_code=404, detail=f"页面不存在：{page_path}")
    return FileResponse(page_path)


@app.get("/import.html", include_in_schema=False)
async def import_page():
    return _page("import.html")


@app.get("/chat.html", include_in_schema=False)
async def chat_page():
    return _page("chat.html")


@app.get("/", include_in_schema=False)
async def index():
    return {"app": "KB-RAG 知识库", "pages": ["/import.html", "/chat.html"], "docs": "/docs"}


@app.get("/health", tags=["system"])
async def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app=app, host="0.0.0.0", port=8000)
