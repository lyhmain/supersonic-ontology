"""Badcase 收集:自动捕获(查空/指标未命中) + 人工上报 + 标注 + 转试问集验收。
旁挂组件:仅读写 /app/data/badcases.jsonl,不影响主链路。"""
import inspect, json, os, threading, functools, uuid, datetime

_PATH = os.getenv("BADCASE_PATH", "/app/data/badcases.jsonl")
_lock = threading.Lock()
_ctx = threading.local()   # 嵌套调用去重 + 问题传递(plugin→nl 内部调用)


def _load():
    if not os.path.exists(_PATH):
        return []
    out = []
    with open(_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out


def _save(items):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    os.replace(tmp, _PATH)


def add(question, channel="manual", reason="manual", detail=None, note=""):
    question = (question or "").strip()
    if not question:
        return None
    with _lock:
        items = _load()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for it in reversed(items[-30:]):    # 同分钟内同问同因去重
            if (it.get("question") == question and it.get("reason") == reason
                    and it.get("channel") == channel
                    and it.get("ts", "") >= now[:11] + now[11:16]):
                return it
        it = {"id": uuid.uuid4().hex[:10], "ts": now, "question": question,
              "channel": channel, "reason": reason, "status": "open",
              "note": note, "detail": detail or {}}
        items.append(it)
        _save(items)
    return it


def judge_api(resp):
    if not isinstance(resp, dict):
        return None
    if resp.get("action") == "kb":      # 知识库通道:不适用数据坏例判据
        return None
    p = resp.get("parsed") or {}
    if p.get("metric_matched") is False:
        return "metric_miss"
    a = resp.get("action")
    if a == "aggregate":
        dim = resp.get("dimension") or resp.get("dim")
        items = resp.get("items") or resp.get("rows")
        if dim and not items:
            return "empty"
        if not dim and resp.get("value") is None \
                and resp.get("total") is None and resp.get("multi") is None:
            return "empty"
        if not dim and (resp.get("value") == 0 or resp.get("total") == 0) \
                and resp.get("multi") is None:
            return "empty"
    elif a == "series" and not resp.get("series"):
        return "empty"
    elif a == "ratio" and resp.get("current") is None:
        return "empty"
    return None


def judge_plugin(resp):
    if not isinstance(resp, dict):
        return "error"
    if resp.get("kb"):                  # 知识库通道:不适用数据坏例判据
        return None
    rl = resp.get("resultList") or []
    if not rl:
        return "empty"
    if all(isinstance(x, dict) and not x.get("分组数") and x.get("合计") is None
           for x in rl):
        return "empty"
    txt = str(resp.get("data") or "")
    if ("失败" in txt) or ("未启用" in txt) or ("无法" in txt):
        return "error"
    return None


def _probe_question(a, k):
    for d in list(a) + list(k.values()):
        if isinstance(d, dict):
            q = d.get("question") or d.get("queryText")
            if q:
                return str(q)
    return None


def record(channel, resp, fallback_q=None):
    q = (resp or {}).get("question") if isinstance(resp, dict) else None
    q = q or fallback_q or getattr(_ctx, "q", "")
    reason = judge_api(resp) if channel == "api" else judge_plugin(resp)
    if not reason:
        return None
    parsed = (resp.get("parsed") or {}) if isinstance(resp, dict) else {}
    return add(q, channel, reason, detail={
        "action": (resp or {}).get("action") if isinstance(resp, dict) else None,
        "range": (resp or {}).get("range") if isinstance(resp, dict) else None,
        "dim": parsed.get("dim"), "metric": parsed.get("metric")})


def set_question(q):
    """async 端点(Request 入参,装饰器探针取不到)解析 body 后显式传递问题"""
    if q:
        _ctx.q = str(q)


def watched(channel):
    """端点装饰器:记录坏例;嵌套调用(plugin→nl)只记最外层;兼容 sync/async 端点"""
    def deco(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def w(*a, **k):
                q0 = _probe_question(a, k)
                if q0:
                    _ctx.q = q0
                if getattr(_ctx, "on", False):
                    return await fn(*a, **k)
                _ctx.on = True
                try:
                    resp = await fn(*a, **k)
                    try:
                        record(channel, resp, q0)
                    except Exception:
                        pass
                    return resp
                finally:
                    _ctx.on = False
            return w

        @functools.wraps(fn)
        def w(*a, **k):
            q0 = _probe_question(a, k)
            if q0:
                _ctx.q = q0
            if getattr(_ctx, "on", False):
                return fn(*a, **k)
            _ctx.on = True
            try:
                resp = fn(*a, **k)
                try:
                    record(channel, resp, q0)
                except Exception:
                    pass
                return resp
            finally:
                _ctx.on = False
        return w
    return deco


def list_(status=None, limit=200):
    items = _load()
    if status:
        items = [x for x in items if x.get("status") == status]
    return list(reversed(items[-limit:]))


def get(bid):
    for it in _load():
        if it.get("id") == bid:
            return it
    return None


def annotate(bid, reason=None, note=None, status=None):
    with _lock:
        items = _load()
        for it in items:
            if it.get("id") == bid:
                if reason is not None:
                    it["reason"] = reason
                if note is not None:
                    it["note"] = note
                if status is not None:
                    it["status"] = status
                _save(items)
                return it
    return None
