# Failure analysis

Section 5.5 asks us to break four of the six RAG stages on purpose, one at a time. For
each one we record the symptom, the diagnosis, the stage and the remedy. We broke:

- **A** · Extraction
- **C** · Retrieval
- **E** · Source
- **F** · Generation

All four were run on 2026-11-06, with:
- the real university corpus (7 PDFs, SHA-256 verified) and the real 431-chunk index;
- `nomic-embed-text` for embeddings and `llama3.2:3b` for answers, through Ollama.

Each failure was put back before the next one started. The raw output of every command is in
`docs/evidence/`.

Nothing real was damaged:
- experiments A and E changed no repository file (they used a one-off wrapper or launcher);
- the broken index in A went into a throwaway Chroma container;
- C and F were configuration changes on the live minikube cluster, and were rolled back.

**Not broken:**
- **B (chunking):** it was measured instead. The 200/25, 400/50 and 800/100 comparison is in
  `docs/PIPELINE.md` and ADR 0002.
- **D (prompt):** not done.

From the RAG lecture: only F is the model's fault, so check A to E first. Our results agree.
- **A and E** moved the retrieval numbers and failed the CI gate.
- **C** was stopped by a guardrail before users noticed.
- **F** passed the gate and showed up only in the generation counters and in the answers.

| Stage | What we broke | Did the CI gate catch it? | The number that showed it |
| --- | --- | --- | --- |
| A · Extraction | indexed the scanned PDFs without OCR | **yes:** recall@5 100% → 85.7% | `/readyz` rows 431 → 381; version-question recall 1.0 → 0.5 |
| C · Retrieval | `EMBED_MODEL` changed on the API only | not needed: the pod never became ready | new pod 0/1 READY; `/readyz` 503 "embedding model mismatch" |
| E · Source | removed the superseded filter | **yes:** recall@5 100% → 92.9% | exact-question recall 1.0 → 0.67; 2023 chunk ids in `retrieved` |
| F · Generation | temperature 0.2 → 1.2 | **no:** every gate number stayed 1.0 | retries 1 → 4, fallback 0 → 1, output tokens +44% |

---

## Stage A · Extraction: the scanned PDFs indexed without OCR

**Evidence:** `docs/evidence/failure-A-extraction-no-ocr-2026-11-06.txt`

- **What we changed.** We ran the real indexer image (`rahbar-indexer:dev`, the patched
  build) over the real corpus. A one-off wrapper replaced `pipeline.extract._ocr_page` with
  a function that returns `""`, exactly as if no OCR engine were installed.
  - The broken index went into a throwaway Chroma (no volume, port 18100).
  - The real API code ran on the laptop against that index, with no Redis, so nothing was
    cached.
- **Symptom.**
  - The dry run showed the scanned documents almost disappearing:
    - `undergraduate-academic-rules-aug-2023.pdf`: 24 of 26 pages need OCR. It went from
      38 chunks to **2** (21 tokens).
    - `faculty-entrepreneurship-and-technical-start-ups-policy.pdf`: 8 of 8 pages need OCR.
      It went from 13 chunks to **0**.
    - Total: **381 chunks instead of 431.**
  - The indexer still finished with exit code 0, and the API reported ready (`rows: 381`).
  - The golden set: **recall@5 fell from 100% to 85.7%, and the gate printed
    `GATE FAILED - recall@5 fell 14.3 points`**. By type, version questions dropped to 0.5
    and ordinary to 0.875.
  - o06 (start-up eligibility, from the scanned policy) and v02 (the 2023 rule) went to
    recall 0, and both were answered "The documents I have do not cover this question".
- **Diagnosis, as if we had not caused it.**
  - Nothing crashed, so the logs stay quiet. The numbers do not:
    1. `/readyz` reports `rows: 381` against the 431 we expect.
    2. The indexer's per-document lines (`"chunked", "source": ..., "chunks": 0`) show two
       documents with 0 or 2 chunks.
    3. `run_eval.py` shows the lost recall on exactly the questions whose answers live in
       scanned files.
  - The tell-tale pattern: **recall lost by document, not by question type**.
  - Root cause: the extraction stage produced no text for image-only pages. Every later stage
    (chunking, embedding, retrieval, generation) worked correctly on missing input.
- **Remedy.** Index with OCR on, which is our normal pipeline (`extract.py` OCRs any page
  with fewer than `MIN_LETTERS_FOR_TEXT_LAYER` letters, at 300 dpi with tesseract
  `--psm 4`).
