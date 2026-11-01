"""
重排序工具：自适应三通道

- DashScope 云端重排（qwen3.7-text-rerank，公网 services API）：毫秒级，配置 DASHSCOPE_API_KEY 即自动启用
- HTTP reranker（GPUStack /v1/reranker，jina 兼容格式）：毫秒级，部署了 reranker 模型即自动启用
- LLM 打分兜底（GLM，关思考模式）：以上均不可用时的默认通道
"""

import re
from typing import Dict, List, Optional

import requests

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.reranker")

_detected: dict = {"checked": False, "model": None}


def detect_http_reranker() -> Optional[str]:
    """
    自动检测 GPUStack 上是否部署了 reranker 模型（结果缓存）

    :return: reranker 模型名，未部署返回 None
    """
    if _detected["checked"]:
        return _detected["model"]
    _detected["checked"] = True

    cfg = get_config()
    try:
        resp = requests.get(
            f"{cfg.reranker_api_base.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {cfg.reranker_api_key}"},
            timeout=10,
        )
        models = [m.get("id", "") for m in resp.json().get("data", [])]
        found = next((m for m in models if "rerank" in m.lower()), None)
        _detected["model"] = found
        if found:
            logger.info(f"检测到 HTTP reranker: {found}")
        else:
            logger.info(f"未检测到 reranker 模型（{models}），使用 LLM 打分通道")
    except Exception as e:
        logger.warning(f"reranker 检测失败（{e}），使用 LLM 打分通道")
    return _detected["model"]


def detect_dashscope() -> bool:
    """是否配置了百炼 key（决定 dashscope 通道可用性）"""
    return bool(get_config().dashscope_api_key)


def dashscope_rerank(query: str, documents: List[str], top_k: int = 10) -> List[Dict]:
    """
    百炼云端精排（native services API + qwen3.7-text-rerank）

    :return: [{"index": 原文档下标, "score": 相关性分数(0~1)}] 按分数降序
    """
    cfg = get_config()
    if not cfg.dashscope_api_key:
        raise RuntimeError("未配置 DASHSCOPE_API_KEY")

    resp = requests.post(
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        headers={"Authorization": f"Bearer {cfg.dashscope_api_key}"},
        json={
            "model": cfg.dashscope_rerank_model,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": min(top_k, len(documents))},
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"DashScope rerank 失败：{resp.status_code} {resp.text[:200]}")

    results = resp.json().get("output", {}).get("results", [])
    out = [{"index": r["index"], "score": r["relevance_score"]} for r in results]
    logger.info(f"DashScope rerank 完成：{len(out)} 条（模型 {cfg.dashscope_rerank_model}）")
    return out


def http_rerank(query: str, documents: List[str], top_k: int = 10) -> List[Dict]:
    """
    HTTP reranker 精排（jina/Cohere 兼容 /v1/reranker 接口）

    :return: [{"index": 原文档下标, "score": 相关性分数}] 按分数降序
    """
    cfg = get_config()
    model = detect_http_reranker()
    if not model:
        raise RuntimeError("无可用 HTTP reranker")

    resp = requests.post(
        f"{cfg.reranker_api_base.rstrip('/')}/reranker",
        headers={"Authorization": f"Bearer {cfg.reranker_api_key}"},
        json={"model": model, "query": query, "documents": documents, "top_n": top_k},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP reranker 失败：{resp.status_code} {resp.text[:200]}")

    results = resp.json().get("results", [])
    out = [{"index": r["index"], "score": r["relevance_score"]} for r in results]
    logger.info(f"HTTP rerank 完成：{len(out)} 条（模型 {model}）")
    return out


def llm_score_one(question: str, chunk_text: str) -> int:
    """
    LLM pointwise 打分（0-3 固定判据，关思考模式提速）

    评分标准（Jev Score 风格，规则固定）：
    0 无关 / 1 同领域沾边 / 2 同主题另方面 / 3 直接回答了问题
    """
    from kb_rag.query_process.prompt import RERANK_SCORE
    from kb_rag.utils.llm_utils import chat

    try:
        prompt = RERANK_SCORE.format(question=question, chunk=chunk_text)
        answer = chat(
            [{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
            disable_thinking=True,
        ) or ""
        answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
        match = re.search(r"\{.*\}", answer, flags=re.DOTALL)
        if not match:
            num = re.search(r"[0-3]", answer)
            return int(num.group(0)) if num else 0
        return max(0, min(3, int(json_loads_safe(match.group(0)).get("score", 0))))
    except Exception as e:
        logger.error(f"LLM 打分失败（计 0 分）：{e}")
        return 0


def json_loads_safe(text: str) -> dict:
    import json
    try:
        return json.loads(text)
    except Exception:
        return {}
