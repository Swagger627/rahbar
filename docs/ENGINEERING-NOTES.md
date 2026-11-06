# Engineering notes

The seven questions from section 7. Each answer points to RAHBAR's own files: the line
numbers are from the code at the end of Step 5.2. The measurements come from the live
minikube cluster and the evidence files in `docs/evidence/`.

## 1. Indexing in API startup: what breaks with 3 replicas and one Chroma volume?

**What breaks:**
- **Three writers race.** `k8s/base/api.yaml:20` runs `replicas: 3`, and there is one Chroma
  volume (`k8s/base/chroma-statefulset.yaml:89`, `volumeClaimTemplates`, `ReadWriteOnce`). If
  each api pod indexed at start-up, three writers would each be half-way through different
  documents in the same collection. The result is an index that no single run produced:
  duplicate or missing chunks, mixed versions.
- **Every restart re-indexes.** A rolling update starts new pods: our `maxSurge: 1` and
  `maxUnavailable: 0` (`api.yaml:26-27`) create a fourth pod at once. An OOMKill or a node
  drain would do the same, and each would pay the full cost again: 247 pages, 41 of them by
  OCR, about 3 minutes and a 1.1 GiB memory peak, measured.
- **Readiness becomes a lie.** Either a pod is ready before its index exists, or it is still
  OCR-ing when the startup probe gives up. Ours allows `failureThreshold: 30 × periodSeconds: 2`
  = 60 s (`api.yaml:71-76`), far less than an index build.

**What prevents it, and where:**
- **The indexer is a Job:** `k8s/base/indexer-job.yaml:15` (`kind: Job`), `:23`
  (`backoffLimit: 2`) and `:29` (`restartPolicy: OnFailure`).
- **The weekly re-index never overlaps itself:** the CronJob has
  `concurrencyPolicy: Forbid` (`k8s/base/reindex-cronjob.yaml:28`).
- **One writer, and a safe swap:** only `indexer/pipeline/store.py` writes to Chroma.
  `write_index` (line 28) builds a complete `rahbar-staging` collection beside the live one
  and swaps it in only after checking it.
- **The API only reads, and refuses an empty index:** `/readyz` refuses an empty collection
  (`api/app/retrieval/chroma.py:106`, `if rows == 0`).
- **Start-up does no indexing at all:** it only checks the index, in a background thread
  (`api/app/main.py:54`).

We learned that last point the hard way. In Step 4.1 the start-up check ran in the
foreground and blocked while Chroma was down. The startup probe failed and the api pods
restarted in a loop. Moving the check to a thread fixed it.

**Re-indexing needs no redeploy:**

```bash
kubectl create job --from=cronjob/reindex manual-reindex-1 -n rahbar
```

ADR 0001 has the full reasoning.

## 2. Readiness and liveness swapped, Chroma slow for 90 seconds: what happens?

**How it is today:**
- **Liveness is `/healthz`** (`api.yaml:77-82`: every 20 s, 3 failures). It only says
  `{"status": "alive"}` and never touches Chroma, Redis or the model
  (`api/app/routes/health.py:11`).
- **Readiness is `/readyz`** (`api.yaml:83-91`: every 10 s, `timeoutSeconds: 3`, 3 failures).
  It checks Chroma, the collection, the embedding pin and Redis, and returns 503 with the
  reason (`health.py:17-30`).

**What happens today if Chroma is slow for 90 s:** after about 30 s (3 × 10 s) all three
pods fail readiness and leave the Service. Users get errors from the Ingress, but **no pod
is restarted**. When Chroma recovers, readiness passes within 10 s and traffic returns. We
saw this behaviour for real:
- In Step 5.1, with Chroma stopped, the API answered 503 *"collection 'rahbar' not found or
  chroma unreachable: ConnectError"*, and "Ask again" worked once Chroma was back
  (`docs/evidence/ui/08-desktop-error-service-not-ready.png`).
- In experiment C, a pod failing readiness stayed alive at 0/1 (`docs/FAILURE-ANALYSIS.md`).

**If the probes were swapped:**
- **Liveness would depend on Chroma.** After 3 × 20 s = 60 s of slowness, the kubelet kills
  **all three** api containers at about the same time. They restart into the same slow
  Chroma, fail again, and go into CrashLoopBackOff with growing back-off delays.
  - What was merely slow for 90 s becomes several minutes of outage.
  - Every restart also redoes the start-up check, adding load to Chroma just when it is
    struggling.
- **Readiness would say "ready" whenever the process is alive.** During the 60 s before the
  kills, the pods stay in the Service and serve requests that fail on retrieval, and
  `maxUnavailable: 0` no longer protects anything.

