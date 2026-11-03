"""Unit tests for the evaluation engine in eval/run_eval.py (section 5).

Every response below is FABRICATED UNIT-TEST DATA built in memory: hand-written
AskResponse-shaped dicts used to exercise the scoring rules. None of it is a real
retrieval result, and no API, model, network, Chroma or cluster is needed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

REAL_CLIENT = httpx.Client   # captured before any test patches run_eval.httpx.Client
EVAL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL_DIR))

import run_eval  # noqa: E402
from run_eval import citation_validity, gate, recall_at_k, summarise  # noqa: E402

RULES_2026 = "academic-rules-handbook-jun-2026.pdf"
RULES_2023 = "undergraduate-academic-rules-aug-2023.pdf"
FEES = "prospectus-2026-fee-information.pdf"


# ---------------------------------------------------------------- fabricated test data

def passage(source: str, section: str, page: int | None = None, cid: str = "c") -> dict:
    """One fabricated RetrievedRef."""
    return {"chunk_id": cid, "source": source, "section": section, "page": page, "score": 0.5}


def response(retrieved: list[dict], citations: list[dict] | None = None,
             answered: bool = True) -> dict:
    """One fabricated AskResponse (api/app/schemas.py)."""
    return {"request_id": "unit-test", "answer": "unit-test answer",
            "citations": citations if citations is not None else [],
            "answered_from_context": answered, "confidence": "low",
            "retrieved": retrieved, "provider": "unit-test"}


def cite(source: str, section: str, page: int | None = None) -> dict:
    return {"source": source, "section": section, "page": page}


def row(qid: str, qtype: str, relevant: list[dict], resp: dict,
        must_abstain: bool = False) -> dict:
    return {"id": qid, "type": qtype, "must_abstain": must_abstain, "relevant": relevant,
            "expect": None, "response": resp, "seconds": 0.0}


WARNING = {"source": RULES_2026, "section": "Academic Warning/Probation", "page": 24}
REPEAT = {"source": RULES_2026, "section": "Repeating a Course", "page": 25}


def good_result_rows() -> list[dict]:
    """A complete fabricated run: one question of every type, all answered well."""
    hit = passage(RULES_2026, "Academic Warning/Probation", 24, "w1")
    return [
        row("o1", "ordinary", [WARNING],
            response([hit], [cite(RULES_2026, "Academic Warning/Probation", 24)])),
        row("v1", "version", [REPEAT],
            response([passage(RULES_2026, "Repeating a Course", 25, "r1")],
                     [cite(RULES_2026, "Repeating a Course", 25)])),
        row("e1", "exact", [WARNING], response([hit], [cite(RULES_2026, "Academic Warning/Probation")])),
        row("i1", "injection", [WARNING], response([hit], [cite(RULES_2026, "Academic Warning/Probation")])),
        row("a1", "abstain", [], response([passage(FEES, "3. Tuition Fee", 1)], [], answered=False),
            must_abstain=True),
    ]


# ---------------------------------------------------------------- recall_at_k

def test_recall_counts_a_match_in_the_top_k() -> None:
    retrieved = [passage(FEES, "3. Tuition Fee"), passage(RULES_2026, "Academic Warning/Probation")]
    assert recall_at_k([WARNING], retrieved, 1) == 0.0
    assert recall_at_k([WARNING], retrieved, 2) == 1.0


def test_recall_at_3_and_at_5_differ_when_the_hit_is_ranked_fourth() -> None:
    misses = [passage(FEES, f"section {i}") for i in range(3)]
    retrieved = misses + [passage(RULES_2026, "Repeating a Course")]
    assert recall_at_k([REPEAT], retrieved, 3) == 0.0
    assert recall_at_k([REPEAT], retrieved, 5) == 1.0


def test_recall_rejects_the_right_document_with_the_wrong_section() -> None:
    retrieved = [passage(RULES_2026, "Attendance Requirements")]
    assert recall_at_k([WARNING], retrieved, 5) == 0.0


def test_recall_rejects_the_right_section_in_the_superseded_edition() -> None:
    retrieved = [passage(RULES_2023, "Repeating a Course")]
    assert recall_at_k([REPEAT], retrieved, 5) == 0.0


def test_recall_normalises_quotes_case_whitespace_and_punctuation() -> None:
    golden = [{"source": RULES_2026, "section": "Bachelor’s Degree Requirements"}]
    retrieved = [passage(RULES_2026.upper(), "  bachelor's degree   REQUIREMENTS ")]
    assert recall_at_k(golden, retrieved, 5) == 1.0


def test_recall_is_not_a_substring_match() -> None:
    golden = [{"source": RULES_2026, "section": "Grade Points"}]
    retrieved = [passage(RULES_2026, "Grade Points and GPA Calculation"),
                 passage(RULES_2026, "Points")]
    assert recall_at_k(golden, retrieved, 5) == 0.0


def test_recall_keeps_clause_numbers_distinct() -> None:
    golden = [{"source": RULES_2026, "section": "5.4"}]
    assert recall_at_k(golden, [passage(RULES_2026, "54")], 5) == 0.0
    assert recall_at_k(golden, [passage(RULES_2026, "5.4")], 5) == 1.0


def test_recall_accepts_the_last_segment_of_a_heading_path() -> None:
    retrieved = [passage(RULES_2026, "5. Academic Assessment > Academic Warning/Probation")]
    assert recall_at_k([WARNING], retrieved, 5) == 1.0


def test_duplicate_retrieved_chunks_do_not_inflate_recall() -> None:
    other = {"source": FEES, "section": "3. Tuition Fee"}
    retrieved = [passage(RULES_2026, "Academic Warning/Probation", cid=f"dup{i}") for i in range(5)]
    assert recall_at_k([WARNING, other], retrieved, 5) == 0.5


def test_recall_with_several_relevant_passages_is_the_fraction_found() -> None:
    golden = [WARNING, REPEAT, {"source": FEES, "section": "3. Tuition Fee"}]
    retrieved = [passage(RULES_2026, "Repeating a Course"), passage(FEES, "3. Tuition Fee")]
    assert recall_at_k(golden, retrieved, 5) == pytest.approx(2 / 3)


def test_recall_counts_a_duplicated_golden_entry_once() -> None:
    assert recall_at_k([WARNING, dict(WARNING)], [passage(RULES_2026, "Academic Warning/Probation")], 5) == 1.0


def test_recall_is_none_without_relevant_passages() -> None:
    assert recall_at_k([], [passage(FEES, "3. Tuition Fee")], 5) is None


def test_recall_ignores_malformed_retrieved_items_and_rejects_bad_inputs() -> None:
    retrieved = ["not a passage", {"section": "Academic Warning/Probation"}, None]
    assert recall_at_k([WARNING], retrieved, 5) == 0.0
    with pytest.raises(ValueError):
        recall_at_k([{"source": RULES_2026}], [], 5)          # golden entry with no section
    with pytest.raises(ValueError):
        recall_at_k([WARNING], [], 0)
    with pytest.raises(ValueError):
        recall_at_k([WARNING], "not a list", 5)  # type: ignore[arg-type]


# ---------------------------------------------------------------- citation_validity

def test_a_citation_of_a_retrieved_passage_is_valid() -> None:
    resp = response([passage(RULES_2026, "Repeating a Course", 25)],
                    [cite(RULES_2026, "Repeating a Course", 25)])
    assert citation_validity(resp) == 1.0


def test_a_citation_of_a_document_that_was_not_retrieved_is_fabricated() -> None:
    resp = response([passage(RULES_2026, "Repeating a Course", 25)],
                    [cite(RULES_2026, "Repeating a Course", 25),
                     cite(FEES, "3. Tuition Fee", 1)])          # real document, not retrieved
    assert citation_validity(resp) == 0.5


def test_a_citation_with_the_wrong_page_is_invalid() -> None:
    resp = response([passage(RULES_2026, "Repeating a Course", 25)],
                    [cite(RULES_2026, "Repeating a Course", 7)])
    assert citation_validity(resp) == 0.0


def test_a_citation_without_a_page_is_checked_on_source_and_section() -> None:
    resp = response([passage(RULES_2026, "Repeating a Course", 25)],
                    [cite(RULES_2026, "repeating a course")])
    assert citation_validity(resp) == 1.0


def test_citation_validity_is_none_without_citations() -> None:
    assert citation_validity(response([passage(FEES, "3. Tuition Fee")], [])) is None


def test_citation_validity_rejects_malformed_responses() -> None:
    with pytest.raises(ValueError):
        citation_validity({"retrieved": []})
    with pytest.raises(ValueError):
        citation_validity({"citations": []})
    assert citation_validity(response([], ["not a citation"])) == 0.0  # type: ignore[list-item]


# ---------------------------------------------------------------- summarise

def test_summarise_a_good_run_covers_every_question_type() -> None:
    results = summarise(good_result_rows())
    assert results["recall@3"] == 1.0 and results["recall@5"] == 1.0
    assert set(results["recall@5_by_type"]) == {"ordinary", "version", "exact", "injection", "abstain"}
    assert results["recall@5_by_type"]["abstain"] is None      # no answerable abstain questions
    assert results["citation_validity"] == 1.0
    assert results["abstention_correctness"] == 1.0
    assert results["counts"]["answerable"] == 4 and results["counts"]["must_abstain"] == 1
    assert results["malformed"] == []
    json.dumps(results)                                         # it becomes results.json


def test_recall_is_a_macro_average_over_questions() -> None:
    three = [WARNING, REPEAT, {"source": FEES, "section": "3. Tuition Fee"}]
    rows = [row("q1", "ordinary", three, response([passage(FEES, "3. Tuition Fee")])),   # 1/3
            row("q2", "ordinary", [WARNING], response([passage(FEES, "other")]))]       # 0/1
    assert summarise(rows)["recall@5"] == pytest.approx((1 / 3 + 0) / 2, abs=1e-6)


def test_must_abstain_questions_are_left_out_of_recall() -> None:
    rows = [row("a1", "abstain", [], response([], [], answered=False), must_abstain=True)]
    results = summarise(rows)
    assert results["recall@5"] is None
    assert results["abstention_correctness"] == 1.0


def test_a_must_abstain_question_answered_from_context_is_counted_as_a_failure() -> None:
    rows = good_result_rows()
    rows[-1]["response"]["answered_from_context"] = True
    results = summarise(rows)
    assert results["abstention_correctness"] == 0.0
    assert results["per_question"][-1]["abstained_correctly"] is False


def test_an_answer_with_no_citation_counts_against_citation_validity() -> None:
    rows = good_result_rows()
    rows[0]["response"]["citations"] = []                       # answered, cited nothing
    results = summarise(rows)
    assert results["counts"]["answers_without_citations"] == 1
    assert results["citation_validity"] == pytest.approx(3 / 4)
    assert results["citation_validity_cited_only"] == 1.0


def test_a_malformed_response_is_reported_and_scores_zero_not_one() -> None:
    rows = good_result_rows()
    del rows[0]["response"]["retrieved"]
    rows[1]["response"] = "an HTML error page"
    results = summarise(rows)
    assert {m["id"] for m in results["malformed"]} == {"o1", "v1"}
    assert results["per_question"][0]["recall@5"] == 0.0
    assert results["recall@5"] == 0.5


def test_a_must_abstain_response_without_the_flag_is_not_correct() -> None:
    rows = good_result_rows()
    del rows[-1]["response"]["answered_from_context"]
    results = summarise(rows)
    assert results["abstention_correctness"] == 0.0
    assert results["malformed"][0]["id"] == "a1"


def test_summarise_with_no_answerable_questions_leaves_recall_undefined() -> None:
    results = summarise([])
    assert results["recall@5"] is None and results["citation_validity"] is None
    assert results["abstention_correctness"] is None


# ---------------------------------------------------------------- gate

def metrics(recall5: float | None = 0.8, validity: float | None = 1.0,
            abstention: float | None = 1.0) -> dict:
    """Fabricated summarise() output, for gate tests only."""
    return {"recall@5": recall5, "citation_validity": validity,
            "abstention_correctness": abstention, "malformed": [], "per_question": []}


BASELINE = {"recall@5": 0.80}


def test_the_gate_passes_a_good_run_against_its_baseline() -> None:
    assert gate(metrics(), BASELINE) == []
    assert gate(summarise(good_result_rows()), {"recall@5": 1.0}) == []


def test_recall_falling_more_than_5_points_fails() -> None:
    failures = gate(metrics(recall5=0.74), BASELINE)
    assert len(failures) == 1 and "fell 6.0 points" in failures[0]


def test_recall_falling_exactly_5_points_passes() -> None:
    assert gate(metrics(recall5=0.75), BASELINE) == []          # 0.80 - 0.75, not more than 5
    assert gate(metrics(recall5=0.7499), BASELINE) != []


def test_the_gate_uses_points_not_relative_change() -> None:
    # 0.10 -> 0.06 is a 40% relative drop but only 4 points: it passes.
    assert gate(metrics(recall5=0.06), {"recall@5": 0.10}) == []


def test_citation_validity_below_95_percent_fails() -> None:
    assert gate(metrics(validity=0.95), BASELINE) == []
    failures = gate(metrics(validity=0.94), BASELINE)
    assert len(failures) == 1 and "below 95%" in failures[0]


def test_any_must_abstain_answer_fails_the_gate() -> None:
    results = summarise(good_result_rows())
    results["abstention_correctness"] = 0.5
    results["per_question"][-1]["abstained_correctly"] = False
    failures = gate(results, {"recall@5": 1.0})
    assert len(failures) == 1 and "a1" in failures[0]


def test_a_missing_baseline_fails_unless_bootstrapping() -> None:
    failures = gate(metrics(), None)
    assert len(failures) == 1 and "no baseline" in failures[0]
    assert gate(metrics(), None, bootstrap=True) == []


def test_a_bootstrap_baseline_still_needs_citations_and_abstention() -> None:
    failures = gate(metrics(validity=0.5, abstention=0.5), None, bootstrap=True)
    assert len(failures) == 2


@pytest.mark.parametrize("field", ["recall@5", "citation_validity", "abstention_correctness"])
def test_a_missing_or_undefined_metric_never_passes(field: str) -> None:
    results = metrics()
    results[field] = None
    assert gate(results, BASELINE) != []
    del results[field]
    assert gate(results, BASELINE) != []


@pytest.mark.parametrize("bad", [{}, {"recall@5": None}, {"recall@5": "0.8"}, {"recall@5": 1.5}])
def test_a_malformed_baseline_fails(bad: dict) -> None:
    assert any("baseline" in f for f in gate(metrics(), bad))


def test_malformed_responses_fail_the_gate() -> None:
    results = metrics()
    results["malformed"] = [{"id": "o1", "problems": ["retrieved missing or not a list"]}]
    assert any("malformed" in f for f in gate(results, BASELINE))


def test_the_gate_rejects_results_that_are_not_a_mapping() -> None:
    assert gate(None, BASELINE) != []  # type: ignore[arg-type]


# ---------------------------------------------------------------- main(): exit codes and baselines

GOLDEN_YAML = """questions:
  - id: q1
    type: ordinary
    question: When is a student placed on academic warning?
    relevant:
      - {source: academic-rules-handbook-jun-2026.pdf, section: "Academic Warning/Probation"}
  - id: q2
    type: abstain
    question: What is the hostel fee?
    relevant: []
    must_abstain: true
