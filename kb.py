"""知识库(RAG)外挂通道:把知识类问题转发给 kb_rag 服务(KB-RAG 知识库插件)。

kb_rag 独立部署(web.app, 默认 127.0.0.1:8000),自带:
  - 范围门控(产品确认,范围外礼貌拒答)
  - 混合检索 + HyDE + 重排 + 引用答案([n] 标注)
本模块只做转发与结果整形,失败时降级、不阻断主链路。
"""
import os
from urllib.parse import quote

import requests

KB_ENABLED = os.getenv("KB_ENABLED", "false").lower() == "true"
KB_API_BASE = os.getenv("KB_API_BASE", "http://127.0.0.1:8000").rstrip("/")
KB_CHAT_URL = os.getenv("KB_CHAT_URL", "")   # 聊天页深链前缀,如 http://host:9200/chat.html
KB_TIMEOUT = int(os.getenv("KB_TIMEOUT", "90"))


def ask(question: str) -> dict:
    """调用 kb_rag 非流式问答。

    返回 {ok, answer, refs, refuse, error}:
      ok=True   → answer 为带 [n] 引用的答案(refuse=True 表示 kb_rag 判定范围外拒答)
      ok=False  → 服务不可用/超时,answer 为用户可读的降级说明
    """
    if not KB_ENABLED:
        return {"ok": False, "answer": "知识库通道未启用", "refs": [], "refuse": False}
    try:
        r = requests.post(f"{KB_API_BASE}/api/chat",
                          json={"query": question, "is_stream": False},
                          timeout=KB_TIMEOUT)
        r.raise_for_status()
        d = r.json()
    except Exception:
        return {"ok": False, "answer": "知识库服务暂不可用,请稍后再试,或改用数据问数(如:昨日GMV)",
                "refs": [], "refuse": False}
    answer = (d.get("answer") or "").strip()
    refuse = d.get("confirm_decision") == "refuse"
    return {"ok": True, "answer": answer, "refs": d.get("refs") or [],
            "refuse": refuse}


def format_answer(res: dict) -> str:
    """整形为聊天可读文本(markdown):答案 + 引用来源列表(每条独立列表项)"""
    text = res.get("answer") or ""
    refs = res.get("refs") or []
    if refs:
        lines = [f"- [{r.get('index')}] **{r.get('doc_name') or ''}**"
                 + (f" · {r['title_path']}" if r.get("title_path") else "")
                 for r in refs]
        text += "\n\n**引用来源:**\n" + "\n".join(lines)
    return text


def followup_link(question: str) -> str:
    """流式追问深链:跳转 kb-rag 聊天页自动发送原问题(打字机+引用展开)"""
    if not KB_CHAT_URL:
        return ""
    return (f"\n\n📚 [流式追问(打字机+引用展开)]"
            f"({KB_CHAT_URL}?q={quote(question)})")


def data_hint(metric_words: list) -> str:
    """拒答时附数据问数词表提示(引导用户走数据通道)"""
    ws = list(dict.fromkeys(w for w in metric_words if w))[:6]
    if not ws:
        return ""
    return "\n\n💡 若您想查询业务数据,可以试试:" + " / ".join(ws)


def health() -> bool:
    if not KB_ENABLED:
        return False
    try:
        r = requests.get(f"{KB_API_BASE}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
