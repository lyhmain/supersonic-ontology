"""决策洞察：GLM 生成决策建议（沿用 AskInsight 四策略提示词，v1 固定策略不接 RL）"""
import json
import logging
import os

import requests

import jev

log = logging.getLogger("decision.insight")

# GLM-5.3-Flash 为思考型：max_tokens 需覆盖推理+正文（800 时正文为空）
MAX_TOKENS = int(os.getenv("INSIGHT_MAX_TOKENS", "2000"))

POLICY_INSTRUCTIONS = {
    "facts_first": "你是严谨的数据分析师。先陈述数据事实（数字、变化幅度），再给出简短结论。禁止编造数据中不存在的信息。",
    "action_first": "你是电商运营顾问。直接给出 3 条以内可执行动作建议，每条附数据依据。禁止编造数据。",
    "risk_focus": "你是风控专家。重点识别数据中的风险信号（下滑、异常、集中度），给出预警等级与应对建议。禁止编造数据。",
    "executive": "你是向高管汇报的分析师。用 3 句话以内总结：发生了什么、为什么、建议做什么。禁止编造数据。",
}


def _fmt(rows, limit: int = 8) -> str:
    if not rows:
        return "无数据"
    return json.dumps(rows[:limit], ensure_ascii=False, default=str)


def build_prompt(question: str, payload: dict) -> str:
    return f"""[用户问题]
{question or "(无明确问题，针对当期指标给出解读)"}

[同环比]
{json.dumps(payload.get("ratio"), ensure_ascii=False)}

[异常检测]
{json.dumps(payload.get("anomaly"), ensure_ascii=False)}

[归因分解]
{_fmt(payload.get("attribution", {}).get("dimensions"), 12)}

[取数明细]
{_fmt(payload.get("detail"))}

请基于以上数据生成分析结论与决策建议。所有数字必须来自上面提供的数据，禁止编造。"""


def generate_insight(question: str, payload: dict, policy: str = "facts_first") -> dict:
    system = POLICY_INSTRUCTIONS.get(policy, POLICY_INSTRUCTIONS["facts_first"])
    system += ("\n输出要求：不要展示思考过程，不要复述原始数据，"
               "直接输出面向业务人员的最终结论（≤200字），"
               "所有数字必须来自提供的数据，禁止编造。")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": build_prompt(question, payload)},
    ]
    pid, _prov = jev.active_provider()
    raw = jev.chat(messages, temperature=0.3, max_tokens=MAX_TOKENS)
    text = _strip_think(raw or "")
    if not text:   # 思考型偶发吃满推理预算 → 放大预算重试一次
        raw = jev.chat(messages, temperature=0.3, max_tokens=int(MAX_TOKENS * 1.5))
        text = _strip_think(raw or "")
    log.info("洞察生成: policy=%s chars=%s jev=%s", policy, len(text), pid)
    return {"policy": policy, "insight": text, "jev_backend": pid}


def _strip_think(text: str) -> str:
    """剥离 <think>...</think> 推理段（Qwen/GLM 思考型模型可能输出）"""
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()
