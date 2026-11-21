# Assignment 03 terminal screenshot guide

Use these labels in the PDF appendix. Capture your own terminal after the command finishes. Include the prompt, command, and all displayed output. Use a maximized terminal, a readable font, and a wide window. When output spans multiple screens, take consecutive numbered captures with overlapping lines. Run the short evidence commands below to keep most captures to one screen.

## 1. Prepare the cluster and your terminal

1. Start Docker Desktop and the existing minikube profile. Use [the Kubernetes runbook](../k8s/README.md) to build and apply the Assignment 03 deployment if you are starting from a fresh cluster.
2. Open two Bash terminals in the repository. In the first, define the namespace helper and list the workloads.

   ```bash
   cd /home/mughees-haider/SCD2/rahbar
   k() { kubectl --context rahbar -n rahbar-a3 "$@"; }
   r3() { bash scripts/ragops-k8s.sh "$@"; }
   k get pods,job
   ```

3. Keep this order for a clean demonstration. Use Batch A for the baseline and candidate comparisons, run Batch B on the baseline for the data-drift comparison, then return the baseline to Batch A before promotion. Parts A and D reuse the Batch B run.
4. Each helper run prints the exact `kubectl ... logs job/NAME` command. Keep the Job names visible in your terminal history. After each set of runs use `r3 export` to save the result JSON and trace JSONL files in the repository.

## A2 · Unchanged Batch A

1. Stage Batch A and complete its first ingestion. If the cluster already has Batch A, the first command can already show zero embeddings.

   ```bash
   r3 stage a
   r3 ingest baseline
   ```

2. Run the same ingestion again and capture the last command and output. Look for `chunks_embedded: 0`, `chunks_deleted: 0`, and an unchanged collection revision.

   ```bash
   r3 ingest baseline
   ```

3. Optionally capture the short Job log with `k logs job/"$INGEST_JOB" --tail=3` after assigning `INGEST_JOB` to the name printed by the helper.

## B1 · Three-level evaluation summary

1. Keep Batch A staged. Run the baseline on the live API Service. The default evaluates the suite twice.
   On a CPU-only machine, allow enough time for generation and judging before starting the candidate.

   ```bash
   r3 eval baseline api
   ```

2. Record the printed Job name in `BASELINE_JOB` and show the summary. Capture the Module, Retrieval, and End to end headings and all their rows.

   ```bash
   BASELINE_JOB=ragops-eval-baseline-REPLACE_WITH_PRINTED_ID
   k logs job/"$BASELINE_JOB" --tail=24
   r3 export
   ```

3. The result is an `evaluation-baseline-*.json` file. Keep it for the candidate and data-drift comparisons. Human authorship and answer-review labels remain the responsibility of the group.

## C1 and C2 · Failed and passing candidate gate

1. Keep Batch A staged and build the separate candidate index. The experiment changes chunk size from 400 to 380 words.

   ```bash
   r3 ingest candidate
   k rollout status deploy/api-candidate --timeout=180s
   ```

2. Make an intentionally weak candidate by setting its top-k to 1. Capture the failed gate along with the live Deployment history before or after the test. The live Deployment should retain the same revision.

   ```bash
   k rollout history deploy/api
   k set env deploy/api-candidate TOP_K=1
   k rollout status deploy/api-candidate --timeout=180s
   r3 eval candidate api-candidate
   k rollout history deploy/api
   ```

3. Restore the ordinary candidate setting and run the full suite. Capture `GATE PASS` when the candidate meets the stated thresholds. Keep any genuine failure result when it does not.

   ```bash
   k set env deploy/api-candidate TOP_K-
   k rollout status deploy/api-candidate --timeout=180s
   r3 eval candidate api-candidate
   r3 export
   ```

4. Assign the filename of the passing result to `CANDIDATE_FILE`, such as `evaluation-candidate-<run-id>.json`. Read the `gate` field and keep this exact file for F3 and promotion. Use the evaluation Job's `--tail=24` log command for a compact capture.

## A1 · Batch B verification, with optional A3 and E3 captures

