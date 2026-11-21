"""Regression cases for page evidence and the Assignment 03 release gate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ragops_eval import relevant_rank  # noqa: E402
from release_gate import decision  # noqa: E402


def test_retrieval_credits_pages_once_and_rejects_stale_versions() -> None:
    gold = {"relevant": [
        {"document_id": "rules", "doc_version": "2026", "page": 3},
        {"document_id": "rules", "doc_version": "2026", "page": 9},
    ]}
    matches = [
        {"document_id": "rules", "doc_version": "2023", "pages": [3, 9]},
        {"document_id": "rules", "doc_version": "2026", "pages": [2, 3]},
        {"document_id": "rules", "doc_version": "2026", "page": 3},
        {"document_id": "rules", "doc_version": "2026", "page": 9},
    ]
    ranks = relevant_rank(gold, matches)
    assert ranks["recall@3"] == .5
    assert ranks["recall@5"] == 1
    assert ranks["mrr"] == .5
    assert .5 < ranks["ndcg@5"] < 1


def test_threshold_edit_can_fail_identical_metrics() -> None:
    metrics = {"recall@5": .90, "faithfulness": .80, "abstention_correctness": .80,
               "latency_p95_seconds": 4.0, "citation_validity": 1, "answerable_answer_rate": .7,
               "pipeline_errors": 0}
    thresholds = {"recall5_max_drop": .02, "faithfulness_min": .60, "abstention_max_drop": 0,
                  "latency_p95_max_seconds": 5, "citation_validity_min": .95,
                  "answerable_answer_rate_min": .4}
    assert not decision(metrics, metrics, thresholds)
    assert decision(metrics, metrics, {**thresholds, "latency_p95_max_seconds": 1})
    assert decision({**metrics, "pipeline_errors": 1}, metrics, thresholds)
    assert decision({**metrics, "faithfulness": None}, metrics, thresholds)
