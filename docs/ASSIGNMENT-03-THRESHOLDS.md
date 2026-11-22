# Assignment 03 thresholds and their justification

C2 and E2 ask for thresholds "you justify". Each value below records how it was chosen and
which committed run it came from. **Recompute the numbers when the golden set changes**, for
example after adding the student-written questions. A new `golden.jsonl` changes the golden
digest, so the baseline and every comparison must be rerun. The method stays the same.

All values were fixed **before any full candidate evaluation existed**. The candidate was
never used to choose them.

## Release gate (C2): `eval/thresholds.json` = ConfigMap `ragops-gate`

Source run: `eval/results/evaluation-baseline-20261121T063936Z-239b0164.json`. This is
Batch A on collection `rahbar-a3-baseline`: two seeded repeats (seed 4032), 25 questions,
`llama3.2:3b` on a CPU-only laptop.

| Threshold | Value | Baseline measurement | Why this value |
| --- | --- | --- | --- |
| `recall5_max_drop` | 0.02 | recall@5 0.95 in both runs (repeat delta 0.00) | Retrieval is deterministic across repeats, so any drop is caused by the change itself. With N answerable questions, losing a single-page question costs 1/N (0.05 at N = 20), and losing one of two pages costs 1/(2N) (0.025). Both exceed 0.02 while N ≤ 25. Recheck this after adding questions. |
| `abstention_max_drop` | 0.00 | 1.00 in both runs | Required by the handout: abstention may not fall below the baseline. |
| `faithfulness_min` | 0.50 | 0.533 and 0.563, mean 0.548, repeat delta 0.029 | Baseline mean minus about 1.5 times the observed repeat spread. It tolerates judge noise but rejects a change that loses about 5 points or more. The earlier value, 0.60, was above what the baseline itself scores on this hardware, so no change could ever pass. |
| `latency_p95_max_seconds` | 120 | p95 94.1 s and 79.8 s; slowest single answer 101.6 s | 120 s is the API's own per-attempt generation timeout (`GENERATE_TIMEOUT_SECONDS` in `api/app/providers/llm/ollama.py`). Above it, a user already gets a fallback refusal instead of an answer. The earlier 45 s target was below the baseline p50 (34.8 s) plus normal variance on a CPU-only 3B model. |
| `citation_validity_min` | 0.95 | 1.00 in both runs | Every citation is checked against the retrieved passages before the answer is returned, so a candidate below 0.95 has a real citation defect. |
| `answerable_answer_rate_min` | 0.40 | 0.75 and 0.80 | A floor against a candidate that "passes" faithfulness by refusing everything. Faithfulness is measured only on answered questions. |

The deliberately bad candidate (`TOP_K=1` on `api-candidate`) fails `recall5_max_drop`
whatever the generation thresholds are.

## Coverage cutoff (E2): cosine ≥ 0.77, target ≥ 85% of live queries

The cutoff comes from the **module-level embedding pairs** (`eval/embedding_pairs.jsonl`,
same model `nomic-embed-text`, same baseline run). The live-query scores are not used, because
choosing a cutoff from the scores it judges would be circular.

| Pair set (15 pairs each) | min | quartile 1 | median | max |
| --- | --- | --- | --- | --- |
| Paraphrase | 0.740 | 0.806 | 0.823 | 0.929 |
| Unrelated | | | | 0.479 |

- **0.77 lies between the two sets.** It is far above the highest unrelated pair (0.479) and
  below the lower quartile of true paraphrases (0.806).
- **A live query counts as covered only when a golden question is about as close as a
  paraphrase.** Sharing the topic is not enough. 13 of the 15 paraphrase pairs clear 0.77.
- **85% is the paper's own example** (section 4.2.7: flag a gap when under 85% of live queries
  reach the similarity score).

`eval/coverage_check.py` uses these values by default and exits 1 below the target. The
`ragops-k8s.sh coverage` helper accepts a different cutoff as its second argument.
