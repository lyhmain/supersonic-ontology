"""取数与决策引擎：SuperSonic 语义取数 + Doris 归因/异常基线

- 同环比：当期 vs 前一期（roll）/ 前 7 期（over），Python 自算（确定性，等价
  SuperSonic chat 管线 MetricRatioCalcProcessor 的 RATIO_ROLL/RATIO_OVER 语义）
- 异常检测：Z-Score，基线取 Doris 滚动窗口（AskInsight 协议：|Z|>2 警告 / >3 严重）
- 归因分解：真 Δ贡献度 = (dim_当期 - dim_基期) / Σ|Δ|，对比 AskInsight 期内占比更严谨
"""
import logging
import math
import threading
from datetime import date, timedelta

import pymysql
import requests

from config import (ANALYSIS_INTENT_WORDS, ATTRIBUTION_DEFAULT_DIMS,
                    ATTRIBUTION_DIM_DEFS, ATTRIBUTION_METRIC_COL,
                    ATTRIBUTION_PAID_FILTER, ATTRIBUTION_TABLE, BASELINE_DAYS,
                    DEFAULT_DATE_COL, DEFAULT_METRIC, DEFAULT_METRIC_NAME,
                    DEFAULT_MODEL_TABLE, DORIS_DB, DORIS_HOST, DORIS_PASS,
                    DORIS_PORT, DORIS_USER, SUPERSONIC_BASE, SUPERSONIC_PASS,
                    SUPERSONIC_USER, Z_CRIT, Z_WARN)

log = logging.getLogger("decision.engine")

import ontology

METRIC_COLUMNS = {
    "gmv": "SUM(gmv)",
    "order_cnt": "SUM(order_count)",
    "order_count": "SUM(order_count)",
    "payer_cnt": "COUNT(DISTINCT user_id)",
    "payer_count": "COUNT(DISTINCT user_id)",
}


def analysis_wanted(question: str, onto=None) -> bool:
    """意图门控：命中业务本体分析词才走重分析尾部"""
    onto = onto or ontology.get()
    q = (question or "").lower()
    return any(w in q for w in onto.analysis_words)


class SupersonicClient:
    """SuperSonic 语义层取数（/api/semantic/query/metric，确定性无 LLM）"""

    def __init__(self):
        self.token = None

    def _login(self):
        j = requests.post(f"{SUPERSONIC_BASE}/api/auth/user/login",
                          json={"name": SUPERSONIC_USER, "password": SUPERSONIC_PASS},
                          timeout=15).json()
        self.token = j["data"]

    def _api(self, path, data=None, method=None):
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        r = requests.request(method or ("POST" if data is not None else "GET"),
                             SUPERSONIC_BASE + path, json=data, headers=h, timeout=60)
        r.raise_for_status()
        return r.json()

    def resolve_metric(self, model_table: str, metric: str):
        """指标名/别名 → SuperSonic (modelId, metricId, bizName)。输入容忍 gmv/GMV/gmv_metric。"""
        models = self._api(f"/api/semantic/model/getModelList/4", method="GET").get("data") or []
        model = next((m for m in models if m["name"] == model_table), None)
        if not model:
            raise ValueError(f"模型不存在: {model_table}")
        mets = self._api(f"/api/semantic/metric/getMetricList/{model['id']}",
                         method="GET").get("data") or []
        want = metric.lower().rstrip("_metric")
        hit = next((m for m in mets if m.get("bizName", "").lower() == want)
                   or None, None) or \
              next((m for m in mets if m.get("bizName", "").lower() == want + "_metric"), None) or \
              next((m for m in mets if (m.get("name") or "").lower() == want), None)
        if not hit:
            raise ValueError(f"模型 {model_table} 无指标 {metric}: 可用 "
                             f"{[m.get('bizName') for m in mets]}")
        return model["id"], hit["id"], hit.get("bizName"), hit.get("name") or hit.get("bizName")

    def query_metric(self, model_id: int, metric_id: int, dim_name: str,
                     start: str, end: str) -> list:
        """按维度+日期区间取数（dateMode=1 BETWEEN）。返回 [{dim, value}]"""
        req = {
            "metricIds": [metric_id],
            "dimensionNames": [dim_name],
            "dateInfo": {"dateMode": 1, "startDate": start, "endDate": end,
                         "period": "DAY", "dateField": dim_name if dim_name == "dt" else "dt",
                         "groupByDate": dim_name == "dt"},
        }
        j = self._api("/api/semantic/query/metric", req)
        if j.get("code") == 401 or (isinstance(j.get("code"), int) and j["code"] >= 500):
            self._login()
            j = self._api("/api/semantic/query/metric", req)
        if j.get("code") != 200:
            raise RuntimeError(f"SuperSonic 查询失败: {j.get('msg')}")
        data = j.get("data") or {}
        key = next((c["nameEn"] for c in (data.get("columns") or [])
                    if c.get("nameEn") and c["nameEn"] != dim_name and c["nameEn"] != "dt"), "value")
        out = []
        for row in (data.get("resultList") or []):
            v = row.get(key)
            out.append({dim_name: row.get(dim_name) or row.get("dt"),
                        "value": float(v) if v is not None else 0.0})
        return out

    def daily_series(self, metric: str, start: str, end: str) -> list:
        """语义层日序列（缺省模型 ads_gmv_total_day / dt）。供 /api/query source=supersonic"""
        model_id, _mid, _biz, _name = self.resolve_metric(DEFAULT_MODEL_TABLE, metric)
        return self.query_metric(model_id, _mid, "dt", start, end)


