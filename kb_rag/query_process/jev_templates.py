"""
Jev 模板库：把 md/ 文章合集（TypeSafe classifying_rag_passages cookbook 等）的
判定模板正式嵌入查询管线。

模板与 Jev 原语一一对应（实现走 kb_rag.utils.decision 的 GLM/百炼仿真基座，
将来接真 Jev（typesafe-sdk systemone 线协议）只换执行器，模板不动）：

- PASSAGE_FILTER_THRESHOLDS : 阈值即策略（改策略=改数字，不改问题措辞）
- filter_passages()         : 段落四问（注入→矛盾→相关→证据）+ 固定顺序路由
- ANSWER_DUAL_BLOCK         : 证据块 / 冲突块 分离的生成模板（cookbook §Build the prompt）
- CITATION_QUESTIONS        : 引用核查封闭分类（supported/unsupported/contradicted/fabricated）

设计约束（jev-1.13 jaggedness 已知局限）：
- 指令直写、不数数、不比日期、无双重否定
- 判定与路由分离：问题只产概率，动作在 route() 的代码里
- fail-open：判定基座不可用时全部按原样放行（宁多勿漏）
"""

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from kb_rag.tool.logger import setup_logger
from kb_rag.utils.decision import judge_chunk

logger = setup_logger("query.jev_templates")

# ==================== 阈值即策略（env 可覆盖） ====================

PASSAGE_FILTER_THRESHOLDS = {
    "injection_max": float(os.getenv("JEV_INJECTION_MAX", "0.70")),    # 超过此值绝不进入提示词
    "contradicts_min": float(os.getenv("JEV_CONTRADICTS_MIN", "0.70")),  # 超过此值送冲突块
    "relevant_min_score": int(os.getenv("JEV_RELEVANT_MIN_SCORE", "1")),  # 语义档位低于此值丢弃
    "evidence_min_score": int(os.getenv("JEV_EVIDENCE_MIN_SCORE", "2")),  # 达到此值才作为证据
}

# ==================== 段落路由（固定顺序，先匹配先用） ====================

def route(judge: Dict) -> str:
    """
    四问判定结果 -> include / conflict / exclude

    顺序与 cookbook 一致：
      1. 注入是安全决定，放最前（相关性再高也排除）
      2. 矛盾测试在证据测试之前（否定前提的段落通常也"含可用内容"，
         反过来测会落进证据块而不是冲突块）
      3. 相关性下限
      4. 证据门槛
    """
    if judge.get("injection", 0) > PASSAGE_FILTER_THRESHOLDS["injection_max"]:
        return "exclude"
    if judge.get("contradicts", 0) > PASSAGE_FILTER_THRESHOLDS["contradicts_min"]:
        return "conflict"
    if judge.get("score", 0) < PASSAGE_FILTER_THRESHOLDS["relevant_min_score"]:
        return "exclude"
    if judge.get("score", 0) >= PASSAGE_FILTER_THRESHOLDS["evidence_min_score"]:
        return "include"
    return "exclude"

# ==================== 段落过滤执行器 ====================

def filter_passages(question: str, items: List[Dict], text_key: str = "chunk_text",
                    max_workers: int = 4) -> Dict:
    """
    对重排后的候选做四问判定并路由。

    :param items: [{"index":1, "chunk_text":"...", ...}, ...]
    :return: {"evidence": [...], "conflicts": [...], "dropped": [...], "stats": {...}}
              每项为原 item 追加 {"jev": {"score","contradicts","injection","route"}}
    """
    if not items:
        return {"evidence": [], "conflicts": [], "dropped": [], "stats": {"enabled": False}}

    def judge_one(item):
        try:
            return item, judge_chunk(question, item.get(text_key) or ""), None
        except Exception as e:            # fail-open：单条判定失败按原样放行
            logger.warning(f"Jev 判定失败（fail-open 放行）：{e}")
            return item, None, e

    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        results = list(pool.map(judge_one, items))

    evidence, conflicts, dropped = [], [], []
    for item, judge, _err in results:
        if judge is None:
            item["jev"] = {"route": "include", "fail_open": True}
            evidence.append(item)
            continue
        action = route(judge)
        item["jev"] = dict(judge, route=action)
        if action == "include":
            evidence.append(item)
        elif action == "conflict":
            conflicts.append(item)
        else:
            dropped.append(item)

    stats = {"enabled": True, "in": len(items), "evidence": len(evidence),
             "conflict": len(conflicts), "dropped": len(dropped)}
    logger.info(f"【Jev过滤】入 {stats['in']} → 证据 {stats['evidence']} / "
                f"冲突 {stats['conflict']} / 丢弃 {stats['dropped']}")
    return {"evidence": evidence, "conflicts": conflicts, "dropped": dropped, "stats": stats}

# ==================== 生成模板（证据/冲突 双块） ====================

ANSWER_DUAL_BLOCK = """你是企业知识库助手，只能依据【证据】与【冲突信息】回答用户问题。

规则：
1. 答案中引用资料时，在句末标注来源编号，如 [1]、[2]（编号对应资料前缀 [n]）。
2. 若【证据】不足以回答，明确回答"知识库中没有找到相关信息"，不要编造。
3. 若【冲突信息】否定了问题中假设的前提，必须明确指出"该前提不成立"并引用对应来源。
4. 【冲突信息】仅在问题前提确实与之相关时使用；用简洁的中文回答。
"""

def build_dual_block_context(evidence: List[Dict], conflicts: List[Dict]) -> str:
    """证据块 / 冲突块 分开组装（合并会让生成器无法区分"回答查询"与"否定前提"）"""
    def _block(title, items):
        if not items:
            return f"【{title}】\n（无）"
        body = "\n\n".join(
            f"[{it['index']}] （{it.get('doc_name')} / {it.get('title_path')}）\n{it.get('chunk_text')}"
            for it in items)
        return f"【{title}】\n{body}"
    return _block("证据", evidence) + "\n\n" + _block("冲突信息（与问题前提相矛盾，注意甄别）", conflicts)

# ==================== 引用核查（封闭分类） ====================

CITATION_QUESTIONS = {
    # 借鉴 cookbook 引用检查的封闭分类；实现复用 decision.citation_check（supported/unsupported 二元）
    "labels": ["supported", "unsupported", "contradicted", "fabricated"],
}
