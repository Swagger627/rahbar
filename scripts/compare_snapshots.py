"""Compare two extraction snapshots (scripts/extraction_snapshot.py) field by field, then
check facts we rely on in the AFTER snapshot: clause 5.8 (2023 rules), clause 1.21
(current rules), the campus table rows, and Civil Engineering at Lahore.

  python scripts/compare_snapshots.py before.json after.json

Exits 1 if anything differs, so a library upgrade can never change the extracted facts
silently.
"""

import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    b = json.load(f)
with open(sys.argv[2], encoding="utf-8") as f:
    a = json.load(f)
print("libraries before:", b["libraries"])
print("libraries after: ", a["libraries"])
print("SHA-256 verified files: before", b["verified_files"], "after", a["verified_files"])

diffs: list[str] = []
if [d["source"] for d in b["documents"]] != [d["source"] for d in a["documents"]]:
    diffs.append("the document lists differ")
for db, da in zip(b["documents"], a["documents"]):
    name = db["source"]
    for key in ("doc_version", "superseded"):
        if db[key] != da[key]:
            diffs.append(f"{name}: {key} {db[key]!r} -> {da[key]!r}")
    if len(db["pages"]) != len(da["pages"]):
        diffs.append(f"{name}: {len(db['pages'])} pages -> {len(da['pages'])}")
    for pb, pa in zip(db["pages"], da["pages"]):
        if pb != pa:
            diffs.append(f"{name} page {pb['page']}: {pb} -> {pa}")
    if len(db["chunks"]) != len(da["chunks"]):
        diffs.append(f"{name}: {len(db['chunks'])} chunks -> {len(da['chunks'])}")
    for cb, ca in zip(db["chunks"], da["chunks"]):
        for key in cb:
            if cb[key] != ca.get(key):
                diffs.append(f"chunk {cb['id']}: {key} changed")

def count(snapshot: dict, what: str) -> int:
    if what == "chunks":
        return sum(len(d["chunks"]) for d in snapshot["documents"])
    return sum(p["ocr"] for d in snapshot["documents"] for p in d["pages"])

print(f"chunks {count(b, 'chunks')} -> {count(a, 'chunks')}; "
      f"OCR pages {count(b, 'ocr')} -> {count(a, 'ocr')}")
for line in diffs:
    print("DIFF", line)
print("TOTAL DIFFERENCES:", len(diffs))

chunks = [c for d in a["documents"] for c in d["chunks"]]
print("\nDocuments (after):")
for d in a["documents"]:
    print(f"  {d['source']}: doc_version={d['doc_version']} superseded={d['superseded']} "
          f"pages={len(d['pages'])} ocr_pages={sum(p['ocr'] for p in d['pages'])} "
          f"chunks={len(d['chunks'])}")
c58 = [c for c in chunks if "2023" in c["source"] and re.search(r"(^|\D)5\.8(\D|$)", c["text"])]
print("clause 5.8 in the 2023 rules:", [(c["id"], c["section"], c["page"]) for c in c58])
c121 = [c for c in chunks
        if "2026" in c["source"] and re.search(r"(^|[^\d.])1\.21(\D|$)", c["text"])]
print("clause 1.21 in the 2026 rules:", [(c["id"], c["section"], c["page"]) for c in c121])
rows = [c for c in chunks if "Table row:" in c["text"]]
print("campus table-row chunks:", len(rows))
for c in rows:
    for line in c["text"].splitlines():
        if "Civil Engineering" in line and line.startswith("Table row:"):
            print("  ", c["id"], "|", line)
sys.exit(1 if diffs else 0)
