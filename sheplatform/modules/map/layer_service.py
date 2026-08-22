"""Secure, provider-neutral GeoJSON layer queries for the EcoAegis map.

Every selectable identifier in this module comes from the fixed registry.
Request values are parameters, never SQL fragments. All source queries fail
closed without an organisation and all site joins repeat the organisation
boundary so a foreign site ID cannot become a cross-tenant location bridge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from types import MappingProxyType
from typing import Mapping

from sheplatform.config import settings


DEFAULT_LAYER_LIMIT = 500
DEFAULT_UNLOCATED_LIMIT = 100
MAX_UNLOCATED_LIMIT = 500


@dataclass(frozen=True)
class BBox:
    west: float
    south: float
    east: float
    north: float

    @property
    def min_lng(self) -> float:
        return self.west

    @property
    def min_lat(self) -> float:
        return self.south

    @property
    def max_lng(self) -> float:
        return self.east

    @property
    def max_lat(self) -> float:
        return self.north

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]


@dataclass(frozen=True)
class LayerSpec:
    key: str
    label: str
    capability: str
    url: str
    supported_filters: tuple[str, ...]
    source_kind: str
    table: str | None = None
    ref_column: str | None = None
    label_column: str | None = None
    status_column: str | None = None
    severity_column: str | None = None
    type_column: str | None = None
    timestamp_column: str | None = None


_SPECS = (
    LayerSpec("facilities", "Facilities", "module.map.access", "/map", ("status", "type", "since"),
              "facilities", table="sites", ref_column="site_code", label_column="site_name",
              status_column="status", type_column="site_type", timestamp_column="created_at"),
    LayerSpec("incidents", "Incidents", "module.incidents.access", "/incidents/{id}",
              ("status", "type", "severity", "since"), "incidents"),
    LayerSpec("permits", "Permits", "module.permits.access", "/permits",
              ("status", "type", "since"), "site_linked", table="permits",
              ref_column="permit_ref", label_column="title", status_column="status",
              type_column="permit_type", timestamp_column="created_at"),
    LayerSpec("inspections", "Inspections", "module.inspections.access", "/inspections",
              ("status", "type", "since"), "site_linked", table="inspections",
              ref_column="inspection_ref", label_column="title", status_column="status",
              type_column="inspection_type", timestamp_column="created_at"),
    LayerSpec("environmental", "Environmental projects", "module.eia.access", "/eia",
              ("status", "type", "since"), "site_linked", table="eia_projects",
              ref_column="project_ref", label_column="project_name", status_column="status",
              type_column="project_type", timestamp_column="created_at"),
    LayerSpec("emergencies", "Emergency events", "module.emergency.access", "/emergency",
              ("status", "severity", "since"), "site_linked", table="emergency_events",
              ref_column="event_ref", label_column="title", status_column="status",
              severity_column="severity", timestamp_column="created_at"),
    LayerSpec("contractors", "Contractor inductions", "module.contractors.access", "/contractors",
              ("status", "type", "since"), "contractors"),
    LayerSpec("corrective_actions", "Corrective actions", "module.capa.access", "/capa",
              ("status", "severity", "since"), "source_linked_capa"),
    LayerSpec("assets", "Assets", "module.assets.access", "/assets",
              ("status", "type", "since"), "site_linked", table="assets",
              ref_column="asset_ref", label_column="name", status_column="status",
              type_column="asset_type", timestamp_column="created_at"),
    LayerSpec("observations", "Observations", "observations.triage", "/observations",
              ("status", "type", "severity", "since"), "site_linked", table="observations",
              ref_column="obs_ref", label_column="title", status_column="status",
              severity_column="severity", type_column="obs_type", timestamp_column="created_at"),
    LayerSpec("risks", "Source-linked risks", "module.risk_register.access", "/risks",
              ("status", "type", "since"), "source_linked_risks"),
)

LAYER_REGISTRY: Mapping[str, LayerSpec] = MappingProxyType({spec.key: spec for spec in _SPECS})


def parse_bbox(raw: str) -> BBox:
    """Parse west,south,east,north WGS84 bounds; antimeridian spans are rejected."""
    parts = [part.strip() for part in (raw or "").split(",")]
    if len(parts) != 4 or any(part == "" for part in parts):
        raise ValueError("bbox must contain west,south,east,north")
    try:
        west, south, east, north = (float(part) for part in parts)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox values must be numbers") from exc
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("bbox values must be finite")
    if not -180 <= west <= 180 or not -180 <= east <= 180:
        raise ValueError("bbox longitude must be between -180 and 180")
    if not -90 <= south <= 90 or not -90 <= north <= 90:
        raise ValueError("bbox latitude must be between -90 and 90")
    if south > north:
        raise ValueError("bbox south cannot be greater than north")
    if west > east:
        raise ValueError("antimeridian-crossing bbox is not supported")
    return BBox(west=west, south=south, east=east, north=north)


def parse_bbox_values(min_lng: str | float | None, min_lat: str | float | None,
                      max_lng: str | float | None, max_lat: str | float | None) -> BBox:
    values = (min_lng, min_lat, max_lng, max_lat)
    if any(value is None or str(value).strip() == "" for value in values):
        raise ValueError("min_lng,min_lat,max_lng,max_lat are required")
    return parse_bbox(",".join(str(value) for value in values))


def clamp_limit(value: int | None, *, default: int = DEFAULT_LAYER_LIMIT,
                maximum: int | None = None) -> int:
    hard_maximum = settings.MAP_MAX_FEATURES_PER_LAYER if maximum is None else maximum
    try:
        requested = default if value is None else int(value)
    except (TypeError, ValueError):
        requested = default
    return max(1, min(requested, hard_maximum))


def validate_since(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    try:
        datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("since must be an ISO 8601 date or timestamp") from exc
    return candidate


def manifest_for(layer_keys: list[str], bbox: BBox) -> dict:
    return {
        "bbox": bbox.as_list(),
        "layers": [
            {
                "key": LAYER_REGISTRY[key].key,
                "label": LAYER_REGISTRY[key].label,
                "endpoint": f"/map/api/layer/{key}",
                "unlocated_endpoint": f"/map/api/unlocated/{key}",
                "supported_filters": list(LAYER_REGISTRY[key].supported_filters),
            }
            for key in layer_keys if key in LAYER_REGISTRY
        ],
        "limits": {"default": DEFAULT_LAYER_LIMIT,
                   "maximum": settings.MAP_MAX_FEATURES_PER_LAYER},
    }


def _column(alias: str, name: str | None) -> str:
    return f"{alias}.{name}" if name else "NULL"


def _simple_base(spec: LayerSpec) -> tuple[str, list]:
    alias = "s" if spec.source_kind == "facilities" else "x"
    site_name = "s.site_name" if spec.source_kind == "facilities" else "site.site_name"
    coordinates = ("s.latitude", "s.longitude") if spec.source_kind == "facilities" else (
        "site.latitude", "site.longitude")
    join = "" if spec.source_kind == "facilities" else (
        "LEFT JOIN sites site ON site.id = x.site_id AND site.org_id = x.org_id ")
    sql = (
        f"SELECT {alias}.id AS id, {_column(alias, spec.ref_column)} AS ref, "
        f"{_column(alias, spec.label_column)} AS label, {_column(alias, spec.status_column)} AS status, "
        f"{_column(alias, spec.severity_column)} AS severity, {_column(alias, spec.type_column)} AS item_type, "
        f"{_column(alias, spec.timestamp_column)} AS event_at, {site_name} AS site_name, "
        f"{coordinates[0]} AS latitude, {coordinates[1]} AS longitude "
        f"FROM {spec.table} {alias} {join}WHERE {alias}.org_id = %s"
    )
    return sql, []


def _incidents_base() -> tuple[str, list]:
    direct = (
        "SELECT i.id AS id, i.incident_ref AS ref, i.title AS label, i.status AS status, "
        "i.severity AS severity, i.incident_type AS item_type, i.occurred_at AS event_at, "
        "s.site_name AS site_name, i.latitude AS latitude, i.longitude AS longitude "
        "FROM incidents i LEFT JOIN sites s ON s.id = i.site_id AND s.org_id = i.org_id "
        "WHERE i.org_id = %s AND i.latitude IS NOT NULL AND i.longitude IS NOT NULL"
    )
    site_fallback = (
        "SELECT i.id AS id, i.incident_ref AS ref, i.title AS label, i.status AS status, "
        "i.severity AS severity, i.incident_type AS item_type, i.occurred_at AS event_at, "
        "s.site_name AS site_name, s.latitude AS latitude, s.longitude AS longitude "
        "FROM incidents i LEFT JOIN sites s ON s.id = i.site_id AND s.org_id = i.org_id "
        "WHERE i.org_id = %s AND (i.latitude IS NULL OR i.longitude IS NULL)"
    )
    return f"{direct} UNION ALL {site_fallback}", ["repeat_org"]


def _contractors_base() -> tuple[str, list]:
    return (
        "SELECT ci.id AS id, v.vendor_ref AS ref, v.company_name AS label, ci.status AS status, "
        "v.risk_profile AS severity, ci.induction_type AS item_type, ci.created_at AS event_at, "
        "s.site_name AS site_name, s.latitude AS latitude, s.longitude AS longitude "
        "FROM contractor_inductions ci "
        "JOIN vendors v ON v.id = ci.vendor_id "
        "LEFT JOIN sites s ON s.id = ci.site_id AND s.org_id = v.org_id "
        "WHERE v.org_id = %s",
        [],
    )


def _source_linked_base(spec: LayerSpec) -> tuple[str, list]:
    if spec.source_kind == "source_linked_capa":
        table, alias, ref, label, severity, item_type, timestamp = (
            "corrective_actions", "x", "action_ref", "title", "priority", "source_type", "created_at")
    else:
        table, alias, ref, label, severity, item_type, timestamp = (
            "risks", "x", "risk_ref", "risk_ref", None, "risk_category", "created_at")
    severity_expr = _column(alias, severity)
    incident_branch = (
        f"SELECT {alias}.id AS id, {alias}.{ref} AS ref, {alias}.{label} AS label, "
        f"{alias}.status AS status, {severity_expr} AS severity, {alias}.{item_type} AS item_type, "
        f"{alias}.{timestamp} AS event_at, s.site_name AS site_name, "
        "CASE WHEN i.latitude IS NOT NULL AND i.longitude IS NOT NULL THEN i.latitude ELSE s.latitude END AS latitude, "
        "CASE WHEN i.latitude IS NOT NULL AND i.longitude IS NOT NULL THEN i.longitude ELSE s.longitude END AS longitude "
        f"FROM {table} {alias} JOIN incidents i ON {alias}.source_type = 'incident' "
        f"AND i.id = {alias}.source_id AND i.org_id = {alias}.org_id "
        "LEFT JOIN sites s ON s.id = i.site_id AND s.org_id = i.org_id "
        f"WHERE {alias}.org_id = %s"
    )
    inspection_branch = (
        f"SELECT {alias}.id AS id, {alias}.{ref} AS ref, {alias}.{label} AS label, "
        f"{alias}.status AS status, {severity_expr} AS severity, {alias}.{item_type} AS item_type, "
        f"{alias}.{timestamp} AS event_at, s.site_name AS site_name, s.latitude AS latitude, s.longitude AS longitude "
        f"FROM {table} {alias} JOIN inspections i ON {alias}.source_type = 'inspection' "
        f"AND i.id = {alias}.source_id AND i.org_id = {alias}.org_id "
        "LEFT JOIN sites s ON s.id = i.site_id AND s.org_id = i.org_id "
        f"WHERE {alias}.org_id = %s"
    )
    return f"{incident_branch} UNION ALL {inspection_branch}", ["repeat_org"]


def _base_query(spec: LayerSpec, org_id: int) -> tuple[str, list]:
    if spec.source_kind in {"facilities", "site_linked"}:
        sql, markers = _simple_base(spec)
    elif spec.source_kind == "incidents":
        sql, markers = _incidents_base()
    elif spec.source_kind == "contractors":
        sql, markers = _contractors_base()
    else:
        sql, markers = _source_linked_base(spec)
    params = [org_id, org_id] if markers else [org_id]
    return sql, params


def _filtered_query(spec: LayerSpec, org_id: int, filters: Mapping[str, str | None],
                    *, located: bool, bbox: BBox | None = None) -> tuple[str, list]:
    base, params = _base_query(spec, org_id)
    conditions = []
    status = (filters.get("status") or "").strip()
    item_type = (filters.get("type") or "").strip()
    severity = (filters.get("severity") or "").strip()
    since = validate_since(filters.get("since"))
    if spec.key == "facilities" and not status:
        conditions.append("q.status = 'active'")
    elif status and "status" in spec.supported_filters:
        conditions.append("q.status = %s")
        params.append(status)
    if item_type and "type" in spec.supported_filters:
        conditions.append("q.item_type = %s")
        params.append(item_type)
    if severity and "severity" in spec.supported_filters:
        conditions.append("q.severity = %s")
        params.append(severity)
    if since and "since" in spec.supported_filters:
        conditions.append("q.event_at >= %s")
        params.append(since)
    if located:
        if bbox is None:
            raise ValueError("bbox is required")
        conditions.extend([
            "q.latitude IS NOT NULL", "q.longitude IS NOT NULL",
            "q.longitude >= %s", "q.longitude <= %s",
            "q.latitude >= %s", "q.latitude <= %s",
        ])
        params.extend([bbox.west, bbox.east, bbox.south, bbox.north])
    else:
        conditions.append("(q.latitude IS NULL OR q.longitude IS NULL)")
    sql = f"SELECT * FROM ({base}) q"
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    return sql, params


def _short_text(value, maximum: int):
    if value is None or not isinstance(value, str):
        return value
    normalized = " ".join(value.split())
    if len(normalized) <= maximum:
        return normalized
    return normalized[:maximum - 3].rstrip() + "..."


def _safe_properties(spec: LayerSpec, row) -> dict:
    url = spec.url.format(id=row["id"])
    properties = {
        "id": row["id"],
        "ref": _short_text(row["ref"], 80),
        "label": _short_text(row["label"], 160),
        "status": _short_text(row["status"], 40),
        "severity": _short_text(row["severity"], 40),
        "type": _short_text(row["item_type"], 80),
        "site_name": _short_text(row["site_name"], 120),
        "timestamp": row["event_at"],
        "url": url,
    }
    return {key: value for key, value in properties.items() if value is not None}


def get_layer_collection(db, *, layer_key: str, org_id: int | None, bbox: BBox,
                         limit: int | None = None,
                         filters: Mapping[str, str | None] | None = None) -> dict:
    spec = LAYER_REGISTRY[layer_key]
    actual_limit = clamp_limit(limit)
    empty_meta = {"layer": layer_key, "returned": 0, "limit": actual_limit,
                  "truncated": False, "unlocated": 0}
    if not org_id:
        return {"type": "FeatureCollection", "features": [], "meta": empty_meta}
    active_filters = filters or {}
    sql, params = _filtered_query(spec, org_id, active_filters, located=True, bbox=bbox)
    sql += " ORDER BY CASE WHEN q.event_at IS NULL THEN 1 ELSE 0 END, q.event_at DESC, q.id DESC LIMIT %s"
    rows = db.execute(sql, [*params, actual_limit + 1]).fetchall()
    truncated = len(rows) > actual_limit
    rows = rows[:actual_limit]
    features = [
        {
            "type": "Feature",
            "id": f"{layer_key}:{row['id']}",
            "geometry": {"type": "Point", "coordinates": [float(row["longitude"]), float(row["latitude"])]},
            "properties": _safe_properties(spec, row),
        }
        for row in rows
    ]
    unlocated_sql, unlocated_params = _filtered_query(
        spec, org_id, active_filters, located=False)
    unlocated_row = db.execute(
        f"SELECT COUNT(*) AS c FROM ({unlocated_sql}) missing", unlocated_params).fetchone()
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "layer": layer_key,
            "returned": len(features),
            "limit": actual_limit,
            "truncated": truncated,
            "unlocated": int(unlocated_row["c"]),
        },
    }


def get_unlocated_records(db, *, layer_key: str, org_id: int | None,
                          limit: int | None = None,
                          filters: Mapping[str, str | None] | None = None) -> dict:
    spec = LAYER_REGISTRY[layer_key]
    actual_limit = clamp_limit(limit, default=DEFAULT_UNLOCATED_LIMIT,
                               maximum=MAX_UNLOCATED_LIMIT)
    if not org_id:
        return {"layer": layer_key, "records": [],
                "meta": {"returned": 0, "limit": actual_limit, "truncated": False}}
    sql, params = _filtered_query(spec, org_id, filters or {}, located=False)
    sql += " ORDER BY CASE WHEN q.event_at IS NULL THEN 1 ELSE 0 END, q.event_at DESC, q.id DESC LIMIT %s"
    rows = db.execute(sql, [*params, actual_limit + 1]).fetchall()
    truncated = len(rows) > actual_limit
    records = [_safe_properties(spec, row) for row in rows[:actual_limit]]
    return {"layer": layer_key, "records": records,
            "meta": {"returned": len(records), "limit": actual_limit, "truncated": truncated}}


_FACILITY_COUNT_SQL = MappingProxyType({
    "incidents": "SELECT COUNT(*) AS c FROM incidents WHERE org_id = %s AND site_id = %s",
    "permits": "SELECT COUNT(*) AS c FROM permits WHERE org_id = %s AND site_id = %s",
    "inspections": "SELECT COUNT(*) AS c FROM inspections WHERE org_id = %s AND site_id = %s",
    "environmental": "SELECT COUNT(*) AS c FROM eia_projects WHERE org_id = %s AND site_id = %s",
    "emergencies": "SELECT COUNT(*) AS c FROM emergency_events WHERE org_id = %s AND site_id = %s",
    "contractors": (
        "SELECT COUNT(*) AS c FROM contractor_inductions ci JOIN vendors v ON v.id = ci.vendor_id "
        "WHERE v.org_id = %s AND ci.site_id = %s"),
    "corrective_actions": (
        "SELECT COUNT(*) AS c FROM corrective_actions x WHERE x.org_id = %s AND "
        "((x.source_type = 'incident' AND EXISTS (SELECT 1 FROM incidents i WHERE i.id = x.source_id "
        "AND i.org_id = x.org_id AND i.site_id = %s)) OR (x.source_type = 'inspection' AND EXISTS "
        "(SELECT 1 FROM inspections i WHERE i.id = x.source_id AND i.org_id = x.org_id AND i.site_id = %s)))"),
    "assets": "SELECT COUNT(*) AS c FROM assets WHERE org_id = %s AND site_id = %s",
    "observations": "SELECT COUNT(*) AS c FROM observations WHERE org_id = %s AND site_id = %s",
    "risks": (
        "SELECT COUNT(*) AS c FROM risks x WHERE x.org_id = %s AND "
        "((x.source_type = 'incident' AND EXISTS (SELECT 1 FROM incidents i WHERE i.id = x.source_id "
        "AND i.org_id = x.org_id AND i.site_id = %s)) OR (x.source_type = 'inspection' AND EXISTS "
        "(SELECT 1 FROM inspections i WHERE i.id = x.source_id AND i.org_id = x.org_id AND i.site_id = %s)))"),
})


def get_facility_detail(db, *, site_id: int, org_id: int | None,
                        count_layers: list[str]) -> dict | None:
    if not org_id:
        return None
    row = db.execute(
        "SELECT id, site_code, site_name, site_type, status, latitude, longitude "
        "FROM sites WHERE id = %s AND org_id = %s", (site_id, org_id)).fetchone()
    if row is None:
        return None
    geometry = None
    if row["latitude"] is not None and row["longitude"] is not None:
        geometry = {"type": "Point", "coordinates": [float(row["longitude"]), float(row["latitude"])]}
    counts = {}
    for key in count_layers:
        sql = _FACILITY_COUNT_SQL.get(key)
        if not sql:
            continue
        placeholders = sql.count("%s")
        params = [org_id, *([site_id] * (placeholders - 1))]
        count_row = db.execute(sql, params).fetchone()
        counts[key] = int(count_row["c"])
    return {
        "type": "Feature",
        "id": f"facilities:{row['id']}",
        "geometry": geometry,
        "properties": {
            "id": row["id"], "ref": _short_text(row["site_code"], 80),
            "label": _short_text(row["site_name"], 160),
            "status": _short_text(row["status"], 40),
            "type": _short_text(row["site_type"], 80), "url": "/map",
        },
        "counts": counts,
    }