class DorisClient:
    """Doris 直连：归因分解 + 异常基线（MySQL 协议）。每线程独立连接(并发安全)"""

    def __init__(self):
        self._tls = threading.local()

    def _get(self):
        conn = getattr(self._tls, "conn", None)
        if conn is None or not conn.open:
            conn = pymysql.connect(host=DORIS_HOST, port=DORIS_PORT,
                                   user=DORIS_USER, password=DORIS_PASS,
                                   database=DORIS_DB, charset="utf8mb4",
                                   cursorclass=pymysql.cursors.DictCursor)
            self._tls.conn = conn
        return conn

    def query(self, sql: str) -> list:
        last = None
        for attempt in (1, 2):          # 空闲断连(TCP 被掐)自动重连重试一次
            try:
                with self._get().cursor() as cur:
                    cur.execute(sql)
                    return cur.fetchall()
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
                last = e
                self._tls.conn = None   # 丢弃死连,下轮 _get() 重建
        raise last

    def close(self):
        if self._conn and self._conn.open:
            self._conn.close()


def _metric_expr(metric: str, onto=None) -> str:
    onto = onto or ontology.get()
    return onto.ads_expr(metric)


def moving_baseline(doris: DorisClient, metric: str, end: date,
                    window: int = None, onto=None) -> dict:
    """异常基线：截至 end 前一天（不含 end）的滚动窗口日值"""
    onto = onto or ontology.get()
    window = window or onto.baseline_days
    sql = f"""
        SELECT {onto.date_col} AS d, {_metric_expr(metric, onto)} AS v
        FROM {onto.ads_table}
        WHERE {onto.date_col} >= '{end - timedelta(days=window)}'
          AND {onto.date_col} <  '{end}'
        GROUP BY {onto.date_col} ORDER BY {onto.date_col}
    """
    rows = doris.query(sql)
    vals = [float(r["v"]) for r in rows if r["v"] is not None]
    n = len(vals)
    if n < 3:
        return {"enough": False, "n": n}
    avg = sum(vals) / n
    std = math.sqrt(sum((v - avg) ** 2 for v in vals) / (n - 1))
    return {"enough": True, "n": n, "avg": avg, "std": std}


def zscore_check(current: float, baseline: dict, onto=None) -> dict:
    onto = onto or ontology.get()
    if not baseline.get("enough") or baseline.get("std", 0) == 0:
        return {"status": "normal", "z": None, "baseline": baseline}
    z = (current - baseline["avg"]) / baseline["std"]
    status = "critical" if abs(z) > onto.z_crit else ("warning" if abs(z) > onto.z_warn else "normal")
    return {"status": status, "z": round(z, 2),
            "current": round(current, 2),
            "baseline_avg": round(baseline["avg"], 2),
            "baseline_std": round(baseline["std"], 2), "n": baseline["n"]}


def ratio_compare(doris: DorisClient, metric: str, start: date, end: date,
                  onto=None) -> dict:
    """同环比：当期聚合 vs 前 1 期（roll）/ 前 7 期（over），区间长度自适应"""
    onto = onto or ontology.get()
    span = (end - start).days + 1
    agg = _metric_expr(metric, onto)
    table = onto.ads_table

    def agg_range(s: date, e: date) -> float:
        r = doris.query(f"SELECT {agg} AS v FROM {table} "
                        f"WHERE {onto.date_col} BETWEEN '{s}' AND '{e}'")
        return float(r[0]["v"] or 0) if r else 0.0

    cur = agg_range(start, end)
    roll = agg_range(start - timedelta(days=span), end - timedelta(days=span))
    over = agg_range(start - timedelta(days=7 * span), end - timedelta(days=7 * span))
    return {
        "current": cur, "span_days": span,
        "roll_base": roll, "roll_ratio": _pct(cur, roll),
        "over_base": over, "over_ratio": _pct(cur, over),
    }


def _pct(cur: float, base: float) -> float:
    if base == 0:
        return None
    return round((cur - base) / base * 100, 2)


