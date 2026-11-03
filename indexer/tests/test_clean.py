"""Cleaning tests: running headers, footers and page numbers go; headings and text stay."""

from __future__ import annotations

from pipeline.clean import clean
from pipeline.models import Document, Page


def make_doc(page_texts: list[str]) -> Document:
    return Document(source="handbook.pdf", doc_version="2026", superseded=False,
                    pages=[Page(source="handbook.pdf", page=i, text=t, ocr=(i == 2))
                           for i, t in enumerate(page_texts, start=1)])


def test_repeated_header_and_page_numbers_are_removed() -> None:
    rules = ["Fees are due", "Classes start", "Exams end", "Grades appear", "Results publish"]
    pages = [f"Student Handbook 2026 {n}\n{rule}\n{n}" for n, rule in enumerate(rules, start=1)]
    cleaned = clean(make_doc(pages))
    assert [p.text for p in cleaned.pages] == rules


def test_page_x_of_y_footer_is_removed() -> None:
    cleaned = clean(make_doc(["Text one\nPage 1 of 2", "Text two\nPage 2 of 2"]))
    assert [p.text for p in cleaned.pages] == ["Text one", "Text two"]


def test_heading_seen_on_few_pages_is_kept() -> None:
    pages = ["CODE OF CONDUCT\nBe respectful"] + [f"Other text {n}" for n in range(9)]
    assert clean(make_doc(pages)).pages[0].text == "CODE OF CONDUCT\nBe respectful"


def test_spaces_ligatures_and_letter_spacing_are_tidied() -> None:
    text = "Use  of   AI\ttools\nThe ﬁnal grade\nt u t o r i a l s are optional"
    cleaned = clean(make_doc([text])).pages[0].text
    assert cleaned == "Use of AI tools\nThe final grade\ntutorials are optional"


def test_blank_lines_are_collapsed_but_paragraphs_kept() -> None:
    cleaned = clean(make_doc(["First paragraph\n\n\n\n\nSecond paragraph"])).pages[0].text
    assert cleaned == "First paragraph\n\nSecond paragraph"


def test_metadata_and_ocr_flag_survive() -> None:
    cleaned = clean(make_doc(["a page", "a scanned page"]))
    assert (cleaned.source, cleaned.doc_version, cleaned.superseded) == ("handbook.pdf", "2026", False)
    assert [p.ocr for p in cleaned.pages] == [False, True]
    assert [p.page for p in cleaned.pages] == [1, 2]
