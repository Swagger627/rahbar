#!/usr/bin/env bash
# Start the whole RAHBAR stack on an Ubuntu laptop with ONE command (the Linux twin of
# start-rahbar.ps1). Safe to run again and again: every step skips what is already done.
#
#   bash start-rahbar.sh          # set up (first time) or start (later), then open the browser
#   bash start-rahbar.sh --stop   # stop everything; the cluster, index and models are kept
#
# First run: about 20-40 minutes and ~6 GB of downloads (minikube, the 2 Ollama models,
# the 3 RAHBAR images from Docker Hub, the 7 PDFs), then the real indexer Job.
# Later runs: about 2-4 minutes.
#
# What runs where (same design as the Windows laptop, docs/DEPLOYMENT.md):
#   minikube profile "rahbar" (Calico, 4 GB)  -> namespace rahbar: api x3, ui, chroma, redis, Jobs
#   Docker container "rahbar-ollama"          -> nomic-embed-text + llama3.2:3b, outside the cluster
# Only two host-specific values change, exactly like scripts/e2e-kind.sh in CI: Ollama's
# address in the ConfigMap and in the two NetworkPolicy rules that allow it. Images are the
# published ones from Docker Hub, pinned by commit SHA (never :latest).
set -euo pipefail

# ---- settings -------------------------------------------------------------------------
REPO_URL=https://github.com/Swagger627/rahbar.git
SHA=2d25894ccd42dd6aa492dd2b6aaf176bf6dbfc1a          # the submitted commit (main)
HUB=docker.io/faseehahmed009                          # where cd.yml published the images
PROFILE=rahbar                                        # minikube profile = kubectl context
MINIKUBE_VERSION=v1.39.0                              # Kubernetes v1.37, as on the Windows laptop
KUBECTL_VERSION=v1.37.0
OLLAMA_IMAGE=ollama/ollama:0.40.14                    # same Ollama version as everywhere else
PORT=18081                                            # local port for the Ingress
BIN="$HOME/.local/bin"
STATE="$HOME/.rahbar"                                 # port-forward pid and log
export PATH="$BIN:$PATH"
K="kubectl --context $PROFILE"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*"; exit 1; }
mkdir -p "$BIN" "$STATE"

# ---- --stop ---------------------------------------------------------------------------
if [ "${1:-}" = "--stop" ]; then
  step "Stopping RAHBAR (nothing is deleted)"
  [ -f "$STATE/port-forward.pid" ] && kill "$(cat "$STATE/port-forward.pid")" 2>/dev/null || true
  rm -f "$STATE/port-forward.pid"
  minikube -p "$PROFILE" stop || true
  docker stop rahbar-ollama >/dev/null 2>&1 || true
  echo "Stopped. Start again with: bash start-rahbar.sh"
  exit 0
fi

# ---- 1. tools -------------------------------------------------------------------------
step "1. Tools (git, curl, Docker, kubectl, minikube)"
missing=""
for t in git curl; do command -v "$t" >/dev/null || missing="$missing $t"; done
if [ -n "$missing" ]; then
  echo "Installing:$missing (needs your password once)"
  sudo apt-get update -qq && sudo apt-get install -y -qq $missing
fi
if ! command -v docker >/dev/null; then
  echo "Installing Docker (needs your password once)"
  sudo apt-get update -qq && sudo apt-get install -y -qq docker.io
  sudo usermod -aG docker "$USER"
  die "Docker installed. Log out and log in again (so your user may use Docker), then run this script again."
fi
docker info >/dev/null 2>&1 || {
  sudo systemctl start docker 2>/dev/null || true
  docker info >/dev/null 2>&1 || die "Docker is not usable by $USER. Run: sudo usermod -aG docker $USER, log out and in, then run this again."
}
# kubectl and minikube: official binaries, checked against their published SHA-256, no sudo.
fetch_bin() {   # name url
  if ! command -v "$1" >/dev/null; then
    echo "Downloading $1"
    curl -fsSL -o "$BIN/$1" "$2"
    echo "$(curl -fsSL "$2.sha256" | cut -d' ' -f1)  $BIN/$1" | sha256sum -c --quiet || { rm -f "$BIN/$1"; die "$1 checksum mismatch"; }
    chmod +x "$BIN/$1"
  fi
}
fetch_bin kubectl  "https://dl.k8s.io/release/$KUBECTL_VERSION/bin/linux/amd64/kubectl"
fetch_bin minikube "https://github.com/kubernetes/minikube/releases/download/$MINIKUBE_VERSION/minikube-linux-amd64"
echo "docker $(docker version --format '{{.Server.Version}}'), $(kubectl version --client | head -1), $(minikube version --short)"

