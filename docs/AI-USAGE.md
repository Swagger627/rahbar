# AI usage

The assignment asks for honest attribution (section "AI assistance"). This file records how
AI was used to build RAHBAR. It is kept up to date as the project grows.

## Summary

Almost all code, tests, Kubernetes manifests and documentation in this repository so far were
written by an AI coding assistant, working step by step under the direction of the team member
who built it (Faseeh, GitHub `Swagger627`). The second team member, `MugheesHiader476`, has not
contributed yet.

The work was done in small, numbered steps (0.x environment and repository, 1.x corpus and
golden set, 2.x Docker and Kubernetes, 3.x the RAG pipeline). For each step the team member:
- wrote the requirements and limits (for example: no secrets in Git, no force-push, never
  claim an untested result, no extra downloads);
- approved every action outside the laptop (creating the GitHub repository, pushes, pull
  requests) and every repair to the local machine (the Docker Desktop socket-folder rename);
- read the report, which included the commands to reproduce each result;
- asked for a plain-English explanation of what was built, why, and how to explain it.

Commits carry only the team member's name. AI co-author lines were turned off on purpose, and
this file is the disclosure instead.

## Tools

| Tool | What it wrote or shaped | What we changed afterwards, and why |
| --- | --- | --- |
| Claude Code (Anthropic, model Claude Opus 5.5), in the Claude desktop app | Corpus selection and verification scripts; the golden set; `eval/run_eval.py` and its tests; the Dockerfiles; all Kubernetes manifests; the indexer (extract, clean, chunk, embed, store); the API retriever, readiness checks, Ollama provider, grounding checks, prompt and Redis semantic cache; all unit tests; `docs/PIPELINE.md`, `docs/CORPUS.md`, `docs/DEPLOYMENT.md`, ADRs 0001 and 0003, and this file. It also ran the real measurements (OCR, embeddings, retrieval, answer evaluation) on the laptop, and reported the numbers in `docs/PIPELINE.md`. | Every change below was made because a real test or measurement showed a problem. It was then re-tested, and the before-and-after numbers are recorded in `docs/PIPELINE.md`. The fixes are listed after this table. |
| Ollama models `nomic-embed-text` and `llama3.2:3b` | These are **part of the product**, not development help: they embed the chunks and questions, and generate the answers. | Their real behaviour drove several of the changes below (the citation format, the injection filter, the relevance floor). |

**Changes made after real tests or measurements:**
- **OCR:** tesseract's page mode was changed from 3 to 4, because mode 3 lost the scanned
  policy's clause numbers.
- **Cleaning:** a rule for table-of-contents dot leaders was added, after the first real
  embedding run overflowed the model's context.
- **Tables:** tick-mark tables got column names, and later one chunk per row, because
  campus information was lost or buried.
- **Version history:** the word "previous" was removed from the history-question words,
  because it matched the injection question i01.
- **Citations:** a ready-made citation line was added to each passage, after the real
  `llama3.2:3b` miscopied pages and sections.
- **Prompt injection:** the filter was added after the real model obeyed a poisoned passage
  5 times out of 5.
- **Relevance floor:** set to 0.65 after measuring real scores.
- **Exact clauses:** the per-clause match limit went from 2 to 1, after a second "5.4" match
  came from an unrelated document.
- **Semantic cache:** the threshold was chosen from real similarity scores of paraphrase and
  hard-negative pairs. A key-term check (numbers, grades, codes, history words) was added
  because similarity alone gave false hits ("old rule" vs "current rule" scored 0.955).
  0.90 was picked over 0.85 for a safety margin.
- **Kubernetes configuration (Step 4.1):** the AI switched the ConfigMap to the real
  settings, added the corpus PVC, the loader pod and the real indexer Job, opened only
  Ollama's port in the NetworkPolicies, removed the destructive dummy Job, and wrote
  `docs/DEPLOYMENT.md`. It tested container access to the host's Ollama before choosing the
  network rule.
- **First live deployment (2026-11-05):** with the owner's approval, the AI installed
  minikube without admin rights (the official binary, SHA-256 checked) and deployed to a
  live cluster.
  - **Three real bugs** came up and were fixed:
    - Chroma crashed on the `CHROMA_PORT` variable that Kubernetes injects
      (`enableServiceLinks: false`);
    - the api start-up check blocked past the startup probe (it now runs in the background);
    - the indexer was OOMKilled at 1 GiB (measured peak 1,117 MiB; the limit is now 2 GiB).
  - **Its own mistakes were corrected:**
    - an earlier claim that the indexer peaked under 1 GiB (Docker swap had hidden the real
      peak);
    - a network test that wrongly showed an allowed path as blocked;
    - a memory reading that Git Bash's path rewriting turned into 0.
- **Bugs found by the AI's own unit tests and fixed:** "1.21" matching "11.21"; overlap
  pushing chunks past their size limit.
- **Corrected over-claims:** the Step 3.5B answer review was corrected in Step 3.5C (o01 is
  mis-cited). An index that had been lost when a container was removed was reported, not
  hidden.

## What the team member must be able to explain

The assignment requires that the submission reflects work the team understands. After each
step the AI gave a short explanation: what was built, why it is needed, how it works, how to
run it again, and how to explain it to the professor. These explanations, and the measured
results in `docs/PIPELINE.md`, are the study material for the viva.

## Other tools

If any team member used other AI tools (for example a chat assistant to read about a
concept), add a row to the table above with what it was used for.
