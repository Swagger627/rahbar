# Indexing and retrieval pipeline

<!-- STARTER-TODO(3.1, 3.3, 5.2): fill each section with your numbers, then delete this comment. -->

## Extraction
Code: `indexer/pipeline/extract.py`. pdfplumber reads each page's text layer. Any page with
fewer than 20 letters is treated as having no text layer: it is rendered at 300 dpi
(pdf2image + poppler) and read by tesseract (`--psm 4`), and the page is marked `ocr=True`.
OCR runs one page at a time, so memory stays low.

Two pdfplumber settings were needed on this corpus:
- `within_bbox(page.bbox)`: the 2026 handbook is an InDesign spread export, and each page
  also carries its neighbour's text just outside the visible area. Without this, page 25
  repeated clauses 5.1-5.5 from page 24.
- `use_text_flow=True`: follows the PDF's own reading order, so two-column pages are read
  one column at a time instead of mixing both columns on each line.

Pages that needed OCR (measured with `python -m pipeline.run --dry-run` inside the
`rahbar-indexer:dev` image, 2026-11-03):

| Document | Pages | OCR pages |
| --- | --- | --- |
| undergraduate-academic-rules-aug-2023.pdf (superseded) | 26 | 24 (pp 2-25: no text layer at all) |
| faculty-entrepreneurship-and-technical-start-ups-policy.pdf | 8 | 8 (a scan, no fonts) |
| academic-rules-handbook-jun-2026.pdf | 58 | 5 (covers and blank pages: 1, 2, 56, 57, 58) |
| student-handbook-jun-2026.pdf | 64 | 4 (covers and blank pages: 1, 2, 63, 64) |
| the other three PDFs | 91 | 0 |

