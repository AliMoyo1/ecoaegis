"""Statutory report assembly service (B4).

Templates, auto-fill from incidents / ESG KPIs / chemicals, locking,
submission tracking, and audit trail.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

from sheplatform.database import get_db, resolve_org


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _to_iso(value: Any) -> str:
    if isinstance(value, str) and (len(value) >= 10):
        return value[:10]
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def seed_templates(db) -> None:
    """Insert default statutory templates if missing."""
    templates = [
        {
            "template_key": "nssa_critical_incident",
            "authority": "nssa",
            "title": "NSSA Critical Incident Notification",
            "description": "48-hour statutory notification to NSSA for critical incidents.",
            "period_type": "incident",
            "fields": [
                {"name": "employer_name", "label": "Employer Name", "source": "organisation.name", "type": "text"},
                {"name": "employer_address", "label": "Employer Address", "source": "static", "static": "Head Office", "type": "text"},
                {"name": "incident_ref", "label": "Incident Reference", "source": "incident.incident_ref", "type": "text"},
                {"name": "date_of_accident", "label": "Date of Accident", "source": "incident.occurred_at", "type": "date"},
                {"name": "time_of_accident", "label": "Time of Accident", "source": "incident.occurred_at", "type": "time"},
                {"name": "location", "label": "Location", "source": "incident.location", "type": "text"},
                {"name": "description", "label": "Description", "source": "incident.description", "type": "textarea"},
                {"name": "injured_persons", "label": "Injured Persons", "source": "static", "static": "To be confirmed", "type": "text"},
                {"name": "immediate_action", "label": "Immediate Action Taken", "source": "incident.immediate_cause", "type": "textarea"},
            ],
            "default_content": {"authority_code": "NSSA", "form": "Accident Notification Form"},
        },
        {
            "template_key": "ema_environmental_monthly",
            "authority": "ema",
            "title": "EMA Monthly Environmental Return",
            "description": "Monthly environmental KPI return to EMA.",
            "period_type": "monthly",
            "fields": [
                {"name": "reporting_period", "label": "Reporting Period", "source": "period.label", "type": "text"},
                {"name": "total_water_m3", "label": "Water Use (m3)", "source": "esg.WATER_USE_M3", "type": "number"},
                {"name": "total_energy_mj", "label": "Energy Use (MJ)", "source": "esg.ENERGY_USE_MJ", "type": "number"},
                {"name": "co2_tco2e", "label": "CO2 Emissions (tCO2e)", "source": "esg.CO2_TCO2E", "type": "number"},
                {"name": "waste_tonnes", "label": "Waste Generated (tonnes)", "source": "esg.WASTE_TONNES", "type": "number"},
                {"name": "incidents_count", "label": "Environmental Incidents", "source": "count.environmental_incidents", "type": "number"},
            ],
            "default_content": {"authority_code": "EMA", "form": "Monthly Environmental Return"},
        },
        {
            "template_key": "zrp_monthly",
            "authority": "zrp",
            "title": "ZRP Monthly SHE Return",
            "description": "Monthly safety return to Zimbabwe Republic Police.",
            "period_type": "monthly",
            "fields": [
                {"name": "reporting_period", "label": "Reporting Period", "source": "period.label", "type": "text"},
                {"name": "total_employees", "label": "Total Employees", "source": "static", "static": "0", "type": "number"},
                {"name": "fatalities", "label": "Fatalities", "source": "count.fatalities", "type": "number"},
                {"name": "serious_injuries", "label": "Serious Injuries", "source": "count.serious_injuries", "type": "number"},
                {"name": "vehicle_incidents", "label": "Vehicle Incidents", "source": "count.vehicle_incidents", "type": "number"},
            ],
            "default_content": {"authority_code": "ZRP", "form": "Monthly SHE Return"},
        },
    ]
    for t in templates:
        fields_json = json.dumps(t["fields"])
        default_json = json.dumps(t["default_content"])
        exists = db.execute(
            "SELECT fields, default_content FROM statutory_report_templates WHERE template_key = %s", (t["template_key"],)
        ).fetchone()
        if not exists:
            db.execute(
                "INSERT INTO statutory_report_templates (template_key, authority, title, description, period_type, fields, default_content) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (t["template_key"], t["authority"], t["title"], t["description"],
                 t["period_type"], fields_json, default_json)
            )


def _next_report_ref(db, authority: str) -> str:
    prefix = authority.upper()
    row = db.execute(
        "SELECT report_ref FROM statutory_reports WHERE report_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (prefix + "-%",)
    ).fetchone()
    n = 1
    if row:
        m = re.search(r"-(\d+)$", row["report_ref"])
        if m:
            n = int(m.group(1)) + 1
    return f"{prefix}-{datetime.utcnow():%Y%m%d}-{n:04d}"


def _get_organisation(db, org_id: int | None) -> dict:
    if not org_id:
        return {"name": "", "slug": ""}
    row = db.execute("SELECT id, name, slug, settings FROM organisations WHERE id = %s", (org_id,)).fetchone()
    if not row:
        return {"name": "", "slug": ""}
    return dict(row)


def _get_incident_for_period(db, org_id: int | None, period_start: str, period_end: str,
                             severity: str | None = None) -> dict | None:
    sql = (
        "SELECT * FROM incidents WHERE org_id = %s AND occurred_at >= %s AND occurred_at <= %s "
    )
    params = [org_id, period_start, period_end]
    if severity:
        sql += " AND severity = %s"
        params.append(severity)
    sql += " ORDER BY occurred_at DESC LIMIT 1"
    row = db.execute(sql, params).fetchone()
    return dict(row) if row else None


def _count_incidents(db, org_id: int | None, period_start: str, period_end: str,
                     incident_type: str | None = None, severity: str | None = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM incidents WHERE org_id = %s AND occurred_at >= %s AND occurred_at <= %s"
    params = [org_id, period_start, period_end]
    if incident_type:
        sql += " AND incident_type = %s"
        params.append(incident_type)
    if severity:
        sql += " AND severity = %s"
        params.append(severity)
    row = db.execute(sql, params).fetchone()
    return row["n"] if row else 0


def _get_esg_value(db, org_id: int | None, kpi_code: str, period: str) -> float | None:
    row = db.execute(
        "SELECT e.actual_value FROM esg_kpi_entries e JOIN esg_kpis k ON e.kpi_id = k.id "
        "WHERE k.kpi_code = %s AND e.period = %s AND e.org_id = %s ORDER BY e.created_at DESC LIMIT 1",
        (kpi_code, period, org_id)
    ).fetchone()
    if not row or row["actual_value"] is None:
        return None
    return float(row["actual_value"])


def _resolve_field(db, field: dict, org: dict, incident: dict | None,
                   period_start: str, period_end: str) -> Any:
    source = field.get("source", "static")
    if source == "static":
        return field.get("static", "")
    if source == "organisation.name":
        return org.get("name", "")
    if source == "incident.incident_ref":
        return incident.get("incident_ref", "") if incident else ""
    if source == "incident.occurred_at":
        return incident.get("occurred_at", "") if incident else ""
    if source == "incident.location":
        return incident.get("location", "") if incident else ""
    if source == "incident.description":
        return incident.get("description", "") if incident else ""
    if source == "incident.immediate_cause":
        return incident.get("immediate_cause", "") if incident else ""
    if source == "period.label":
        return period_start[:7] if len(period_start) >= 7 else period_start
    if source.startswith("esg."):
        kpi_code = source.split(".", 1)[1]
        period = period_start[:7] if len(period_start) >= 7 else period_start
        return _get_esg_value(db, org.get("id"), kpi_code, period)
    if source.startswith("count."):
        metric = source.split(".", 1)[1]
        if metric == "environmental_incidents":
            return _count_incidents(db, org.get("id"), period_start, period_end, incident_type="environmental")
        if metric == "fatalities":
            return _count_incidents(db, org.get("id"), period_start, period_end, incident_type="accident", severity="critical")
        if metric == "serious_injuries":
            return _count_incidents(db, org.get("id"), period_start, period_end, severity="critical")
        if metric == "vehicle_incidents":
            return _count_incidents(db, org.get("id"), period_start, period_end, incident_type="vehicle")
        return 0
    return ""


def _autofill_data(db, template: dict, org_id: int | None,
                   period_start: str, period_end: str,
                   incident_id: int | None = None) -> dict:
    org = _get_organisation(db, org_id)
    incident = None
    if incident_id:
        row = db.execute("SELECT * FROM incidents WHERE id = %s AND org_id = %s", (incident_id, org_id)).fetchone()
        incident = dict(row) if row else None
    elif template.get("period_type") == "incident":
        incident = _get_incident_for_period(db, org_id, period_start, period_end, severity="critical")
    data: dict[str, Any] = {}
    for field in template.get("fields", []):
        data[field["name"]] = _resolve_field(db, field, org, incident, period_start, period_end)
    data["_meta"] = {
        "authority": template.get("authority"),
        "template_key": template.get("template_key"),
        "title": template.get("title"),
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": _now(),
    }
    return data


def list_templates(db, authority: str | None = None) -> list[dict]:
    sql = "SELECT * FROM statutory_report_templates"
    params = ()
    if authority:
        sql += " WHERE authority = %s"
        params = (authority,)
    sql += " ORDER BY authority, title"
    rows = db.execute(sql, params).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["fields"] = json.loads(item.get("fields") or "[]")
        item["default_content"] = json.loads(item.get("default_content") or "{}")
        out.append(item)
    return out


def create_report(db, template_key: str, period_start: str, period_end: str,
                  created_by: int, org_id: int | None = None,
                  incident_id: int | None = None,
                  overrides: dict | None = None) -> dict:
    org_id = resolve_org(db, org_id, created_by)
    row = db.execute(
        "SELECT * FROM statutory_report_templates WHERE template_key = %s", (template_key,)
    ).fetchone()
    if not row:
        return {"ok": False, "error": "template_not_found"}
    tpl = dict(row)
    tpl["fields"] = json.loads(tpl.get("fields") or "[]")
    tpl["default_content"] = json.loads(tpl.get("default_content") or "{}")
    data = _autofill_data(db, tpl, org_id, period_start, period_end, incident_id)
    if overrides:
        data.update(overrides)
    report_ref = _next_report_ref(db, tpl["authority"])
    rendered = render_text(data, dict(tpl))
    row = db.execute(
        "INSERT INTO statutory_reports (report_ref, template_key, authority, title, "
        "period_start, period_end, status, data, rendered_text, org_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (report_ref, tpl["template_key"], tpl["authority"], tpl["title"],
         period_start, period_end, "draft", json.dumps(data), rendered,
         org_id, created_by)
    ).fetchone()
    report_id = row["id"]
    _audit(db, report_id, "created", created_by, None, {"report_ref": report_ref, "status": "draft"})
    db.commit()
    return {"ok": True, "report_id": report_id, "report_ref": report_ref, "data": data}


def get_report(db, report_id: int, org_id: int | None = None) -> dict | None:
    row = db.execute("SELECT * FROM statutory_reports WHERE id = %s", (report_id,)).fetchone()
    if not row:
        return None
    report = dict(row)
    if org_id and report.get("org_id") != org_id:
        return None
    report["data"] = json.loads(report.get("data") or "{}")
    return report


def list_reports(db, org_id: int | None = None, status: str | None = None,
                 authority: str | None = None) -> list[dict]:
    sql = "SELECT * FROM statutory_reports WHERE 1=1"
    params = []
    if org_id:
        sql += " AND org_id = %s"
        params.append(org_id)
    if status:
        sql += " AND status = %s"
        params.append(status)
    if authority:
        sql += " AND authority = %s"
        params.append(authority)
    sql += " ORDER BY created_at DESC"
    rows = db.execute(sql, params).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["data"] = json.loads(item.get("data") or "{}")
        out.append(item)
    return out


def update_report_data(db, report_id: int, updates: dict,
                       updated_by: int) -> dict:
    report = get_report(db, report_id)
    if not report:
        return {"ok": False, "error": "not_found"}
    if report["status"] != "draft":
        return {"ok": False, "error": "report_locked"}
    data = report.get("data", {})
    old_data = dict(data)
    data.update(updates)
    tpl_row = db.execute(
        "SELECT * FROM statutory_report_templates WHERE template_key = %s", (report["template_key"],)
    ).fetchone()
    tpl = dict(tpl_row) if tpl_row else {}
    tpl["fields"] = json.loads(tpl.get("fields") or "[]")
    rendered = render_text(data, tpl)
    db.execute(
        "UPDATE statutory_reports SET data = %s, rendered_text = %s, updated_at = %s, "
        "lock_version = lock_version + 1 WHERE id = %s",
        (json.dumps(data), rendered, _now(), report_id)
    )
    _audit(db, report_id, "updated", updated_by, old_data, data)
    db.commit()
    return {"ok": True, "report_id": report_id}


def lock_report(db, report_id: int, locked_by: int) -> dict:
    report = get_report(db, report_id)
    if not report:
        return {"ok": False, "error": "not_found"}
    if report["status"] != "draft":
        return {"ok": False, "error": "already_locked_or_submitted"}
    db.execute(
        "UPDATE statutory_reports SET status = 'locked', updated_at = %s, lock_version = lock_version + 1 "
        "WHERE id = %s", (_now(), report_id)
    )
    _audit(db, report_id, "locked", locked_by, {"status": "draft"}, {"status": "locked"})
    db.commit()
    return {"ok": True, "report_id": report_id, "status": "locked"}


def submit_report(db, report_id: int, submitted_by: int,
                  channel: str = "manual", recipient: str = "") -> dict:
    report = get_report(db, report_id)
    if not report:
        return {"ok": False, "error": "not_found"}
    if report["status"] not in ("draft", "locked"):
        return {"ok": False, "error": "invalid_status_for_submit"}
    now = _now()
    db.execute(
        "UPDATE statutory_reports SET status = 'submitted', submitted_at = %s, submitted_by = %s, "
        "updated_at = %s, lock_version = lock_version + 1 WHERE id = %s",
        (now, submitted_by, now, report_id)
    )
    db.execute(
        "INSERT INTO statutory_report_submissions (report_id, channel, recipient, status, created_by) "
        "VALUES (%s,%s,%s,%s,%s)",
        (report_id, channel, recipient or None, "pending", submitted_by)
    )
    _audit(db, report_id, "submitted", submitted_by, {"status": report["status"]}, {"status": "submitted"})
    db.commit()
    return {"ok": True, "report_id": report_id, "status": "submitted"}


def record_submission_status(db, report_id: int, status: str,
                             tracking_ref: str = "", payload: dict | None = None,
                             recorded_by: int | None = None) -> dict:
    report = get_report(db, report_id)
    if not report:
        return {"ok": False, "error": "not_found"}
    db.execute(
        "INSERT INTO statutory_report_submissions (report_id, channel, tracking_ref, status, payload, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (report_id, "api", tracking_ref, status, json.dumps(payload or {}), recorded_by)
    )
    new_report_status = "submitted"
    if status == "acknowledged":
        new_report_status = "acknowledged"
    elif status == "failed":
        new_report_status = "rejected"
    db.execute(
        "UPDATE statutory_reports SET status = %s, updated_at = %s WHERE id = %s",
        (new_report_status, _now(), report_id)
    )
    _audit(db, report_id, f"submission_{status}", recorded_by,
           {"status": report["status"]}, {"status": new_report_status})
    db.commit()
    return {"ok": True, "report_id": report_id, "status": new_report_status}


def render_text(data: dict, template: dict) -> str:
    """Render a simple plain-text representation of the report."""
    lines: list[str] = []
    meta = data.get("_meta", {})
    lines.append(f"STATUTORY REPORT - {meta.get('authority','').upper()}")
    lines.append(f"Title: {meta.get('title','')}")
    lines.append(f"Period: {meta.get('period_start','')} to {meta.get('period_end','')}")
    lines.append(f"Generated: {meta.get('generated_at','')}")
    lines.append("")
    for field in template.get("fields", []):
        name = field["name"]
        label = field.get("label", name)
        value = data.get(name, "")
        lines.append(f"{label}: {value}")
    return "\n".join(lines)


def export_json(report: dict) -> dict:
    return {
        "report_ref": report.get("report_ref"),
        "authority": report.get("authority"),
        "title": report.get("title"),
        "period_start": report.get("period_start"),
        "period_end": report.get("period_end"),
        "status": report.get("status"),
        "data": report.get("data", {}),
        "rendered_text": report.get("rendered_text", ""),
    }


def _audit(db, report_id: int, action: str, actor_id: int | None,
           old_data: dict | None, new_data: dict | None) -> None:
    db.execute(
        "INSERT INTO statutory_report_audit (report_id, action, actor_id, old_data, new_data) "
        "VALUES (%s,%s,%s,%s,%s)",
        (report_id, action, actor_id,
         json.dumps(old_data or {}), json.dumps(new_data or {}))
    )
