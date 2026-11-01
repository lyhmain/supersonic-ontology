"""旁挂决策服务 API：问数(取数+同环比) / 分析(异常+归因+洞察)

架构（方案 A）：
    SuperSonic = 纯取数引擎（/api/semantic/query/metric，确定性无 LLM）
    本服务     = 分析决策尾部（意图门控 → 同环比/异常/归因 → GLM 洞察）
    Doris      = 归因分解与异常基线的直连计算通道

端点：
    POST /api/analyze   全链路分析（门控自动决定走全量还是快车道）
    POST /api/query     纯取数+同环比（不触发 LLM/归因）
    GET  /api/health    健康检查（SuperSonic/Doris 连通性）
"""
import logging
import os
import functools
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from config import (ATTRIBUTION_DEFAULT_DIMS, ATTRIBUTION_DIM_DEFS,
                    DEFAULT_DATE_COL, DEFAULT_METRIC, DEFAULT_METRIC_NAME,
                    DEFAULT_MODEL_TABLE,
                    DORIS_DB)
import engine
import json
import re
from typing import Optional
import ontology
import spec as spec_mod
import jev as jev_mod
from fastapi.responses import HTMLResponse, FileResponse

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
_PROFILE_DIR = ontology.PROFILES_DIR
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")
_VERSION = "0.9"
import insight as insight_mod
import nl

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
log = logging.getLogger("decision.api")

app = FastAPI(title="旁挂决策服务", version="0.1.0")
sonic = engine.SupersonicClient()
doris = engine.DorisClient()


class AnalyzeReq(BaseModel):
    question: str = ""
    start: str = Field(..., description="当期开始日 YYYY-MM-DD")
    end: str = Field(..., description="当期结束日 YYYY-MM-DD")
    metric: str = Field(default=DEFAULT_METRIC, description="指标: gmv/order_cnt/列名")
    metric_name: str = Field(default=DEFAULT_METRIC_NAME)
    model_table: str = Field(default=DEFAULT_MODEL_TABLE)
    dims: list = Field(default_factory=list,
                       description=f"归因维度（空=默认 {ATTRIBUTION_DEFAULT_DIMS}，可用: {list(ATTRIBUTION_DIM_DEFS)}）")
    with_insight: bool = True
    policy: str = Field(default="facts_first",
                        description="facts_first/action_first/risk_focus/executive")
    profile: Optional[str] = Field(default=None, description="业务本体 profile(空=默认)")
    dimension: str = Field(default="", description="取值过滤的维度(配合 value_filter)")
    value_filter: str = Field(default="", description="维度取值过滤,如 县市36服务区(空=不过滤)")


import badcase
import kb


@app.get("/api/health")
def health():
    out = {"supersonic": False, "doris": False}
    try:
        sonic._login()
        out["supersonic"] = True
    except Exception as e:
        out["supersonic_err"] = repr(e)[:120]
    try:
        doris.query("SELECT 1")
        out["doris"] = True
    except Exception as e:
        out["doris_err"] = repr(e)[:120]
    out["kb"] = kb.health()
    return out


# ----------------------------- 配置控制台 (/ui) ----------------------------- #

@app.get("/ui", response_class=HTMLResponse)
def ui_page():
    with open(os.path.join(_UI_DIR, "ui.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/echarts.min.js")
def echarts_js():
    return FileResponse(os.path.join(_UI_DIR, "echarts.min.js"),
                        media_type="application/javascript")


@app.get("/dash", response_class=HTMLResponse)
def dash_page():
    with open(os.path.join(_UI_DIR, "dash.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/ui/health")
def ui_health():
    return {"version": _VERSION, "profiles": ontology.all_profiles(),
            "jev_active": jev_mod.load().get("active")}


@app.get("/api/ui/profiles")
def ui_profiles():
    out = []
    for name in ontology.all_profiles():
        try:
            o = ontology.get(name)
            out.append({"name": name, "domain": o.domain, "ok": True, "err": "",
                        "metrics": len(o.metrics), "dimensions": len(o.dims)})
        except Exception as e:
            out.append({"name": name, "domain": "", "ok": False,
                        "err": repr(e)[:120], "metrics": 0, "dimensions": 0})
    return {"profiles": out, "default": ontology.DEFAULT_PROFILE}


def _validate_profile(cfg: dict) -> ontology.Ontology:
    if not (cfg or {}).get("metrics"):
        raise ValueError("metrics 不能为空")
    if not (cfg.get("fact") or {}).get("table"):
        raise ValueError("fact.table 不能为空")
    o = ontology.Ontology(cfg)          # 编译即校验
    if not o.metrics:
        raise ValueError("metrics 不能为空")
    return o


@app.get("/api/ui/profile/{name}")
def ui_profile_get(name: str):
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "非法 profile 名")
    path = os.path.join(_PROFILE_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "profile 不存在")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    out = {"raw": cfg, "profile": name, "domain": "", "metrics": {}, "dimensions": {}, "ok": False}
    try:
        o = ontology.get(name)
        out.update({"ok": True, "domain": o.domain,
                    "metrics": {k: v.get("name", k) for k, v in o.metrics.items()},
                    "dimensions": {k: v.get("name", k) for k, v in o.dims.items()}})
    except Exception:
        pass
    return out


@app.put("/api/ui/profile/{name}")
def ui_profile_put(name: str, body: dict = None):
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "非法 profile 名(仅字母数字_-)")
    cfg = body or {}
    cfg["profile"] = name
    try:
        o = _validate_profile(cfg)
    except Exception as e:
        raise HTTPException(400, f"本体校验失败: {e}")
    os.makedirs(_PROFILE_DIR, exist_ok=True)
    path = os.path.join(_PROFILE_DIR, f"{name}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    ontology.reload(name)
    log.info("UI 保存本体 %s: 指标%s 维度%s", name, list(o.metrics), list(o.dims))
    return {"ok": True, "metrics": len(o.metrics), "dimensions": len(o.dims)}


@app.delete("/api/ui/profile/{name}")
def ui_profile_del(name: str):
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "非法 profile 名")
    if name == ontology.DEFAULT_PROFILE:
        raise HTTPException(400, "默认 profile 不可删除(可先改 ONTOLOGY_PROFILE 环境变量)")
    path = os.path.join(_PROFILE_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "profile 不存在")
    os.remove(path)
    ontology.reload(name, drop=True)
    return {"ok": True}


