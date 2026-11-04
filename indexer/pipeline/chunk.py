"""Stage 3: Chunk. Structure-aware, with overlap.

Size and overlap are counted in tokens, and a token here is one whitespace-separated
word (`text.split()`). These are NOT the embedding model's subword tokens, which run
somewhat higher (a rule of thumb is about 1.3 per English word). Whitespace tokens are
easy to count and to explain, and need no tokenizer library.
"""

from __future__ import annotations

import re
from pathlib import Path

from .extract import TABLE_ROW
from .models import Chunk, Document

DEFAULT_SECTION = "Untitled section"   # text that appears before the first heading

CLAUSE = re.compile(r"^\d+(\.\d+)+\.?\s")                     # "5.4 Minimum ...", "1.2.1. founding"
LIST_ITEM = re.compile(r"^(\d{1,2}[.)]|[a-z][.)]|[ivx]{1,4}[.)])\s")  # "21. ...", "a. ...", "iv) ..."
NUMBERED_TITLE = re.compile(r"^(\d{1,2}|[A-Z]|[IVXLC]{1,5})\.\s+[A-Z]")  # "5. Academic Assessment"
CHAPTER = re.compile(r"^Chapter[- ]?\d+\b")                          # "Chapter-1 Programs Offered ..."
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}


def chunk(doc: Document, size: int, overlap: int) -> list[Chunk]:
    """Split on sections, then paragraphs, then sentences, keeping each chunk's body to
    at most `size` tokens (whitespace words), with `overlap` tokens shared between
    neighbours in the same section. Every chunk keeps its section heading, page,
    doc_version and superseded."""
    if size < 1 or not 0 <= overlap < size:
        raise ValueError(f"need size >= 1 and 0 <= overlap < size, got {size}, {overlap}")
    chunks: list[Chunk] = []
    for section, paragraphs in _sections(doc):
        for page, words in _pack(paragraphs, size, overlap):
            chunks.append(Chunk(
                id=f"{Path(doc.source).stem}-{len(chunks):04d}",
                # The heading is repeated at the top of every chunk, so a chunk still says
                # what it is about when it is retrieved on its own.
                text=f"{section}\n{' '.join(words)}",
                source=doc.source, section=section, page=page,
                doc_version=doc.doc_version, superseded=doc.superseded,
            ))
    return chunks


def _sections(doc: Document) -> list[tuple[str, list[tuple[int, str]]]]:
    """Walk the pages in order and group paragraphs under the heading they follow.
    A section can continue across pages; each paragraph remembers its own page."""
    sections: list[tuple[str, list[tuple[int, str]]]] = []
    current: list[tuple[int, str]] = []
    heading = DEFAULT_SECTION
    paragraph: list[str] = []
    paragraph_page = 0

    def end_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            current.append((paragraph_page, " ".join(paragraph)))
            paragraph = []

    for page in doc.pages:
        lines = [line.strip() for line in page.text.splitlines()]
        for i, line in enumerate(lines):
            next_line = next((later for later in lines[i + 1:] if later), "")
            if not line:
                end_paragraph()
            elif line.startswith(TABLE_ROW):
                # One table row = one chunk, under the current heading, on its own page.
                end_paragraph()
                if current:
                    sections.append((heading, current))
                    current = []
                sections.append((heading, [(page.page, line)]))
            elif _is_heading(line, next_line):
                end_paragraph()
                if current:
                    sections.append((heading, current))
                heading, current = line.lstrip("#").strip(), []
            else:
                if CLAUSE.match(line) or LIST_ITEM.match(line):
                    end_paragraph()
                if not paragraph:
                    paragraph_page = page.page
                paragraph.append(line)
        end_paragraph()   # a paragraph stops at the page end, so its page number is exact
    if current:
        sections.append((heading, current))
    return sections


def _is_heading(line: str, next_line: str) -> bool:
    if line.startswith("#"):
        return True
    words = line.split()
    if len(words) > 10 or len(line) > 80 or line[-1] in ".,;:" or "..." in line:
        return False                                  # "..." is a table-of-contents line
    if NUMBERED_TITLE.match(line) and not CLAUSE.match(line):
        return True
    if CHAPTER.match(line):
        return True
    if any(ch.isdigit() for ch in line):
        return False
    letters = [ch for ch in line if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        # "CODE OF CONDUCT" is a heading; a row of short codes such as the campus header
        # "CFD ISB KHI LHR PWR" is a table header, not a heading.
        return len(words) >= 2 and any(len(w) > 3 for w in words)
    title_case = all(w[0].isupper() or w.lower() in SMALL_WORDS for w in words if w[0].isalpha())
    return title_case and bool(CLAUSE.match(next_line))   # "Repeating a Course" + "5.8 ..."


def _pack(paragraphs: list[tuple[int, str]], size: int, overlap: int) -> list[tuple[int, list[str]]]:
    """Fill chunks paragraph by paragraph up to `size` tokens. A new chunk starts with the
    last `overlap` tokens of the previous one; its page is where its new text starts."""
    pieces = [(page, part) for page, text in paragraphs for part in _split_long(text, size)]
    chunks: list[tuple[int, list[str]]] = []
    words: list[str] = []
    page = 0
    for piece_page, piece in pieces:
        piece_words = piece.split()
        if words and len(words) + len(piece_words) > size:
            chunks.append((page, words))
            keep = min(overlap, size - len(piece_words))   # never let the overlap push past size
            words = words[-keep:] if keep > 0 else []
            page = piece_page
        if not words:
            page = piece_page
        words = words + piece_words
    if words:
        chunks.append((page, words))
    return chunks


def _split_long(text: str, size: int) -> list[str]:
    """A paragraph longer than `size` tokens is split at sentence ends; a sentence that is
    still too long is cut every `size` tokens."""
    if len(text.split()) <= size:
        return [text]
    parts: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = sentence.split()
        parts.extend(" ".join(words[i:i + size]) for i in range(0, len(words), size))
    return parts
