"""Evidence vault data service (guide 4.13, cross-module).

Files stored on disk under sheplatform/data/evidence/; metadata + SHA-256
hash in the evidence table. Links any entity (incident, risk, permit...).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

EVIDENCE_DIR = Path(__file__).resolve().parent.parent / "data" / "evidence"


def _ensure_dir() -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    return EVIDENCE_DIR


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_evidence(db, *, entity_type: str, entity_id: int, original_name: str,
                   file_bytes: bytes, mime_type: str = "",
                   tags: list | None = None, uploaded_by: int | None = None,
                   org_id: int | None = None) -> dict:
    """Store a file + metadata row. Returns the evidence record."""
    _ensure_dir()
    file_name = f"{uuid.uuid4().hex}_{original_name}"
    file_path = str(_ensure_dir() / file_name)
    with open(file_path, "wb") as f:
        f.write(file_bytes)
    file_hash = _sha256(file_bytes)

    import json
    db.execute(
        "INSERT INTO evidence (file_name, original_name, file_path, file_size, mime_type, "
        "file_hash, entity_type, entity_id, tags, uploaded_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (file_name, original_name, file_path, len(file_bytes), mime_type,
         file_hash, entity_type, entity_id, json.dumps(tags or []),
         uploaded_by, org_id))
    db.commit()
    row = db.execute("SELECT * FROM evidence ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def list_evidence(db, entity_type: str | None = None, entity_id: int | None = None,
                  org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM evidence"
    conds, params = [], []
    if entity_type:
        conds.append("entity_type = %s")
        params.append(entity_type)
    if entity_id is not None:
        conds.append("entity_id = %s")
        params.append(entity_id)
    if org_id:
        conds.append("org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_evidence(db, evidence_id: int) -> dict | None:
    row = db.execute("SELECT * FROM evidence WHERE id = %s", (evidence_id,)).fetchone()
    return dict(row) if row else None


def verify_file(db, evidence_id: int) -> tuple[bool, str]:
    """Verify stored file hash matches the recorded hash (integrity check)."""
    ev = get_evidence(db, evidence_id)
    if ev is None:
        return False, "not found"
    path = Path(ev["file_path"])
    if not path.exists():
        return False, "file missing"
    actual = _sha256(path.read_bytes())
    return actual == ev["file_hash"], "ok" if actual == ev["file_hash"] else "hash mismatch"
