# CI/CD

Two GitHub Actions workflows (section 6). Both run on GitHub's Linux runners, not on the
laptop.

| Workflow | When | What it proves |
| --- | --- | --- |
| [`ci.yml`](../.github/workflows/ci.yml) | every pull request into `dev` or `main`, every push to `dev`, or by hand | the code is linted and tested, the manifests are valid, the images build and are free of fixable HIGH/CRITICAL CVEs, and the whole system answers correctly on a real cluster |
| [`cd.yml`](../.github/workflows/cd.yml) | every push to `dev` or `main` (that is, a merged pull request), or by hand | the merged commit is re-tested, its images are scanned and published to Docker Hub by commit SHA, and those exact images deploy and pass a smoke test |

## ci.yml jobs

| Job | Needs | What it runs | Fails when |
| --- | --- | --- | --- |
| `lint` | – | `ruff check api indexer eval corpus`; `mypy` on `api/app` and `indexer/pipeline` | any lint or type error |
| `test` | – | `pytest` for api, indexer and eval with the stub retriever and stub model (no network) | a test fails, or coverage of the api's `app` package (or the indexer's `pipeline`) is under 60% |
| `manifests` | – | `kubectl kustomize` of `k8s/overlays/prod` and `dev`, plus the two hand-applied manifests, through `kubeconform -strict` | a manifest has an unknown field or a wrong type |
| `build` | lint, test, manifests | builds the three images with BuildKit and caches the layers. **It never pushes.** | a Dockerfile does not build |
| `scan` | build | Trivy on each image (taken from the build cache), `HIGH,CRITICAL`, `ignore-unfixed` | a HIGH or CRITICAL vulnerability with a fix available |
| `e2e-eval` | test, manifests | [`scripts/e2e-kind.sh`](../scripts/e2e-kind.sh) on a kind cluster (details below) | any step of the deployment fails, or the eval gate fails |
| `checker` | – | `python scripts/check_submission.py`, unchanged | the checker still finds gaps. It is red until later steps add the remaining evidence and documents. **That red result is the honest state, and is not hidden.** |

Every action is pinned to a full commit SHA, with the version in a comment. The workflows
only get `contents: read`. pip downloads and image layers are cached.

## The e2e test (`scripts/e2e-kind.sh`)

It runs the real system end to end, with two differences from the laptop:

- **The corpus.** It uses the three fictional fixture documents in
  [`eval/fixtures/`](../eval/fixtures/README.md). The university PDFs never leave the laptop.
- **Where Ollama runs.** GitHub's runner cannot reach the laptop's Ollama, so the same Ollama
  version (`ollama/ollama:0.40.14`) runs as a container on the runner. It uses the **same two
  models** (`nomic-embed-text`, `llama3.2:3b`), pulled on GitHub's machine and cached there,
  never on the laptop. Because of this, only Ollama's address changes: `OLLAMA_URL` in the
  ConfigMap, and the two NetworkPolicy rules that allow it. The script prints the changed
  lines and keeps the full applied YAML in the artifact.

The steps:

1. Start Ollama and pull the two models.
2. Build the images and load them into kind (in CD: pull the published SHA images instead).
3. Install the ingress-nginx controller.
4. Apply the prod overlay. Wait for Chroma and Redis.
5. Fill the corpus PVC through the same `corpus-loader` pod used on minikube.
6. Run the **real indexer Job** (`indexer-job.yaml`): real extraction, real embeddings.
7. Wait on `kubectl rollout status deploy/api`, then check `/readyz` says `ready`.
8. Through the Ingress (`Host: rahbar.local`):
   - ask one question;
   - run `eval/run_eval.py` on `golden.fixture.yaml`.

Logs, the applied manifests, the readiness answer, the smoke answer and `results.json` are
uploaded as an artifact, even when a step fails.

### The fixture regression gate

`run_eval.py` applies the section 5.4 gate:

- citation validity must be at least 0.95;
- every must-abstain question must abstain;
- recall@5 must not drop more than 5 points below the baseline.

The baseline for the fixture set is `eval/fixtures/baseline.json`, and it can only come from a
real passing run. So, on the first run, when that file does not exist yet:

- the script runs with `--bootstrap-baseline`. Citation validity and abstention are still
  gated;
- the baseline is written into the artifact only if they pass.

The baseline taken from that real CI run is then committed. From then on, every run also
gates on recall@5.

## cd.yml jobs

`test` → `build-push` (`needs: test`) → `deploy` (`needs: build-push`).

