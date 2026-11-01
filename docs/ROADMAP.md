# 路线图

## v0.1(当前)
- 本体 profile(指标/维度/口径/日历/令牌/派生)+ NL 路由 + Doris 直连
- 归因/下钻/洞察(Jev 多后端)/SuperSonic 插件契约
- 控制台:本体配置/Jev 切换/接入向导/试问集验收+报告

## v0.2
- 数据源抽象:MySQL / StarRocks / ClickHouse(SQLAlchemy 方言)
- 跨事实表问法(同粒度 UNION / 显式 join path)
- 接入向导 v2:LLM 辅助——从表注释/样例数据自动补词表与口径建议
- 多租户:profile 级 API Key 与审计日志

## v0.3
- SuperSonic 深度集成:数据集/模型/Agent 自动注册(profile → SuperSonic 元数据)
- 时序预测与异常归因增强(因果拆解)
- 英文问句支持(语言级模式参数化)

## 永远欢迎
- 更多行业模板(Pull Request 到 examples/templates/)
- 更多数据库方言、更多语言
