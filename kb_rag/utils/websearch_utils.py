"""
百炼联网搜索客户端（Q4 网络搜索）

方案：qwen-flash + enable_search（内置夸克搜索），prompt 约束输出结构化 JSON 来源。
说明：MCP WebSearch SSE 端点不接受 sk-ws- 专属端点 key（401），故改用 grounded chat。

请求形态：
  POST {DASHSCOPE_BASE_URL}/chat/completions
  {"model": "qwen-flash", "enable_search": true, "search_options": {"forced_search": true}, ...}
"""

import json
import re
from typing import Dict, List

import requests

from kb_rag.config import get_config
from kb_rag.tool.logger import setup_logger

logger = setup_logger("utils.websearch")

_EXTRACT_PROMPT = (
    "搜索并整理与问题相关的信息，然后仅输出一个 JSON 数组（不要输出任何其他文字），"
    '每项格式：{{"title": "来源标题", "url": "信息来源链接", "summary": "与问题相关的要点，50字内"}}，'
    "最多 {top_k} 项，必须是搜索结果中真实存在的网页链接。\n\n问题：{query}"
)


def web_search(query: str, top_k: int = 5, timeout: int = 60) -> List[Dict]:
    """
    联网搜索入口（节点用）

    :return: [{"title", "url", "snippet"}]（最多 top_k 条）；失败抛异常由调用方兜底
    """
    cfg = get_config()
    resp = requests.post(
        f"{cfg.dashscope_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {cfg.dashscope_api_key}"},
        json={
            "model": cfg.web_search_model,
            "messages": [{"role": "user", "content": _EXTRACT_PROMPT.format(top_k=top_k, query=query)}],
            "enable_search": True,
            "search_options": {"forced_search": True, "search_strategy": "standard"},
            "temperature": 0.1,
            "max_tokens": 1024,
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"联网搜索失败：{resp.status_code} {resp.text[:200]}")

    content = resp.json()["choices"][0]["message"]["content"] or ""
    return _parse_sources(content, top_k)


def _parse_sources(content: str, top_k: int) -> List[Dict]:
    """从模型输出解析 JSON 数组；兼容 ```json 围栏与杂质文本"""
    content = re.sub(r"```(?:json)?|```", "", content).strip()
    match = re.search(r"\[.*\]", content, flags=re.DOTALL)
    out: List[Dict] = []
    if match:
        try:
            for item in json.loads(match.group(0)):
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                url = str(item.get("url") or item.get("link") or "").strip()
                snippet = str(item.get("summary") or item.get("snippet") or item.get("content") or "").strip()
                if url or title:
                    out.append({"title": title, "url": url, "snippet": snippet})
                if len(out) >= top_k:
                    break
        except Exception as e:
            logger.warning(f"【联网】JSON 解析失败（{e}）")
    logger.info(f"【联网】解析得到 {len(out)} 条来源")
    return out
