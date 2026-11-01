# Known Issues

## 1. 种子账号网页登录报 "password encrypt error, please try again"

**现象**:用 `admin`(或 jack/tom/lucy/alice 等出厂账号)在网页登录,无论密码对错,一律报
`password encrypt error, please try again`;但用相同密码调 API 登录(`POST /api/auth/user/login`)
却成功。通过网页"注册"新建的账号则一切正常。

**根因**(上游前后端协议不配套,非密码问题):

1. 网页登录会把密码先用 **AES-ECB(密钥 `supersonic@2024`,CryptoJS 对该 15 字节密钥有特殊
   key schedule)** 加密成 base64 再提交(`Login/index.tsx` 的 `encryptPassword`);
2. 后端登录不做解密,而是把收到的值**再加密一次**(PBKDF2-HMAC-SHA256 + AES-CBC,盐=用户盐列)
   与库中存储值做密文比对(`DefaultUserAdaptor.getUserWithPassword`);
3. **网页注册**的账号,库里存的本来就是"对(网页密文)再加密"的值 → 自洽,能登录;
4. **种子账号**库里存的是"对(出厂明文)单次加密"的值 → 网页提交的密文被二次加密后永远对不上,
   且后端把一切登录异常统一包装成 `password encrypt error`(`DefaultUserAdaptor.java:118`),
   与密码是否正确无关。

上游同类反馈:[issue #1905](https://github.com/tencentmusic/supersonic/issues/1905)。

**影响面**:仅网页登录种子账号受影响;API 登录、网页注册账号、(经本插件形态接入的)旁挂服务均不受影响。

**修复**(不改任何源码,二选一):

- 方案 A(推荐):不用种子账号,网页注册新账号当管理员使用——上游文档的推荐姿势;
- 方案 B:把种子账号的存储值改写为网页协议同构值:

  ```bash
  npm i crypto-js
  node scripts/reset_web_login.mjs admin '你的新密码'   # 打印 UPDATE SQL
  # 在 SuperSonic 元数据库执行该 SQL,然后网页用 admin/新密码登录
  ```

**教训**:该报错信息是后端的"兜底文案",排查时不要被"加密失败"字面误导;先区分
`网页通道 vs API 通道`、`网页注册账号 vs 种子账号` 两条轴。

---

## 2. 数据源列表报 "Could not write JSON: ... this.text is null"

**现象**:语义建模/数据库页 500,日志 `HttpMessageNotWritableException`,Caused by 指向
`DatabaseResp.getHost()`(对 host 做正则脱敏时 NPE)。

**根因**:`s2_database` 里存在 `config` 缺 `url` 字段的脏数据(常见于添加数据源时半途保存),
列表序列化时 host 解析为 null。

**修复**:找到缺 url 的行,补全 url 或删除(确认无模型引用):

```sql
SELECT id, name, type, config FROM s2_database;
DELETE FROM s2_database WHERE id = <脏行id>;   -- 先确认 s2_model.database_id 无引用
```

---

## 3. WEB_PAGE 型插件(聊天内嵌 iframe)召回不生效

**现象**:创建 WEB_PAGE 类型插件(聊天回复内嵌网页)后,相关问题无法召回该插件
(`EmbeddingRecallRecognizer recallResult: selectedParses=[]`,无 resolve 日志),
即使其样例问题已写入向量库(preset_query_collection 中可查到、向量 512 维非空)。

**同源坑清单**(本版本 1.0.0-SNAPSHOT 实测):
1. `POST /api/chat/plugin` 永远是**新建**(带 id 直接主键冲突),没有更新接口;
   改插件只能"删除+重建"或直接改库;
2. WEB_PAGE 类型创建时 `containsAllDataSet=true` 不落库(`s2_plugin.data_set` 仍为空),
   导致召回后 resolve 阶段也会被跳过——需手工:
   `UPDATE s2_plugin SET data_set='-1' WHERE name='决策看板';`
3. 插件样例 embedding 依赖创建时的 `PluginAddEvent`;启动重启后未见全量重嵌逻辑,
   插件召回失效时优先"删除+重建"该插件。

**结论**:聊天内的看板入口采用**文本深链**方式(插件回答末尾附 `/dash` 链接,
见 `app.py` 的 `with_dash_link`),稳定可用;WEB_PAGE 内嵌方式留待上游修复。

## 4. 插件 embedding 召回:更新/删除残留导致 top-N 竞争失效(2026-10-31)

**现象**:插件 PUT 更新后召回静默失效;删除插件 API 报 500 但向量残留;两插件含同字面示例时 `s2.embedding.nResult` 默认=1,胜者独占,败者的 agent 全部空回答。

**根因链**:`PluginManager` 的 embedding 增删依赖事件监听,删除接口 500 时向量残留于 `preset_query_collection`;`nResult=1` 下 top-1 相似度最高向量若属于未绑定该 agent 的插件,遍历 `pluginMap.get(id)=null → continue` 后无候选。

**修复**(零源码):
1. 清理 `preset_query_collection` 中已删插件的残留向量(按 `metadata->>'queryId'` 前缀);
2. `conf-extra/application.yaml` 增加 `s2.embedding.nResult: 10`(top-N 内多候选遍历,pluginMap 命中即返回);
3. 同字面示例避免跨插件重复(语义区分措辞)。

## 5. 前端入口与聊天输入框的两个行为要点(2026-11-01)

1. **路由前缀**:前端 umi base 为 `/webapp`,聊天/助理管理等页面正确入口为
   `/webapp/chat?agentId=N`、`/webapp/agent`;裸 `/chat`、`/agent` 会渲染空白 root(服务端 fallback 返回 index.html,但前端路由不匹配)。
2. **聊天输入框渲染条件**:聊天区(含底部输入框 `composerInputWrapper`)仅在存在「当前会话」时渲染
   (`Chat/index.tsx: currentConversation && ...`)。进入聊天页若为空态,需先点击左侧「新对话」或任一历史会话,输入框才会出现。
3. 浏览器自动化(Playwright/CDP)环境下 composer 偶发不渲染,人工浏览器(Edge/Chrome/ego(lite))正常—— UI 验收建议人工进行。

## 6. 新建插件的 embedding 召回不稳定(2026-11-01)

**现象**:通过官方 API 新建 WEB_SERVICE 插件(含 pattern + examples)并绑定助理后,聊天链路
`EmbeddingRecallRecognizer resolve: (false,[])` 持续无候选;同库中既有插件召回正常。
`preset_query_collection` 中向量行已写入(embedding 非 NULL),且文本正确。

**定位过程**:
- 向量行以 `metadata.queryId = 插件ID*1000 + 序号` 标识,`getPluginIdFromEmbeddingId` 解析过滤
- 新插件的向量行存在但检索 top-N 不含它(同文本例句应 distance≈0 必进 top)
- SQL 直改 pattern/parse_mode 不触发向量重建;官方 PUT 可触发
  `requestEmbeddingPluginDelete/Add`,但重建后的向量与旧值完全一致(疑似上游
  embedding 服务端缓存/批量写入错位)
- 重启 supersonic、清理向量行后重走 PUT、aligned admin/viewer 权限均无法恢复

**影响**:多公司/多实例场景(每个业务注册自己的插件)下,新插件的聊天入口可能
无法召回;`/api/nl` 直连不受影响。

**缓解**:①重要插件尽量复用已验证的插件并区分 profile(插件 URL 带 `&profile=`),
不新建平行插件;②新建插件后先以脚本验证 `parse` 是否有候选,再交人工;
③依赖 `preset_query_collection` 的向量健康检查(向量非 NULL + 同文本自相似度≈1)。
