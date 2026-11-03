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

What could not be recovered:
- Tick-mark matrices lose their columns. In the annual report's "programs by campus" table,
  a row becomes "5 Bachelor of Science (Civil Engineering) √", and which campus the tick
  belongs to (LHR, checked on the rendered page) is lost.
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

**The unit is words, not model tokens.** Size and overlap count whitespace-separated words,
because words are easy to count and to explain. An embedding model counts subword tokens,
roughly 1.3 per English word, so a 400-word chunk is about 520 tokens. That estimate is a
rule of thumb, not a measurement. The ConfigMap values `CHUNK_SIZE` and `CHUNK_OVERLAP` are
read as words.

How it splits, in order:
1. **Sections.** A line is a heading if it is short (10 words or fewer, 80 characters or
   fewer), does not end in `. , ; :` and is not a table-of-contents line (`...`), and is one
   of the following: a Markdown `#` heading, a numbered title ("4. Eligibility criteria for a
   technical start-up", "B. Fee Refund Policy"), a chapter line ("Chapter-1 Programs Offered
   and Campuses"), an ALL-CAPS line of 2 or more words ("CODE OF CONDUCT"), or a Title-Case
   line followed by a numbered clause ("Repeating a Course" then "5.8 ..."). A section
   continues across pages until the next heading.
2. **Paragraphs** end at a blank line, at a new clause ("5.8 ...", "1.2.1. ...") or list item
   ("a.", "iv)"), and at the end of a page, so a paragraph's page number is exact.
3. **Packing.** Paragraphs fill a chunk up to `size` words, never mixing two sections or two
   documents. A paragraph longer than `size` is split at sentence ends, and a sentence that
   is still too long is cut every `size` words.
4. **Overlap.** Each new chunk in the same section starts with the last `overlap` words of
   the previous one. The overlap is shortened when needed, so a chunk body never exceeds
   `size`. There is no overlap across a section boundary.
5. **Text and metadata.** The chunk text is the heading line, then the body, so a retrieved
   chunk still says what it is about. Metadata is `source`, `section` (the nearest printed
   heading), `page` (where the chunk's new text starts), `doc_version` and `superseded`.
   IDs are stable: `<file stem>-<4-digit number>`, for example
   `academic-rules-handbook-jun-2026-0032`.

**Shipped: size 400 words, overlap 50 words** (ConfigMap `rahbar-config`).

Measured on the full corpus (7 PDFs, 247 pages, 58,801 words after cleaning) inside the
indexer image. Storage amplification is stored words divided by cleaned words. Stored words
include the overlap and the heading line repeated on every chunk.

| Size (words) | Overlap (words) | recall@5 | Chunks stored | Amplification | Median chunk (words) |
| --- | --- | --- | --- | --- | --- |
| 200 | 25 | not measured yet | 537 | 1.10 | 132 |
| 400 | 50 | not measured yet | 405 | 1.07 | 100 |
| 800 | 100 | not measured yet | 358 | 1.06 | 87 |

recall@5 needs embeddings and retrieval (Steps 3.1B and 3.3). It will be filled in from
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

Seen weaknesses: a few table header rows are caught as ALL-CAPS headings (the annual
report's campus row "CFD ISB KHI LHR PWR"). Text before a document's first heading (cover
pages) goes into a section named "Untitled section": 6 chunks in the whole corpus.

## Embedding
Model, vector dimension, where the pin lives, how the API checks it.

## Semantic cache
Thresholds you tried, the false hit that ruled one out, the one you shipped.

## Evaluation method
How recall@k matches retrieved passages to golden ones (by source and section? by chunk id?).
