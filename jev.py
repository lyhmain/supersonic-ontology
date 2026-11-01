"""Jev 洞察后端注册表:本地部署 / 原版 / 百炼 三路可切换(OpenAI 兼容协议)。

配置持久化 jev_config.json(容器内 /app/data,可挂载卷)。
页面(/ui)可编辑、测试连通、热切换 active 后端;insight 生成走 active 后端。
"""
import json
import logging
import os
import threading

import requests

from config import GLM_BASE, GLM_KEY, GLM_MODEL

log = logging.getLogger("decision.jev")

CONFIG_PATH = os.getenv("JEV_CONFIG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "jev_config.json"))
_TIMEOUT = int(os.getenv("JEV_TIMEOUT", "120"))

_LOCK = threading.Lock()

# 默认种子:本地 = 现 GLM 网关;百炼 = DashScope 兼容模式(URL 预填,密钥待填);原版 = 待填
DEFAULT_CONFIG = {
    "active": "local",
    "providers": {
        "local":    {"label": "本地部署 Jev", "base_url": GLM_BASE,
                     "api_key": GLM_KEY, "model": GLM_MODEL},
        "original": {"label": "原 Jev", "base_url": "", "api_key": "", "model": ""},
        "bailian":  {"label": "百炼 Jev", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                     "api_key": "", "model": "qwen-plus"},
    },
}


def load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        log.warning("jev_config 解析失败,用默认: %s", e)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    base = json.loads(json.dumps(DEFAULT_CONFIG))
    base.update({k: v for k, v in cfg.items() if k in ("active", "providers")})
    for pid, seed in DEFAULT_CONFIG["providers"].items():
        base["providers"].setdefault(pid, seed)
    return base


def save(cfg: dict):
    with _LOCK:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def active_provider() -> tuple:
    """返回 (provider_id, provider_cfg)"""
    cfg = load()
    pid = cfg.get("active") or "local"
    prov = (cfg.get("providers") or {}).get(pid) or {}
    return pid, prov


def chat(messages: list, temperature: float = 0.3, max_tokens: int = 2000) -> str:
    """经 active Jev 后端生成(OpenAI 兼容 /chat/completions)"""
    pid, prov = active_provider()
    base_url = (prov.get("base_url") or "").rstrip("/")
    model = prov.get("model") or ""
    if not base_url or not model:
        raise RuntimeError(f"Jev 后端 [{pid}] 未配置 base_url/model,请在 /ui 配置")
    body = {"model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    headers = {"Content-Type": "application/json"}
    if prov.get("api_key"):
        headers["Authorization"] = f"Bearer {prov['api_key']}"
    r = requests.post(f"{base_url}/chat/completions", json=body,
                      headers=headers, timeout=_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
    if content is None:
        raise RuntimeError(f"Jev 后端 [{pid}] 响应无 content: {str(data)[:200]}")
    return content


def test_connect(pid: str) -> dict:
    """连通测试:发一句最小补全"""
    cfg = load()
    prov = (cfg.get("providers") or {}).get(pid)
    if not prov:
        return {"ok": False, "error": f"未知后端 {pid}"}
    base_url = (prov.get("base_url") or "").rstrip("/")
    model = prov.get("model") or ""
    if not base_url or not model:
        return {"ok": False, "error": "base_url/model 未配置"}
    try:
        body = {"model": model, "messages": [{"role": "user", "content": "回复:OK"}],
                "temperature": 0, "max_tokens": 16}
        headers = {"Content-Type": "application/json"}
        if prov.get("api_key"):
            headers["Authorization"] = f"Bearer {prov['api_key']}"
        r = requests.post(f"{base_url}/chat/completions", json=body,
                          headers=headers, timeout=30)
        r.raise_for_status()
        msg = ((r.json().get('choices') or [{}])[0].get('message') or {})
        text = (msg.get('content') or msg.get('reasoning_content') or '').strip()
        if not text:
            text = '(HTTP 200 但正文为空——思考型模型请调大模型侧 max_tokens)'
        return {'ok': True, 'reply': text[:60], 'model': model}
    except Exception as e:
        return {"ok": False, "error": repr(e)[:200]}


def masked(cfg: dict) -> dict:
    """api_key 打码(只露末 4 位)"""
    out = json.loads(json.dumps(cfg))
    for prov in (out.get("providers") or {}).values():
        k = prov.get("api_key") or ""
        prov["api_key_masked"] = (k[:6] + "***" + k[-4:]) if len(k) > 12 else ("***" if k else "")
        prov.pop("api_key", None)
    return out
