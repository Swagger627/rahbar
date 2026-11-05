#!/usr/bin/env bash
# End-to-end test of RAHBAR on an ephemeral kind cluster (section 6), on a GitHub runner.
#
#   ci.yml  (e2e-eval): images built from this commit and loaded into kind
#   cd.yml  (deploy):   images pulled from Docker Hub by commit SHA (REGISTRY and TAG set)
#
# It uses the real path end to end: the prod overlay, the corpus PVC filled through the
# loader pod, the real indexer Job, the real retriever, real embeddings and answers from
# Ollama (nomic-embed-text and llama3.2:3b, running on the runner, on CPU), the Ingress,
# and eval/run_eval.py with the section 5.4 gate. The corpus is the fictional fixture set
# in eval/fixtures/; the university PDFs never leave the laptop.
#
# Only two host-specific values change from the laptop setup: Ollama's address (here a
# container on kind's Docker network instead of the Windows host), in the ConfigMap and in
# the two NetworkPolicy rules that allow it.
set -euo pipefail

CLUSTER=${CLUSTER:-rahbar}
REGISTRY=${REGISTRY:-}          # empty: build the images here; else pull REGISTRY/rahbar-*:TAG
TAG=${TAG:-dev}
OUT=${OUT:-e2e-out}
OLLAMA_IMAGE=ollama/ollama:0.40.14
INGRESS_NGINX=https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.15.1/deploy/static/provider/kind/deploy.yaml
K="kubectl --context kind-$CLUSTER"
mkdir -p "$OUT"

step() { echo; echo "=== $*"; }
collect() {                    # always keep logs for the artifact, even after a failure
  $K get pods,pvc,jobs -A -o wide > "$OUT/cluster.txt" 2>&1 || true
  $K -n rahbar describe pods > "$OUT/describe-pods.txt" 2>&1 || true
  for d in deploy/api statefulset/chroma deploy/redis job/indexer; do
    $K -n rahbar logs "$d" --all-containers --tail=300 > "$OUT/logs-${d//\//-}.txt" 2>&1 || true
  done
  docker logs ollama > "$OUT/logs-ollama.txt" 2>&1 || true
  # Let actions/cache (running as the runner user) read what the root container pulled.
  sudo chmod -R a+rX "$HOME/.ollama/models" 2>/dev/null || true
}
trap collect EXIT

if [ -n "$REGISTRY" ]; then image() { echo "$REGISTRY/rahbar-$1:$TAG"; }; else image() { echo "rahbar-$1:dev"; }; fi

step "1. Ollama on the runner, with the two pinned models"
mkdir -p "$HOME/.ollama/models"     # only the models are cached (no key files)
docker run -d --name ollama --network kind -v "$HOME/.ollama/models:/root/.ollama/models" "$OLLAMA_IMAGE"
timeout 120 bash -c "until docker exec ollama ollama list >/dev/null 2>&1; do sleep 2; done"
docker exec ollama ollama pull nomic-embed-text
docker exec ollama ollama pull llama3.2:3b
OLLAMA_IP=$(docker inspect -f '{{(index .NetworkSettings.Networks "kind").IPAddress}}' ollama)
echo "Ollama at $OLLAMA_IP"

step "2. Images"
if [ -z "$REGISTRY" ]; then
  for i in api indexer ui; do
    docker build -q -t "rahbar-$i:dev" "$i"
    kind load docker-image "rahbar-$i:dev" --name "$CLUSTER"
  done
else
  for i in api indexer ui; do docker pull -q "$(image "$i")"; kind load docker-image "$(image "$i")" --name "$CLUSTER"; done
fi

step "3. Ingress controller"
$K apply -f "$INGRESS_NGINX"
$K label node --all ingress-ready=true --overwrite
$K -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s