def attribution(doris: DorisClient, metric: str, start: date, end: date,
                dims: list = None, top_n: int = 5, onto=None) -> dict:
    """归因分解（口径与 ads 层精确对齐）：

    基表 dwd_order_info_inc o + 已支付过滤(order_status='1001')，
    SUM(o.total_amount) == ads GMV（已对拍 74,931.47 == 74,931.47）。
    每个维度定义（ATTRIBUTION_DIM_DEFS）带自己的 JOIN 片段；sku 维度走
    明细分摊金额（d.split_total_amount）。各取值算 Δ(当期-基期同长区间)，
    contribution_i = Δ_i / Σ_j|Δ_j|（总 Δ≈0 时退化为符号占比）。
    """
    onto = onto or ontology.get()
    dims = dims or onto.attribution_dims
    span = (end - start).days + 1
    b_start, b_end = start - timedelta(days=span), end - timedelta(days=span)
    dcol = onto.dcol
    metric_key = metric.lower()
    col, kind_count_m = onto.metric_col(metric_key)
    default_agg = f"SUM({col})"

    result = []
    for dim in dims:
        dd = onto.dim_def(dim)
        if not dd:
            log.warning("未知归因维度 %s，跳过（可用: %s）", dim, list(onto.dims))
            continue
        inner = dd.get("amount_override", {}).get(metric_key, col)
        agg = f"COUNT(DISTINCT {col})" if kind_count_m else f"SUM({inner})"
        cur_cond = f"{dcol} BETWEEN '{start}' AND '{end}'"
        base_cond = f"{dcol} BETWEEN '{b_start}' AND '{b_end}'"
        sql = f"""
            SELECT {dd['expr']} AS dim_val,
                   COALESCE({_case_wrap(agg, cur_cond)}, 0) AS cur,
                   COALESCE({_case_wrap(agg, base_cond)}, 0) AS base
            FROM {onto.fact_table}
            {dd['join']}
            WHERE {dcol} BETWEEN '{b_start}' AND '{end}'
              AND {onto.paid_filter}
            GROUP BY {dd.get('group', dd['expr'])} ORDER BY ABS(cur - base) DESC
        """
        try:
            rows = doris.query(sql)
        except Exception as e:
            log.warning("归因维度 %s 失败: %s", dim, e)
            continue
        items = []
        for r in rows:
            if r["dim_val"] is None:
                continue
            cur, base = float(r["cur"] or 0), float(r["base"] or 0)
            items.append({"value": r["dim_val"], "current": cur,
                          "base": base, "delta": round(cur - base, 2)})
        total_delta = sum(i["delta"] for i in items)
        denom = sum(abs(i["delta"]) for i in items)
        for i in items:
            i["contribution_pct"] = round(i["delta"] / denom * 100, 2) if denom else (
                0.0 if total_delta == 0 else round(i["delta"] / abs(total_delta) * 100, 2))
        result.append({"dimension": dim,
                       "top_drivers": items[:top_n],
                       "total_delta": round(total_delta, 2)})

    result.sort(key=lambda d: max((abs(i["contribution_pct"]) for i in d["top_drivers"]), default=0),
                reverse=True)
    return {"base_range": [str(b_start), str(b_end)],
            "current_range": [str(start), str(end)],
            "table": onto.fact_table.split()[0] + "(+维表)",
            "caliber": f"{onto.domain} 口径: {onto.paid_filter}",
            "dimensions": result}


def drill_down(doris: DorisClient, dim: str, value: str, start: date, end: date,
               limit: int = 20, onto=None) -> dict:
    """行级下钻：归因定位到某维度取值后，看该群当期的订单明细。

    service_area/province 等走 dim_service_area/dim_base_province JOIN；
    sku 走明细分摊行。
    """
    onto = onto or ontology.get()
    dd = onto.dim_def(dim)
    if not dd:
        raise ValueError(f"未知维度 {dim}，可用: {list(onto.dims)}")
    safe_value = str(value).replace("'", "''")
    join = dd.get("join", "")
    if dim == "sku":
        sql = f"""
            SELECT o.id AS order_id, o.create_time, d.sku_name,
                   d.sku_num, d.split_total_amount AS amount, o.total_amount AS order_amount
            FROM {onto.fact_table}
            JOIN dwd_order_detail_inc d ON d.order_id = o.id
            WHERE {onto.dcol} BETWEEN '{start}' AND '{end}'
              AND {onto.paid_filter} AND d.sku_name = '{safe_value}'
            ORDER BY d.split_total_amount DESC LIMIT {limit}
        """
        note = "sku 行金额为订单内分摊口径"
    else:
        sql = f"""
            SELECT o.id AS order_id, o.create_time, o.total_amount AS amount,
                   {dd['expr']} AS dim_val
            FROM {onto.fact_table}
            {join}
            WHERE {onto.dcol} BETWEEN '{start}' AND '{end}'
              AND {onto.paid_filter} AND {dd['expr']} = '{safe_value}'
            ORDER BY o.total_amount DESC LIMIT {limit}
        """
        note = "订单级口径（total_amount 已支付）"
    rows = doris.query(sql)
    out = []
    for r in rows:
        d = dict(r)
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = str(v)
            elif hasattr(v, "quantize"):
                d[k] = float(v)
        out.append(d)
    # 总额口径与归因一致：sku 用分摊金额列（JOIN 重复行不能用 o.total_amount）
    total_col = dd.get("amount_override", {}).get("gmv", "o.total_amount")
    total = doris.query(f"""
        SELECT COALESCE(SUM({total_col}),0) v FROM {onto.fact_table} {join}
        WHERE {onto.dcol} BETWEEN '{start}' AND '{end}'
          AND {onto.paid_filter} AND {dd['expr']} = '{safe_value}'""")[0]["v"]
    return {"dimension": dim, "value": value, "range": [str(start), str(end)],
            "note": note, "total": float(total or 0),
            "orders": out[:limit], "shown": len(out)}


