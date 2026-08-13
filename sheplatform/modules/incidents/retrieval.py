"""FTS5 / hybrid similar-incident retrieval (guide A4).

Keeps an incidents_fts index synchronised with the incidents table. In dev SQLite
mode this uses FTS5 if available; otherwise it falls back to LIKE queries.
"""
from __future__ import annotations

import re

from sheplatform.config import settings
from sheplatform.database import get_db


_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "incident", "accident", "near", "miss", "report", "employee", "worker",
}


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", text or "") if w.lower() not in _STOPWORDS]


def _fts_available(db) -> bool:
    try:
        db.execute("SELECT * FROM incidents_fts WHERE 1=0")
        return True
    except Exception:
        return False


def index_incident(db, incident_id: int, title: str, description: str,
                   incident_type: str, severity: str) -> None:
    content = " ".join(_tokenize(f"{title} {description} {incident_type} {severity}"))
    if settings.is_postgres():
        db.execute(
            "INSERT INTO incidents_fts (incident_id, title, description, incident_type, severity, content) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (incident_id) DO UPDATE SET title=EXCLUDED.title, "
            "description=EXCLUDED.description, incident_type=EXCLUDED.incident_type, "
            "severity=EXCLUDED.severity, content=EXCLUDED.content",
            (incident_id, title, description or "", incident_type or "", severity or "", content))
    else:
        db.execute("DELETE FROM incidents_fts WHERE incident_id = %s", (incident_id,))
        db.execute(
            "INSERT INTO incidents_fts (incident_id, title, description, incident_type, severity, content) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (incident_id, title, description or "", incident_type or "", severity or "", content))
    db.commit()


def _fts_search(db, terms: list[str], org_id: int | None, limit: int, exclude_id: int | None) -> list[dict]:
    query = " OR ".join(terms)
    sql = (
        "SELECT i.* FROM incidents_fts f "
        "JOIN incidents i ON i.id = f.incident_id "
        "WHERE f.content MATCH %s")
    params = [query]
    if org_id:
        sql += " AND i.org_id = %s"
        params.append(org_id)
    if exclude_id:
        sql += " AND i.id != %s"
        params.append(exclude_id)
    sql += " ORDER BY rank LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _like_search(db, terms: list[str], org_id: int | None, limit: int, exclude_id: int | None) -> list[dict]:
    conds, params = [], []
    term_conds = []
    for term in terms:
        term_conds.append("(title LIKE %s OR description LIKE %s)")
        params.extend([f"%{term}%", f"%{term}%"])
    sql = "SELECT * FROM incidents WHERE (" + " OR ".join(term_conds) + ")"
    if org_id:
        sql += " AND org_id = %s"
        params.append(org_id)
    if exclude_id:
        sql += " AND id != %s"
        params.append(exclude_id)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_similar(db, text: str, org_id: int | None = None, limit: int = 5,
                   exclude_id: int | None = None) -> list[dict]:
    terms = _tokenize(text)
    if not terms:
        return []
    seen = set()
    results = []
    if _fts_available(db):
        try:
            for row in _fts_search(db, terms, org_id, limit, exclude_id):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    results.append(row)
        except Exception:
            pass
    # Supplement with LIKE fallback for coverage / when FTS is empty.
    try:
        for row in _like_search(db, terms, org_id, limit, exclude_id):
            if row["id"] not in seen:
                seen.add(row["id"])
                results.append(row)
    except Exception:
        pass
    return results[:limit]