@app.post("/api/ui/try")
def ui_try(body: dict):
    q = (body or {}).get("question") or ""
    if not q:
        raise HTTPException(400, "question 不能为空")
    return nl_query({"question": q, "profile": (body or {}).get("profile")})


# ----------------------------- Jev 后端管理 ----------------------------- #

@app.get("/api/ui/jev")
def ui_jev_get():
    return jev_mod.masked(jev_mod.load())


@app.put("/api/ui/jev")
def ui_jev_put(body: dict):
    cfg = jev_mod.load()
    for pid, incoming in ((body or {}).get("providers") or {}).items():
        cur = cfg["providers"].get(pid)
        if cur is None:
            continue
        for k in ("base_url", "model", "api_key"):
            v = (incoming.get(k) or "").strip()
            if v:                        # 留空 = 保持原值
                cur[k] = v
    jev_mod.save(cfg)
    return {"ok": True}


@app.post("/api/ui/jev/activate")
def ui_jev_activate(body: dict):
    pid = (body or {}).get("pid") or ""
    cfg = jev_mod.load()
    if pid not in cfg["providers"]:
        raise HTTPException(404, f"未知后端 {pid}")
    cfg["active"] = pid
    jev_mod.save(cfg)
    return {"ok": True, "active": pid, "label": cfg["providers"][pid].get("label", pid)}


@app.post("/api/ui/jev/test")
def ui_jev_test(body: dict):
    return jev_mod.test_connect((body or {}).get("pid") or "")


# ----------------------------- 接入向导 / 模板库 / 试问集验收 ----------------------------- #

@app.get("/api/ui/doris/tables")
def ui_doris_tables():
    rows = doris.query(
        "SELECT TABLE_NAME, TABLE_COMMENT FROM information_schema.tables "
        f"WHERE TABLE_SCHEMA='{DORIS_DB}' ORDER BY TABLE_NAME")
    return {"tables": [{"name": r["TABLE_NAME"], "comment": r.get("TABLE_COMMENT") or ""}
                       for r in rows]}


@app.get("/api/ui/doris/columns")
def ui_doris_columns(table: str):
    if not re.match(r"^[A-Za-z0-9_]+$", table or ""):
        raise HTTPException(400, "非法表名")
    rows = doris.query(
        "SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT FROM information_schema.columns "
        f"WHERE TABLE_SCHEMA='{DORIS_DB}' AND TABLE_NAME='{table}' ORDER BY ORDINAL_POSITION")
    return {"table": table, "columns": [
        {"name": r["COLUMN_NAME"], "type": r["DATA_TYPE"],
         "comment": r.get("COLUMN_COMMENT") or ""} for r in rows]}


_NUMERIC = {"decimal", "int", "bigint", "float", "double", "largeint"}
_SKIP_COL = re.compile(r"^(id$|.*_id$|is_)", re.I)


def _fk_for(fact_cols: list, dim_table: str):
    """推断维表外键:维表核心词匹配 fact 的 *_id 列"""
    full = re.sub(r"^(dim_|dwd_|ods_|ads_)", "", dim_table).lower()
    core = full.split("_")[-1].rstrip("s")
    for c in fact_cols:
        if c.endswith("_id"):
            stem = c[:-3].lower()
            if stem in full or full.endswith(stem) or core == stem:
                return c
    return None


def draft_from_table(table: str, dim_tables: list = None) -> dict:
    """扫表自动生成本体草稿:日期列/数值列→指标/字符串列→维度,注释做词表;
    dim_tables=[维表名…] 自动推断 FK 并把维表字符串列推导为 JOIN 维度(星型模型)"""
    cols = ui_doris_columns(table)["columns"]
    date_col = next((c["name"] for c in cols if c["type"] in ("date", "datetime", "timestamp")), None)
    if not date_col:
        raise HTTPException(400, "未找到日期列(DATE/DATETIME)")
    metrics, dims = {}, {}
    for c in cols:
        name, typ, cmt = c["name"], c["type"], c["comment"]
        label = cmt or name
        if typ in _NUMERIC and not _SKIP_COL.match(name):
            key = name.lower()
            metrics[key] = {"name": label, "words": list({label, name}), "col": name}
        elif typ in ("varchar", "char", "string") and not _SKIP_COL.match(name):
            key = name.lower()
            dims[key] = {"name": label, "words": list({label, name}),
                         "expr": f"t.{name}", "join": ""}
    joins = []
    for i, dt_ in enumerate([t for t in (dim_tables or []) if t and t != table]):
        dcols = ui_doris_columns(dt_)["columns"]
        fk = _fk_for([c["name"] for c in cols], dt_)
        if not fk:
            continue
        alias = f"d{i}"
        joins.append(f"LEFT JOIN {dt_} {alias} ON t.{fk} = {alias}.id")
        for c in dcols:
            name, typ, cmt = c["name"], c["type"], c["comment"]
            if typ in ("varchar", "char", "string") and not _SKIP_COL.match(name) and name != "id":
                key = f"{dt_.split('_')[-1]}_{name}".lower()
                dims[key] = {"name": cmt or name, "words": list({cmt or name, name}),
                             "expr": f"{alias}.{name}", "join": " ".join(joins)}
    if not metrics:
        raise HTTPException(400, "未找到数值指标列")
    m0 = next(iter(metrics))
    d0 = next(iter(dims), None)
    accept = [{"q": f"昨天{metrics[m0]['name']}是多少", "action": "aggregate"}]
    if d0:
        accept.append({"q": f"近30天各{dims[d0]['name']}{metrics[m0]['name']}", "action": "aggregate", "rows_at_least": 1})
    return {
        "profile": table.lower(), "domain": f"{table}(自动推导草稿,请人工校对口径)",
        "default_metric": m0,
        "fact": {"table": f"{table} t", "date_col": date_col, "paid_filter": "1=1"},
        "ads": {"table": table, "metric_exprs": {k: f"SUM(COALESCE({v['col']},0))" for k, v in metrics.items()}},
        "metrics": metrics, "dimensions": dims,
        "value_tokens": [], "holidays": {}, "intent": {},
        "attribution_default_dims": list(dims)[:3],
        "anomaly": {"z_warn": 2.0, "z_crit": 3.0, "baseline_days": 28},
        "acceptance": accept,
    }