free_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
echo "free RAM: $free_mb MB (minikube needs 4 GB, Ollama about 3 GB)"
[ "$free_mb" -lt 5000 ] && echo "Low memory: close the browser and other big apps if answers are very slow."

# ---- 2. the code ----------------------------------------------------------------------
step "2. The RAHBAR repository"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if   [ -d "$HERE/k8s/overlays" ];    then REPO="$HERE"
elif [ -d "$HERE/../k8s/overlays" ]; then REPO="$(cd "$HERE/.." && pwd)"
else
  REPO="$HOME/rahbar"
  [ -d "$REPO/.git" ] || git clone -q "$REPO_URL" "$REPO"
  git -C "$REPO" fetch -q origin && git -C "$REPO" checkout -q "$SHA"
fi
cd "$REPO"
echo "using $REPO"

# ---- 3. the cluster -------------------------------------------------------------------
step "3. minikube (profile $PROFILE, Calico, ingress, metrics-server)"
PREV_CONTEXT=$(kubectl config current-context 2>/dev/null || true)
minikube -p "$PROFILE" start --driver=docker --cni=calico --memory=4096 --cpus=4
minikube -p "$PROFILE" addons enable metrics-server >/dev/null
minikube -p "$PROFILE" addons enable ingress >/dev/null
# minikube makes itself the default context; give the old one back (this script always
# names --context $PROFILE, so nothing here depends on the default).
if [ -n "$PREV_CONTEXT" ] && [ "$PREV_CONTEXT" != "$PROFILE" ]; then
  kubectl config use-context "$PREV_CONTEXT" >/dev/null && echo "default kubectl context restored to $PREV_CONTEXT"
fi
$K -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=600s

# ---- 4. Ollama ------------------------------------------------------------------------
step "4. Ollama in Docker, on minikube's network, with the two pinned models"
if ! docker inspect rahbar-ollama >/dev/null 2>&1; then
  gpu=""
  docker info 2>/dev/null | grep -qi 'runtimes:.*nvidia' && gpu="--gpus all" && echo "NVIDIA GPU found: Ollama will use it"
  docker run -d --name rahbar-ollama --network "$PROFILE" $gpu -v rahbar-ollama:/root/.ollama "$OLLAMA_IMAGE" >/dev/null
else
  docker start rahbar-ollama >/dev/null
fi
timeout 120 bash -c "until docker exec rahbar-ollama ollama list >/dev/null 2>&1; do sleep 2; done"
docker exec rahbar-ollama ollama pull nomic-embed-text
docker exec rahbar-ollama ollama pull llama3.2:3b
OLLAMA_IP=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$PROFILE\").IPAddress}}" rahbar-ollama)
echo "Ollama at $OLLAMA_IP:11434"

# ---- 5. manifests ---------------------------------------------------------------------
step "5. Apply the manifests (Docker Hub images @ $SHA)"
fix() {   # host-specific values and image names; everything else exactly as committed
  sed -e "s#http://host.minikube.internal:11434#http://$OLLAMA_IP:11434#" \
      -e "s#192.168.65.254/32#$OLLAMA_IP/32#" \
      -e "s#image: rahbar-\(api\|indexer\|ui\):dev#image: $HUB/rahbar-\1:$SHA#"
}
old_url=$($K -n rahbar get configmap rahbar-config -o jsonpath='{.data.OLLAMA_URL}' 2>/dev/null || true)
kubectl kustomize k8s/overlays/dev | fix > "$STATE/applied.yaml"
for attempt in 1 2 3 4 5 6; do   # the ingress webhook can refuse for a few seconds after start
  $K apply -f "$STATE/applied.yaml" >/dev/null && break
  [ "$attempt" = 6 ] && die "kubectl apply failed 6 times"
  echo "apply refused (attempt $attempt), retrying in 10 s"; sleep 10
