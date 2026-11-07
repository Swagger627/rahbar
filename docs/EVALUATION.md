# Evaluation on the real corpus (Step 5.2, 2026-11-06)

This is the final measurement of RAHBAR on the **real** university documents, through the
live minikube Ingress. It is also where `eval/baseline.json` comes from.

There are **two separate baselines**. Do not mix them up:

| File | Corpus | Used by | Made by |
| --- | --- | --- | --- |
| `eval/baseline.json` | the **real** 7 university PDFs, 431 chunks | the section 5.4 gate when you run `make eval` against minikube | cold run 1 below |
| `eval/fixtures/baseline.json` | 3 **fictional** fixture documents, 11 chunks | the `e2e-eval` job in `ci.yml` (kind cluster on GitHub) | the first real CI e2e run, 2026-11-05 (`eval/fixtures/README.md`) |

## What was measured

| Item | Value |
| --- | --- |
| Date | 2026-11-06 (UTC), commit `e0142c6` and the api images `rahbar-api:sec` / `rahbar-api:5.2` (same dependencies; 5.2 adds the hosted provider and metrics) |
| Corpus | 7 PDFs, SHA-256 verified (`python -m pipeline.run --verify-corpus`: `"corpus verified", "files": 7`) |
| Index | collection `rahbar` on the minikube Chroma PVC: **431 chunks**, `nomic-embed-text`, dimension 768, built by the indexer Job on 2026-11-05 (`/readyz` confirmed `rows: 431`) |
| Embedding model | `nomic-embed-text:latest`, 137M, F16, Ollama digest `0a109f422b47` |
| Answer model | `llama3.2:3b`, 3.2B, Q4_K_M, Ollama digest `a80c4f17acd5`, temperature 0.2 |
| Ollama | 0.40.14, on the laptop (`ollama-host` mode) |
| Questions | `eval/golden.yaml`, 16 questions: 8 ordinary, 2 version-sensitive, 3 exact-term, 2 must-abstain, 1 prompt injection |
| Path | the real UI's route: `POST /api/ask` through the ingress-nginx controller (`Host: rahbar.local`, via `kubectl port-forward`), then 3 api replicas, Chroma, Redis and Ollama |
| Settings | top_k 5, relevance floor 0.65, chunks 400/50, cache threshold 0.90 |

**Cold means cold.** Redis was emptied (`redis-cli FLUSHALL` in the redis pod) before every
cold run, and every response in the full-answer run carried `X-Cache: miss`. So the cache
could not hide a wrong answer.

## Results

### Automatic metrics (the section 5.4 gate)

Five cold runs, all through the Ingress:

| Run | api image | recall@3 | recall@5 | citation validity | abstention correctness | answerable questions declined | gate |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 (wrote `eval/baseline.json`, `eval/results.json`) | sec | 1.0 | 1.0 | 1.0 (11 citations) | 1.0 | 5: o01, o07, v01, v02, i01 | passed |
| 2 | sec | 1.0 | 1.0 | 1.0 (13) | 1.0 | 2: o07, i01 | passed |
| 3 (full answers saved, reviewed by hand) | sec | – | – | – | 2/2 abstained | 3: o01, o07, i01 | – |
| 4 | 5.2 | 1.0 | 1.0 | 1.0 (16) | 1.0 | 1: o07 | passed |
| 5 | 5.2 | 1.0 | 1.0 | 1.0 (14) | 1.0 | 2: o07, i01 | passed |

- **Retrieval:** recall@5 by type was 1.0 for ordinary, version, exact and injection in every
  run. Retrieval is deterministic: the same chunk ids came back each time.
- **Citations:** "citation validity" is **automatic**. Every citation matched the source,
  section and page of a chunk retrieved for that request. It does *not* check that the cited
  text supports the claim; the manual review below does.
- **Abstention:** a01 and a02 (the hostel questions the corpus deliberately lacks) were
  declined in every run.
- **Prompt injection (i01):** the model never obeyed the planted instruction. It never said
  attendance is optional. But it declined to answer the real question in 4 of 5 runs, which
  is safe but unhelpful.

Raw output: `docs/evidence/eval-2026-11-06/` (console output and `results.json` of every
run).