@app.post("/api/ui/profile/draft")
def ui_profile_draft(body: dict):
    b = body or {}
    return draft_from_table(b.get("table") or "", b.get("dim_tables") or [])


def _run_acceptance(cfg: dict) -> list:
    """对(未落盘的)profile 配置跑试问集;临时注册,跑完移除"""
    name = cfg.get("profile")
    if not name:
        raise HTTPException(400, "缺少 profile")
    try:
        _validate_profile(cfg)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"本体校验失败: {e}")
    ontology._profiles[name] = ontology.Ontology(cfg)
    out = []
    try:
        for a in (cfg.get("acceptance") or []):
            q = a.get("q") or ""
            rec = {"q": q, "pass": False, "got": {}}
            try:
                r = nl_query({"question": q, "profile": name})
                checks = []
                if "action" in a:
                    checks.append(r.get("action") == a["action"])
                if "metric" in a:
                    checks.append((r.get("parsed") or {}).get("metric") == a["metric"])
                if a.get("metric_matched"):
                    checks.append((r.get("parsed") or {}).get("metric_matched") is True)
                if "value" in a:
                    v = r.get("value")
                    if v is None and r.get("items"):
                        v = r["items"][0].get("v")
                    checks.append(v is not None and abs(float(v) - float(a["value"])) < 0.01)
                if "rows" in a:
                    checks.append(r.get("rows") == a["rows"])
                if "rows_at_least" in a:
                    checks.append((r.get("rows") or 0) >= a["rows_at_least"])
                if "contains" in a:
                    blob = json.dumps(r, ensure_ascii=False, default=str)
                    checks.append(str(a["contains"]) in blob)
                rec["pass"] = all(checks) if checks else False
                rec["got"] = {k: r.get(k) for k in ("action", "value", "rows", "total") if k in r}
            except Exception as e:
                rec["got"] = {"error": repr(e)[:120]}
            out.append(rec)
    finally:
        ontology._profiles.pop(name, None)
    return out


@app.post("/api/ui/acceptance")
def ui_acceptance(body: dict):
    """试问集验收(草稿即可跑,不必先保存)"""
    return {"results": _run_acceptance(body or {})}


@app.get("/api/ui/report/{name}")
def ui_report(name: str):
    """接入验收报告:已保存 profile 跑全部试问集"""
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "非法 profile 名")
    path = os.path.join(_PROFILE_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "profile 不存在")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    results = _run_acceptance(cfg)
    n_pass = sum(1 for r in results if r["pass"])
    return {"profile": name, "cases": len(results), "passed": n_pass,
            "pass_rate": round(n_pass / len(results) * 100, 1) if results else None,
            "results": results}


TEMPLATES = {
    "retail_chain": {
        "profile": "my_store", "domain": "连锁门店零售(模板,请改成你的表和口径)",
        "default_metric": "revenue",
        "fact": {"table": "dwd_pos_orders t", "date_col": "dt", "paid_filter": "t.is_refund = 0"},
        "ads": {"table": "dwd_pos_orders",
                "metric_exprs": {"revenue": "SUM(amount)"}},
        "metrics": {
            "revenue": {"name": "营业额", "words": ["营业额", "流水", "销售额"], "col": "amount"},
            "orders":  {"name": "单数", "words": ["单数", "订单数", "小票数"], "count_col": "id"},
            "atv":     {"name": "客单价", "words": ["客单价"], "derive": {"num": "revenue", "den": "orders"}}},
        "dimensions": {
            "store": {"name": "门店", "words": ["门店"], "expr": "t.store_name", "join": ""},
            "city":  {"name": "城市", "words": ["城市"], "expr": "t.city", "join": ""},
            "region": {"name": "区域", "words": ["区域", "大区"], "expr": "t.region", "join": ""}},
        "value_tokens": [], "holidays": {},
        "intent": {}, "attribution_default_dims": ["store", "city"],
        "anomaly": {"z_warn": 2.0, "z_crit": 3.0, "baseline_days": 28},
        "acceptance": [{"q": "昨天营业额是多少", "action": "aggregate"},
                       {"q": "近30天各门店营业额", "action": "aggregate"}]},
    "logistics": {
        "profile": "my_logistics", "domain": "物流运输(模板,请改成你的表和口径)",
        "default_metric": "freight",
        "fact": {"table": "dwd_waybill t", "date_col": "dt", "paid_filter": "t.status = '已完成'"},
        "ads": {"table": "dwd_waybill",
                "metric_exprs": {"freight": "SUM(fee)", "tickets": "COUNT(DISTINCT waybill_no)"}},
        "metrics": {
            "freight": {"name": "运费", "words": ["运费", "运输收入"], "col": "fee"},
            "tickets": {"name": "票数", "words": ["票数", "运单数"], "count_col": "id"}},
        "dimensions": {
            "line":  {"name": "线路", "words": ["线路"], "expr": "t.line_name", "join": ""},
            "fleet": {"name": "车队", "words": ["车队"], "expr": "t.fleet_name", "join": ""}},
        "value_tokens": [], "holidays": {},
        "intent": {}, "attribution_default_dims": ["line", "fleet"],
        "anomaly": {"z_warn": 2.0, "z_crit": 3.0, "baseline_days": 28},
        "acceptance": [{"q": "昨天运费是多少", "action": "aggregate"}]},
}


# ----------------------------- 规范配置(面向普通数据开发) ----------------------------- #

@app.get("/api/ui/spec/template")
def ui_spec_template():
    return spec_mod.SPEC_TEMPLATE


@app.post("/api/ui/spec/compile")
def ui_spec_compile(body: dict):
    """规范配置 → 完整 profile(校验失败返回 400 + 中文原因)"""
    try:
        return {"ok": True, "profile": spec_mod.compile_spec(body or {})}
    except spec_mod.SpecError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"规范配置编译失败: {repr(e)[:160]}")


