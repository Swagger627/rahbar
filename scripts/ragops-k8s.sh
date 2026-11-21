#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
context=${RAGOPS_CONTEXT:-rahbar}
k=(kubectl --context "$context" -n rahbar-a3)
python=${RAGOPS_PYTHON:-python3.12}
command=${1:-help}
if [ "$#" -gt 0 ]; then shift; fi

wait_job() {
  local job=$1 state seconds expires
  seconds=$("${k[@]}" get job "$job" -o jsonpath='{.spec.activeDeadlineSeconds}')
  expires=$((SECONDS + ${seconds:-3600} + 60))
  while (( SECONDS < expires )); do
    state=$("${k[@]}" get job "$job" -o jsonpath='{.status.conditions[?(@.status=="True")].type}')
    case "$state" in
      *Complete*) "${k[@]}" logs "job/$job"; return 0 ;;
      *Failed*) "${k[@]}" logs "job/$job" || true; return 1 ;;
    esac
    sleep 2
  done
  "${k[@]}" describe job "$job"
  return 2
}

case "$command" in
  build)
    docker build -t rahbar-api:a3 api
    docker build -t rahbar-indexer:a3 indexer
    docker build -t rahbar-eval:a3 -f eval/Dockerfile .
    for image in api indexer eval; do
      minikube -p "$context" image load "rahbar-$image:a3" --overwrite
    done
    ;;
  apply)
    ollama_ip=${1:?provide the Ollama IPv4 address as seen inside minikube}
    "$python" -c 'from ipaddress import IPv4Address; import sys; IPv4Address(sys.argv[1])' "$ollama_ip"
    kubectl kustomize k8s/assignment3 |
      sed "s#http://host.minikube.internal:11434#http://$ollama_ip:11434#g;
           s#192.168.65.254/32#$ollama_ip/32#g" | "${k[@]}" apply -f -
    "${k[@]}" apply -f k8s/assignment3/artifacts-pod.yaml
    "${k[@]}" rollout status statefulset/chroma --timeout=300s
    "${k[@]}" wait --for=condition=Ready pod/ragops-artifacts --timeout=300s
    ;;
  stage|stage-dir)
    if [ "$command" = stage ]; then
      stage=${1:?choose a, b, edited or deleted}
      directory=$(mktemp -d)
      trap 'rm -rf "$directory"' EXIT
      "$python" corpus/stage_ragops.py "$stage" "$directory"
    else
      directory=${1:?provide the full desired inbox directory}
      test -f "$directory/_ragops.json"
    fi
    remote="inbox-$(date +%s)-$$"
    "${k[@]}" exec ragops-artifacts -- mkdir -p "/corpus/$remote"
    "${k[@]}" cp "$directory/." "ragops-artifacts:/corpus/$remote"
    "${k[@]}" exec ragops-artifacts -- python -c '
from pathlib import Path
import fcntl, os, sys
lake = Path("/corpus/lake")
lake.mkdir(exist_ok=True)
with (lake / "writer.lock").open("a") as guard:
    fcntl.flock(guard, fcntl.LOCK_EX)
    inbox = Path("/corpus/inbox")
    if inbox.exists() and not inbox.is_symlink():
        raise SystemExit("expected a managed inbox symlink")
    old = inbox.readlink() if inbox.is_symlink() else None
    pending = inbox.with_name(".inbox-link")
    pending.unlink(missing_ok=True)
    pending.symlink_to(sys.argv[1])
    os.replace(pending, inbox)
    if old and old.name.startswith("inbox-") and old == Path(old.name):
        import shutil
        shutil.rmtree(inbox.parent / old)
' "$remote"
    ;;
  ingest)
    profile=${1:-baseline}
    if [ "$#" -gt 0 ]; then shift; fi
    case "$profile" in
      baseline) chunk_size=400 ;;
      candidate) chunk_size=380 ;;
      *) echo "Unknown profile: $profile" >&2; exit 2 ;;
    esac
    job="ingest-$profile-$(date +%s)"
    "${k[@]}" create job "$job" --from=cronjob/reindex --dry-run=client -o json |
      "$python" -c '
import json, sys
job = json.load(sys.stdin)
job["spec"]["backoffLimit"] = 0
container = job["spec"]["template"]["spec"]["containers"][0]
container["env"] = [
    {"name": "CHROMA_COLLECTION", "value": "rahbar-a3-" + sys.argv[1]},
    {"name": "CHUNK_SIZE", "value": sys.argv[2]},
]
container["args"] = sys.argv[3:]
print(json.dumps(job))
' "$profile" "$chunk_size" "$@" | "${k[@]}" apply -f -
    echo "kubectl --context $context -n rahbar-a3 logs job/$job"
    wait_job "$job"
    ;;
  eval|components)
    if [ "$command" = "components" ]; then
      phase=observe
      target=${1:-api}
      repeats=${2:-2}
      job="ragops-components-$(date +%s)"
      components=1
    else
      phase=${1:?choose baseline, candidate, or observe}
      target=${2:-api}
      repeats=${3:-2}
      job="ragops-eval-$phase-$(date +%s)"
      components=0
    fi
    "${k[@]}" create --dry-run=client -o json -f k8s/eval-job.yaml |
      "$python" -c '
