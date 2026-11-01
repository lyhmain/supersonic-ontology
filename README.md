# supersonic-ontology

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/lyhmain/supersonic-ontology)](https://github.com/lyhmain/supersonic-ontology/releases)
[![SuperSonic](https://img.shields.io/badge/works%20with-SuperSonic-blue)](https://github.com/tencentmusic/supersonic)
[![Stars](https://img.shields.io/github/stars/lyhmain/supersonic-ontology?style=social)](https://github.com/lyhmain/supersonic-ontology/stargazers)

**SuperSonic dual plug-ins: Ontology-driven NL2Analytics + JEV+RAG Knowledge Base**
Two sidecar plug-ins — deployable independently or as a decision engine for
[SuperSonic](https://github.com/tencentmusic/supersonic), one minute to wire into your chatbot.
[English README](README_EN.md) 🇬🇧

| 插件 | 形态 | 能力 | 入口 |
|---|---|---|---|
| **决策分析**(本体建模问数) | 本仓库主体 | 指标/分组/同环比/归因/口径对拍/试问集验收 | `/api/nl` `/api/plugin/analyze` |
| **知识库问答**(JEV+RAG) | `kb_rag/` + `web/` | 文档咨询/引用溯源/拒答门控/数据意图分流 | `/api/kb` `/api/plugin/kb` |

> 把"问数"的正确性从大模型手里拿回来:语义本体配置化、业务口径精确对拍、毫秒级响应、
> 试问集验收"全绿才上线"。把"知识问答"的可信性同样拿回来:JEV 门控拒答、引用溯源、
> 无据不答。LLM 只做它擅长的事——最终洞察的一句话总结。

> ⭐ **如果这个项目帮到了你,欢迎点个 Star** —— 这是我们持续维护的最大动力。
> ⭐ If this project helps you, please consider giving it a star!

---

## 为什么不是又一个 NL2SQL

通用 NL2SQL/ChatBI 在真实业务里反复踩同一批坑,本项目把它们全部移出 LLM:

| 坑 | 本项目的答案 |
|---|---|
| 词表不可控(同义词/新词/术语劫持) | **业务本体 profile**:指标/维度词典 + 取值令牌,配置即生效 |
| 口径不精确(已支付?退款?分摊?) | 口径写在本体里(`paid_filter`/`amount_override`),**与数仓对拍锚定** |
| 相对日期/节假日错 | 语言级日期解析 + 本体**业务日历**(法定假日/公司周年庆) |
| 复杂题(分组/TopN/HAVING/同环比) | 确定性解析执行,不赌 LLM |
| 准确率低、延迟高、token 贵 | 实测 30 题基准 **86.7% @ 0.02s/题、0 token**(LLM 通道 83.3% @ 26s) |
| 上线质量靠人肉 | **试问集验收**:profile 内置断言,全绿才允许上线,验收报告一键交付 |

LLM(任意 OpenAI 兼容后端:本地部署 / 原版 API / 阿里百炼)只负责把归因数据写成给业务看的洞察。

## 架构

```
用户问题 ──► /api/nl  ──► 本体解析(profile 词典: 指标/维度/日期/TopN/HAVING/取值令牌)
                │                │
                │                ├─ 总量/分组/TopN/HAVING ──► Doris 直连(口径过滤+维表JOIN)
                │                ├─ 每日序列 / 同环比(滚动或显式基准月)
                │                └─ 归因意图 ──► 维度贡献分解 ──► Jev 后端(LLM)──► 洞察
                │
SuperSonic 聊天 ──► 插件 /api/plugin/analyze ──┘(Agent 即插即用,明细/归因同样返回)
管理台   /ui    ──► 本体配置 · Jev 后端切换 · 接入向导(扫表生成草稿+试问集验收)
```

## 30 秒上手

```bash
cp .env.example .env      # 填你的 Doris / LLM 连接
cp examples/profiles/demo_tea.json profiles/   # 载入演示本体(可换成你自己的)
docker exec -i <你的doris容器> mysql -uroot -P9030 < examples/demo_tea.sql   # 演示数据(可选)
docker compose up -d      # 或: docker build -t supersonic-ontology . && docker run ...
open http://localhost:9102/ui
```

> profile 标识 = 文件名(`demo_tea.json` → `demo_tea`),需与 `.env` 的 `ONTOLOGY_PROFILE` 一致。
> 换自己的业务:管理台 → 接入向导,或照 `docs/SPEC.md` 填一张中文四清单,再走
> `examples/demo_tea.sql` 的建表格式导入你的数据。

接入向导:选表(可填维表)→ 自动生成本体草稿(列注释直接变业务词表)→ 补口径与验收期望 →
试问集全绿 → 保存上线。全程不写一行代码。

## 本体 profile 一览(完整规范见 docs/ONTOLOGY_SPEC.md)

```jsonc
{
  "profile": "demo_tea",
  "fact":  { "table": "demo_tea_orders t", "date_col": "dt", "paid_filter": "t.is_refund = 0" },
  "ads":   { "table": "demo_tea_orders", "metric_exprs": { "revenue": "SUM(amount)" } },
  "metrics": {                       // 词表 → 口径列 / 计数列 / 可加列 / 派生公式
    "revenue": { "name": "营业额", "words": ["营业额", "流水"], "col": "amount" },
    "cups":    { "name": "杯数",   "words": ["杯数"],         "sum_col": "cups" },
    "ppc":     { "name": "杯单价", "words": ["杯单价"],       "derive": {"num": "revenue", "den": "cups"} }
  },
  "dimensions": {                    // 词表 → JOIN 片段 + 取值列 + 组粒度
    "store": { "name": "门店", "words": ["门店"], "expr": "s.name",
               "join": "LEFT JOIN demo_tea_store s ON t.store_id = s.id" }
  },
  "value_tokens": [ { "attr": "store", "regex": "([一-龥]{2,6}店)" } ],  // "西湖店"点查
  "holidays": { "周年庆": { "2026": ["2026-09-15", "2026-09-15"] } },
  "acceptance": [ { "q": "昨天营业额是多少", "value": 9828.65, "metric": "revenue" } ]
}
```

## 能力矩阵

- **问数**:总量 / 维度分组 / TopN(前N、最高的N、最低)/ HAVING / 点查(县市01、西湖店、G25长深高速)
- **时间**:昨天/今日/近N天/某年某月/区间(含省写"9月29日到30日")/上月本月/**业务日历**/去年X节
- **指标**:计数列 / 可加列 / 派生(客单价=GMV÷订单数)/ 多指标("GMV和订单数")
- **分析**:同环比(滚动+显式基准月)/ 每日序列 / **归因分解**(维度贡献%)/ 行级下钻 / LLM 洞察
- **工程**:多公司多 profile 一份部署 / Jev 多后端热切换 / 试问集验收 + 验收报告 / SuperSonic 插件契约
- **治理**:**Badcase 闭环**——查空/指标未命中自动捕获 + 控制台手工上报 → 标注 → 一键转试问集验收 → 修复关单
- **可验证**:真实业务基准上 26/30~26/30 精确对拍;归因下钻 total 与归因 current 严格一致

## 与 SuperSonic 集成

零源码修改:SuperSonic 官方镜像原样运行,本服务以旁挂形式存在,集成走官方插件机制。

```bash
export SUPERSONIC_BASE=http://127.0.0.1:9080
export SUPERSONIC_PASS='你的密码'
export ONTOLOGY_URL=http://127.0.0.1:9102
python scripts/setup_plugin.py        # 一键注册「决策分析」插件 + 创建绑定它的助理
```

或手动:

1. SuperSonic 建一个 Agent,挂"WebService 插件"
2. 插件 URL:`http://<本服务>:9102/api/plugin/analyze?queryText=(预置参数 queryText)`
3. 聊天框即获得全部问数/分析能力(返回 columns/resultList/data,含分组 Top 明细)

详见 docs/INTEGRATION_SUPERSONIC.md。不使用 SuperSonic 时,直接调 `/api/nl` 即可。

### 知识库插件(JEV+RAG)

```bash
docker build -f Dockerfile.kb -t supersonic-kb-rag . && \
docker run -d --network host -e PORT=9200 --env-file .env -v $PWD/data:/app/data supersonic-kb-rag
export KB_PLUGIN_URL=http://127.0.0.1:9102   # decision-service(拦截+排版层)
python scripts/setup_kb_plugin.py     # 一键注册「知识库问答」插件 + 知识库助手
```

前置:Milvus / MinIO / PostgreSQL / LLM / Embedding(全部环境变量配置),
文档导入 `/import.html`,架构与契约详见 docs/INTEGRATION_KB.md。

## 文档

- docs/SPEC.md —— **规范配置(推荐)**:中文四清单,普通数据开发直接用
- docs/ONTOLOGY_SPEC.md —— profile 字段完整规范(高级)
- docs/INTEGRATION_SUPERSONIC.md —— 聊天机器人接入(问数插件)
- docs/INTEGRATION_KB.md —— **知识库插件(JEV+RAG)接入**
- docs/ACCEPTANCE.md —— 试问集验收与交付报告
- docs/ARCHITECTURE.md —— 设计决策与踩坑实录
- docs/COMPATIBILITY.md —— **与 SuperSonic 的兼容性分析**(集成面/坑/契约/口径治理)
- docs/KNOWN_ISSUES.md —— **上游已知坑**:种子账号网页登录报错、数据源脏数据 500(含修复脚本)
- docs/ROADMAP.md —— 路线图

## scripts

- `scripts/setup_plugin.py` —— 一键注册「决策分析」插件 + 绑定助理(官方 API,零源码修改)
- `scripts/reset_web_login.mjs` —— 修复种子账号网页登录 `password encrypt error`(见 KNOWN_ISSUES)
- `scripts/smoke.sh` —— 冒烟测试

## 基准数据(供参考)

内部真实业务数仓(高速服务区电商,订单域)30 题冻结基准,与论文 LLM 通道同一判分器:

| 通道 | 准确率 | 延迟 |
|---|---|---|
| LLM NL2SQL(DeepSeek,本体系) | 86.7% | mean 7s |
| LLM NL2SQL(GLM-5.3,本体系) | 83.3% | p50 12s / mean 26s |
| **supersonic-ontology 本体路由** | **86.7%** | **0.02s/题 · 0 token** |

## License

Apache-2.0
