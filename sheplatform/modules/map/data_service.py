"""Geographic map data service (guide C1).

Plots incidents and sites that have real coordinates. Risks are deliberately
excluded: the risk register (modules/risk_register) has no site_id or
lat/long, it is a process/function-based enterprise register, not a
site-bound one, so it has no genuine location to plot.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math

from sheplatform.core.audit import log_audit


COORDINATE_SOURCES = frozenset({"manual", "device_gps", "imported", "geocoder"})


def validate_coordinates(latitude: float, longitude: float) -> tuple[float, float]:
    """Return finite WGS84 latitude/longitude values in canonical order."""
    try:
        lat, lng = float(latitude), float(longitude)
    except (TypeError, ValueError) as exc:
        raise ValueError("latitude and longitude must be numbers") from exc
    if not math.isfinite(lat) or not -90 <= lat <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if not math.isfinite(lng) or not -180 <= lng <= 180:
        raise ValueError("longitude must be between -180 and 180")
    return lat, lng


def _validated_accuracy(accuracy_m: float | None) -> float | None:
    if accuracy_m is None or accuracy_m == "":
        return None
    try:
        accuracy = float(accuracy_m)
    except (TypeError, ValueError) as exc:
        raise ValueError("accuracy_m must be a number") from exc
    if not math.isfinite(accuracy) or accuracy < 0:
        raise ValueError("accuracy_m must be zero or greater")
    return accuracy


def _coordinate_values(row) -> dict:
    return {
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "coordinate_source": row["coordinate_source"],
        "coordinate_accuracy_m": row["coordinate_accuracy_m"],
        "coordinates_updated_at": row["coordinates_updated_at"],
        "coordinates_updated_by": row["coordinates_updated_by"],
    }


def _editable_site(db, site_id: int, updated_by: int, org_id: int | None):
    """Resolve both actor and site inside one tenant; fail closed on missing org."""
    if not org_id or not updated_by:
        return None
    actor = db.execute(
        "SELECT id FROM users WHERE id = %s AND org_id = %s AND is_active = TRUE",
        (updated_by, org_id),
    ).fetchone()
    if actor is None:
        return None
    return db.execute(
        "SELECT * FROM sites WHERE id = %s AND org_id = %s", (site_id, org_id)
    ).fetchone()


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


def list_sites_for_coordinate_admin(db, org_id: int | None) -> list[dict]:
    """All active tenant sites, including unlocated sites, for coordinate editing."""
    if not org_id:
        return []
    rows = db.execute(
        "SELECT id, site_code, site_name, city, region, latitude, longitude, "
        "coordinate_source, coordinate_accuracy_m, coordinates_updated_at "
        "FROM sites WHERE org_id = %s AND status = 'active' ORDER BY site_name",
        (org_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def set_site_coords(db, *, site_id: int, latitude: float, longitude: float,
                    source: str, updated_by: int, org_id: int | None,
                    accuracy_m: float | None = None) -> dict:
    """Set canonical site coordinates with tenant, actor, provenance, and audit."""
    lat, lng = validate_coordinates(latitude, longitude)
    source = (source or "").strip().lower()
    if source not in COORDINATE_SOURCES:
        raise ValueError("invalid coordinate source")
    accuracy = _validated_accuracy(accuracy_m)
    row = _editable_site(db, site_id, updated_by, org_id)
    if row is None:
        return {"ok": False, "message": "site not found"}
    previous = _coordinate_values(row)
    updated_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE sites SET latitude = %s, longitude = %s, coordinate_source = %s, "
        "coordinate_accuracy_m = %s, coordinates_updated_at = %s, "
        "coordinates_updated_by = %s, geocode_provider = NULL, geocode_place_id = NULL "
        "WHERE id = %s AND org_id = %s",
        (lat, lng, source, accuracy, updated_at, updated_by, site_id, org_id),
    )
    current = {
        "latitude": lat,
        "longitude": lng,
        "coordinate_source": source,
        "coordinate_accuracy_m": accuracy,
        "coordinates_updated_at": updated_at,
        "coordinates_updated_by": updated_by,
    }
    log_audit(db, updated_by, org_id, "site.set_coords", "sites", site_id,
              old_value=previous, new_value=current)
    site = db.execute(
        "SELECT * FROM sites WHERE id = %s AND org_id = %s", (site_id, org_id)
    ).fetchone()
    return {"ok": True, "site": dict(site)}


def clear_site_coords(db, *, site_id: int, updated_by: int,
                      org_id: int | None) -> dict:
    """Clear a tenant site's location while preserving an append-only audit event."""
    row = _editable_site(db, site_id, updated_by, org_id)
    if row is None:
        return {"ok": False, "message": "site not found"}
    previous = _coordinate_values(row)
    updated_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE sites SET latitude = NULL, longitude = NULL, coordinate_source = NULL, "
        "coordinate_accuracy_m = NULL, coordinates_updated_at = %s, "
        "coordinates_updated_by = %s, geocode_provider = NULL, geocode_place_id = NULL "
        "WHERE id = %s AND org_id = %s",
        (updated_at, updated_by, site_id, org_id),
    )
    current = {
        "latitude": None,
        "longitude": None,
        "coordinate_source": None,
        "coordinate_accuracy_m": None,
        "coordinates_updated_at": updated_at,
        "coordinates_updated_by": updated_by,
    }
    log_audit(db, updated_by, org_id, "site.clear_coords", "sites", site_id,
              old_value=previous, new_value=current)
    site = db.execute(
        "SELECT * FROM sites WHERE id = %s AND org_id = %s", (site_id, org_id)
    ).fetchone()
    return {"ok": True, "site": dict(site)}
