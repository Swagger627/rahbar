# Assignment 03 Kubernetes runbook

Use a single-node minikube with the Docker driver and Calico. Use the real Ollama models
`nomic-embed-text` and `llama3.2:3b`. The API, indexer, evaluator, Chroma and Redis run in
the `rahbar-a3` namespace. The base manifests for Assignment 02 remain independent.

## Install in a fresh cluster

1. Start a minikube profile. Choose memory with enough room for Kubernetes and Ollama.

   ```bash
   minikube -p rahbar start --driver=docker --cni=calico --memory=4096 --cpus=4
   minikube -p rahbar addons enable metrics-server
   ```

2. Obtain the seven PDFs. If they are already in `corpus/raw`, verify the checked-in hashes.

   ```bash
   make PY=python3.12 install
   .venv/bin/python corpus/fetch.py
   .venv/bin/python corpus/fetch.py --verify
   ```

3. Make Ollama reachable from the profile. The local Linux launcher uses a container named
   `rahbar-ollama` on Docker network `rahbar`. If that container exists, start and reuse it.
   Otherwise create it with `docker run -d --name rahbar-ollama --network rahbar -v
   rahbar-ollama:/root/.ollama ollama/ollama:0.40.14`.

   ```bash
   docker start rahbar-ollama
   docker exec rahbar-ollama ollama pull nomic-embed-text
   docker exec rahbar-ollama ollama pull llama3.2:3b
   ollama_ip=$(docker inspect -f '{{(index .NetworkSettings.Networks "rahbar").IPAddress}}' rahbar-ollama)
   bash scripts/ragops-k8s.sh build
   bash scripts/ragops-k8s.sh apply "$ollama_ip"
   bash scripts/ragops-k8s.sh stage a
   bash scripts/ragops-k8s.sh ingest baseline
   kubectl --context rahbar -n rahbar-a3 rollout status deploy/api
   ```

   The apply helper renders the overlay with the actual Ollama address and restricts egress
   to its IP. If Docker changes that address, reapply using the new IP and restart the two API
   Deployments so their ConfigMap environment is refreshed. Use a distinct tag or restart
   affected Deployments after reloading edited local images.

4. Run one query and check its trace with the commands in the [root README](../README.md).

## Evidence and controlled rollout

```bash
k=(kubectl --context rahbar -n rahbar-a3)
"${k[@]}" get all,pvc,configmap,cronjob,job
"${k[@]}" top pods --containers
"${k[@]}" rollout history deploy/api
```

### Reject a candidate and change a threshold

An intentionally poor candidate can be demonstrated by setting the **candidate** API's
top-k to 1. Reset that setting before measuring the actual 380-word chunk candidate.

```bash
"${k[@]}" set env deploy/api-candidate TOP_K=1
"${k[@]}" rollout status deploy/api-candidate
bash scripts/ragops-k8s.sh eval candidate api-candidate
"${k[@]}" rollout history deploy/api
"${k[@]}" set env deploy/api-candidate TOP_K-
"${k[@]}" rollout status deploy/api-candidate
```

For a ConfigMap-only check, save the configured thresholds, set a deliberately strict p95
budget, and replay the gate from the candidate Job’s saved metrics with the same image.
This isolates the ConfigMap change from timing and model variation. Restore the original
thresholds before promoting anything.

```bash
candidate_file=evaluation-candidate-RUN_ID.json
bash scripts/ragops-k8s.sh gate-run "$candidate_file"    # normal threshold
strict=$(mktemp)
python3.12 -c 'import json; p=json.load(open("eval/thresholds.json")); p["latency_p95_max_seconds"]=0.001; print(json.dumps(p))' > "$strict"
"${k[@]}" create configmap ragops-gate --from-file=thresholds.json="$strict" --dry-run=client -o yaml | "${k[@]}" apply -f -
bash scripts/ragops-k8s.sh gate-run "$candidate_file"    # expect failure
"${k[@]}" create configmap ragops-gate --from-file=thresholds.json=eval/thresholds.json --dry-run=client -o yaml | "${k[@]}" apply -f -
rm "$strict"
```

### Roll back a bad release

Perform the following local drill after recording a passing controlled rollout and capture
the pre-drill collection and embedding version. Deliberately select a missing collection.
The new revision remains unready while the previous ready pod continues serving.

```bash
"${k[@]}" set env deploy/api CHROMA_COLLECTION=rahbar-a3-missing-drill
"${k[@]}" rollout status deploy/api --timeout=35s
"${k[@]}" rollout undo deploy/api
"${k[@]}" rollout status deploy/api --timeout=120s
```

Ask a new question via a restarted port-forward, export the trace, and verify its
`collection` and `embedding_version` match the pre-drill values. Keep both collections
available throughout the experiment.

### Demo evidence

- A completed ingestion and a completed evaluation Job, plus the failed candidate Job.
- A per-document verification report, zero-chunk rerun, delete count and document restore.
- A query trace and exact model prompt, plus the dropped Batch B fixture.
- Both coverage runs and the before/after data-drift comparison.
- Actual CPU and memory samples during indexing and evaluation. Resource requests and limits
  belong in the API Deployment, Chroma StatefulSet, and ingestion/evaluation Pod templates.

Use `bash scripts/ragops-k8s.sh export` to save PVC contents in `eval/results/` and
`traces/`. Reset the staged corpus to Batch A and reindex the required profile before a
fresh controlled candidate comparison. The online API only reads Chroma; the evaluator
cannot connect to Chroma through the namespace policy; ingestion Jobs serialize writes on
the corpus PVC. The shared trace PVC is designed for this single-node demo.

### Ingest an unseen batch during the demo

Prepare an inbox directory starting from the desired existing inventory, then add the new
files and their manifest rows. Use the trusted source, title, stable document ID, sortable
version and expected page count for each new file. For short text documents the expected
count is one. A declared SHA-256 or minimum word count provides an additional truncation
check. Run `bash scripts/ragops-k8s.sh stage-dir /path/to/inbox`, then
`bash scripts/ragops-k8s.sh ingest baseline` (or `candidate` if it is the live collection).
The staged manifest is a complete snapshot, so retain rows for documents you want to keep.
