# Deploying RAHBAR on minikube (Windows)

Status (2026-11-04):
- The manifests are configured for the real system and pass the offline checks.
- The real images were rebuilt and tested as containers.
- **No live cluster has been started yet.** minikube is not installed on the development
  laptop, and there was not enough free memory (see "Resource check").

Everything below is the exact procedure for the first live deployment. Nothing here has
been run against a cluster yet.

Each command block names its shell. PowerShell means Windows PowerShell; Git Bash means
the Git for Windows bash. Run everything from the repository folder
(`D:\SCD assignment 2\rahbar`).

> **Always pass `--context minikube`.** This laptop's kubectl also knows an unrelated
> cluster (`kind-civicpulse`). Every `kubectl` command below names the minikube context,
> so nothing can land on the wrong cluster.

## What runs where

| Piece | Where | Storage |
| --- | --- | --- |
| api (3 replicas), ui, Chroma, Redis, indexer Job, reindex CronJob | minikube, namespace `rahbar` | Chroma: PVC from the StatefulSet. Redis: PVC `redis-data`. PDFs: PVC `corpus`. |
| Ollama with `nomic-embed-text` and `llama3.2:3b` | the Windows laptop itself (`ollama-host`), on the GPU | `%USERPROFILE%\.ollama` |

The Chroma index used during development lives in a Docker volume, `rahbar-chroma-real`,
outside Kubernetes. **The cluster does not see it.** Its Chroma PVC starts empty, and the
indexer Job builds the cluster's own index from the same seven PDFs.

## Resource check (do this first)

minikube with the docker driver runs inside Docker Desktop's virtual machine. That VM is
limited to 7.9 GB on this laptop. Ollama runs separately, on Windows and the GPU.

**PowerShell**
```powershell
"available MB: " + [int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue
docker info --format '{{.MemTotal}}'
docker ps --format '{{.Names}} {{.Status}}'
Get-PSDrive C, D
```

Go ahead only if **at least about 5 GB** is available: 4 GB for minikube, plus room for
Windows and Ollama's model. Measured on 2026-11-04:
- 0.45-0.70 GB available, with Chrome holding about 2.5 GB;
- Ollama's runner holding about 1.4 GB, with its model also on the GPU;
- 1.3 GB of commit headroom.

So the cluster was **not** started. Closing browser tabs and other large apps frees the
memory. The assignment allows 4 GB for minikube when Ollama runs on the laptop (section
9), so 4 GB is used below instead of the Makefile's 6 GB.

## 1. Install minikube (one time)

minikube is not installed yet. Installing it is a decision for the laptop's owner.

**PowerShell**
```powershell
winget install --id Kubernetes.minikube -e
minikube version
```

## 2. Start the cluster

**PowerShell**
```powershell
minikube start --driver=docker --cni=calico --memory=4096 --cpus=4
minikube addons enable metrics-server
minikube addons enable ingress
kubectl --context minikube get pods -A
```
Calico enforces the NetworkPolicies; without it they are ignored. Wait until the
`calico-*`, `ingress-nginx-controller` and `metrics-server` pods are `Running`.

## 3. Build the images and load them into minikube

The images must be built from the current code. Those built before Step 4.1 contained
early stub code.

**PowerShell**
```powershell
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev indexer
docker build -t rahbar-ui:dev ui
minikube image load rahbar-api:dev
minikube image load rahbar-indexer:dev
minikube image load rahbar-ui:dev
```

## 4. Apply the manifests

**PowerShell**
```powershell
kubectl --context minikube apply -k k8s/overlays/dev
kubectl --context minikube get pods,pvc -n rahbar
```

What to expect:
- Chroma, Redis and the ui become `Running`, and the three PVCs become `Bound`.
- The **api pods stay `0/1` (not ready)**: `/readyz` answers 503, because the Chroma
  collection does not exist yet. That is correct.
- The reindex CronJob is created **suspended**.