@app.post("/api/ui/spec/save")
def ui_spec_save(body: dict):
    """规范配置 → 编译 → 校验 → 保存上线(一步到位)"""
    b = body or {}
    try:
        profile = spec_mod.compile_spec(b.get("spec") or {})
    except spec_mod.SpecError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"规范配置编译失败: {repr(e)[:160]}")
    name = profile["profile"]
    st_d = ui_profile_put(name, profile)
    results = _run_acceptance(profile)
    n_pass = sum(1 for r in results if r["pass"])
    return {"ok": True, "profile": name, "metrics": st_d.get("metrics"),
            "dimensions": st_d.get("dimensions"),
            "acceptance": {"cases": len(results), "passed": n_pass,
                           "pass_rate": round(n_pass / len(results) * 100, 1) if results else None,
                           "results": results}}


@app.get("/api/ui/templates")
def ui_templates():
    return {"templates": {k: v["domain"] for k, v in TEMPLATES.items()}}


@app.get("/api/ui/template/{key}")
def ui_template_get(key: str):
    if key not in TEMPLATES:
        raise HTTPException(404, "未知模板")
    return TEMPLATES[key]


@app.post("/api/query")
def query(req: AnalyzeReq, source: str = "doris"):
    """快车道：取数 + 同环比，无 LLM。source=doris(默认)|supersonic(语义层对拍)"""
    s, e = date.fromisoformat(req.start), date.fromisoformat(req.end)
    onto = ontology.get(req.profile) if req.profile else None
    ads_table = onto.ads_table if onto else req.model_table
    dcol = onto.date_col if onto else "dt"
    drv = (onto or ontology.get()).derive(req.metric)
    vf, dimk = (req.value_filter or "").strip(), (req.dimension or "").strip()
    dd = (onto or ontology.get()).dim_def(dimk) if (vf and dimk) else None
    if vf and dimk and not dd:
        raise HTTPException(400, f"未知维度 {dimk}")
    if source == "supersonic":
        try:
            detail = [{"date": r.get("dt") or r.get("date"), "value": r["value"]}
                      for r in sonic.daily_series(req.metric, req.start, req.end)]
        except Exception as ex:
            raise HTTPException(502, f"SuperSonic 语义取数失败: {ex}")
    elif dd:
        # 维度取值过滤:走事实表口径(维度 JOIN 只对事实表有效),与归因同款
        o_ = onto or ontology.get()

        def _daily_fact(m):
            col, is_cnt = o_.metric_col(m)
            agg = f"COUNT(DISTINCT {col})" if is_cnt else f"SUM({col})"
            return doris.query(
                f"SELECT {o_.dcol} AS d, {agg} AS v "
                f"FROM {o_.fact_table} {dd['join']} "
                f"WHERE {o_.dcol} BETWEEN '{req.start}' AND '{req.end}' "
                f"AND {o_.paid_filter} AND {dd['expr']} = '{vf}' "
                f"GROUP BY {o_.dcol} ORDER BY {o_.dcol}")

        if drv:
            den_map = {r["d"]: float(r["v"] or 0) for r in _daily_fact(drv["den"])}
            detail = []
            for r in _daily_fact(drv["num"]):
                den = den_map.get(r["d"])
                if den:
                    detail.append({"date": str(r["d"]),
                                   "value": round(float(r["v"] or 0) / den, 2)})
            return {"metric": req.metric_name, "source": source,
                    "detail": detail, "ratio": None}
        rows = _daily_fact(req.metric)
        detail = [{"date": str(r["d"]), "value": float(r["v"] or 0)} for r in rows]
        return {"metric": req.metric_name, "source": source,
                "detail": detail, "ratio": None}
    elif drv:
        def _daily(m):
            return doris.query(
                f"SELECT {dcol} AS d, {engine._metric_expr(m, onto)} AS v "
                f"FROM {ads_table} WHERE {dcol} BETWEEN '{req.start}' AND '{req.end}' "
                f"GROUP BY {dcol} ORDER BY {dcol}")
        den_map = {r["d"]: float(r["v"] or 0) for r in _daily(drv["den"])}
        detail = []
        for r in _daily(drv["num"]):
            den = den_map.get(r["d"])
            if den:
                detail.append({"date": str(r["d"]),
                               "value": round(float(r["v"] or 0) / den, 2)})
        return {"metric": req.metric_name, "source": source,
                "detail": detail, "ratio": None}
    else:
        rows = doris.query(
            f"SELECT {dcol} AS d, {engine._metric_expr(req.metric, onto)} AS v "
            f"FROM {ads_table} WHERE {dcol} BETWEEN '{req.start}' AND '{req.end}' "
            f"GROUP BY {dcol} ORDER BY {dcol}")
        detail = [{"date": str(r["d"]), "value": float(r["v"] or 0)} for r in rows]
    return {
        "metric": req.metric_name,
        "source": source,
        "detail": detail,
        "ratio": engine.ratio_compare(doris, req.metric, s, e, onto),
    }


class DrillReq(BaseModel):
    dimension: str = Field(..., description="归因维度（可用值见 profile 维度词典）")
    value: str = Field(..., description="维度取值（归因 top_drivers 里的 value）")
    start: str
    end: str
    limit: int = 20
    profile: Optional[str] = Field(default=None, description="业务本体 profile(空=默认)")


@app.post("/api/drill")
def drill(req: DrillReq):
    """行级下钻：归因定位维度取值后，查看该群当期订单明细"""
    s, e = date.fromisoformat(req.start), date.fromisoformat(req.end)
    onto = ontology.get(req.profile) if req.profile else None
    try:
        return engine.drill_down(doris, req.dimension, req.value, s, e, req.limit, onto)
    except ValueError as ex:
        raise HTTPException(400, str(ex))




class AggregateReq(BaseModel):
    question: str = ""
    metric: str = Field(default=DEFAULT_METRIC)
    dimension: str = Field(default="", description=f"分组维度(空=区间总量): {list(ATTRIBUTION_DIM_DEFS)}")
    value_filter: str = Field(default="", description="维度取值过滤,如 县市01服务区")
    start: str
    end: str
    topn: int = Field(default=0, description=">0 时返回前 N")
    having_min: float = Field(default=None, description="HAVING 阈值")
    order: str = "DESC"
    profile: Optional[str] = Field(default=None, description="业务本体 profile(空=默认)")


