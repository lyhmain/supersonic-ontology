# 架构与设计决策

## 定位

旁挂(out-of-band)语义层:不侵入 SuperSonic 引擎,以插件/HTTP 形式提供
确定性问数与分析。语义本体全部配置化,LLM 仅做归因洞察的文本生成。

## 模块

```
app.py        FastAPI:/api/nl /api/analyze /api/drill /api/aggregate /api/query
              /api/plugin/analyze(SuperSonic 契约)/ui + 配置台/向导/验收 API
ontology.py   Ontology:profile 编译(词典/令牌/日历/意图) + 注册表(按名缓存)
nl.py         语言级解析:日期(含省写区间)/TopN/HAVING/每日/多指标/意图路由
engine.py     聚合(口径过滤+维表JOIN+组粒度)/归因(贡献%)/行级下钻/异常基线
jev.py        Jev 后端注册表(本地/原版/百炼,OpenAI 兼容,热切换,配置落盘)
insight.py    归因洞察生成(四策略提示词;空正文自动放大预算重试)
ui.html       控制台:本体配置 / Jev 后端 / 接入向导(扫表→草稿→试问集验收)
```

## 关键设计决策(踩坑实录)

1. **词表进配置,语言进代码**:指标/维度/节假日/取值令牌是业务知识,逐公司不同;
   日期/TopN/HAVING 是语言能力,全公司共享。两者分开使"接入新公司=一个 JSON"。
2. **口径锚定**:fact.paid_filter 与数仓 ads 层对拍(如 SUM(total_amount WHERE
   status='1001') == ads.gmv),三通道(/api/nl、/api/aggregate、/api/query)同值由
   守恒扫描持续验证。
3. **组粒度防重名**:会员维 GROUP BY u.id, u.nick_name,否则重名昵称金额合并虚增。
4. **取值令牌防误吞**:dim+分组词时跳过同名令牌("各门店"≠"西湖店")。
5. **metric_matched 哨兵**:default_metric 兜底会让词表退化静默;解析显式暴露命中
   与否,试问集可断言,词表回归在验收层被拦截。
6. **金额分摊/跨组重叠是口径语义**:sku 分摊金额与订单 total_amount 存在舍入差
   (~1.9%),多 SKU 订单在分组计数中跨组重复——不守恒不是 bug,文档声明。
7. **验收先于上线**:acceptance 断言在草稿态直跑(临时注册 Ontology,跑完移除),
   "编译通过但表不存在/词表改坏"都会被红灯拦截。
8. **确定性优先,LLM 殿后**:解析与执行零 LLM;洞察生成走可切换的 OpenAI 兼容后端,
   响应带 jev_backend 可追溯。

## 已知边界(v0.1)

- 单事实表(+任意维表);跨事实表 JOIN 未开放
- 仅 Doris(MySQL 协议);抽象层已预留(_metric_expr/aggregate 参数化)
- 未识别的取值令牌会退化为总量(不报错)——验收集应覆盖核心点查
- 多语言:词表与语言级模式当前面向中文问句
