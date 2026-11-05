# Deploying RAHBAR on minikube (Windows)

Status (2026-11-05): **deployed and verified on a live minikube cluster** on the
development laptop. That covers the real index, three api replicas, the Ingress, the
Redis cache, Calico-enforced NetworkPolicies, persistence and the HPA. Raw output is in
`docs/evidence/minikube-2026-11-05.txt`, and the full results are under "Live results"
at the end of this file.

Each command block names its shell. Run everything from the repository folder
(`D:\SCD assignment 2\rahbar`).

> **Always pass `--context minikube`.** This laptop's kubectl also knows an unrelated
> cluster (`kind-civicpulse`). minikube is started with `--keep-context`, so the default
> context is never switched, and every command names minikube explicitly.

## What runs where

| Piece | Where | Storage |
| --- | --- | --- |
| api (3 replicas), ui, Chroma, Redis, indexer Job, reindex CronJob | minikube, namespace `rahbar` | Chroma: PVC `data-chroma-0`. Redis: PVC `redis-data`. PDFs: PVC `corpus`. |
| Ollama with `nomic-embed-text` and `llama3.2:3b` | the Windows laptop itself (`ollama-host`), on the GPU | `%USERPROFILE%\.ollama` |

The Chroma index used during development lives in a Docker volume, `rahbar-chroma-real`,
outside Kubernetes. **The cluster does not see it.** The cluster's Chroma PVC starts empty,
and the indexer Job builds its own index from the same seven PDFs.

## Resource check (do this first)

minikube with the docker driver runs inside Docker Desktop's virtual machine (limited to
7.9 GB here). Ollama runs separately, on Windows and the GPU.

**PowerShell**
```powershell
"available MB: " + [int](Get-Counter '\Memory\Available MBytes').CounterSamples[0].CookedValue
docker ps --format '{{.Names}} {{.Status}}'
Get-PSDrive C, D
```

You need about 5 GB available before starting.

What we saw on 2026-11-05:
- After closing the browser and other large apps, 5.8 GB was available.
- Starting Docker took about 3 GB, and minikube more on top of that.
- Free memory then ran at 0.3-1.8 GB, and the laptop stayed usable.
- minikube used 2.2-2.6 GiB of its 4 GiB (`kubectl top node`).

## 1. Install minikube (one time)

With admin rights:

**PowerShell**
```powershell
winget install --id Kubernetes.minikube -e
```

On this laptop the installer's admin prompt could not be shown, so minikube was installed
**without admin rights instead**: the official single binary from the same GitHub release,
checked against its published SHA-256, in a user folder. It is not on PATH, so the
commands below use `$MK`:

**PowerShell**
```powershell
$MK = "$env:LOCALAPPDATA\Programs\minikube\minikube.exe"
& $MK version          # v1.39.0
```

## 2. Start the cluster

**PowerShell**
```powershell
& $MK start --driver=docker --cni=calico --memory=4096 --cpus=4 --keep-context=true
& $MK addons enable metrics-server
& $MK addons enable ingress
kubectl --context minikube get pods -A
```

- The first start downloads about 1.5 GB (the base image and a Kubernetes preload). That
  took about 15 minutes here.
- minikube v1.39 uses Kubernetes v1.37 with **containerd**.
- Calico enforces the NetworkPolicies; without it they are ignored.
- Wait until `calico-node`, `calico-kube-controllers`, `ingress-nginx-controller` and
  `metrics-server` are `Running`.

If Docker Desktop itself will not start, this laptop's Docker 4.91 has a known socket bug.
The fix used here, only with the owner's approval each time, is to rename
`%LOCALAPPDATA%\Docker\run` and `%LOCALAPPDATA%\docker-secrets-engine` while Docker is
stopped.

## 3. Build the images and load them into minikube

**PowerShell**
```powershell
docker build -t rahbar-api:dev api
docker build -t rahbar-indexer:dev indexer
docker build -t rahbar-ui:dev ui
& $MK image load rahbar-api:dev --overwrite=true
& $MK image load rahbar-indexer:dev --overwrite=true
& $MK image load rahbar-ui:dev --overwrite=true
```

After loading a rebuilt image, restart the pods that use it, for example
`kubectl --context minikube -n rahbar rollout restart deploy/api`.

## 4. Apply the manifests

**PowerShell**
```powershell
kubectl --context minikube apply -k k8s/overlays/dev
kubectl --context minikube get pods,pvc -n rahbar
```

What to expect:
- Chroma, Redis and the ui become `Running`, and the three PVCs become `Bound`.
- The **api pods are `Running` but `0/1`**. Their log says "index check failed; not ready:
  collection 'rahbar' not found". That is correct until step 7.