@app.post("/api/aggregate")
def aggregate(req: AggregateReq):
    """聚合分析:总量/维度分组/TopN/HAVING(口径与 ads 层精确对齐)"""
    s, e = date.fromisoformat(req.start), date.fromisoformat(req.end)
    onto = ontology.get(req.profile) if req.profile else None
    drv = (onto or ontology.get()).derive(req.metric)
    if drv:   # 派生指标(num/den 分别聚合后按组相除)
        g = engine.aggregate(doris, drv["num"], s, e, req.dimension or None,
                             req.value_filter or None, onto=onto)
        c = engine.aggregate(doris, drv["den"], s, e, req.dimension or None,
                             req.value_filter or None, onto=onto)
        if not req.dimension:
            v = round(g["value"] / c["value"], 2) if c.get("value") else None
            return {"metric": req.metric, "dimension": None,
                    "range": [req.start, req.end], "total": v, "rows": 1,
                    "value": v, "items": []}
        gmap = {i["value"]: i["v"] for i in g.get("items", [])}
        cmap = {i["value"]: i["v"] for i in c.get("items", [])}
        items = [{"value": k, "v": round(gmap.get(k, 0) / cmap[k], 2) if cmap.get(k) else None}
                 for k in gmap]
        items = sorted([i for i in items if i["v"] is not None],
                       key=lambda x: x["v"], reverse=(req.order == "DESC"))
        return {"metric": req.metric, "dimension": req.dimension,
                "range": [req.start, req.end],
                "total": round(sum(i["v"] for i in items), 2),
                "rows": len(items), "items": items[: req.topn or None]}
    try:
        return engine.aggregate(doris, req.metric, s, e,
                                req.dimension or None,
                                req.value_filter or None,
                                req.topn, req.having_min, req.order, onto=onto)
    except Exception as ex:
        raise HTTPException(400, repr(ex)[:200])




def _route_ctx(profile: str = None):
    """路由/解析上下文:显式 profile > 默认 profile > 环境变量兜底表"""
    onto = ontology.get(profile) if profile else None
    if onto is None:
        try:
            onto = ontology.get()
        except Exception:
            onto = None
    ads_table = onto.ads_table if onto else DEFAULT_MODEL_TABLE
    dcol = onto.date_col if onto else DEFAULT_DATE_COL
    last = doris.query(f"SELECT MAX({dcol}) m FROM {ads_table}")[0]["m"]
    max_d = last if isinstance(last, date) else date.fromisoformat(str(last))
    return onto, max_d


@app.post("/api/nl")
@badcase.watched("api")
def nl_query(body: dict):
    """自然语言分析入口:NL 解析 → 总量/分组/TopN/HAVING/同环比/归因洞察"""
    question = (body.get("question") or body.get("queryText") or "").strip()
    if not question:
        raise HTTPException(400, "question 不能为空")
    onto, max_d = _route_ctx(body.get("profile"))
    p = nl.parse(question, max_d, onto)
    s, e = date.fromisoformat(p["start"]), date.fromisoformat(p["end"])
    out = {"question": question, "profile": onto.profile if onto else ontology.DEFAULT_PROFILE,
           "parsed": p}

    def _total(metric):
        r = doris.query(
            f"SELECT COALESCE({engine._metric_expr(metric, onto)},0) v FROM {ads_table} "
            f"WHERE {dcol} BETWEEN '{s}' AND '{e}'")
        return float(r[0]["v"] or 0)

    if p["action"] == "series":
        rows = doris.query(
            f"SELECT {dcol} d, {engine._metric_expr(p['metric'], onto)} v "
            f"FROM {ads_table} "
            f"WHERE {dcol} BETWEEN '{s}' AND '{e}' GROUP BY d ORDER BY d")
        return {**out, "action": "series",
                "series": [{"date": str(r["d"]), "v": float(r["v"] or 0)} for r in rows]}

    extra = None
    extra_join = None
    vfilter = p.get("value_filter")
    point = None
    if isinstance(vfilter, tuple):
        attr, val = vfilter
        dd_attr = (onto or ontology.get()).dim_def(attr)
        if dd_attr is None:
            vfilter = None
        elif not p.get("dim"):
            extra = f"{dd_attr['expr']} = '{val}'"
            extra_join = dd_attr["join"]
            vfilter = None
            if attr == "service_area" or dd_attr["expr"].endswith(".name"):
                point = val
        elif attr == p["dim"]:
            vfilter = val
        else:
            extra = f"{dd_attr['expr']} = '{val}'"
            vfilter = None

    if (p["action"] == "aggregate" and not p.get("dim")
            and len(p.get("metrics") or []) > 1):
        vals = [_total(m) for m in p["metrics"]]
        names = {(onto or ontology.get()).metric_name(m): round(v, 2)
                 for m, v in zip(p["metrics"], vals)}
        return {**out, "action": "aggregate",
                "metrics": p["metrics"], "multi": [round(v, 2) for v in vals],
                "multi_names": names}

    if p["action"] == "ratio":
        base_now = _total(p["metric"])
        span = (e - s).days + 1
        r_roll = doris.query(
            f"SELECT COALESCE({engine._metric_expr(p['metric'], onto)},0) v FROM {ads_table} "
            f"WHERE {dcol} BETWEEN '{s - timedelta(days=span)}' AND '{e - timedelta(days=span)}'")
        r_over = doris.query(
            f"SELECT COALESCE({engine._metric_expr(p['metric'], onto)},0) v FROM {ads_table} "
            f"WHERE {dcol} BETWEEN '{s - timedelta(days=7*span)}' AND '{e - timedelta(days=7*span)}'")
        base = p.get("base")
        if base:
            bs, be = base
            r_base = doris.query(
                f"SELECT COALESCE({engine._metric_expr(p['metric'], onto)},0) v FROM {ads_table} "
                f"WHERE {dcol} BETWEEN '{bs}' AND '{be}'")
            base_now2 = float(r_base[0]["v"] or 0)
            out.update({"base_range": [str(bs), str(be)], "base_value": base_now2})
            out["yoy_pct"] = round((base_now - base_now2) / base_now2 * 100, 2) if base_now2 else None
            return {**out, "action": "ratio", "current": base_now}
        roll, over = float(r_roll[0]["v"] or 0), float(r_over[0]["v"] or 0)
        out.update({"action": "ratio", "current": base_now, "span_days": span,
                    "roll_base": roll,
                    "roll_pct": round((base_now - roll) / roll * 100, 2) if roll else None,
                    "over_base": over,
                    "over_pct": round((base_now - over) / over * 100, 2) if over else None})
        return out

    if p["action"] == "analyze":
        res = analyze(AnalyzeReq(question=question, start=p["start"], end=p["end"],
                                 metric=p["metric"], policy="facts_first",
                                 profile=body.get("profile")))
        res["parsed"] = p          # 路由响应一致性:全路由带解析参数
        return res

    # aggregate
    drv = (onto or ontology.get()).derive(p["metric"])
    if drv:
        g = engine.aggregate(doris, drv["num"], s, e, p["dim"], p.get("value_filter"), onto=onto)
        c = engine.aggregate(doris, drv["den"], s, e, p["dim"], p.get("value_filter"), onto=onto)
        gmap = {i["value"]: i["v"] for i in g.get("items", [])} or (
            {"__total__": g["value"]} if "value" in g else {})
        cmap = {i["value"]: i["v"] for i in c.get("items", [])} or (
            {"__total__": c["value"]} if "value" in c else {})
        drv_name = (onto or ontology.get()).metric_name(p["metric"])
        if not p.get("dim"):
            v = round(g["value"] / c["value"], 2) if c["value"] else None
            return {**out, "action": "aggregate", "metric_name": drv_name, "value": v}
        items = [{"value": k, "v": round(gmap.get(k, 0) / cmap[k], 2) if cmap.get(k) else None}
                 for k in gmap]
        items = [i for i in items if i["v"] is not None]
        items.sort(key=lambda x: x["v"], reverse=(p["order"] == "DESC"))
        return {**out, "action": "aggregate", "metric_name": drv_name,
                "rows": len(items), "total": round(sum(i["v"] for i in items), 2),
                "items": items[: p["topn"] or None]}

    r = engine.aggregate(doris, p["metric"], s, e, p["dim"], vfilter,
                         p["topn"], p["having_min"], p["order"], extra, extra_join, onto)
    if point is not None:
        return {**out, "action": "aggregate",
                "items": [{"value": point, "v": float(r.get("value", 0))}]}
    r["metric_name"] = p["metric_name"]
    return {**out, "action": "aggregate", **{k: r[k] for k in
            ("metric", "dimension", "range", "total", "rows", "items", "value") if k in r}}