import json, sys
job = json.load(sys.stdin)
job["metadata"]["name"] = sys.argv[1]
job["spec"]["template"]["spec"]["containers"][0]["env"] = [
    {"name": "EVAL_PHASE", "value": sys.argv[2]},
    {"name": "EVAL_API", "value": f"http://{sys.argv[3]}:8000"},
    {"name": "EVAL_RUNS", "value": sys.argv[4]},
    {"name": "EVAL_COMPONENTS_ONLY", "value": sys.argv[5]},
]
print(json.dumps(job))
' "$job" "$phase" "$target" "$repeats" "$components" | "${k[@]}" apply -f -
    echo "kubectl --context $context -n rahbar-a3 logs job/$job"
    wait_job "$job"
    ;;
  gate-run)
    result=${1:?provide a candidate result filename on the results PVC}
    case "$result" in evaluation-candidate-*.json) ;; *) exit 2 ;; esac
    result=$(basename "$result")
    job="ragops-gate-$(date +%s)"
    "${k[@]}" create --dry-run=client -o json -f k8s/eval-job.yaml |
      "$python" -c '
import json, sys
job = json.load(sys.stdin)
job["metadata"]["name"] = sys.argv[1]
container = job["spec"]["template"]["spec"]["containers"][0]
container["command"] = ["python", "/suite/release_gate.py"]
container["args"] = [
    "--candidate", "/results/" + sys.argv[2], "--baseline", "/results/a3-baseline.json",
    "--config", "/gate/thresholds.json",
]
print(json.dumps(job))
' "$job" "$result" | "${k[@]}" apply -f -
    echo "kubectl --context $context -n rahbar-a3 logs job/$job"
    wait_job "$job"
    ;;
  coverage)
    gold=${1:-golden}
    similarity=${2:-0.77}
    case "$gold" in golden|golden_expanded) ;; *) exit 2 ;; esac
    job="ragops-coverage-$(date +%s)"
    "${k[@]}" create --dry-run=client -o json -f k8s/eval-job.yaml |
      "$python" -c '
import json, sys
job = json.load(sys.stdin)
job["metadata"]["name"] = sys.argv[1]
container = job["spec"]["template"]["spec"]["containers"][0]
container["command"] = ["python", "/suite/coverage_check.py"]
container["args"] = [
    "--golden", "/suite/" + sys.argv[2] + ".jsonl", "--out", "/results",
    "--similarity", sys.argv[3],
]
print(json.dumps(job))
' "$job" "$gold" "$similarity" | "${k[@]}" apply -f -
    echo "kubectl --context $context -n rahbar-a3 logs job/$job"
    wait_job "$job"
    ;;
  export)
    mkdir -p eval/results traces
    "${k[@]}" cp ragops-artifacts:/results/. eval/results/
    "${k[@]}" cp ragops-artifacts:/traces/. traces/
    ;;
  promote)
    result=${1:?provide the exported passing candidate result JSON}
    ready=$("${k[@]}" exec deploy/api-candidate -- python -c '
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/readyz", timeout=5) as response:
    print(response.read().decode())
')
    "$python" -c '
import json, sys
with open(sys.argv[1]) as file:
    run = json.load(file)
if run["phase"] != "candidate" or not run["gate"]["pass"] or len(run["runs"]) < 2:
    raise SystemExit("candidate gate did not pass a full two-run suite")
current = json.loads(sys.argv[2])["checks"]["index"]
fields = ["collection", "collection_version", "embedding_version", "top_k"]
expected = [[current[key] for key in fields]]
if current["collection"] != "rahbar-a3-candidate" or run.get("serving_snapshots") != expected:
    raise SystemExit("candidate snapshot changed since evaluation: rerun the suite before promoting")
' "$result" "$ready"
    bash scripts/ragops-k8s.sh gate-run "$(basename "$result")"
    "${k[@]}" set env deploy/api CHROMA_COLLECTION=rahbar-a3-candidate
    "${k[@]}" rollout status deploy/api --timeout=180s
    "${k[@]}" set env cronjob/reindex CHROMA_COLLECTION=rahbar-a3-candidate CHUNK_SIZE=380
    ;;
  status)
    "${k[@]}" get all,pvc,configmap,cronjob,job
    ;;
  *)
    echo "Usage: $0 build | apply OLLAMA_IP | stage a|b|edited|deleted | stage-dir DIRECTORY | ingest PROFILE [--restore ID=VERSION|--latest ID] | eval PHASE [SERVICE [RUNS]] | components [SERVICE [RUNS]] | gate-run CANDIDATE_FILENAME | coverage golden|golden_expanded [COSINE] | export | promote RESULT | status"
    ;;
esac
