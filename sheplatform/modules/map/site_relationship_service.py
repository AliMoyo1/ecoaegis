"""Tenant-safe assignment of operational records to canonical sites.

Free-text locations remain useful descriptions, but ``site_id`` is the only
authoritative relationship. A selected site is locked and revalidated in the
same transaction that creates the operational record so it cannot become
inactive between validation and insertion.
"""
from __future__ import annotations

from sheplatform.config import settings
from sheplatform.database import resolve_org


SITE_UNAVAILABLE_MESSAGE = "site is not active for this organisation"


def list_active_sites(db, org_id: int | None) -> list[dict]:
    """Return safe picker fields for active sites in one organisation."""
    if not org_id:
        return []
    rows = db.execute(
        "SELECT id, site_code, site_name, city, region FROM sites "
        "WHERE org_id = %s AND status = 'active' "
        "ORDER BY site_name, site_code",
        (org_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def prepare_site_assignment(
    db,
    *,
    site_id: int | None,
    org_id: int | None,
    user_id: int | None,
) -> tuple[int | None, int | None]:
    """Resolve tenant scope and lock an optional active site for record creation.

    The generic failure message deliberately does not reveal whether a site
    belongs to another organisation, is inactive, or does not exist.
    """
    resolved_org = resolve_org(db, org_id, user_id)

    if user_id:
        actor = db.execute(
            "SELECT org_id FROM users WHERE id = %s", (user_id,)
        ).fetchone()
        actor_org = actor["org_id"] if actor else None
        if actor_org and resolved_org and actor_org != resolved_org:
            db.rollback()
            raise ValueError(SITE_UNAVAILABLE_MESSAGE)

    if site_id is None:
        return resolved_org, None

    try:
        normalized_site_id = int(site_id)
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise ValueError(SITE_UNAVAILABLE_MESSAGE) from exc
    if normalized_site_id <= 0 or not resolved_org:
        db.rollback()
        raise ValueError(SITE_UNAVAILABLE_MESSAGE)

    if not settings.is_postgres():
        # SQLite has no row-level SELECT lock. A reserved write lock prevents a
        # concurrent status update until the caller commits the new record.
        db.execute("BEGIN IMMEDIATE")

    sql = (
        "SELECT id FROM sites "
        "WHERE id = %s AND org_id = %s AND status = 'active'"
    )
    if settings.is_postgres():
        sql += " FOR SHARE"
    site = db.execute(sql, (normalized_site_id, resolved_org)).fetchone()
    if site is None:
        db.rollback()
        raise ValueError(SITE_UNAVAILABLE_MESSAGE)

    return resolved_org, normalized_site_id
