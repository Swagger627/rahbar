# Assignment 03 implementation plan

## Assignment 02 audit

The existing pipeline has OCR, table-aware chunking, Ollama embedding and generation,
Chroma, Redis, an API and UI, Kubernetes Deployments and scheduled indexing, and CI.
It can serve as the control. The Assignment 02 evaluator and cluster workflow remain usable.

| Finding | Consequence | Validation |
| --- | --- | --- |
| A checksum mismatch aborts the entire legacy run | A single corrupt upload blocks healthy updates | Stage a corrupt input alongside good inputs and inspect all four check results |
| The legacy indexer embeds every chunk and swaps the collection | Unchanged updates are expensive and the live collection briefly disappears | Repeat a batch and compare actual embedding calls and chunk identities |
| Historical queries can read superseded chunks | Assignment 03 requires normal query paths to exclude archived versions | Trace an update and archive restore |
| The cache keys on collection UUID | An in-place update can return stale responses | Namespace by content revision and serving configuration |
| The old evaluator matches source and section | It cannot detect wrong-page retrieval or measure faithfulness | Score versioned document-page evidence and use the exact traced context |
| Logs are spread across several events | A retry, cache hit or abstention cannot be reproduced from a single record | Compare lookup output with the provider's actual payload |
| The current corpus volume is read-only in the indexer | Quarantine and archive need writable persistent storage | Inspect PVC contents after the Job exits |
| The old cluster has no evaluation Job or candidate Service | A locally passing command cannot gate a rollout | Exercise ConfigMap thresholds through a Job targeting the candidate Service |

## Sequence and acceptance

1. Preserve the existing overlay. Build a separate Assignment 03 overlay around the same API,
   Chroma, Redis and Job roles, with collection and data separation.
2. Stage Batch A and a reproducible Batch B. Verify documents before chunking. Keep immutable
   files, exported verification reports, a per-collection revision and per-document chunk counts.
3. Instrument the real API path. Keep full prompts and the exact filtered context in per-pod
   JSONL. Join evaluation responses to traces by request ID and test prompt reconstruction.
4. Run embedding, retrieval and answer tests from one seeded command. Record original and noisy
   runs, repetitions, raw outputs and gate decisions. Compare candidate and control on Batch A
   with one pipeline setting changed, then compare Batch A and B with the setting held fixed.
5. Run semantic coverage against a frozen simulated query set. Expand golden coverage separately
   from the data-drift experiment.
6. Run both Job types and controlled rollouts on Kubernetes. Export evidence before cleaning up
   Jobs and confirm the previous collection after undo.
7. Render the report using actual results, include terminal screenshots from the user's cluster,
   and record the actual student contributions and reviews.

Parts A and D must land before official B runs. C and E reuse the same B suite with distinct
controls. F is developed alongside A and B to exercise the actual Job entry points early.

## Final review checks

- A rerun with an unchanged inbox must keep the same Chroma revision and show zero embedding calls.
- Compare the gate result against the candidate's current collection revision, embedding digest,
  and top-k before promotion. The promotion command replays the gate with the mounted thresholds.
- Require one serving snapshot and the same seed across both evaluation repeats.
- Keep automated verdicts and measured outcomes in exported JSON. Identify missing human work and
  runs in the report without assigning scores to work that has no evidence.
- Use the appendix placeholders and the terminal capture guide for student screenshots.
