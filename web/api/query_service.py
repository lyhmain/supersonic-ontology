"""
查询问答服务 API（SSE 流式）

- GET  /chat.html                     聊天前端页面
- POST /api/chat                      提问（is_stream=true 返回 SSE 流；false 返回 JSON）
- GET  /api/chat/history/{session_id} 会话历史
- DELETE /api/chat/history/{session_id} 清空会话历史

SSE 事件：ready / progress / delta / final / error
"""

import queue
import threading
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from kb_rag.query_process.main_graph import KBQueryWorkflow
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.history_utils import add_feedback, clear_history, get_history, list_sessions
from web.sse_utils import (
    SSE_EVENT_DELTA,
    SSE_EVENT_THINKING,
    SSE_EVENT_ERROR,
    SSE_EVENT_FINAL,
    SSE_EVENT_PROGRESS,
    SSE_EVENT_READY,
    SENTINEL,
    format_sse,
    sse_drain,
)

logger = setup_logger("web.query")

router = APIRouter(prefix="/api/chat", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class FeedbackRequest(BaseModel):
    """答案反馈"""

    session_id: str = Field(..., description="会话ID")
    question: str = Field(..., description="问题")
    answer: str = Field(..., description="回答")
    rating: str = Field(..., description="up / down")


class ChatRequest(BaseModel):
    """查询请求数据结构"""

    query: str = Field(..., description="用户问题")
    session_id: str = Field(default="", description="会话ID，不传自动生成")
    is_stream: bool = Field(True, description="是否流式返回（SSE）")
    doc_name: str = Field(default="", description="可选，限定在该文档内检索（文件目录点选）")


def _run_graph_in_thread(question: str, session_id: str, q: queue.Queue, doc_name: str = ""):
    """后台线程执行检索图：节点完成→progress，答案增量→delta，结束→final"""

    def delta_callback(text: str):
        q.put((SSE_EVENT_DELTA, {"text": text}))

    def thinking_callback(text: str):
        q.put((SSE_EVENT_THINKING, {"text": text}))

    try:
        workflow = KBQueryWorkflow()
        app = workflow.compile()
        init_state = {
            "task_id": f"web_{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
            "question": question,
            "stream_callback": delta_callback,
            "thinking_callback": thinking_callback,
            "filter_doc_name": doc_name or None,
        }

        done_list: list = []
        last_state: dict = {}
        for event in app.stream(init_state):
            for node_name, node_delta in event.items():
                done_list.append(node_name)
                q.put((SSE_EVENT_PROGRESS, {"done_list": list(done_list), "running": node_name}))
                if isinstance(node_delta, dict) and ("answer" in node_delta or "refs" in node_delta):
                    last_state = node_delta

        q.put((
            SSE_EVENT_FINAL,
            {
                "answer": last_state.get("answer", ""),
                "refs": last_state.get("refs", []),
                "session_id": session_id,
            },
        ))
    except Exception as e:
        logger.error(f"检索流程异常：{e}", exc_info=True)
        q.put((SSE_EVENT_ERROR, {"error": str(e)}))
    finally:
        q.put(SENTINEL)


@router.post("")
async def chat(request: ChatRequest):
    """提问接口"""
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    session_id = request.session_id or f"s_{uuid.uuid4().hex[:12]}"

    # ---------- 非流式：同步执行，直接返回 JSON ----------
    if not request.is_stream:
        final_state = KBQueryWorkflow.create_and_run({
            "task_id": f"web_{uuid.uuid4().hex[:8]}",
            "session_id": session_id,
            "question": request.query,
            "filter_doc_name": request.doc_name or None,
        })
        return {
            "message": "处理完成！",
            "session_id": session_id,
            "answer": final_state.get("answer", ""),
            "refs": final_state.get("refs", []),
            "confirm_decision": final_state.get("confirm_decision", ""),
        }

    # ---------- 流式：SSE ----------
    q: queue.Queue = queue.Queue()
    worker = threading.Thread(target=_run_graph_in_thread, args=(request.query, session_id, q, request.doc_name), daemon=True)
    worker.start()

    def generator():
        yield format_sse(SSE_EVENT_READY, {"session_id": session_id})
        yield from sse_drain(q)

    return StreamingResponse(generator(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/history/{session_id}")
async def chat_history(session_id: str, limit: int = 50):
    """查询会话历史"""
    items = get_history(session_id, limit=limit)
    return {"session_id": session_id, "items": items}


@router.delete("/history/{session_id}")
async def chat_history_delete(session_id: str):
    """清空会话历史"""
    clear_history(session_id)
    return {"message": "History cleared", "session_id": session_id}


@router.post("/feedback")
async def chat_feedback(request: FeedbackRequest):
    """答案反馈（点赞/点踩 → 评测集原料）"""
    if request.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating 仅支持 up/down")
    add_feedback(request.session_id, request.question, request.answer, request.rating)
    return {"message": "反馈已记录", "rating": request.rating}


@router.get("/sessions")
async def chat_sessions(limit: int = 50):
    """会话列表（按最近活跃排序）"""
    return {"items": list_sessions(limit=limit)}
