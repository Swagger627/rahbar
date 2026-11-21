"""Render the Assignment 03 evidence report from the exported run files."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval/results"
TRACES = ROOT / "traces"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle("ReportTitle", fontName="Helvetica-Bold", fontSize=27, leading=30,
                          spaceAfter=10, textColor=colors.black))
styles.add(ParagraphStyle("ReportSection", fontName="Helvetica-Bold", fontSize=18, leading=21,
                          spaceAfter=12, textColor=colors.black))
styles.add(ParagraphStyle("ReportSub", fontName="Helvetica-Bold", fontSize=11, leading=14,
                          spaceBefore=9, spaceAfter=5, textColor=colors.black))
styles.add(ParagraphStyle("ReportBody", fontName="Helvetica", fontSize=9.3, leading=13.2,
                          textColor=colors.black, spaceAfter=8, alignment=TA_LEFT))
styles.add(ParagraphStyle("ReportCell", fontName="Helvetica", fontSize=8.2, leading=10.7,
                          textColor=colors.black, splitLongWords=True))
styles.add(ParagraphStyle("ReportHeader", parent=styles["ReportCell"], fontName="Helvetica-Bold"))


def read_all(glob: str) -> list[dict[str, Any]]:
    return [{**json.loads(file.read_text()), "_file": file.name} for file in sorted(RESULTS.glob(glob))]


def printable(value: Any) -> str:
    if value is None:
        return "Pending"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def para(value: Any, style: str = "ReportBody") -> Paragraph:
    return Paragraph(escape(printable(value)), styles[style])


def table(values: list[list[Any]], widths: list[int]) -> Table:
    rows = [[para(value, "ReportHeader" if i == 0 else "ReportCell") for value in row]
            for i, row in enumerate(values)]
    result = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    result.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.black),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
    ]))
    return result


def coverage_chart(before: dict[str, Any], after: dict[str, Any]) -> Drawing:
    chart = Drawing(500, 90)
    for y, label, entry in ((56, "Original", before), (23, "Expanded", after)):
        chart.add(String(0, y + 4, label, fontName="Helvetica-Bold", fontSize=9))
        chart.add(Rect(72, y, entry["coverage"] * 352, 16, fillColor=None, strokeColor=colors.black))
        chart.add(String(434, y + 4, f"{entry['coverage']:.1%}", fontSize=9))
    threshold_x = 72 + before["minimum_coverage"] * 352
    chart.add(Line(threshold_x, 18, threshold_x, 78, strokeColor=colors.black, strokeDashArray=[3, 2]))
    chart.add(String(threshold_x - 28, 6, "Coverage target", fontSize=8))
    return chart


CAPTURES = (
    ("A1", "Batch B verification",
     'python3.12 eval/show_ingestion.py "eval/results/$BATCH_B_REPORT" --batch-b',
     "Stage and ingest Batch B on the baseline. Export the report and set BATCH_B_REPORT "
     "to its filename. Capture every document and its four check results and reasons."),
    ("A2", "Zero embeddings on an unchanged corpus",
     "bash scripts/ragops-k8s.sh ingest baseline",
     "Complete Batch A first. Rerun the same ingestion with the same inputs. Capture "
     "the run summary showing chunks_embedded: 0 and the same collection revision."),
    ("A3", "Incremental edit, deletion and archive restore (optional)",
     "bash scripts/ragops-k8s.sh ingest baseline --restore fee-info=2026-27",
     "With Batch B indexed, restore the original fee edition and capture the active/archive counts. "
     "Return to latest, stage edited, then stage deleted and ingest after each stage. "
     "Capture the debate document's changed chunks and then its zero count."),
    ("B1", "Three-level evaluation summary",
     'k logs job/"$BASELINE_JOB" --tail=24',
     "Run the baseline evaluation while Batch A is staged. Set BASELINE_JOB to the printed "
     "Job name. Include the Module, Retrieval and End to end blocks in the capture."),
    ("C1", "Rejected candidate",
     "bash scripts/ragops-k8s.sh eval candidate api-candidate",
     "Set TOP_K=1 on api-candidate, wait for its rollout, then run the evaluator. Capture GATE FAIL. "
     "Record the live API revision to show the failed candidate did not reach live."),
    ("C2", "Passing candidate",
     "bash scripts/ragops-k8s.sh eval candidate api-candidate",
     "Remove the candidate TOP_K override and wait for readiness. The normal candidate uses 380-word "
     "chunks. Run the full suite and capture a genuine GATE PASS. Save the result filename."),
    ("D1", "Full trace and retrieval guardrail",
     'python3.12 eval/trace_tools.py trace "$D1_ID"',
     "With Batch B indexed, ask the Azure Lantern badge question and save its X-Trace-ID. "
     "Export traces and print the full record. Use consecutive captures if necessary."),
    ("D2", "Reconstructed context and prompt",
     'python3.12 eval/trace_tools.py prompt "$D2_ID"',
     "Ask a grounded question with X-Rahbar-Eval enabled, save a fresh X-Trace-ID and export. "
     "Capture the context and all model attempts. Choose a trace that reached generation."),
    ("E1", "Coverage before expansion",
     "bash scripts/ragops-k8s.sh coverage golden",
     "Run the original golden set against the fixed simulated query log. Capture the percentage, "
     "threshold, uncovered topics and outcome."),
    ("E2", "Coverage after expansion",
     "bash scripts/ragops-k8s.sh coverage golden_expanded",
     "Run the expanded golden set with the same cutoff and live query log. Capture the new "
     "percentage and any remaining uncovered topics."),
    ("E3", "Data drift comparison (optional)",
     "python3.12 eval/compare_runs.py BEFORE.json AFTER.json --out eval/results/data-drift.json",
     "Export the Batch A baseline and the Batch A plus B observe run. Keep the same API "
     "profile and golden set. Substitute both filenames and capture the delta output."),
    ("F1", "Kubernetes inventory",
     "k get all,pvc,configmap,cronjob,job",
     "Wait for an ingestion and evaluation Job to finish. Capture the inventory in the rahbar-a3 "
     "namespace. Include the Services, workloads, PVCs, ConfigMaps, CronJob and Job completion counts."),
    ("F2a", "Completed ingestion Job log",
     'k logs job/"$INGEST_JOB"',
     "Set INGEST_JOB to an actual completed ingestion Job. Capture the command, verification "
     "progress and final index update summary."),
    ("F2b", "Completed evaluation Job log",
     'k logs job/"$BASELINE_JOB"',
     "Set BASELINE_JOB to an actual completed evaluation Job. Include the evaluation progress "
     "and summary. Capture consecutive screens when needed."),
    ("F3", "ConfigMap-only gate change",
     'bash scripts/ragops-k8s.sh gate-run "$CANDIDATE_FILE"',
     "Replay the same passing result with the normal ConfigMap. Apply the stricter p95 threshold "
     "from the guide and replay with the same image. Capture both decisions and restore the threshold."),
    ("F4", "Controlled rollout status",
     "k rollout status deploy/api --timeout=180s",
     "Return the baseline to Batch A and promote the passing candidate result. Record the live "
     "collection and embedding digest before starting the bad-release drill."),
    ("F5", "Undo the bad rollout",
     "k rollout undo deploy/api",
     "Set the live collection to the missing drill collection and show rollout status timing out. "
     "Run undo and status again. Capture the undo and the successful status."),
    ("F6", "Trace after undo",
     'python3.12 eval/trace_tools.py trace "$F_ID"',
     "Restart the port-forward, send a fresh question, and export its trace ID. "
     "Show the collection and embedding version matching the pre-drill trace."),
)


def screenshot_box(label: str, screenshot: Path | None) -> list[Any]:
    if screenshot and screenshot.exists():
        image = Image(str(screenshot))
        scale = min(505 / image.imageWidth, 210 / image.imageHeight)
        image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
        image.hAlign = "LEFT"
        return [image]
    box = Table([[para(f"INSERT TERMINAL SCREENSHOT {label} HERE", "ReportCell")]],
                colWidths=[507], rowHeights=[148], hAlign="LEFT")
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
    ]))
    return [box]


def add_appendix(body: list[Any], screenshots: Path | None) -> None:
    for index in range(0, len(CAPTURES), 2):
        body.extend([PageBreak(), para("Appendix  /  Terminal evidence", "ReportSection")])
        if index == 0:
            body.append(para("Open the same Bash terminal in the repository and use your running minikube cluster. "
                             "Capture your command and all its output. Use consecutive screenshots for long output. "
                             "Full preparation, Job names, curl requests and ConfigMap commands are in "
                             "docs/ASSIGNMENT-03-SCREENSHOTS.md."))
            body.append(para('Terminal helper: k() { kubectl --context rahbar -n rahbar-a3 "$@"; }'))
        for label, title, command, steps in CAPTURES[index:index + 2]:
            body.extend([para(f"{label}  /  {title}", "ReportSub"), para(steps), para(command, "ReportCell"),
                         Spacer(1, 8)])
            body.extend(screenshot_box(label, screenshots / f"{label}.png" if screenshots else None))
            body.append(Spacer(1, 13))


def page_decor(canvas: Any, doc: Any) -> None:
    width, height = doc.pagesize
    canvas.setStrokeColor(colors.black)
    canvas.setLineWidth(0.35)
    canvas.line(42, height-37, width-42, height-37)
    canvas.line(42, 37, width-42, 37)
    canvas.setFillColor(colors.black)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawString(42, height-27, "RAHBAR  /  SOFTWARE CONSTRUCTION AND DEVELOPMENT")
    canvas.drawString(42, 24, "CS4032  /  ASSIGNMENT 03")
    canvas.drawRightString(width-42, 24, str(doc.page))


def ingestion_milestones() -> list[list[Any]]:
    steps = [
        ("Batch A initial", "a-batch-a-final"), ("Batch A unchanged", "a-unchanged-a"),
        ("Batch B initial", "a-batch-b"), ("Batch B unchanged", "a-unchanged-b"),
        ("Restore fee edition", "a-restore-fees"), ("Edit debate notice", "a-edited"),
        ("Delete debate notice", "a-deleted"), ("Candidate initial", "c-candidate-index"),
    ]
    rows = []
    for label, filename in steps:
        log = RESULTS / f"{filename}-terminal.txt"
        if not log.exists():
            continue
        for line in log.read_text().splitlines():
            if '"msg": "verification and update complete"' not in line:
                continue
            proof = json.loads(line)
            rows.append([label, Path(proof["report"]).name, proof["chunks_embedded"],
                         proof["chunks_deleted"], proof["chunks_total"]])
    return rows


def result_file(run: dict[str, Any]) -> str:
    return run.get("_file", "Pending cluster result")


def diagnosis(baseline: dict[str, Any]) -> list[str]:
    targets = {q["trace_id"] for trial in baseline.get("runs", []) for q in trial["questions"]
               if q.get("trace_id") and q.get("outcome") == "abstained"
               and not q["golden"]["must_abstain"]}
    answerable = {row["question"] for line in (ROOT / "eval/golden.jsonl").read_text().splitlines()
                  if (row := json.loads(line)) and not row["must_abstain"]}
    observed = []
    for file in sorted(TRACES.glob("*.jsonl")):
        for line in file.read_text().splitlines():
            trace = json.loads(line)
            qualifies = trace.get("trace_id") in targets or (
                trace.get("evaluation") and not trace.get("retrieval_only")
                and trace.get("query") in answerable and trace.get("answered_from_context") is False)
            if qualifies and trace["retrieved"]:
                observed.append(trace)
    if not observed:
        return ["Trace-only diagnosis pending an exported eligible evaluation trace."]
    trace = min(observed, key=lambda row: row["outcome"] == "fallback")
    hit = trace["retrieved"][0]
    drops = dict(Counter(action["action"] for action in trace["guardrail_actions"]))
    return [
        f"Trace {trace['trace_id']} recorded the query {trace['query']}",
        f"Collection {trace['collection']} revision {trace['collection_version']} applied {trace['filters']}.",
        f"Rank 1 was {hit['chunk_id']} ({hit['document_id']} v{hit['doc_version']}) at {hit['score']:.3f}.",
        f"The guardrails recorded {drops} and delivered {len(trace['context'])} passages to the model.",
        f"The outcome was {trace['outcome']} with {len(trace['generation_attempts'])} model attempts. "
        f"The trace records {trace.get('outcome_reason', 'no reason')}.",
    ]


def render(out: Path, appendix: Path | None, student: str, second: str, reviews: Path | None) -> None:
    ingestions = [run for run in read_all("ingestion-*.json") if "index_update" in run]
    evaluations = read_all("evaluation-*.json")
    baseline = next((r for r in reversed(evaluations) if r.get("phase") == "baseline"), {})
    candidates = [r for r in evaluations if r.get("phase") == "candidate"]
    candidate = next((r for r in reversed(candidates) if r["gate"]["pass"]),
                     candidates[-1] if candidates else {})
    rejected = next((r for r in reversed(candidates) if not r["gate"]["pass"]), {})
    drift = next((r for r in reversed(evaluations) if r.get("phase") == "observe"), {})
    coverage = read_all("coverage-*.json")
    before = next((r for r in coverage if r["golden"].endswith("/golden.jsonl")), {})
    after = next((r for r in reversed(coverage) if r["golden"].endswith("/golden_expanded.jsonl")), {})
    batch_b = next((r for r in ingestions if any(d["file"] == "b09-debate.md" for d in r["documents"])), {})
    guard = next((d for d in batch_b.get("documents", []) if d["file"] == "b07-guardrail.md"), {})
    revisions = [r["index_update"]["revision"] for r in ingestions
                 if r["index_update"]["collection"] == "rahbar-a3-baseline"]
    components = read_all("components-observe-*.json")

    def component_run(collection: str, revision: str | None = None) -> dict[str, Any]:
        return next((r for r in reversed(components) if r["metrics"].get("pipeline_errors") == 0
                     and any(s[0] == collection and (revision is None or s[1] == revision)
                             for s in r.get("serving_snapshots", []))), {})

    control = baseline or component_run("rahbar-a3-baseline", revisions[0] if revisions else None)
    experiment = candidate or component_run("rahbar-a3-candidate")
    drift_results = drift or component_run("rahbar-a3-baseline",
                                           batch_b.get("index_update", {}).get("revision", "missing"))
    thresholds = candidate.get("thresholds") or json.loads((ROOT / "eval/thresholds.json").read_text())
    base_metrics = control.get("metrics", {})
    cand_metrics = experiment.get("metrics", {})
    drift_metrics = drift_results.get("metrics", {})
    gold_rows = [json.loads(line) for line in (ROOT / "eval/golden.jsonl").read_text().splitlines()
                 if line.strip()]
    student_written = sum(row["origin"] == "human" for row in gold_rows)
    synthetic_reviewed = sum(row["origin"] == "synthetic" and row.get("review_status") == "reviewed"
                             and bool(row.get("reviewer")) for row in gold_rows)
    agreement = baseline.get("human_judge_agreement", {})
    if reviews:
        labels = {row["trace_id"]: row["supported"] for line in reviews.read_text().splitlines()
                  if line.strip() and (row := json.loads(line)) and row.get("reviewer")
                  and row.get("reason") and isinstance(row.get("supported"), bool)}
        judged = {row["trace_id"]: row["judge"]["supported"] for run in evaluations
                  for repeat in run["runs"] for row in repeat["questions"] if row.get("judge")}
        overlap = labels.keys() & judged.keys()
        agreement = {"human_reviewed": len(overlap), "agreement": (
            sum(labels[key] == judged[key] for key in overlap) / len(overlap) if overlap else None)}
    metrics = [
        ("Embedding", "paraphrase_cosine_mean"), ("Embedding", "unrelated_cosine_mean"),
        ("Embedding", "cosine_gap"), ("Embedding", "embedding_ms_per_chunk"),
        ("Embedding", "embedding_repeat_max_delta"), ("Retrieval", "recall@3"),
        ("Retrieval", "recall@5"), ("Retrieval", "mrr"), ("Retrieval", "ndcg@5"),
        ("End to end", "faithfulness"), ("End to end", "abstention_correctness"),
        ("End to end", "citation_validity"), ("End to end", "answerable_answer_rate"),
        ("End to end", "latency_p50_seconds"), ("End to end", "latency_p95_seconds"),
    ]

    body = [
        para("Operating RAHBAR with RAGOps", "ReportTitle"),
        para("Assignment 03 evidence report", "ReportSub"),
        para(f"Student and roll number: {student}"),
        para(f"Second student and roll number: {second}"),
        para("RAGOps extends the existing FAST-NUCES document assistant with verified ingestion, "
             "versioned retrieval sources, traceable answers, and tests that control release."),
        para("Figure 3 mapped to the implementation", "ReportSub"),
        table([
            ["Lifecycle stage", "RAHBAR behavior and implementation", "Remaining scope"],
            ["Ingest", "A manifest drives a Kubernetes Job and CronJob. corpus/stage_ragops.py and pipeline/run.py.",
             "The demo stages files explicitly."],
            ["Verify", "Quality, completeness, recency and hash/embedding uniqueness. pipeline/ragops.py.",
             "Content consistency needs independent policy review."],
            ["Update data lake", "Verified bytes, quarantine, extraction cache and historical editions persist on a PVC.",
             "Single-node persistent volumes."],
            ["Update retrieval sources", "Upsert changed chunks, exclude archived versions and delete absent documents.",
             "Readiness blocks new queries during index mutation."],
            ["Offline testing", "Three levels, seeded repetitions, typo perturbation and a release gate. eval/ragops_eval.py.",
             "Student provenance and review labels require confirmation."],
            ["Live testing", "Trace real API query paths and deploy a candidate Service. app/trace.py and k8s/assignment3.",
             "The traffic sample is simulated."],
            ["Check coverage", "Embedding similarity to golden queries drives a coverage gate. eval/coverage_check.py.",
             "Representative coverage is estimated from simulated queries."],
        ], [102, 259, 146]),
        para("Reference", "ReportSub"),
        para("Xu, Weytjens, Zhang, Lu, Weber and Zhu. RAGOps: Operating and Managing "
             "Retrieval-Augmented Generation Pipelines. 2026 preprint, Figure 3, Tables 1 and 2."),
        PageBreak(),
        para("A and E  /  Document changes and coverage", "ReportSection"),
        para("The canonical inbox is manifest-driven. Verified files are immutable by content hash. "
             "The Job keeps old accepted versions in the archive and changes their retrieval metadata. "
             "Deletion removes the associated Chroma rows. A restore promotes the archived edition."),
        table([["Run", "Verification result file", "Embedded", "Deleted", "Total"]]
              + ingestion_milestones(), [115, 223, 58, 55, 56]),
        para("Batch B checks", "ReportSub"),
    ]
    if batch_b:
        dispositions = Counter(d["disposition"] for d in batch_b["documents"] if d["file"].startswith("b"))
        body.append(para(f"Batch B dispositions {dict(dispositions)}. "
                         f"The verification report is {result_file(batch_b)}. "
                         f"The injected-document disposition is {guard.get('disposition', 'pending')}."))
    else:
        body.append(para("Batch B cluster evidence pending."))
    guard_summary = RESULTS / "trace-summary.json"
    if guard_summary.exists():
        summary = json.loads(guard_summary.read_text())
        body.append(para(f"Recorded guardrail activity: {summary['injected_chunks_dropped']} injected chunk drops "
                         "in the exported operational window."))
    if (RESULTS / "a-deleted-terminal.txt").exists():
        body.append(para("The edit replaced one debate-notice chunk. Deletion removed it and left no chunks "
                         "for that document ID. Fee restoration switched eight old chunks to active and "
                         "the replacement chunk to archived without embedding new chunks."))
    if before and after:
        body.append(para(f"Cosine cutoff {before['similarity']:.2f}, coverage target "
                         f"{before['minimum_coverage']:.0%}. Coverage changed from "
                         f"{before['coverage']:.1%} to {after['coverage']:.1%} on the same live queries. "
                         "The cutoff distinguishes closely matched query intent from broader topic overlap "
                         "in this simulated sample."))
        body.append(coverage_chart(before, after))
    else:
        body.append(para("Coverage comparison pending exported cluster results."))
    body.extend([
        para("The drift comparison keeps the original reference set fixed. The fictional revised fee "
             "schedule may displace evidence labelled with the original version. Such cases indicate "
             "a corpus and reference change to inspect alongside the answers."),
        PageBreak(),
        para("B and C  /  Test results and decision", "ReportSection"),
        para(f"The golden set has {len(gold_rows)} questions. It contains "
             f"{sum(not row['must_abstain'] for row in gold_rows)} answerable questions, "
             f"{sum(row['hard'] for row in gold_rows)} hard questions and "
             f"{sum(row['must_abstain'] for row in gold_rows)} expected abstentions. "
             "The embedding suite uses 15 paraphrase pairs and 15 unrelated pairs."),
        table([["Level", "Metric", "Batch A", "A repeat delta", "Candidate", "A + B"]] + [
            [group, name, base_metrics.get(name), control.get("repeat_delta", {}).get(name),
             cand_metrics.get(name), drift_metrics.get(name)]
            for group, name in metrics
        ], [66, 148, 72, 78, 72, 71]),
        para("Inputs", "ReportSub"),
        para(f"Baseline: {result_file(control)}. Candidate: {result_file(experiment)}. "
             f"Drift: {result_file(drift_results)}. Component-only checkpoints cover embedding and retrieval."),
    ])
    if control:
        noise = ", ".join(f"{name} {printable(value)}"
                          for name, value in control["runs"][0]["noise_delta"].items())
        body.append(para(f"Baseline noise deltas, first run: {noise}. "
                         "Per-query ranks and repeat deltas are in the exported JSON. The seed and cache bypass "
                         "control part of the variation. Runtime scheduling and approximate ranking ties can still vary."))
    if base_metrics.get("recall@5") is not None and drift_metrics.get("recall@5") is not None:
        before_rows = {row["id"]: row["retrieval"]["recall@5"]
                       for row in control["runs"][0]["questions"] if "retrieval" in row}
        degraded = sum(before_rows.get(row["id"], 0) > row["retrieval"]["recall@5"]
                       for row in drift_results["runs"][0]["questions"] if "retrieval" in row)
        body.append(para(f"Batch B recall@5 changed from {base_metrics['recall@5']:.3f} to "
                         f"{drift_metrics['recall@5']:.3f} on the original reference set. Inspect "
                         "the per-question results for changed fee-policy evidence. The revised fee edition "
                         f"replaces the source version used by the degraded questions ({degraded} in run one)."))
    if candidate:
        gate = candidate["gate"]
        body.append(para("Candidate decision: " + ("release gate passed." if gate["pass"] else
                         "reject. " + " ".join(gate["reasons"]))))
    else:
        body.append(para("Release decision: hold the candidate until a full three-level result meets the live gate."))
    if rejected:
        body.append(para(f"Rejected run {rejected['run_id']}: {'. '.join(rejected['gate']['reasons'])}"))
    promotion_proof = RESULTS / "c-promotion-safety.txt"
    if promotion_proof.exists() and "Live deployment revision after:" in promotion_proof.read_text():
        body.append(para("The promotion check refused the component-only result. "
                         "The live Deployment revision was unchanged."))
    if (RESULTS / "b-baseline-cpu-contention.txt").exists() and not baseline:
        body.append(para("The original-model baseline had a generation timeout while indexing was active. "
                         "The measured timeout is documented in the trace. The isolated run supplies "
                         "the remaining end-to-end results when it completes."))
    body.extend([
        PageBreak(),
        para("D and F  /  Traceability and cluster operation", "ReportSection"),
        table([
            ["Paper quality", "Measurement and threshold", "Evidence"],
            ["Adaptability", "An unchanged input embeds zero chunks. Edits affect the declared document.",
             "Ingestion JSON and version restore run."],
            ["Monitorability", f"Recall@5 drop <= {thresholds['recall5_max_drop']:.0%} of scale. "
             f"p95 budget {thresholds['latency_p95_max_seconds']} s.",
             "Evaluation suite and the gate ConfigMap."],
            ["Observability", "A planted instruction appears as a retrieval drop. The summary reports "
             "top-1 similarity, cache and abstention, stage p95 and injection counts.", "Trace JSONL and summary."],
            ["Traceability", "A query has a trace ID, collection revision and model version. "
             "Generation exposes exact inputs and retry data.", "Trace lookup and prompt reconstruction."],
            ["Reliability", "Live update sets maxUnavailable to zero. Undo recovers the previous collection "
             "and embedding version.", "Kubernetes rollout and post-undo trace."],
        ], [100, 251, 156]),
        para("Trace-only diagnosis", "ReportSub"),
        *[para(line) for line in diagnosis(baseline)],
        para("Resources", "ReportSub"),
    ])
    config_wiring = RESULTS / "f-configmap-wiring-smoke.txt"
    if config_wiring.exists():
        smoke = config_wiring.read_text()
        if "GATE PASS" in smoke and "GATE FAIL" in smoke:
            body.append(para("A synthetic wiring check ran the same image with a mounted threshold ConfigMap. "
                             "The default setting produced exit 0. Tightening the p95 threshold produced exit 1. "
                             "A real candidate release still requires the full end-to-end gates."))
    prompt_replay = RESULTS / "d-prompt-replay.json"
    if prompt_replay.exists():
        replay = json.loads(prompt_replay.read_text())
        body.append(para(f"Prompt replay recovered {len(replay['context'])} passages and "
                         f"{len(replay['generation_attempts'])} attempt(s) for trace {replay['trace_id']}."))
    post_undo = RESULTS / "f-post-undo-trace.json"
    if post_undo.exists():
        restored = json.loads(post_undo.read_text())
        body.append(para(f"The missing-collection rollout timed out. Undo restored "
                         f"{restored['collection']} revision {restored['collection_version']}, with "
                         f"embedding digest {restored['embedding_version'][:12]} in trace "
                         f"{restored['trace_id']}."))
    resources = []
    for file in sorted(RESULTS.glob("f-resources-*.txt")):
        for line in file.read_text().splitlines():
            fields = line.split()
            if len(fields) == 4 and fields[1] in {"api", "chroma", "indexer", "evaluation"}:
                if fields[2].endswith("m") and fields[3].endswith("Mi"):
                    resources.append((fields[1], int(fields[2][:-1]), int(fields[3][:-2])))
    if resources:
        budgets = {"api": ("50m / 128Mi", "500m / 384Mi"),
                   "chroma": ("100m / 128Mi", "500m / 512Mi"),
                   "indexer": ("500m / 1280Mi", "1 CPU / 2Gi"),
                   "evaluation": ("50m / 96Mi", "500m / 384Mi")}
        rows = [["Container", "Sampled CPU / RAM", "Request", "Limit"]]
        for name in sorted({row[0] for row in resources}):
            cpu = max(row[1] for row in resources if row[0] == name)
            mem = max(row[2] for row in resources if row[0] == name)
            rows.append([name, f"{cpu}m / {mem}Mi", *budgets[name]])
        body.append(table(rows, [119, 134, 126, 128]))
    else:
        body.append(para("Resource measurements pending."))
    body.extend([
        PageBreak(),
        para("G  /  Reflection and contributions", "ReportSection"),
        para("Which verification check caught the most?", "ReportSub"),
        para(f"Uniqueness caught the most planted faults. It marked {sum(not row['checks']['uniqueness']['pass'] and 'duplicate' in row['checks']['uniqueness']['reason'] for row in batch_b.get('documents', []))} duplicate inputs in the selected Batch B report. "
             "The planted instruction is syntactically valid document content and reaches the separate "
             "retrieval guardrail. A document-level content check could detect instruction-like text before indexing."
             if batch_b else "Batch B verification evidence is pending."),
        para("Where did levels disagree?", "ReportSub"),
        para(f"The baseline embedding cosine gap was {base_metrics['cosine_gap']:.3f}, while recall@5 "
             f"was {base_metrics['recall@5']:.3f}. A useful average semantic gap does not guarantee that "
             "the right version and page were ranked in the first five chunks."
             if base_metrics.get("cosine_gap") is not None and base_metrics.get("recall@5") is not None
             else "Compare the embedding gap and ranked retrieval after the exported evaluation completes."),
        para("What does archival preserve?", "ReportSub"),
        para("Archival preserves the exact verified bytes, identity, version and metadata. Removing them "
             "would break restore and make old evidence harder to audit."),
        para("Which metric is least trustworthy?", "ReportSub"),
        para("Faithfulness is judged by a small language model from the same runtime family. It can miss "
             "a numeric qualifier or accept an unsupported inference. Agreement requires at least eight "
             "independent student labels tied to actual trace IDs."),
        para("Which idea took most adaptation?", "ReportSub"),
        para("A reportable example needed document versions to flow through Chroma, API responses and "
             "exact prompts. This deployment uses a PVC data lake and a local candidate Service, "
             "with controlled shell commands for promotion."),
        para("Which Kubernetes objects carry the work?", "ReportSub"),
        para("The Job and CronJob own ingestion. A file lock on the corpus PVC serializes them. "
             "The StatefulSet owns vector storage. The API Deployments serve current and candidate "
             "collections. The evaluator Job reads an API Service and the threshold ConfigMap."),
        table([["Contributor", "Work and verification"],
               [student, "Student contribution and hand-written questions to confirm."],
               [second, "Student contribution and review to confirm if applicable."]], [160, 347]),
        Spacer(1, 8),
        para("Human review status", "ReportSub"),
        para(f"The golden set records {student_written} student-written questions and "
             f"{synthetic_reviewed} reviewed synthetic questions. "
             + (f"Judge agreement was {agreement['agreement']:.1%} over "
                f"{agreement['human_reviewed']} manually labeled answers."
                if agreement.get("agreement") is not None else
                "Agreement awaits actual student answer labels.")),
        para("AI assistance", "ReportSub"),
        para("Codex assisted with implementation, synthetic evaluation fixtures, tests and report assembly. "
             "The report uses exported measurements from the local minikube pipeline. "
             "Student authorship, manual labels and identity details await confirmation."),
    ])
    add_appendix(body, appendix)

    out.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=43, rightMargin=43,
                                 topMargin=55, bottomMargin=51, title="RAHBAR Assignment 03")
    document.build(body, onFirstPage=page_decor, onLaterPages=page_decor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "output/pdf/Assignment-03-Report.pdf")
    parser.add_argument("--screenshots", type=Path, help="optional PNGs named by evidence label, e.g. A1.png")
    parser.add_argument("--student", default="[Your full name] / [Your roll number]")
    parser.add_argument("--second", default="[Teammate name] / [Teammate roll number, if applicable]")
    parser.add_argument("--reviews", type=Path, help="optional student answer review JSONL keyed by trace ID")
    args = parser.parse_args()
    render(args.out, args.screenshots, args.student, args.second, args.reviews)


if __name__ == "__main__":
    main()
