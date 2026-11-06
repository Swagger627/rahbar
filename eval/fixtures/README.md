# CI fixtures

Three short Markdown documents for the `e2e-eval` job (section 6), so CI can index
a corpus and score recall in a minute instead of six. The institute is fictional.

| File | Why it is here |
| --- | --- |
| `academic-rules-2023.md` | The superseded version (75% attendance, CGPA 1.75, grade cap B) |
| `academic-rules-2026.md` | The current version (80%, 2.00, no cap), plus one planted injection line |
| `fees-2026.md` | Two tables and exact-term numbers |

The hostel topic is missing on purpose: `golden.fixture.yaml` asks about it and
expects the service to decline.

Each file starts with front matter (`doc_version`, `superseded`). The indexer's
`discover()` reads it when there is no `_index.json`, so your extract stage
needs to handle `.md` files as well as PDFs.

`baseline.json` is the regression baseline for this fixture set. It was written by
`run_eval.py --bootstrap-baseline` in the first real `e2e-eval` run on GitHub Actions
(ci.yml run 42089834821, commit f3d7e6f), whose gate passed: recall@5 1.0, citation
validity 1.0, abstention 1.0. Every later run must not drop recall@5 by more than 5 points.
