"""Dashboard data service: rich, at-a-glance KPIs + chart datasets.

Every query is grounded in real tables. All tiles deep-link to filtered
module views.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sheplatform.core.notifications import unread_count
from sheplatform.database import get_db


def _scalar(db, sql, params=()):
    row = db.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def _rows(db, sql, params=()):
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def dashboard_stats(db, user_id: int | None = None) -> dict:
    """Full at-a-glance dataset for the dashboard."""
    now = datetime.now(timezone.utc)
    month_ago = (now - timedelta(days=30)).isoformat()

    # ---- KPI tiles (all clickable) ----
    stats = {
        "active_incidents": _scalar(db, "SELECT COUNT(*) FROM incidents WHERE status != 'closed'"),
        "open_corrective_actions": _scalar(
            db, "SELECT COUNT(*) FROM corrective_actions WHERE status IN ('open','in_progress','overdue')"),
        "overdue_cas": _scalar(db, "SELECT COUNT(*) FROM corrective_actions WHERE status = 'overdue'"),
        "open_risks": _scalar(
            db, "SELECT COUNT(*) FROM risks WHERE status IN ('open','under_review','monitoring')"),
        "high_risks": _scalar(
            db, "SELECT COUNT(*) FROM risks WHERE residual_score >= 12 AND status != 'mitigated'"),
        "open_grievances": _scalar(db, "SELECT COUNT(*) FROM grievances WHERE status NOT IN ('closed','resolved')"),
        "active_permits": _scalar(db, "SELECT COUNT(*) FROM permits WHERE status NOT IN ('closed','cancelled')"),
        "pending_approvals": _scalar(
            db, "SELECT COUNT(*) FROM permits WHERE status = 'pending_approval'"),
        "scheduled_drills": _scalar(db, "SELECT COUNT(*) FROM mock_drills WHERE status = 'scheduled'"),
        "training_sessions": _scalar(db, "SELECT COUNT(*) FROM training_sessions WHERE status = 'scheduled'"),
        "eia_projects": _scalar(db, "SELECT COUNT(*) FROM eia_projects WHERE status NOT IN ('closed','rejected')"),
        "unread": unread_count(db, user_id) if user_id else 0,
    }

    # ---- ratios ----
    total_incidents = _scalar(db, "SELECT COUNT(*) FROM incidents")
    near_misses = _scalar(db, "SELECT COUNT(*) FROM incidents WHERE incident_type = 'near_miss'")
    stats["near_miss_ratio"] = round(near_misses * 100.0 / total_incidents, 1) if total_incidents else 0
    closed_cas = _scalar(db, "SELECT COUNT(*) FROM corrective_actions WHERE status = 'resolved'")
    total_cas = _scalar(db, "SELECT COUNT(*) FROM corrective_actions")
    stats["ca_closure_rate"] = round(closed_cas * 100.0 / total_cas, 1) if total_cas else 0

    # ---- incident trend: last 12 months ----
    months = []
    for i in range(11, -1, -1):
        m = now - timedelta(days=30 * i)
        months.append(m.strftime("%Y-%m"))
    trend = []
    for m in months:
        trend.append(_scalar(
            db, "SELECT COUNT(*) FROM incidents WHERE substr(reported_at, 1, 7) = %s", (m,)))
    stats["incident_trend"] = {"labels": [m[2:] + "/" + m[:4] for m in months], "values": trend}

    # ---- severity distribution ----
    sev_rows = _rows(db, "SELECT severity, COUNT(*) AS n FROM incidents GROUP BY severity")
    stats["severity_distribution"] = {
        "labels": [r["severity"] for r in sev_rows],
        "values": [r["n"] for r in sev_rows],
    }

    # ---- incidents by type ----
    type_rows = _rows(db, "SELECT incident_type, COUNT(*) AS n FROM incidents GROUP BY incident_type")
    stats["incident_types"] = {
        "labels": [r["incident_type"] for r in type_rows],
        "values": [r["n"] for r in type_rows],
    }

    # ---- risk heat map data (5x5 likelihood x impact) ----
    heat = [[0] * 5 for _ in range(5)]
    risk_rows = _rows(db, "SELECT likelihood, impact FROM risks")
    for r in risk_rows:
        l = max(1, min(5, int(r["likelihood"] or 1))) - 1
        i = max(1, min(5, int(r["impact"] or 1))) - 1
        heat[l][i] += 1
    stats["risk_heatmap"] = heat

    # ---- upcoming statutory deadlines (next 30 days) ----
    deadline_rows = _rows(db,
        "SELECT incident_ref, title, severity, statutory_deadline FROM incidents "
        "WHERE statutory_deadline IS NOT NULL AND statutory_deadline BETWEEN %s AND %s "
        "AND status != 'closed' ORDER BY statutory_deadline LIMIT 6",
        (now.isoformat(), (now + timedelta(days=30)).isoformat()))
    stats["upcoming_deadlines"] = deadline_rows

    # ---- expiring vendor certifications (next 30 days) ----
    cert_rows = _rows(db,
        "SELECT v.company_name AS vendor_name, vc.cert_name AS certification_name, vc.expiry_date "
        "FROM vendor_certifications vc JOIN vendors v ON v.id = vc.vendor_id "
        "WHERE vc.expiry_date BETWEEN %s AND %s AND vc.status != 'expired' "
        "ORDER BY vc.expiry_date LIMIT 6",
        (now.isoformat(), (now + timedelta(days=30)).isoformat()))
    stats["expiring_certs"] = cert_rows

    # ---- key issues aging ----
    stats["key_issues"] = _rows(db,
        "SELECT title, age_days, status FROM key_issues "
        "WHERE status IN ('open','in_progress') ORDER BY age_days DESC LIMIT 5")

    # ---- grievance trend (last 6 months) ----
    g_months = []
    for i in range(5, -1, -1):
        m = now - timedelta(days=30 * i)
        g_months.append(m.strftime("%Y-%m"))
    g_trend = []
    for m in g_months:
        g_trend.append(_scalar(
            db, "SELECT COUNT(*) FROM grievances WHERE substr(created_at, 1, 7) = %s", (m,)))
    stats["grievance_trend"] = {"labels": [m[2:] + "/" + m[:4] for m in g_months], "values": g_trend}

    # ---- ESG RAG summary ----
    rag_rows = _rows(db,
        "SELECT rag_status, COUNT(*) AS n FROM esg_kpi_entries GROUP BY rag_status")
    stats["esg_rag"] = {r["rag_status"]: r["n"] for r in rag_rows}

    return stats
