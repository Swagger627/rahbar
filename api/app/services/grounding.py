"""Grounding rules around the model (section 3.4): the relevance floor, the citation
check, and the canned answers used when the API must not answer."""

from __future__ import annotations

import re

from ..retrieval.chroma import clause_refs, mentions_clause
from ..schemas import Answer, Citation, Passage

# The corpus does not cover the question (honest abstention).
NOT_COVERED = ("The documents I have do not cover this question, so I cannot answer it. "
               "Please check with the university office.")
# Something went wrong (model down, or no valid grounded reply after one retry). Still an
# honest refusal: nothing is invented.
FALLBACK = ("I could not produce an answer I can back with the documents right now. "
            "Please try again later.")


def refusal(text: str, reason: str) -> Answer:
    """An answer that states nothing and cites nothing."""
    return Answer(reasoning=reason, answer=text, citations=[], answered_from_context=False,
                  confidence="low")


# Text that talks to an AI instead of stating a rule. University rulebooks never say this; a
# poisoned document does. The real llama3.2:3b obeyed such a passage 5 times out of 5 in
# our test, despite the prompt's rules, so these passages never reach the model.
INSTRUCTION_LIKE = re.compile(
    r"ignore (all |any )?(the |your )?(previous|prior|above|earlier|other) "
    r"(instructions|rules|documents)"
    r"|disregard (all |any )?(the |your )?(previous|prior|above|earlier) "
    r"|(system|developer) (instruction|prompt|message)"
    r"|instructions? to the (ai|assistant|model)"
    r"|you are (now )?an? (ai|assistant|language model)",
    re.IGNORECASE)


def above_floor(passages: list[Passage], floor: float, question: str = "") -> list[Passage]:
    """The passages relevant enough to show the model: score >= floor, or the passage
    contains a clause the question names ("clause 1.21"). Exact text is stronger evidence
    than vector similarity, which blurs numbers (the real clause 1.21 chunk scored 0.643)."""
    asked = clause_refs(question)
    return [p for p in passages
            if p.score >= floor or any(mentions_clause(p.text, n) for n in asked)]


def without_instructions(passages: list[Passage]) -> tuple[list[Passage], list[str]]:
    """Split off passages whose text gives instructions to an AI. Returns (kept, dropped
    chunk ids). This only catches obvious injections; a poisoned document that states a
    false rule plainly is stopped by who may write the index, not here."""
    kept = [p for p in passages if not INSTRUCTION_LIKE.search(p.text)]
    dropped = [p.chunk_id for p in passages if INSTRUCTION_LIKE.search(p.text)]
    return kept, dropped


def check_answer(answer: Answer, passages: list[Passage]) -> tuple[Answer, str | None]:
    """Return (answer, None) when the answer may be shown, or (answer, problem) when it
    must be rejected.

    - A model that says the passages do not answer the question gets the standard
      NOT_COVERED wording and no citations.
    - An answer given from context must have text and at least one citation, and every
      citation must name a passage given to the model (same source, section and page).
      Matching ignores case, spacing and quote style; the citation is then returned with
      the passage's exact metadata. Nothing is ever added to make the check pass.

    This proves the citations are real, not that every sentence is supported by them.
    """
    if not answer.answered_from_context:
        return refusal(NOT_COVERED, "the model found no answer in the passages"), None
    if not answer.answer.strip():
        return answer, "answered_from_context is true but the answer is empty"
    if not answer.citations:
        return answer, "answered_from_context is true but there are no citations"
    by_key = {_key(p.source, p.section, p.page): p for p in passages}
    checked: list[Citation] = []
    for citation in answer.citations:
        passage = by_key.get(_key(citation.source, citation.section, citation.page))
        if passage is None:
            return answer, (f"citation {citation.source} / {citation.section} / page "
                            f"{citation.page} is not one of the passages provided")
        exact = Citation(source=passage.source, section=passage.section, page=passage.page)
        if exact not in checked:
            checked.append(exact)
    return answer.model_copy(update={"citations": checked}), None


def _key(source: str, section: str, page: int | None) -> tuple[str, str, int | None]:
    def tidy(text: str) -> str:
        text = text.replace("’", "'").replace("‘", "'")
        return re.sub(r"\s+", " ", text).strip().casefold()

    return tidy(source), tidy(section), page
