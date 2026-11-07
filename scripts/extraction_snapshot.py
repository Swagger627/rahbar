"""Snapshot of the REAL extract -> clean -> chunk stages over the real corpus: every page
(hash, OCR flag) and every chunk (id, heading, text, metadata) as JSON. Nothing is
embedded and nothing is written to Chroma.

Use it before and after upgrading an extraction library, with the old and the new indexer
image, then compare the two files with scripts/compare_snapshots.py (docs/CI-CD.md,
"Security fixes"). PowerShell, from the repository root:

  docker run --rm --network none --read-only --tmpfs /tmp -e PYTHONPATH=/app `
    -v "${PWD}\\corpus\\raw:/corpus:ro" -v "${PWD}\\scripts:/work" -v "$env:TEMP:/out" `
    --entrypoint python rahbar-indexer:dev /work/extraction_snapshot.py /out/before.json
"""

import hashlib
import json
import sys
from importlib.metadata import version

from pipeline.chunk import chunk
from pipeline.clean import clean
from pipeline.config import get_settings
from pipeline.extract import extract
from pipeline.run import discover, verify_corpus

settings = get_settings()
checked = verify_corpus(settings.corpus_dir)          # raises on any SHA-256 mismatch
out = {
    "libraries": {p: version(p) for p in ("pdfplumber", "pdfminer.six", "pypdfium2",
                                          "pytesseract", "pdf2image", "pillow")},
    "verified_files": checked,
    "documents": [],
}
for path, doc_version, superseded in discover(settings.corpus_dir):
    doc = clean(extract(str(path), doc_version, superseded))
    pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)
    out["documents"].append({
        "source": path.name,
        "doc_version": doc_version,
        "superseded": superseded,
        "pages": [{"page": p.page, "ocr": p.ocr, "chars": len(p.text),
                   "sha256": hashlib.sha256(p.text.encode()).hexdigest()} for p in doc.pages],
        "chunks": [{"id": c.id, "section": c.section, "page": c.page, "text": c.text,
                    **c.metadata()} for c in pieces],
    })
    print(path.name, len(doc.pages), "pages", len(pieces), "chunks", flush=True)
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("total chunks", sum(len(d["chunks"]) for d in out["documents"]))
