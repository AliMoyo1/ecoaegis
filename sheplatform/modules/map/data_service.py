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
MAP_METRIC_EVENTS = frozenset({
    "map_session", "layer_request", "coordinate_save", "coordinate_clear",
    "provider_failure", "import_preview", "import_commit",
})
MAP_LAYERS = frozenset({"incidents", "sites"})


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


def validate_accuracy(accuracy_m: float | None) -> float | None:
    if accuracy_m is None or accuracy_m == "":
        return None
    try:
        accuracy = float(accuracy_m)
    except (TypeError, ValueError) as exc:
        raise ValueError("accuracy_m must be a number") from exc
    if not math.isfinite(accuracy) or accuracy < 0:
        raise ValueError("accuracy_m must be zero or greater")
    return accuracy


def count_unlocated_sites(db, org_id: int | None) -> int:
    """Current active-site count without canonical coordinates."""
    if not org_id:
        return 0
    row = db.execute(
        "SELECT COUNT(*) AS c FROM sites WHERE org_id = %s AND status = 'active' "
        "AND (latitude IS NULL OR longitude IS NULL)",
        (org_id,),
    ).fetchone()
    return int(row["c"])


def record_map_metric(db, *, event_type: str, org_id: int | None,
                      layer_name: str | None = None, feature_count: int = 0,
                      unlocated_count: int | None = None,
                      duration_ms: float | None = None, truncated: bool = False,
                      coordinate_source: str | None = None,
                      commit: bool = True) -> bool:
    """Persist private aggregate measurements without users, coordinates, or text."""
    if not org_id:
        return False
    if event_type not in MAP_METRIC_EVENTS:
        raise ValueError("invalid map metric event")
    if layer_name is not None and layer_name not in MAP_LAYERS:
        raise ValueError("invalid map metric layer")
    if coordinate_source is not None and coordinate_source not in COORDINATE_SOURCES:
        raise ValueError("invalid coordinate source")
    features = max(0, int(feature_count))
    unlocated = None if unlocated_count is None else max(0, int(unlocated_count))
    duration = None if duration_ms is None else max(0.0, float(duration_ms))
    db.execute(
        "INSERT INTO map_usage_metrics (event_type, layer_name, feature_count, "
        "unlocated_count, duration_ms, truncated, coordinate_source, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (event_type, layer_name, features, unlocated, duration, bool(truncated),
         coordinate_source, org_id),
    )
    if commit:
        db.commit()
    return True


def map_metrics_summary(db, org_id: int | None) -> dict:
    """Return tenant-only operational aggregates; never expose raw metric rows."""
    empty = {
        "sessions": 0,
        "coordinate_saves": 0,
        "coordinate_clears": 0,
        "provider_failures": 0,
        "import_previews": 0,
        "import_commits": 0,
        "layers": {},
        "unlocated_sites": 0,
    }
    if not org_id:
        return empty
    rows = db.execute(
        "SELECT event_type, COUNT(*) AS event_count FROM map_usage_metrics "
        "WHERE org_id = %s GROUP BY event_type",
        (org_id,),
    ).fetchall()
    mapping = {
        "map_session": "sessions",
        "coordinate_save": "coordinate_saves",
        "coordinate_clear": "coordinate_clears",
        "provider_failure": "provider_failures",
        "import_preview": "import_previews",
        "import_commit": "import_commits",
    }
    summary = dict(empty)
    for row in rows:
        key = mapping.get(row["event_type"])
        if key:
            summary[key] = int(row["event_count"])
    layers = db.execute(
        "SELECT layer_name, COUNT(*) AS requests, COALESCE(SUM(feature_count), 0) AS features, "
        "COALESCE(AVG(duration_ms), 0) AS average_duration_ms, "
        "COALESCE(SUM(CASE WHEN truncated = TRUE THEN 1 ELSE 0 END), 0) AS truncations "
        "FROM map_usage_metrics WHERE org_id = %s AND event_type = 'layer_request' "
        "GROUP BY layer_name",
        (org_id,),
    ).fetchall()
    summary["layers"] = {
        row["layer_name"]: {
            "requests": int(row["requests"]),
            "features": int(row["features"]),
            "average_duration_ms": round(float(row["average_duration_ms"]), 2),
            "truncations": int(row["truncations"]),
        }
        for row in layers
    }
    summary["unlocated_sites"] = count_unlocated_sites(db, org_id)
    return summary


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
                    accuracy_m: float | None = None, commit: bool = True) -> dict:
    """Set canonical site coordinates with tenant, actor, provenance, and audit."""
    lat, lng = validate_coordinates(latitude, longitude)
    source = (source or "").strip().lower()
    if source not in COORDINATE_SOURCES:
        raise ValueError("invalid coordinate source")
    accuracy = validate_accuracy(accuracy_m)
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
              old_value=previous, new_value=current, commit=commit)
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
