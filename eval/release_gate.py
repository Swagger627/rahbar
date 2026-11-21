"""Compare a candidate with a compatible, recorded Assignment 03 baseline."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

THRESHOLDS = {
    "recall5_max_drop", "faithfulness_min", "latency_p95_max_seconds",
    "abstention_max_drop", "citation_validity_min", "answerable_answer_rate_min",
}


def decision(metrics: dict[str, Any], baseline: dict[str, Any] | None,
             thresholds: dict[str, Any]) -> list[str]:
    if set(thresholds) != THRESHOLDS or any(
        not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v)
        or v < 0 or (key != "latency_p95_max_seconds" and v > 1)
        for key, v in thresholds.items()
    ):
        return ["invalid or incomplete threshold configuration"]
    tests = [
        ("faithfulness", ">=", thresholds["faithfulness_min"]),
        ("latency_p95_seconds", "<=", thresholds["latency_p95_max_seconds"]),
        ("citation_validity", ">=", thresholds["citation_validity_min"]),
        ("answerable_answer_rate", ">=", thresholds["answerable_answer_rate_min"]),
    ]
    failures = []
    for name in ("recall@5", "abstention_correctness"):
        value = baseline.get(name) if baseline else 0.0
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            failures.append(f"baseline {name} missing or invalid")
            continue
        key = "recall5_max_drop" if name == "recall@5" else "abstention_max_drop"
        tests.append((name, ">=", value - thresholds[key]))
    for key, operator, target in tests:
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            failures.append(f"{key} is undefined")
        elif (operator == ">=" and value + 1e-12 < target) or (operator == "<=" and value > target):
            failures.append(f"{key} {value:.4f} requires {operator} {target:.4f}")
    if metrics.get("pipeline_errors") != 0:
        failures.append(f"pipeline or judge errors: {metrics.get('pipeline_errors')}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "config"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text())
    candidate = json.loads(args.candidate.read_text())
    config = json.loads(args.config.read_text())
    failures = decision(candidate["metrics"], baseline["metrics"], config)
    if candidate["golden_digest"] != baseline["golden_digest"]:
        failures.append("golden set differs from baseline")
    print("GATE FAIL\n" + "\n".join(failures) if failures else "GATE PASS")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