1. Run this before promoting the candidate, with the live Service still pointing to `rahbar-a3-baseline`.

   ```bash
   r3 stage b
   r3 ingest baseline
   r3 export
   ```

2. Copy the report path printed by the ingestion Job and assign its basename to `BATCH_B_REPORT`. Print the retained per-document outcomes, including all check reasons. Capture all of the table across consecutive screens. Keep the full result file with the screenshots.

   ```bash
   BATCH_B_REPORT=ingestion-REPLACE_WITH_PRINTED_ID.json
   python3.12 eval/show_ingestion.py "eval/results/$BATCH_B_REPORT" --batch-b
   ```

3. Run the same suite against Batch A plus Batch B while settings stay fixed. Compare the exported `observe` result against the Part B baseline.

   ```bash
   r3 eval observe api
   r3 export
   python3.12 eval/compare_runs.py      eval/results/evaluation-baseline-REPLACE.json      eval/results/evaluation-observe-REPLACE.json      --out eval/results/data-drift.json
   ```

4. For the document update proof, run the following in order and retain each ingestion JSON. Capture the changed and then deleted `b09-debate` counts, and the fee edition's status switching on restoration. The Job summary prints the document counts.

   ```bash
   r3 ingest baseline
   r3 ingest baseline --restore fee-info=2026-27
   r3 ingest baseline --latest fee-info
   r3 stage edited
   r3 ingest baseline
   r3 stage deleted
   r3 ingest baseline
   r3 export
   ```

## D1 and D2 · Full trace and exact prompt

1. While Batch B is indexed, start the port-forward in the second terminal. Keep it open while you run curl in the first.

   ```bash
   kubectl --context rahbar -n rahbar-a3 port-forward svc/api 18000:8000
   ```

2. Send the planted-document query. Export and inspect the resulting trace for the guardrail's drop action. The header supplies the trace ID. Use this compact example for D1 if it contains the required guardrail action.

   ```bash
   trace_id() { awk 'tolower($1)=="x-trace-id:" {gsub(/\r/, "", $2); print $2}' "$1"; }
   curl -fsS -D /tmp/rahbar-d1.headers -o /tmp/rahbar-d1.body      http://127.0.0.1:18000/api/ask -H 'Content-Type: application/json'      -d '{"question":"Where is Azure Lantern badge replacement requested?"}'
   D1_ID=$(trace_id /tmp/rahbar-d1.headers)
   r3 export
   python3.12 eval/trace_tools.py trace "$D1_ID"
   ```

3. Send a normal question that reaches generation. Use the evaluation header to bypass Redis and get a new trace. Capture the prompt command's complete output with its exact passages and generation attempts.

   ```bash
   curl -fsS -D /tmp/rahbar-d2.headers -o /tmp/rahbar-d2.body      http://127.0.0.1:18000/api/ask -H 'Content-Type: application/json'      -H 'X-Rahbar-Eval: 1' -d '{"question":"How much is the one-time admission fee?"}'
   D2_ID=$(trace_id /tmp/rahbar-d2.headers)
   r3 export
   python3.12 eval/trace_tools.py prompt "$D2_ID"
   ```

4. An abstention can have zero generation attempts. If the chosen prompt capture has no attempts, ask a different grounded question. To check the operational metrics, run `curl -fsS http://127.0.0.1:18000/metrics` and `python3.12 eval/trace_tools.py summary`.
5. For the required stdout proof, use `k logs -l app=api,track=live --since=10m --tail=-1 | rg -F "$D1_ID"`. Use `--since=30m` or a wider interval if the query is older.

## E1 and E2 · Coverage before and after

1. Run the original coverage Job. The provided live log stays fixed across comparisons. Capture the coverage percentage, threshold, failed Job, and uncovered topic groups.

   ```bash
   r3 coverage golden
   ```

2. Run the expanded golden set with the same cutoff. Capture the increased percentage and remaining gaps. Export both result files.

   ```bash
   r3 coverage golden_expanded
   r3 export
   ```

## F1 and F2 · Cluster inventory and Job logs

1. Once at least one ingestion and one evaluation Job have finished, capture the inventory command with the API Deployment, Chroma StatefulSet, PVCs, ConfigMaps, CronJob and Job completions.

   ```bash
   k get all,pvc,configmap,cronjob,job
   ```