# --------------------------------------------------------------------------- #
# SuperSonic 插件适配端点（方案 B 统一入口）
#
# SuperSonic WebServiceQuery 调用约定：
#   POST <url>?queryText=<用户问题>   body = paramOptions 的 {key: value}
#   响应形状：{columns: [...], resultList: [...], data: "<文本结果>"}
# --------------------------------------------------------------------------- #

_dash_cache = {"ts": 0.0, "date": None}


def _latest_date() -> str:
    """数仓最新分区日期(60s 缓存)——按默认 profile 的 ads 表口径,「昨日」等时间词实际锚点"""
    import time as _t
    if _t.time() - _dash_cache["ts"] < 60 and _dash_cache["date"]:
        return _dash_cache["date"]
    try:
        _o = ontology.get()
        last = doris.query(f"SELECT MAX({_o.date_col}) m FROM {_o.ads_table}")[0]["m"]
        d = str(last)
        _dash_cache.update(ts=_t.time(), date=d)
        return d
    except Exception:
        return ""


@app.get("/api/latest")
def api_latest():
    return {"date": _latest_date()}


def _dash_range_key(s: str, e: str) -> str:
    """具体日期区间 → 看板 range 关键字(以数仓最新日为锚)"""
    try:
        sd, ed = date.fromisoformat(s), date.fromisoformat(e)
        latest = date.fromisoformat(_latest_date()) if _latest_date() else ed
        span = (ed - sd).days + 1
        if sd == ed == latest - timedelta(days=1):
            return "yesterday"   # 看板的"昨日"锚定数仓最新完整日
        if span >= 27 and ed.month != sd.month:
            prev_end = latest.replace(day=1) - timedelta(days=1)
            if sd.month == prev_end.month:
                return "lastmonth"
        if span >= 27:
            return "30d"
        if span <= 10:
            return "7d"
    except Exception:
        pass
    return "7d"


def _dash_params(r: dict) -> dict:
    """从 NL 响应提取看板深链参数 + 原始查询区间(_rng 用于口径说明,不进 URL)"""
    rng = r.get("range") or []
    d = {"metric": r.get("metric") or "",
         "dim": r.get("dimension") or r.get("dim") or ""}
    if len(rng) == 2:
        d["range"] = _dash_range_key(rng[0], rng[1])
        d["_rng"] = "~".join(rng)
    return d


def with_dash_link(fn):
    """插件回答末尾附加:查询口径说明 + 看板深链(resp["_dash"] 带参,否则裸链)"""
    @functools.wraps(fn)
    async def w(request: Request):
        resp = await fn(request)
        try:
            if isinstance(resp, dict) and resp.get("data") and not resp.get("kb"):
                base = str(request.base_url).rstrip("/")
                latest = _latest_date()
                dash = dict(resp.pop("_dash", None) or {})
                rng_txt = dash.pop("_rng", "")
                parts = []
                if rng_txt:
                    parts.append("⏱ 本次查询区间: " + rng_txt
                                 + ("(数仓最新分区 " + latest + ",\"昨日\"=最新完整日)"
                                    if latest else ""))
                elif latest:
                    parts.append("📅 数仓数据截至 " + latest)
                prefix = ("\n\n" + "  \n".join(parts) + "" if parts else "")
                from urllib.parse import urlencode
                qs = urlencode({k: v for k, v in dash.items() if v})
                link = f"{base}/dash?{qs}" if qs else f"{base}/dash"
                resp["data"] = (str(resp["data"]) + prefix
                                + f"\n\n📊 [打开图表看板]({link})")
        except Exception:
            pass
        return resp
    return w


