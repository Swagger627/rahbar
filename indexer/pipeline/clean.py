"""Stage 2: Clean. Remove artefacts without destroying structure."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .models import Document, Page

# A line that comes back on at least this share of a document's pages (and on at least
# MIN_PAGES_REPEATED pages) is a running header or footer, e.g. "Student Handbook 2026 5"
# or "NATIONAL UNIVERSITY OF COMPUTER AND EMERGING SCIENCES".
REPEATED_SHARE = 0.25
MIN_PAGES_REPEATED = 3

PAGE_NUMBER = re.compile(r"^(page\s+)?\d+(\s+of\s+\d+)?$", re.IGNORECASE)   # "18", "Page 11 of 85"
LETTER_SPACED = re.compile(r"\b(?:[A-Za-z] ){3,}[A-Za-z]\b")                 # "t u t o r i a l s"


def clean(doc: Document) -> Document:
    """Strip headers and footers that repeat on every page; keep headings and
    section boundaries, because chunking and citations depend on them."""
    pages = [[_tidy(line) for line in page.text.splitlines()] for page in doc.pages]
    repeated = _repeated_lines(pages)
    cleaned = []
    for page, lines in zip(doc.pages, pages):
        kept = [line for line in lines
                if not PAGE_NUMBER.match(line) and _key(line) not in repeated]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
        cleaned.append(Page(source=page.source, page=page.page, text=text, ocr=page.ocr))
    return Document(source=doc.source, doc_version=doc.doc_version,
                    superseded=doc.superseded, pages=cleaned)


def _tidy(line: str) -> str:
    """Normalise one line: Unicode compatibility forms (the "ﬁ" ligature becomes "fi"),
    one space between words, letter-spaced words joined again."""
    line = unicodedata.normalize("NFKC", line)
    line = re.sub(r"[ \t ]+", " ", line).strip()
    return LETTER_SPACED.sub(lambda m: m.group(0).replace(" ", ""), line)


def _key(line: str) -> str:
    """Compare lines ignoring numbers, spaces and case, so "Student Handbook 2026 5" and
    "Student Handbook 2026 6" count as the same header."""
    return re.sub(r"\s+", "", re.sub(r"\d+", "#", line)).lower()


def _repeated_lines(pages: list[list[str]]) -> set[str]:
    counts = Counter(key for lines in pages for key in {_key(line) for line in lines if line})
    needed = max(MIN_PAGES_REPEATED, REPEATED_SHARE * len(pages))
    return {key for key, count in counts.items() if count >= needed}
