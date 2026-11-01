# [Community] supersonic-ontology: a plug-in decision engine for SuperSonic (ontology-driven NL2Analytics + JEV+RAG knowledge base) — zero source modification

## TL;DR

We built two production plug-ins around SuperSonic **without modifying a single line of
upstream source**, and would love to contribute the pattern back to the community:

| Plug-in | What it does | Entry |
|---|---|---|
| **Analytics plug-in** (ontology-driven) | Metrics / group-by / TopN / YoY-MoM / holiday calendar / attribution drill-down / LLM insight, with acceptance test-gates ("all green before go-live") | `/api/nl`, `/api/plugin/analyze` |
| **KB plug-in** (JEV+RAG) | Document Q&A with citation `[n]`, refuse-to-answer gating (no fabrication), and a data-intent interceptor that routes metric questions to the analytics plug-in | `/api/kb`, `/api/plugin/kb` |

Repo: `[repo-url]` *(pending public mirror)*

## Why this pattern may be interesting to SuperSonic users

SuperSonic's native semantic engine covers schema mapping well, but in our production
scenarios we needed:

1. **Exact business-caliber alignment** — e.g. "yesterday" must anchor to the latest
   **complete** warehouse partition, not the calendar day; refunds filtered by business
   rules; derived metrics (ARPPU = GMV ÷ payers) with synonym dictionaries.
2. **Deterministic routing** — LLM decides *insight wording*, not *SQL or numbers*.
   All numbers come from parameterized SQL over the ontology; LLM never sees a blank
   check. P99 latency 0.2s for aggregation questions.
3. **Acceptance-test gates** — a curated 30-question benchmark must pass before any
   profile change goes live (26/30+ stable on real business data), otherwise rollback.
4. **Bad-case loop** — empty results / unmatched metrics are auto-captured, annotated,
   and turned into new acceptance cases.
5. **Complementary KB** — a knowledge plug-in that *refuses* out-of-scope questions
   (JEV gate: in_scope / out_of_scope / unclear) instead of hallucinating, and that
   redirects data questions to the analytics plug-in instead of guessing numbers
   from documents.

## How it integrates (zero source modification)

```
SuperSonic (official image, untouched)
   └── Agent → WebService plug-in → http://<decision-service>:9102/api/plugin/analyze?queryText=...
                                    http://<decision-service>:9102/api/plugin/kb?queryText=...
decision-service (FastAPI, ~3k lines)
   ├── ontology profiles (JSON, per-company multi-tenant)
   ├── Doris/MySQL direct channel for attribution & baselines
   └── kb-rag (LangGraph import/query pipelines, Milvus/MinIO/PG)
scripts/setup_plugin.py      # registers plug-in + agent via official REST API
scripts/setup_kb_plugin.py   # same safety rails (anti cross-wiring, exact binding)
```

Everything is configuration + sidecar deployment. SuperSonic upgrades never break it.

## What we'd love feedback on

- Would the community want `supersonic-ontology` listed under a "community plug-ins"
  section of the docs?
- We hit several upstream pitfalls while doing this (documented with root-cause and
  mitigations) — filed separately: **plugin embedding-recall instability** and
  **seed-account web login encrypt error**. Happy to turn them into PRs if maintainers
  point us at the right places.