def _case_wrap(agg: str, cond: str) -> str:
    import re
    m = re.match(r"COUNT\s*\(\s*DISTINCT\s+(.+?)\s*\)", agg, re.I)
    if m:
        return f"COUNT(DISTINCT CASE WHEN {cond} THEN {m.group(1)} END)"
    m = re.match(r"(\w+)\s*\(\s*(.+?)\s*\)", agg, re.I)
    if m:
        return f"{m.group(1)}(CASE WHEN {cond} THEN {m.group(2)} ELSE 0 END)"
    return agg


def _existing_columns(doris: DorisClient, table: str) -> set:
    rows = doris.query(
        "SELECT COLUMN_NAME FROM information_schema.columns "
        f"WHERE TABLE_SCHEMA='{DORIS_DB}' AND TABLE_NAME='{table}'")
    return {r["COLUMN_NAME"] for r in rows}


def aggregate(doris: DorisClient, metric: str, start: date, end: date,
              dim: str = None, value_filter: str = None,
              topn: int = 0, having_min: float = None,
              order: str = "DESC", extra_filter: str = None,
              extra_join: str = None, onto=None) -> dict:
    """聚合分析(口径与 ads GMV 精确对齐):

    metric × [维度分组] × 日期区间 → 汇总/分组/TopN/HAVING。
    dim 为空时返回区间总量;dim 取 ATTRIBUTION_DIM_DEFS(带维表 JOIN)。
    """
    onto = onto or ontology.get()
    m = metric.lower()
    col, kind_count = onto.metric_col(m)
    dd = onto.dim_def(dim) if dim else None
    join = dd["join"] if dd else ""
    expr_col = dd.get("amount_override", {}).get(m, col) if dd else col
    agg_expr = (f"COUNT(DISTINCT {col})" if kind_count
                else f"SUM({expr_col})" if dd and "amount_override" in dd
                else f"SUM({col})")
    paid = onto.paid_filter
    extra = f" AND {extra_filter}" if extra_filter else ""
    vfilter = ""
    if value_filter and dd:
        safe = str(value_filter).replace("'", "''")
        vfilter = f" AND {dd['expr']} = '{safe}'"

    if not dim:
        r = doris.query(
            f"SELECT COALESCE({agg_expr},0) v FROM {onto.fact_table} {extra_join or ''} "
            f"WHERE {onto.dcol} BETWEEN '{start}' AND '{end}' AND {paid}{extra}")
        v = float(r[0]["v"] or 0)
        return {"metric": metric, "range": [str(start), str(end)],
                "value": v, "total": v}

    having = f" HAVING v >= {having_min}" if having_min is not None else ""
    order_dir = "ASC" if str(order).upper() == "ASC" else "DESC"
    sql = f"""
        SELECT {dd['expr']} AS dim_val, COALESCE({agg_expr},0) AS v
        FROM {onto.fact_table} {join}
        WHERE {onto.dcol} BETWEEN '{start}' AND '{end}'
          AND {paid}{vfilter}{extra}
        GROUP BY {dd.get('group', dd['expr'])}
        {having} ORDER BY v {order_dir}
    """
    rows = doris.query(sql)
    items = [{"value": r["dim_val"], "v": float(r["v"] or 0)} for r in rows]
    total = sum(i["v"] for i in items)
    if topn:
        items = items[:topn]
    return {"metric": metric, "dimension": dim,
            "range": [str(start), str(end)],
            "total": round(total, 2), "rows": len(items),
            "items": items}
