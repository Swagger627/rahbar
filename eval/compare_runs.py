"""Write the metric and per-question differences between two compatible runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from result_io import difference, review_agreement, save_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    before = json.loads(args.before.read_text())
    after = json.loads(args.after.read_text())
    if before["golden_digest"] != after["golden_digest"]:
        parser.error("comparisons require identical golden sets")
    old = {row["id"]: row for row in before["runs"][0]["questions"]}
    comparisons = []
    for now in after["runs"][0]["questions"]:
        prior = old[now["id"]]
        comparisons.append({
            "id": now["id"], "before_trace": prior.get("trace_id"), "after_trace": now.get("trace_id"),
            "before_outcome": prior.get("outcome"), "after_outcome": now.get("outcome"),
            "before_answer": prior.get("answer", {}).get("answer"),
            "after_answer": now.get("answer", {}).get("answer"),
            "before_retrieval": prior.get("retrieval"), "after_retrieval": now.get("retrieval"),
        })
    result = {"before": before["run_id"], "after": after["run_id"],
              "before_metrics": before["metrics"], "after_metrics": after["metrics"],
              "delta": {key: difference(value, before["metrics"].get(key))
                        for key, value in after["metrics"].items()},
              "questions": comparisons,
              "human_judge_agreement_after": review_agreement(after["runs"], args.reviews)}
    save_json(args.out, result)
    print(json.dumps(result["delta"], indent=2))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
