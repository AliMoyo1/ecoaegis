"""Audit trail (guide 5.8, NFR-SHE-004).

Append-only (no code path UPDATEs or DELETEs audit_log) AND tamper-evident:
every row carries a SHA-256 ``record_hash`` over its normalized content chained
to the previous row's hash (``prev_hash``), so any out-of-band edit, deletion,
or reordering of a past entry is detectable via ``verify_audit_chain()``.

Hashing is backend-agnostic by construction: JSON is normalized with sorted
keys (never hashed as raw JSONB, which PostgreSQL re-formats) and the hashed
timestamp is a plain-TEXT ``chain_ts`` (not the timestamptz ``created_at``). On
PostgreSQL the append is serialized with a row lock so concurrent writers can't
fork the chain; SQLite serializes writes already.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sheplatform.config import settings

_GENESIS = ""  # prev_hash of the first hashed row


def _norm_json(value) -> str:
    """Canonical JSON for a dict/list/None or an already-stored JSON string.

    Sorted keys, so it is identical whether the value arrives as a Python object
    (insert side) or as a JSON string that PostgreSQL JSONB re-formatted (verify
    side)."""
    if value is None:
        return "null"
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return json.dumps(value)  # opaque string, hash as-is
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _canonical(user_id, org_id, action, entity_type, entity_id,
               old_value, new_value, ip_address, chain_ts) -> str:
    return json.dumps({
        "user_id": user_id, "org_id": org_id, "action": action,
        "entity_type": entity_type, "entity_id": entity_id,
        "old": _norm_json(old_value), "new": _norm_json(new_value),
        "ip_address": ip_address, "chain_ts": chain_ts,
    }, sort_keys=True, separators=(",", ":"))


def _record_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{canonical}".encode("utf-8")).hexdigest()


def _chain_head(db) -> str:
    sql = ("SELECT record_hash FROM audit_log WHERE record_hash IS NOT NULL "
           "ORDER BY id DESC LIMIT 1")
    if settings.is_postgres():
        sql += " FOR UPDATE"  # serialize concurrent appends so the chain can't fork
    row = db.execute(sql).fetchone()
    return row["record_hash"] if row else _GENESIS


def log_audit(db, user_id: int | None, org_id: int | None, action: str,
              entity_type: str, entity_id: int | None = None,
              old_value: dict | None = None, new_value: dict | None = None,
              ip_address: str | None = None, commit: bool = True) -> None:
    chain_ts = datetime.now(timezone.utc).isoformat()
    prev_hash = _chain_head(db)
    canonical = _canonical(user_id, org_id, action, entity_type, entity_id,
                           old_value, new_value, ip_address, chain_ts)
    record_hash = _record_hash(prev_hash, canonical)
    db.execute(
        "INSERT INTO audit_log (user_id, org_id, action, entity_type, entity_id, "
        "old_value, new_value, ip_address, chain_ts, prev_hash, record_hash) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (user_id, org_id, action, entity_type, entity_id,
         json.dumps(old_value) if old_value is not None else None,
         json.dumps(new_value) if new_value is not None else None,
         ip_address, chain_ts, prev_hash, record_hash),
    )
    if commit:
        db.commit()


def verify_audit_chain(db) -> dict:
    """Walk the hashed audit rows in id order and detect tampering.

    Returns ``{ok, checked, first_break}``. ``first_break`` is None when intact,
    else ``{id, reason}`` for the earliest row whose content hash or chain
    linkage fails - i.e. an out-of-band edit, deletion, or reordering.
    """
    rows = db.execute(
        "SELECT id, user_id, org_id, action, entity_type, entity_id, old_value, "
        "new_value, ip_address, chain_ts, prev_hash, record_hash FROM audit_log "
        "WHERE record_hash IS NOT NULL ORDER BY id ASC").fetchall()
    expected_prev = _GENESIS
    checked = 0
    for r in rows:
        row = dict(r)
        canonical = _canonical(row["user_id"], row["org_id"], row["action"],
                               row["entity_type"], row["entity_id"], row["old_value"],
                               row["new_value"], row["ip_address"], row["chain_ts"])
        if _record_hash(row["prev_hash"] or _GENESIS, canonical) != row["record_hash"]:
            return {"ok": False, "checked": checked,
                    "first_break": {"id": row["id"], "reason": "content hash mismatch"}}
        if (row["prev_hash"] or _GENESIS) != expected_prev:
            return {"ok": False, "checked": checked,
                    "first_break": {"id": row["id"],
                                    "reason": "broken chain link (row deleted or inserted)"}}
        expected_prev = row["record_hash"]
        checked += 1
    return {"ok": True, "checked": checked, "first_break": None}


def list_audit(db, entity_type: str | None = None, entity_id: int | None = None,
               limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM audit_log"
    params = []
    conds = []
    if entity_type:
        conds.append("entity_type = %s")
        params.append(entity_type)
    if entity_id is not None:
        conds.append("entity_id = %s")
        params.append(entity_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
