"""FTS5 / hybrid document retrieval (guide C3), mirrors modules/incidents/retrieval.py.

Keeps a documents_fts index synchronised with the documents table. In dev
SQLite mode this uses FTS5 if available; otherwise it falls back to LIKE
queries. Only approved documents are searchable - an unapproved draft SOP
should never be cited as an authoritative procedure.
"""
from __future__ import annotations

import re

from sheplatform.config import settings

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "what", "how", "when", "where", "procedure", "document",
}


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text or "") if w.lower() not in _STOPWORDS]


def _fts_available(db) -> bool:
    # FTS5 MATCH is SQLite-only. On PostgreSQL documents_fts is a plain table,
    # so a MATCH query would error AND poison the transaction (aborting the
    # LIKE fallback on the same connection). Force the LIKE path on PG.
    if settings.is_postgres():
        return False
    try:
        db.execute("SELECT * FROM documents_fts WHERE 1=0")
        return True
    except Exception:
        return False


def index_document(db, document_id: int, title: str, description: str, content_text: str = "") -> None:
    content = " ".join(_tokenize(f"{title} {description} {content_text or ''}"))
    if settings.is_postgres():
        db.execute(
            "INSERT INTO documents_fts (document_id, title, description, content) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (document_id) DO UPDATE SET title=EXCLUDED.title, "
            "description=EXCLUDED.description, content=EXCLUDED.content",
            (document_id, title, description or "", content))
    else:
        db.execute("DELETE FROM documents_fts WHERE document_id = %s", (document_id,))
        db.execute(
            "INSERT INTO documents_fts (document_id, title, description, content) "
            "VALUES (%s, %s, %s, %s)",
            (document_id, title, description or "", content))
    db.commit()


def _fts_search(db, terms: list[str], org_id: int | None, limit: int) -> list[dict]:
    query = " OR ".join(terms)
    sql = (
        "SELECT d.* FROM documents_fts f "
        "JOIN documents d ON d.id = f.document_id "
        "WHERE f.content MATCH %s AND d.status = 'approved'")
    params = [query]
    if org_id:
        sql += " AND d.org_id = %s"
        params.append(org_id)
    sql += " ORDER BY f.rank LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _like_search(db, terms: list[str], org_id: int | None, limit: int) -> list[dict]:
    # LOWER() both sides: SQLite LIKE is case-insensitive by default but
    # PostgreSQL LIKE is case-sensitive. terms are already lowercased by
    # _tokenize(), so LOWER(col) LIKE %term% matches on both engines.
    term_conds, params = [], []
    for term in terms:
        term_conds.append(
            "(LOWER(title) LIKE %s OR LOWER(description) LIKE %s OR LOWER(content_text) LIKE %s)")
        params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
    sql = "SELECT * FROM documents WHERE status = 'approved' AND (" + " OR ".join(term_conds) + ")"
    if org_id:
        sql += " AND org_id = %s"
        params.append(org_id)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_documents(db, text: str, org_id: int | None = None, limit: int = 3) -> list[dict]:
    terms = _tokenize(text)
    if not terms:
        return []
    seen = set()
    results = []
    if _fts_available(db):
        try:
            for row in _fts_search(db, terms, org_id, limit):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    results.append(row)
        except Exception:
            pass
    try:
        for row in _like_search(db, terms, org_id, limit):
            if row["id"] not in seen:
                seen.add(row["id"])
                results.append(row)
    except Exception:
        pass
    return results[:limit]
