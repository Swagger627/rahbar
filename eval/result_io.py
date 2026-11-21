"""Read, compare and write evaluation results without runtime model dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def difference(value: Any, prior: Any) -> float | None:
    return value - prior if isinstance(value, (int, float)) and isinstance(prior, (int, float)) else None


def review_agreement(runs: list[dict[str, Any]], path: Path | None) -> dict[str, Any]:
    rows = {row["trace_id"]: row["judge"]["supported"] for run in runs for row in run["questions"]
            if row.get("judge")}
    labels = load_rows(path) if path else []
    valid = {row["trace_id"]: row["supported"] for row in labels
             if row.get("trace_id") in rows and isinstance(row.get("supported"), bool)
             and row.get("reviewer") and row.get("reason")}
    matched = sum(rows[trace_id] == supported for trace_id, supported in valid.items())
    return {"human_reviewed": len(valid), "agreement": matched / len(valid) if valid else None}