- **Verification.** The same API, pointed at the index built with OCR (`rahbar-chroma-real`):
  - `rows: 431`;
  - recall@5 back to 1.0, with version and ordinary questions both 1.0;
  - gate passed, and o06 and v02 answered again.
  - A fresh OCR re-index was not repeated during the experiment: it peaks at about 1.1 GiB,
    and the laptop had about 1.2 GB free. The 431-chunk index it produces was already built
    and verified in Steps 4.1 and 4.3.
- **Guard to add next:** the indexer could warn when a document yields fewer than N tokens
  per page.

## Stage C · Retrieval: `EMBED_MODEL` changed on the API only

**Evidence:** `docs/evidence/failure-C-retrieval-embed-model-2026-11-06.txt`

- **What we changed.** On the live minikube cluster:
  `kubectl -n rahbar set env deploy/api EMBED_MODEL=all-minilm`. The ConfigMap and the
  indexer kept `nomic-embed-text`. This is the section 4.4 demonstration as well.
- **Symptom.**
  - The rolling update started one new pod, which stayed **`0/1 Running`**.
  - `kubectl rollout status` timed out with "1 out of 3 new replicas have been updated".
  - Users saw nothing wrong. With `maxUnavailable: 0`, the three old pods kept serving: an
    availability probe through the Ingress got **235 answers and 0 failures** during the
    experiment.
- **Diagnosis, as if we had not caused it.** Status, then events, then logs:
  1. `kubectl get pods` shows the new pod at 0/1.
  2. `kubectl describe pod` ends with
     `Readiness probe failed: HTTP probe failed with statuscode: 503`, repeated 11 times in
     95 s.
  3. The pod's `/readyz` answers
     `503 {"status":"not ready","reason":"embedding model mismatch: the index was built with 'nomic-embed-text' but EMBED_MODEL is 'all-minilm'"}`.
  4. Its log has the ERROR line `"index check failed; not ready"` with the same reason.
- **Root cause.**
  - **The mismatch:** the question vectors would come from a different model, with a
    different vector space (and a different dimension), than the stored chunk vectors.
  - **What stops it:** `indexer/pipeline/store.py` (lines 47-48) writes `embed_model` and
    `dimension` into the collection metadata, and
    `api/app/retrieval/chroma.py:143-145` refuses to serve when they differ.
- **Without the guardrail:** the pod would have become ready and sent garbage-similarity
  retrievals. The metric that would have shown it is recall@5 collapsing in `run_eval.py`,
  or Chroma rejecting the query because of the wrong vector length.
- **Remedy.** `kubectl -n rahbar rollout undo deploy/api`.
- **Verification.**
  - 3/3 pods ready, with no `EMBED_MODEL` override in the Deployment;
  - `/readyz` shows `rows: 431, embed_model: nomic-embed-text, dimension: 768`;
  - a question through the Ingress answered "Rs. 12,000" with its citation.

## Stage E · Source: the superseded filter removed

**Evidence:** `docs/evidence/failure-E-source-superseded-filter-2026-11-06.txt`

- **What we changed.**
  - The real API code ran on the laptop against the real 431-chunk index, with no Redis.
  - A one-off launcher patched `ChromaRetriever._search` in memory so that every search
    ignores the `superseded` filter.
  - Two variants:
    1. filter removed, but the `[SUPERSEDED: ...]` label the retriever puts on old passages
       is kept;
    2. filter and label both removed.
  - The version-sensitive question was v01, "If I fail a course, when do I have to register
    for it again?", asked 3 times per variant:
    - **2026 rule (current):** register again in the subsequent semester;
    - **2023 rule (superseded):** whenever the course is offered again.
- **Symptom.**

  | Variant | v01, 3 tries | 2023 chunks retrieved | Gate |
  | --- | --- | --- | --- |
  | healthy | "subsequent semester" ×2, 1 refusal | none | passed |
  | filter removed (label kept) | **"Whenever that course is offered again" ×3** (the 2023 rule) | 2023-0018, 2023-0028 | **failed: recall@5 92.9%** |
  | filter and label removed | **the 2023 rule ×3, stated as the current rule** | same | **failed: recall@5 92.9%** |

  Two findings:
  - The label alone did not stop `llama3.2:3b`. One answer even attached the old rule to a
    2026 section ("4. Registration", p. 19). **The filter is the defence that works.** The
    label and prompt rule 5 are only a second line.
  - The exact-clause question e01 ("clause 1.21") lost its recall (exact recall 1.0 →
    0.67). The 2023 rules also contain a clause 1.21: the exact-clause lookup put
    `undergraduate-academic-rules-aug-2023-0009` first, pushing out the 2026 clause.
