# Corpus

Source: the FAST-NUCES public website (nu.edu.pk) · Documents: 7 PDFs · Pages: 247

Every document is a public university file, downloaded on 3 November 2026 with
`corpus/fetch.py` from the URLs in `corpus/sources.yaml`. The PDFs belong to the
university, so they are **not committed**: `corpus/MANIFEST.md` records each URL, size and
SHA-256, and `python corpus/fetch.py` rebuilds `corpus/raw/` (gitignored) for anyone who
clones the repository. No page behind a login (SLATE, Flex, NU Mail) was used.

Everything below was checked against the downloaded files themselves, not against their
file names. Page numbers are PDF page numbers (1-based) unless a printed page number is
stated. Text was inspected with PyMuPDF; the image-only pages were inspected by rendering
them, because OCR has **not** been run yet (see "Known limitations").

## Requirement summary

| # | Required property | Document(s) that provide it | Evidence or notes |
| --- | --- | --- | --- |
| 1 | At least 6 documents, 40 pages | All seven documents below | 7 PDFs, 247 pages, page counts read from each file |
| 2 | Two versions of one policy | Undergraduate Academic Rules and Regulations, 2023 and 2026 editions | Same title, structure and clause numbers; clause 5.8 changed. The 2023 edition is `superseded: true` (inferred from the university's current listing) |
| 3 | Scanned, image-only PDF | Faculty Entrepreneurship and Technical Start-ups Policy | No fonts and no text on any of its 8 pages; each page is one ~300 dpi image. The 2023 rules also lack a text layer |
| 4 | A table with real information | Annual Report 2023-24; Prospectus Fee Information; grade-point table in the 2026 rules | Programmes-by-campus table (24 rows × 5 columns) on page 11 of the annual report |
| 5 | Long definitions | Student Handbook 2026 | "Policy on Generative AI Based Tools" runs about 1,600 words over several pages; long definitional clauses in the Code of Conduct |
| 6 | A deliberately absent topic | Hostel rules, hostel fees, hostel application procedures | The Prospectus "Transport and Hostel Facilities" PDF is excluded. Hostel **availability** is mentioned and stays answerable |
| 7 | Exact-term content | 2026 rules, Prospectus rules, Fee Information | Clause 1.21, clause 5.4 (CGPA 2.00), Rs. 12,000 per credit hour; attendance is clause 1 in the Prospectus but 1.21 in the handbook |

## Document inventory

| # | Document | File in `corpus/raw/` | Version | Pages | Superseded | Text layer | Properties |
| --- | --- | --- | --- | ---: | --- | --- | --- |
| 1 | Undergraduate Academic Rules and Regulations 2023 | `undergraduate-academic-rules-aug-2023.pdf` | 2023-08 | 26 | yes (inferred) | None on 24 of 26 pages | 2, 3*, 7 |
| 2 | Undergraduate Academic Rules and Regulations 2026 | `academic-rules-handbook-jun-2026.pdf` | 2026-06 | 58 | no | Yes | 2, 4, 7 |
| 3 | Prospectus 2026: Academic Rules and Regulations | `prospectus-2026-academic-rules-and-regulations.pdf` | 2026 | 5 | no | Yes | 7 |
| 4 | Student Handbook 2026 | `student-handbook-jun-2026.pdf` | 2026-06 | 64 | no | Yes | 5 |
| 5 | Annual Report of the University 2023-2024 | `annual-report-2023-24.pdf` | 2023-24 | 85 | no | Yes | 4 |
| 6 | Prospectus 2026: Fee Information | `prospectus-2026-fee-information.pdf` | 2026-27 | 1 | no | Yes | 4, 7 |
| 7 | Faculty Entrepreneurship / Technical Start-ups Policy and Procedure | `faculty-entrepreneurship-and-technical-start-ups-policy.pdf` | 2022-05 | 8 | no | None | 3 |
| | **Total** | | | **247** | | | |

\* The 2023 rules are a CorelDRAW export rather than a paper scan, but they need OCR for the
same reason: pages 2 to 25 carry no fonts and no extractable text.

The file names are slugs of the names in `sources.yaml`, and the indexer stores them as each
chunk's `source`. The exact source URLs and SHA-256 checksums are in `corpus/MANIFEST.md`.

## 1. Scale: 7 documents, 247 pages

The page counts were read from the PDFs themselves: 26 + 58 + 5 + 64 + 85 + 1 + 8 = 247.
That is well above the 40-page minimum. It also leaves room to drop a document if the
indexer's memory limit forces it, without falling below either threshold.

## 2. Two versions of one policy

**Policy:** *Undergraduate Academic Rules and Regulations*.

| | 2023 edition | 2026 edition |
| --- | --- | --- |
| Title as printed | "UNDERGRADUATE ACADEMIC RULES AND REGULATIONS 2023" (cover) | "Undergraduate Academic Rules and Regulations … June 2026" (PDF page 3) |
| File metadata | Created 17 Aug 2023 (CorelDRAW 2021) | Created 22 Jun 2026 (Adobe InDesign) |
| Where the university links it | Only from the older Bachelor rules page (`/Student/RulesUndergrad`) | The Downloads page, as "Undergraduate Academic Rules & Regulations" |
| Metadata | `doc_version: 2023-08`, `superseded: true` | `doc_version: 2026-06`, `superseded: false` |

**Same policy.** Both editions have the same chapter structure (1. Introduction, 2. Grading
and GPA Calculations, 3. Admission Policy, 4. Registration, 5. Academic Assessment, …).
Many clauses are word-for-word identical under the same number:

- **1.21** "Students are required to maintain 100% attendance in lectures, labs, tutorials,
  seminars and discussions … Absence of a maximum of 20% of the total attendance may be
  condoned …" (2023: PDF page 7; 2026: PDF page 10).
- **5.4 to 5.7**, Academic Warning/Probation: the minimum CGPA to avoid academic warning is
  2.00; three consecutive semesters below 2.00 close the admission (2023: PDF page 17;
  2026: PDF page 24).

**Different editions: clause 5.8 changed.** It is the first clause under "Repeating a Course":

| Edition | Clause 5.8 |
| --- | --- |
| 2023 (PDF page 17, read from the rendered page) | "A student must repeat a course in which s/he gets an F grade, whenever that course is offered again." |
| 2026 (PDF page 25) | "Any failed or missed course from a previous semester should become mandatory in the subsequent semester (if Offered), and students should not be allowed to skip its registration." |

The 2023 rule only requires the repeat *at some point*. The 2026 rule makes the failed or
missed course mandatory in the very next semester, covers missed courses as well as failed
ones, and forbids skipping its registration. A question such as "If I fail a course, when must
I take it again?" therefore has a different answer in each edition, which is exactly what
failure mode E needs. We also noticed that 2026 clause 5.12 adds a pointer to a "Policy on
Retake Examinations (Section 9)" that the 2023 text does not contain.

**Why 2023 is marked superseded, and the limits of that claim.** Neither document says that
it replaces the other. The supersession is **inferred** from the university's own listing:
the current Downloads page offers only the 2026 edition as the undergraduate rules, and the
2023 file survives only on an older page. The 2026 edition is also the later edition of the
same title. We record this as `superseded: true` in `sources.yaml` and state the inference
there too.

**Not version pairs.** The Prospectus 2026 academic rules (document 3) are a current,
parallel summary of the same rules, not an older edition, so they are not superseded. The
annual report is a yearly report, not a version of a policy.

## 3. Scanned, image-only PDF

**Faculty Entrepreneurship / Technical Start-ups Policy and Procedure** (8 pages):

- PyMuPDF reports **0 fonts and 0 characters of text on every page**. That is the same
  test as `pdffonts` returning an empty font list, which the handout suggests.
- Each page is a single RGB image of 2448 × 3456 pixels, about **296 dpi** on an A4 page.
- The rendered first page shows a photographed or printed sheet with punch-hole marks. Its
  content matches the title: 1. Purpose, 2. Scope, 3. Objectives, 4. Eligibility criteria for
  a technical start-up. The page header reads "30.05.2022/FacultyEntrepreneurshipPolicy".

Without OCR, this document contributes nothing to the index, silently (failure mode A).

The **2023 academic rules** are a second, different case. They are a CorelDRAW export in
which only the cover and the last page carry fonts (212 characters of text in total); pages 2
to 25 have no text layer. The rendered pages are clean and sharp, so OCR should work better
than on a paper scan, but this has not been tested yet.

## 4. Tables with real information

- **Annual Report 2023-24**: PyMuPDF's table detector finds 33 tables in the first 80 pages.
  Example, PDF page 11: a 24-row × 5-column table of degree programmes against the campuses
  that offer them (CFD, ISB, KHI, LHR, PWR), marked with ✓. The raw extraction shows the
  expected damage: headers merge ("ProgramsOffered andCampuses"), words lose their spaces
  ("BachelorofBusinessAdministration"), and the campus columns collapse ("ISB KHI LHR" in
  one cell). This is the "mangled table" case the pipeline report needs.
- **Prospectus Fee Information**: fee categories with exact amounts, and a detected table.
- **2026 rules, clause 2.3 (PDF page 12)**: the grade-points table (A+ 4.00 … F 0.00) is
  printed as two side-by-side column pairs, and plain text extraction interleaves them:
  "A+ 4.00 C+ 2.33 A 4.00 C 2.00 A- 3.67 C- 1.67 …". A chunk built from that text pairs
  each grade with the right points only by luck of the reading order.

## 5. Long definitions

**Student Handbook 2026**:

- **"XII. Policy on Generative AI Based Tools"** starts on PDF page 48 (printed page 40)
  and runs to about 1,600 words over that page and the next five. It is a long, connected
  policy that a fixed-size chunker would cut mid-argument.
- **Code of Conduct**, for example "Lying encompasses the following: Willful and knowledgeable
  telling of an untruth, as well as any form of deceit, attempted deceit, or fraud …"
  followed by lettered sub-items (PDF page 13). The definition and its sub-items only make
  sense together.

## 6. Deliberately absent topic: hostel rules, fees and applications

**What we left out:** the Prospectus "Transport and Hostel Facilities" PDF, which is listed
on the Prospectus page and is the obvious source for hostel questions.

**Why a user would plausibly ask:** students moving to Chiniot-Faisalabad or Peshawar need
to know how to apply for a hostel room, what it costs and what the rules are.

**What is still in the corpus, and therefore answerable:**

| Document | What it says about hostels |
| --- | --- |
| Student Handbook 2026, PDF page 45 | "In-Campus Hostel Facility is exclusively provided at Chiniot-Faisalabad and Peshawar Campuses." |
| Annual Report 2023-24, PDF pages 6, 10, 84, 85 | Chiniot-Faisalabad offers boys' and girls' hostels; Peshawar has a hostel for boys; first-aid boxes are kept in the Peshawar hostel |

**What is absent:** a search of every document with a text layer found no hostel fees,
charges, rent, rules, timings, wardens, applications or allotment. The Fee Information page
and both rules documents do not mention hostels at all.

**Consequence for the golden set:** must-abstain questions must ask about hostel
**rules, fees or how to apply**, for example "How do I apply for a hostel room?" or "What is
the hostel fee per semester?". A question such as "Which campuses have a hostel?" is
**answerable** from the corpus and must not be marked must-abstain.

**Limitation:** the two documents without a text layer could only be checked by reading
their rendered pages. Their subjects (academic rules, and faculty start-ups) make hostel
fees very unlikely, but this will be re-checked after OCR.

## 7. Exact-term content

| Exact term | Where | Why it is hard |
| --- | --- | --- |
| Clause **1.21**: 100% attendance, up to 20% condonable | 2026 rules, PDF page 10 (same number in 2023) | Embeddings treat 1.21 and 1.12 as near-identical |
| The same attendance rule as clause **1** | Prospectus rules, first page | Two clause numbers for one rule |
| Clause **5.4**: minimum CGPA **2.00** to avoid academic warning | 2026 rules, PDF page 24 | A threshold number inside a sentence |
| CGPA **3.90** or more: "Suma cum laude" (clause 7.2, spelled as printed) | 2026 rules, PDF page 30 | Several nearby thresholds (3.75, 3.60) |
| Tuition **Rs. 12,000 per credit hour** (2026-27) | Fee Information | Amounts that differ in one digit |
| Admission fee **Rs. 30,000**; security deposit **Rs 20,000** | Fee Information | The same |

## Source availability and integrity

- All seven URLs downloaded successfully on 3 November 2026: HTTP 200, `application/pdf`,
  and every file starts with `%PDF-` and opens. The 2023 rules, which the handout reported
  as not opening on 1 November, downloaded and opened normally (14,888,226 bytes).
- `python corpus/fetch.py --verify` re-hashes every local file against `corpus/MANIFEST.md`:
  **7 of 7 match**.
- Reproduce from a clean clone: install the fetcher's dependencies (`httpx`, `pyyaml`,
  pinned in `indexer/requirements.txt`), run `python corpus/fetch.py`, then
  `python corpus/fetch.py --verify`. The fetcher downloads one file at a time, pauses
  between requests, and reuses files it already has.
