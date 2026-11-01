# Ontology Profile 规范(v0.1)

一个 profile = 一家公司的语义本体,一个 JSON 文件,放在 `ONTOLOGY_DIR`(默认 `./profiles`)。
请求级切换:所有接口 body 传 `"profile": "<name>"`;默认由环境变量 `ONTOLOGY_PROFILE` 指定。

## 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `profile` | ✓ | 唯一名(字母数字_-),同文件名 |
| `domain` | | 人类可读描述,UI 展示 |
| `default_metric` | | 无指标词命中时的兜底指标(建议配合验收 `metric_matched` 断言) |
| `fact` | ✓ | 事实表(见下) |
| `ads` | | 日汇总表:总量/序列/同环比走它;缺省用 fact 主表 |
| `metrics` | ✓ | 指标词典 |
| `dimensions` | | 维度词典 |
| `value_tokens` | | 维度取值令牌(点查) |
| `holidays` | | 业务日历 |
| `intent` | | 意图词表覆盖(一般留空用默认) |
| `attribution_default_dims` | | 归因默认维度顺序 |
| `anomaly` | | `z_warn`(默认2.0)/`z_crit`(3.0)/`baseline_days`(28) |
| `acceptance` | | 试问集(见 docs/ACCEPTANCE.md) |

## fact

```json
{ "table": "dwd_order_info_inc o",   // 可带别名;别名用于生成 o.dt 等
  "date_col": "dt",
  "paid_filter": "o.order_status = '1001'" }   // 业务口径,拼进所有聚合 WHERE
```

## metrics(三型)

| 型 | 字段 | 生成 SQL(fact 表) |
|---|---|---|
| 求和 | `col` | `SUM(o.<col>)` |
| 计数 | `count_col` | `COUNT(DISTINCT o.<col>)` |
| 可加 | `sum_col` | `SUM(o.<col>)`(语义同求和,但表达"该列本身是量"而非金额) |
| 派生 | `derive: {num, den}` | 分子/分母各自聚合后相除(支持分组) |

公共:`name`(中文名,聊天/报表展示)、`words`(业务词表,长短词优先匹配)。
维度上的金额重算:`dimensions.<key>.amount_override: {"gmv": "d.split_total_amount"}`(如 SKU 分摊金额)。

## dimensions

```json
"county": { "name": "县市", "words": ["县市"],
            "expr": "sa.county",
            "join": "LEFT JOIN dim_service_area sa ON o.service_area_id = sa.id",
            "group": null }        // 可选:组粒度,如会员维 "u.id, u.nick_name" 防重名合并
```

星型维表多列:每个列一个 dim 条目,`join` 相同(引擎会复用同一别名)。

## value_tokens(点查令牌)

```json
[ { "attr": "county", "regex": "(县市\\d+)" },
  { "attr": "road_section", "regex": "(G\\d+[一-龥A-Za-z0-9]{0,8}?)(?=近|是多少|GMV|订单|，|。|,|$|\\s)" } ]
```

- 命中后:与维度同名+分组词 → 忽略("各门店"≠取值);无分组词 → 点查/总量过滤
- `when`:可选关键词门槛,句中含任一才启用该令牌

## holidays(业务日历)

```json
{ "国庆": { "2025": ["2025-10-01", "2025-10-08"], "2026": ["2026-10-01", "2026-10-08"] },
  "周年庆": { "2026": ["2026-09-15", "2026-09-15"] } }
```

问句含节日名即命中;`2026年X` 显式取年,`去年X` 取数据最大年份-1,否则取数据最大年份。

## intent(可选覆盖)

```json
{ "analysis_words": ["为什么", "归因", "…"], "ratio_words": ["同比", "环比"], "group_words": ["各", "每个"] }
```

语言级模式(显式日期/省写区间/近N天/TopN/HAVING/每日)与业务无关,内置不配置。

## acceptance(试问集,见 docs/ACCEPTANCE.md)

```json
[ { "q": "昨天营业额是多少", "action": "aggregate", "metric": "revenue",
    "metric_matched": true, "value": 9828.65 } ]
```
