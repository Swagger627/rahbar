#!/usr/bin/env python3
"""Score a running RAHBAR against a golden set, and gate CI on the result (section 5).

    python eval/run_eval.py --api http://<ingress>                 # your corpus
    python eval/run_eval.py --api ... --golden eval/fixtures/golden.fixture.yaml
    python eval/run_eval.py --api ... --bootstrap-baseline         # once: the first baseline
    python eval/run_eval.py --api ... --update-baseline            # after a deliberate improvement

Exit code 0 when every gate passes, 1 when any fails, 2 when the evaluation itself could
not run (the API was unreachable, the golden set or baseline is malformed). CI relies on
that exit code.

Given: talking to the API, reading the golden set, writing results.json, the exit
code. Ours: recall_at_k, citation_validity, summarise and gate below, and the two
baseline flags. Their docstrings state the matching and aggregation rules, which the
"Evaluation method" section of docs/PIPELINE.md must repeat.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent

# The gate from section 5.4. Change these only with a reason written in docs/PIPELINE.md.
MAX_RECALL5_DROP = 0.05        # recall@5 may fall at most 5 points below the baseline
MIN_CITATION_VALIDITY = 0.95   # at least 95% of citations must match retrieved passages
REQUIRED_ABSTENTION = 1.0      # every must-abstain question must be declined

# Typographic quotes and dashes folded to ASCII before comparing headings: the PDFs
# print "Bachelor’s", a chunker or a golden-set author may type "Bachelor's".
_FOLD = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
})
# A chunker may record a heading path such as "5. Academic Assessment > Repeating a Course".
_PATH_SEPARATOR = " > "


def load_golden(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    questions = data.get("questions") or []
    if not questions:
        sys.exit(f"no questions in {path}")
    return questions


def ask(client: httpx.Client, api: str, question: str) -> dict:
    """POST one question and return the AskResponse as a dict (see api/app/schemas.py)."""
    response = client.post(f"{api.rstrip('/')}/api/ask", json={"question": question})
    response.raise_for_status()
    return response.json()


def normalise_heading(value: object) -> str:
    """Comparison key for a section heading: Unicode NFKC, typographic quotes and dashes
    folded, case folded, and every run of punctuation or whitespace turned into one space.
    "Bachelor’s Degree Requirements" and "bachelor's degree  requirements" give the same
    key; "5.4" and "54" do not. Non-strings give "" (which never matches)."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).translate(_FOLD).casefold()
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


def normalise_source(value: object) -> str:
    """Comparison key for a source: the file name only, NFKC and case folded. No
    punctuation is removed, so different files can never collide."""
    if not isinstance(value, str):
        return ""
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    return unicodedata.normalize("NFKC", name).casefold().strip()


def _section_keys(section: object) -> set[str]:
    """The keys a retrieved or cited section can match: the whole heading, and its last
    segment when the chunker recorded a heading path."""
    keys = {normalise_heading(section)}
    if isinstance(section, str) and _PATH_SEPARATOR in section:
        keys.add(normalise_heading(section.rsplit(_PATH_SEPARATOR, 1)[-1]))
    return keys - {""}


def _golden_key(item: object) -> tuple[str, str]:
    """(source, section) key of one golden-set evidence entry; malformed entries raise,
    because silently dropping one would shrink the denominator and inflate recall."""
    if not isinstance(item, dict):
        raise ValueError(f"relevant entry is not a mapping: {item!r}")
    source, section = normalise_source(item.get("source")), normalise_heading(item.get("section"))
    if not source or not section:
        raise ValueError(f"relevant entry needs a source and a section: {item!r}")
    return source, section


def _passage_keys(item: object) -> set[tuple[str, str]]:
    """Every (source, section) key one retrieved passage can satisfy; none if malformed."""
    if not isinstance(item, dict):
        return set()
    source = normalise_source(item.get("source"))
    return {(source, section) for section in _section_keys(item.get("section"))} if source else set()