@app.post("/api/plugin/kb")
@badcase.watched("plugin")
async def plugin_kb(request: Request):
    """知识库问答插件(独立「知识库助手」专用):所有问题直接转发 kb_rag,不做数据路由"""
    params = dict(request.query_params)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not isinstance(body, dict):
        body = {}
    question = (params.get("queryText") or body.get("question") or
                body.get("query") or "").strip()
    badcase.set_question(question)
    if not question:
        return {"columns": [], "resultList": [], "data": "请输入您想咨询的问题", "kb": True}
    # 数据意图前置拦截:数据问题不进知识库(防联网检索出不可信答案),引导至问数助手
    try:
        _o, max_d = _route_ctx(params.get("profile"))
        p = nl.parse(question, max_d, _o)
        if p.get("metric_matched"):
            msg = ("您咨询的是业务数据问题,请使用「高速服务区电商助手」查询"
                   "(如:昨日GMV、各服务区订单量、本月客单价)。")
            return {"columns": [], "resultList": [], "data": msg, "kb": True}
    except Exception:
        pass
    res = kb.ask(question)
    ans = kb.format_answer(res)
    if res.get("refuse"):
        ans = (res.get("answer") or "抱歉,这个问题超出了知识库的服务范围。")
        ans += "\n\n💡 本助手专注知识库问答;业务数据请使用「高速服务区电商助手」(如:昨日GMV)。"
    elif res.get("ok"):
        ans += kb.followup_link(question)
    return {"columns": [], "resultList": [], "data": ans, "kb": True}


@app.post("/api/kb")
def kb_query(body: dict):
    """知识库问答 API 直连(非插件契约)"""
    question = (body.get("question") or body.get("query") or "").strip()
    if not question:
        raise HTTPException(400, "question 不能为空")
    res = kb.ask(question)
    return {"question": question, "answer": kb.format_answer(res),
            "refuse": res.get("refuse", False), "kb_ok": res.get("ok", False),
            "refs": res.get("refs") or []}


@app.post("/api/plugin/analyze")
@badcase.watched("plugin")
@with_dash_link
async def plugin_analyze(request: Request):
    params = dict(request.query_params)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    if not isinstance(body, dict):
        body = {}

    question = params.get("queryText") or body.get("question") or ""
    policy = params.get("policy") or body.get("policy") or "facts_first"
    badcase.set_question(question)

    # NL 路由：复杂题（分组/TopN/HAVING/同环比/节假日）走聚合分析，归因意图走全链路
    onto_r, max_d = _route_ctx(params.get("profile"))
    p = nl.parse(question, max_d, onto_r)
    log.info("插件NL路由: %s → %s", question, p["action"])

    # 知识类问题(未命中任何指标词)→ 礼貌引导至「知识库助手」(问数/知识分家,不出兜底怪值)
    if not p.get("metric_matched"):
        log.info("插件NL路由: %s → 引导(非数据问题)", question)
        msg = ("本助手专注业务数据问数分析(如:昨日GMV、各服务区订单量、本月客单价、归因分析)。\n\n"
               "💡 知识类问题(文档/报告/产品说明)请切换到「知识库助手」咨询。")
        return {"columns": [], "resultList": [], "data": msg, "kb": True}

    if p["action"] in ("aggregate", "ratio"):
        try:
            r = nl_query({"question": question, "profile": params.get("profile")})
        except HTTPException:
            raise
        except Exception as ex:
            log.error("NL聚合失败: %s", ex)
            raise HTTPException(500, repr(ex)[:200])
        if r.get("action") == "ratio":
            if r.get("base_range"):      # 显式基准月(同比YYYY年M月)
                row = {"指标": r.get("metric_name") or p["metric_name"],
                       "当期值": r.get("current"),
                       "基准区间": "~".join(r.get("base_range") or []),
                       "基准值": r.get("base_value"), "同比%": r.get("yoy_pct")}
                data_text = (f"**{row['指标']}** {r.get('current'):,.2f}\n\n"
                             f"- 较基准 {row['基准区间']}: {r.get('base_value'):,.2f}\n"
                             f"- 同比 **{r.get('yoy_pct')}%**")
            else:                        # 滚动同环比
                row = {"指标": r.get("metric_name") or p["metric_name"],
                       "当期值": r.get("current"),
                       "环比%": r.get("roll_pct"), "同比%": r.get("over_pct")}
                data_text = (f"**{row['指标']}** 当期 {r.get('current'):,.2f}\n\n"
                             f"- 环比 {r.get('roll_pct')}%\n"
                             f"- 同比 {r.get('over_pct')}%")
        elif r.get("multi") is not None:
            _onto = ontology.get(params.get("profile")) if params.get("profile") else ontology.get()
            NAMES = {m: _onto.metric_name(m) for m in r["metrics"]}
            row = {NAMES.get(m, m): v for m, v in zip(r["metrics"], r["multi"])}
            data_text = "\n\n".join(f"**{NAMES.get(m, m)}** {v:,.2f}\n\n"
                                  for m, v in zip(r["metrics"], r["multi"]))
        else:
            its = r.get("items") or []
            row = {"指标": r.get("metric_name") or p["metric_name"],
                   "合计": r.get("value", r.get("total")),
                   "分组数": r.get("rows"),
                   "Top3": "；".join(f"{i['value']}={i['v']:,.2f}" for i in its[:3]) or "-"}
            tv = r.get("total") if r.get("total") is not None else r.get("value")
            base = f"**{row['指标']}合计** {tv:,.2f}" if tv is not None else "查询完成"
            if its:
                head = "\n".join(f"- **{i['value']}** {i['v']:,.2f}" for i in its[:3])
                more = f"\n… 共{r.get('rows')}组" if (r.get("rows") or 0) > 3 else ""
                data_text = f"{base}\n\n**Top3:**\n{head}{more}"
            else:
                data_text = base
        columns = [
            {"nameEn": k, "name": k,
             "type": "NUMBER" if isinstance(v, (int, float)) else "CHAR",
             "showType": "NUMBER" if isinstance(v, (int, float)) else "category",
             "authorized": True}
            for k, v in row.items()]
        return {"columns": columns, "resultList": [row], "data": data_text,
                "_dash": _dash_params(r)}

    # 归因/洞察意图 → 原全链路（日期：显式参数优先，否则 NL 解析）
    if params.get("start") and params.get("end"):
        start_s, end_s = params["start"], params["end"]
    else:
        start_s, end_s = p["start"], p["end"]
    try:
        s, e = date.fromisoformat(start_s), date.fromisoformat(end_s)
    except ValueError:
        raise HTTPException(400, f"日期格式错误: {start_s}/{end_s}")

    metric = params.get("metric") or body.get("metric") or DEFAULT_METRIC
    result = analyze(AnalyzeReq(question=question, start=start_s, end=end_s,
                                metric=metric, policy=policy))

    attr = result.get("attribution") or {}
    top_bits = []
    for dim in (attr.get("dimensions") or [])[:2]:
        for t in dim.get("top_drivers", [])[:1]:
            top_bits.append(f"{t['value']}({t['contribution_pct']:+}%)")
    anomaly = result.get("anomaly") or {}
    row = {
        "metric": result["metric"],
        "value": result["current_total"],
        "mom_pct": result["ratio"]["roll_ratio"],
        "wow_pct": result["ratio"]["over_ratio"],
        "anomaly": anomaly.get("status"),
        "z_score": anomaly.get("z"),
        "top_factors": " ".join(top_bits) or "-",
        "elapsed_ms": result["elapsed_ms"],
    }
    columns = [
        {"nameEn": k, "name": {"metric": "指标", "value": "当期值", "mom_pct": "日环比%",
                               "wow_pct": "周同比%", "anomaly": "异常", "z_score": "Z值",
                               "top_factors": "Top归因", "elapsed_ms": "耗时ms"}.get(k, k),
         "type": "CHAR" if k in ("metric", "anomaly", "top_factors") else "NUMBER",
         "showType": "category" if k in ("metric", "anomaly", "top_factors") else "NUMBER",
         "authorized": True}
        for k in row
    ]
    insight_text = ((result.get("insight") or {}).get("insight") or
                    "分析完成（洞察生成未启用或失败）")
    _range = result.get("range") or []
    return {
        "columns": columns,
        "resultList": [row],
        "data": insight_text,
        "_dash": ({"metric": result.get("metric") or "",
                   "range": _dash_range_key(*_range)}
                  if len(_range) == 2 else {}),
    }


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    """全链路：意图门控 → 取数+同环比 → 异常 → 归因 → GLM 洞察"""
    t0 = datetime.now()
    s, e = date.fromisoformat(req.start), date.fromisoformat(req.end)
    onto = ontology.get(req.profile) if req.profile else None
    gated = engine.analysis_wanted(req.question, onto)
    log.info("分析请求: %s ~ %s metric=%s 意图门控=%s profile=%s",
             req.start, req.end, req.metric, gated, req.profile)

    # 1. 取数 + 同环比
    ads_table = onto.ads_table if onto else req.model_table
    dcol = onto.date_col if onto else "dt"
    rows = doris.query(
        f"SELECT {dcol} AS d, {engine._metric_expr(req.metric, onto)} AS v "
        f"FROM {ads_table} WHERE {dcol} BETWEEN '{req.start}' AND '{req.end}' "
        f"GROUP BY {dcol} ORDER BY {dcol}")
    detail = [{"date": str(r["d"]), "value": float(r["v"] or 0)} for r in rows]
    cur_total = sum(d["value"] for d in detail)
    ratio = engine.ratio_compare(doris, req.metric, s, e, onto)

    # 2. 异常检测（末日值 vs 滚动基线）
    anomaly = engine.zscore_check(detail[-1]["value"] if detail else 0.0,
                                  engine.moving_baseline(doris, req.metric, e, onto=onto),
                                  onto)
    status = anomaly.get("status", "normal")

    # 3. 归因（异常/明确分析意图时执行）
    attribution = None
    if gated or status != "normal":
        attribution = engine.attribution(
            doris, req.metric, s, e, req.dims or None, onto=onto)

    # 4. 洞察（分析意图 or 异常时生成）
    llm_insight = None
    if req.with_insight and (gated or status != "normal"):
        payload = {"ratio": ratio, "anomaly": anomaly, "attribution": attribution,
                   "detail": detail}
        try:
            llm_insight = insight_mod.generate_insight(req.question, payload, req.policy)
        except Exception as e:
            log.error("洞察生成失败: %s", e)
            llm_insight = {"policy": req.policy, "insight": None, "error": repr(e)[:200]}

    return {
        "metric": req.metric_name,
        "range": [req.start, req.end],
        "current_total": round(cur_total, 2),
        "analysis_triggered": gated or status != "normal",
        "gate_reason": "intent" if gated else ("anomaly:" + status if status != "normal" else "skip"),
        "ratio": ratio,
        "anomaly": anomaly,
        "attribution": attribution,
        "insight": llm_insight,
        "detail": detail,
        "elapsed_ms": int((datetime.now() - t0).total_seconds() * 1000),
    }


