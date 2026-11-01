"""检索流程图状态定义"""

from typing import TypedDict


class QueryGraphState(TypedDict, total=False):
    """检索问答图的状态定义"""

    task_id: str            # 任务唯一ID
    session_id: str         # 会话ID（历史管理）

    question: str           # 用户问题
    item_name: str          # Q1 识别/匹配到的知识库主体（空串表示未匹配）

    # Q1 产品确认结论
    confirm_decision: str   # search / refuse
    confirm_reason: str     # 判断理由（用于日志与调试）

    # 多路召回结果
    hybrid_hits: list       # Q2 服务端混合检索（dense+BM25 RRF）
    hyde_hits: list         # Q3 HyDE 假设性文档检索
    web_hits: list          # Q4 网络搜索（可选）

    # 融合与重排
    fused: list             # Q5 客户端 RRF 融合后的候选
    reranked: list          # Q6 LLM 重排 + 断崖截断后的上下文

    # Jev 段落过滤（Q6.5，模板见 jev_templates.py）
    evidence: list          # 四问路由后作为证据进入提示词的条目
    conflicts: list         # 与问题前提相矛盾的条目（双块生成用）
    jev_stats: dict         # {"enabled","in","evidence","conflict","dropped"}
    citation_check: dict    # 引用核查结果 {"supported","unsupported_refs"}

    # 答案
    answer: str             # 最终答案（含 [n] 引用）
    refs: list              # 引用来源元数据
    gated: bool             # 可答门控是否触发（True=跳过了 LLM 生成）

    # 流式回调（可选）：def callback(text_chunk) -> None
    stream_callback: object