step "4. Apply the prod overlay (Ollama's address adjusted for this runner)"
fix() {   # host-specific values and image names; everything else as committed
  sed -e "s#http://host.minikube.internal:11434#http://$OLLAMA_IP:11434#" \
      -e "s#192.168.65.254/32#$OLLAMA_IP/32#" \
      -e "s#image: rahbar-api:dev#image: $(image api)#" \
      -e "s#image: rahbar-indexer:dev#image: $(image indexer)#" \
      -e "s#image: rahbar-ui:dev#image: $(image ui)#"
}
kubectl kustomize k8s/overlays/prod | fix > "$OUT/applied.yaml"
grep -nE "image:|OLLAMA_URL|cidr:" "$OUT/applied.yaml"
# ingress-nginx's admission webhook (it checks every Ingress) can refuse connections for a
# few seconds after the controller is ready (CI run 42097509816 failed this way). Retry a
# few times; a real manifest error fails every time.
for attempt in 1 2 3 4 5 6; do
  $K apply -f "$OUT/applied.yaml" && break
  [ "$attempt" = 6 ] && { echo "apply failed 6 times"; exit 1; }
  echo "apply failed (attempt $attempt): retrying in 10 s"; sleep 10
done
$K -n rahbar rollout status statefulset/chroma --timeout=300s
$K -n rahbar rollout status deploy/redis --timeout=300s

step "5. Load the fixture corpus into the corpus PVC (same loader pod as on minikube)"
fix < k8s/base/corpus-loader.yaml | $K apply -f -
$K -n rahbar wait --for=condition=Ready pod/corpus-loader --timeout=300s
$K cp eval/fixtures/. rahbar/corpus-loader:/corpus
$K -n rahbar exec corpus-loader -- ls -la /corpus
$K -n rahbar delete pod corpus-loader --wait=false

step "6. The real indexer Job"
fix < k8s/base/indexer-job.yaml | $K apply -f -
if ! $K -n rahbar wait --for=condition=complete job/indexer --timeout=900s; then
  echo "indexer Job did not complete"; exit 1
fi
$K -n rahbar logs job/indexer | tee "$OUT/indexer.log"

step "7. API rollout and readiness"
$K -n rahbar rollout status deploy/api --timeout=300s
ready=$($K -n rahbar exec deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz', timeout=10).read().decode())")
echo "$ready" | tee "$OUT/readyz.json"
echo "$ready" | grep -q '"status":"ready"' || { echo "api is not ready"; exit 1; }

step "8. Through the Ingress: one question, then the fixture golden set with the gate"
$K -n ingress-nginx port-forward svc/ingress-nginx-controller 18080:80 > "$OUT/port-forward.log" 2>&1 &
timeout 60 bash -c "until curl -s -o /dev/null http://127.0.0.1:18080/; do sleep 1; done"
curl -s --max-time 300 -D "$OUT/smoke-headers.txt" -H "Host: rahbar.local" -H "Content-Type: application/json" \
  -d '{"question": "How much is the BS fee per credit hour?"}' \
  http://127.0.0.1:18080/api/ask | tee "$OUT/smoke.json"
echo
grep -q '"answer"' "$OUT/smoke.json" || { echo "no answer through the Ingress"; exit 1; }

if [ -f eval/fixtures/baseline.json ]; then
  python eval/run_eval.py --api http://127.0.0.1:18080 --host rahbar.local \
    --golden eval/fixtures/golden.fixture.yaml --baseline eval/fixtures/baseline.json \
    --out "$OUT/results.json"
else
  # First run only: no committed baseline yet. Citation validity and abstention are still
  # gated; a baseline is written (to the artifact) only if they pass. Review it and commit
  # it as eval/fixtures/baseline.json to switch on the recall@5 regression gate.
  echo "No eval/fixtures/baseline.json yet: bootstrapping one."
  python eval/run_eval.py --api http://127.0.0.1:18080 --host rahbar.local \
    --golden eval/fixtures/golden.fixture.yaml --baseline "$OUT/baseline.json" \
    --out "$OUT/results.json" --bootstrap-baseline
fi
