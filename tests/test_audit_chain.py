"""NFR-SHE-004: tamper-evident audit log (hash chain + verification)."""
from __future__ import annotations

from sheplatform.core.audit import log_audit, verify_audit_chain


def _log_a_few(db):
    log_audit(db, 1, 1, "incident.create", "incidents", 10, new_value={"ref": "INC-1"})
    log_audit(db, 1, 1, "incident.close", "incidents", 10, old_value={"status": "open"},
              new_value={"status": "closed"})
    log_audit(db, 2, 1, "report.approve", "reports", 5, new_value={"decision": "approved"})


class TestAuditChain:
    def test_intact_chain_verifies(self, db):
        _log_a_few(db)
        result = verify_audit_chain(db)
        assert result["ok"] is True
        assert result["checked"] == 3
        assert result["first_break"] is None

    def test_edited_row_is_detected(self, db):
        _log_a_few(db)
        # tamper: rewrite a past entry's action directly in the DB (out of band)
        target = db.execute(
            "SELECT id FROM audit_log WHERE action = 'incident.close'").fetchone()["id"]
        db.execute("UPDATE audit_log SET action = 'incident.reopen' WHERE id = %s", (target,))
        db.commit()
        result = verify_audit_chain(db)
        assert result["ok"] is False
        assert result["first_break"]["id"] == target
        assert "content hash" in result["first_break"]["reason"]

    def test_deleted_row_breaks_the_chain(self, db):
        _log_a_few(db)
        # tamper: delete a middle entry to remove evidence
        rows = db.execute("SELECT id FROM audit_log ORDER BY id ASC").fetchall()
        middle = rows[1]["id"]
        db.execute("DELETE FROM audit_log WHERE id = %s", (middle,))
        db.commit()
        result = verify_audit_chain(db)
        assert result["ok"] is False
        assert "chain link" in result["first_break"]["reason"]

    def test_altered_json_value_is_detected(self, db):
        _log_a_few(db)
        target = db.execute(
            "SELECT id FROM audit_log WHERE action = 'incident.close'").fetchone()["id"]
        # flip the recorded new_value - the classic "cover your tracks" edit
        db.execute("UPDATE audit_log SET new_value = %s WHERE id = %s",
                   ('{"status": "open"}', target))
        db.commit()
        result = verify_audit_chain(db)
        assert result["ok"] is False
        assert result["first_break"]["id"] == target

    def test_hashes_are_populated_and_linked(self, db):
        _log_a_few(db)
        rows = db.execute(
            "SELECT prev_hash, record_hash FROM audit_log ORDER BY id ASC").fetchall()
        assert all(r["record_hash"] for r in rows)
        # each row's prev_hash equals the previous row's record_hash
        for prev, cur in zip(rows, rows[1:]):
            assert cur["prev_hash"] == prev["record_hash"]


class TestAuditVerifyRoute:
    def _mk_user(self, db, role, email):
        from sheplatform.core.auth import hash_password
        db.execute(
            "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
            "VALUES (%s, %s, 'T', 'U', %s, 1)", (email, hash_password("Test1234!"), role))
        db.commit()

    def test_super_admin_can_verify(self, client, db):
        self._mk_user(db, "super_admin", "auditadmin@test.com")
        _log_a_few(db)
        client.post("/login", data={"email": "auditadmin@test.com", "password": "Test1234!"})
        resp = client.get("/admin/api/audit/verify")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True and body["checked"] >= 3

    def test_non_admin_is_forbidden(self, client, db):
        self._mk_user(db, "she_officer", "auditofficer@test.com")
        client.post("/login", data={"email": "auditofficer@test.com", "password": "Test1234!"})
        resp = client.get("/admin/api/audit/verify")
        assert resp.status_code == 403