### Manual review of the answers (run 3, all 14 answerable questions)

Every answer was read against the exact text of the chunk(s) it cited, fetched from the
cluster's Chroma.

| Result | Questions | Count |
| --- | --- | --- |
| Correct, and the cited passage states it | o02 (2.67), o03 (6 years), o05 (LHR), o06 (more than 1 year), v01 (subsequent semester), v02 (2023 rule, labelled by the question), e01 (100%), e02 (2.00), e03 (Rs. 12,000) | **9 / 14** |
| **Wrong answer** | o04: said **60%** for withdrawal within 15 days. The cited chunk's table says "Upto 15th day ... 80% fee refund" (60% is the 20-day row) | 1 |
| **Mis-cited** | o08: the answer is clause 8.6 of the *Academic Honesty* section (chunk `academic-rules-handbook-jun-2026-0042`, retrieved but **not cited**). It cited the code-of-conduct introduction and a contents-page line instead. The automatic check passed it, because both cited chunks were retrieved | 1 |
| Wrongly declined | o01, o07, i01: in each case the chunk with the answer was retrieved (o01's at rank 1, score 0.78) | 3 |

So with recall@5 = 1.0:
- **answer correctness is 9 of 14 answerable questions (64%);**
- among the 11 questions actually answered, 9 were right and properly cited (82%);
- citation support, checked by hand, holds for 9 of 11 answers;
- the automatic citation validity is 100%.

### The model's variability

`llama3.2:3b` at temperature 0.2 is not deterministic:
- The number of answerable questions it declined ranged from **1 to 5** across the five cold
  runs, on identical retrieved passages.
- o07 was declined in every run. o01, v01, v02 and i01 came and went.
- In Step 5.1, the same question was answered from the documents once and declined another
  time.
- At temperature 1.2 the noise grows further (experiment F, `docs/FAILURE-ANALYSIS.md`).

The gate therefore asserts on retrieval, citation format and abstention, which are stable,
and not on the model's words.

### Latency (cold vs cached)

| Path | Measured |
| --- | --- |
| Cold, model called (runs 1 and 2, 30 answers) | median **1.69 s**, from 0.97 s to 8.11 s (the first question pays the model load) |
| Abstention below the relevance floor (a02, no model call) | 0.07-0.10 s |
| Cached (warm run straight after run 1) | **0.03-0.06 s** for the 8 answers in the cache (o03, o04, o05, o06, o08, e01, e02, e03) |
| Regenerated, not served from the cache | o01, o02, o07, v01, v02, a01, i01 (0.7-4.7 s). Six of them had been declined in run 1, and abstentions are never cached: o01, o07, v01, v02, a01 and i01. o02 had been answered in run 1; why it was not reused was not recorded |

During cold run 1, the api pods used at most **7m CPU and 78 MiB**
(`docs/evidence/eval-2026-11-06/kubectl-top-during-cold-run1.txt`). The time is spent in
Ollama, not in the pods.

## How to reproduce

1. Start the cluster and port-forward the Ingress (README, "Run it on minikube").
2. Check the index is the real one:
   `kubectl --context minikube -n rahbar exec deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz').read().decode())"`
   should show `rows: 431`.
3. Empty the cache, so the run is cold:
   `kubectl --context minikube -n rahbar exec deploy/redis -- redis-cli FLUSHALL`
4. Run the evaluation with the gate:
   ```bash
   python eval/run_eval.py --api http://127.0.0.1:18081 --host rahbar.local \
     --golden eval/golden.yaml --baseline eval/baseline.json --out eval/results.json
   ```
   It exits 0 when the gate passes, and 1 with the reason when it fails. Use
   `--update-baseline` only after a deliberate improvement; it refuses unless every gate
   passes.

## Limits of this evaluation

- **Few questions.** 14 answerable questions: one question is worth 7.1 percentage points.
- **Coarse recall matching.** recall@k matches the document and section heading, not the exact
  chunk. o08 counts as found through a different "CODE OF CONDUCT" chunk.
- **Generation quality is not gated.** The manual review (one run) is the only correctness
  check, and the declines vary from run to run.
- **One model.** All numbers are for `llama3.2:3b` on this laptop. The hosted provider has not
  been evaluated against a real cloud model.
