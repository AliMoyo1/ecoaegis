"""Chemicals / SDS register data service (competitor benchmark gap #7).

Inventory of chemicals with hazard classes, CAS numbers, storage locations
and SDS document references. Hazard-class alerts: corrosive/flammable/toxic
chemicals flagged for the emergency module and inspections.
"""
from __future__ import annotations

from datetime import datetime, timezone

import json

from sheplatform.core.audit import log_audit
from sheplatform.database import resolve_org

HAZARD_CLASSES = ("flammable", "corrosive", "toxic", "oxidising", "explosive",
                  "environmental_hazard", "irritant", "compressed_gas")


def _next_ref(db) -> str:
    row = db.execute("SELECT chem_ref FROM chemicals ORDER BY id DESC LIMIT 1").fetchone()
    seq = int(row["chem_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"CHEM-{seq:03d}"


def create_chemical(db, *, name: str, cas_number: str = "", supplier: str = "",
                    hazard_class: str = "", pictogram: str = "", sds_path: str = "",
                    sds_attachment_id: int | None = None, sds_review_date: str | None = None,
                    sds_status: str = "current", sds_extracted: dict | None = None,
                    quantity_units: str = "", storage_location: str = "",
                    site_id: int | None = None, created_by: int,
                    org_id: int | None = None) -> dict:
    org_id = resolve_org(db, org_id, created_by)
    if hazard_class and hazard_class not in HAZARD_CLASSES:
        raise ValueError("invalid hazard_class")
    if sds_status not in ("current", "expiring", "expired", "draft"):
        raise ValueError("invalid sds_status")
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO chemicals (chem_ref, name, cas_number, supplier, hazard_class, "
        "pictogram, sds_path, sds_attachment_id, sds_review_date, sds_status, "
        "sds_extracted, quantity_units, storage_location, site_id, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (ref, name, cas_number or None, supplier or None, hazard_class or None,
         pictogram or None, sds_path or None, sds_attachment_id, sds_review_date, sds_status,
         json.dumps(sds_extracted or {}), quantity_units or None,
         storage_location or None, site_id, org_id, created_by))
    db.commit()
    log_audit(db, created_by, org_id, "chemical.created", "chemicals", ref,
              new_value={"name": name, "hazard_class": hazard_class})
    return dict(db.execute("SELECT * FROM chemicals WHERE chem_ref = %s", (ref,)).fetchone())


def list_chemicals(db, hazard_class: str | None = None, site_id: int | None = None,
                   org_id: int | None = None) -> list[dict]:
    sql = ("SELECT c.*, s.site_name FROM chemicals c "
           "LEFT JOIN sites s ON s.id = c.site_id")
    conds, params = [], []
    if hazard_class:
        conds.append("c.hazard_class = %s")
        params.append(hazard_class)
    if site_id:
        conds.append("c.site_id = %s")
        params.append(site_id)
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    conds.append("c.org_id = %s")
    params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY c.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def hazard_summary(db, org_id: int | None = None) -> dict:
    """Counts by hazard class for the dashboard/emergency readiness."""
    if not org_id:
        return {"total": 0}
    rows = db.execute(
        "SELECT hazard_class, COUNT(*) AS n FROM chemicals "
        "WHERE hazard_class IS NOT NULL AND org_id = %s GROUP BY hazard_class",
        (org_id,)).fetchall()
    summary = {r["hazard_class"]: r["n"] for r in rows}
    summary["total"] = db.execute(
        "SELECT COUNT(*) FROM chemicals WHERE org_id = %s", (org_id,)).fetchone()[0]
    return summary


def update_chemical(db, chemical_id: int, *, org_id: int, user_id: int,
                    **fields) -> dict | None:
    """Update a chemical record. Reserved SDS fields should be set explicitly."""
    allowed = {
        "name", "cas_number", "supplier", "hazard_class", "pictogram", "sds_path",
        "sds_attachment_id", "sds_review_date", "sds_status", "sds_extracted",
        "quantity_units", "storage_location", "site_id",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return None
    if "hazard_class" in updates and updates["hazard_class"] not in ("", None) and updates["hazard_class"] not in HAZARD_CLASSES:
        raise ValueError("invalid hazard_class")
    if "sds_status" in updates and updates["sds_status"] not in ("current", "expiring", "expired", "draft"):
        raise ValueError("invalid sds_status")

    # JSON-encode sds_extracted if passed as dict
    if "sds_extracted" in updates and isinstance(updates["sds_extracted"], dict):
        updates["sds_extracted"] = json.dumps(updates["sds_extracted"])

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    params = list(updates.values())
    params.extend([chemical_id, org_id])
    db.execute(f"UPDATE chemicals SET {set_clause} WHERE id = %s AND org_id = %s", params)
    db.commit()
    row = db.execute("SELECT * FROM chemicals WHERE id = %s AND org_id = %s",
                     (chemical_id, org_id)).fetchone()
    if row:
        log_audit(db, user_id, org_id, "chemical.updated", "chemicals", row["chem_ref"],
                  new_value={k: v for k, v in updates.items() if k != "updated_at"})
    return dict(row) if row else None


def check_sds_review_dates(db, org_id: int | None = None, horizon_days: int = 30) -> list[dict]:
    """Return chemicals whose SDS review date is within horizon_days or already past.
    Used by the scheduler.
    """
    if not org_id:
        return []
    from datetime import timedelta
    horizon = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).isoformat()
    rows = db.execute(
        "SELECT * FROM chemicals WHERE org_id = %s AND sds_review_date IS NOT NULL "
        "AND sds_review_date <= %s AND sds_status != 'expired'",
        (org_id, horizon)).fetchall()
    return [dict(r) for r in rows]
