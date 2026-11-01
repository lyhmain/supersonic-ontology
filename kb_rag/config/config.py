"""
KB-RAG 知识库插件全局配置

复用 infra 外部服务：
- LLM: GLM 网关（OpenAI 兼容）
- VL / Embedding: GPUStack / 任意 OpenAI 兼容网关
- Milvus: 自建实例(默认 19530)
- MinIO: Milvus 内嵌实例（只允许操作自建桶）
- Chat History: PostgreSQL
"""

from dataclasses import dataclass, field
from typing import Optional
import os

from dotenv import load_dotenv

load_dotenv(override=True)  # .env 优先于系统环境变量（避免旧 key 干扰）


@dataclass
class KBConfig:
    """KB-RAG 知识库插件全局配置"""

    # ==================== LLM（GLM 网关） ====================
    llm_api_base: str = field(default_factory=lambda: os.getenv("LLM_API_BASE", ""))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    default_model: str = field(default_factory=lambda: os.getenv("LLM_DEFAULT_MODEL", "GLM-5.3-Flash"))
    llm_temperature: float = field(default_factory=lambda: float(os.getenv("LLM_TEMPERATURE", "0.1")))

    # ==================== 视觉模型（GPUStack） ====================
    vl_api_base: str = field(default_factory=lambda: os.getenv("VL_API_BASE", ""))
    vl_api_key: str = field(default_factory=lambda: os.getenv("VL_API_KEY", ""))
    vl_model: str = field(default_factory=lambda: os.getenv("VL_MODEL", "Qwen3.6-VL-27B-Instruct"))

    # ==================== Embedding（GPUStack） ====================
    embed_api_base: str = field(default_factory=lambda: os.getenv("EMBED_API_BASE", ""))
    embed_api_key: str = field(default_factory=lambda: os.getenv("EMBED_API_KEY", ""))
    embed_model: str = field(default_factory=lambda: os.getenv("EMBED_MODEL", "Qwen3-VL-8B-embedding"))
    embedding_dim: int = field(default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "4096")))
    embedding_batch_size: int = field(default_factory=lambda: int(os.getenv("EMBED_BATCH_SIZE", "8")))

    # ==================== Milvus ====================
    milvus_uri: str = field(default_factory=lambda: os.getenv("MILVUS_URL", ""))
    milvus_user: str = field(default_factory=lambda: os.getenv("MILVUS_USER", "root"))
    milvus_password: str = field(default_factory=lambda: os.getenv("MILVUS_PASSWORD", ""))
    chunks_collection: str = field(default_factory=lambda: os.getenv("CHUNKS_COLLECTION", "kb_rag_chunks"))
    item_name_collection: str = field(default_factory=lambda: os.getenv("ITEM_NAME_COLLECTION", "kb_rag_item_names"))
    milvus_metric_type: str = field(default_factory=lambda: os.getenv("MILVUS_METRIC_TYPE", "COSINE"))
    min_cosine_score: float = field(default_factory=lambda: float(os.getenv("MILVUS_MIN_COSINE_SCORE", "0.75")))

    # ==================== MinIO ====================
    minio_endpoint: str = field(default_factory=lambda: os.getenv("MINIO_ENDPOINT", ""))
    minio_access_key: str = field(default_factory=lambda: os.getenv("MINIO_ACCESS_KEY", ""))
    minio_secret_key: str = field(default_factory=lambda: os.getenv("MINIO_SECRET_KEY", ""))
    minio_bucket: str = field(default_factory=lambda: os.getenv("MINIO_BUCKET_NAME", "knowledge-base"))
    minio_secure: bool = field(default_factory=lambda: os.getenv("MINIO_SECURE", "false").lower() == "true")

    # ==================== PostgreSQL（会话历史） ====================
    pg_host: str = field(default_factory=lambda: os.getenv("PG_HOST", ""))
    pg_port: int = field(default_factory=lambda: int(os.getenv("PG_PORT", "5432")))
    pg_user: str = field(default_factory=lambda: os.getenv("PG_USER", "app"))
    pg_password: str = field(default_factory=lambda: os.getenv("PG_PASSWORD", ""))
    pg_db: str = field(default_factory=lambda: os.getenv("PG_DB", "app"))
    pg_history_table: str = field(default_factory=lambda: os.getenv("PG_HISTORY_TABLE", "chat_history"))

    # ==================== MinerU ====================
    mineru_gpustack_base: str = field(default_factory=lambda: os.getenv("MINERU_GPUSTACK_BASE", ""))
    mineru_api_token: str = field(default_factory=lambda: os.getenv("MINERU_API_TOKEN", ""))
    mineru_base_url: str = field(default_factory=lambda: os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4"))

    # ==================== 阿里百炼（DashScope） ====================
    # 专属端点（chat/联网搜索）+ 公网 services（rerank）共用一个 ws key
    dashscope_api_key: str = field(default_factory=lambda: os.getenv("DASHSCOPE_API_KEY", ""))
    dashscope_base_url: str = field(default_factory=lambda: os.getenv(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    dashscope_rerank_model: str = field(default_factory=lambda: os.getenv("DASHSCOPE_RERANK_MODEL", "qwen3.7-text-rerank"))
    web_search_model: str = field(default_factory=lambda: os.getenv("WEB_SEARCH_MODEL", "qwen-flash"))

    # ==================== Reranker（自适应） ====================
    # mode: auto（默认，检测到 HTTP reranker 用之，否则 LLM 打分）/ http / llm
    reranker_mode: str = field(default_factory=lambda: os.getenv("RERANKER_MODE", "auto"))
    reranker_api_base: str = field(default_factory=lambda: os.getenv("RERANKER_API_BASE", os.getenv("VL_API_BASE", "")))
    reranker_api_key: str = field(default_factory=lambda: os.getenv("RERANKER_API_KEY", os.getenv("VL_API_KEY", "")))
    reranker_model: str = field(default_factory=lambda: os.getenv("RERANKER_MODEL", ""))  # 留空自动检测

    # ==================== 决策层（Jev 兼容，ollaya 等） ====================
    # 留空则全部走 GLM 伪决策；配置后 noul/choice 优先走本地校准决策模型
    decision_api_base: str = field(default_factory=lambda: os.getenv("DECISION_API_BASE", ""))
    decision_model: str = field(default_factory=lambda: os.getenv("DECISION_MODEL", "laya:multilingual"))
    # 原语实现基座：glm（GLM 网关）| dashscope（百炼 qwen-flash，快且便宜）
    decision_backend: str = field(default_factory=lambda: os.getenv("DECISION_BACKEND", "glm"))
    decision_llm_model: str = field(default_factory=lambda: os.getenv("DECISION_LLM_MODEL", "qwen-flash"))

    # ==================== 文档处理 ====================
    max_content_length: int = 2000      # 切片最大长度
    img_content_length: int = 200       # 图片上下文最大长度
    min_content_length: int = 500       # 合并短内容的最小长度
    overlap_sentences: int = 1          # 句子级切分时的重叠句数
    item_name_chunk_k: int = 3          # 主体识别使用的切片数量
    item_name_chunk_size: int = 2500    # 主体识别使用的切片内容长度
    data_root_dir: str = field(default_factory=lambda: os.getenv("DATA_BASED_ROOT_DIR", "./data"))

    @classmethod
    def from_env(cls) -> "KBConfig":
        return cls()

    def get_minio_base_url(self) -> str:
        protocol = "https://" if self.minio_secure else "http://"
        return f"{protocol}{self.minio_endpoint}"


_config: Optional[KBConfig] = None


def get_config() -> KBConfig:
    """获取配置单例"""
    global _config
    if _config is None:
        _config = KBConfig.from_env()
    return _config