Readiness protects users; liveness protects the pod. A dependency belongs only in readiness.

## 3. What does a CPU-based HPA measure on a generation service, and what should scale it?

**What it measures:** `k8s/base/hpa.yaml:14-22` scales the api from 3 to 8 replicas at 70%
CPU utilisation, which is usage divided by the request (100m, `api.yaml:102`). With
`ollama-host`, the model runs in Ollama on the laptop, outside the cluster. While a pod waits
for an answer, it is just holding an open HTTP connection. Measured on 2026-11-06:
- **During a cold run of all 16 golden questions** (sampled every 10 s), the three api pods
  peaked at **4-7m CPU**, at most 7% of their request, while answering a question every
  1-8 s (`docs/evidence/eval-2026-11-06/kubectl-top-during-cold-run1.txt`).
- **The HPA itself** read `cpu: 3% (3m) / 70%` (`docs/evidence/kubectl-get-hpa.txt`).

So the CPU HPA measures JSON handling and HTTP work in the API, not demand. It would never
scale on a generation backlog. Even if it did, more api pods would not help, because the
real bottleneck is the single Ollama.

**What should scale it:** a demand signal.
- **In the API:** the number of requests in flight per pod, or the queue wait time.
- **At the model:** latency, for example p95 of `rahbar_generation_seconds`, or tokens per
  second at the LLM backend.

**What we would need to build:**
1. an in-flight requests gauge on `/metrics`. The histograms and counters exist already
   (`api/app/routes/metrics.py`); the gauge does not yet;
2. Prometheus to scrape it;
3. prometheus-adapter (or KEDA), so the HPA can read it through the custom metrics API;
4. and, for generation itself, scaling the model servers (an in-cluster Ollama Deployment
   with GPUs, or a hosted provider), not the thin api pods.

## 4. The indexer's peak memory, its limit, the margin, and the exit code when it is wrong

**Measured peak: 1,117 MiB.** This is cgroup `memory.peak` of the `manual-reindex-3` pod,
read every 2 s with `kubectl exec ... cat /sys/fs/cgroup/memory.peak` during a real re-index
on minikube (`docs/evidence/minikube-2026-11-05.txt:95-96`). The peak comes during OCR of the
scanned PDFs: pdf2image renders each page at 300 dpi before tesseract reads it.

`kubectl top pods` showed at most **663-784 Mi** for the same run. It samples about once a
minute and misses short spikes, so it alone would have suggested a 1 GiB limit was safe.

**Limit and margin:**

| | Value |
| --- | --- |
| Limit | **2 GiB** (`k8s/base/indexer-job.yaml:57`, the same in `reindex-cronjob.yaml:66`) |
| Request | 512 Mi |
| Margin | 2,048 − 1,117 = **931 MiB**: the peak is 55% of the limit |

**Exit code when it is wrong: 137** (128 + SIGKILL, `OOMKilled`). That happened for real: with
the first limit of 1 GiB, the indexer was OOMKilled twice in Step 4.1.

For comparison, the api pods use 74-92 Mi against a 384 Mi limit
(`docs/evidence/kubectl-top-pods.txt`). Answering a question needs far less memory than
building the index.

## 5. recall@5 by question type: where is it worst, and why?

**Today:** on the final system, every type is at **recall@5 = 1.0**:
- ordinary 8, version 2, exact 3, injection 1;
- four cold runs through the minikube Ingress on 2026-11-06 (`docs/EVALUATION.md`).

**Where it was worst: exact-term questions, 0.667**, in the first real measurement
(Step 3.5B, `docs/PIPELINE.md`):
- **e01** ("What does clause 1.21 ... require?"): the right chunk sat at rank 8, with a score
  of 0.643.
- Ordinary questions were 0.875: **o05** (which campus offers BS Civil Engineering) ranked
  19th, inside a 305-token programme list.

**Why embeddings struggle there:**
- **Exact terms:** the meaning of e01 is almost entirely the number "1.21". An embedding
  turns text into a blend of its meaning, and numbers, codes and clause ids carry almost no
  semantic signal. So "clause 1.21" lands near every attendance-or-rules chunk, and the
  exact one does not stand out.
- **Tables:** o05's answer was one tick mark in a long table. Its vector blurred dozens of
  programmes together.

**The techniques that addressed it:**
- **Hybrid retrieval.** Keyword search alongside vectors, the approach from the RAG lecture.
  We measured three versions of it:
  - a keyword re-rank and full BM25 fusion both *lost* recall (0.786-0.857): words like
    "fee" and "campus" are everywhere in this corpus;
  - only a precise exact-clause lookup worked, and it is what we ship. It finds chunks that
    contain the cited clause number with Chroma's `$contains` filter, and puts them first
    (`api/app/retrieval/chroma.py:189`, `_exact_clause_matches`). Exact recall went
    0.667 → 1.0.