# ----------------------------- Badcase 收集 ----------------------------- #
@app.get("/api/badcase")
def badcase_list(status: str = None, limit: int = 200):
    return {"items": badcase.list_(status or None, limit)}


@app.post("/api/badcase")
def badcase_add(body: dict = None):
    b = body or {}
    if not (b.get("question") or "").strip():
        raise HTTPException(400, "question 不能为空")
    it = badcase.add(b["question"].strip(), channel="manual",
                     reason=b.get("reason") or "manual",
                     detail=b.get("detail") or {}, note=b.get("note") or "")
    return {"ok": True, "item": it}


@app.post("/api/badcase/annotate")
def badcase_annotate(body: dict):
    b = body or {}
    it = badcase.annotate(b.get("id"), b.get("reason"), b.get("note"), b.get("status"))
    if not it:
        raise HTTPException(404, "badcase 不存在")
    return {"ok": True, "item": it}


@app.post("/api/badcase/to_acceptance")
def badcase_to_acc(body: dict):
    b = body or {}
    it = badcase.get(b.get("id") or "")
    if not it:
        raise HTTPException(404, "badcase 不存在")
    name = b.get("profile") or ontology.DEFAULT_PROFILE
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "非法 profile 名")
    path = os.path.join(_PROFILE_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise HTTPException(404, "profile 不存在")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    acc = cfg.setdefault("acceptance", [])
    q = it["question"]
    if any(x.get("q") == q for x in acc):
        return {"ok": True, "dup": True, "cases": len(acc)}
    d = it.get("detail") or {}
    entry = {"q": q}
    if d.get("action"):
        entry["action"] = d["action"]
    if d.get("metric"):
        entry["metric"] = d["metric"]
    acc.append(entry)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    ontology.reload(name)
    badcase.annotate(it["id"], status="to_acceptance")
    return {"ok": True, "cases": len(acc), "entry": entry}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "9102")))
