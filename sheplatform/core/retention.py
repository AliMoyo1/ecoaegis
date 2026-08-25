"""Data retention policy (NFR-SHE-003).

Statutory SHE records must be retained for a configurable minimum (default 7
years). The platform does not hard-delete these records; this module makes the
minimum configurable per record type, guards any future hard-delete against
premature disposal, and reports what is still within retention vs eligible for
disposal once the minimum has elapsed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sheplatform.config import settings

# Statutory record types named by NFR-SHE-003, mapped to their table. Values are
# hardcoded (never user input), so they are safe to interpolate into COUNT SQL.
RECORD_TABLES = {
    "incident": "incidents",
    "permit": "permits",
    "eia_project": "eia_projects",
    "grievance": "grievances",
    "training": "training_sessions",
    "statutory_report": "statutory_reports",
    "audit": "audit_log",
}


def default_retention_years() -> int:
    return settings.RETENTION_YEARS


def get_retention_policy(db, record_type: str) -> int:
    row = db.execute(
        "SELECT retention_years FROM retention_policies WHERE record_type = %s",
        (record_type,)).fetchone()
    return int(row["retention_years"]) if row else default_retention_years()


def set_retention_policy(db, record_type: str, years: int,
                         updated_by: int | None = None, description: str = "") -> dict:
    if record_type not in RECORD_TABLES:
        return {"ok": False, "message": "unknown record type"}
    if years < default_retention_years():
        return {"ok": False, "message": (
            f"retention may not be below the {default_retention_years()}-year "
            f"statutory minimum")}
    now = datetime.now(timezone.utc).isoformat()
    existing = db.execute(
        "SELECT id FROM retention_policies WHERE record_type = %s", (record_type,)).fetchone()
    if existing:
        db.execute("UPDATE retention_policies SET retention_years = %s, description = %s, "
                   "updated_by = %s, updated_at = %s WHERE record_type = %s",
                   (years, description, updated_by, now, record_type))
    else:
        db.execute("INSERT INTO retention_policies (record_type, retention_years, description, "
                   "updated_by) VALUES (%s,%s,%s,%s)",
                   (record_type, years, description, updated_by))
    db.commit()
    return {"ok": True, "record_type": record_type, "retention_years": years}


def _cutoff_date(years: int) -> str:
    """Date on/after which a record has completed its retention: records created
    strictly before this date are disposal-eligible. 365*years is a deliberate
    approximation - immaterial for a multi-year statutory window."""
    return (datetime.now(timezone.utc) - timedelta(days=365 * years)).date().isoformat()


def is_retention_expired(db, record_type: str, created_at) -> bool:
    """Has the minimum retention elapsed for a record created at created_at?"""
    years = get_retention_policy(db, record_type)
    return str(created_at)[:10] < _cutoff_date(years)


def assert_retention_allows_delete(db, record_type: str, created_at) -> None:
    """Guard: raise if a record is still within its minimum retention window.
    Any future hard-delete path MUST call this first (NFR-SHE-003)."""
    if record_type in RECORD_TABLES and not is_retention_expired(db, record_type, created_at):
        raise PermissionError(
            f"{record_type} record is within its {get_retention_policy(db, record_type)}-year "
            f"retention period and may not be deleted")


def retention_report(db) -> dict:
    """Per statutory record type: total rows, still-within-retention, and
    disposal-eligible (past the minimum). System-wide (super_admin view).
    Portable date compare via substr(CAST(created_at AS TEXT),1,10)."""
    out = {}
    for rtype, table in RECORD_TABLES.items():
        years = get_retention_policy(db, rtype)
        cutoff = _cutoff_date(years)
        total = db.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        eligible = db.execute(
            f"SELECT COUNT(*) AS c FROM {table} "
            "WHERE substr(CAST(created_at AS TEXT),1,10) < %s", (cutoff,)).fetchone()["c"]
        out[rtype] = {
            "table": table, "retention_years": years, "cutoff_date": cutoff,
            "total": int(total), "disposal_eligible": int(eligible),
            "within_retention": int(total) - int(eligible),
        }
    return out
