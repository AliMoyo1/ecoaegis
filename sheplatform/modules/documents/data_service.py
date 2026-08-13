"""Document control data service (competitor benchmark gap #4).

SOP/policy library with versioning and staff acknowledgement tracking
(document_acknowledgements). Audit-ready: approval chain + review dates.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core.audit import log_audit
from sheplatform.database import resolve_org

DOC_TYPES = ("sop", "policy", "guideline", "form", "template", "regulation")
STATUSES = ("draft", "in_review", "approved", "superseded", "archived")


def _next_ref(db) -> str:
    row = db.execute("SELECT doc_ref FROM documents ORDER BY id DESC LIMIT 1").fetchone()
    seq = int(row["doc_ref"].rsplit("-", 1)[1]) + 1 if row else 1
    return f"DOC-{seq:03d}"


def create_document(db, *, title: str, doc_type: str, description: str = "",
                    version: str = "1.0", file_path: str = "", review_due_date: str = "",
                    supersedes: int | None = None, created_by: int,
                    org_id: int | None = None) -> dict:
    org_id = resolve_org(db, org_id, created_by)
    if doc_type not in DOC_TYPES:
        raise ValueError("invalid doc_type")
    ref = _next_ref(db)
    db.execute(
        "INSERT INTO documents (doc_ref, title, doc_type, description, version, file_path, "
        "status, review_due_date, supersedes, org_id, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s)",
        (ref, title, doc_type, description, version, file_path, review_due_date or None,
         supersedes, org_id, created_by))
    if supersedes:
        db.execute("UPDATE documents SET status = 'superseded', updated_at = %s WHERE id = %s",
                   (datetime.now(timezone.utc).isoformat(), supersedes))
    db.commit()
    log_audit(db, created_by, org_id, "document.created", "documents", ref,
              new_value={"title": title, "doc_type": doc_type, "version": version})
    return dict(db.execute("SELECT * FROM documents WHERE doc_ref = %s", (ref,)).fetchone())


def list_documents(db, doc_type: str | None = None, status: str | None = None,
                   org_id: int | None = None) -> list[dict]:
    sql = ("SELECT d.*, u.email AS approver_email, "
           "(SELECT COUNT(*) FROM document_acknowledgements a WHERE a.document_id = d.id) AS ack_count "
           "FROM documents d LEFT JOIN users u ON u.id = d.approved_by")
    conds, params = [], []
    if doc_type:
        conds.append("d.doc_type = %s")
        params.append(doc_type)
    if status:
        conds.append("d.status = %s")
        params.append(status)
    if not org_id:
        return []  # fail closed: no tenant scope -> no data (audit S5)
    conds.append("d.org_id = %s")
    params.append(org_id)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY d.id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def submit_for_review(db, doc_id: int, user_id: int) -> dict:
    _get(db, doc_id, "draft")
    db.execute("UPDATE documents SET status = 'in_review', updated_at = %s WHERE id = %s",
               (datetime.now(timezone.utc).isoformat(), doc_id))
    db.commit()
    return dict(db.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone())


def approve_document(db, doc_id: int, user_id: int) -> dict:
    _get(db, doc_id, "in_review")
    now = datetime.now(timezone.utc).isoformat()
    db.execute("UPDATE documents SET status = 'approved', approved_by = %s, approved_at = %s, "
               "updated_at = %s WHERE id = %s", (user_id, now, now, doc_id))
    db.commit()
    log_audit(db, user_id, None, "document.approved", "documents",
              db.execute("SELECT doc_ref FROM documents WHERE id = %s", (doc_id,)).fetchone()["doc_ref"])
    return dict(db.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone())


def acknowledge_document(db, doc_id: int, user_id: int) -> dict:
    """Staff acknowledge they have read the approved document."""
    _get(db, doc_id, "approved")
    db.execute(
        "INSERT OR IGNORE INTO document_acknowledgements (document_id, user_id) VALUES (%s, %s)",
        (doc_id, user_id))
    db.commit()
    return {"ok": True, "acknowledged": True}


def unacknowledged_users(db, doc_id: int) -> list[dict]:
    rows = db.execute(
        "SELECT u.id, u.email, u.first_name, u.last_name FROM users u "
        "WHERE u.is_active = TRUE AND u.id NOT IN "
        "(SELECT user_id FROM document_acknowledgements WHERE document_id = %s)",
        (doc_id,)).fetchall()
    return [dict(r) for r in rows]


def _get(db, doc_id: int, expected_status: str) -> dict:
    row = db.execute("SELECT * FROM documents WHERE id = %s", (doc_id,)).fetchone()
    if not row:
        raise ValueError("document not found")
    if row["status"] != expected_status:
        raise ValueError(f"document must be '{expected_status}' (is '{row['status']}')")
    return dict(row)
