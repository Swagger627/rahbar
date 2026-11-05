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

Publishing happens on a push to `dev` or `main`. Both are protected branches, so in practice
that means **after an approved pull request is merged**. Pull requests themselves never
publish anything.

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