- **test**: lint, unit tests with coverage, and kubeconform, again on the merged commit.
- **build-push**:
  1. builds each image;
  2. runs Trivy first;
  3. only then logs in to Docker Hub and pushes
     `docker.io/<DOCKERHUB_USERNAME>/rahbar-<api|indexer|ui>:<commit SHA>`.

  There is never a `:latest` tag.
- **deploy**: a new kind cluster on the runner runs the same e2e script with those published
  SHA images. It waits on `kubectl rollout status`, checks readiness and smoke tests through
  the Ingress.

Publishing happens only on a push to `dev` or `main`. A manual "Run workflow" on any other
branch runs the tests but skips `build-push` and `deploy`. Pull requests themselves never
publish anything.

- **The team rule:** a push to `dev` or `main` happens only by merging a pull request that
  passed `ci.yml`.
- **What GitHub enforces.** Branch protection on `main` (a PR, 1 approval, the 11 `ci.yml`
  checks, no admin bypass) and on `dev` (a PR and the same checks) was set on 2026-11-06.
  It was shown blocking merges while the repository was public
  (`docs/evidence/github-branch-protection.txt`, `docs/evidence/github-gate-demo.txt`).
  A private repository on the free plan keeps these rules but does not enforce them.
  Enforcement needs public visibility or GitHub Pro.

### Secrets needed

Add these under **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `DOCKERHUB_USERNAME` | the Docker Hub account (or organisation) that owns the `rahbar-*` repositories |
| `DOCKERHUB_TOKEN` | a Docker Hub **access token** (Account settings → Personal access tokens) with Read & Write scope. Not the account password |

If either secret is missing, `build-push` stops at its first step with
`BLOCKED: add the repository secrets ...`. Nothing is published, and `deploy` does not run.

### What CD does not do

It does not deploy to the laptop's minikube. GitHub cannot reach it, and opening the laptop's
cluster to the internet, or uploading its kubeconfig, would be a security hole. Deploying to
minikube stays a manual, explicit step.

## Deploy a version to minikube

Every published commit has its own images. To run commit `<sha>` on the laptop:

```bash
SHA=<the full commit SHA from the cd.yml run>
U=<DOCKERHUB_USERNAME>
kubectl --context minikube -n rahbar set image deploy/api api=docker.io/$U/rahbar-api:$SHA
kubectl --context minikube -n rahbar set image deploy/ui ui=docker.io/$U/rahbar-ui:$SHA
kubectl --context minikube -n rahbar rollout status deploy/api --timeout=300s
kubectl --context minikube -n rahbar rollout status deploy/ui --timeout=300s
kubectl --context minikube -n rahbar get pods
```

Then check readiness through the Ingress, as in [DEPLOYMENT.md](DEPLOYMENT.md).

The indexer image is only used by Jobs. To re-index with a new indexer image, set it on the
CronJob first:

```bash
kubectl --context minikube -n rahbar set image cronjob/reindex indexer=docker.io/$U/rahbar-indexer:$SHA
```

**Always pass `--context minikube`.** The laptop's default context belongs to a different
project.

## Roll back

The api Deployment keeps its previous ReplicaSets, so either:

```bash
kubectl --context minikube -n rahbar rollout undo deploy/api
kubectl --context minikube -n rahbar rollout status deploy/api --timeout=300s
```

or deploy the previous good SHA with the `set image` commands above. Because images are tagged
by commit, "the previous version" is always an exact, known commit, never a moving `:latest`.
`kubectl --context minikube -n rahbar rollout history deploy/api` lists the revisions.

## Security fixes (Step 4.3)

