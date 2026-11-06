# ADR 0003: The semantic cache lives in Redis, keyed by the index version

## Status
Accepted · 2026-11-04

## Context
Answering one question takes about 2-3 s and one or two calls to the language model.
Students ask the same things in slightly different words. The assignment asks for a
semantic cache (section 3.3):
- reuse an answer when a new question is close enough;
- choose the threshold with data;
- never cache abstentions or anything that failed validation;
- expire the cache when the index is rebuilt.

The API runs as three replicas, so the cache must be shared. Redis is already part of the
deployment, with a PVC, and `redis-py` is already a dependency.

## Decision
- **Store:** plain Redis, one key per cached answer, with a time-to-live (one hour). Each
  value holds the question, its vector, its key terms, and the response without the
  request id. Lookup reads the entries of the current index (`SCAN` + `MGET`) and compares
  vectors in Python.
- **Hit rule:** cosine similarity >= `CACHE_THRESHOLD` (0.90, measured in
  `docs/PIPELINE.md`) **and** the same numbers, grades, capital codes and history words.
- **Invalidation:** the key contains the embedding model and the Chroma collection id. The
  staged re-index always creates a new collection (ADR 0001), so after a re-index old
  answers are simply never looked up, and their time-to-live removes them.
- **Only safe answers are stored:** given from the documents and passing the citation
  check. Redis errors never fail a request: it runs the normal pipeline instead.

## Options considered
- **A second Chroma collection for cached questions.** That would give a real vector
  search, but Chroma has no per-entry expiry, and the API would have to *write* to Chroma.
  ADR 0001 keeps the indexer as Chroma's only writer.
- **Redis Stack / RediSearch vector index.** That is faster for large caches, but needs a
  different Redis image and a new module, which is too much for a few hundred entries.
- **Flushing the cache from the indexer Job.** The indexer would need Redis access and a
  NetworkPolicy change, and a failed flush would leave stale answers. A new key per index
  version needs neither.
- **Similarity only, no key-term check.** Rejected by measurement. "Old rule" vs "current
  rule" (0.955), "B-" vs "B+" (0.943) and "15 days" vs "30 days" (0.930) scored above most
  real paraphrases.

## Consequences
- Easier: replicas share answers; a re-index or a model change can never serve stale or
  incompatible answers; a Redis outage only costs speed.
- Harder: lookup cost grows with the number of entries (fine for hundreds, not for
  millions). A wrong answer that passed the checks is repeated until it expires.
- To watch: the hit ratio (`rahbar_cache_lookups_total`), and whether real users' questions
  produce a false hit that the test pairs missed. If one does, raise the threshold, or add
  the deciding word to the key terms.
