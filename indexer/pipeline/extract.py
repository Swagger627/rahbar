"""Stage 1: Extract. PDF (or Markdown / plain text) to text, page by page."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import Document, Page

# A page with fewer letters than this has no usable text layer (a scan, or text drawn
# as pictures), so it is read with OCR instead.
MIN_LETTERS_FOR_TEXT_LAYER = 20
OCR_DPI = 300   # tesseract reads small print well at 300 dots per inch
# Page segmentation mode 4 reads the page as one column of lines. The default (3) treated
# the scanned policy's clause numbers ("4.2.") as a separate column and lost them.
OCR_CONFIG = "--psm 4"
TICKS = "√✓✔"
TABLE_ROW = "Table row: "   # chunk.py makes every line starting with this its own chunk
TICK_COLUMN_WIDTH = 20   # points: how far a tick may sit from the centre of its heading


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
                text = _name_ticks(visible, visible.extract_text(use_text_flow=True) or "")
                if _letters(text) >= MIN_LETTERS_FOR_TEXT_LAYER:
                    pages.append(Page(source=file.name, page=number, text=text))
                else:
                    pages.append(Page(source=file.name, page=number, text=_ocr_page(file, number),
                                      ocr=True))
    except Exception as exc:   # a damaged or encrypted PDF: say which file, keep the cause
        raise ValueError(f"{file.name}: could not extract text: {exc}") from exc
    return pages


def _name_ticks(page: Any, text: str) -> str:
    """Tick-mark tables ("√" under column headings such as CFD ISB KHI LHR PWR) lose their
    columns in plain text: "5 Bachelor of Science (Civil Engineering) √" no longer says
    which campus. Each tick is replaced by the heading printed straight above it, and each
    table row becomes one self-contained line that the chunker keeps as its own chunk:

        Table row: 5 Bachelor of Science (Civil Engineering) | marked under: LHR
        (columns: CFD ISB KHI LHR PWR)          <- on the same line in the output

    A tick with no heading within TICK_COLUMN_WIDTH points above it stays a tick, and an
    empty cell is simply not listed; nothing is guessed."""
    ticks = [c for c in page.chars if c["text"] in TICKS]
    if not ticks:
        return text
    first_tick = min(c["top"] for c in ticks)
    headings = [w for w in page.extract_words() if w["bottom"] <= first_tick and w["text"].isupper()]

    def heading_above(tick: dict[str, Any]) -> dict[str, Any] | None:
        x = (tick["x0"] + tick["x1"]) / 2
        above = [w for w in headings if abs((w["x0"] + w["x1"]) / 2 - x) <= TICK_COLUMN_WIDTH]
        return max(above, key=lambda w: w["bottom"]) if above else None

    # The column headings: every heading word on the same printed line as the ones above
    # the ticks (so a column that nobody ticked is still listed).
    used = [h for h in map(heading_above, ticks) if h is not None]
    header = [w for w in headings if used and abs(w["bottom"] - used[0]["bottom"]) <= 2]
    columns = " ".join(w["text"] for w in sorted(header, key=lambda w: w["x0"]))
    # The column names for each line that has ticks, in the same reading order as `text`.
    rows = [[(heading_above(c) or {"text": c["text"]})["text"]
             for c in line["chars"] if c["text"] in TICKS]
            for line in page.extract_text_lines(use_text_flow=True, return_chars=True)
            if any(c["text"] in TICKS for c in line["chars"])]
    lines: list[str] = []
    pending: list[str] = []          # lines since the last table row
    for line in text.splitlines():
        if not (rows and any(t in line for t in TICKS)):
            pending.append(line)
            continue
        names = " ".join(rows.pop(0))
        label = "".join(ch for ch in line if ch not in TICKS).strip()
        if not label:
            # The row's name wrapped onto the lines above ("6 Master of Science (Computer
            # Networks &" / "Security)"): take the lines back to the one with the row number.
            start = max((i for i, p in enumerate(pending) if re.match(r"\d+\s", p)), default=None)
            if start is not None:
                label = " ".join(p.strip() for p in pending[start:])
                pending = pending[:start]
        lines += pending
        pending = []
        lines.append(f"{TABLE_ROW}{label} | marked under: {names} (columns: {columns})")
    return "\n".join(lines + pending)


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
