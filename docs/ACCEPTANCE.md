# 试问集验收(Acceptance)

设计哲学:**编译通过 ≠ 能用,验收全绿才上线。**
profile 内置 `acceptance` 断言,保存前直跑(草稿态不落盘),上线后可随时出交付报告。

## 断言字段(全部可选,组合使用)

| 字段 | 含义 |
|---|---|
| `q` | 试问(必填) |
| `action` | 期望路由:aggregate / ratio / series / analyze |
| `metric` | 期望解析到的指标键(防串指标) |
| `metric_matched` | true=要求词表显式命中(防 default 兜底静默掩盖词表退化) |
| `value` | 期望数值(总量或首行),误差 0.01 |
| `rows` | 期望分组行数 |
| `rows_at_least` | 行数下限 |
| `contains` | 响应 JSON 序列化后须包含的子串(如维值名) |

## 建议的最小验收集

1. 总量锚点:昨天<主指标> 是多少,带精确 `value`(从数仓对拍取数)
2. 主力维度:近30天各<维度><主指标>,`rows` 或 `rows_at_least`
3. 词表哨兵:每个核心指标一条 `metric_matched: true`(防词表被误改后兜底静默)
4. 业务日历/点查/派生:按需

## 接口

- 草稿直跑:`POST /api/ui/acceptance`,body=完整 profile(不必先保存)
- 交付报告:`GET /api/ui/report/<name>` → `{cases, passed, pass_rate, results}`

## UI

接入向导 Tab → ② 试问集 → 运行验收 → 全绿后"保存上线"。
