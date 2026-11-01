"""
Jev 决策层 Sidecar 服务（standalone，供 Dify/RAGFlow 等平台外挂调用）

端点：
  POST /v1/scope            门前三振（choice 三分类：in_scope/out_of_scope/unclear）
  POST /v1/answerability    可答门控（noul 逐条抽取式判定取 max）
  POST /v1/citation_check   引用核查
  GET  /healthz             探活

基座：阿里百炼 qwen-flash（DECISION_LLM_MODEL 可换），temperature=0，JSON 严格输出。
运行：uvicorn decision_service:app --host 0.0.0.0 --port 9101
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Jev Decision Sidecar", version="1.0")

DASHSCOPE_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DECISION_MODEL = os.getenv("DECISION_LLM_MODEL", "qwen-flash")


# ==================== 判定基座 ====================

def _chat(prompt: str, max_tokens: int = 256) -> str:
    if not DASHSCOPE_KEY:
        raise HTTPException(status_code=503, detail="DASHSCOPE_API_KEY 未配置")
    resp = requests.post(
        f"{DASHSCOPE_BASE.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {DASHSCOPE_KEY}"},
        json={"model": DECISION_MODEL, "messages": [{"role": "user", "content": prompt}],
              "temperature": 0.0, "max_tokens": max_tokens},
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"决策基座失败：{resp.status_code}")
    return resp.json()["choices"][0]["message"]["content"] or ""


def _extract_json(text: str) -> Optional[dict]:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return 0.5


# ==================== 模型 ====================

class ScopeRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    scope_description: str = Field(..., description="受理范围全量描述（写全话题域，宁全勿缺）")


class AnswerabilityRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    contexts: List[str] = Field(..., description="候选片段文本列表（≤6 条）")
    threshold: float = Field(0.65, description="放行阈值")


class CitationRequest(BaseModel):
    question: str
    answer: str
    sources: List[dict] = Field(..., description='[{"index":1,"chunk_text":"..."}, ...]')


# ==================== 端点 ====================

@app.get("/healthz")
def healthz():
    return {"status": "ok", "model": DECISION_MODEL}


@app.post("/v1/scope")
def v1_scope(req: ScopeRequest):
    """门前三振：choice 三分类 + 概率分布"""
    criteria = {
        "in_scope": "属于上述受理范围内的咨询，即使措辞口语化或不含领域字眼",
        "out_of_scope": "与受理范围完全无关的日常闲聊或外部话题",
        "unclear": "无法判断意图的碎片输入或需要上下文的后续追问",
    }
    options = "\n".join(f'- "{k}": {v}' for k, v in criteria.items())
    prompt = (
        "你是判定器，只输出 JSON，不要解释。\n"
        '输出格式：{"choice": "选项标签", "probabilities": {"标签": 概率}}\n\n'
        f"【判定问题】这个问题是否属于本系统的受理范围\n"
        f"【受理范围说明】{req.scope_description}\n"
        f"【可选选项】\n{options}\n\n"
        f"【待判内容】\n{req.question}"
    )
    data = _extract_json(_chat(prompt)) or {}
    label = data.get("choice", "unclear")
    if label not in criteria:
        label = "unclear"
    return {"choice": label, "pass": label == "in_scope", "probabilities": data.get("probabilities", {})}


def _judge_one(question: str, text: str) -> float:
    prompt = (
        "你是判定器，只输出 JSON，不要解释。\n"
        '输出格式：{"p": 0到1的小数}\n\n'
        "【判定问题】这一条文本是否直接陈述了回答问题所要求的具体信息（数值、条件、方法、步骤、结论）\n"
        '【判定为"是"的条件】文本明确写出了问题所问的具体数值/标准/方法本身，仅凭本条即可作答\n'
        '【判定为"否"的条件】文本只是谈到相同话题，但没写出问题所问的具体事项；'
        "或用『另行规定』『另行核算』『按司龄核算』等把关键数值指向了别处；或写的是其他对象/其他事项\n\n"
        f"【问题】\n{question}\n\n【文本】\n{text[:300]}"
    )
    data = _extract_json(_chat(prompt)) or {}
    return _clamp01(data.get("p", 0.5))


@app.post("/v1/answerability")
def v1_answerability(req: AnswerabilityRequest):
    """可答门控：逐条抽取式判定取 max（近失陷阱敏感）"""
    if not req.contexts:
        return {"probability": 0.0, "answerable": False, "detail": []}
    with ThreadPoolExecutor(max_workers=min(5, len(req.contexts))) as pool:
        probs = list(pool.map(lambda c: _judge_one(req.question, c), req.contexts))
    p = max(probs)
    return {
        "probability": round(p, 4),
        "answerable": p >= req.threshold,
        "detail": [{"index": i + 1, "p": round(x, 3)} for i, x in enumerate(probs)],
    }


@app.post("/v1/citation_check")
def v1_citation_check(req: CitationRequest):
    """引用核查：答案中 [n] 主张是否被来源支撑"""
    if not req.sources or not req.answer:
        return {"supported": True, "unsupported_refs": []}
    digest = "\n".join(f"[{s.get('index')}] {str(s.get('chunk_text', ''))[:400]}" for s in req.sources)
    prompt = (
        "你是引用核查器，只输出 JSON，不要解释。\n"
        '输出格式：{"supported": true或false, "unsupported_refs": [不被支撑的来源编号数组]}\n\n'
        "核查规则：判断答案中的引用标注 [n] 所对应的事实主张，是否被来源 [n] 的文本直接支撑。\n"
        "来源只讨论相同话题但未陈述答案所引用的具体信息 => 判定为不支撑。\n\n"
        f"【问题】\n{req.question}\n\n【答案（含 [n] 引用标注）】\n{req.answer[:800]}\n\n【来源列表】\n{digest}"
    )
    data = _extract_json(_chat(prompt)) or {}
    unsupported = data.get("unsupported_refs", [])
    if not isinstance(unsupported, list):
        unsupported = []
    return {"supported": bool(data.get("supported", not unsupported)), "unsupported_refs": unsupported}
