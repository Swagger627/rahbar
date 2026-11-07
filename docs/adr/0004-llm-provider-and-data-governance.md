# ADR 0004: The LLM provider, and what data leaves where

## Status
Accepted · 2026-11-06

## Context
Every answer needs two model calls:
1. an **embedding** of the question, which must use the same model as the index (ADR 0001,
   section 4.4);
2. a **generation**, which gets the question plus up to 5 retrieved passages.

Section 3.5 asks for a pluggable provider, selected by `LLM_PROVIDER`, with at least one mode
fully supported.

**What we must respect:**
- **The laptop's limits:** minikube runs with 4 GB, so an in-cluster model is not realistic.
- **Students' privacy:** the documents are public university PDFs, but a *question* can
  contain personal data ("I am roll number 21L-1234 and failed ...").
- **No secrets in Git.**

## Decision
- **Default mode: `ollama-host`.**
  - `llama3.2:3b` generates and `nomic-embed-text` embeds, both through Ollama on the
    developer's laptop.
  - The cluster reaches it at `OLLAMA_URL` (`http://host.minikube.internal:11434`, in the
    ConfigMap).
  - NetworkPolicies allow port 11434 on the host's IP for the api and the indexer only.
  - **Questions and passages never leave the machine.** Embeddings always go through Ollama,
    in every mode.
- **Optional mode: `hosted`.**
  - `api/app/providers/llm/hosted.py` talks to any OpenAI-compatible chat-completions
    endpoint (Groq, OpenRouter, Google AI Studio's compatible API).
  - It uses exactly the same prompt, `Answer` schema, citation check and single retry as the
    Ollama provider. Swapping the generator is configuration: `LLM_PROVIDER=hosted`,
    `HOSTED_BASE_URL`, `LLM_MODEL`.
  - The key arrives only as `LLM_API_KEY`, from the Secret `rahbar-secret`, created on the
    command line (`k8s/base/secret.placeholder.yaml` holds only `REPLACE_ME`).
  - The provider refuses to start without a base URL or a key. It never logs the key or puts
    it in an error message (`api/tests/test_hosted.py` checks both).
- **The swap is one file.** `api/app/providers/llm/__init__.py` picks the provider. A new
  provider implements `generate(question, passages, problem) -> Answer`.

## Data governance
| Data | Where it goes | How long it stays |
| --- | --- | --- |
| The question text | Ollama on the laptop (embedding, and generation in `ollama-host`). In `hosted` mode, also the hosted provider | Not logged by RAHBAR (log lines carry request ids, chunk ids and scores, never the question). Stored in Redis inside a cache entry for at most `CACHE_TTL_SECONDS` = 3600 s; abstentions are never cached |
| Retrieved passages (public PDFs) | Ollama; in `hosted` mode, also the hosted provider | Not stored outside Chroma |
| The answer | The user; Redis (same 1 h TTL) | Removed by TTL or a re-index (a new collection id makes the cache namespace change) |
| The API key (`hosted` only) | Kubernetes Secret, then an environment variable in the api pods | Never in Git, logs or error messages |

- **Who can reach the cache:** Redis is reachable only from the api (NetworkPolicy, verified
  on Calico: the ui pod cannot open a TCP connection to `redis:6379`).
- **What a hosted provider would receive:** the question and the passages, and nothing else.
  No user identity is collected anywhere in RAHBAR.

## Options considered
- **`ollama-incluster`** (Ollama as a Deployment with a PVC): needs about 8 GB for minikube,
  and a long startup probe while the weights load. Not possible on this laptop alongside the
  rest of the stack.
- **Hosted as the default:** fast and free-tier, but it would send every student question to
  a third party, and it needs a key the team would have to protect. It stays opt-in.
- **No hosted mode at all:** simpler, but the generator could then only be swapped by
  changing code. The implementation is small and shares all the validation.

## Consequences
- **Good:**
  - in the default mode, no student question leaves the developer's machine;
  - the answer contract and the safety checks are identical for both providers.
- **Hard:**
  - **Only one machine generates.** Scaling the api does not add generation capacity (see
    `docs/ENGINEERING-NOTES.md`, Q3).
  - **The cluster depends on the laptop.** The cluster can only answer while the laptop's
    Ollama is running.
- **Tested so far:**
  - **The default mode:** the hosted path was checked end to end only against a local
    OpenAI-compatible endpoint (Ollama's `/v1`, three real calls, all valid and cited).
  - **The hosted mode against a real cloud provider:** not yet. That needs a key, and should
    be followed by the golden evaluation, because a different model changes answer quality.
- **If questions start to carry personal data routinely:** do not enable `hosted` without
  telling users. Consider dropping the question text from cache entries and keeping only its
  vector and key terms.
