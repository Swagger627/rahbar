"""The indexer Job's entry point. It runs once and exits (section 4.2).

    python -m pipeline.run --dummy    # week 1: three fake chunks, no model needed
    python -m pipeline.run            # the real pipeline: extract, clean, chunk, embed, store
    python -m pipeline.run --dry-run  # extract, clean, chunk only; print counts, write nothing
    python -m pipeline.run --verify-corpus   # only check the PDFs against their SHA-256

Exit code 0 means the index was written; anything else fails the Job, and
Kubernetes retries it up to the Job's backoffLimit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from .config import IndexerSettings, get_settings
from .models import Chunk


def log(msg: str, **fields: object) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "logger": "rahbar.indexer", "msg": msg, **fields}), flush=True)


def _front_matter(path: Path) -> dict[str, str]:
    """Read `key: value` lines between leading '---' markers (used by eval/fixtures)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def discover(corpus_dir: str) -> list[tuple[Path, str, bool]]:
    """List (path, doc_version, superseded) for every document to index.

    corpus/fetch.py writes _index.json next to the files it downloads; use it when
    present. Otherwise read Markdown front matter (the CI fixtures work this way).
    """
    root = Path(corpus_dir)
    index = root / "_index.json"
    if index.exists():
        entries = json.loads(index.read_text(encoding="utf-8"))
        return [(root / e["file"], str(e.get("doc_version", "unknown")),
                 bool(e.get("superseded", False))) for e in entries]
    found = []
    for path in sorted(root.glob("*")):
        if path.suffix.lower() not in {".pdf", ".md", ".html", ".txt"}:
            continue
        if path.name.lower().startswith("readme"):
            continue
        meta = _front_matter(path) if path.suffix.lower() == ".md" else {}
        found.append((path, meta.get("doc_version", "unknown"),
                      meta.get("superseded", "false").lower() == "true"))
    return found


def verify_corpus(corpus_dir: str) -> int:
    """Check every file listed in _index.json against the SHA-256 recorded when it was
    downloaded (corpus/fetch.py). A missing or changed file stops the run before anything
    touches Chroma. Returns the number of files checked; 0 when there is no _index.json
    (the CI fixtures)."""
    root = Path(corpus_dir)
    index = root / "_index.json"
    if not index.exists():
        return 0
    entries = json.loads(index.read_text(encoding="utf-8"))
    for entry in entries:
        path = root / entry["file"]
        if not path.exists():
            raise RuntimeError(f"corpus file missing: {entry['file']}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if entry.get("sha256") and digest != entry["sha256"]:
            raise RuntimeError(f"corpus file changed: {entry['file']} has SHA-256 {digest}, "
                               f"expected {entry['sha256']}")
    log("corpus verified", files=len(entries))
    return len(entries)


def run_pipeline(settings: IndexerSettings) -> int:
    from .chunk import chunk
    from .clean import clean
    from .embed import embed
    from .extract import extract
    from .store import store

    verify_corpus(settings.corpus_dir)
    documents = discover(settings.corpus_dir)
    if not documents:
        raise SystemExit(f"no documents found in {settings.corpus_dir}")
    all_chunks: list[Chunk] = []
    for path, doc_version, superseded in documents:
        doc = clean(extract(str(path), doc_version, superseded))
        pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)
        log("chunked", source=path.name, chunks=len(pieces))
        all_chunks.extend(pieces)
    vectors = embed([c.text for c in all_chunks], settings)
    log("embedded", chunks=len(vectors), model=settings.embed_model,
        dimension=len(vectors[0]) if vectors else 0)
    return store(all_chunks, vectors, settings)


def dry_run(settings: IndexerSettings) -> int:
    """Extract, clean and chunk every document, log what came out, and stop before the
    embed and store stages: nothing is written to Chroma. Sizes are in tokens
    (whitespace words)."""
    from .chunk import chunk
    from .clean import clean
    from .extract import extract

    documents = discover(settings.corpus_dir)
    if not documents:
        raise SystemExit(f"no documents found in {settings.corpus_dir}")
    total_chunks = total_doc_tokens = total_chunk_tokens = 0
    for path, doc_version, superseded in documents:
        doc = clean(extract(str(path), doc_version, superseded))
        pieces = chunk(doc, settings.chunk_size, settings.chunk_overlap)
        doc_tokens = sum(len(page.text.split()) for page in doc.pages)
        chunk_tokens = sum(len(c.text.split()) for c in pieces)
        log("dry-run document", source=path.name, pages=len(doc.pages),
            ocr_pages=sum(page.ocr for page in doc.pages), tokens=doc_tokens, chunks=len(pieces),
            chunk_tokens=chunk_tokens, sections=len({c.section for c in pieces}))
        total_chunks += len(pieces)
        total_doc_tokens += doc_tokens
        total_chunk_tokens += chunk_tokens
    # Amplification: stored tokens divided by original (cleaned) tokens. Above 1
    # because of the overlap and the heading repeated at the top of every chunk.
    log("dry-run total", documents=len(documents), chunks=total_chunks, tokens=total_doc_tokens,
        chunk_tokens=total_chunk_tokens,
        amplification=round(total_chunk_tokens / total_doc_tokens, 2) if total_doc_tokens else 0)
    return total_chunks


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the RAHBAR index, then exit.")
    parser.add_argument("--dummy", action="store_true", help="write three fake chunks")
    parser.add_argument("--dry-run", action="store_true",
                        help="extract, clean and chunk only; print a summary, write nothing")
    parser.add_argument("--verify-corpus", action="store_true",
                        help="only check the corpus files against their SHA-256, then exit")
    args = parser.parse_args()
    settings = get_settings()
    log("indexer starting", dummy=args.dummy, embed_model=settings.embed_model,
        collection=settings.collection, chroma=f"{settings.chroma_host}:{settings.chroma_port}",
        pod=os.environ.get("HOSTNAME", "-"))
    started = time.perf_counter()
    try:
        if args.dummy:
            from .dummy import write_dummy_index
            from .store import get_client

            count = write_dummy_index(settings, get_client(settings))
        elif args.verify_corpus:
            count = verify_corpus(settings.corpus_dir)
        elif args.dry_run:
            count = dry_run(settings)
        else:
            count = run_pipeline(settings)
    except RuntimeError as exc:   # embed or store refused: say why, fail the Job
        log("indexer failed", error=str(exc))
        return 1
    log("indexer finished", chunks=count, seconds=round(time.perf_counter() - started, 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
