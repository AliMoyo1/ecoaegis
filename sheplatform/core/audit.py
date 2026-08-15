"""Audit trail (guide 5.8, NFR-SHE-004). Append-only; never UPDATE/DELETE grants."""
from __future__ import annotations

import json


def log_audit(db, user_id: int | None, org_id: int | None, action: str,
              entity_type: str, entity_id: int | None = None,
              old_value: dict | None = None, new_value: dict | None = None,
              ip_address: str | None = None, commit: bool = True) -> None:
    db.execute(
        "INSERT INTO audit_log (user_id, org_id, action, entity_type, entity_id, "
        "old_value, new_value, ip_address) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (user_id, org_id, action, entity_type, entity_id,
         json.dumps(old_value) if old_value is not None else None,
         json.dumps(new_value) if new_value is not None else None,
         ip_address),
    )
    if commit:
        db.commit()


def list_audit(db, entity_type: str | None = None, entity_id: int | None = None, limit: int = 100) -> list[dict]:
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
