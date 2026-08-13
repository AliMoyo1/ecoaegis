"""Contractor readiness tests: readiness gate, induction, PTW gate."""
from __future__ import annotations

import pytest


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key) "
        "VALUES (%s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _mk_vendor(db, officer, insurance="2099-01-01T00:00:00+00:00", status="active"):
    db.execute(
        "INSERT INTO vendors (vendor_ref, company_name, contact_person, email, "
        "insurance_expiry, status, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)",
        (f"VD-TEST-{officer['id']}", "Test Contractors", "J. Moyo",
         "tc@test.com", insurance, status, officer["id"]))
    db.commit()
    return dict(db.execute("SELECT * FROM vendors ORDER BY id DESC LIMIT 1").fetchone())


def _mk_site(db):
    db.execute("INSERT INTO sites (site_code, site_name, city) VALUES (%s, %s, %s)",
               ("SITE-1", "Harare HQ", "Harare"))
    db.commit()
    return dict(db.execute("SELECT * FROM sites ORDER BY id DESC LIMIT 1").fetchone())


class TestContractorReadiness:
    def test_not_ready_without_induction(self, db):
        officer = _mk_user(db, "she_officer", "ctr1@test.com")
        vendor = _mk_vendor(db, officer)
        site = _mk_site(db)

        from sheplatform.modules.contractors.data_service import site_readiness
        status = site_readiness(db, vendor["id"], site["id"])
        assert status["ready"] is False
        assert any("induction" in r for r in status["reasons"])

    def test_ready_after_induction(self, db):
        officer = _mk_user(db, "she_officer", "ctr2@test.com")
        vendor = _mk_vendor(db, officer)
        site = _mk_site(db)

        from sheplatform.modules.contractors.data_service import record_induction, site_readiness
        record_induction(db, vendor_id=vendor["id"], site_id=site["id"],
                         induction_type="site_specific",
                         valid_until="2099-12-31T00:00:00+00:00", trainer_id=officer["id"])
        status = site_readiness(db, vendor["id"], site["id"])
        assert status["ready"] is True

    def test_not_ready_with_expired_insurance(self, db):
        officer = _mk_user(db, "she_officer", "ctr3@test.com")
        vendor = _mk_vendor(db, officer, insurance="2000-01-01T00:00:00+00:00")
        site = _mk_site(db)

        from sheplatform.modules.contractors.data_service import record_induction, site_readiness
        record_induction(db, vendor_id=vendor["id"], site_id=site["id"],
                         induction_type="general", valid_until="2099-12-31T00:00:00+00:00",
                         trainer_id=officer["id"])
        status = site_readiness(db, vendor["id"], site["id"])
        assert status["ready"] is False
        assert any("insurance" in r for r in status["reasons"])

    def test_expired_induction_not_ready(self, db):
        officer = _mk_user(db, "she_officer", "ctr4@test.com")
        vendor = _mk_vendor(db, officer)
        site = _mk_site(db)

        from sheplatform.modules.contractors.data_service import record_induction, site_readiness
        record_induction(db, vendor_id=vendor["id"], site_id=site["id"],
                         induction_type="general", valid_until="2000-12-31T00:00:00+00:00",
                         trainer_id=officer["id"])
        status = site_readiness(db, vendor["id"], site["id"])
        assert status["ready"] is False

    def test_ptw_gate(self, db):
        officer = _mk_user(db, "she_officer", "ctr5@test.com")
        vendor = _mk_vendor(db, officer)
        site = _mk_site(db)

        from sheplatform.modules.contractors.data_service import ensure_ptw_eligible
        ok, reason = ensure_ptw_eligible(db, vendor["id"], site["id"])
        assert ok is False
        assert "induction" in reason

        from sheplatform.modules.contractors.data_service import record_induction
        record_induction(db, vendor_id=vendor["id"], site_id=site["id"],
                         induction_type="site_specific",
                         valid_until="2099-12-31T00:00:00+00:00", trainer_id=officer["id"])
        ok, reason = ensure_ptw_eligible(db, vendor["id"], site["id"])
        assert ok is True
        assert reason == "site-ready"
