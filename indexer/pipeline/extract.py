"""Stage 1: Extract. PDF (or Markdown / plain text) to text, page by page."""

from __future__ import annotations

from pathlib import Path

from .models import Document, Page

# A page with fewer letters than this has no usable text layer (a scan, or text drawn
# as pictures), so it is read with OCR instead.
MIN_LETTERS_FOR_TEXT_LAYER = 20
OCR_DPI = 300   # tesseract reads small print well at 300 dots per inch
# Page segmentation mode 4 reads the page as one column of lines. The default (3) treated
# the scanned policy's clause numbers ("4.2.") as a separate column and lost them.
OCR_CONFIG = "--psm 4"


def extract(path: str, doc_version: str, superseded: bool) -> Document:
    """Return the document's text page by page.

    Detect pages with no text layer and OCR them; mark those pages ocr=True.
    The OCR libraries need system packages in your Dockerfile (tesseract-ocr,
    poppler-utils), not only pip packages.
    """
    file = Path(path)
    doc = Document(source=file.name, doc_version=doc_version, superseded=superseded)
    suffix = file.suffix.lower()
    if suffix == ".pdf":
        doc.pages = _extract_pdf(file)
    elif suffix in (".md", ".txt"):
        doc.pages = [Page(source=file.name, page=1, text=_read_text_file(file))]
    else:
        raise ValueError(f"{file.name}: unsupported file type {suffix!r} (expected .pdf, .md or .txt)")
    return doc


def _extract_pdf(file: Path) -> list[Page]:
    import pdfplumber

    pages = []
    try:
        with pdfplumber.open(file) as pdf:
            for number, pdf_page in enumerate(pdf.pages, start=1):
                # Keep only text inside the visible page: some InDesign PDFs also carry
                # the neighbouring page's text just outside it. use_text_flow follows the
                # PDF's own reading order, so two-column pages are read column by
                # column instead of mixing both columns on one line.
                visible = pdf_page.within_bbox(pdf_page.bbox)
                text = visible.extract_text(use_text_flow=True) or ""
                if _letters(text) >= MIN_LETTERS_FOR_TEXT_LAYER:
                    pages.append(Page(source=file.name, page=number, text=text))
                else:
                    pages.append(Page(source=file.name, page=number, text=_ocr_page(file, number),
                                      ocr=True))
    except Exception as exc:   # a damaged or encrypted PDF: say which file, keep the cause
        raise ValueError(f"{file.name}: could not extract text: {exc}") from exc
    return pages


def _ocr_page(file: Path, number: int) -> str:
    """Render one page to an image and read it with tesseract. One page at a time keeps
    memory low; the image goes to /tmp, the only writable path in the container."""
    import pytesseract
    from pdf2image import convert_from_path

    images = convert_from_path(str(file), dpi=OCR_DPI, first_page=number, last_page=number)
    return pytesseract.image_to_string(images[0], lang="eng", config=OCR_CONFIG) if images else ""


def _read_text_file(file: Path) -> str:
    """Markdown or text, without the `---` front matter the CI fixtures start with."""
    lines = file.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), 0)
        lines = lines[end + 1:]
    return "\n".join(lines)


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)
