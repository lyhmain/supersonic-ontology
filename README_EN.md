# supersonic-ontology

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/lyhmain/supersonic-ontology)](https://github.com/lyhmain/supersonic-ontology/releases)
[![SuperSonic](https://img.shields.io/badge/works%20with-SuperSonic-blue)](https://github.com/tencentmusic/supersonic)
[![中文 README](https://img.shields.io/badge/README-%E4%B8%AD%E6%96%87-red)](README.md)

**SuperSonic dual plug-ins: Ontology-driven NL2Analytics + JEV+RAG Knowledge Base.**
Two sidecar services that turn [SuperSonic](https://github.com/tencentmusic/supersonic)
into a production-grade decision engine — **zero upstream source modification**.

| Plug-in | Shape | Capabilities | Entry |
|---|---|---|---|
| **Analytics** (ontology-driven NL2Analytics) | this repo's core | metrics / group-by / TopN / YoY-MoM / holiday calendar / attribution drill-down / LLM insight — with acceptance-test gates | `/api/nl` `/api/plugin/analyze` |
| **Knowledge Base** (JEV+RAG) | `kb_rag/` + `web/` | document Q&A with `[n]` citations, refuse-to-answer gating, data-intent interception | `/api/kb` `/api/plugin/kb` |

**The idea:** take correctness *out* of the LLM's hands. Semantic ontology is
configuration, business calibers are exactly reconciled against the warehouse,
aggregation answers return in ~0.2s, and a curated benchmark must pass
("all green") before any profile change goes live. The LLM only does what it is
good at — one sentence of insight on top of facts. For knowledge Q&A, a JEV gate
(in-scope / out-of-scope / unclear) refuses to fabricate: no evidence, no answer.

⭐ **If this helps you, please star the repo** — it keeps the project alive.

## Quick start

```bash
cp .env.example .env                                   # your Doris / LLM endpoints
cp examples/profiles/demo_tea.json profiles/           # demo ontology (tea-shop)
docker exec -i <doris-container> mysql -uroot -P9030 < examples/demo_tea.sql   # demo data
docker compose up -d
open http://localhost:9102/ui
```

Management console (`/ui`): table picker → auto ontology draft → calibers & acceptance
expectations → benchmark all-green → go live. No code required.

### Hook into SuperSonic chat

```bash
export SUPERSONIC_BASE=http://127.0.0.1:9080
export SUPERSONIC_PASS='<encoded password>'
export ONTOLOGY_URL=http://127.0.0.1:9102
python scripts/setup_plugin.py         # registers the Analytics plug-in + agent

docker build -f Dockerfile.kb -t supersonic-kb-rag .
docker run -d --network host -e PORT=9200 --env-file .env -v $PWD/data:/app/data supersonic-kb-rag
export KB_PLUGIN_URL=http://127.0.0.1:9102
python scripts/setup_kb_plugin.py      # registers the KB plug-in + agent
```

Both scripts use official REST APIs only and ship safety rails: `--plugin-name`
namespacing, URL-mismatch rejection (anti cross-wiring across instances), exact
agent binding.

## Why it is reliable

- **Ontology profiles** (JSON, multi-tenant): metric synonyms, caliber filters
  (e.g. refunds excluded), derived metrics (ARPPU = GMV ÷ payers), dimension value
  tokens ("West Lake store" → point lookup), business holiday calendar.
- **Deterministic NL routing**: single value / group-by / TopN / HAVING / YoY-MoM
  (rolling + explicit baseline) / daily series / multi-metric / attribution — all
  parameterized SQL against the warehouse, never LLM-generated.
- **Acceptance gates**: `q → expected value/rows/action` cases run on every save;
  all-green is the deployment barrier. Benchmark: 26/30 exact-match on real
  business data, 0.02s per question.
- **Bad-case loop**: empty results / unmatched metrics auto-captured → annotated →
  one click to convert into acceptance cases.
- **Attribution that adds up**: dimension-contribution decomposition whose total
  exactly equals the attributed metric (verified invariant).

## Knowledge base (JEV+RAG)

LangGraph pipelines: import (PDF→MD, item-name recognition, semantic chunking,
embedding, Milvus) and query (JEV gate → HyDE → vector search → fusion → rerank →
generation with `[n]` citations → refusal when evidence is insufficient).
Data-intent interception redirects metric questions to the Analytics plug-in
instead of guessing numbers from documents. See [docs/INTEGRATION_KB.md](docs/INTEGRATION_KB.md).

## Docs

- [docs/SPEC.md](docs/SPEC.md) — **standard spec** (a Chinese four-clause sheet; compiles to a full profile)
- [docs/ONTOLOGY_SPEC.md](docs/ONTOLOGY_SPEC.md) — full profile schema
- [docs/INTEGRATION_SUPERSONIC.md](docs/INTEGRATION_SUPERSONIC.md) — chat integration (analytics)
- [docs/INTEGRATION_KB.md](docs/INTEGRATION_KB.md) — chat integration (knowledge base)
- [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) — benchmark & acceptance reports
- [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) — upstream pitfalls with root causes (also filed upstream: tencentmusic/supersonic #2449, #2450)

## License

Apache-2.0
