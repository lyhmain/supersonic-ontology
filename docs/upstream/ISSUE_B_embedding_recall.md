# [Bug] Plugin embedding recall: stale vectors after PUT/delete + new plugins may never be recalled

## Summary

While building WebService plugins around SuperSonic (standalone 1.0.0-SNAPSHOT,
official image, zero source modification), we found the plugin embedding-recall
pipeline (`EmbeddingRecallRecognizer` → `PluginManager.embeddingRetrieval`) has
several failure modes that silently break chat routing:

## Reproduced failure modes

### 1. Stale vectors on plugin update (PUT)

`PUT /api/chat/plugin` triggers `requestEmbeddingPluginDelete/Add`, but in our runs
the re-built vectors for the plugin were **byte-identical to the pre-update values**,
even after changing `pattern`/`examples` via the official API. Suspected server-side
embedding cache or batch-write misalignment: stored `text` is the new example, the
`embedding` column holds a vector of a *different* text (verified via pgvector
cosine: our example's stored vector had similarity 1.0000 to an unrelated plugin's
example, and ≈0.86 to its own sibling example).

### 2. Vector rows outlive the plugin (DELETE returns 500)

`DELETE /api/chat/plugin` fails with HTTP 500 in our environment, while its rows in
`preset_query_collection` remain. These orphaned vectors then compete in top-N
recall: with `s2.embedding.nResult` defaulting to 1, the winner (highest similarity)
may belong to a plugin **not bound to the current agent** — the recognizer iterates
`pluginMap.get(id)` and `continue`s on null, leaving the agent with zero candidates
and an empty chat answer.

Mitigation we shipped: raise `s2.embedding.nResult` (we run 10) and purge orphaned
rows from `preset_query_collection` (rows are identifiable via
`metadata.queryId = pluginId*1000 + seq`).

### 3. Newly created plugins may never enter recall

After `POST /api/chat/plugin` (pattern + examples set, agent bound via official API),
chat parsing persistently returns `EmbeddingRecallRecognizer resolve: (false,[])`.
Vector rows for the new plugin **do exist** in `preset_query_collection` (embedding
column non-NULL, text correct), agent `toolConfig` binding is confirmed in logs
("plugins which can be supported by cur agent: <agent> [<new plugin>]"), yet retrieval
never surfaces them. DB-side `parse_mode` changes and restarts do not help; the same
query text recalls fine under an agent bound to an older plugin, so the vector store
and search path work — it is the plugin-id mapping / recall surface that misses.

## Environment

- tencentmusic/supersonic standalone latest (1.0.0-SNAPSHOT), official Docker image
- PostgreSQL metadata + built-in pgvector storage (`preset_query_collection`)
- Plugin type: WEB_SERVICE, containsAllDataSet, agent bound via toolConfig

## Expected behavior

- PUT should recompute (and *change*) plugin example embeddings, or at minimum expose
  a force-rebuild hook.
- DELETE should cascade `preset_query_collection` rows in the same transaction.
- Recall top-N filtering should never silently drop the only bound plugin; a warning
  log ("bound plugin X has no recallable vectors") would save hours of debugging.

## What we'd love

If maintainers can point to where `requestEmbeddingPluginAdd` computes/stores
embeddings for WEB_SERVICE plugins, we're happy to validate a fix and contribute
a regression test + a health-check script (we already have one that detects
NULL/misaligned vectors by self-similarity).