def recall_at_k(relevant: list[dict], retrieved: list[dict], k: int) -> float | None:
    """Fraction of the relevant (source, section) pairs found in the top k retrieved.
    Return None when the question has no relevant passages (must-abstain questions).

    A relevant pair counts once however many retrieved chunks match it, so duplicates
    cannot inflate the score; matching is exact on the normalised keys, never substring."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError(f"k must be a positive integer, got {k!r}")
    wanted = {_golden_key(item) for item in relevant or []}
    if not wanted:
        return None
    if not isinstance(retrieved, list):
        raise ValueError("retrieved must be a list")
    found: set[tuple[str, str]] = set()
    for item in retrieved[:k]:                 # the retriever returns best first
        found |= _passage_keys(item)
    return len(wanted & found) / len(wanted)


def _citation_matches(citation: dict, passage: object) -> bool:
    if not isinstance(passage, dict):
        return False
    if normalise_source(citation.get("source")) != normalise_source(passage.get("source")):
        return False
    if not _section_keys(citation.get("section")) & _section_keys(passage.get("section")):
        return False
    page = citation.get("page")
    return page is None or page == passage.get("page")


def _valid_citations(citations: list, retrieved: list) -> int:
    return sum(1 for c in citations
               if isinstance(c, dict) and any(_citation_matches(c, p) for p in retrieved))


def citation_validity(response: dict) -> float | None:
    """Fraction of the response's citations that match a passage in response['retrieved'].
    Return None when there are no citations.

    A citation matches a passage retrieved for THIS request with the same source and
    section, and the same page when the citation names one. It says nothing about
    whether the passage supports the claim: that is correctness, not validity."""
    if not isinstance(response, dict):
        raise ValueError("response is not a mapping")
    citations, retrieved = response.get("citations"), response.get("retrieved")
    if not isinstance(citations, list):
        raise ValueError("response has no citations list")
    if not isinstance(retrieved, list):
        raise ValueError("response has no retrieved list")
    if not citations:
        return None
    return _valid_citations(citations, retrieved) / len(citations)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def summarise(rows: list[dict]) -> dict:
    """Overall recall@3, recall@5, citation validity and abstention correctness, plus
    recall@5 broken down by question type. This dict becomes results.json.

    Recall is a macro average: each answerable question counts once, however many
    relevant passages it lists. Must-abstain questions have no relevant passages and are
    left out of recall. Citation validity pools every citation of every response, and
    counts an answer given from context with no citation at all as one invalid citation,
    so that answering without citing cannot pass the gate. A malformed response scores
    zero recall and is listed under "malformed"; nothing missing becomes a perfect score."""
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")
    malformed: list[dict] = []
    per_question: list[dict] = []
    recall3: list[float] = []
    recall5: list[float] = []
    by_type: dict[str, dict[str, list[float]]] = {}
    cited = cited_valid = uncited_answers = 0
    abstain_total = abstain_correct = over_abstained = unscored = 0

    for row in rows:
        qid, qtype = row.get("id", "?"), str(row.get("type", "ordinary"))
        must_abstain = bool(row.get("must_abstain", False))
        relevant = row.get("relevant") or []
        response = row.get("response")
        bucket = by_type.setdefault(qtype, {"recall@3": [], "recall@5": []})
        entry: dict = {"id": qid, "type": qtype, "must_abstain": must_abstain}
        problems = []
        if not isinstance(response, dict):
            problems.append("response is not a JSON object")
            response = {}
        answered = response.get("answered_from_context")
        if not isinstance(answered, bool):
            problems.append("answered_from_context missing or not a boolean")
        retrieved = response.get("retrieved")
        if not isinstance(retrieved, list):
            problems.append("retrieved missing or not a list")
            retrieved = []
        citations = response.get("citations")
        if not isinstance(citations, list):
            problems.append("citations missing or not a list")
            citations = []

        if must_abstain:
            abstain_total += 1
            correct = answered is False
            abstain_correct += correct
            entry["abstained_correctly"] = correct
        elif relevant:
            r3 = recall_at_k(relevant, retrieved, 3)
            r5 = recall_at_k(relevant, retrieved, 5)
            r3, r5 = (0.0, 0.0) if problems else (r3 or 0.0, r5 or 0.0)
            recall3.append(r3)
            recall5.append(r5)
            bucket["recall@3"].append(r3)
            bucket["recall@5"].append(r5)
            entry.update({"recall@3": r3, "recall@5": r5})
            over_abstained += answered is False
        else:
            unscored += 1   # answerable but the golden set lists no evidence: fix the set
            problems.append("not must_abstain but relevant is empty")

        if citations:
            valid = _valid_citations(citations, retrieved)
            cited += len(citations)
            cited_valid += valid
            entry["citation_validity"] = round(valid / len(citations), 6)
        elif answered is True:
            uncited_answers += 1
            entry["citation_validity"] = None
        entry["answered_from_context"] = answered
        entry["retrieved_ids"] = [p.get("chunk_id") for p in retrieved if isinstance(p, dict)]
        if problems:
            malformed.append({"id": qid, "problems": problems})
        per_question.append(entry)

    denominator = cited + uncited_answers
    return {
        "recall@3": _mean(recall3),
        "recall@5": _mean(recall5),
        "recall@3_by_type": {t: _mean(v["recall@3"]) for t, v in sorted(by_type.items())},
        "recall@5_by_type": {t: _mean(v["recall@5"]) for t, v in sorted(by_type.items())},
        "answerable_by_type": {t: len(v["recall@5"]) for t, v in sorted(by_type.items())},
        "citation_validity": round(cited_valid / denominator, 6) if denominator else None,
        "citation_validity_cited_only": round(cited_valid / cited, 6) if cited else None,
        "abstention_correctness": round(abstain_correct / abstain_total, 6) if abstain_total else None,
        "counts": {
            "rows": len(rows), "answerable": len(recall5), "must_abstain": abstain_total,
            "abstained_correctly": abstain_correct, "unscored": unscored,
            "citations": cited, "citations_valid": cited_valid,
            "answers_without_citations": uncited_answers,
            "answerable_declined": over_abstained, "malformed": len(malformed),
        },
        "malformed": malformed,
        "per_question": per_question,
        "method": {
            "recall": "macro average over answerable questions; exact match on normalised "
                      "(source file name, section heading); top k in retrieval order",
            "citation_validity": "valid citations / (citations + answers from context with "
                                 "no citation); valid = same source and section as a passage "
                                 "retrieved for that request, and the same page if cited",
            "abstention": "must-abstain questions with answered_from_context == false",
        },
    }


def _fraction(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    return float(value)


def _points(value: float) -> float:
    """A fraction in percentage points, rounded so 0.80 - 0.75 is exactly 5.0."""
    return round(value * 100, 6)


def gate(results: dict, baseline: dict | None, *, bootstrap: bool = False) -> list[str]:
    """Return one message per failed gate (section 5.4); an empty list means pass.

    Every required metric must be present: a missing or undefined one fails, it never
    passes. Without a baseline the regression check fails unless bootstrap is True,
    which only --bootstrap-baseline sets, and then the other two gates still apply."""
    if not isinstance(results, dict):
        return ["results are not a mapping"]
    failures = []
    malformed = results.get("malformed") or []
    if malformed:
        ids = ", ".join(str(m.get("id")) for m in malformed if isinstance(m, dict))
        failures.append(f"{len(malformed)} malformed response(s), metrics are unreliable: {ids}")

    validity = _fraction(results.get("citation_validity"))
    if validity is None:
        failures.append("citation validity is undefined: no citations were made")
    elif _points(validity) < _points(MIN_CITATION_VALIDITY):
        failures.append(f"citation validity {_points(validity):.1f}% is below "
                        f"{_points(MIN_CITATION_VALIDITY):.0f}%")

    abstention = _fraction(results.get("abstention_correctness"))
    if abstention is None:
        failures.append("abstention correctness is undefined: no must-abstain questions ran")
    elif abstention < REQUIRED_ABSTENTION:
        wrong = [q.get("id") for q in results.get("per_question") or []
                 if isinstance(q, dict) and q.get("must_abstain") and not q.get("abstained_correctly")]
        failures.append(f"abstention correctness {_points(abstention):.1f}% is below 100%; "
                        f"answered from context: {', '.join(map(str, wrong)) or 'unknown'}")

    recall5 = _fraction(results.get("recall@5"))
    if recall5 is None:
        failures.append("recall@5 is undefined: no answerable question was scored")
    elif baseline is None:
        if not bootstrap:
            failures.append("no baseline: commit eval/baseline.json first; create it once, after "
                            "the live pipeline works, with --bootstrap-baseline")
    else:
        base5 = _fraction(baseline.get("recall@5")) if isinstance(baseline, dict) else None
        if base5 is None:
            failures.append("the baseline has no valid recall@5")
        else:
            drop = round(_points(base5) - _points(recall5), 6)
            if drop > _points(MAX_RECALL5_DROP):
                failures.append(f"recall@5 fell {drop:.1f} points: {_points(recall5):.1f}% "
                                f"against a baseline of {_points(base5):.1f}% "
                                f"(at most {_points(MAX_RECALL5_DROP):.0f} allowed)")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate RAHBAR against a golden set.")
    parser.add_argument("--api", required=True, help="base URL, e.g. http://192.168.49.2")
    parser.add_argument("--host", help="Host header, when your Ingress rule names a host")
    parser.add_argument("--golden", type=Path, default=HERE / "golden.yaml")
    parser.add_argument("--baseline", type=Path, default=HERE / "baseline.json")
    parser.add_argument("--out", type=Path, default=HERE / "results.json")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--update-baseline", action="store_true",
                       help="replace the existing baseline, only if every gate passes against it")
    flags.add_argument("--bootstrap-baseline", action="store_true",
                       help="write the FIRST baseline when none exists, only if citation "
                            "validity and abstention pass")
    args = parser.parse_args()

    questions = load_golden(args.golden)
    rows = []
    headers = {"Host": args.host} if args.host else {}
    try:
        with httpx.Client(timeout=120.0, headers=headers) as client:
            for q in questions:
                started = time.perf_counter()
                response = ask(client, args.api, q["question"])
                rows.append({
                    "id": q["id"], "type": q.get("type", "ordinary"),
                    "must_abstain": bool(q.get("must_abstain", False)),
                    "relevant": q.get("relevant") or [],
                    "expect": q.get("expect"),
                    "response": response,
                    "seconds": round(time.perf_counter() - started, 2),
                })
                print(f"  {q['id']:<5} {rows[-1]['seconds']:>6.2f}s  {q['question'][:60]}")
    except (httpx.HTTPError, ValueError) as exc:
        print(f"\nevaluation error: could not query the API: {exc}")
        return 2

    try:
        results = summarise(rows)
        baseline = json.loads(args.baseline.read_text(encoding="utf-8")) \
            if args.baseline.exists() else None
    except (ValueError, OSError) as exc:      # json.JSONDecodeError is a ValueError
        print(f"\nevaluation error: {exc}")
        return 2
    if args.bootstrap_baseline and baseline is not None:
        print(f"\nevaluation error: {args.baseline} already exists; use --update-baseline")
        return 2
    if args.update_baseline and baseline is None:
        print(f"\nevaluation error: no baseline at {args.baseline}; use --bootstrap-baseline once")
        return 2
    failures = gate(results, baseline, bootstrap=args.bootstrap_baseline)

    results["golden"] = str(args.golden)
    results["questions"] = len(rows)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nresults written to {args.out}")
    for key in ("recall@3", "recall@5", "citation_validity", "abstention_correctness"):
        print(f"  {key:<24} {results[key]}")
    if args.update_baseline or args.bootstrap_baseline:
        if failures:
            print("baseline NOT written: the gate failed, and a baseline is only ever "
                  "recorded from a passing run")
        else:
            args.baseline.write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(f"baseline written: {args.baseline}")

    if failures:
        print("\nGATE FAILED")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\ngate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
