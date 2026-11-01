"""
任务状态工具：导入任务的内存注册表（线程安全）

状态流转：pending → processing → completed / failed
"""

import threading
from collections import defaultdict
from typing import Dict, List

TASK_STATUS_PENDING = "pending"
TASK_STATUS_PROCESSING = "processing"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"

_lock = threading.Lock()
_tasks: Dict[str, dict] = defaultdict(dict)


def create_task(task_id: str) -> None:
    with _lock:
        _tasks[task_id] = {
            "status": TASK_STATUS_PENDING,
            "done_list": [],
            "error": "",
        }


def update_task_status(task_id: str, status: str, error: str = "") -> None:
    with _lock:
        _tasks[task_id]["status"] = status
        if error:
            _tasks[task_id]["error"] = error


def add_done_task(task_id: str, node_name: str) -> None:
    with _lock:
        done: List[str] = _tasks[task_id].setdefault("done_list", [])
        if node_name not in done:
            done.append(node_name)


def get_task(task_id: str) -> dict:
    with _lock:
        return dict(_tasks.get(task_id, {}))
