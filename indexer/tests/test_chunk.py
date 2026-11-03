"""Chunking tests: sections, paragraphs, size and overlap (in words), pages, ids, metadata."""

from __future__ import annotations

import pytest

from pipeline.chunk import DEFAULT_SECTION, chunk
from pipeline.models import Document, Page


def make_doc(page_texts: list[str], source: str = "rules.pdf") -> Document:
    return Document(source=source, doc_version="2023", superseded=True,
                    pages=[Page(source=source, page=i, text=t)
                           for i, t in enumerate(page_texts, start=1)])


def body(text: str) -> list[str]:
    """The chunk's words without the heading line at the top."""
    return text.split("\n", 1)[1].split()


def test_each_heading_starts_a_new_section() -> None:
    doc = make_doc(["5. Academic Assessment\nIntro text.\nRepeating a Course\n"
                    "5.8 A failed course must be repeated.\n5.9 A course may be repeated."])
    chunks = chunk(doc, size=400, overlap=50)
    assert [c.section for c in chunks] == ["5. Academic Assessment", "Repeating a Course"]
    assert chunks[1].text == ("Repeating a Course\n5.8 A failed course must be repeated. "
                              "5.9 A course may be repeated.")


def test_all_caps_and_markdown_headings() -> None:
    doc = make_doc(["# Grade Points\nA is 4.0\nCODE OF CONDUCT\nBe kind to others"])
    assert [c.section for c in chunk(doc, 400, 50)] == ["Grade Points", "CODE OF CONDUCT"]


def test_ordinary_lines_and_clauses_are_not_headings() -> None:
    doc = make_doc(["Attendance Requirements\n4.1 Students must attend 80% of classes.\n"
                    "This rule applies to all students.\nFees are listed below:"])
    chunks = chunk(doc, 400, 50)
    assert [c.section for c in chunks] == ["Attendance Requirements"]


def test_clause_numbers_with_a_trailing_dot_are_clauses_not_headings() -> None:
    doc = make_doc(["4. Eligibility criteria for a technical start-up\n"
                    "4.1. Faculty member must be full-time.\n4.2. More than 1 year of service."])
    chunks = chunk(doc, 400, 50)
    assert [c.section for c in chunks] == ["4. Eligibility criteria for a technical start-up"]
    assert "4.1. Faculty member must be full-time. 4.2. More than" in chunks[0].text


def test_text_before_the_first_heading_gets_a_default_section() -> None:
    assert chunk(make_doc(["just some text"]), 400, 50)[0].section == DEFAULT_SECTION


def test_section_continues_across_pages_and_page_is_where_text_starts() -> None:
    first = "# Grade Points\n" + " ".join(f"a{i}" for i in range(30))
    second = " ".join(f"b{i}" for i in range(30))
    chunks = chunk(make_doc([first, second]), size=40, overlap=5)
    assert [c.section for c in chunks] == ["Grade Points", "Grade Points"]
    assert [c.page for c in chunks] == [1, 2]
    assert body(chunks[1].text)[:5] == [f"a{i}" for i in range(25, 30)]   # the overlap


def test_size_and_overlap_are_respected() -> None:
    paragraphs = "\n\n".join(" ".join(f"w{p}x{i}" for i in range(30)) for p in range(10))
    chunks = chunk(make_doc(["Grade Points\n" + paragraphs]), size=100, overlap=10)
    assert len(chunks) > 1
    assert all(len(body(c.text)) <= 100 for c in chunks)
    for before, after in zip(chunks, chunks[1:]):
        assert body(after.text)[:10] == body(before.text)[-10:]


def test_overlap_never_crosses_sections() -> None:
    doc = make_doc(["# Grade Points\nA is 4.0 and B is 3.0\nRepeating a Course\n5.8 Repeat it."])
    second = chunk(doc, size=400, overlap=50)[1]
    assert "4.0" not in second.text


def test_oversized_paragraph_is_split_by_sentences_then_words() -> None:
    sentences = " ".join(f"Sentence {i} has exactly six words." for i in range(20))
    one_long_sentence = " ".join(f"w{i}" for i in range(250))
    chunks = chunk(make_doc([f"# Grade Points\n{sentences}\n\n{one_long_sentence}"]), 50, 0)
    assert all(len(body(c.text)) <= 50 for c in chunks)
    assert sum(len(body(c.text)) for c in chunks) == 120 + 250      # nothing lost, nothing doubled


def test_ids_are_stable_and_metadata_is_copied() -> None:
    doc = make_doc(["# Grade Points\nA is 4.0\nCODE OF CONDUCT\nBe kind"], source="handbook-2023.pdf")
    first, again = chunk(doc, 400, 50), chunk(doc, 400, 50)
    assert [c.id for c in first] == ["handbook-2023-0000", "handbook-2023-0001"]
    assert [c.id for c in first] == [c.id for c in again]
    assert first[0].metadata() == {"source": "handbook-2023.pdf", "section": "Grade Points",
                                   "page": 1, "doc_version": "2023", "superseded": True}


def test_empty_pages_make_no_chunks() -> None:
    assert chunk(make_doc(["", "   \n  "]), 400, 50) == []
    chunks = chunk(make_doc(["", "Grade Points\nA is 4.0"]), 400, 50)
    assert [c.page for c in chunks] == [2]


def test_two_documents_never_share_a_chunk() -> None:
    a = chunk(make_doc(["Grade Points\nfrom document a"], source="a.pdf"), 400, 50)
    b = chunk(make_doc(["Grade Points\nfrom document b"], source="b.pdf"), 400, 50)
    assert all(c.source == "a.pdf" and "document b" not in c.text for c in a)
    assert all(c.source == "b.pdf" and "document a" not in c.text for c in b)


@pytest.mark.parametrize("size, overlap", [(0, 0), (100, 100), (100, -1)])
def test_bad_size_or_overlap_is_rejected(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk(make_doc(["text"]), size, overlap)
