# Release readiness (Step 5.3, 2026-11-06)

This is where RAHBAR stands before the final merges, checked against the handout itself,
not only the mechanical checker. **Nothing has been merged.** The merges are the owner's
decision.

## 1. Git and pull requests

| Item | State |
| --- | --- |
| Repository | `Swagger627/rahbar`. **Public** while this was written; the owner plans to make it private again (see section 2) |
| `main` | `e4eee4a`, the starter kit import |
| `dev` | `74ee458`, 1 commit ahead of `main` (`.gitignore` and line endings) |
| `rag/retrieval-improvement` | all the work, 29 commits ahead of `dev` (including the Step 5.3 commit), linear on top of it. Latest CI: every check green |
| PR #1 | `dev` → `main`, open. **Merging is blocked**: it needs 1 approving review, and its required checks have not run yet on its old head commit |
| PR #2 | `rag/retrieval-improvement` → `dev`, draft. All 11 required checks pass |
| PR #3 | a temporary demonstration of the eval gate. It is closed **without merging** once the demonstration is captured (`docs/evidence/github-gate-demo.txt`) |
| Working tree | clean |
| Not in Git, on purpose | `corpus/raw/` (the PDFs, verified by SHA-256 against `corpus/MANIFEST.md`), `.venv`, test caches, `.claude/` |
| Secrets in history | none. A scan of every commit for token and key patterns, PDFs, model files, databases, kubeconfigs and `.env` files found nothing |

## 2. Branch protection

Both rules are now in place and enforced, and checked with screenshots, while the repository
is public:
- **`main`:** a pull request, 1 approving review, all 11 `ci.yml` checks, conversation
  resolution, no force-push or deletion, and no admin bypass.
- **`dev`:** a pull request and the same 11 checks.

Details and evidence are in `docs/evidence/github-branch-protection.txt` and
`docs/evidence/github/`.

- **If the repository goes private again on the free plan:** GitHub keeps the rules but
  **stops enforcing them** (measured in Step 5.2: HTTP 403, PRs mergeable with no review).
- **GitHub Pro (or making it public) brings enforcement back:** the same rules apply to a
  private repository, and nothing else changes.
- **The approval on `main` needs a second person.** The author cannot approve their own pull
  request. MugheesHiader476 has write access and can approve once he has really reviewed it.
  No review has been faked.

## 3. Docker Hub and `cd.yml`

**Secrets: missing.** The repository has **no Actions secrets** (`gh secret list` returns
nothing). Until both exist, `cd.yml`'s `build-push` stops at its first step with
`BLOCKED: add the repository secrets ...`, and nothing is published.

Add them:
1. **Create a token:** on Docker Hub, go to **Account settings → Personal access tokens →
   Generate new token**. Name it `rahbar-github-actions`, with **Read & Write** access. Copy
   it once.
2. **Store both secrets:** on GitHub, go to **Swagger627/rahbar → Settings → Secrets and
   variables → Actions → New repository secret**, and add:
   - `DOCKERHUB_USERNAME`: your Docker Hub user name;
   - `DOCKERHUB_TOKEN`: the token from step 1.

   Or from a terminal (it asks for the value; nothing is printed):
   ```bash
   gh secret set DOCKERHUB_USERNAME --repo Swagger627/rahbar
   gh secret set DOCKERHUB_TOKEN --repo Swagger627/rahbar
   ```
3. Check that only the names show: `gh secret list --repo Swagger627/rahbar`.

**What `cd.yml` does (reviewed in Step 5.3):**

| Requirement | `cd.yml` |
| --- | --- |
| Runs on the intended events | `push` to `dev` or `main`. A manual "Run workflow" on any other branch now runs only the tests: `build-push` has `if: refs/heads/dev or main` (added in Step 5.3) |
| Preceded by real checks | `test` (ruff, mypy, pytest with coverage, kubeconform) runs on the merged commit. `build-push` needs `test`, and `deploy` needs `build-push`. Branch protection on `dev`/`main` makes the full `ci.yml` (including the e2e eval gate) pass before the merge, but only while protection is enforced (section 2) |
| Scan before publish | each image is built and loaded, scanned by Trivy (HIGH/CRITICAL, fixable, exit code 1), and **only then** pushed |
| Immutable tags | `docker.io/<DOCKERHUB_USERNAME>/rahbar-{api,indexer,ui}:<commit SHA>`. There is no `:latest` tag at all |
| No exposed tokens | the token is only read from `secrets.*`, passed to `docker/login-action`, and masked in logs. `permissions: contents: read` |
| Verified deployment | `deploy` starts a new kind cluster with **the published SHA images**, waits on `kubectl rollout status`, checks `/readyz`, asks a question through the Ingress, and runs the fixture eval gate |
| Rollback | `docs/CI-CD.md`, "Deploy a version to minikube" and "Roll back" (`kubectl rollout undo`, or `set image` to the previous SHA) |
| Not demonstrated | `cd.yml` has **never run**: no secrets yet, and no merge into `dev`/`main` yet |

## 4. CI and security

- **CI:** `ci.yml` on PR #2 is fully green on the latest commit, including the checker: 164
  passed, 5 warnings, 0 failures.
- **A new CVE, found and fixed in Step 5.3:** Trivy flagged a newly published HIGH CVE in the
  ui image (zlib `CVE-2026-85091`). It was fixed by the existing targeted `apk upgrade` (zlib
  1.3.2-r1), and the scan passed again.
