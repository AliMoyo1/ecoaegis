"""Controlled, tenant-scoped CSV imports for authoritative site coordinates."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import logging
import uuid

from sheplatform.core.audit import log_audit
from sheplatform.modules.map import data_service


MAX_IMPORT_BYTES = 1_048_576
MAX_IMPORT_ROWS = 2_000
REQUIRED_COLUMNS = frozenset({"site_code", "latitude", "longitude"})
logger = logging.getLogger(__name__)


def _active_actor(db, created_by: int, org_id: int | None):
    if not org_id or not created_by:
        return None
    return db.execute(
        "SELECT id FROM users WHERE id = %s AND org_id = %s AND is_active = TRUE",
        (created_by, org_id),
    ).fetchone()


def _batch_with_rows(db, import_id: int, org_id: int | None) -> dict | None:
    if not org_id:
        return None
    batch = db.execute(
        "SELECT * FROM site_coordinate_imports WHERE id = %s AND org_id = %s",
        (import_id, org_id),
    ).fetchone()
    if batch is None:
        return None
    rows = db.execute(
        "SELECT row_number, site_id, site_code, latitude, longitude, "
        "coordinate_accuracy_m, status, error FROM site_coordinate_import_rows "
        "WHERE import_id = %s ORDER BY row_number",
        (import_id,),
    ).fetchall()
    return {"batch": dict(batch), "rows": [dict(row) for row in rows]}


def preview_coordinate_import(db, *, file_bytes: bytes, org_id: int | None,
                              created_by: int) -> dict:
    """Validate and persist an inert preview. This function never changes sites."""
    if _active_actor(db, created_by, org_id) is None:
        raise ValueError("organisation or actor is not available")
    if not file_bytes:
        raise ValueError("CSV file is empty")
    if len(file_bytes) > MAX_IMPORT_BYTES:
        raise ValueError("CSV file exceeds the 1 MB limit")
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV file must use UTF-8 encoding") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames:
        raise ValueError("CSV header is missing")
    header_map = {name.strip().lower(): name for name in reader.fieldnames if name}
    missing = REQUIRED_COLUMNS - set(header_map)
    if missing:
        raise ValueError("CSV is missing required columns: " + ", ".join(sorted(missing)))

    raw_rows = list(reader)
    if not raw_rows:
        raise ValueError("CSV contains no data rows")
    if len(raw_rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"CSV contains more than {MAX_IMPORT_ROWS} rows")

    site_rows = db.execute(
        "SELECT id, site_code, latitude, longitude FROM sites WHERE org_id = %s",
        (org_id,),
    ).fetchall()
    sites = {row["site_code"]: dict(row) for row in site_rows}
    seen_codes: set[str] = set()
    preview_rows: list[dict] = []

    for row_number, raw in enumerate(raw_rows, start=2):
        site_code = (raw.get(header_map["site_code"]) or "").strip()
        errors: list[str] = []
        site = sites.get(site_code)
        if not site_code:
            errors.append("site_code is required")
        elif site_code in seen_codes:
            errors.append("duplicate site_code in this file")
        else:
            seen_codes.add(site_code)
        if site_code and site is None:
            errors.append("site_code was not found in this organisation")

        latitude = longitude = accuracy = None
        try:
            latitude, longitude = data_service.validate_coordinates(
                raw.get(header_map["latitude"]), raw.get(header_map["longitude"]))
        except ValueError as exc:
            errors.append(str(exc))
        accuracy_header = header_map.get("accuracy_m")
        try:
            accuracy = data_service.validate_accuracy(
                raw.get(accuracy_header) if accuracy_header else None)
        except ValueError as exc:
            errors.append(str(exc))

        if errors:
            status = "invalid"
        elif site["latitude"] is not None or site["longitude"] is not None:
            status = "conflict"
        else:
            status = "valid"
        preview_rows.append({
            "row_number": row_number,
            "site_id": site["id"] if site else None,
            "site_code": site_code,
            "latitude": latitude,
            "longitude": longitude,
            "coordinate_accuracy_m": accuracy,
            "status": status,
            "error": "; ".join(errors) or None,
        })

    counts = {
        "total_rows": len(preview_rows),
        "valid_rows": sum(row["status"] == "valid" for row in preview_rows),
        "invalid_rows": sum(row["status"] == "invalid" for row in preview_rows),
        "conflict_rows": sum(row["status"] == "conflict" for row in preview_rows),
    }
    batch_ref = str(uuid.uuid4())
    db.execute(
        "INSERT INTO site_coordinate_imports (batch_ref, status, total_rows, valid_rows, "
        "invalid_rows, conflict_rows, org_id, created_by) "
        "VALUES (%s,'previewed',%s,%s,%s,%s,%s,%s)",
        (batch_ref, counts["total_rows"], counts["valid_rows"], counts["invalid_rows"],
         counts["conflict_rows"], org_id, created_by),
    )
    batch = db.execute(
        "SELECT * FROM site_coordinate_imports WHERE batch_ref = %s AND org_id = %s",
        (batch_ref, org_id),
    ).fetchone()
    import_id = batch["id"]
    for row in preview_rows:
        db.execute(
            "INSERT INTO site_coordinate_import_rows (import_id, row_number, site_id, "
            "site_code, latitude, longitude, coordinate_accuracy_m, status, error) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (import_id, row["row_number"], row["site_id"], row["site_code"],
             row["latitude"], row["longitude"], row["coordinate_accuracy_m"],
             row["status"], row["error"]),
        )
    log_audit(
        db, created_by, org_id, "site_coords.import_preview", "site_coordinate_imports",
        import_id, new_value={"batch_ref": batch_ref, **counts})
    try:
        data_service.record_map_metric(
            db, event_type="import_preview", org_id=org_id,
            feature_count=counts["total_rows"])
    except Exception:
        db.rollback()
        logger.warning("Private map metric recording failed after import preview", exc_info=True)
    return {"ok": True, "batch": dict(batch), "rows": preview_rows}


def get_coordinate_import(db, *, import_id: int, org_id: int | None) -> dict:
    result = _batch_with_rows(db, import_id, org_id)
    if result is None:
        return {"ok": False, "message": "coordinate import not found"}
    return {"ok": True, **result}


def commit_coordinate_import(db, *, import_id: int, org_id: int | None,
                             updated_by: int, overwrite_existing: bool) -> dict:
    """Atomically apply a clean preview; conflicts require explicit approval."""
    if _active_actor(db, updated_by, org_id) is None:
        return {"ok": False, "message": "coordinate import not found"}
    result = _batch_with_rows(db, import_id, org_id)
    if result is None:
        return {"ok": False, "message": "coordinate import not found"}
    batch, rows = result["batch"], result["rows"]
    if batch["status"] != "previewed":
        return {"ok": False, "message": "coordinate import has already been committed"}
    if batch["invalid_rows"]:
        return {
            "ok": False,
            "message": "correct invalid rows and upload a new preview before committing",
            "invalid_rows": batch["invalid_rows"],
        }
    if batch["conflict_rows"] and not overwrite_existing:
        return {
            "ok": False,
            "message": "explicit overwrite approval is required for existing coordinates",
            "requires_overwrite": True,
            "conflict_rows": batch["conflict_rows"],
        }

    try:
        updated = 0
        for row in rows:
            # The preview's valid/conflict classification is frozen at preview
            # time. A row previewed as "valid" (no existing coordinates) can
            # gain coordinates from a different admin/import while this batch
            # sits uncommitted (previews have no expiry); re-check the site's
            # *current* state so that case is treated as an unreviewed
            # conflict instead of a silent overwrite. A row already flagged
            # "conflict" at preview time keeps the approval the caller gave.
            current = db.execute(
                "SELECT latitude, longitude FROM sites WHERE id = %s AND org_id = %s",
                (row["site_id"], org_id),
            ).fetchone()
            if current is None:
                raise ValueError("a previewed site is no longer available")
            now_has_coords = current["latitude"] is not None or current["longitude"] is not None
            if row["status"] == "valid" and now_has_coords:
                raise ValueError(
                    "a previewed site gained coordinates after this preview was "
                    "taken; generate a new preview before committing")
            applied = data_service.set_site_coords(
                db, site_id=row["site_id"], latitude=row["latitude"],
                longitude=row["longitude"], source="imported",
                accuracy_m=row["coordinate_accuracy_m"], updated_by=updated_by,
                org_id=org_id, commit=False,
                require_unlocated=row["status"] == "valid")
            if not applied["ok"]:
                if applied.get("message") == "site coordinates changed":
                    raise ValueError(
                        "a previewed site gained coordinates after this preview was "
                        "taken; generate a new preview before committing")
                raise ValueError("a previewed site is no longer available")
            updated += 1
        committed_at = datetime.now(timezone.utc).isoformat()
        claimed = db.execute(
            "UPDATE site_coordinate_imports SET status = 'committed', "
            "overwrite_approved = %s, committed_at = %s "
            "WHERE id = %s AND org_id = %s AND status = 'previewed'",
            (bool(overwrite_existing), committed_at, import_id, org_id),
        )
        if claimed.rowcount != 1:
            raise ValueError("coordinate import has already been committed")
        log_audit(
            db, updated_by, org_id, "site_coords.import_commit", "site_coordinate_imports",
            import_id, old_value={"status": "previewed"},
            new_value={"status": "committed", "updated_sites": updated,
                       "overwrite_approved": bool(overwrite_existing)}, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    try:
        data_service.record_map_metric(
            db, event_type="import_commit", org_id=org_id,
            feature_count=updated)
    except Exception:
        db.rollback()
        logger.warning("Private map metric recording failed after import commit", exc_info=True)
    return {
        "ok": True,
        "import_id": import_id,
        "updated_sites": updated,
        "overwrite_approved": bool(overwrite_existing),
    }