Two problems were found and fixed here:
- Chroma crashed with *"found string tcp://10.96.x.x:8000, expected u16 for key PORT"*.
  Kubernetes injects `CHROMA_PORT=tcp://...` for a Service named `chroma`, and Chroma reads
  every `CHROMA_*` variable as its own config. The pods now set
  `enableServiceLinks: false`.
- The api pods were restarted again and again while Chroma was down. The start-up index
  check waited past the 60 s startup probe. It now runs in the background, so the pod waits
  as not-ready instead.

## 5. Check the path to Ollama

Ollama stays bound to `127.0.0.1:11434` on Windows. No firewall rule and no
`OLLAMA_HOST=0.0.0.0` are needed.

**PowerShell**
```powershell
& $MK ssh "grep host.minikube.internal /etc/hosts"
& $MK ssh "curl -s -m 5 http://host.minikube.internal:11434/api/version"
```

- The first command should print `192.168.65.254` (verified 2026-11-05). That is the one
  address the NetworkPolicies allow on TCP 11434, for the api and the indexer only. If it
  differs, change both Ollama rules in `k8s/base/networkpolicy.yaml` and apply again.
- The second should print `{"version":"0.40.14"}`. If it fails, check that Ollama is
  running at all (`curl.exe http://127.0.0.1:11434/api/version`). Closing apps had stopped
  it once.

## 6. Load the corpus (one time)

The seven PDFs stay outside Git and every image. They are copied into the `corpus` PVC
through a short-lived loader pod.

**PowerShell**
```powershell
.venv\Scripts\python.exe corpus\fetch.py --verify
kubectl --context minikube apply -f k8s/base/corpus-loader.yaml
kubectl --context minikube wait -n rahbar --for=condition=Ready pod/corpus-loader --timeout=180s
kubectl --context minikube cp corpus/raw/. rahbar/corpus-loader:/corpus
kubectl --context minikube exec -n rahbar corpus-loader -- python -m pipeline.run --verify-corpus
kubectl --context minikube delete pod corpus-loader -n rahbar
```

- The verify step must log `"corpus verified", "files": 7`. With no `_index.json`, it now
  fails instead of passing quietly.
- In **Git Bash**, put `MSYS_NO_PATHCONV=1` in front of the `kubectl cp` line. Git Bash
  otherwise rewrites `rahbar/corpus-loader:/corpus` into a Windows path, and nothing is
  copied. PowerShell does not have this problem.

## 7. Build the first real index

**PowerShell**
```powershell
kubectl --context minikube apply -f k8s/base/indexer-job.yaml
kubectl --context minikube logs -n rahbar job/indexer -f
kubectl --context minikube get job indexer -n rahbar
```

The log shows, in order:
1. `corpus verified` (7 files);
2. `chunked` for each document;
3. `embedded` with `dimension: 768`;
4. `indexer finished` with `chunks: 431`.

The Job shows `Complete 1/1` after about 3 minutes.

Memory: the indexer peaks at **1,117 MiB** (cgroup `memory.peak`, measured on minikube)
during OCR of the scanned PDFs. A 1 GiB limit was OOMKilled (exit 137) twice. The limit is
now 2 GiB. `kubectl top` never saw more than 784 Mi, because it samples too rarely to catch
the spike.

## 8. Check the api, ui and Ingress

**PowerShell**
```powershell
kubectl --context minikube rollout status deploy/api -n rahbar
kubectl --context minikube exec -n rahbar deploy/api -- python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz').read())"
```

All three api pods should be `1/1`, and `/readyz` should report
`rows: 431, embed_model: nomic-embed-text, dimension: 768, redis: ok`.

To reach the Ingress from Windows, either run `minikube tunnel` (needs admin, for port 80),
or forward a local port to the ingress controller. The forward still goes through the real
Ingress rules:

**PowerShell (second window, leave running)**
```powershell
kubectl --context minikube -n ingress-nginx port-forward svc/ingress-nginx-controller 18081:80
```

**PowerShell**
```powershell
curl.exe -s -D - -H "Host: rahbar.local" -H "Content-Type: application/json" -d "{\"question\": \"What is the tuition fee per credit hour for bachelor's programmes in the 2026-27 academic year?\"}" http://127.0.0.1:18081/api/ask
curl.exe -s -o NUL -w "%{http_code}\n" -H "Host: rahbar.local" http://127.0.0.1:18081/
```

- The first request answers "Rs. 12,000", citing `3. Tuition Fee`, p1, with
  `X-Cache: miss`.
- Repeating it gives `X-Cache: hit`, with a new `request_id`.
- The ui returns `200`; a wrong `Host` returns `404`.