## 5. Check the path to Ollama

Ollama stays bound to `127.0.0.1:11434` on Windows. No firewall rule and no
`OLLAMA_HOST=0.0.0.0` are needed. Docker Desktop forwards the host gateway to Windows'
localhost: on 2026-11-04, a plain container reached Ollama at
`host.docker.internal = 192.168.65.254`. The NetworkPolicies allow TCP 11434 to exactly that
address, for the api and the indexer only.

**PowerShell**
```powershell
minikube ssh "getent hosts host.minikube.internal"
kubectl --context minikube exec -n rahbar deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://host.minikube.internal:11434/api/version', timeout=5).read())"
```
- If the first command prints an address other than `192.168.65.254`, put that address in
  both Ollama rules in `k8s/base/networkpolicy.yaml`, then apply again (step 4).
- The second command should print `{"version":"0.40.14"}`.
- If it times out even with the right address, the smallest safe change is still **not**
  to expose Ollama on the network. Ask first, and consider binding Ollama only to the
  Docker/WSL adapter's address, never `0.0.0.0` on a laptop that joins public Wi-Fi.

## 6. Load the corpus (one time)

The seven PDFs stay outside Git and outside every image. They are copied into the `corpus`
PVC through a short-lived loader pod. Their SHA-256 values come from
`corpus/raw/_index.json`.

**PowerShell**
```powershell
.venv\Scripts\python.exe corpus\fetch.py --verify
kubectl --context minikube apply -f k8s/base/corpus-loader.yaml
kubectl --context minikube wait -n rahbar --for=condition=Ready pod/corpus-loader --timeout=120s
kubectl --context minikube cp corpus/raw/. rahbar/corpus-loader:/corpus
kubectl --context minikube exec -n rahbar corpus-loader -- python -m pipeline.run --verify-corpus
kubectl --context minikube delete pod corpus-loader -n rahbar
```
The verify step must log `"corpus verified", "files": 7`. `corpus/raw` also holds three
candidate PDFs that are not in `_index.json`; the indexer ignores them.

## 7. Build the first real index

**PowerShell**
```powershell
kubectl --context minikube apply -f k8s/base/indexer-job.yaml
kubectl --context minikube logs -n rahbar job/indexer -f
kubectl --context minikube get job indexer -n rahbar
```

The log should show, in order:
1. `corpus verified` (7 files);
2. `chunked` for each document (OCR on the scanned PDFs takes a few minutes);
3. `embedded` with `dimension: 768`;
4. `indexer finished` with `chunks: 431`.

The Job then shows `1/1` completions. Chroma is written only by this Job. The api has no
write path to it.

## 8. Check the api, ui and Ingress

**PowerShell**
```powershell
kubectl --context minikube rollout status deploy/api -n rahbar
kubectl --context minikube get pods -n rahbar -o wide
kubectl --context minikube exec -n rahbar deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz').read())"
```

- **api:** all three pods should be `1/1`, and `/readyz` should report
  `rows: 431, embed_model: nomic-embed-text, dimension: 768, redis: ok`.

The Ingress needs a tunnel on Windows. Leave it running in a second PowerShell window:

**PowerShell (second window)**
```powershell
minikube tunnel
```

**PowerShell**
```powershell
curl.exe -s -D - -H "Host: rahbar.local" -H "Content-Type: application/json" -d "{\"question\": \"What is the tuition fee per credit hour for bachelor's programmes in the 2026-27 academic year?\"}" http://127.0.0.1/api/ask
curl.exe -s -o NUL -w "%{http_code}\n" -H "Host: rahbar.local" http://127.0.0.1/
```

- **The answer:** the first request should answer "Rs. 12,000", citing `3. Tuition Fee`,
  p1, with the header `X-Cache: miss`.
- **The cache:** sending the same request again should give `X-Cache: hit`, with a new
  `request_id`.
- **The ui:** the second command should return `200`.

