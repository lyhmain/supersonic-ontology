"""业务本体配置层：不同公司/行业通过 profile JSON 接入，零代码修改。

一个 profile = 一家公司的语义本体：
  fact        事实表 + 日期列 + 口径过滤（如 order_status='1001'）
  ads         日汇总表 + 指标表达式（总量/每日序列走它）
  metrics     指标词典：业务词 → 指标键（列/计数列/派生公式）
  dimensions  维度词典：业务词 → 维度键（JOIN 片段 + 取值列 + 组粒度）
  value_tokens 维度取值令牌（如 县市01 / G25长深高速 / 西湖店）
  holidays    业务日历（法定节假日 / 公司周年庆等）
  intent      意图词表（分析词/同环比词/分组词）
语言级模式（日期/TopN/HAVING/每日）与公司无关，留在 nl.py。
"""
import json
import os
import re
from datetime import date

PROFILES_DIR = os.getenv(
    "ONTOLOGY_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "profiles"))
DEFAULT_PROFILE = os.getenv("ONTOLOGY_PROFILE", "gasa_ecom")

DEFAULT_INTENT = {
    "analysis_words": ["为什么", "为何", "原因", "归因", "异常", "解读", "洞察",
                       "建议", "分析"],
    "ratio_words": ["同比", "环比", "增长多少", "变化率", "增长了多少"],
    "group_words": ["各", "每个", "分别", "排行", "排名", "最高", "最低"],
}


class Ontology:
    """一家公司/一个业务的语义本体（从 profile JSON 构建的一次性编译产物）"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.profile = cfg.get("profile", "default")
        self.domain = cfg.get("domain", "")

        fact = cfg["fact"]
        self.fact_table = fact["table"]                      # 可带别名: "dwd_xxx o"
        parts = fact["table"].split()
        self.alias = parts[1] if len(parts) > 1 else "t"
        self.date_col = fact.get("date_col", "dt")
        self.paid_filter = fact.get("paid_filter", "1=1")
        self.dcol = f"{self.alias}.{self.date_col}"

        ads = cfg.get("ads") or {}
        self.ads_table = ads.get("table", parts[0])
        self.ads_metric_exprs = ads.get("metric_exprs", {})

        self.metrics = cfg["metrics"]
        self.dims = cfg.get("dimensions", {})
        self.value_tokens = cfg.get("value_tokens", [])
        self.holidays = {k: {y: tuple(v) for y, v in ys.items()}
                         for k, ys in (cfg.get("holidays") or {}).items()}
        self.intent = {**DEFAULT_INTENT, **(cfg.get("intent") or {})}
        self.analysis_words = self.intent["analysis_words"]
        self.ratio_words = self.intent["ratio_words"]
        self.group_words = self.intent["group_words"]
        self.attribution_dims = (cfg.get("attribution_default_dims")
                                 or list(self.dims)[:3])
        an = cfg.get("anomaly") or {}
        self.z_warn = float(an.get("z_warn", 2.0))
        self.z_crit = float(an.get("z_crit", 3.0))
        self.baseline_days = int(an.get("baseline_days", 28))
        self.default_metric = cfg.get("default_metric") or next(iter(self.metrics))

        self.metric_patterns = sorted(
            ((w, k) for k, d in self.metrics.items() for w in d.get("words", [])),
            key=lambda x: -len(x[0]))
        self.dim_patterns = sorted(
            ((w, k) for k, d in self.dims.items() for w in d.get("words", [])),
            key=lambda x: -len(x[0]))
        self._value_tokens = [(vt["attr"], re.compile(vt["regex"]), vt.get("when") or [])
                              for vt in self.value_tokens]

    # ---------- 词典查询 ----------
    def find_metric(self, q: str):
        k, name, _ = self.find_metric_full(q)
        return k, name

    def find_metric_full(self, q: str):
        """返回 (key, name, matched)——matched=False 表示走了默认指标兜底(词表未命中)"""
        ql = q.lower()
        for w, k in self.metric_patterns:
            if w.lower() in ql:
                return k, self.metric_name(k), True
        return self.default_metric, self.metric_name(self.default_metric), False

    def find_metrics_multi(self, q: str):
        words = sorted({w for w, _ in self.metric_patterns}, key=len, reverse=True)
        if not words:
            return None
        alt = "|".join(re.escape(w) for w in words)
        m = re.search(rf"({alt})(?:和|与|及)({alt})", q, re.I)
        if m:
            k1, k2 = self._word_key(m.group(1)), self._word_key(m.group(2))
            if k1 and k2 and k1 != k2:
                return [k1, k2]
        return None

    def _word_key(self, word: str):
        for w, k in self.metric_patterns:
            if w.lower() == word.lower():
                return k
        return None

    def find_dim(self, q: str):
        ql = q.lower()
        for w, k in self.dim_patterns:
            if w.lower() in ql:
                return k
        return None

    def find_value_token(self, q: str, dim: str = None, group_intent: bool = False):
        """维度取值令牌。dim+分组词已锁定同名维度时不误吞(如'各门店'≠'西湖店')"""
        for attr, rx, when in self._value_tokens:
            if when and not any(w in q for w in when):
                continue
            if group_intent and attr == dim:
                continue
            m = rx.search(q)
            if m:
                return attr, m.group(1)
        return None

    # ---------- 结构查询 ----------
    def metric_name(self, key: str) -> str:
        return (self.metrics.get(key) or {}).get("name", key)

    def metric_col(self, key: str):
        """返回 (基表列, 是否计数指标)。sum_col=可加列(SUM),count_col=去重计数"""
        d = self.metrics.get(key) or {}
        if d.get("sum_col"):
            return f"{self.alias}.{d['sum_col']}", False
        if d.get("count_col"):
            return f"{self.alias}.{d['count_col']}", True
        return f"{self.alias}.{d.get('col', key)}", False

    def derive(self, key: str):
        return (self.metrics.get(key) or {}).get("derive")

    def dim_def(self, key: str):
        return self.dims.get(key)

    def ads_expr(self, key: str) -> str:
        return self.ads_metric_exprs.get(key, f"SUM({key})")

    def holiday_range(self, q: str, max_date: date):
        for name, years in self.holidays.items():
            if name in q:
                ym = re.search(r"(20\d{2})年", q)
                if ym:
                    year = ym.group(1)
                elif "去年" in q:
                    year = str(max_date.year - 1)
                else:
                    year = str(max_date.year)
                rng = years.get(year) or years.get(str(max_date.year))
                if rng:
                    return date.fromisoformat(rng[0]), date.fromisoformat(rng[1])
        return None


_profiles: dict = {}


def get(name=None) -> Ontology:
    name = name or DEFAULT_PROFILE
    if name not in _profiles:
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        if not os.path.exists(path):
            avail = sorted(f[:-5] for f in os.listdir(PROFILES_DIR)
                           if f.endswith(".json"))
            raise FileNotFoundError(
                f"profile '{name}' 不存在: {path}。"
                f"注意: profile 标识取【文件名】(demo_tea.json → demo_tea)。"
                f"当前可用: {avail or '(profiles/ 为空)'}")
        with open(path, encoding="utf-8") as f:
            _profiles[name] = Ontology(json.load(f))
    return _profiles[name]


def reload(name: str = None, drop: bool = False):
    """UI 保存后失效缓存;drop=True 仅移除"""
    if name is None:
        _profiles.clear()
        return
    if drop or not os.path.exists(os.path.join(PROFILES_DIR, f"{name}.json")):
        _profiles.pop(name, None)
    else:
        _profiles.pop(name, None)


def all_profiles():
    try:
        return sorted(f[:-5] for f in os.listdir(PROFILES_DIR) if f.endswith(".json"))
    except OSError:
        return []
