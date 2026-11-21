"""Measure how closely the simulated query log is represented by a golden set."""

from __future__ import annotations

import argparse
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ragops_eval import HERE, Ollama, cosine
from result_io import load_rows, save_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=HERE / "golden.jsonl")
    parser.add_argument("--live", type=Path, default=HERE / "live_queries.jsonl")
    parser.add_argument("--similarity", type=float, default=0.77)
    parser.add_argument("--minimum-coverage", type=float, default=0.85)
    parser.add_argument("--ollama", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.environ.get("EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--out", type=Path, default=HERE / "results")
    args = parser.parse_args()
    if not -1 <= args.similarity <= 1 or not 0 <= args.minimum_coverage <= 1:
        parser.error("cosine cutoff must be in [-1, 1] and minimum coverage in [0, 1]")
    golden, live = load_rows(args.golden), load_rows(args.live)
    if not golden or not live:
        parser.error("both datasets must contain queries")
    with httpx.Client(timeout=120) as http:
        ollama = Ollama(http, args.ollama, args.model, "", 4032)
        known = ollama.embed([row["question"] for row in golden])
        observed = ollama.embed([row["query"] for row in live])
    results = []
    uncovered: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row, vector in zip(live, observed, strict=True):
        score, index = max((cosine(vector, other), i) for i, other in enumerate(known))
        match = {**row, "nearest": golden[index]["id"], "nearest_question": golden[index]["question"],
                 "cosine": score, "covered": score >= args.similarity}
        results.append(match)
        if not match["covered"]:
            uncovered[row["topic"]].append(match)
    coverage = sum(row["covered"] for row in results) / len(results)
    passed = coverage >= args.minimum_coverage
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    path = args.out / f"coverage-{args.golden.stem}-{run_id}.json"
    save_json(path, {"golden": str(args.golden), "live": str(args.live), "model": args.model,
                    "similarity": args.similarity, "minimum_coverage": args.minimum_coverage,
                    "coverage": coverage, "passed": passed, "queries": results,
                    "uncovered_by_topic": dict(uncovered)})
    print(f"Coverage {coverage:.1%} of {len(live)} queries; cosine >= {args.similarity}; "
          f"minimum {args.minimum_coverage:.0%}: {'PASS' if passed else 'FAIL'}")
    for topic, rows in sorted(uncovered.items()):
        print(f"{topic}: {', '.join(str(row['id']) for row in rows)}")
    print(path)
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
