# KB-RAG 知识库插件(JEV+RAG)接入指南

基于 **JEV 门控 + RAG 检索生成**的企业知识库服务,作为 SuperSonic 的旁挂插件,
为聊天机器人提供**带引用来源与拒答门控**的知识问答能力。与「决策分析」问数插件
(本体建模)双插件互补:数据问题走问数,知识问题走本插件,互不串台。

## 架构

```
导入管线(LangGraph)                    查询管线(LangGraph)
PDF/MD/DOCX ─→ PDF转MD ─→ 图片处理      问题 ─→ JEV门控(in_scope/拒答)
            ─→ 主体识别 ─→ 切片         │→ HyDE 改写 ─→ 向量检索(Milvus)
            ─→ Embedding ─→ Milvus      │→ 融合 ─→ 重排 ─→ 生成(带引用[n])
MinIO 存原图                            │→ 引用核查 ─→ 答案
                                        └→ 无据可依 → 拒答(不编造)
```

- **JEV 门控**:主体识别 + 三振判定(in_scope / out_of_scope / unclear),
  闲聊/超纲问题礼貌拒答,不进入检索——这是"不编造"的第一道闸
- **引用溯源**:答案内嵌 `[n]` 标记,`refs` 返回文档名/章节路径
- **数据意图拦截**:`/api/plugin/kb` 对业务数据问题(命中问数指标词)直接引导至问数助手,
  防止用知识库检索出不可信的"数据答案"

## 依赖(外部服务)

| 服务 | 用途 | 环境变量 |
|---|---|---|
| Milvus ≥2.5 | 向量库(chunks + 主体词两个 collection) | `MILVUS_URL/USER/PASSWORD` |
| MinIO | 原文件/图片存储 | `MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY` |
| PostgreSQL | 会话历史 | `PG_HOST/PORT/USER/PASSWORD/DATABASE` |
| LLM(OpenAI 兼容) | 生成/门控 | `LLM_API_BASE/KEY` |
| Embedding(OpenAI 兼容) | 向量化 | `EMBED_API_BASE/KEY/MODEL`,`EMBEDDING_DIM` |
| (可选)VL 多模态 | PDF 图表理解 | `VL_API_BASE/KEY/MODEL` |
| (可选)MinerU | PDF 高保真解析(不可达时自动回退 pypdf 文本层) | `MINERU_API_TOKEN` |

全部配置走环境变量(见 `.env.example` 的 KB 段),无硬编码。

## 部署

```bash
cp .env.example .env            # 填 KB 段(Milvus/MinIO/PG/LLM/Embed)
docker build -f Dockerfile.kb -t supersonic-kb-rag .
docker run -d --name kb-rag --network host \
  -e PORT=9200 --env-file .env \
  -v $PWD/data:/app/data \
  --restart unless-stopped supersonic-kb-rag
curl http://localhost:9200/health     # {"ok": true}
```

页面: `/import.html` 导入文档 · `/chat.html` 知识问答 · `/docs` Swagger

## 导入文档

```bash
# API 方式
curl -X POST http://localhost:9200/api/import/upload -F "file=@报告.pdf"
curl http://localhost:9200/api/import/docs     # 文档清单(total_docs/total_chunks)
# 或打开 /import.html 拖拽上传
```

管线自动完成:PDF→MD(文本层回退)→ 主体识别 → 语义切片 → Embedding → 入库。

## 注册为 SuperSonic 插件

```bash
export SUPERSONIC_BASE=http://127.0.0.1:9080
export SUPERSONIC_PASS='你的密码(协议同构加密值)'
export KB_PLUGIN_URL=http://<decision-service>:9102  # 推荐指向拦截+排版层;容器回调勿写127.0.0.1
python scripts/setup_kb_plugin.py        # 注册「知识库问答」插件 + 「知识库助手」助理
```

### 插件契约(/api/plugin/kb)

- 请求:`POST <url>?queryText=<用户问题>`(SuperSonic 自动带 chatId)
- 响应:标准 `{"columns": [], "resultList": [], "data": "<答案文本>"}`
  - 答案含引用标记与 `**引用来源:**` 列表;拒答时 `kb: true` 且文本礼貌引导
  - 数据意图前置拦截:业务数据问题返回引导文本(不编造数据)

### 双助理(推荐形态)

| 助理 | 插件 | 能力 |
|---|---|---|
| 问数助手 | 决策分析(本体建模) | 指标/分组/同环比/归因 |
| 知识库助手 | 知识库问答(JEV+RAG) | 文档咨询/引用/拒答 |

也可单助理双工具并存(拦截逻辑自动分流)。

## 直连 API(不经 SuperSonic)

```bash
curl -X POST http://localhost:9102/api/kb -H "Content-Type: application/json" \
  -d '{"question": "xx活动规则是什么"}'
```

decision-service(`kb.py`)提供转发 + 引用列表化排版 + 追问深链;
`/api/plugin/kb` 在 decision-service 侧提供同能力插件契约。

## 已知边界

- SuperSonic 对**新建插件**的 embedding 召回存在上游不稳定 → 见 `KNOWN_ISSUES.md #6`
- MinerU 不可达时 PDF 走 pypdf 文本层(表格/图片保真度下降,可后补高保真重导)
- 联网搜索默认关闭(内部知识库场景,防不可信外部信息)