- **Structure-aware chunking.** One chunk per table row, with the column names, fixed o05
  (`indexer/pipeline/extract.py` `_name_ticks`, ADR 0002).

**Limits of this number:**
- **Few questions:** with 14 answerable questions, one question is worth 7.1 points.
- **Matching is by document and section, not by exact chunk.** o08 counts as found because a
  "CODE OF CONDUCT" chunk (p. 9) was retrieved, but the clause 21 text (p. 13) was not.
- **Recall is not correctness.** With recall@5 at 1.0, a manual review of one cold run found
  9 of 14 answers correct and properly cited: one wrong number, one mis-citation and three
  wrong refusals (`docs/EVALUATION.md`).

## 6. EMBED_MODEL changed on the API only: what does your system do?

**What it does:** it refuses to serve, and it says why. We did exactly this on the live
cluster (experiment C, `docs/evidence/failure-C-retrieval-embed-model-2026-11-06.txt`):

```bash
kubectl -n rahbar set env deploy/api EMBED_MODEL=all-minilm
```

- **The new pod stayed `0/1 Running`.** `/readyz` answered
  `503 {"reason": "embedding model mismatch: the index was built with 'nomic-embed-text' but EMBED_MODEL is 'all-minilm'"}`.
  The log had `"msg": "index check failed; not ready"` with the same reason.
- **The rollout stalled.** With `maxUnavailable: 0`, the three old pods kept serving: 235
  probes through the Ingress, 0 failures.
- **`kubectl rollout undo` restored it.**

**The guardrail:**
1. **The indexer records the model.** It writes `embed_model` and `dimension` into the
   collection metadata (`indexer/pipeline/store.py:47-48`).
2. **The API checks it before serving.** It compares the metadata with its own `EMBED_MODEL`
   (`api/app/retrieval/chroma.py:142-145`). It also checks that a stored vector really has
   the recorded dimension (`chroma.py:110-112`).
3. **The cache is separated by model too.** Each cache entry is namespaced by the embedding
   model and the index version (`api/app/services/cache.py:42`). A different model can never
   reuse another model's cached answers.

**Without the guardrail:**
- if the new model has a different vector size, every question fails at Chroma;
- if it happens to share the size, the pod becomes ready and silently retrieves passages
  that are close in the wrong vector space, so answers cite the wrong chunks;
- the only signal would be recall@5 collapsing in the next `run_eval.py`, or users
  complaining.

## 7. The failure that cost us more than an hour

**The indexer's memory, Step 4.1 (2026-11-05).** It was the slowest problem of the first
live deployment.

**Symptoms:** the first real indexer Job on minikube, with a 1 GiB memory limit, kept
failing. The pod went to `Error`, Kubernetes retried it (`backoffLimit: 2`), and the index
was never built.

**What we wrongly believed first:**
- **That 1 GiB was plenty.** Before the cluster existed, we had run the same indexer in plain
  Docker and watched its memory, which stayed under 1 GiB. On the laptop, Docker's swap had
  silently absorbed the real peak, so the number we trusted was too low.
- **That `kubectl top` confirmed it.** It showed only 663-784 Mi for the indexer. It samples
  too rarely to catch a short OCR spike.
- **A tooling trap on top.** Our first attempt to read the real peak printed **0**: Git Bash
  rewrote the path `/sys/fs/cgroup/memory.peak` into a Windows path before `kubectl exec`
  ran.

**What told us the truth:**
1. `kubectl describe pod` on the failed indexer pod showed `Last State: Terminated`,
   `Reason: OOMKilled`, `Exit Code: 137`. It was not a bug in our code: the kernel killed it.
2. Reading the cgroup's own high-water mark from inside the pod, with path rewriting off:
   ```bash
   MSYS_NO_PATHCONV=1 kubectl --context minikube -n rahbar exec <indexer-pod> -- cat /sys/fs/cgroup/memory.peak
   ```
   It returned **1,117 MiB**.

**The fix:** we raised the limit to 2 GiB (`indexer-job.yaml:57`) and re-applied the CronJob
too. A second run had failed because only the Job manifest had been updated at first.
Re-indexes then completed with 431 chunks each. We also recorded the corrected number, and
the earlier wrong claim, in `docs/AI-USAGE.md`.

**What we would do next time:** measure the peak with `memory.peak` inside the real cluster
before choosing a limit, and keep the Job and the CronJob templates in step.