## 9. Check the NetworkPolicies on the live cluster

**PowerShell**
```powershell
kubectl --context minikube exec -n rahbar deploy/ui -- wget -T 4 -O - http://api:8000/healthz
kubectl --context minikube exec -n rahbar deploy/ui -- wget -T 4 -O - http://chroma:8000/api/v2/heartbeat
kubectl --context minikube exec -n rahbar deploy/api -- python -c "import socket; socket.create_connection(('example.com', 443), timeout=4)"
```

- The first must succeed (ui to api is allowed).
- The other two must **time out**.
- Use `-O -`, not `-O /dev/null`: busybox `wget -O /dev/null` fails in this container even
  when the connection works, which once made an allowed path look blocked.

"Live results" lists all 13 paths that were tested.

## 10. Scheduled re-index

The CronJob is **enabled**: it runs Sundays at 03:00, after the first index and three
manual runs worked. A manual re-index, or pausing the schedule:

**PowerShell**
```powershell
kubectl --context minikube create job --from=cronjob/reindex manual-reindex-4 -n rahbar
kubectl --context minikube patch cronjob reindex -n rahbar -p '{\"spec\":{\"suspend\":true}}'
```

A re-index builds a new collection beside the old one and swaps it in (ADR 0001). The new
collection id makes the Redis cache miss for every old answer (ADR 0003). Both were
verified live.

## 11. Autoscaling and resource numbers

**PowerShell**
```powershell
kubectl --context minikube get hpa -n rahbar
kubectl --context minikube top pods -n rahbar
```

## 12. Stop without losing data

**PowerShell**
```powershell
& $MK stop
```

`minikube stop` keeps the cluster, its PVCs (corpus, Chroma index, Redis) and the loaded
images; `& $MK start --keep-context=true` brings them back. **Do not run `minikube delete`**:
it deletes the cluster and every PVC. The Docker volume `rahbar-chroma-real` is separate,
and neither command touches it.

## Live results (2026-11-05)

minikube v1.39.0, Kubernetes v1.37.0, containerd, Calico, 4 GiB / 4 CPUs, on Docker
Desktop 4.91 / WSL2.

| Stage | Result |
| --- | --- |
| Namespace, ConfigMap | applied; real values (chroma, ollama-host, nomic-embed-text, floor 0.65, cache 0.90 / 3600 s, Redis URL) |
| Calico, ingress, metrics-server | all `Running` |
| Storage | PVCs `corpus` 1Gi, `data-chroma-0` 2Gi, `redis-data` 1Gi `Bound` |
| Corpus | copied (35 MB); **7/7 SHA-256 verified inside the cluster** |
| First index (Job `indexer`) | `Complete 1/1` in 3m9s: 431 chunks, dimension 768 (after the 1Gi OOM fix) |
| Collection | `/readyz`: rows 431, `nomic-embed-text`, 768 |
| api | **3/3 ready** behind the Service; rolling restart kept 3 ready pods throughout |
| ui and Ingress | ui `200`; unknown host `404`; `/api/ask` answered through the Ingress |
| Answers (sample) | tuition "Rs. 12,000" (cited); clause 1.21 "100% attendance" (cited); hostel fee declined; o05 (Civil Engineering) declined this time, model variance |
| Cache | paraphrase hit in 0.046 s, fresh `request_id` |
| Re-index from the CronJob | `manual-reindex-2` and `-3` `Complete`; collection id `f9ce...` changed to `aebb...`; the cached question then **missed**, then hit under the new id |
| Persistence | after deleting the Chroma and Redis pods: still 431 rows, 3 cache keys, next ask a cache hit |
| HPA | `cpu: 3-6%/70%`, 3 replicas (min 3, max 8) |
| `kubectl top` | api 71-79Mi each (limit 384Mi), Chroma 26-37Mi, Redis 3-10Mi, ui 3Mi; indexer peak 1,117 MiB (cgroup) |

**NetworkPolicies, enforced live by Calico (13 paths):**
- **Allowed and connected:** ui to api; api to Chroma; api to Redis; api to Ollama on the
  host. The indexer reaching Chroma and Ollama is shown by the completed Jobs.
- **Blocked (timed out):** ui to Chroma, Redis and Ollama; api to the internet; api to the
  ui; and a test pod with an unknown label (`app=netcheck`) to the api, Chroma, Redis and
  Ollama.

**Not done:**
- the full 16-question evaluation through the cluster's Ingress (done outside Kubernetes in
  Steps 3.5B-3.5D);
- load tests that would make the HPA scale up;
- `minikube tunnel` on port 80 (it needs admin; the port-forward to the ingress controller
  was used instead).
