"""Generic attachments subsystem (guide 3.1).

Reusable photo/file attachment for any entity (incident, observation,
inspection, chemical, permit). Follows the evidence-vault pattern but adds
strict MIME-type + magic-byte validation and org-scoped access.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sheplatform.config import settings

ATTACHMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "attachments"

ALLOWED = {
    "image/jpeg": (b"\xff\xd8\xff", "jpg"),
    "image/png": (b"\x89PNG", "png"),
    "application/pdf": (b"%PDF", "pdf"),
}

MAX_BYTES = {"photo": 10 * 1024 * 1024, "file": 25 * 1024 * 1024}

_ENTITY_TYPES = {"incident", "observation", "inspection", "chemical", "permit", "risk"}


def _ensure_dir() -> Path:
    ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)
    return ATTACHMENTS_DIR


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_file(file_bytes: bytes, declared_mime: str, kind: str) -> tuple[str, str]:
    """Return (stored_name, mime_type) or raise ValueError on rejection."""
    if kind not in MAX_BYTES:
        raise ValueError(f"invalid kind: {kind}")
    if len(file_bytes) > MAX_BYTES[kind]:
        raise ValueError(
            f"{kind} exceeds maximum size of {MAX_BYTES[kind] // (1024 * 1024)} MB")

    mime = declared_mime or ""
    if mime not in ALLOWED:
        raise ValueError(f"unsupported file type: {mime}")

    magic, ext = ALLOWED[mime]
    if not file_bytes.startswith(magic):
        raise ValueError(f"file content does not match declared type {mime}")

    return f"{uuid.uuid4().hex}.{ext}", mime


def save_attachment(db, *, entity_type: str, entity_id: int, file_bytes: bytes,
                    original_name: str, mime_type: str = "", kind: str = "file",
                    org_id: int, uploaded_by: int | None = None) -> dict:
    """Store a validated file + metadata row. Returns the attachment record."""
    if entity_type not in _ENTITY_TYPES:
        raise ValueError(f"unsupported entity_type: {entity_type}")
    if not org_id:
        raise ValueError("org_id is required")

    file_name, mime = _validate_file(file_bytes, mime_type, kind)
    storage_path = _ensure_dir() / file_name
    with open(storage_path, "wb") as f:
        f.write(file_bytes)
    file_hash = _sha256(file_bytes)

    new_id = db.execute(
        "INSERT INTO attachments (entity_type, entity_id, file_name, original_name, "
        "mime_type, size_bytes, sha256, kind, ai_labels, org_id, uploaded_by, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (entity_type, entity_id, file_name, original_name or file_name, mime,
         len(file_bytes), file_hash, kind, None, org_id, uploaded_by,
         datetime.now(timezone.utc).isoformat()),
    ).fetchone()["id"]
    db.commit()
    row = db.execute("SELECT * FROM attachments WHERE id = %s", (new_id,)).fetchone()
    return dict(row)


def list_attachments(db, entity_type: str, entity_id: int, org_id: int | None) -> list[dict]:
    if not org_id:
        return []
    rows = db.execute(
        "SELECT * FROM attachments WHERE entity_type = %s AND entity_id = %s AND org_id = %s "
        "ORDER BY id DESC",
        (entity_type, entity_id, org_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_attachment(db, attachment_id: int, org_id: int | None) -> dict | None:
    if not org_id:
        return None
    row = db.execute(
        "SELECT * FROM attachments WHERE id = %s AND org_id = %s",
        (attachment_id, org_id),
    ).fetchone()
    return dict(row) if row else None


def delete_attachment(db, attachment_id: int, org_id: int, user_id: int | None = None) -> bool:
    row = get_attachment(db, attachment_id, org_id)
    if row is None:
        return False
    storage_path = _ensure_dir() / row["file_name"]
    if storage_path.exists():
        storage_path.unlink()
    db.execute("DELETE FROM attachments WHERE id = %s AND org_id = %s", (attachment_id, org_id))
    db.commit()
    _log_audit(db, user_id, org_id, "attachment_deleted", row)
    return True


def update_ai_labels(db, attachment_id: int, org_id: int, labels: dict) -> bool:
    row = get_attachment(db, attachment_id, org_id)
    if row is None:
        return False
    db.execute(
        "UPDATE attachments SET ai_labels = %s WHERE id = %s AND org_id = %s",
        (json.dumps(labels), attachment_id, org_id),
    )
    db.commit()
    return True


def _log_audit(db, user_id, org_id, action, record):
    from sheplatform.core.audit import log_audit
    log_audit(
        db,
        user_id=user_id,
        org_id=org_id,
        action=action,
        entity_type="attachment",
        entity_id=record.get("id"),
        new_value={
            "entity_type": record.get("entity_type"),
            "entity_id": record.get("entity_id"),
            "original_name": record.get("original_name"),
            "sha256": record.get("sha256"),
        },
    )


def verify_file(db, attachment_id: int, org_id: int) -> tuple[bool, str]:
    att = get_attachment(db, attachment_id, org_id)
    if att is None:
        return False, "not found"
    storage_path = _ensure_dir() / att["file_name"]
    if not storage_path.exists():
        return False, "file missing"
    actual = _sha256(storage_path.read_bytes())
    return actual == att["sha256"], "ok" if actual == att["sha256"] else "hash mismatch"