The first CI runs ([42089834821](https://github.com/Swagger627/rahbar/actions/runs/42089834821),
[42091124707](https://github.com/Swagger627/rahbar/actions/runs/42091124707)) failed the
`scan` job. That was correct: Trivy found HIGH and CRITICAL vulnerabilities that already
had fixes. Nothing was ignored or hidden. Every one was fixed by an upgrade.

### What was vulnerable, and what changed

| Image | Package | Before | CVEs (Trivy) | After | How |
| --- | --- | --- | --- | --- | --- |
| api | starlette | 0.46.2 | CVE-2025-62727, CVE-2026-48818, CVE-2026-54283 (HIGH; fixed by 1.3.1) | 1.10.0 | FastAPI 0.115.12 capped Starlette below 0.47, so FastAPI went to 0.162.0 (it allows Starlette 1.x). Starlette is now pinned explicitly |
| api, indexer | Debian `apt` | 3.0.3 | CVE-2026-109518 (HIGH) | 3.0.4 | base image `python:3.12.15-slim-trixie` → `3.12.16-slim-trixie` (new pinned digest) |
| indexer | pdfminer.six | 20250327 | CVE-2025-64512, CVE-2025-70559 (HIGH) | 20251230 | pdfplumber 0.11.6 → 0.11.9, the first version that pins the fixed pdfminer.six |
| indexer | pypdf | 5.4.0 | 17 CVEs (HIGH; fixed by 6.14.1 to 6.21.4) | removed from the image | the pipeline never imports it. Only the tests use it, so it moved to `requirements-dev.txt` at 6.21.4 |
| ui | brotli-libs | 1.2.0-r1 | CVE-2026-111497 (**CRITICAL**) | 1.2.1-r0 | same pinned `nginx-unprivileged:1.30.6-alpine3.24` base (already the newest 1.30 build), plus `apk upgrade` of just these packages |
| ui | curl, libcurl | 8.22.0-r0 | CVE-2026-92392 (HIGH) | 8.23.0-r0 | as above |
| ui | libexpat | 2.9.0-r0 | CVE-2026-113351, CVE-2026-117610, CVE-2026-118370 (HIGH) | 2.10.0-r0 | as above |

### Compatibility checks

- **Dependencies:** `pip check` passes in both new images and in the local virtualenv.
- **Unit tests:** api 117 passed (coverage 96%), indexer 65, eval 53. ruff and mypy are
  clean.
- **API contract:** the OpenAPI schema of the old and the new api image was compared.
  - The routes (`/api/ask`, `/healthz`, `/readyz`, `/metrics`) and our request and response
    models are identical.
  - The only difference is in FastAPI's own 422 error schema, which now also documents two
    optional fields (`ctx`, `input`). Nothing a caller relies on changed.
- **The images start correctly:** both new images were started with a read-only root
  filesystem.
  - **api** (uid 10001): `/healthz` 200, `/readyz` 200, `/metrics` serves the `rahbar_*`
    metrics, `/api/ask` 200 (with `X-Cache`), and a bad body gets 422.
  - **ui** (uid 101, port 8080): `/healthz` 200, `/` 200.
- **Test-only warning:** Starlette 1.x prints one deprecation warning in the tests about its
  test client and `httpx`. It does not affect the running service.

### PDF extraction before and after

The extraction libraries changed (pdfplumber and pdfminer.six). So the real extract, clean and
chunk stages were run over the real corpus twice:
- once with the **old** indexer image (pdfplumber 0.11.6, pdfminer.six 20250327);
- once with the **new** one (0.11.9, 20251230).

Neither run used Chroma or embeddings, and the production index was not touched. The commands
are in `scripts/extraction_snapshot.py`; the comparison is `scripts/compare_snapshots.py`.

| Check | Before | After |
| --- | --- | --- |
| SHA-256 of the corpus files | 7 verified | 7 verified |
| Pages / OCR pages | 247 / 41 | 247 / 41, every page's text byte-identical |
| Chunks | 431 | 431, identical ids, headings, text and metadata |
| Versions and superseded flags | 2023-08 rules superseded, the rest current | identical |
| Clause 5.8 (2023 rules) | page 17, "Repeating a Course" | identical |
| Clause 1.21 (2026 rules) | page 10, "Attendance Requirements" | identical |
| Campus table rows | 30 | 30, identical. Civil Engineering (BS, MS, PhD) still marked under LHR |

**Total differences: 0.** The facts the index is built from did not change, so the real
index on minikube does not need re-indexing.

### Scan results after the fixes

CI run [42097509816](https://github.com/Swagger627/rahbar/actions/runs/42097509816), with the
same Trivy gate (HIGH,CRITICAL, fixable, OS and library packages, exit code 1):
- `scan (api)`, `scan (indexer)` and `scan (ui)` all **passed**, with 0 findings.

In that same run, `e2e-eval` failed for a reason unrelated to the upgrades. The ingress-nginx
admission webhook refused the first `kubectl apply` for a few seconds after the controller
reported ready. `scripts/e2e-kind.sh` now retries the apply up to six times, 10 s apart.

A first attempt to also wait for the webhook's set-up Jobs broke run
[42098461675](https://github.com/Swagger627/rahbar/actions/runs/42098461675): those Jobs are
already gone by then, so `kubectl wait` found nothing and failed. That wait was removed.

### Remaining risk

- New CVEs are published every week, so a green scan today can turn red later without any code
  change. That is the point of the gate: when it happens, upgrade the same way, and rerun the
  snapshot comparison if an extraction library changes.
- The ui fix upgrades Alpine packages on top of a pinned base. When a newer
  `nginx-unprivileged` 1.30 build ships with these versions, switch to its digest and drop the
  `apk upgrade` line.