2. Substitute the two actual completed Job names and capture each command separately. If you use the full log and it spans several screens, take consecutive captures. Also collect a resource sample **while** one of the Jobs is running.

   ```bash
   INGEST_JOB=ingest-baseline-REPLACE_WITH_PRINTED_ID
   BASELINE_JOB=ragops-eval-baseline-REPLACE_WITH_PRINTED_ID
   k logs job/"$INGEST_JOB"
   k logs job/"$BASELINE_JOB"
   k top pods --containers
   ```

## F3 · Gate result changes after ConfigMap edit

1. Use the exact passing candidate file from C2. Capture its replay with the normal threshold. Each replay runs from the same evaluation image and saved metrics.

   ```bash
   r3 gate-run "$CANDIDATE_FILE"
   ```

2. Apply a stricter p95 threshold and replay. Capture the ConfigMap edit and failure. Restore the checked-in thresholds and verify one more pass.

   ```bash
   strict=$(mktemp)
   python3.12 -c 'import json; p=json.load(open("eval/thresholds.json")); p["latency_p95_max_seconds"]=0.001; print(json.dumps(p))' > "$strict"
   k create configmap ragops-gate --from-file=thresholds.json="$strict" --dry-run=client -o yaml | k apply -f -
   r3 gate-run "$CANDIDATE_FILE"
   k create configmap ragops-gate --from-file=thresholds.json=eval/thresholds.json --dry-run=client -o yaml | k apply -f -
   rm "$strict"
   r3 gate-run "$CANDIDATE_FILE"
   ```

## F4, F5 and F6 · Rollout status, undo and post-undo trace

1. After finishing Batch B captures, re-stage and ingest Batch A on the baseline. Promote the passing candidate. Capture the successful rollout status. Ask a question and retain its trace's collection and embedding version before the bad-release drill.

   ```bash
   r3 stage a
   r3 ingest baseline
   r3 promote "eval/results/$CANDIDATE_FILE"
   k rollout status deploy/api --timeout=180s
   ```

2. Use the local-only bad-release drill. The new pod references a missing collection and stays unready. Capture the rollout timeout, undo and successful rollout status. The timeout is expected. Run each line in the interactive terminal.

   ```bash
   k set env deploy/api CHROMA_COLLECTION=rahbar-a3-missing-drill
   k rollout status deploy/api --timeout=35s
   k rollout undo deploy/api
   k rollout status deploy/api --timeout=120s
   ```

3. Restart the port-forward, send a fresh question, and export its trace. Capture the trace ID, collection, collection revision, and embedding digest. Compare these with the values you recorded before the bad deployment.

   ```bash
   curl -fsS -D /tmp/rahbar-f.headers -o /tmp/rahbar-f.body      http://127.0.0.1:18000/api/ask -H 'Content-Type: application/json'      -H 'X-Rahbar-Eval: 1' -d '{"question":"How much is the one-time admission fee?"}'
   F_ID=$(trace_id /tmp/rahbar-f.headers)
   r3 export
   python3.12 eval/trace_tools.py trace "$F_ID" | rg '"(trace_id|collection|collection_version|embedding_version)"'
   ```

4. Use the same runtime and own terminal for the screenshots you submit. The supplied PDF reserves a box for each label. Add multiple consecutive captures after the relevant placeholder when the output does not fit one screen.

## Insert the captures in the report

1. Open your operating system's screenshot tool. Select the terminal window and save each image using
   its evidence label, such as `A1.png` or `F2b.png`. Leave the prompt, command, and displayed output visible.
2. Put the images in one directory. Capture A3 and E3 if you want the extra change and drift evidence.
3. Enter your cover details and regenerate the report from the repository root.

   ```bash
   python3.12 scripts/build_ragops_report.py      --student 'Your full name / your roll number'      --second 'Teammate full name / teammate roll number'      --screenshots /path/to/captures
   ```

4. For consecutive captures, place the first image in its labeled box and append the following images
   next to that page in a PDF editor. Confirm every required command and its full output remain legible
   in the exported PDF.
