"""Inspect persisted query traces and summarize operational metrics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = fraction * (len(values) - 1)
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


class TraceReader:
    def __init__(self, path: Path):
        self.path = path
        self.positions: dict[Path, int] = {}
        self.records: dict[str, dict[str, Any]] = {}

    def refresh(self) -> None:
        paths = sorted(self.path.glob("*.jsonl")) if self.path.is_dir() else [self.path]
        for path in paths:
            if not path.is_file():
                continue
            with path.open("rb") as stream:
                stream.seek(self.positions.get(path, 0))
                while (line := stream.readline()).endswith(b"\n"):
                    row = json.loads(line)
                    if row.get("event") == "rag_query":
                        self.records[row["trace_id"]] = row
                    self.positions[path] = stream.tell()

    def get(self, trace_id: str, wait: float = 0) -> dict[str, Any]:
        deadline = time.monotonic() + wait
        while True:
            self.refresh()
            if trace_id in self.records:
                return self.records[trace_id]
            if time.monotonic() >= deadline:
                raise ValueError(f"trace {trace_id} is missing from {self.path}")
            time.sleep(0.15)

    def prompt(self, trace_id: str) -> dict[str, Any]:
        query = self.get(trace_id)
        source = self.get(query["original_trace_id"]) if query.get("original_trace_id") else query
        attempts = source["generation_attempts"]
        # Recompute each prompt's digest from the stored messages. A match proves the printed
        # prompt is byte-for-byte the one sent to the model (traces before the digest: None).
        checks = [attempt.get("prompt_sha256") == prompt_digest(attempt["messages"])
                  if attempt.get("prompt_sha256") else None for attempt in attempts]
        return {"trace_id": trace_id, "cache": query["cache"], "source_trace_id": source["trace_id"],
                "collection": source.get("collection"),
                "collection_version": source.get("collection_version"),
                "embedding_version": source.get("embedding_version"),
                "prompt_sha256": source.get("prompt_sha256"), "prompt_digest_verified": checks,
                "context": source["context"], "generation_attempts": attempts,
                "outcome": source.get("outcome")}


def prompt_digest(messages: list[dict[str, str]]) -> str:
    """Same canonical form as api/app/trace.py prompt_digest."""
    canonical = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("status") == 200 and not row.get("retrieval_only")]
    similarities = [row["retrieved"][0]["score"] for row in eligible
                    if row["retrieved"] and row["cache"] != "hit"]
    stages = {stage for row in eligible for stage in row["latency_ms"]}
    cache = Counter(row["cache"] for row in eligible)
    outcomes = Counter(row.get("outcome") for row in eligible)
    bins = {"<0.50": 0, "0.50-0.64": 0, "0.65-0.79": 0, ">=0.80": 0}
    for score in similarities:
        key = "<0.50" if score < .50 else "0.50-0.64" if score < .65 else (
            "0.65-0.79" if score < .80 else ">=0.80")
        bins[key] += 1
    return {
        "queries": len(rows), "answered_or_abstained": len(eligible),
        "top1_distribution": bins, "top1_p50": quantile(similarities, 0.5),
        "cache_counts": dict(cache),
        "cache_hit_rate": cache["hit"] / (cache["hit"] + cache["miss"])
        if cache["hit"] + cache["miss"] else None,
        "abstention_rate": sum(row.get("answered_from_context") is False for row in eligible)
        / len(eligible) if eligible else None,
        "outcomes": dict(outcomes),
        "p95_ms_by_stage": {
            key: quantile([row["latency_ms"][key] for row in eligible if key in row["latency_ms"]], .95)
            for key in sorted(stages)
        },
        "queries_with_injection_flagged": sum(any(
            action["action"] == "drop_injected_instruction" for action in row["guardrail_actions"]
        ) for row in rows),
        "injected_chunks_dropped": sum(
            event["action"] == "drop_injected_instruction"
            for row in rows for event in row["guardrail_actions"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, default=Path("traces"))
    tasks = parser.add_subparsers(dest="command", required=True)
    for command in ("prompt", "trace"):
        tasks.add_parser(command).add_argument("trace_id")
    summary = tasks.add_parser("summary")
    summary.add_argument("--since", help="ISO 8601 timestamp with time zone")
    summary.add_argument("--until", help="exclusive ISO 8601 timestamp with time zone")
    summary.add_argument("--out", type=Path)
    args = parser.parse_args()
    reader = TraceReader(args.traces)
    try:
        if args.command == "prompt":
            result = reader.prompt(args.trace_id)
        elif args.command == "trace":
            result = reader.get(args.trace_id)
        else:
            reader.refresh()
            start = datetime.fromisoformat(args.since) if args.since else None
            end = datetime.fromisoformat(args.until) if args.until else None
            rows = []
            for record in reader.records.values():
                stamp = datetime.fromisoformat(record["timestamp"])
                if (start is None or start <= stamp) and (end is None or stamp < end):
                    rows.append(record)
            result = {"since": args.since, "until": args.until, **summarize(rows)}
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if getattr(args, "out", None):
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
