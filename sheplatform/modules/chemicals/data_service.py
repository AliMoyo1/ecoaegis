"""Chemicals / SDS register data service (competitor benchmark gap #7).

Inventory of chemicals with hazard classes, CAS numbers, storage locations
and SDS document references. Hazard-class alerts: corrosive/flammable/toxic
chemicals flagged for the emergency module and inspections.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core.audit import log_audit

HAZARD_CLASSES = ("flammable", "corrosive", "toxic", "oxidising", "explosive",
                  "environmental_hazard", "irritant", "compressed_gas")


def _next_ref(db) -> str:
    row = db.execute("SELECT chem_ref FROM chemicals ORDER BY id DESC LIMIT 1").fetchone()
    seq = int(row["chem_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"CHEM-{seq:03d}"


def create_chemical(db, *, name: str, cas_number: str = "", supplier: str = "",
                    hazard_class: str = "", pictogram: str = "", sds_path: str = "",
                    quantity_units: str = "", storage_location: str = "",
                    site_id: int | None = None, created_by: int,
                    org_id: int | None = None) -> dict:
    if hazard_class and hazard_class not in HAZARD_CLASSES:
        raise ValueError("invalid hazard_class")
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO chemicals (chem_ref, name, cas_number, supplier, hazard_class, "
        "pictogram, sds_path, quantity_units, storage_location, site_id, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (ref, name, cas_number or None, supplier or None, hazard_class or None,
         pictogram or None, sds_path or None, quantity_units or None,
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
    if org_id:
        conds.append("c.org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY c.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def hazard_summary(db, org_id: int | None = None) -> dict:
    """Counts by hazard class for the dashboard/emergency readiness."""
    rows = db.execute(
        "SELECT hazard_class, COUNT(*) AS n FROM chemicals "
        "WHERE hazard_class IS NOT NULL GROUP BY hazard_class").fetchall()
    summary = {r["hazard_class"]: r["n"] for r in rows}
    summary["total"] = db.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0]
    return summary
