"""Exact, reviewed assignment of historical records to canonical sites.

The resolver is deliberately conservative: it normalizes case, Unicode, and
whitespace, then accepts only an exact unique active-site code or name inside
the current organisation. It never performs fuzzy matching and never commits
a link without a reviewed write action.
"""
from __future__ import annotations

from datetime import datetime, timezone
import unicodedata

from sheplatform.config import settings
from sheplatform.core.audit import log_audit


MAX_REVIEW_RECORDS = 200
RECORD_NOT_FOUND_MESSAGE = "record is not available for this organisation"
SITE_NOT_FOUND_MESSAGE = "site is not active for this organisation"
RECORD_ALREADY_LINKED_MESSAGE = "record already has a canonical site"

RECORD_SPECS = {
    "permit": {
        "table": "permits",
        "ref": "permit_ref",
        "location": "site_location",
        "label": "Permit",
    },
    "inspection": {
        "table": "inspections",
        "ref": "inspection_ref",
        "location": "site_location",
        "label": "Inspection",
    },
    "eia": {
        "table": "eia_projects",
        "ref": "project_ref",
        "location": "location",
        "label": "EIA project",
    },
    "emergency": {
        "table": "emergency_events",
        "ref": "event_ref",
        "location": "site_location",
        "label": "Emergency",
    },
}
REVIEW_STATUSES = {"pending", "skipped", "resolved", "all"}
SITE_TYPES = {"facility", "tower", "retail", "warehouse", "office"}