- **Diagnosis, as if we had not caused it.**
  - **What made it look fine:** citation validity stayed 1.0, because the 2023 chunks really
    were retrieved.
  - **What showed it:**
    1. the per-request log line (`chunk_ids`) and the response's `retrieved` list contain
       `undergraduate-academic-rules-aug-2023-*` ids for a question that never mentioned an
       old edition;
    2. the eval's recall@5 by type shows the exact-term drop;
    3. a manual look at the answer shows an old rule presented as current.
- **Remedy.** Restore the filter: the unpatched retriever, as deployed.
  `api/app/retrieval/chroma.py:73-74` searches `superseded=False` unless the question asks
  about history.
- **Verification.** The healthy API again:
  - v01 answered "You have to register for the course again in the subsequent semester."
    **3 out of 3**, with no 2023 chunks retrieved;
  - recall@5 1.0, gate passed.

## Stage F · Generation: temperature raised to 1.2

**Evidence:** `docs/evidence/failure-F-generation-temperature-2026-11-06.txt`, and the full
answers in `docs/evidence/eval-2026-11-06/failure-F-answers-at-1.2.json`.

- **What we changed.** On the live cluster:
  `kubectl -n rahbar set env deploy/api TEMPERATURE=1.2` (the ConfigMap says 0.2). Redis
  was emptied before every run, so all answers were really generated.
- **Symptom.** The CI gate did not notice: recall@3, recall@5, citation validity and
  abstention correctness all stayed at **1.0, and the gate passed**. The damage is in
  generation. These are the counters summed over the 3 api pods, for one cold run of the 16
  golden questions:

  | Counter (`/metrics`) | Healthy, 0.2 | Broken, 1.2 |
  | --- | --- | --- |
  | `rahbar_generation_retries_total` (replies rejected once) | 1 | **4** |
  | `rahbar_answers_total{outcome="fallback"}` | 0 | 0, then **1** in the second pass |
  | `rahbar_answers_total{outcome="abstained"}` | 3 | **5** |
  | `rahbar_llm_tokens_total{direction="out"}` | 1,784 | **2,569** (+44%) |
  | mean generation time (`rahbar_generation_seconds` sum ÷ count) | 2.07 s | 2.56 s |

  The answers themselves, reviewed by hand (second pass at 1.2):
  - **Fallback:** o01 got the safe fallback ("I could not produce an answer I can back with
    the documents right now").
  - **New wrong refusals:** o02 (B- = 2.67) and v02, both answered correctly in every
    healthy run.
  - **Garbled text:** o05 came out as "LHR (columns: CFD ISB KHI LHR PWR) is under LHR,
    according to Table row: ...", and v01 contains a raw "(Evidence 1.5.8 [1])".
- **Diagnosis, as if we had not caused it.**
  - A to E look healthy here: same retrieved chunks, same recall, valid citations.
  - Only the generation numbers moved: retries, fallbacks, abstentions on answerable
    questions, output tokens and generation latency.
  - Then check the effective configuration:
    `kubectl get deploy api -o jsonpath='{...env[?(@.name=="TEMPERATURE")]}'` shows the
    override.
  - Root cause: high temperature makes `llama3.2:3b` sample unlikely tokens. Its JSON and
    citations fail our checks more often (hence the retries and the fallback), and the
    answers drift.
- **Remedy.**
  1. `kubectl -n rahbar set env deploy/api TEMPERATURE-` removes the override, back to 0.2
     from the ConfigMap.
  2. Then `redis-cli FLUSHALL`, so no answer generated at 1.2 is served later from the
     cache.
- **Verification.** A cold run at 0.2 on the new pods:
  - gate passed;
  - **0 fallbacks**, 2 retries;
  - output tokens 1,742;
  - 1.68 s per generation.
- **Honest limit:** run to run, `llama3.2:3b` at 0.2 already varies. Across healthy runs, 2
  to 5 answerable questions were declined (see `docs/EVALUATION.md`). So the abstention
  increase alone is not proof. The fallback, the +44% output tokens and the garbled answers
  are the clear signs.

## What we learned

- **The gate measures retrieval and citation format, not answer quality.**
  - It caught the two retrieval-side failures (A, E).
  - It missed the generation failure (F). That is why `/metrics` now counts retries,
    fallbacks, abstentions and tokens.
- **Guardrails in code beat rules in documents.** The embedding pin (C) stopped a bad config
  before any user saw it. The superseded filter (E) mattered more than the label we also put
  in the text.
- **Silent failures are the dangerous ones.** In A, the indexer exited 0 and the API reported
  ready. Only the row count and the evaluation showed that two documents were missing.
