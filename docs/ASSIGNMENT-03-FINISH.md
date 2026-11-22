# Assignment 03: what is left, in order

`python3 scripts/check_a3_readiness.py` lists every handout item as `OK` or `TODO`. Run it
after each step. Part A, the Batch B evidence, the coverage expansion and the rollback drill are
already committed. What remains is the work only the group can do (steps 1 and 9) and the cluster
runs that depend on it (steps 2 to 8).

**The order matters.** Editing `eval/golden.jsonl` changes its digest. Every evaluation,
baseline and gate comparison made before that edit then stops matching, and the gate fails
with "golden set differs from baseline". So finish the golden set first, then run everything
after it once.

Shorthand used below: `k() { kubectl --context rahbar -n rahbar-a3 "$@"; }`

## 1. Finish the golden set (group work, no cluster)

1. **Write at least 8 questions yourselves.** Add them to `eval/golden.jsonl` with
   `"origin": "human"` and ids `h-01` … `h-08`, so they cannot clash with `a3-26` … `a3-35`.
   Use the same fields as the other rows (`relevant` gives the document id, version and page).
   Answerable rows count towards the 15. Hard ones count towards the 5.
2. **Review at least 8 synthetic rows by hand.** Generate the checklist with
   `python3.12 eval/make_review_sheets.py eval/results/evaluation-baseline-20261121T063936Z-239b0164.json`.
   Open `eval/reviews/golden-review.md`, check each row against the real PDF page, then set
   `"review_status": "reviewed"`, `"reviewer": "INITIALS"` and `"review_note": "…"`.
   Fix rows that are wrong. Two to look at first:
   - `a3-01`: the rule says "admission … is automatically closed", not "registration".
   - `a3-02`: the pipeline answered 3.33, but the table says B− is 2.67.
3. **Rebuild the expanded set**, so it stays equal to the golden set plus the coverage additions:
   ```bash
   python3.12 - <<'EOF'
   import json
   gold = [json.loads(l) for l in open("eval/golden.jsonl") if l.strip()]
   ids = {r["id"] for r in gold}
   extra = [json.loads(l) for l in open("eval/golden_expanded.jsonl") if l.strip()]
   extra = [r for r in extra if r["id"] not in ids and int(r["id"].split("-")[1]) > 25]
   open("eval/golden_expanded.jsonl", "w").write("".join(json.dumps(r) + "\n" for r in gold + extra))
   EOF
   ```

## 2. Start the stack and rebuild the images

```bash
# Docker Desktop running, then:
START=$(date -u +%Y-%m-%dT%H:%M:%SZ); echo "$START" > /tmp/a3-start   # the D3 summary window starts here
minikube -p rahbar start
ollama serve &                         # skip if Ollama already runs as a service
bash scripts/ragops-k8s.sh build       # api (prompt hash), indexer, eval (new golden files)
bash scripts/ragops-k8s.sh apply OLLAMA_IP   # the address that `minikube ssh` sees for the host
k rollout restart deploy/api deploy/api-candidate
```

## 3. Baseline and candidate indexes (Batch A)

```bash
bash scripts/ragops-k8s.sh stage a
bash scripts/ragops-k8s.sh ingest baseline
bash scripts/ragops-k8s.sh ingest candidate          # 380-word chunks, its own collection
bash scripts/ragops-k8s.sh eval baseline api         # about 1 h: 2 seeded runs
bash scripts/ragops-k8s.sh export
python3 scripts/check_a3_readiness.py                # read the "baseline itself meets" line
```

**Checkpoint: recalibrate before any candidate run.** The new golden set gives a new baseline.
If the readiness line "baseline itself meets the absolute thresholds" is `TODO`, or the set now
has more than 25 answerable questions, recompute the thresholds with the method in
[ASSIGNMENT-03-THRESHOLDS.md](ASSIGNMENT-03-THRESHOLDS.md) and update the run IDs and numbers
there. Then put the same values in both `eval/thresholds.json` and the ConfigMap in
`k8s/assignment3/resources.yaml`, and re-apply the ConfigMap:
`k create configmap ragops-gate --from-file=thresholds.json=eval/thresholds.json --dry-run=client -o yaml | k apply -f -`.
Doing this **now**, before the candidate exists, keeps the thresholds independent of it.

## 4. Candidate stopped by the gate (C2 fail, F3 never rolled out)

