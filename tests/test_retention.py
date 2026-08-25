"""NFR-SHE-003: configurable per-record-type retention + disposal guard."""
from __future__ import annotations

import pytest

from sheplatform.core import retention


class TestRetentionPolicy:
    def test_default_is_seven_years(self, db):
        assert retention.get_retention_policy(db, "incident") == 7

    def test_set_and_get_policy(self, db):
        res = retention.set_retention_policy(db, "incident", 10, updated_by=None)
        assert res["ok"] is True
        assert retention.get_retention_policy(db, "incident") == 10

    def test_cannot_set_below_statutory_minimum(self, db):
        res = retention.set_retention_policy(db, "incident", 5)
        assert res["ok"] is False
        assert "minimum" in res["message"]
        assert retention.get_retention_policy(db, "incident") == 7  # unchanged

    def test_unknown_record_type_rejected(self, db):
        res = retention.set_retention_policy(db, "nonsense", 8)
        assert res["ok"] is False


class TestRetentionGuard:
    def test_old_record_is_expired(self, db):
        assert retention.is_retention_expired(db, "incident", "2010-01-01T00:00:00") is True

    def test_recent_record_not_expired(self, db):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        assert retention.is_retention_expired(db, "incident", now) is False

    def test_guard_blocks_delete_within_retention(self, db):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with pytest.raises(PermissionError):
            retention.assert_retention_allows_delete(db, "incident", now)

    def test_guard_allows_delete_after_retention(self, db):
        # no raise
        retention.assert_retention_allows_delete(db, "incident", "2010-01-01T00:00:00")


class TestRetentionReport:
    def test_report_shape_and_disposal_counting(self, db):
        # an audit row created well past retention is disposal-eligible
        db.execute("INSERT INTO audit_log (action, created_at) VALUES ('old.event', '2010-01-01T00:00:00')")
        db.commit()
        report = retention.retention_report(db)
        assert set(retention.RECORD_TABLES) <= set(report)
        assert report["incident"]["retention_years"] == 7
        assert report["audit"]["total"] >= 1
        assert report["audit"]["disposal_eligible"] >= 1


class TestRetentionHttp:
    def _admin(self, db, email, role="super_admin"):
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, 'T', 'U', %s, 1)", (email, hash_password("Test1234!"), role))
        db.commit()

    def test_super_admin_report_and_set(self, client, db):
        self._admin(db, "retadmin@test.com")
        client.post("/login", data={"email": "retadmin@test.com", "password": "Test1234!"})
        csrf = client.cookies.get("she_csrf", "")
        assert client.get("/admin/api/retention").status_code == 200
        set_resp = client.post("/admin/api/retention",
                               data={"record_type": "permit", "retention_years": 9},
                               headers={"X-CSRF-Token": csrf})
        assert set_resp.status_code == 200, set_resp.text
        assert retention.get_retention_policy(db, "permit") == 9

    def test_below_minimum_rejected_over_http(self, client, db):
        self._admin(db, "retadmin2@test.com")
        client.post("/login", data={"email": "retadmin2@test.com", "password": "Test1234!"})
        csrf = client.cookies.get("she_csrf", "")
        resp = client.post("/admin/api/retention",
                           data={"record_type": "permit", "retention_years": 3},
                           headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 400

    def test_non_admin_forbidden(self, client, db):
        self._admin(db, "retofficer@test.com", role="she_officer")
        client.post("/login", data={"email": "retofficer@test.com", "password": "Test1234!"})
        assert client.get("/admin/api/retention").status_code == 403