"""


def run_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *flags: str,
             answers_hostel: bool = False, hit: bool = True, status: int = 200) -> int:
    """Run main() against an in-memory fake API (httpx.MockTransport): no network."""
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, text="fake server error")
        question = json.loads(request.content)["question"]
        if "hostel" in question:
            return httpx.Response(200, json=response([], [], answered=answers_hostel))
        section = "Academic Warning/Probation" if hit else "Attendance Requirements"
        return httpx.Response(200, json=response([passage(RULES_2026, section, 24)],
                                                 [cite(RULES_2026, section, 24)]))

    monkeypatch.setattr(run_eval.httpx, "Client",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))
    golden = tmp_path / "golden.yaml"
    golden.write_text(GOLDEN_YAML, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--api", "http://unit-test",
                                      "--golden", str(golden),
                                      "--baseline", str(tmp_path / "baseline.json"),
                                      "--out", str(tmp_path / "results.json"), *flags])
    return run_eval.main()


def test_main_fails_without_a_baseline_but_still_writes_results(tmp_path, monkeypatch) -> None:
    assert run_main(tmp_path, monkeypatch) == 1
    assert (tmp_path / "results.json").exists()
    assert not (tmp_path / "baseline.json").exists()


def test_main_bootstraps_a_first_baseline_only_when_the_gates_pass(tmp_path, monkeypatch) -> None:
    assert run_main(tmp_path, monkeypatch, "--bootstrap-baseline", answers_hostel=True) == 1
    assert not (tmp_path / "baseline.json").exists()            # abstention failed: no baseline
    assert run_main(tmp_path, monkeypatch, "--bootstrap-baseline") == 0
    assert json.loads((tmp_path / "baseline.json").read_text())["recall@5"] == 1.0
    assert run_main(tmp_path, monkeypatch) == 0                 # a normal run now passes


def test_main_refuses_to_bootstrap_over_an_existing_baseline(tmp_path, monkeypatch) -> None:
    (tmp_path / "baseline.json").write_text(json.dumps({"recall@5": 1.0}))
    assert run_main(tmp_path, monkeypatch, "--bootstrap-baseline") == 2


def test_main_never_updates_the_baseline_to_hide_a_regression(tmp_path, monkeypatch) -> None:
    (tmp_path / "baseline.json").write_text(json.dumps({"recall@5": 1.0}))
    assert run_main(tmp_path, monkeypatch, "--update-baseline", hit=False) == 1
    assert json.loads((tmp_path / "baseline.json").read_text()) == {"recall@5": 1.0}


def test_main_exits_2_when_the_api_fails(tmp_path, monkeypatch) -> None:
    assert run_main(tmp_path, monkeypatch, status=500) == 2


def test_main_exits_2_on_a_corrupt_baseline(tmp_path, monkeypatch) -> None:
    (tmp_path / "baseline.json").write_text("{not json")
    assert run_main(tmp_path, monkeypatch) == 2


# ---------------------------------------------------------------- the golden sets themselves

@pytest.mark.parametrize("path", [EVAL_DIR / "golden.yaml",
                                  EVAL_DIR / "fixtures" / "golden.fixture.yaml"])
def test_every_golden_set_loads_and_its_evidence_is_well_formed(path: Path) -> None:
    questions = run_eval.load_golden(path)
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids))
    for q in questions:
        if q.get("must_abstain"):
            assert q["relevant"] == [], q["id"]
        else:
            assert q["relevant"], q["id"]
            assert recall_at_k(q["relevant"], [], 5) == 0.0    # parses; nothing retrieved


def test_the_real_golden_set_meets_the_section_5_1_minimums() -> None:
    questions = run_eval.load_golden(EVAL_DIR / "golden.yaml")
    counts: dict[str, int] = {}
    for q in questions:
        counts[q["type"]] = counts.get(q["type"], 0) + 1
    assert len(questions) >= 15
    for qtype, minimum in {"ordinary": 8, "version": 2, "exact": 2, "abstain": 2,
                           "injection": 1}.items():
        assert counts.get(qtype, 0) >= minimum, qtype