- **More CVEs will come.** A green scan can turn red without any code change; the fix is
  the same each time.

## 5. Evaluation: what is and is not proven

See `docs/EVALUATION.md` for the details. On the 16-question golden set, against the real
431-chunk index, through the minikube Ingress:
- **Retrieval recall is 100%** (recall@3 = recall@5 = 1.0) in all five cold runs.
- **Automatic citation validity is 100%**, and so is must-abstain correctness.
- **Generation quality is lower, and it varies.** In one manually reviewed run, 9 of 14
  answerable questions were correct and properly cited:
  - one wrong number: o04 said 60% instead of 80%;
  - one mis-citation (o08);
  - three answerable questions declined.
  - Across runs, `llama3.2:3b` declined between 1 and 5 answerable questions.
- **The gate checks** retrieval, citation format and abstention. It does not check the model's
  words.

## 6. The handout's requirements, checked by hand

| Section | Requirement | Status |
| --- | --- | --- |
| 2 | Corpus: 7 documents, 247 pages, two versions, a scanned PDF, tables, an abstention topic, exact terms | done (`docs/CORPUS.md`) |
| 3 | Pipeline, semantic cache, grounded generation, a pluggable provider (at least one mode fully supported) | done. `ollama-host` fully; `hosted` implemented but **not required**, and not tried against a cloud provider |
| 4.1-4.8 | All objects, three probes, the embedding pin (shown live), `kubectl top`, rollout and undo, endpoints, five security closures, metrics | done (`docs/evidence/`) |
| 5 | Golden set (16), recall by type, citation validity, abstention, the CI gate, a committed baseline, four broken stages | done (`docs/EVALUATION.md`, `docs/FAILURE-ANALYSIS.md`) |
| 6 | `dev` and a protected `main` (PR, CI, 1 approval) | protection set and shown working. **The approval needs Mughees**, and enforcement stops if the repository is private on the free plan |
| 6 | `ci.yml` jobs | done, and green |
| 6 | `cd.yml` with `needs:`, SHA tags, secrets, deploy | written and reviewed. **Never run: needs the secrets and a merge** |
| 6 | Gate demonstration (a failing PR with a greyed-out merge button, then green) | done in Step 5.3 (`docs/evidence/github-gate-demo.txt`, screenshots 07 and 08) |
| 7 | Seven engineering answers | done (`docs/ENGINEERING-NOTES.md`) |
| 8 | AI usage disclosed | done (`docs/AI-USAGE.md`) |
| 10 | Repository layout | done, except **`LICENSE`**, which the layout lists. Choosing a licence is the owner's decision |
| 8, submission | (1) the repository URL: public, or private with both instructors added; (2) a successful `cd.yml` run; (3) the Docker Hub images with SHA tags; (4) `eval/results.json` and `eval/baseline.json` | (4) is done on the feature branch and reaches `main` with the merges. (1)-(3) need the owner's actions in section 7 |

## 7. The owner's actions

1. **Docker Hub secrets.** Add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN` (section 3).
2. **Licence (optional).** Choose one, if wanted (for example MIT), for the `LICENSE` file the
   layout lists.
3. **Approve the merges.** Tell the assistant to go ahead with the merge sequence below, or
   do it yourself.
4. **Mughees's review.** Ask MugheesHiader476 to review and approve PR #1 into `main`. If he
   cannot before the deadline, there are two options, and both must be stated honestly in
   the submission:
   - get GitHub Pro or keep the repository public, so the rule stays enforced, and submit
     with PR #1 unmerged;
   - change the rule.
5. **Visibility.** If the repository goes private again, add **both instructors** as
   collaborators: the handout requires it for private repositories. Their GitHub user names
   are needed.
6. **Submit on Google Classroom:**
   - the repository URL;
   - the link to the successful `cd.yml` run on `main`;
   - the Docker Hub link showing the SHA tags.
   - (`eval/results.json` and `eval/baseline.json` are in the repository.)

## 8. Merge sequence (not executed)

1. **Add the Docker Hub secrets** (section 3).
2. **PR #2 → `dev`.**
   - Mark it ready for review. All 11 required checks are already green; `dev` needs no
     approval. Merge with a merge commit.
   - The push to `dev` runs `ci.yml` and `cd.yml`. Check the `cd.yml` run:
     - `test`, `build-push` (Trivy, then the push) and `deploy` (kind, `rollout status`,
       smoke test, gate) are all green;
     - the three images `rahbar-{api,indexer,ui}:<dev merge SHA>` are on Docker Hub.
3. **PR #1 → `main`.**
   - It then contains everything. Its 11 checks run on the new head and must pass.
   - **Mughees approves after reviewing.**
   - Then merge. The push to `main` runs `cd.yml` again: **that run is the submission link**,
     and the `main` SHA tags are the Docker Hub link.
4. **Final check on `main`:**
   - run `python scripts/check_submission.py`;
   - confirm `eval/results.json` and `eval/baseline.json` are on `main`;
   - confirm the README badge is green.
5. **Only then**, if wanted, make the repository private and add the instructors. Protection
   evidence (screenshots) was captured while it was enforced.

If anything in steps 2-3 fails, stop, fix it in a new pull request into `dev`, and repeat.
Never merge red checks or force-push.