def normalize_site_label(value: object) -> str:
    """Normalize only representation, not meaning, for exact comparison."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.casefold().split())


def _active_sites(db, org_id: int | None) -> list[dict]:
    if not org_id:
        return []
    rows = db.execute(
        "SELECT id, site_code, site_name, city, region FROM sites "
        "WHERE org_id = %s AND status = 'active' ORDER BY site_name, site_code",
        (org_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _resolve_from_sites(original_text: object, sites: list[dict]) -> dict:
    normalized = normalize_site_label(original_text)
    if not normalized:
        return {"status": "unresolved", "normalized": "", "candidates": []}

    matches: dict[int, dict] = {}
    for site in sites:
        keys = {
            normalize_site_label(site.get("site_code")),
            normalize_site_label(site.get("site_name")),
        }
        if normalized in keys:
            matches[site["id"]] = site

    candidates = list(matches.values())
    if len(candidates) == 1:
        status = "matched"
    elif len(candidates) > 1:
        status = "ambiguous"
    else:
        # "suggested" is reserved for a separately approved suggestion
        # engine. Release 1 intentionally has no fuzzy or substring fallback.
        status = "unresolved"
    return {"status": status, "normalized": normalized, "candidates": candidates}


def resolve_exact_site(db, *, org_id: int | None, original_text: object) -> dict:
    """Return a reviewed-resolution outcome without changing any record."""
    return _resolve_from_sites(original_text, _active_sites(db, org_id))


def _safe_limit(limit: int) -> int:
    try:
        parsed = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    return max(1, min(parsed, MAX_REVIEW_RECORDS))


def list_resolution_queue(
    db,
    *,
    org_id: int | None,
    review_status: str = "pending",
    limit: int = 100,
) -> dict:
    """List tenant records and exact candidates for human review."""
    if review_status not in REVIEW_STATUSES:
        raise ValueError("invalid review status")
    limit = _safe_limit(limit)
    if not org_id:
        return {
            "records": [],
            "available_sites": [],
            "counts": {"total": 0, "linked": 0, "unlinked": 0,
                       "pending": 0, "skipped": 0},
            "limit": limit,
            "truncated": False,
        }

    sites = _active_sites(db, org_id)
    records: list[dict] = []
    totals = {"total": 0, "unlinked": 0, "skipped": 0}

    for record_type, spec in RECORD_SPECS.items():
        table = spec["table"]
        total_row = db.execute(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN r.site_id IS NULL THEN 1 ELSE 0 END) AS unlinked, "
            f"SUM(CASE WHEN r.site_id IS NULL AND d.decision = 'skipped' "
            f"THEN 1 ELSE 0 END) AS skipped "
            f"FROM {table} r LEFT JOIN site_resolution_decisions d "
            f"ON d.org_id = r.org_id AND d.record_type = %s AND d.record_id = r.id "
            f"WHERE r.org_id = %s",
            (record_type, org_id),
        ).fetchone()
        totals["total"] += int(total_row["total"] or 0)
        totals["unlinked"] += int(total_row["unlinked"] or 0)
        totals["skipped"] += int(total_row["skipped"] or 0)

        conditions = ["r.org_id = %s"]
        params: list[object] = [record_type, org_id]
        if review_status == "pending":
            conditions.append(
                "r.site_id IS NULL AND (d.decision IS NULL OR d.decision <> 'skipped')"
            )
        elif review_status == "skipped":
            conditions.append("r.site_id IS NULL AND d.decision = 'skipped'")
        elif review_status == "resolved":
            conditions.append("r.site_id IS NOT NULL")

        rows = db.execute(
            f"SELECT r.id, r.{spec['ref']} AS record_ref, "
            f"r.{spec['location']} AS original_text, r.site_id AS current_site_id, "
            f"r.created_at, s.site_code AS current_site_code, "
            f"s.site_name AS current_site_name, d.decision, d.decision_note, "
            f"d.reviewed_at "
            f"FROM {table} r "
            f"LEFT JOIN sites s ON s.id = r.site_id AND s.org_id = r.org_id "
            f"LEFT JOIN site_resolution_decisions d "
            f"ON d.org_id = r.org_id AND d.record_type = %s AND d.record_id = r.id "
            f"WHERE {' AND '.join(conditions)} ORDER BY r.id DESC LIMIT %s",
            (*params, limit),
        ).fetchall()
        for row in rows:
            item = dict(row)
            item["record_type"] = record_type
            item["record_type_label"] = spec["label"]
            resolver = _resolve_from_sites(item.get("original_text"), sites)
            item["resolver"] = {
                "status": resolver["status"],
                "candidates": resolver["candidates"],
            }
            item["current_site"] = None
            if item.get("current_site_id") and item.get("current_site_name"):
                item["current_site"] = {
                    "id": item["current_site_id"],
                    "site_code": item["current_site_code"],
                    "site_name": item["current_site_name"],
                }
            records.append(item)

    records.sort(
        key=lambda item: (str(item.get("created_at") or ""), int(item["id"])),
        reverse=True,
    )
    linked = totals["total"] - totals["unlinked"]
    pending = totals["unlinked"] - totals["skipped"]
    eligible = {
        "pending": pending,
        "skipped": totals["skipped"],
        "resolved": linked,
        "all": totals["total"],
    }[review_status]
    records = records[:limit]
    for item in records:
        item.pop("created_at", None)
        item.pop("current_site_id", None)
        item.pop("current_site_code", None)
        item.pop("current_site_name", None)
    return {
        "records": records,
        "available_sites": sites,
        "counts": {
            "total": totals["total"],
            "linked": linked,
            "unlinked": totals["unlinked"],
            "pending": pending,
            "skipped": totals["skipped"],
        },
        "limit": limit,
        "truncated": eligible > len(records),
    }


def _spec(record_type: str) -> dict:
    spec = RECORD_SPECS.get(record_type)
    if spec is None:
        raise ValueError("invalid record type")
    return spec


def _require_actor(org_id: int | None, reviewed_by: int | None) -> tuple[int, int]:
    if not org_id or not reviewed_by:
        raise ValueError(RECORD_NOT_FOUND_MESSAGE)
    return int(org_id), int(reviewed_by)


def _begin_write(db) -> None:
    if not settings.is_postgres():
        db.execute("BEGIN IMMEDIATE")


def _record_for_update(db, record_type: str, record_id: int, org_id: int):
    spec = _spec(record_type)
    sql = (
        f"SELECT id, {spec['ref']} AS record_ref, "
        f"{spec['location']} AS original_text, site_id "
        f"FROM {spec['table']} WHERE id = %s AND org_id = %s"
    )
    if settings.is_postgres():
        sql += " FOR UPDATE"
    return db.execute(sql, (record_id, org_id)).fetchone()


def _active_site_for_update(db, site_id: int, org_id: int):
    sql = (
        "SELECT id, site_code, site_name, city, region FROM sites "
        "WHERE id = %s AND org_id = %s AND status = 'active'"
    )
    if settings.is_postgres():
        sql += " FOR SHARE"
    return db.execute(sql, (site_id, org_id)).fetchone()


def _upsert_decision(
    db,
    *,
    record_type: str,
    record_id: int,
    original_text: str | None,
    decision: str,
    resolved_site_id: int | None,
    decision_note: str,
    org_id: int,
    reviewed_by: int,
) -> None:
    reviewed_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO site_resolution_decisions "
        "(record_type, record_id, original_text, decision, resolved_site_id, "
        "decision_note, org_id, reviewed_by, reviewed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (org_id, record_type, record_id) DO UPDATE SET "
        "original_text = excluded.original_text, decision = excluded.decision, "
        "resolved_site_id = excluded.resolved_site_id, "
        "decision_note = excluded.decision_note, "
        "reviewed_by = excluded.reviewed_by, reviewed_at = excluded.reviewed_at",
        (record_type, record_id, original_text, decision, resolved_site_id,
         decision_note or None, org_id, reviewed_by, reviewed_at),
    )


def resolve_record(
    db,
    *,
    record_type: str,
    record_id: int,
    site_id: int,
    org_id: int | None,
    reviewed_by: int | None,
    decision_note: str = "",
) -> dict:
    """Atomically apply one reviewed active-site link."""
    spec = _spec(record_type)
    org_id, reviewed_by = _require_actor(org_id, reviewed_by)
    try:
        record_id = int(record_id)
        site_id = int(site_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(RECORD_NOT_FOUND_MESSAGE) from exc
    note = str(decision_note or "").strip()
    if len(note) > 500:
        raise ValueError("decision note is too long")

    _begin_write(db)
    try:
        record = _record_for_update(db, record_type, record_id, org_id)
        if record is None:
            db.rollback()
            return {"ok": False, "message": RECORD_NOT_FOUND_MESSAGE}
        if record["site_id"] is not None:
            db.rollback()
            return {"ok": False, "message": RECORD_ALREADY_LINKED_MESSAGE}
        site = _active_site_for_update(db, site_id, org_id)
        if site is None:
            db.rollback()
            return {"ok": False, "message": SITE_NOT_FOUND_MESSAGE}

        cursor = db.execute(
            f"UPDATE {spec['table']} SET site_id = %s "
            "WHERE id = %s AND org_id = %s AND site_id IS NULL",
            (site_id, record_id, org_id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return {"ok": False, "message": RECORD_ALREADY_LINKED_MESSAGE}

        _upsert_decision(
            db, record_type=record_type, record_id=record_id,
            original_text=record["original_text"], decision="resolved",
            resolved_site_id=site_id, decision_note=note, org_id=org_id,
            reviewed_by=reviewed_by,
        )
        log_audit(
            db, reviewed_by, org_id, "site_resolution.resolve", spec["table"],
            record_id,
            old_value={"site_id": None, "original_text": record["original_text"]},
            new_value={"site_id": site_id, "decision": "resolved",
                       "original_text": record["original_text"],
                       "decision_note": note or None},
            commit=False,
        )
        db.commit()
        return {
            "ok": True,
            "record_type": record_type,
            "record_id": record_id,
            "record_ref": record["record_ref"],
            "original_text": record["original_text"],
            "site": dict(site),
        }
    except Exception:
        db.rollback()
        raise


def skip_record(
    db,
    *,
    record_type: str,
    record_id: int,
    org_id: int | None,
    reviewed_by: int | None,
    decision_note: str = "",
) -> dict:
    """Persist a reviewed skip while leaving the authoritative link empty."""
    spec = _spec(record_type)
    org_id, reviewed_by = _require_actor(org_id, reviewed_by)
    note = str(decision_note or "").strip()
    if len(note) > 500:
        raise ValueError("decision note is too long")

    _begin_write(db)
    try:
        record = _record_for_update(db, record_type, int(record_id), org_id)
        if record is None:
            db.rollback()
            return {"ok": False, "message": RECORD_NOT_FOUND_MESSAGE}
        if record["site_id"] is not None:
            db.rollback()
            return {"ok": False, "message": RECORD_ALREADY_LINKED_MESSAGE}
        _upsert_decision(
            db, record_type=record_type, record_id=int(record_id),
            original_text=record["original_text"], decision="skipped",
            resolved_site_id=None, decision_note=note, org_id=org_id,
            reviewed_by=reviewed_by,
        )
        log_audit(
            db, reviewed_by, org_id, "site_resolution.skip", spec["table"],
            int(record_id),
            old_value={"site_id": None, "original_text": record["original_text"]},
            new_value={"site_id": None, "decision": "skipped",
                       "original_text": record["original_text"],
                       "decision_note": note or None},
            commit=False,
        )
        db.commit()
        return {"ok": True, "record_type": record_type,
                "record_id": int(record_id), "record_ref": record["record_ref"]}
    except Exception:
        db.rollback()
        raise


def _clean_text(value: object, field: str, *, maximum: int, required: bool) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise ValueError(f"{field} is required")
    if len(text) > maximum:
        raise ValueError(f"{field} is too long")
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise ValueError(f"{field} contains unsupported characters")
    return text


def create_site_and_resolve(
    db,
    *,
    record_type: str,
    record_id: int,
    site_code: str,
    site_name: str,
    city: str = "",
    region: str = "",
    site_type: str = "facility",
    org_id: int | None,
    reviewed_by: int | None,
) -> dict:
    """Create an unlocated active site and link it in one transaction."""
    spec = _spec(record_type)
    org_id, reviewed_by = _require_actor(org_id, reviewed_by)
    code = _clean_text(site_code, "site code", maximum=80, required=True)
    name = _clean_text(site_name, "site name", maximum=200, required=True)
    city = _clean_text(city, "city", maximum=120, required=False)
    region = _clean_text(region, "region", maximum=120, required=False)
    if site_type not in SITE_TYPES:
        raise ValueError("invalid site type")

    _begin_write(db)
    try:
        record = _record_for_update(db, record_type, int(record_id), org_id)
        if record is None:
            db.rollback()
            return {"ok": False, "message": RECORD_NOT_FOUND_MESSAGE}
        if record["site_id"] is not None:
            db.rollback()
            return {"ok": False, "message": RECORD_ALREADY_LINKED_MESSAGE}
        existing = db.execute(
            "SELECT id FROM sites WHERE org_id = %s AND site_code = %s",
            (org_id, code),
        ).fetchone()
        if existing is not None:
            db.rollback()
            raise ValueError("site code is already in use")
        try:
            db.execute(
                "INSERT INTO sites (site_code, site_name, city, region, site_type, "
                "status, org_id) VALUES (%s,%s,%s,%s,%s,'active',%s)",
                (code, name, city or None, region or None, site_type, org_id),
            )
        except Exception as exc:
            db.rollback()
            raise ValueError("site could not be created; choose another site code") from exc
        site = db.execute(
            "SELECT id, site_code, site_name, city, region FROM sites "
            "WHERE org_id = %s AND site_code = %s",
            (org_id, code),
        ).fetchone()
        if site is None:
            raise RuntimeError("created site could not be read")
        cursor = db.execute(
            f"UPDATE {spec['table']} SET site_id = %s "
            "WHERE id = %s AND org_id = %s AND site_id IS NULL",
            (site["id"], int(record_id), org_id),
        )
        if cursor.rowcount != 1:
            db.rollback()
            return {"ok": False, "message": RECORD_ALREADY_LINKED_MESSAGE}

        _upsert_decision(
            db, record_type=record_type, record_id=int(record_id),
            original_text=record["original_text"], decision="site_created",
            resolved_site_id=site["id"], decision_note="", org_id=org_id,
            reviewed_by=reviewed_by,
        )
        log_audit(
            db, reviewed_by, org_id, "site_resolution.create_site", "sites",
            site["id"], old_value=None,
            new_value={"site_code": code, "site_name": name, "city": city or None,
                       "region": region or None, "site_type": site_type,
                       "status": "active"},
            commit=False,
        )
        log_audit(
            db, reviewed_by, org_id, "site_resolution.resolve", spec["table"],
            int(record_id),
            old_value={"site_id": None, "original_text": record["original_text"]},
            new_value={"site_id": site["id"], "decision": "site_created",
                       "original_text": record["original_text"]},
            commit=False,
        )
        db.commit()
        return {
            "ok": True,
            "record_type": record_type,
            "record_id": int(record_id),
            "record_ref": record["record_ref"],
            "original_text": record["original_text"],
            "site": dict(site),
        }
    except Exception:
        db.rollback()
        raise
