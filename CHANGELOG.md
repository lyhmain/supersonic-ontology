# Changelog

## 0.4.0 (2026-11-01) —— 双插件包:知识库(JEV+RAG)插件并入
- **知识库插件开源**:`kb_rag/`(4k+ 行 LangGraph 双管线:导入[PDF转MD/主体识别/切片/Embedding/入Milvus]
  + 查询[JEV门控/HyDE/向量检索/融合/重排/引用生成/拒答])+ `web/`(FastAPI 服务与导入/聊天页面)
  + `sidecar/`(Jev 决策层 standalone)
- `Dockerfile.kb` + `requirements-kb.txt`(端口 PORT 化,默认 9200)
- `scripts/setup_kb_plugin.py`:一键注册「知识库问答」插件 + 知识库助手
  (与 setup_plugin.py 同级安全:--plugin-name / URL 一致性防串台 / 精确绑定 / 鉴权失败提示)
- `/api/plugin/kb` 契约:标准 SuperSonic WebService 响应 + 数据意图前置拦截(数据问题引导问数助手)
- docs/INTEGRATION_KB.md:依赖表/部署/导入/插件契约/双助理推荐形态/已知边界
- 全量脱敏:内部 IP/品牌字样清零,配置全部环境变量化(.env.example KB 段)
- README 改版为双插件总览

## 0.3.0 (2026-11-01) —— 新用户全流程演练打磨
以"新用户拉取开源包 → 建自己的业务(连锁茶饮 demo)→ 部署 → 适配 → 问数"全流程实测,
修掉 9 个断点(全部为新用户真实会踩的坑):
- **端口可配**:Dockerfile CMD / docker-compose 端口映射读 `PORT` 环境变量(同机多实例并存)
- **profile 命名统一**:示例本体 `tea_demo` → `demo_tea`(文件名=标识,与 `.env`/文档全部对齐);
  `ontology.get()` 找不到 profile 时给出友好报错(含当前可用列表)
- **无年份日期**:「9月29日」此前不匹配单日正则(强制要求年份)落入默认当月返回 0;
  现支持无年份(默认数据最新年,数据末日之后自动回退一年)
- **默认本体路由**:`/api/nl`、`/api/plugin/*` 的 max_date 此前固定查环境变量兜底表,
  与默认 profile 不一致(多公司部署时"昨天"锚错表)→ 统一 `_route_ctx()`(显式 profile >
  默认 profile > 环境变量兜底)
- **setup_plugin.py**:`--plugin-name` 参数化;同名插件 URL 不一致时拒绝(防多实例串台);
  助理绑定改为精确替换;创建后找不到插件的静默失败改为鉴权提示;pattern 话术通用化
- **README 30 秒上手**:补齐演示本体拷贝/建数据步骤与 profile 命名说明
- 演练结果:茶饮业务适配 `/api/nl` 层 10/10(单值/口径过滤/维表分组/点查/派生/区间/节日),
  内置验收 4/4;SuperSonic 聊天链路对**新建插件**的 embedding 召回存在上游不稳定
  (见 docs/KNOWN_ISSUES.md #6),已定位至插件向量存储层

## 0.2.0 (2026-10-29)
- **Badcase 闭环**:查空/指标未命中自动捕获(api+插件双通道,同分钟去重)+ 控制台手工上报
  → 标注(待处理/已修复/忽略)→ 一键转试问集验收;新增 `/api/badcase` 系列端点与 UI 标签页
- NL 解析补齐分组触发词(「按X排序」「有哪些X」「哪些X」),`各X`/`每个X` 原有句式不变
- `scripts/setup_plugin.py`:一键注册 SuperSonic「决策分析」插件并绑定助理(官方 API,零源码修改)
- `scripts/reset_web_login.mjs` + `docs/KNOWN_ISSUES.md`:修复/解释上游种子账号网页登录
  `password encrypt error`(前后端加密协议不配套,issue #1905 同源)
- 代码与内部生产版本对齐(含省份/地区维度示例、验收断言增强)

## 0.1.0 (2026-10-28)
- 首个开源版本
- 本体 profile:指标/维度词典、口径过滤、业务日历、取值令牌、派生指标、组粒度
- NL 路由:总量/分组/TopN/HAVING/点查/同环比(滚动+显式基准)/每日序列/多指标
- 归因分解 + 行级下钻 + LLM 洞察(Jev 三后端热切换:本地/原版/百炼)
- SuperSonic 插件契约(/api/plugin/analyze)
- 控制台 /ui:本体配置管理、Jev 后端、接入向导(扫表草稿/星型维表/试问集验收/报告)
- 真实业务基准:30 题 86.7% @ 0.02s/题(判分器与 LLM 通道一致)
