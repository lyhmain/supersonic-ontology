"""自然语言解析:问题 → 聚合/分析参数(指标/维度/日期/TopN/HAVING/节假日)

语言级模式(日期/TopN/HAVING/每日)与公司无关,留在本模块;
业务词表(指标/维度/节假日/取值令牌/意图词)全部来自业务本体(ontology.Ontology)。
"""
import re
from datetime import date, timedelta

import ontology


def parse_metric(q: str, onto=None):
    onto = onto or ontology.get()
    return onto.find_metric(q)


def parse_dim(q: str, onto=None):
    onto = onto or ontology.get()
    return onto.find_dim(q)


def parse_dates(q: str, max_date: date, onto=None):
    """返回 (start, end)。支持 显式日期/到-区间/某年某月/近N天/昨天/今日/节假日/上月本月/默认当月"""
    m = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:到|至|-|~)\s*(?:(\d{4})年)?(?:(\d{1,2})月)?(\d{1,2})日", q)
    if m:
        y1, m1, d1, y2, m2, d2 = m.groups()
        y1 = int(y1 or (y2 or max_date.year))
        y2 = int(y2 or y1)
        m1, d1 = int(m1), int(d1)
        m2, d2 = int(m2 or m1), int(d2)
        return date(y1, m1, d1), date(y2, m2, d2)
    m = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日", q)
    if m:
        y, mo, d = m.groups()
        y = int(y or max_date.year)
        try:
            day = date(y, int(mo), int(d))
        except ValueError:
            return None, None
        if day > max_date and date(y - 1, int(mo), int(d)) <= max_date:
            day = date(y - 1, int(mo), int(d))   # 无年份且落在数据末日之后 → 回退一年
        return day, day
    m = re.search(r"(\d{4})年(\d{1,2})月", q)
    if m:
        y, mo = map(int, m.groups())
        start = date(y, mo, 1)
        end = date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1)
        return start, end
    m = re.search(r"近\s*(\d+)\s*天", q)
    if m:
        n = int(m.group(1))
        return max_date - timedelta(days=n - 1), max_date
    if "昨天" in q or "昨日" in q:
        return max_date - timedelta(days=1), max_date - timedelta(days=1)
    if "今天" in q or "今日" in q:
        return max_date, max_date
    onto = onto or ontology.get()
    rng = onto.holiday_range(q, max_date)
    if rng:
        return rng
    if "上月" in q:
        first = date(max_date.year, max_date.month, 1) - timedelta(days=1)
        return date(first.year, first.month, 1), first
    if "本月" in q or "当月" in q:
        return date(max_date.year, max_date.month, 1), max_date
    # 无显式日期 → 默认数据最新月
    return date(max_date.year, max_date.month, 1), max_date


def parse_topn(q: str):
    m = re.search(r"前\s*(\d+)(?:\s*个)?|top\s*(\d+)", q, re.I)
    if m:
        return int(m.group(1) or m.group(2))
    m = re.search(r"(?:最高|最低|最多|最少)(?:的)?\s*(\d+)\s*个?", q)
    if m:
        return int(m.group(1))
    return 0


def parse_having(q: str):
    m = re.search(r"(?:超过|大于|高于|以上)\s*([\d.]+)\s*万", q)
    if m:
        return float(m.group(1)) * 10000
    m = re.search(r"(?:超过|大于|高于|以上)\s*([\d,]+(?:\.\d+)?)", q)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def is_daily(q: str) -> bool:
    return any(w in q for w in ("每日", "按天", "每天", "逐日"))


def parse(question: str, max_date: date, onto=None) -> dict:
    """问题 → 路由参数。action: aggregate(分组/TopN/HAVING/总量) | analyze(归因意图) | ratio(同环比) | series(每日)"""
    q = question or ""
    onto = onto or ontology.get()
    metric, metric_name, metric_matched = onto.find_metric_full(q)
    start, end = parse_dates(q, max_date, onto)

    ratio_kw = any(w in q for w in onto.ratio_words)
    analysis_kw = any(w in q for w in onto.analysis_words)
    topn = parse_topn(q)
    having = parse_having(q)

    if ratio_kw:
        action = "ratio"
    elif analysis_kw and not (topn or having is not None
                              or re.search(r"各|每个|分别|排行|排名|是多少", q)):
        action = "analyze"
    else:
        action = "aggregate"

    order = "ASC" if ("最低" in q or "最少" in q) else "DESC"
    m_yoy = re.search(r"同比\s*(?:(20\d{2})年)?(\d{1,2})月", q)
    base = None
    if m_yoy:
        y = int(m_yoy.group(1)) if m_yoy.group(1) else start.year - 1
        mo = int(m_yoy.group(2))
        base = (date(y, mo, 1),
                date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1))
    else:
        m_mom = re.search(r"环比\s*(?:(20\d{2})年)?(\d{1,2})月", q)
        if m_mom:
            y = int(m_mom.group(1)) if m_mom.group(1) else start.year
            mo = int(m_mom.group(2))
            base = (date(y, mo, 1), date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1))
    if is_daily(q):
        action = "series"
    group_intent = bool(topn or having is not None
                        or any(w in q for w in onto.group_words))
    dim = onto.find_dim(q) if (action == "aggregate" and group_intent) else None
    vfilter = (onto.find_value_token(q, dim, group_intent)
               if action == "aggregate" else None)
    return {"action": action, "metric": metric, "metric_name": metric_name,
            "metric_matched": metric_matched,
            "base": base,
            "metrics": onto.find_metrics_multi(q),
            "dim": dim,
            "value_filter": vfilter,
            "topn": topn, "having_min": having, "order": order,
            "start": start.isoformat(), "end": end.isoformat()}
