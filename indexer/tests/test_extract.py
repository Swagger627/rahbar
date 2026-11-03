"""Extraction tests. Tiny PDFs are built in memory with pypdf, and OCR is replaced by a
fake, so these run without tesseract or poppler (those exist only in the indexer image)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pipeline import extract as ex


def write_pdf(path: Path, page_texts: list[str]) -> Path:
    """One PDF page per string; an empty string makes a page with no text layer (like a scan)."""
    writer = PdfWriter()
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        if not text:
            continue
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def fake_ocr(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []

    def ocr(file: Path, number: int) -> str:
        calls.append(number)
        return f"OCR text of page {number}"

    monkeypatch.setattr(ex, "_ocr_page", ocr)
    return calls


def test_text_pages_are_read_directly_and_blank_pages_go_to_ocr(tmp_path: Path,
                                                               fake_ocr: list[int]) -> None:
    pdf = write_pdf(tmp_path / "mixed.pdf", ["Attendance Requirements apply to every course", ""])
    doc = ex.extract(str(pdf), "2026", False)
    assert [p.page for p in doc.pages] == [1, 2]
    assert "Attendance Requirements" in doc.pages[0].text and not doc.pages[0].ocr
    assert doc.pages[1].text == "OCR text of page 2" and doc.pages[1].ocr
    assert fake_ocr == [2]                      # only the page without text was OCR'd


def test_document_metadata_is_kept(tmp_path: Path, fake_ocr: list[int]) -> None:
    doc = ex.extract(str(write_pdf(tmp_path / "rules.pdf", ["Some rules text here"])),
                     "2023", True)
    assert (doc.source, doc.doc_version, doc.superseded) == ("rules.pdf", "2023", True)
    assert all(p.source == "rules.pdf" for p in doc.pages)


def test_markdown_front_matter_is_dropped(tmp_path: Path) -> None:
    md = tmp_path / "fixture.md"
    md.write_text("---\ndoc_version: 2026\nsuperseded: false\n---\n# Grade Points\nA is 4.0\n",
                  encoding="utf-8")
    doc = ex.extract(str(md), "2026", False)
    assert doc.pages[0].text == "# Grade Points\nA is 4.0"


def test_unsupported_file_type_is_rejected(tmp_path: Path) -> None:
    other = tmp_path / "notes.docx"
    other.write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported file type"):
        ex.extract(str(other), "1", False)


def test_broken_pdf_names_the_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf")
    with pytest.raises(ValueError, match="broken.pdf: could not extract text"):
        ex.extract(str(broken), "1", False)
