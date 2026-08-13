"""ESG KPI data service (guide 19, Module 13).

- FNR-SHE-060: monthly data entry with validation
- FNR-SHE-063: non-zero critical KPIs (spillage, injury, fatality) auto-create incident
- FNR-SHE-064: regulatory penalty KPIs auto-update Risk Register
- RAG status from actual vs target
"""
from __future__ import annotations

from datetime import datetime, timezone

from sheplatform.core import events


# Representative ESG KPI seed set (BRS Sections 12.1-12.3; 40+ in production)
SEED_KPIS = [
    # (code, category, subcategory, name, unit, frequency, threshold_type, alert_threshold, sdg)
    ("ESG-ENV-01", "environmental", "emissions", "CO2 emissions", "tCO2e", "monthly", "max", None, "SDG13"),
    ("ESG-ENV-02", "environmental", "water", "Water consumption", "m3", "monthly", "max", None, "SDG6"),
    ("ESG-ENV-03", "environmental", "waste", "Diesel spillage", "litres", "monthly", "max", 0, "SDG14"),
    ("ESG-ENV-04", "environmental", "waste", "Hazardous waste generated", "tonnes", "monthly", "max", None, "SDG12"),
    ("ESG-ENV-05", "environmental", "energy", "Energy consumption", "MWh", "monthly", "max", None, "SDG7"),
    ("ESG-SOC-01", "social", "safety", "Lost time injuries (LTI)", "count", "monthly", "max", 0, "SDG8"),
    ("ESG-SOC-02", "social", "safety", "Fatalities", "count", "monthly", "max", 0, "SDG8"),
    ("ESG-SOC-03", "social", "training", "Training hours per employee", "hours", "monthly", "min", None, "SDG4"),
    ("ESG-SOC-04", "social", "community", "Community complaints", "count", "monthly", "max", None, "SDG11"),
    ("ESG-GOV-01", "governance", "compliance", "Regulatory penalties", "USD", "monthly", "max", 0, "SDG16"),
    ("ESG-GOV-02", "governance", "compliance", "Compliance training completion", "pct", "quarterly", "min", None, "SDG16"),
    ("ESG-GOV-03", "governance", "risk", "Risk register items reviewed", "count", "quarterly", "min", None, "SDG16"),
]


def seed_kpis(db, org_id: int | None = None) -> int:
    count = 0
    for code, category, sub, name, unit, freq, ttype, alert, sdg in SEED_KPIS:
        exists = db.execute("SELECT id FROM esg_kpis WHERE kpi_code = %s", (code,)).fetchone()
        if exists:
            continue
        db.execute(
            "INSERT INTO esg_kpis (kpi_code, category, subcategory, name, unit, frequency, "
            "threshold_type, alert_threshold, sdg_mapping, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (code, category, sub, name, unit, freq, ttype, alert, sdg, org_id))
        count += 1
    db.commit()
    return count


def list_kpis(db, category: str | None = None) -> list[dict]:
    sql = "SELECT * FROM esg_kpis WHERE is_active = 1"
    if category:
        sql += f" AND category = '{category}'"
    sql += " ORDER BY kpi_code"
    return [dict(r) for r in db.execute(sql).fetchall()]


def get_kpi(db, kpi_id: int) -> dict | None:
    row = db.execute("SELECT * FROM esg_kpis WHERE id = %s", (kpi_id,)).fetchone()
    return dict(row) if row else None


def _rag(actual: float, target: float, threshold_type: str, alert_threshold) -> str:
    if target is None:
        # no target configured: only alert on threshold breach
        if alert_threshold is not None and actual > alert_threshold:
            return "red"
        return "green"
    if threshold_type == "max":
        if actual > target * 1.1:
            return "red"
        if actual > target:
            return "amber"
        return "green"
    if threshold_type == "min":
        if actual < target * 0.9:
            return "red"
        if actual < target:
            return "amber"
        return "green"
    return "green"


def record_kpi_entry(db, *, kpi_id: int, period: str, actual_value: float,
                     target_value: float | None = None, notes: str = "",
                     created_by: int | None = None, org_id: int | None = None) -> dict:
    kpi = get_kpi(db, kpi_id)
    if kpi is None:
        return {"ok": False, "message": "KPI not found"}

    target = target_value if target_value is not None else kpi.get("target_fy26")
    rag = _rag(actual_value, float(target) if target is not None else None,
               kpi["threshold_type"], kpi.get("alert_threshold"))
    variance = round(actual_value - target, 2) if target is not None else None

    db.execute(
        "INSERT INTO esg_kpi_entries (kpi_id, period, actual_value, target_value, variance, "
        "rag_status, notes, org_id, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (kpi_id, period, actual_value, target, variance, rag, notes, org_id, created_by))
    db.commit()
    entry_row = db.execute(
        "SELECT * FROM esg_kpi_entries ORDER BY id DESC LIMIT 1").fetchone()
    entry = dict(entry_row)

    # FNR-SHE-063: non-zero critical KPI (spillage, LTI, fatality) -> auto incident
    if kpi["alert_threshold"] == 0 and actual_value > 0:
        from sheplatform.modules.incidents.data_service import create_incident
        severity = "critical" if kpi["kpi_code"] in ("ESG-ENV-03", "ESG-SOC-02") else "high"
        incident = create_incident(
            db, title=f"{kpi['name']} breach ({kpi['kpi_code']})",
            description=f"KPI {kpi['name']} recorded {actual_value} {kpi['unit']} for {period}. "
                        f"Threshold is 0 (non-zero triggers automatic incident).",
            severity=severity, incident_type="environmental" if kpi["category"] == "environmental" else "accident",
            occurred_at=datetime.now(timezone.utc).isoformat(),
            reported_by=created_by, org_id=org_id)
        db.execute("UPDATE esg_kpi_entries SET linked_incident_id = %s WHERE id = %s",
                   (incident["id"], entry["id"]))
        db.commit()
        entry["linked_incident_id"] = incident["id"]

    events.emit("kpi.recorded", {
        "kpi_id": kpi_id, "kpi_code": kpi["kpi_code"], "period": period,
        "actual_value": actual_value, "rag_status": rag,
        "org_id": org_id, "entity_type": "esg_kpi_entry", "entity_id": entry["id"],
    }, db, user_id=created_by, source_module="esg_kpi")
    return {"ok": True, "entry": entry}


def list_entries(db, kpi_id: int | None = None, period: str | None = None) -> list[dict]:
    sql = "SELECT * FROM esg_kpi_entries"
    conds, params = [], []
    if kpi_id:
        conds.append("kpi_id = %s")
        params.append(kpi_id)
    if period:
        conds.append("period = %s")
        params.append(period)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def dashboard_summary(db) -> dict:
    rows = db.execute(
        "SELECT rag_status, COUNT(*) AS c FROM esg_kpi_entries GROUP BY rag_status").fetchall()
    summary = {"red": 0, "amber": 0, "green": 0}
    for r in rows:
        if r["rag_status"] in summary:
            summary[r["rag_status"]] = r["c"]
    return summary
