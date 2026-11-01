# -*- coding: utf-8 -*-
"""规范配置(Standard Spec)编译器:面向普通数据开发的简化格式 → 完整本体 profile。

设计原则(约定优于配置):
  - 中文字段名,数据开发按"表/指标/维度/验收"四张清单填
  - 指标类型四选一:金额(求和)/数量(求和)/次数(去重计数)/人数(去重计数)/比值(两指标相除)
  - 维度:主表列 或 维表(自动推 JOIN);点查词列表替代正则令牌
  - 其余(意图词/异常阈值/归因顺序)全部走默认
"""
import re

METRIC_TYPES = {"金额": "sum", "数量": "sum", "次数": "count", "人数": "count"}


class SpecError(ValueError):
    pass


def compile_spec(spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise SpecError("规范配置必须是 JSON 对象")
    table = (spec.get("主表") or {}).get("表") or ""
    if not re.match(r"^[A-Za-z0-9_]+$", table):
        raise SpecError("主表.表 必填且只能是字母数字下划线")
    date_col = (spec.get("主表") or {}).get("日期列") or ""
    if not re.match(r"^[A-Za-z0-9_]+$", date_col):
        raise SpecError("主表.日期列 必填且只能是字母数字下划线")
    alias = "t"
    filt = (spec.get("主表") or {}).get("过滤") or ""
    if filt:
        conds = [c.strip() for c in re.split(r"\s+and\s+", filt, flags=re.I) if c.strip()]
        conds = [c if "." in c.split(" ")[0] else f"{alias}.{c}" for c in conds]
        paid = " AND ".join(conds)
    else:
        paid = "1=1"

    # ---- 指标 ----
    metrics_in = spec.get("指标") or []
    if not metrics_in:
        raise SpecError("指标 不能为空(至少 1 条)")
    metrics, exprs = {}, {}
    names = set()
    for m in metrics_in:
        name = (m.get("名称") or "").strip()
        if not name:
            raise SpecError("指标.名称 不能为空")
        if name in names:
            raise SpecError(f"指标名称重复: {name}")
        names.add(name)
    for m in metrics_in:
        name = m["名称"].strip()
        typ = (m.get("类型") or "").strip()
        col = (m.get("列") or "").strip()
        words = list(dict.fromkeys([name] + [w.strip() for w in (m.get("同义词") or []) if w.strip()]))
        if typ == "比值":
            num, den = (m.get("分子") or "").strip(), (m.get("分母") or "").strip()
            if num not in names or den not in names:
                raise SpecError(f"比值指标「{name}」的 分子/分母 必须引用已定义的指标名称")
            metrics[name] = {"name": name, "words": words,
                             "derive": {"num": num, "den": den}}
            exprs[name] = None          # 占位,待数值指标表达式齐后再拼
        elif typ in METRIC_TYPES:
            if not re.match(r"^[A-Za-z0-9_]+$", col):
                raise SpecError(f"指标「{name}」的 列 必填且只能是字母数字下划线")
            if METRIC_TYPES[typ] == "count":
                metrics[name] = {"name": name, "words": words, "count_col": col}
                exprs[name] = f"COUNT(DISTINCT {col})"
            else:
                metrics[name] = {"name": name, "words": words, "col": col}
                exprs[name] = f"SUM(COALESCE({col},0))"
        else:
            raise SpecError(f"指标「{name}」类型必须是: 金额/数量/次数/人数/比值")
    for k, v in metrics.items():
        if v.get("derive"):
            ne, de = exprs[v["derive"]["num"]], exprs[v["derive"]["den"]]
            exprs[k] = f"({ne})/NULLIF({de},0)"

    # ---- 维度(主表列 + 维表) ----
    dims, joins, tokens = {}, [], []
    for d in spec.get("维度") or []:
        name = (d.get("名称") or "").strip()
        col = (d.get("列") or "").strip()
        if not name or not re.match(r"^[A-Za-z0-9_]+$", col):
            raise SpecError(f"维度「{name}」需要 名称 + 列(字母数字下划线)")
        words = list(dict.fromkeys([name] + [w.strip() for w in (d.get("同义词") or []) if w.strip()]))
        dims[name] = {"name": name, "words": words, "expr": f"{alias}.{col}", "join": ""}
        if d.get("点查词"):
            alt = "|".join(re.escape(w) for w in d["点查词"])
            tokens.append({"attr": name, "regex": f"({alt})"})
    for i, dt_ in enumerate(spec.get("维表") or []):
        tname = (dt_.get("表") or "").strip()
        fk = (dt_.get("外键") or "").strip()
        if not re.match(r"^[A-Za-z0-9_]+$", tname) or not re.match(r"^[A-Za-z0-9_]+$", fk):
            raise SpecError(f"维表「{tname}」需要 表 + 外键(字母数字下划线)")
        a = f"d{i}"
        joins.append(f"LEFT JOIN {tname} {a} ON {alias}.{fk} = {a}.id")
        join_sql = " ".join(joins)
        for d in dt_.get("维度") or []:
            name = (d.get("名称") or "").strip()
            col = (d.get("列") or "").strip()
            if not name or not re.match(r"^[A-Za-z0-9_]+$", col):
                raise SpecError(f"维表「{tname}」维度「{name}」需要 名称 + 列")
            words = list(dict.fromkeys([name] + [w.strip() for w in (d.get("同义词") or []) if w.strip()]))
            dims[name] = {"name": name, "words": words, "expr": f"{a}.{col}", "join": join_sql}
            if d.get("点查词"):
                alt = "|".join(re.escape(w) for w in d["点查词"])
                tokens.append({"attr": name, "regex": f"({alt})"})

    accept = spec.get("验收")
    if accept is None:
        m0 = metrics_in[0]["名称"]
        accept = [{"q": f"昨天{m0}是多少", "action": "aggregate"}]

    return {
        "profile": spec.get("profile") or table.lower(),
        "domain": spec.get("业务说明") or f"{table}(规范配置)",
        "default_metric": metrics_in[0]["名称"],
        "fact": {"table": f"{table} {alias}", "date_col": date_col, "paid_filter": paid},
        "ads": {"table": table,
                "metric_exprs": {k: v for k, v in exprs.items() if v is not None}},
        "metrics": metrics,
        "dimensions": dims,
        "value_tokens": tokens,
        "holidays": spec.get("节日") or {},
        "intent": {},
        "attribution_default_dims": list(dims)[:3],
        "anomaly": {"z_warn": 2.0, "z_crit": 3.0, "baseline_days": 28},
        "acceptance": accept,
    }


SPEC_TEMPLATE = {
    "profile": "tea",
    "业务说明": "连锁茶饮(示例)",
    "主表": {"表": "demo_tea_orders", "日期列": "dt", "过滤": "is_refund = 0"},
    "维表": [
        {"表": "demo_tea_store", "外键": "store_id",
         "维度": [{"名称": "门店", "列": "name", "点查词": ["西湖店", "湖滨店", "观前店"]},
                  {"名称": "城市", "列": "city"}]}
    ],
    "指标": [
        {"名称": "营业额", "类型": "金额", "列": "amount", "同义词": ["流水", "销售额"]},
        {"名称": "杯数", "类型": "数量", "列": "cups", "同义词": ["出杯量"]},
        {"名称": "杯单价", "类型": "比值", "分子": "营业额", "分母": "杯数"}
    ],
    "验收": [
        {"q": "昨天营业额是多少", "value": 9828.65},
        {"q": "昨天杯数是多少", "value": 554},
        {"q": "近30天各门店营业额", "rows": 3},
        {"q": "西湖店营业额是多少", "value": 12580.75}
    ]
}