- Websites change. A `changed` line from `--verify` means the university published new
  bytes at the same URL; the new file must be re-inspected before the manifest is updated.

## Known limitations and future work

- **OCR is required and not yet implemented.** Two documents (34 pages) have no text layer.
  The indexer image will need `tesseract-ocr` and `poppler-utils`, and the indexer Job a
  memory limit sized from measured usage. Until OCR runs, the superseded edition of the
  version pair and the entrepreneurship policy are invisible to retrieval.
- **Extraction artefacts already seen:** letter-spaced words in the Prospectus rules
  ("t u t o r i a l s"), merged words and collapsed columns in annual-report tables, and
  repeating running headers on every page ("Undergraduate Academic Rules and Regulations
  2026 … NATIONAL UNIVERSITY OF COMPUTER AND EMERGING SCIENCES"). The clean stage must
  handle these; `docs/PIPELINE.md` will report the rules and before-and-after examples.
- **Supersession is inferred**, as explained in section 2.
- **Overlapping current documents:** the Prospectus rules repeat parts of the 2026 handbook
  under different clause numbers. Both are current, so retrieval may return either one,
  and citations must name whichever was actually retrieved.
- **Candidates checked but not selected:** the Annual Report 2019-20 (33 pages) and the Fee
  Structure and Grading HTML pages. They were left out to keep the corpus PDF-only and to
  avoid HTML navigation boilerplate in the extract stage.