## 9. Check the NetworkPolicies on the live cluster

Allowed paths are shown by step 8 working: api to Chroma, api to Redis, api to Ollama, and
the Ingress to the api and ui. Denied paths:

**PowerShell**
```powershell
kubectl --context minikube exec -n rahbar deploy/ui -- wget -q -T 3 -O- http://chroma:8000/api/v2/heartbeat
kubectl --context minikube exec -n rahbar deploy/ui -- wget -q -T 3 -O- http://redis:6379
kubectl --context minikube exec -n rahbar deploy/api -- python -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)"
```
All three must **time out**:
- the ui may only talk to the api;
- the api may not reach the internet.

If one succeeds, check that Calico is running.

## 10. Scheduled re-index

Only after step 7 has worked:

**PowerShell**
```powershell
kubectl --context minikube patch cronjob reindex -n rahbar -p '{\"spec\":{\"suspend\":false}}'
kubectl --context minikube create job --from=cronjob/reindex manual-reindex-1 -n rahbar
```

The schedule is Sunday 03:00. A re-index builds a new collection beside the old one and
swaps it in (ADR 0001). The new collection id makes the Redis cache miss for every old
answer (ADR 0003).

## 11. Autoscaling and resource numbers

**PowerShell**
```powershell
kubectl --context minikube get hpa -n rahbar
kubectl --context minikube top pods -n rahbar
```
The HPA reads CPU from metrics-server. The `kubectl top` output is the evidence for the
resource requests and limits (section 4.5), and should be committed.

## 12. Stop without losing data

**PowerShell**
```powershell
minikube stop
```

`minikube stop` keeps the cluster, its PVCs (corpus, Chroma index, Redis) and the loaded
images. `minikube start` brings them back. **Do not run `minikube delete`**: it deletes the
cluster and every PVC in it. The development index in the Docker volume
`rahbar-chroma-real` is separate, and neither command touches it.

## Checks done without a cluster (2026-11-04)

- `kubectl kustomize` renders 27 objects for both the dev and prod overlays.
- **63 automated offline checks passed:**
  - every object is in `rahbar`;
  - the ConfigMap holds the real values, and no stub or `dummy-768`;
  - every ConfigMap key is read by the code, and every setting the code reads is in it
    (except `LLM_API_KEY` and `HOSTED_BASE_URL`);
  - every volume mount and PVC reference resolves;
  - every container is non-root, read-only, with no privilege escalation, all capabilities
    dropped, and requests and limits set;
  - the corpus is mounted read-only in the Job and the CronJob, with
    `restartPolicy: OnFailure` and `backoffLimit: 2`;
  - no `--dummy` anywhere; the CronJob is suspended with `concurrencyPolicy: Forbid`;
  - the api has 3 replicas and a `/readyz` probe with a 3 s timeout;
  - the Ingress timeout is 180 s;
  - only the api and the indexer may reach Ollama; default deny is in place; Redis accepts
    only the api.
- **Container pre-flight with the rebuilt images** (Docker, not Kubernetes):
  - the indexer image, non-root with a read-only root filesystem, verified all 7 PDFs from a
    read-only mount;
  - the api image, run with the ConfigMap's values against the real 431-chunk index, a
    temporary Redis and the host's Ollama (reached through the host gateway, as pods will),
    reported ready;
  - it answered "Rs. 12,000" with a valid citation (a cache miss, 1.7 s), and a paraphrase
    was then a cache hit in 0.04 s;
  - it used 77 MB of its 384 MB limit.
- **Not done, and needing a live cluster:**
  - Calico enforcement (allowed and denied paths);
  - the corpus PVC copy;
  - the indexer Job inside Kubernetes;
  - three ready replicas behind the Service;
  - the Ingress through `minikube tunnel`;
  - the HPA and `kubectl top`;
  - whether `host.minikube.internal` resolves to 192.168.65.254.
