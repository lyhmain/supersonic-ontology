# 与 SuperSonic 的兼容性分析

> 结论先行:**严格加法式集成(sidecar),零代码分叉、零元数据侵入,升级安全。**
> 全部耦合收敛为一个 HTTP 插件契约 + 共享只读数仓;已验证的 4 个坑均有绕行方案。

## 1. 集成面清单(全部耦合点)

| # | 集成面 | 方向 | 性质 |
|---|---|---|---|
| 1 | 插件契约 `POST /api/plugin/analyze?queryText=…` → `{columns, resultList, data}` | SuperSonic → 本服务 | **唯一功能耦合**(官方 WebService 插件扩展点) |
| 2 | Doris 数仓 `dw.*`(dwd/dim/ads) | 双方只读 | 数据共享,口径以对拍锚定 |
| 3 | `/api/health` 里的 sonic 登录探活 | 本服务 → SuperSonic | 可选;SuperSonic 缺席仅影响该项检查 |
| 4 | `/api/query?source=supersonic` 语义对拍通道 | 本服务 → SuperSonic | 可选对拍,非功能依赖 |

不存在的关系:不改 SuperSonic 代码 / 不写它的 PostgreSQL 元数据 / 不共享进程或端口。

## 2. 兼容性良好项(已实测)

- **插件即插即用**:Agent 挂 WebService 插件后,聊天框直接获得问数/分析全能力;
  分组结果的 Top3 明细拼进 `data` 文本(SuperSonic 聊天仅渲染该字段)
- **部署隔离**:独立容器/生命周期/端口(9102),SuperSonic 重启不影响本服务
- **口径一致**:两套系统读同一数仓,GMV 等核心指标经三通道对拍
  (/api/nl dwd 口径 == /api/aggregate == /api/query ads 口径)+ 48 组合守恒扫描
- **降级可用**:SuperSonic 完全下线时,`/api/nl`、控制台、向导、验收全部照常
  (仅 health 的 supersonic 项变 false)——本服务不依赖 SuperSonic 存活
- **无认证耦合**:两边各管各的账号体系,互不需要对方发放凭证

## 3. 已踩过的坑与绕行(部署版 1.0.0-SNAPSHOT 实测)

| # | 坑 | 根因 | 绕行/状态 |
|---|---|---|---|
| 1 | 插件 URL 写 127.0.0.1 不通 | SuperSonic 容器网络 | 插件 URL 写宿主机 LAN IP |
| 2 | 同一 Agent 同时挂插件+数据集互相覆盖 | 部署版 `enableNL2SQL()` 字节码**缺 selectedParses 空门**(javap 实证) | 拆双 Agent:6=纯插件(分析),7=纯数据集(快车道);master 分支已修,升级后可合并 |
| 3 | 聊天接口缺 chatId 报错 | SuperSonic 会话模型要求 | 聊天自动携带;脚本直调须显式传 |
| 4 | 原生快车道术语劫持(术语表把"订单数"映射到错误指标,SQL 退化为 SELECT dt) | 原生解析的术语机制 | 删除 17 条问题术语;或统一走本服务通道 |
| 5 | 原生通道按天粒度注入 dt | headless 翻译器层行为,插件侧不可达 | 需要日粒度/节假日/同环比的问题由本服务承接(绕开而非修复) |

## 4. 契约依赖与升级风险

本服务对 SuperSonic 的**全部契约假设**(共 3 条,均为插件路径行为):

1. `POST`,问题在 query 参数 `queryText`,body 参数键值被忽略
2. 响应 JSON 的 `data`(字符串)字段会被聊天界面渲染
3. `resultList/columns` 供表格类展示(聊天主用 data)

风险评级:低。该契约是 WebService 插件的既有约定;若上游变更,故障表现为
"聊天无响应/空 data",与 `/api/nl` 直连通道完全隔离,**业务不中断**。
建议:升级 SuperSonic 前跑一遍插件端到端用例(见 scripts/smoke.sh 思路,3 条聊天断言)。

## 5. 口径治理:单一事实源原则

两套系统并存的最大隐患是**口径漂移**(各自维护"GMV"定义)。约定:

- **本体 profile 是口径唯一事实源**(fact.paid_filter / amount_override / 派生公式)
- SuperSonic 数据集只保留简单点查快车道,不承载口径定义
- 新增/修改口径必须:改 profile → 跑试问集(含对拍锚点 value 断言)→ 全绿上线
- 守恒扫描(指标×维度×时段)作为持续校验手段

## 6. 安全模型差异

| | SuperSonic | 本服务 |
|---|---|---|
| 认证 | 账号密码(会话 Token) | **当前无鉴权**(内网信任模型) |
| 暴露面 | 9080 全功能 | 9102 全数据读取 |

两者鉴权域独立。⚠️ 内网部署可接受;若暴露到非信任网络,应在 v0.2 落实
插件端点 Token 校验(ROADMAP 已列)。

## 7. 兼容矩阵(实测)

| SuperSonic 版本 | 插件契约 | 双 Agent 要求 | 备注 |
|---|---|---|---|
| 1.0.0-SNAPSHOT(社区版,当前部署) | ✅ | ⚠️ 必须(字节码缺门) | 上表 #2 |
| master(含 enableNL2SQL 修复) | ✅(未实测,契约未变) | 可单 Agent 双工具 | 升级后跑冒烟确认 |
| 本服务任意版本 | 与 SuperSonic 解耦 | — | 升级互不影响 |
