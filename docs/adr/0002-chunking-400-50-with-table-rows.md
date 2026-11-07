# ADR 0002: Structure-aware chunks of 400 tokens with 50 overlap, and one chunk per table row

## Status
Accepted · 2026-11-04 (measured in Steps 3.1, 3.5C and 3.5D)

## Context
Retrieval can only return what chunking produced. The corpus mixes three very different
kinds of text:
- **numbered rules:** the academic rules handbooks, where one clause like "5.4" or "1.21" is
  a few lines;
- **long prose:** the annual report and the student handbook;
- **tables:** the fee structure, and the "programmes by campus" grid in the annual report,
  where a campus is marked only by a tick mark under a column heading.

Chunks that are too small cut a rule away from its heading. Chunks that are too large blur
several rules into one vector. Tick-mark tables lose their meaning entirely in plain text:
"5 Bachelor of Science (Civil Engineering) √" no longer says which campus.

## Decision
- **Size and overlap.** `CHUNK_SIZE=400`, `CHUNK_OVERLAP=50`, counted in whitespace tokens,
  set in the ConfigMap. Every chunk starts with its section heading and records `source`,
  `section`, `page`, `doc_version` and `superseded` (`indexer/pipeline/chunk.py`).
- **Break at the structure first.** Headings and numbered clauses start new sections, so a
  chunk never spans two sections. Within a long section, overlap keeps a sentence that
  crosses the boundary in both chunks.
- **Tables: one chunk per row.** `extract.py` `_name_ticks` replaces each tick with the
  column heading above it. Each row becomes a self-contained line,
  `Table row: <label> | marked under: LHR (columns: CFD ISB KHI LHR PWR)`, and the chunker
  keeps every `Table row:` line as its own chunk.

## Options considered
All measured with real `nomic-embed-text` vectors, over the 14 answerable golden questions
(`docs/PIPELINE.md`, "Chunking"):

| Size / overlap | recall@3 | recall@5 | Chunks | Why not |
| --- | --- | --- | --- | --- |
| 200 / 25 | 0.786 | 0.857 | 532 | ranks the right passage lower; 34% more chunks to embed and store |
| **400 / 50** | **0.857** | **0.857** | 398 | chosen |
| 800 / 100 | 0.786 | 0.786 | 351 | merges the Generative-AI "Acceptable Uses" passage into a bigger chunk, losing o07 |

- **Fixed-size chunks with no overlap** (section 5.5, stage B): not shipped. Cutting at fixed
  token counts would split clauses away from their headings, which is the failure this
  decision avoids.
- **Table rows inside the surrounding 400-token chunk:** this was the first version. The
  programmes grid was one 305-token list, and o05 (which campus offers BS Civil Engineering)
  ranked 19th.

## Why it fits RAHBAR
- Most questions target one rule or one table cell. 400 tokens is about one handbook section,
  big enough to keep a clause with its context, small enough to keep the vector specific.
- One chunk per row turns "which campus" questions into a near-exact match. After the
  change, o05 ranked first, and the final real-corpus evaluation has recall@5 1.0 for every
  question type (`docs/EVALUATION.md`).
- The 431-chunk index embeds in about 3 minutes on the laptop, so a weekly re-index is cheap.

## Trade-offs and consequences
- **Small differences.** With 14 questions, one question is worth 0.071, so the
  200/400/800 comparison is evidence for 400/50, not proof.
- **Storage.** Overlap and the repeated heading store about 7-8% more tokens than the text
  itself (amplification 1.07-1.08).
- **Corpus-specific.** The `Table row:` format is a convention the chunker must keep. A new
  document type (for example a timetable) may need its own extraction rule.
- **Bigger index.** Row chunks add chunks (398 → 431) and make each row compete on its own,
  which is good for lookups but adds noise for vague questions.
- **How to change it.** Changing `CHUNK_SIZE` or `CHUNK_OVERLAP` needs a re-index
  (`kubectl create job --from=cronjob/reindex ...`) and a new `eval/run_eval.py` run. The CI
  gate will flag a recall@5 drop of more than 5 points against `eval/baseline.json`.