```bash
k set env deploy/api-candidate TOP_K=1 && k rollout status deploy/api-candidate
bash scripts/ragops-k8s.sh eval candidate api-candidate      # expect GATE FAIL
bash scripts/ragops-k8s.sh export
k rollout history deploy/api | tee eval/results/c-rejected-not-rolled-out.txt
bash scripts/ragops-k8s.sh promote eval/results/evaluation-candidate-FAILED_RUN.json 2>&1 \
  | tee -a eval/results/c-rejected-not-rolled-out.txt              # refused
k set env deploy/api-candidate TOP_K- && k rollout status deploy/api-candidate
```

## 5. The real candidate and the ConfigMap flip (C1, C2 pass, F2)

```bash
bash scripts/ragops-k8s.sh eval candidate api-candidate      # the 380-word candidate, full suite
bash scripts/ragops-k8s.sh export
```

Then follow "Reject a candidate and change a threshold" in [k8s/README.md](../k8s/README.md)
with the candidate file, and pipe the whole sequence into `eval/results/f-configmap-gate-flip.txt`.

**If the 380-word candidate fails the gate,** keep it and report it as **rejected**. That is
a valid C1 decision. Do not loosen the thresholds to rescue it. C2 still needs one passing
candidate, so:

1. **The genuine failure becomes your C2 "fail" evidence.** The `TOP_K=1` run is then
   optional.
2. **Agree on a second single change as a group and evaluate it the same way.** F3 needs its
   own collection, and `promote` only accepts a candidate that serves `rahbar-a3-candidate`.
   A new chunk size therefore means changing `candidate) chunk_size=380` in
   `scripts/ragops-k8s.sh` and rebuilding that collection. The indexer refuses to mix profiles
   in one collection.
3. **For the ConfigMap flip,** replay the **failed** result with `gate-run`, loosen only the
   threshold it failed, replay again to show fail→pass, then restore the checked-in values.

A promoted candidate must have passed under the checked-in thresholds.

## 6. Data drift with the full suite (E3), before any promotion

```bash
bash scripts/ragops-k8s.sh stage b && bash scripts/ragops-k8s.sh ingest baseline
bash scripts/ragops-k8s.sh eval observe api
bash scripts/ragops-k8s.sh export
python3.12 eval/compare_runs.py eval/results/evaluation-baseline-RUN.json \
  eval/results/evaluation-observe-RUN.json --out eval/results/data-drift.json
bash scripts/ragops-k8s.sh coverage golden; bash scripts/ragops-k8s.sh coverage golden_expanded
```

While Batch B is live, open a port-forward (`k port-forward svc/api 18000:8000`) and send:

- the Azure Lantern question (D4);
- one ordinary question **twice, without the `X-Rahbar-Eval` header**, to produce a cache hit (D3).

Then stage Batch A again and run `ingest baseline`.

## 7. Promote, then the bad release and the undo (F3)

```bash
bash scripts/ragops-k8s.sh promote eval/results/evaluation-candidate-PASSING_RUN.json \
  2>&1 | tee eval/results/f-promote-terminal.txt
```

Then run "Roll back a bad release" from [k8s/README.md](../k8s/README.md), save it to
`f-rollout-undo-terminal.txt`, and keep the trace from after the undo.

## 8. Export, summarize, inventory

```bash
bash scripts/ragops-k8s.sh export
python3.12 eval/trace_tools.py summary --since "$(cat /tmp/a3-start)" \
  --until "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --out eval/results/trace-summary.json
k get all,pvc,configmap,cronjob,job | tee eval/results/f-cluster-inventory.txt
k top pods --containers                # during an ingestion and during an eval, for F5
```

## 9. Hand labels, report and screenshots (group work)

**Do not edit `eval/golden.jsonl` again.** Changing it now would invalidate every run from
step 3 onwards. `golden-review.md` is regenerated here only for reference.

```bash
python3.12 eval/make_review_sheets.py eval/results/evaluation-baseline-NEW_RUN.json --force
```

1. **Label the answers.** Label at least 8 rows of `eval/reviews/faithfulness-labels.jsonl`
   **before** looking at the judge's verdict.
2. **Render the report:**
   `python3.12 scripts/build_ragops_report.py --student "…" --second "…" --reviews eval/reviews/faithfulness-labels.jsonl --screenshots DIR`.
3. **Fill in the contribution table and confirm the AI statement.** Both are in the generated
   report.
4. **Take the screenshots** listed in [ASSIGNMENT-03-SCREENSHOTS.md](ASSIGNMENT-03-SCREENSHOTS.md).
5. **Check readiness.** `python3 scripts/check_a3_readiness.py` should end with `0 to do`.
6. **Commit from both accounts.**
