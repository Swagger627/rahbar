"""Write the two hand-review worksheets for an exported evaluation run.

    python3.12 eval/make_review_sheets.py eval/results/evaluation-baseline-RUN_ID.json

1. eval/reviews/faithfulness-labels.jsonl: one row per judged answer, with the exact context
   the judge saw. The judge's verdict is left out on purpose, so your label is independent.
   Fill `supported` (true/false), `reason` and `reviewer` for at least 8 rows. Rows left blank
   are ignored. Pass the file as --reviews to compare_runs.py or build_ragops_report.py.
2. eval/reviews/golden-review.md: a checklist for the synthetic golden questions, with the
   retrieved passage from the labelled page when the run found it. After checking a row,
   set `review_status: "reviewed"`, `reviewer` and `review_note` on it in eval/golden.jsonl.

Regenerate both after rerunning the baseline: trace ids change with every run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("evaluation", type=Path, help="an exported evaluation-*.json")
    parser.add_argument("--out", type=Path, default=HERE / "reviews")
    parser.add_argument("--force", action="store_true", help="overwrite an existing labels file")
    args = parser.parse_args()
    run = json.loads(args.evaluation.read_text(encoding="utf-8"))
    questions = run["runs"][0]["questions"]
    args.out.mkdir(parents=True, exist_ok=True)

    labels = args.out / "faithfulness-labels.jsonl"
    if labels.exists() and not args.force:
        parser.error(f"{labels} exists; it may hold your labels. Use --force to overwrite.")
    rows = [{
        "trace_id": q["trace_id"], "id": q["id"], "question": q["question"],
        "answer": q["answer"]["answer"],
        "context": [{"source": c["source"], "page": c["page"], "text": c["text"]} for c in q["context"]],
        "supported": None, "reason": "", "reviewer": "",
    } for q in questions if q.get("judge")]
    labels.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                      encoding="utf-8")

    lines = [f"# Golden-set review sheet (run {run['run_id']})", "",
             "Check each question against the cited page in the real PDF, not only the passage below.",
             "Then edit its row in eval/golden.jsonl: "
             '`"review_status": "reviewed", "reviewer": "INITIALS", "review_note": "what you checked"`.',
             "Fix the question, answer or page first if they are wrong.", ""]
    for q in questions:
        gold = q["golden"]
        if gold.get("origin") != "synthetic":
            continue
        pages = {(r["document_id"], r["doc_version"], r["page"]) for r in gold["relevant"]}
        evidence = [c for c in q.get("context", [])
                    if any((c.get("document_id"), c.get("doc_version"), p) in pages
                           for p in (c.get("pages") or [c.get("page")]))]
        lines += [f"## {gold['id']} ({'must abstain' if gold['must_abstain'] else 'answerable'}"
                  f"{', hard' if gold.get('hard') else ''})", "",
                  f"- **Question:** {gold['question']}",
                  f"- **Expected:** {gold['expected']}",
                  f"- **Labelled evidence:** {', '.join(f'{d} v{v} p{p}' for d, v, p in sorted(pages)) or 'none'}",
                  f"- **Pipeline answer:** {q.get('answer', {}).get('answer', 'error')}",
                  "- [ ] Question is clear  - [ ] Expected answer is right  - [ ] Page is right", ""]
        for passage in evidence[:2]:
            text = passage["text"].replace("\n", " ")[:700]
            lines += [f"> {passage['source']} p{passage['page']}: {text}", ""]
    review = args.out / "golden-review.md"
    review.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(rows)} judged answers -> {labels}\n{review}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
