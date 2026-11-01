"""
Q7 答案输出节点：组装上下文 + 会话历史，LLM 流式生成带引用答案

- stream_callback（可选）：state 中携带的回调 fn(text_chunk)，用于 SSE/控制台流式输出
- 会话历史：问答双方写入 PostgreSQL（session_id 存在时）
- Jev 模板嵌入：上游 node_jev_filter 产出 证据块/冲突块 时走双块生成模板（jev_templates.py），
  并在生成后做引用核查（decision.citation_check，fail-safe：失败不阻断）
"""

from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.jev_templates import ANSWER_DUAL_BLOCK, build_dual_block_context
from kb_rag.query_process.prompt import ANSWER_SYSTEM
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.decision import citation_check
from kb_rag.utils.history_utils import add_message, get_history
from kb_rag.utils.llm_utils import chat, stream_answer

logger = setup_logger("query.answer")


class NodeAnswer(QueryNodeBase):
    """Q7 答案输出节点"""

    name = "node_answer"

    HISTORY_TURNS = 6      # 携带最近 N 条历史
    GATE_MIN_SCORE = 2     # 可答门控：最高重排分 < 2 时视为"答不了"

    NO_ANSWER_TEXT = "知识库中没有找到与该问题相关的信息，无法回答。"

    def process(self, state: QueryGraphState):
        question = state.get("question")
        if not question:
            raise ValueError("question 不能为空")

        session_id = state.get("session_id") or ""
        evidence = state.get("evidence")
        conflicts = state.get("conflicts") or []
        jev_stats = state.get("jev_stats") or {}
        reranked = state.get("reranked") or []
        callback = state.get("stream_callback")

        # Jev 过滤未启用（消融/旁路）时回退：reranked 即证据
        if evidence is None:
            evidence = reranked

        # 步骤0：可答门控（Jev 式：答不了就不调生成，省时省成本）
        max_score = max((item.get("rerank_score", 0) for item in evidence or reranked), default=0)
        if max_score < self.GATE_MIN_SCORE:
            logger.info(f"【门控】最高重排分 {max_score} < {self.GATE_MIN_SCORE}，跳过 LLM 生成，直接回答未找到")
            if session_id:
                add_message(session_id, "user", question)
                add_message(session_id, "assistant", self.NO_ANSWER_TEXT, refs=[])
            if callback:
                callback(self.NO_ANSWER_TEXT)
            return {"answer": self.NO_ANSWER_TEXT, "refs": [], "gated": True}

        # 步骤1：组装参考资料块（Jev 双块模板：证据/冲突分离；否则单块，消融兼容）
        if jev_stats.get("enabled"):
            system_prompt = ANSWER_DUAL_BLOCK
            context_blocks = build_dual_block_context(evidence, conflicts)
        else:
            system_prompt = ANSWER_SYSTEM
            context_blocks = "\n\n".join(
                f"[{item['index']}] （{item.get('doc_name')} / {item.get('title_path')}）\n{item.get('chunk_text')}"
                for item in evidence
            ) if evidence else "（无）"

        # 步骤2：携带会话历史
        messages = [{"role": "system", "content": system_prompt}]
        if session_id:
            for h in get_history(session_id, limit=self.HISTORY_TURNS):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({
            "role": "user",
            "content": f"【参考资料】\n{context_blocks}\n\n【问题】\n{question}",
        })

        # 步骤3：记录用户消息（先于回答，保证历史顺序）
        if session_id:
            add_message(session_id, "user", question)

        # 步骤4：流式生成答案（ThinkFilter 清洗思考型模型推理段）
        thinking_cb = state.get("thinking_callback")
        if callback:
            answer = stream_answer(messages, temperature=0.2, on_delta=callback, on_thinking=thinking_cb)
        else:
            answer = chat(messages, temperature=0.2) or ""
            answer = self._strip_think_full(answer).strip()

        answer = answer.strip()
        refs = [
            {
                "index": item["index"],
                "doc_name": item.get("doc_name"),
                "title_path": item.get("title_path"),
                "score": round(item.get("rerank_score", 0), 2),
                "chunk_text": (item.get("chunk_text") or "")[:300],
            }
            for item in (evidence + conflicts)
        ]

        # 步骤4.5：引用核查（Jev citation 模板；流式已发出，核查结果仅标记与告警，不阻断）
        citation = {"supported": True, "unsupported_refs": []}
        if answer and refs:
            citation = citation_check(question, answer, refs)
            if not citation["supported"]:
                logger.warning(f"【引用核查】存在不被支撑的引用：{citation['unsupported_refs']}")

        # 步骤5：答案入库（写历史）
        if session_id:
            add_message(session_id, "assistant", answer, refs=refs)

        logger.info(f"【答案】{answer[:120]}...（refs={len(refs)}，"
                    f"jev={'on' if jev_stats.get('enabled') else 'off'}，"
                    f"citation={'ok' if citation['supported'] else 'unsupported'}）")
        return {"answer": answer, "refs": refs, "citation_check": citation}

    @staticmethod
    def _strip_think_full(text: str) -> str:
        import re
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
