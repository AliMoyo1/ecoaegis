"""SHECMV - Vendor Compliance data service (guide 10, Module 4).

Business rules:
- BRN-SHE-013: 30-day certification expiry alert; auto-suspend PTW eligibility on lapse
- FNR-SHE-006: vendor.onboarding event -> Procurement gateway (webhook)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sheplatform.core import events


def next_vendor_ref(db) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"VD-{year}-"
    row = db.execute(
        "SELECT vendor_ref FROM vendors WHERE vendor_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        return f"{prefix}001"
    m = re.search(r"(\d+)$", row["vendor_ref"])
    return f"{prefix}{(int(m.group(1)) if m else 0) + 1:03d}"


def create_vendor(db, *, company_name: str, contact_person: str = "", email: str = "",
                  phone: str = "", risk_profile: str = "medium",
                  created_by: int | None = None, org_id: int | None = None) -> dict:
    ref = next_vendor_ref(db)
    db.execute(
        "INSERT INTO vendors (vendor_ref, company_name, contact_person, email, phone, "
        "risk_profile, ptw_eligible, certification_status, created_by, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ref, company_name, contact_person, email, phone, risk_profile, True, "valid",
         created_by, org_id),
    )
    db.commit()
    vendor = get_vendor_by_ref(db, ref)
    events.emit("vendor.onboarding", {
        "vendor_id": vendor["id"], "vendor_ref": ref, "company_name": company_name,
        "risk_profile": risk_profile, "org_id": org_id,
        "entity_type": "vendor", "entity_id": vendor["id"],
    }, db, user_id=created_by, source_module="vendor_compliance")
    return vendor


def get_vendor(db, vendor_id: int) -> dict | None:
    row = db.execute("SELECT * FROM vendors WHERE id = %s", (vendor_id,)).fetchone()
    return dict(row) if row else None


def get_vendor_by_ref(db, ref: str) -> dict | None:
    row = db.execute("SELECT * FROM vendors WHERE vendor_ref = %s", (ref,)).fetchone()
    return dict(row) if row else None


def list_vendors(db, status: str | None = None, org_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM vendors"
    conds, params = [], []
    if status:
        conds.append("status = %s")
        params.append(status)
    if org_id:
        conds.append("org_id = %s")
        params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def add_certification(db, *, vendor_id: int, cert_name: str, expiry_date: str,
                      cert_number: str = "", issued_date: str = "",
                      document_path: str = "") -> dict:
    db.execute(
        "INSERT INTO vendor_certifications (vendor_id, cert_name, cert_number, issued_date, "
        "expiry_date, document_path) VALUES (%s,%s,%s,%s,%s,%s)",
        (vendor_id, cert_name, cert_number, issued_date or None, expiry_date, document_path),
    )
    db.commit()
    row = db.execute(
        "SELECT * FROM vendor_certifications WHERE vendor_id = %s ORDER BY id DESC LIMIT 1",
        (vendor_id,),
    ).fetchone()
    return dict(row)


def list_certifications(db, vendor_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM vendor_certifications WHERE vendor_id = %s ORDER BY expiry_date",
        (vendor_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _refresh_vendor_cert_status(db, vendor_id: int) -> str:
    """Recompute vendor.certification_status and ptw_eligible from certs (BRN-013)."""
    certs = list_certifications(db, vendor_id)
    now = datetime.now(timezone.utc)
    if not certs:
        return "valid"
    # Any expired cert -> suspended
    for c in certs:
        exp = datetime.fromisoformat(c["expiry_date"].replace("Z", "+00:00"))
        if exp < now:
            db.execute(
                "UPDATE vendors SET certification_status = 'suspended', ptw_eligible = 0 "
                "WHERE id = %s", (vendor_id,))
            db.commit()
            return "suspended"
    # Any cert expiring within 30 days -> expiring
    for c in certs:
        exp = datetime.fromisoformat(c["expiry_date"].replace("Z", "+00:00"))
        if exp < now + timedelta(days=30):
            db.execute(
                "UPDATE vendors SET certification_status = 'expiring', ptw_eligible = 1 "
                "WHERE id = %s", (vendor_id,))
            db.commit()
            return "expiring"
    db.execute(
        "UPDATE vendors SET certification_status = 'valid', ptw_eligible = 1 WHERE id = %s",
        (vendor_id,))
    db.commit()
    return "valid"


def check_certification_expiry(db) -> list[dict]:
    """Scheduler: find certs expiring within 30 days (alert) and expired (suspend)."""
    from sheplatform.core.notifications import notify_roles

    now = datetime.now(timezone.utc)
    expiring_soon = now + timedelta(days=30)
    alerts = []
    rows = db.execute(
        "SELECT vc.*, v.company_name, v.id AS vendor_id "
        "FROM vendor_certifications vc JOIN vendors v ON v.id = vc.vendor_id "
        "WHERE vc.expiry_date <= %s AND vc.expiry_date > %s AND vc.status != 'expired'",
        (expiring_soon.isoformat(), now.isoformat()),
    ).fetchall()
    for r in rows:
        cert = dict(r)
        db.execute("UPDATE vendor_certifications SET status = 'expiring' WHERE id = %s", (cert["id"],))
        _refresh_vendor_cert_status(db, cert["vendor_id"])
        notify_roles(db, ["she_officer", "she_manager"],
                     f"Certification expiring: {cert['company_name']}",
                     f"{cert['cert_name']} expires {cert['expiry_date']}.")
        alerts.append(cert)

    expired = db.execute(
        "SELECT vc.*, v.company_name, v.id AS vendor_id "
        "FROM vendor_certifications vc JOIN vendors v ON v.id = vc.vendor_id "
        "WHERE vc.expiry_date <= %s AND vc.status != 'expired'",
        (now.isoformat(),),
    ).fetchall()
    for r in expired:
        cert = dict(r)
        db.execute("UPDATE vendor_certifications SET status = 'expired' WHERE id = %s", (cert["id"],))
        _refresh_vendor_cert_status(db, cert["vendor_id"])  # suspends ptw_eligible (BRN-013)
        notify_roles(db, ["she_manager", "cro"],
                     f"CERTIFICATION EXPIRED: {cert['company_name']}",
                     f"{cert['cert_name']} expired {cert['expiry_date']}. PTW eligibility suspended.")
        alerts.append(cert)
    if alerts:
        db.commit()
    return alerts
