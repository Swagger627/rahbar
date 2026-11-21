"""Print the document checks from an exported ingestion report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--batch-b", action="store_true", help="show the ten Batch B fixtures")
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    print(f"Run {report['run_id']}")
    for row in report.get("documents", []):
        if args.batch_b and not row["file"].startswith("b"):
            continue
        print(f"{row['file']}  [{row.get('disposition', 'pending')}]")
        for check, result in row["checks"].items():
            label = "PASS" if result["pass"] else "FAIL"
            print(f"  {check:13} {label:4}  {result['reason']}")
    if stats := report.get("index_update"):
        for key in ("collection", "revision", "chunks_embedded", "chunks_deleted", "chunks_total"):
            print(f"{key}: {stats[key]}")


if __name__ == "__main__":
    main()