done
if [ -n "$old_url" ] && [ "$old_url" != "http://$OLLAMA_IP:11434" ]; then
  echo "Ollama moved ($old_url -> $OLLAMA_IP): restarting the api to read the new ConfigMap"
  $K -n rahbar rollout restart deploy/api >/dev/null
fi
$K -n rahbar rollout status statefulset/chroma --timeout=600s
$K -n rahbar rollout status deploy/redis --timeout=600s

# ---- 6. corpus + first index (first run only) -----------------------------------------
if [ "$($K -n rahbar get job indexer -o jsonpath='{.status.succeeded}' 2>/dev/null)" != "1" ]; then
  step "6a. Download and verify the 7 PDFs (python in a throwaway container)"
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$REPO:/w" -w /w python:3.12-slim \
    sh -c "pip install -q --target /tmp/p httpx pyyaml && PYTHONPATH=/tmp/p python corpus/fetch.py && PYTHONPATH=/tmp/p python corpus/fetch.py --verify"

  step "6b. Copy the PDFs into the corpus PVC"
  $K -n rahbar delete pod corpus-loader --ignore-not-found >/dev/null
  fix < k8s/base/corpus-loader.yaml | $K apply -f - >/dev/null
  $K -n rahbar wait --for=condition=Ready pod/corpus-loader --timeout=600s
  $K cp corpus/raw/. rahbar/corpus-loader:/corpus
  $K -n rahbar exec corpus-loader -- python -m pipeline.run --verify-corpus
  $K -n rahbar delete pod corpus-loader --wait=false >/dev/null

  step "6c. The real indexer Job (OCR + embeddings; 5-20 minutes on a CPU)"
  $K -n rahbar delete job indexer --ignore-not-found >/dev/null
  fix < k8s/base/indexer-job.yaml | $K apply -f - >/dev/null
  $K -n rahbar wait --for=condition=complete job/indexer --timeout=2400s || die "indexer Job did not finish: $K -n rahbar logs job/indexer"
  $K -n rahbar logs job/indexer | tail -3
else
  step "6. Index already built (job/indexer Complete): skipping the corpus and indexing"
fi

# ---- 7. ready? ------------------------------------------------------------------------
step "7. Waiting for all pods to be Ready"
$K -n rahbar rollout status deploy/api --timeout=900s
$K -n rahbar rollout status deploy/ui --timeout=300s
$K -n rahbar get pods
$K -n rahbar exec deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz', timeout=10).read().decode())"

# ---- 8. open it -----------------------------------------------------------------------
step "8. Ingress on 127.0.0.1:$PORT"
if ! curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
  nohup kubectl --context "$PROFILE" -n ingress-nginx port-forward svc/ingress-nginx-controller "$PORT:80" \
    > "$STATE/port-forward.log" 2>&1 &
  echo $! > "$STATE/port-forward.pid"
fi
code=000
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: rahbar.local' "http://127.0.0.1:$PORT/" || true)
  [ "$code" = 200 ] && break; sleep 2
done
[ "$code" = 200 ] || die "the Ingress did not answer (see $STATE/port-forward.log)"
echo "RAHBAR answers through the Ingress: HTTP $code"

if ! grep -q 'rahbar.local' /etc/hosts; then
  echo "Adding '127.0.0.1 rahbar.local' to /etc/hosts so the browser can use the Ingress host name"
  echo "127.0.0.1 rahbar.local" | sudo tee -a /etc/hosts >/dev/null
fi
command -v xdg-open >/dev/null && xdg-open "http://rahbar.local:$PORT/" >/dev/null 2>&1 || true
printf '\nDone: http://rahbar.local:%s  (first answer can take a minute on a CPU)\nStop later with: bash start-rahbar.sh --stop\n' "$PORT"
