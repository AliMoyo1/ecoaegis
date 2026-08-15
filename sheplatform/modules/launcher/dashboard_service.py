"""Dashboard data service: rich, at-a-glance KPIs + chart datasets.

Every query is grounded in real tables. All tiles deep-link to filtered
module views.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sheplatform.core.notifications import unread_count
from sheplatform.database import get_db
from sheplatform.modules.assets.data_service import get_asset_dashboard_summary
from sheplatform.modules.incidents.data_service import get_ltifr_stats


def _scalar(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def _rows(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def dashboard_stats(db, user_id: int | None = None, org_id: int | None = None) -> dict:
    """Full at-a-glance dataset for the dashboard, scoped to org_id."""
    if not org_id:
        # Fail closed: no org scope -> all zeroes
        return _empty_stats()

    now = datetime.now(timezone.utc)
    month_ago = (now - timedelta(days=30)).isoformat()

    # ---- KPI tiles (all clickable) ----
    stats = {
        "active_incidents": _scalar(db, "SELECT COUNT(*) FROM incidents WHERE status != 'closed' AND org_id = %s", (org_id,)),
        "open_corrective_actions": _scalar(
            db, "SELECT COUNT(*) FROM corrective_actions WHERE status IN ('open','in_progress','overdue') AND org_id = %s", (org_id,)),
        "overdue_cas": _scalar(db, "SELECT COUNT(*) FROM corrective_actions WHERE status = 'overdue' AND org_id = %s", (org_id,)),
        "open_risks": _scalar(
            db, "SELECT COUNT(*) FROM risks WHERE status IN ('open','under_review','monitoring') AND org_id = %s", (org_id,)),
        "high_risks": _scalar(
            db, "SELECT COUNT(*) FROM risks WHERE residual_score >= 12 AND status != 'mitigated' AND org_id = %s", (org_id,)),
        "open_grievances": _scalar(db, "SELECT COUNT(*) FROM grievances WHERE status NOT IN ('closed','resolved') AND org_id = %s", (org_id,)),
        "active_permits": _scalar(db, "SELECT COUNT(*) FROM permits WHERE status NOT IN ('closed','cancelled') AND org_id = %s", (org_id,)),
        "pending_approvals": _scalar(
            db, "SELECT COUNT(*) FROM permits WHERE status = 'pending_approval' AND org_id = %s", (org_id,)),
        "scheduled_drills": _scalar(db, "SELECT COUNT(*) FROM mock_drills WHERE status = 'scheduled' AND org_id = %s", (org_id,)),
        "training_sessions": _scalar(db, "SELECT COUNT(*) FROM training_sessions WHERE status = 'scheduled' AND org_id = %s", (org_id,)),
        "eia_projects": _scalar(db, "SELECT COUNT(*) FROM eia_projects WHERE status NOT IN ('closed','rejected') AND org_id = %s", (org_id,)),
        "unread": unread_count(db, user_id) if user_id else 0,
    }

    # ---- ratios ----
    total_incidents = _scalar(db, "SELECT COUNT(*) FROM incidents WHERE org_id = %s", (org_id,))
    near_misses = _scalar(db, "SELECT COUNT(*) FROM incidents WHERE incident_type = 'near_miss' AND org_id = %s", (org_id,))
    stats["near_miss_ratio"] = round(near_misses * 100.0 / total_incidents, 1) if total_incidents else 0
    closed_cas = _scalar(db, "SELECT COUNT(*) FROM corrective_actions WHERE status = 'resolved' AND org_id = %s", (org_id,))
    total_cas = _scalar(db, "SELECT COUNT(*) FROM corrective_actions WHERE org_id = %s", (org_id,))
    stats["ca_closure_rate"] = round(closed_cas * 100.0 / total_cas, 1) if total_cas else 0

    # ---- incident trend: last 12 months ----
    months = []
    for i in range(11, -1, -1):
        m = now - timedelta(days=30 * i)
        months.append(m.strftime("%Y-%m"))
    trend = []
    for m in months:
        trend.append(_scalar(
            db, "SELECT COUNT(*) FROM incidents WHERE substr(reported_at, 1, 7) = %s AND org_id = %s", (m, org_id)))
    stats["incident_trend"] = {"labels": [m[2:] + "/" + m[:4] for m in months], "values": trend}

    # ---- severity distribution ----
    sev_rows = _rows(db, "SELECT severity, COUNT(*) AS n FROM incidents WHERE org_id = %s GROUP BY severity", (org_id,))
    stats["severity_distribution"] = {
        "labels": [r["severity"] for r in sev_rows],
        "values": [r["n"] for r in sev_rows],
    }

    # ---- incidents by type ----
    type_rows = _rows(db, "SELECT incident_type, COUNT(*) AS n FROM incidents WHERE org_id = %s GROUP BY incident_type", (org_id,))
    stats["incident_types"] = {
        "labels": [r["incident_type"] for r in type_rows],
        "values": [r["n"] for r in type_rows],
    }

    # ---- risk heat map data (5x5 likelihood x impact) ----
    heat = [[0] * 5 for _ in range(5)]
    risk_rows = _rows(db, "SELECT likelihood, impact FROM risks WHERE org_id = %s", (org_id,))
    for r in risk_rows:
        l = max(1, min(5, int(r["likelihood"] or 1))) - 1
        i = max(1, min(5, int(r["impact"] or 1))) - 1
        heat[l][i] += 1
    stats["risk_heatmap"] = heat

    # ---- upcoming statutory deadlines (next 30 days) ----
    deadline_rows = _rows(db,
        "SELECT incident_ref, title, severity, statutory_deadline FROM incidents "
        "WHERE statutory_deadline IS NOT NULL AND statutory_deadline BETWEEN %s AND %s "
        "AND status != 'closed' AND org_id = %s ORDER BY statutory_deadline LIMIT 6",
        (now.isoformat(), (now + timedelta(days=30)).isoformat(), org_id))
    stats["upcoming_deadlines"] = deadline_rows

    # ---- expiring vendor certifications (next 30 days) ----
    cert_rows = _rows(db,
        "SELECT v.company_name AS vendor_name, vc.cert_name AS certification_name, vc.expiry_date "
        "FROM vendor_certifications vc JOIN vendors v ON v.id = vc.vendor_id "
        "WHERE vc.expiry_date BETWEEN %s AND %s AND vc.status != 'expired' AND v.org_id = %s "
        "ORDER BY vc.expiry_date LIMIT 6",
        (now.isoformat(), (now + timedelta(days=30)).isoformat(), org_id))
    stats["expiring_certs"] = cert_rows

    # ---- key issues aging ----
    stats["key_issues"] = _rows(db,
        "SELECT title, age_days, status FROM key_issues "
        "WHERE status IN ('open','in_progress') AND org_id = %s ORDER BY age_days DESC LIMIT 5", (org_id,))

    # ---- grievance trend (last 6 months) ----
    g_months = []
    for i in range(5, -1, -1):
        m = now - timedelta(days=30 * i)
        g_months.append(m.strftime("%Y-%m"))
    g_trend = []
    for m in g_months:
        g_trend.append(_scalar(
            db, "SELECT COUNT(*) FROM grievances WHERE substr(created_at, 1, 7) = %s AND org_id = %s", (m, org_id)))
    stats["grievance_trend"] = {"labels": [m[2:] + "/" + m[:4] for m in g_months], "values": g_trend}

    # ---- ESG RAG summary ----
    rag_rows = _rows(db,
        "SELECT rag_status, COUNT(*) AS n FROM esg_kpi_entries WHERE org_id = %s GROUP BY rag_status", (org_id,))
    stats["esg_rag"] = {r["rag_status"]: r["n"] for r in rag_rows}

    # ---- LTIFR (B5): trailing 12 months, ISO 45001 million-hour base ----
    stats["ltifr"] = get_ltifr_stats(db, org_id)

    # ---- Assets (C4): tracked count + open maintenance ----
    stats["assets"] = get_asset_dashboard_summary(db, org_id)

    return stats


def _empty_stats() -> dict:
    return {
        "active_incidents": 0,
        "open_corrective_actions": 0,
        "overdue_cas": 0,
        "open_risks": 0,
        "high_risks": 0,
        "open_grievances": 0,
        "active_permits": 0,
        "pending_approvals": 0,
        "scheduled_drills": 0,
        "training_sessions": 0,
        "eia_projects": 0,
        "unread": 0,
        "near_miss_ratio": 0,
        "ca_closure_rate": 0,
        "incident_trend": {"labels": [], "values": []},
        "severity_distribution": {"labels": [], "values": []},
        "incident_types": {"labels": [], "values": []},
        "risk_heatmap": [[0] * 5 for _ in range(5)],
        "upcoming_deadlines": [],
        "expiring_certs": [],
        "key_issues": [],
        "grievance_trend": {"labels": [], "values": []},
        "esg_rag": {},
        "ltifr": {"lost_time_injuries": 0, "total_lost_days": 0, "hours_worked": None,
                  "ltifr": None, "period_start": None, "period_end": None},
        "assets": {"assets_tracked": 0, "open_maintenance_tasks": 0},
    }
