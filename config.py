"""旁挂决策服务配置（全部环境变量可覆盖）"""
import os

# SuperSonic 语义层（取数通道）
SUPERSONIC_BASE = os.getenv("SUPERSONIC_BASE", "http://127.0.0.1:9080")
SUPERSONIC_USER = os.getenv("SUPERSONIC_USER", "admin")
SUPERSONIC_PASS = os.getenv("SUPERSONIC_PASS", "")

# Doris 直连（归因/异常基线，MySQL 协议）
DORIS_HOST = os.getenv("DORIS_HOST", "127.0.0.1")
DORIS_PORT = int(os.getenv("DORIS_PORT", "9030"))
DORIS_USER = os.getenv("DORIS_USER", "root")
DORIS_PASS = os.getenv("DORIS_PASS", "")
DORIS_DB = os.getenv("DORIS_DB", "dw")

# GLM 网关（决策洞察 LLM；用非思考型 deepseek-v4-flash——思考型推理会吃满预算且慢）
GLM_BASE = os.getenv("GLM_BASE", "http://127.0.0.1:8000/v1")
GLM_KEY = os.getenv("GLM_KEY", "")
GLM_MODEL = os.getenv("GLM_MODEL", "deepseek-v4-flash")

# 主分析模型（默认 ads_gmv_total_day）
DEFAULT_MODEL_TABLE = os.getenv("DEFAULT_MODEL_TABLE", "ads_gmv_total_day")
DEFAULT_DATE_COL = os.getenv("DEFAULT_DATE_COL", "dt")
DEFAULT_METRIC = os.getenv("DEFAULT_METRIC", "gmv")
DEFAULT_METRIC_NAME = os.getenv("DEFAULT_METRIC_NAME", "GMV")

# ============ 归因定义（口径与 ads_gmv_total_day 精确对齐） ============
# 归因基表 + 已支付过滤：SUM(total_amount WHERE order_status='1001') == ads GMV（已对拍）
ATTRIBUTION_TABLE = os.getenv("ATTRIBUTION_TABLE", "dwd_order_info_inc o")
ATTRIBUTION_PAID_FILTER = os.getenv("ATTRIBUTION_PAID_FILTER", "o.order_status = '1001'")
# 主指标 → 基表上的内层列（engine 按 SUM/COUNT DISTINCT 包 CASE WHEN）
ATTRIBUTION_METRIC_COL = {
    "gmv": "o.total_amount",
    "order_cnt": "o.id", "order_count": "o.id",
    "payer_cnt": "o.user_id", "payer_count": "o.user_id",
}
# 维度定义：join 片段 + 取值列（sku 特殊：走明细分摊金额）
ATTRIBUTION_DIM_DEFS = {
    "province":     {"join": "LEFT JOIN dim_base_province p ON o.province_id = p.id",
                     "expr": "p.name"},
    "service_area": {"join": "LEFT JOIN dim_service_area sa ON o.service_area_id = sa.id",
                     "expr": "sa.name"},
    "county":       {"join": "LEFT JOIN dim_service_area sa ON o.service_area_id = sa.id",
                     "expr": "sa.county"},
    "road_section": {"join": "LEFT JOIN dim_service_area sa ON o.service_area_id = sa.id",
                     "expr": "sa.road_section"},
    "direction":    {"join": "LEFT JOIN dim_service_area sa ON o.service_area_id = sa.id",
                     "expr": "sa.direction"},
    "level":        {"join": "LEFT JOIN dim_service_area sa ON o.service_area_id = sa.id",
                     "expr": "sa.level"},
    "sku":          {"join": "JOIN dwd_order_detail_inc d ON d.order_id = o.id",
                     "expr": "d.sku_name",
                     "amount_override": {"gmv": "d.split_total_amount"}},
    "member_level": {"join": "LEFT JOIN dim_user_info u ON o.user_id = u.id",
                     "expr": "u.user_level"},
    "member":       {"join": "LEFT JOIN dim_user_info u ON o.user_id = u.id",
                     "expr": "u.nick_name", "group": "u.id, u.nick_name"},
}
ATTRIBUTION_DEFAULT_DIMS = ["service_area", "province", "sku"]

# 异常检测（沿用 AskInsight 协议）
Z_WARN = float(os.getenv("Z_WARN", "2.0"))
Z_CRIT = float(os.getenv("Z_CRIT", "3.0"))
BASELINE_DAYS = int(os.getenv("BASELINE_DAYS", "28"))

# 分析意图门控（沿用 AskInsight 词表）
ANALYSIS_INTENT_WORDS = [
    "为什么", "为何", "怎么", "如何", "分析", "解读", "洞察", "异常", "归因",
    "对比", "原因", "建议", "趋势", "走势", "波动", "变化", "分布",
    "why", "how", "analyze", "insight", "compare", "trend",
]
