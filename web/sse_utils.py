"""
SSE 工具：事件打包与队列消费

事件协议（与原版对齐）：
- event: ready     data: {"session_id": "..."}
- event: progress  data: {"done_list": [...], "running": "节点名"}
- event: thinking  data: {"text": "思考段增量"}
- event: delta     data: {"text": "增量文本"}
- event: final     data: {"answer": "...", "refs": [...]}
- event: error     data: {"error": "..."}
"""

import json
import queue
from typing import Generator

SSE_EVENT_READY = "ready"
SSE_EVENT_PROGRESS = "progress"
SSE_EVENT_DELTA = "delta"
SSE_EVENT_THINKING = "thinking"
SSE_EVENT_FINAL = "final"
SSE_EVENT_ERROR = "error"

SENTINEL = object()


def format_sse(event: str, data: dict) -> str:
    """打包为 SSE 帧"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sse_drain(q: queue.Queue) -> Generator[str, None, None]:
    """从队列持续取事件并打包为 SSE 帧，直到收到哨兵"""
    while True:
        item = q.get()
        if item is SENTINEL:
            break
        event_type, data = item
        yield format_sse(event_type, data)
