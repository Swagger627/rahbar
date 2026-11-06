# ADR 0001: The indexer is a Job, and it swaps in a finished index

## Status
Accepted · 2026-11-04

## Context
Building the index means extracting 247 PDF pages (41 of them by OCR), chunking them and
embedding about 400 chunks. That takes minutes and a lot of memory. Answering a question
only needs to read the finished index. If the API built the index itself, when it starts:
- three API replicas would write to the same Chroma collection at once, and leave an index
  that no single run produced;
- every restart, rolling update or OOM kill would pay the full indexing cost again;
- readiness would be wrong: either a pod is "ready" with an empty index, or it is still
  indexing when Kubernetes gives up on it;
- re-indexing would need a redeploy.

The API keeps serving while a re-index runs, so the re-index must not break the index the
API is reading.

## Decision
- **One writer.** Indexing runs as a Kubernetes Job (`restartPolicy: OnFailure`,
  `backoffLimit: 2`), and the same image runs weekly as the CronJob `reindex`. It runs once
  and exits. `indexer/pipeline/store.py` is the only code that writes to Chroma. The API
  only reads, and the NetworkPolicy lets only the api and the indexer reach Chroma.
- **Re-index without a redeploy:**
  `kubectl create job --from=cronjob/reindex manual-reindex-1 -n rahbar`.
- **Build beside, then swap.** Each run writes a complete new collection,
  `rahbar-staging`, and checks that it holds one row per chunk. Only then does it delete
  the old `rahbar` and rename staging to `rahbar`. If anything fails earlier, the old index
  is untouched and the Job exits 1, so Kubernetes retries it.
- **Pin recorded.** The collection metadata holds `embed_model` and the real vector
  `dimension`, so the API can refuse to start against an index built with another model
  (section 4.4).

## Options considered
- **Index inside the API at startup, or in an init container.** Rejected for the four
  reasons above.
- **Delete the old collection first, then rebuild it.** This is the simplest, but the API
  would serve an empty or half-built index for minutes, and a failed run would leave
  nothing at all.
- **Upsert into the existing collection.** Rows of deleted chunks would stay behind, and a
  model change would mix vectors from two models in one collection.

## Consequences
- Easier: a failed re-index never hurts the running service, a model change can never mix
  vectors, and repeated runs give the same result.
- Harder: for a few milliseconds between the delete and the rename, no `rahbar`
  collection exists, so a question asked in exactly that instant fails once. Staging
  briefly needs room for two copies of the index (about 400 rows here, which is small).
- To watch: after a re-index the semantic cache must be cleared (section 3.3, not built
  yet), or old answers keep being served. The API's readiness check (non-empty
  collection, matching `embed_model`) is also still to be built.
