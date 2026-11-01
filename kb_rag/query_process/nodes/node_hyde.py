"""Q3 HyDE 节点：假设性文档生成 + 二次检索"""

from kb_rag.query_process.base import QueryNodeBase
from kb_rag.query_process.prompt import HYDE_GENERATION
from kb_rag.query_process.state import QueryGraphState
from kb_rag.tool.logger import setup_logger
from kb_rag.utils.embedding_utils import embed_query
from kb_rag.utils.llm_utils import chat
from kb_rag.utils.milvus_utils import hybrid_search_chunks

logger = setup_logger("query.hyde")


class NodeHyDE(QueryNodeBase):
    """Q3 假设性文档向量搜索（HyDE）"""

    name = "node_hyde"

    TOP_K = 10

    def process(self, state: QueryGraphState):
        question = state.get("question")
        if not question:
            raise ValueError("question 不能为空")

        # 步骤1：生成假设性答案段落
        try:
            prompt = HYDE_GENERATION.format(question=question)
            hyde_answer = chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=512,
                disable_thinking=True,  # 简短假设段落，关思考提速
            ) or ""
            hyde_answer = hyde_answer.strip()[:500]
        except Exception as e:
            logger.error(f"HyDE 生成失败（跳过本路召回）：{e}")
            return {"hyde_hits": []}

        if not hyde_answer:
            return {"hyde_hits": []}

        logger.debug(f"HyDE 假设答案: {hyde_answer[:80]}...")

        # 步骤2：假设答案稠密检索（不带主体过滤，融合阶段去重）
        item_name = state.get("item_name") or None
        try:
            hits = hybrid_search_chunks(hyde_answer, embed_query(hyde_answer), top_k=self.TOP_K, item_name=item_name)
        except Exception as e:
            logger.error(f"HyDE 检索失败（跳过本路召回）：{e}")
            hits = []

        return {"hyde_hits": hits, "hyde_answer": hyde_answer}