OCR quality check: in the 2023 rules, clause 5.8 comes out word for word ("A student must
repeat a course in which s/he gets an F grade, whenever that course is offered again").
Some small errors remain ("Astudent", "Ifa student"). With tesseract's default layout mode
(`--psm 3`), the scanned policy's clause numbers were read as a separate column ("lel. ese
Late. 1.2.4.") and its headings lost their numbers. `--psm 4` (one column of lines) keeps
"4. Eligibility criteria for a technical start-up" followed by clauses 4.1-4.4.

Table, before and after (prospectus-2026-fee-information.pdf, "5. Miscellaneous Fees"). The
rendered table has the columns Description | Fee:

| Description | Fee |
| --- | --- |
| Additional Transcript Fee | Rs. 300 |
| Camps Transfer Fee | Rs. 5,000 |
| Semester Freeze Fee (within two weeks of start of classes) | Rs. 2,000 |

What extraction produces: one line per row, in reading order, with the cell borders gone:

```
5. Miscellaneous Fees
Description Fee
Additional Transcript Fee Rs. 300
Camps Transfer Fee Rs. 5,000
Semester Freeze Fee
(within two weeks of start of classes)
Rs. 2,000
```

Each fee stays next to its description, which is what a question about a fee needs. A
wrapped cell ("Semester Freeze Fee" plus its note) splits across lines, but it stays in
order.

Tick-mark tables needed one targeted fix. In the annual report's "programs by campus" table
(pp 11-12), a plain-text row read "5 Bachelor of Science (Civil Engineering) √", so the
column the tick sat in, which is the campus, was lost. `_name_ticks()` replaces each tick
with the column heading printed directly above it (within 20 points horizontally). A tick
with no heading above it is left as it is, so nothing is guessed. Before and after:

```
before: 3 Bachelor of Science (Artificial Intelligence) √ √ √ √
after:  3 Bachelor of Science (Artificial Intelligence) CFD ISB KHI PWR
before: 5 Bachelor of Science (Civil Engineering) √
after:  5 Bachelor of Science (Civil Engineering) LHR
```

All 80 ticks on the two pages were named, and none was left over. All 11 bachelor's rows
were compared with the rendered page and match. The rows of p11 and p12 end up in one
305-token chunk under "Chapter-1 Programs Offered and Campuses".

What could not be recovered:
- Superscripts split off. The refund schedule's "10th day" comes out as "th" on one line
  and "Upto 10 day of commencement of classes" on the next.
- The scanned policy's application forms (pp 4-8) OCR with checkbox noise ("O Recommended
  01 Not Recommended") and empty signature fields.

## Cleaning
Code: `indexer/pipeline/clean.py`. The rules, in order:
1. Unicode NFKC normalisation, so ligatures like "ﬁnal" become "final".
2. Runs of spaces, tabs and non-breaking spaces collapse to one space.
3. Letter-spaced words are joined again ("t u t o r i a l s" becomes "tutorials").
4. Page-number lines are dropped ("18", "Page 11 of 85").
5. Running headers and footers are dropped. A line counts as a header or footer if it
   appears on at least 3 pages and on at least 25% of the document's pages, comparing lines
   with digits, spaces and case ignored. Examples: "NATIONAL UNIVERSITY OF COMPUTER AND
   EMERGING SCIENCES" (53 of 58 pages of the 2026 handbook), "Student Handbook 2026 17",
   "Page 3 of 85".
6. Three or more blank lines collapse to one blank line.

Deliberately not stripped:
- Headings. "CODE OF CONDUCT" or "Repeating a Course" appear on only one or two pages, so
  rule 5 never touches them, and the chunker needs them for the `section` field.
- Line breaks and blank lines, because the chunker uses them to find headings and
  paragraphs.
- Clause numbers ("5.8"), because the golden set and citations refer to them.

Known trade-off: two body lines on different pages that differ only by numbers ("Rule 1
applies", "Rule 2 applies") look the same to rule 5. If such lines appeared on 25% of a
document's pages, they would be removed. No such case was seen in this corpus.

## Chunking
Code: `indexer/pipeline/chunk.py`.

**Unit: tokens, where one token is one whitespace-separated word** (`text.split()`). The
assignment measures size, overlap and amplification in tokens, and the starter asks us to
say which kind. These are whitespace tokens, NOT the embedding model's subword tokens. A
model tokenizer splits text into more pieces (roughly 1.3 per English word, a rule of
thumb, not a measurement here). Whitespace tokens need no tokenizer library and anyone can
recount them. `CHUNK_SIZE` and `CHUNK_OVERLAP` in the ConfigMap use this unit, and the
ConfigMap comment says so.

How it splits, in order:
1. **Sections.** A line is a heading if it is short (10 tokens or fewer, 80 characters or
   fewer), does not end in `. , ; :` and is not a table-of-contents line (`...`), and is one
   of the following: a Markdown `#` heading, a numbered title ("4. Eligibility criteria for a
   technical start-up", "B. Fee Refund Policy"), a chapter line ("Chapter-1 Programs Offered
   and Campuses"), an ALL-CAPS line of 2 or more words with at least one word longer than 3
   letters ("CODE OF CONDUCT", but not the table header "CFD ISB KHI LHR PWR"), or a
   Title-Case line followed by a numbered clause ("Repeating a Course" then "5.8 ..."). A
   section continues across pages until the next heading.
2. **Paragraphs** end at a blank line, at a new clause ("5.8 ...", "1.2.1. ...") or list item
   ("a.", "iv)"), and at the end of a page, so a paragraph's page number is exact.
3. **Packing.** Paragraphs fill a chunk up to `size` tokens, never mixing two sections or two
   documents. A paragraph longer than `size` is split at sentence ends, and a sentence that
   is still too long is cut every `size` tokens.
4. **Overlap.** Each new chunk in the same section starts with the last `overlap` tokens of
   the previous one. The overlap is shortened when needed, so a chunk body never exceeds
   `size`. There is no overlap across a section boundary.
5. **Text and metadata.** The chunk text is the heading line, then the body, so a retrieved
   chunk still says what it is about. Metadata is `source`, `section` (the nearest printed
   heading), `page` (where the chunk's new text starts), `doc_version` and `superseded`.
   IDs are stable: `<file stem>-<4-digit number>`, for example
   `academic-rules-handbook-jun-2026-0032`.

**Shipped: size 400 tokens, overlap 50 tokens** (ConfigMap `rahbar-config`).

Measured on the full corpus (7 PDFs, 247 pages, 58,801 tokens after cleaning) inside the
indexer image on 2026-11-04, after the tick-table and table-header fixes. Storage
amplification is stored tokens divided by original (cleaned) tokens. Stored tokens include
the overlap and the heading line repeated on every chunk.

| Size (tokens) | Overlap (tokens) | recall@5 | Chunks stored | Amplification | Median chunk (tokens) |
| --- | --- | --- | --- | --- | --- |
| 200 | 25 | not measured yet | 531 | 1.10 | 133 |
| 400 | 50 | not measured yet | 398 | 1.07 | 107 |
| 800 | 100 | not measured yet | 351 | 1.06 | 87 |

recall@5 needs real embeddings and retrieval (Step 3.3). It will be filled in from
`eval/run_eval.py` then.

Why amplification is low and the median chunk is small: most sections in these documents
are shorter than 400 words, and a chunk never crosses a section. So the section boundaries,
not the size limit, decide most chunks, and overlap applies only inside the few long
sections. Raising the size from 400 to 800 removes only 47 chunks. The median falls as the
size grows because the long sections pack into fewer, larger chunks, while the many short
sections stay as they are.

Section check against the golden set: all 15 `(source, section)` pairs in
`eval/golden.yaml` appear as chunk sections in the 400/50 run (matched with case,
punctuation and quotes ignored), including the two from OCR documents. This shows the
sections exist. It is not a retrieval score.

Seen weaknesses: text before a document's first heading (cover pages) goes into a section
named "Untitled section", 7 chunks in the whole corpus. A table row that starts like a
numbered title can still become a heading, e.g. on annual report p71: "13. Doctor of
Philosophy (Civil Engineering) LHR Sanctioned".

## Embedding
Code: `indexer/pipeline/embed.py`.

- **Model:** whatever `EMBED_MODEL` says. The pin lives in one place, the ConfigMap
  `rahbar-config`, which both the indexer and the API read. The intended model is
  `nomic-embed-text` on Ollama (the code default). The ConfigMap still holds `dummy-768`
  from the week-1 dummy index, and must be changed to the real model before the real
  indexer runs. The real pipeline refuses `dummy-768` with a clear error, so a fake model
  name can never produce a "real" index.
- **Call:** `POST {OLLAMA_URL}/api/embed` with `{"model": EMBED_MODEL, "input": [16 chunk
  texts], "truncate": false}`, 16 chunks per request (398 chunks take 25 requests). The
  timeout is 120 s per request, because the first call also loads the model, with 10 s to
  connect, so a missing Ollama fails fast. `truncate: false` makes a chunk that is too long
  for the model an error, instead of being silently cut.
- **Checks:** every request must return exactly one vector per text sent. Every vector must
  be a non-empty list of finite numbers, and all vectors must have the same length as the
  first one. Any failure (Ollama down, model not pulled, HTTP error, malformed JSON, wrong
  count, wrong or mixed dimension) stops the run with a message naming the chunks, the
  model and the URL. The Job then exits 1. There is no fallback to fake vectors.
- **Vector dimension:** not hard-coded. The indexer records the length of the vectors the
  model actually returned. `nomic-embed-text` is documented as 768-dimensional, but that
  has **not been measured here**, because no Ollama model has been run yet (see the test
  results below).
- **API check (section 4.4):** the indexer writes `embed_model` and `dimension` into the
  collection metadata. Comparing them with the API's `EMBED_MODEL` at startup is the API's
  half, and is not built yet.

### Storage in Chroma
Code: `indexer/pipeline/store.py`, the only code that writes to Chroma. The API only reads.

- One row per chunk: `id` (the stable chunk id), `document` (chunk text), `embedding`, and
  `metadata` = `source`, `section`, `page`, `doc_version`, `superseded`. The `superseded`
  flag is a real boolean, so the retriever can filter with `where={"superseded": False}`.
- Collection metadata: `{"hnsw:space": "cosine", "embed_model": <EMBED_MODEL>,
  "dimension": <actual vector length>}`.

### Re-indexing
Every run builds a complete new index beside the old one, then swaps it in:
1. Delete any `rahbar-staging` collection left by a crashed run, then create a fresh one.
2. Write all chunks into `rahbar-staging`, 100 rows per `add()`.
3. Check that `rahbar-staging` holds exactly one row per chunk. If not, delete it and stop:
   the old `rahbar` collection was never touched.
4. Delete the old `rahbar` and rename `rahbar-staging` to `rahbar`.

This makes repeated runs safe: there are no duplicate rows, and no rows left over from
deleted chunks. Vectors from two models can never share a collection, because each run
starts a brand-new collection, and Chroma itself rejects a vector of the wrong dimension.
Bad input (no chunks, a count mismatch, duplicate ids, mixed dimensions) is refused before
Chroma is touched.

Limitation: between the delete and the rename in step 4 (milliseconds), no `rahbar`
collection exists, so a question asked in exactly that instant fails once. Staging also
briefly needs room for two copies of the index (398 rows here, which is small).

### Test results (2026-11-04)
- Unit tests with controlled HTTP responses (no Ollama): batching, the configured model and
  URL, Ollama down, model not pulled (HTTP 404), 8 malformed-response cases, mixed
  dimensions across batches, dummy model refused. These vectors are test data, not
  embeddings.
- Unit tests with a fake Chroma client: metadata, repeated indexing, model change, leftover
  staging, a failed write keeping the old index, bad input.
- Integration against a temporary real Chroma 1.0.12 container (memory capped at 400 MB;
  it used 54 MB). The 398 real corpus chunks were stored with **controlled test vectors
  (hash-based, not embeddings)**:
  - 398 rows were written in 0.9 s, and the metadata read back intact (e.g. 2023 clause 5.8:
    `Repeating a Course`, p17, `superseded: True`).
  - A second run still held 398 rows, in one collection.
  - The `superseded=False` filter returned only non-superseded chunks.
  - Bad input left the old index unchanged.
  - A model change (768 to 4 dimensions) with a leftover staging collection ended with one
    collection holding only 4-d vectors and the new model in its metadata.
  - Chroma itself rejected a 768-d vector added to the 4-d collection.
- **Real Ollama embeddings: PENDING.** Ollama is not installed on this laptop and no
  embedding model has been downloaded, so no real embedding has been produced yet.

## Semantic cache
Thresholds you tried, the false hit that ruled one out, the one you shipped.

## Evaluation method
How recall@k matches retrieved passages to golden ones (by source and section? by chunk id?).
