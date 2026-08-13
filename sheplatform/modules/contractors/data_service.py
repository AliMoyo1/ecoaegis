"""Contractor site-readiness (competitor benchmark gap #6).

A contractor is site-ready when:
  1. Active vendor record (not suspended/offboarded)
  2. Valid insurance (not expired)
  3. Valid certifications (no expired certs)
  4. Valid site induction for the target site (not expired/pending)

Site-ready is required before PTW issuance (extends BRN-SHE-001 gate).
"""
from __future__ import annotations

from datetime import datetime, timezone


def record_induction(db, *, vendor_id: int, site_id: int, induction_type: str,
                     valid_until: str, trainer_id: int, notes: str = "") -> dict:
    if induction_type not in ("general", "site_specific", "high_risk", "refresher"):
        raise ValueError("invalid induction_type")
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO contractor_inductions (vendor_id, site_id, induction_date, "
        "induction_type, valid_until, trainer_id, status, notes) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'valid', %s)",
        (vendor_id, site_id, now, induction_type, valid_until or None, trainer_id, notes))
    db.commit()
    row = db.execute("SELECT * FROM contractor_inductions ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row)


def site_readiness(db, vendor_id: int, site_id: int | None = None) -> dict:
    """Compute readiness status for a vendor (optionally per site)."""
    vendor = db.execute("SELECT * FROM vendors WHERE id = %s", (vendor_id,)).fetchone()
    if not vendor:
        return {"ready": False, "reasons": ["vendor not found"]}
    vendor = dict(vendor)
    now = datetime.now(timezone.utc).isoformat()
    reasons = []

    if vendor["status"] != "active":
        reasons.append(f"vendor status is '{vendor['status']}'")

    if vendor.get("insurance_expiry"):
        if vendor["insurance_expiry"] < now:
            reasons.append("insurance expired")
    else:
        reasons.append("no insurance expiry recorded")

    cert_rows = db.execute(
        "SELECT cert_name, expiry_date FROM vendor_certifications "
        "WHERE vendor_id = %s AND expiry_date < %s AND status != 'expired'",
        (vendor_id, now)).fetchall()
    if cert_rows:
        reasons.append(f"{len(cert_rows)} expired certification(s)")

    if site_id:
        ind = db.execute(
            "SELECT * FROM contractor_inductions WHERE vendor_id = %s AND site_id = %s "
            "ORDER BY id DESC LIMIT 1", (vendor_id, site_id)).fetchone()
        if not ind:
            reasons.append("no induction recorded for this site")
        elif ind["status"] != "valid" or (ind["valid_until"] and ind["valid_until"] < now):
            reasons.append("site induction expired or invalid")

    return {"ready": len(reasons) == 0, "reasons": reasons}


def list_inductions(db, vendor_id: int | None = None) -> list[dict]:
    sql = ("SELECT ci.*, v.company_name AS vendor_name, s.site_name AS site_name, "
           "u.email AS trainer_email FROM contractor_inductions ci "
           "LEFT JOIN vendors v ON v.id = ci.vendor_id "
           "LEFT JOIN sites s ON s.id = ci.site_id "
           "LEFT JOIN users u ON u.id = ci.trainer_id")
    if vendor_id:
        sql += " WHERE ci.vendor_id = %s"
        return [dict(r) for r in db.execute(sql, (vendor_id,)).fetchall()]
    return [dict(r) for r in db.execute(sql).fetchall()]


def ensure_ptw_eligible(db, vendor_id: int, site_id: int | None = None) -> tuple[bool, str]:
    """PTW gate: site-ready contractors only (extends BRN-SHE-001)."""
    status = site_readiness(db, vendor_id, site_id)
    if not status["ready"]:
        return False, "; ".join(status["reasons"])
    return True, "site-ready"
