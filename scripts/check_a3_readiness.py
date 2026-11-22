#!/usr/bin/env python3
"""Assignment 03 readiness: check the committed evidence against the handout, item by item.

    python3 scripts/check_a3_readiness.py

Reads only files in the repository (eval/, traces/, k8s/). It cannot tell whether numbers are
good, only whether the required evidence exists and is consistent. Exit 1 while anything is
missing. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval/results"
results: list[tuple[bool, str, str]] = []


def check(ok: bool, item: str, detail: str) -> None:
    results.append((bool(ok), item, detail))


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] \
        if path.exists() else []


def load(glob: str) -> list[dict]:
    return [{**json.loads(p.read_text()), "_file": p.name} for p in sorted(RESULTS.glob(glob))]


def main() -> int:
    golden_path = ROOT / "eval/golden.jsonl"
    gold = rows(golden_path)
    digest = hashlib.sha256(golden_path.read_bytes()).hexdigest() if golden_path.exists() else ""

    # A: verification and incremental update
    ingest = [r for r in load("ingestion-*.json") if "index_update" in r]
    batch_b = next((r for r in ingest if sum(d["file"].startswith("b") for d in r["documents"]) >= 10), None)
    check(batch_b, "A1 Batch B verification report (>= 10 planted docs)",
          batch_b["_file"] if batch_b else "stage b, then ingest baseline")
    if batch_b:
        reasons = [c["reason"] for d in batch_b["documents"] for c in d["checks"].values() if not c["pass"]]
        check(any("exact SHA-256" in r for r in reasons) and any("near-duplicate" in r for r in reasons),
              "A1 exact and near-duplicate caught", f"{len(reasons)} failed checks with reasons")
    check(any(r["index_update"]["chunks_embedded"] == 0 and r["index_update"]["chunks_deleted"] == 0
              for r in ingest), "A2 unchanged rerun embeds 0 chunks", "ingestion JSON with chunks_embedded 0")
    check(any(any(v == {"active": 0, "archived": 0} for v in r["index_update"]["counts_by_document_id"].values())
              for r in ingest), "A2 deleted document leaves 0 chunks", "a document id with zero counts")

    # B: golden set and suite
    answerable = sum(not r["must_abstain"] for r in gold)
    check(len(gold) >= 25 and answerable >= 15 and len(gold) - answerable >= 5
          and sum(r.get("hard", False) for r in gold) >= 5, "B golden >=25 / 15 answerable / 5 abstain / 5 hard",
          f"{len(gold)} / {answerable} / {len(gold) - answerable} / {sum(r.get('hard', False) for r in gold)}")
    human = sum(r.get("origin") == "human" for r in gold)
    check(human >= 8, "B >= 8 questions written by hand (origin: human)", f"{human} found  [HUMAN WORK]")
    reviewed = sum(r.get("origin") == "synthetic" and r.get("review_status") == "reviewed"
                   and r.get("reviewer") for r in gold)
    check(reviewed >= 8, "B >= 8 synthetic questions reviewed by hand", f"{reviewed} found  [HUMAN WORK]")
    pairs = rows(ROOT / "eval/embedding_pairs.jsonl")
    check(min(sum(p["kind"] == k for p in pairs) for k in ("paraphrase", "unrelated")) >= 15,
          "B >= 15 paraphrase and 15 unrelated pairs", f"{len(pairs)} pairs")
    evals = load("evaluation-*.json")
    current = [e for e in evals if e["golden_digest"] == digest and len(e["runs"]) >= 2]
    base = next((e for e in reversed(current) if e["phase"] == "baseline"), None)
    check(base, "B full three-level baseline, 2 seeded runs, current golden set",
          base["_file"] if base else "rerun: eval baseline api (golden digest changed or missing)")
    labels = [r for r in rows(ROOT / "eval/reviews/faithfulness-labels.jsonl")
              if isinstance(r.get("supported"), bool) and r.get("reviewer") and r.get("reason")]
    traced = {q["trace_id"] for q in base["runs"][0]["questions"]} if base else set()
    matched = sum(r["trace_id"] in traced for r in labels)
    check(matched >= 8, "B >= 8 hand labels on judged answers of that baseline",
          f"{matched} labels match baseline trace ids  [HUMAN WORK: eval/make_review_sheets.py]")

    # C: change experiment and gate
    cands = [e for e in current if e["phase"] == "candidate"]
    check(any(e["gate"]["pass"] for e in cands), "C1/C2 candidate full suite with GATE PASS",
          next((e["_file"] for e in cands if e["gate"]["pass"]), "eval candidate api-candidate"))
    check(any(e["gate"]["pass"] is False for e in cands), "C2 candidate with GATE FAIL (e.g. TOP_K=1)",
          next((e["_file"] for e in cands if e["gate"]["pass"] is False), "set TOP_K=1, eval candidate"))
    config = json.loads((ROOT / "eval/thresholds.json").read_text())
    manifest = (ROOT / "k8s/assignment3/resources.yaml").read_text()
    in_map = json.loads(manifest.split("thresholds.json: |", 1)[1].split("---", 1)[0])
    check(config == in_map, "C2 thresholds.json equals the ragops-gate ConfigMap", str(config))
    if base:
        failures = [k for k, op, v in (("faithfulness", ">=", config["faithfulness_min"]),
                                       ("latency_p95_seconds", "<=", config["latency_p95_max_seconds"]))
                    if base["metrics"].get(k) is None
                    or (op == ">=" and base["metrics"][k] < v) or (op == "<=" and base["metrics"][k] > v)]
        check(not failures, "C2 baseline itself meets the absolute thresholds", ", ".join(failures) or "ok")

    # D: traces
    traces = [json.loads(x) for p in (ROOT / "traces").glob("*.jsonl")
              for x in p.read_text().splitlines() if x.strip()]
    need = {"trace_id", "timestamp", "query", "rewritten_query", "embedding_model", "embedding_version",
            "collection", "collection_version", "retrieved", "filters", "reranked_order",
            "generation_attempts", "model", "model_parameters", "response", "citations",
            "guardrail_actions", "latency_ms", "cache"}
    complete = [t for t in traces if need <= t.keys()]
    check(complete, "D1 trace records with every required field", f"{len(complete)}/{len(traces)} complete")
    check(any(t.get("prompt_sha256") for t in traces), "D1 trace carries prompt_sha256",
          "rebuild the api image (a3) and send a fresh query")
    check(any(a["action"] == "drop_injected_instruction" for t in traces for a in t["guardrail_actions"]),
          "D4 injected Batch B chunk dropped and logged", "ask the Azure Lantern question")
    summary = json.loads((RESULTS / "trace-summary.json").read_text()) if (RESULTS / "trace-summary.json").exists() else {}
    check(summary.get("cache_hit_rate") is not None and summary.get("since"),
          "D3 windowed trace summary with a cache hit rate",
          "send a question twice WITHOUT X-Rahbar-Eval, then summary --since/--until")

    # E: coverage and drift
    live = rows(ROOT / "eval/live_queries.jsonl")
    check(len(live) >= 40 and sum(not q["in_scope"] for q in live) >= 12, "E1 >= 40 live queries, >= 12 off-topic",
          f"{len(live)} / {sum(not q['in_scope'] for q in live)}")
    expanded = rows(ROOT / "eval/golden_expanded.jsonl")
    ids = {r["id"] for r in gold}
    check(ids <= {r["id"] for r in expanded} and len(expanded) - len(gold) >= 6,
          "E4 expanded set = golden + >= 6 new", f"{len(expanded) - len(gold)} new; superset: {ids <= {r['id'] for r in expanded}}")
    cov = load("coverage-*.json")
    before = [c for c in cov if c["golden"].endswith("/golden.jsonl")]
    after = [c for c in cov if c["golden"].endswith("/golden_expanded.jsonl")]
    check(before and after and after[-1]["coverage"] > before[-1]["coverage"] and after[-1]["passed"],
          "E2/E4 coverage rises and passes after expansion",
          f"{before[-1]['coverage']:.1%} -> {after[-1]['coverage']:.1%}" if before and after else "run coverage x2")
    drift = [e for e in current if e["phase"] == "observe"]
    check(drift, "E3 full Part B suite rerun after Batch B (not components only)",
          drift[-1]["_file"] if drift else "stage b, ingest baseline, eval observe api")

    # F: Kubernetes
    api = (ROOT / "k8s/base/api.yaml").read_text()
    check(re.search(r"maxUnavailable:\s*0", api), "F3 api rolling update maxUnavailable: 0", "k8s/base/api.yaml")
    check((ROOT / "k8s/eval-job.yaml").exists() and (ROOT / "k8s/README.md").exists(),
          "F1/F2 eval-job.yaml and k8s/README.md", "present")
    for name, what in (("f-rollout-undo-terminal.txt", "F3 rollout undo log"),
                       ("f-post-undo-trace.json", "F3 trace after undo"),
                       ("f-cluster-inventory.txt", "F1 kubectl get all,pvc,configmap,cronjob,job")):
        check((RESULTS / name).exists(), what, name)
    promoted = RESULTS / "f-promote-terminal.txt"
    check(promoted.exists() and "successfully rolled out" in promoted.read_text(),
          "F3 gate-passed candidate promoted (rollout status)", "save promote output to f-promote-terminal.txt")
    smoke = (RESULTS / "f-configmap-wiring-smoke.txt")
    real_flip = RESULTS / "f-configmap-gate-flip.txt"
    check(real_flip.exists(), "F2 ConfigMap edit flips the gate on a REAL candidate result",
          "gate-run on evaluation-candidate-*.json before/after the edit"
          + (" (only the synthetic smoke exists)" if smoke.exists() else ""))

    width = max(len(item) for _, item, _ in results)
    for ok, item, detail in results:
        print(f"{'OK  ' if ok else 'TODO'}  {item:<{width}}  {detail}")
    todo = sum(not ok for ok, _, _ in results)
    print(f"\n{len(results) - todo}/{len(results)} ready; {todo} to do.")
    return int(bool(todo))


if __name__ == "__main__":
    raise SystemExit(main())
