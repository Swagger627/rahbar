"""Assignment 03 ingestion. The inbox manifest is the authoritative desired corpus."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import re
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .chunk import chunk
from .clean import clean
from .config import IndexerSettings
from .embed import embed
from .extract import extract
from .models import Document, Page
from .run import log
from .store import ADD_BATCH, get_client


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path, default: Any = None) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pending.replace(path)


def cosine(a: list[float], b: list[float]) -> float:
    denominator = math.hypot(*a) * math.hypot(*b)
    return sum(x * y for x, y in zip(a, b, strict=True)) / denominator if denominator else 0.0


def extracted(path: Path, sha: str, lake: Path, version: str, superseded: bool) -> Document:
    cached = lake / "extractions" / f"v1-{sha}.json"
    pages = read_json(cached)
    if pages is None:
        result = extract(str(path), version, superseded)
        save(cached, [asdict(page) for page in result.pages])
        return result
    return Document(path.name, version, superseded,
                    [Page(**{**page, "source": path.name}) for page in pages])


def model_version(settings: IndexerSettings) -> str:
    response = httpx.get(settings.ollama_url.rstrip("/") + "/api/tags", timeout=20)
    response.raise_for_status()
    name = settings.embed_model if ":" in settings.embed_model else settings.embed_model + ":latest"
    for model in response.json()["models"]:
        if model["name"] == name:
            return str(model["digest"])
    raise RuntimeError(f"embedding model {name} is not installed in Ollama")

def verify(inbox: Path, lake: Path, settings: IndexerSettings, threshold: float,
           previous: dict[str, Any], version_pin: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    specs = read_json(inbox / "_ragops.json")
    if not isinstance(specs, list):
        raise RuntimeError("expected a list in the inbox _ragops.json")
    names = [spec["file"] for spec in specs]
    if len(set(names)) != len(names) or any(Path(name).name != name for name in names):
        raise RuntimeError("manifest filenames must be unique basenames")
    # Compare new inputs against accepted inputs first. A manifest row with a different
    # version of the same policy is a version transition, even when most wording matches.
    specs.sort(key=lambda spec: spec["file"] not in previous)
    accepted: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    signatures = 0
    for spec in specs:
        name = spec["file"]
        path = inbox / name
        old = previous.get(name, {})
        quarantined = old.get("quarantine")
        if not path.is_file() and quarantined and (lake / quarantined).is_file():
            rows.append(old)
            continue

        result: dict[str, Any] = {"file": name, "document_id": spec.get("document_id"), "checks": {}}
        checks = result["checks"]
        sha = ""
        doc = None
        text = ""
        try:
            sha = checksum(path.read_bytes())
            doc = extracted(path, sha, lake, str(spec.get("doc_version", "unknown")), False)
            text = "\n".join(page.text for page in doc.pages).strip()
            letters = sum(char.isalpha() for char in text)
            quality = letters >= 20 and text.count("\ufffd") <= len(text) * 0.02
            checks["quality"] = check(quality, f"{letters} letters; replacement characters: "
                                      f"{text.count(chr(65533))}")
        except (OSError, ValueError) as exc:
            checks["quality"] = check(False, str(exc))

        missing = [key for key in ("source", "title", "doc_version", "document_id")
                   if not isinstance(spec.get(key), str) or not spec[key].strip()]
        safe_components = all(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", str(spec.get(key, "")))
                              for key in ("document_id", "doc_version"))
        expected = spec.get("page_count")
        pages = len(doc.pages) if doc else 0
        intact = (type(expected) is int and expected > 0 and pages == expected
                  and (not spec.get("sha256") or spec["sha256"] == sha)
                  and len(text.split()) >= spec.get("min_words", 1))
        checks["completeness"] = check(not missing and intact and safe_components,
                                       f"missing fields: {missing}; pages: {pages}/{expected}; "
                                       f"page, digest and length checks: {intact}; safe identity: {safe_components}")
        checks["recency"] = check(safe_components, "valid policy identity and version format")
        duplicate = next((item for item in accepted if item["sha256"] == sha), None)
        vector: list[float] | None = None
        if duplicate:
            checks["uniqueness"] = check(False, f"exact SHA-256 duplicate of {duplicate['file']}")
        elif all(entry["pass"] for entry in checks.values()):
            if old.get("sha256") == sha and old.get("signature_model") == version_pin:
                vector = old.get("signature")
            if not vector:
                words = text.split()
                middle = len(words) // 2
                sample = words if len(words) <= 240 else (
                    words[:96] + words[middle-36:middle+36] + words[-72:]
                )
                # Bound the signature independently of chunk size. OCR can produce
                # long multi-token words that make word count a poor context limit.
                signature_text = " ".join(sample).encode()[:1800].decode(errors="ignore")
                vector = embed([signature_text], settings)[0]
                signatures += 1
            nearest = max(((cosine(vector, item["signature"]), item["file"])
                           for item in accepted if item["document_id"] != spec["document_id"]),
                          default=(-1.0, "none"))
            checks["uniqueness"] = check(nearest[0] < threshold,
                                        f"closest different policy: {nearest[1]} ({nearest[0]:.4f}); "
                                        f"near-duplicate threshold {threshold}")
        else:
            checks["uniqueness"] = check(False, "no comparable verified text or metadata")

        result["sha256"] = sha
        if all(entry["pass"] for entry in checks.values()):
            result.update(spec, sha256=sha, signature=vector, signature_model=version_pin)
            accepted.append(result)
        rows.append(result)
        log("document checked", file=name, quality=checks["quality"]["pass"],
            completeness=checks["completeness"]["pass"], uniqueness=checks["uniqueness"]["pass"])

    newest: dict[str, str] = {}
    for item in accepted:
        doc_id, version = item["document_id"], item["doc_version"]
        newest[doc_id] = max(newest.get(doc_id, ""), version)
    for item in accepted:
        newer = newest[item["document_id"]]
        if item["doc_version"] == newer:
            item["checks"]["recency"] = check(True, f"latest verified version {newer}")
        else:
            known = item.get("superseded") or "stored" in previous.get(item["file"], {})
            item["checks"]["recency"] = check(bool(known), f"newer verified version {newer}; "
                                              f"historical version {item['doc_version']}")
    for row in rows:
        if not all(entry["pass"] for entry in row["checks"].values()):
            path = inbox / row["file"]
            if path.is_file():
                relative = Path("quarantine") / f"{row['sha256'][:16]}-{row['file']}"
                (lake / relative).parent.mkdir(parents=True, exist_ok=True)
                # Save the report before moving the file so retry can retain its real reasons.
                row["quarantine"] = str(relative)
                save(lake / "verification-state.json", {**previous, **{r["file"]: r for r in rows}})
                shutil.move(path, lake / relative)
            row["disposition"] = "quarantine"
            continue
        relative = Path("documents") / row["document_id"] / row["sha256"] / row["file"]
        target = lake / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            pending = target.with_suffix(".tmp")
            shutil.copyfile(inbox / row["file"], pending)
            pending.replace(target)
        row["stored"] = str(relative)
    return [row for row in accepted if "stored" in row], rows, signatures


def retain_history(rows: list[dict[str, Any]], lake: Path,
                   pins: dict[str, str]) -> list[dict[str, Any]]:
    """Read prior verified editions of policies still present in the desired inventory."""
    by_policy: dict[str, set[str]] = {}
    for row in rows:
        by_policy.setdefault(row["document_id"], set()).add(row["doc_version"])
    history = []
    for policy, versions in by_policy.items():
        for record in sorted((lake / "history" / policy).glob("*.json")):
            prior = read_json(record)
            version = prior["doc_version"]
            if version in versions or (version >= max(versions) and pins.get(policy) != version):
                continue
            path = lake / prior["stored"]
            if not path.is_file() or checksum(path.read_bytes()) != prior["sha256"]:
                raise RuntimeError(f"verified historical file missing or changed: {policy}={version}")
            checks = {**prior["checks"], "recency": check(True, f"verified archive edition {version}")}
            history.append({**prior, "checks": checks})
    return history


def check(passed: bool, reason: str) -> dict[str, Any]:
    return {"pass": passed, "reason": reason}


def update_index(settings: IndexerSettings, lake: Path, rows: list[dict[str, Any]],
                 pins: dict[str, str], version_pin: str) -> dict[str, Any]:
    by_version = {(row["document_id"], row["doc_version"]): row for row in rows}
    if len(by_version) != len(rows):
        raise RuntimeError("verified inputs contain two files for the same policy/version")
    active: dict[str, str] = {}
    for doc_id, version in by_version:
        active[doc_id] = max(active.get(doc_id, ""), version)
    for doc_id, version in pins.items():
        if doc_id in active:
            if (doc_id, version) not in by_version:
                raise RuntimeError(f"rollback version unavailable: {doc_id}={version}")
            active[doc_id] = version
    client = get_client(settings)
    existing = settings.collection in {c.name for c in client.list_collections()}
    profile = f"{settings.embed_model}@{version_pin}/{settings.chunk_size}/{settings.chunk_overlap}/pages-v1"
    revision = checksum(json.dumps([profile, sorted(active.items()),
                                    sorted((key, row["sha256"]) for key, row in by_version.items())],
                                   sort_keys=True).encode())[:16]
    metadata: dict[str, Any] = {"embed_model": settings.embed_model, "profile": profile,
                                "revision": revision, "updating": True,
                                "embed_version": version_pin}
    collection = client.get_collection(settings.collection) if existing else client.create_collection(
        settings.collection, metadata=metadata, configuration={"hnsw": {"space": "cosine"}})
    before = collection.metadata or {}
    if before.get("profile") != profile:
        raise RuntimeError("collection profile differs; select a separate Assignment 03 collection")
    indexed = collection.get(include=["metadatas"])
    stored = dict(zip(indexed["ids"], indexed["metadatas"], strict=True))
    prior_counts: dict[str, int] = {}
    for info in stored.values():
        prior_counts[info["document_id"]] = prior_counts.get(info["document_id"], 0) + 1

    writes = embedded = 0
    desired: set[str] = set()
    counts: dict[str, dict[str, int]] = {}
    dimension = before.get("dimension")
    if before.get("revision") != revision or before.get("updating"):
        collection.modify(metadata={**before, **metadata})
    for (doc_id, version), row in sorted(by_version.items()):
        archived = version != active[doc_id]
        row["disposition"] = "archive" if archived else "active"
        archive = lake / "archive" / doc_id / version / row["file"]
        if archived and not archive.exists():
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(lake / row["stored"], archive)
        prefix = checksum(f"{doc_id}:{version}:{row['sha256']}:".encode())[:24] + "-"
        keys = sorted(key for key in stored if key.startswith(prefix))
        # Count from the document record, since a terminated upsert can leave a partial batch.
        cache_path = (lake / "collections" / settings.collection / "chunks" / (prefix + ".json"))
        expected = read_json(cache_path)
        if not expected or len(keys) != expected:
            doc = clean(extracted(lake / row["stored"], row["sha256"], lake, version, archived))
            chunks = chunk(doc, settings.chunk_size, settings.chunk_overlap)
            if not chunks:
                raise RuntimeError(f"verified document has no indexable chunks: {row['file']}")
            keys = [f"{prefix}{i:06d}" for i in range(len(chunks))]
            save(cache_path, len(chunks))
            missing_indices = [i for i, key in enumerate(keys) if key not in stored]
            for offset in range(0, len(missing_indices), ADD_BATCH):
                indices = missing_indices[offset:offset + ADD_BATCH]
                texts = [chunks[i].text for i in indices]
                vectors = embed(texts, settings)
                dimension = len(vectors[0])
                details = [{**chunks[i].metadata(), "document_id": doc_id,
                            "pages": ",".join(map(str, chunks[i].pages))} for i in indices]
                batch_ids = [keys[i] for i in indices]
                collection.upsert(ids=batch_ids, documents=texts, embeddings=vectors, metadatas=details)
                stored.update(zip(batch_ids, details, strict=True))
                embedded += len(indices)
        changed = [key for key in keys if stored[key]["superseded"] != archived]
        if changed:
            collection.update(ids=changed, metadatas=[{"superseded": archived}] * len(changed))
            writes += len(changed)
        desired.update(keys)
        stat = counts.setdefault(doc_id, {"active": 0, "archived": 0})
        stat["archived" if archived else "active"] += len(keys)
    obsolete = sorted(stored.keys() - desired)
    for offset in range(0, len(obsolete), ADD_BATCH):
        collection.delete(ids=obsolete[offset:offset + ADD_BATCH])
    if collection.count() != len(desired):
        raise RuntimeError("collection count differs from the verified chunk set")
    if dimension is None:
        raise RuntimeError("new collection has no embeddable document")
    if before.get("revision") != revision or before.get("updating"):
        collection.modify(metadata={**metadata, "dimension": dimension, "updating": False})
    for doc_id in prior_counts.keys() - counts.keys():
        counts[doc_id] = {"active": 0, "archived": 0}
    return {"collection": settings.collection, "revision": revision, "profile": profile,
            "chunks_embedded": embedded, "chunks_deleted": len(obsolete),
            "chunk_status_updates": writes, "chunks_total": len(desired),
            "counts_by_document_id": counts, "counts_before": prior_counts}


def ingest(settings: IndexerSettings, lake: Path, output: Path, threshold: float,
           restore: str | None, latest: str | None) -> int:
    inbox = Path(settings.corpus_dir)
    lake.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    report_path = output / f"ingestion-{run_id}.json"
    with (lake / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = lake / "verification-state.json"
        report: list[dict[str, Any]] = []
        payload: dict[str, Any] = {"run_id": run_id}
        verified = False
        try:
            version_pin = model_version(settings)
            rows, report, signatures = verify(inbox, lake, settings, threshold,
                                              read_json(state_path, {}), version_pin)
            verified = True
            inbox_records = report.copy()
            pins_path = lake / "collections" / settings.collection / "restored.json"
            pins = read_json(pins_path, {})
            if restore:
                selection = restore.split("=", 1)
                if len(selection) != 2 or not any(row["document_id"] == selection[0] for row in rows):
                    raise RuntimeError("restore requires DOCUMENT_ID=VERSION for a verified policy in the inbox")
                pins[selection[0]] = selection[1]
            if latest:
                pins.pop(latest, None)
            historical = retain_history(rows, lake, pins)
            rows.extend(historical)
            report.extend(historical)
            for row in rows:
                save(lake / "history" / row["document_id"] / (row["doc_version"] + ".json"), row)
            payload["verification_embeddings"] = signatures
            payload["index_update"] = stats = update_index(settings, lake, rows, pins, version_pin)
        except Exception as exc:
            payload["error"] = str(exc)
            raise
        finally:
            if verified:
                save(state_path, {row["file"]: row for row in inbox_records})
            save(report_path, {**payload, "documents": [
                {key: value for key, value in row.items() if key != "signature"} for row in report
            ]})
        save(pins_path, pins)
        log("verification and update complete", report=str(report_path), **stats)
        return int(stats["chunks_total"])
