"""Geographic map data service (guide C1).

Plots incidents and sites that have real coordinates. Risks are deliberately
excluded: the risk register (modules/risk_register) has no site_id or
lat/long, it is a process/function-based enterprise register, not a
site-bound one, so it has no genuine location to plot.
"""
from __future__ import annotations


def list_incident_points(db, org_id: int | None, severity: str | None = None,
                         incident_type: str | None = None,
                         since: str | None = None) -> list[dict]:
    """Incidents with real coordinates, org-scoped. Fails closed: no org, no rows."""
    if not org_id:
        return []
    conds = ["org_id = %s", "latitude IS NOT NULL", "longitude IS NOT NULL"]
    params: list = [org_id]
    if severity:
        conds.append("severity = %s")
        params.append(severity)
    if incident_type:
        conds.append("incident_type = %s")
        params.append(incident_type)
    if since:
        conds.append("occurred_at >= %s")
        params.append(since)
    sql = (
        "SELECT id, incident_ref, title, severity, status, incident_type, "
        "latitude, longitude, occurred_at FROM incidents WHERE "
        + " AND ".join(conds) + " ORDER BY occurred_at DESC"
    )
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def list_site_points(db, org_id: int | None) -> list[dict]:
    """Active sites with real coordinates, org-scoped. Fails closed: no org, no rows."""
    if not org_id:
        return []
    rows = db.execute(
        "SELECT id, site_code, site_name, city, region, site_type, latitude, longitude "
        "FROM sites WHERE org_id = %s AND status = 'active' "
        "AND latitude IS NOT NULL AND longitude IS NOT NULL "
        "ORDER BY site_name",
        (org_id,)).fetchall()
    return [dict(r) for r in rows]


def set_site_coords(db, site_id: int, latitude: float, longitude: float,
                    org_id: int | None) -> dict:
    """Admin sets a site's fixed coordinates. Org-scoped: updates 0 rows (not
    found) rather than another tenant's site if org_id doesn't match."""
    row = db.execute(
        "SELECT id FROM sites WHERE id = %s AND org_id = %s", (site_id, org_id)
    ).fetchone()
    if row is None:
        return {"ok": False, "message": "site not found"}
    db.execute(
        "UPDATE sites SET latitude = %s, longitude = %s WHERE id = %s AND org_id = %s",
        (latitude, longitude, site_id, org_id))
    db.commit()
    site = db.execute("SELECT * FROM sites WHERE id = %s", (site_id,)).fetchone()
    return {"ok": True, "site": dict(site)}
