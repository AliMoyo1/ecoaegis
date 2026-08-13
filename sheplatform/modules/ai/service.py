"""AI service org-scoping fix (tenant isolation audit finding).

Threads org_id through every data-gathering function so the AI copilot
only ever sees the caller's organisation's data.
"""
from __future__ import annotations

import json

from sheplatform.core.ai_client import ask_ai
from sheplatform.database import get_db


def _safe_json(text: str) -> dict:
    """Tolerant JSON object parser: returns {} on any failure."""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # Find the first JSON object
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return {}


def _safe_json_array(text: str) -> list:
    """Tolerant JSON array parser: returns [] on any failure."""
    if not text:
        return []
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        return json.loads(s[start:end + 1])
    except Exception:
        return []

# ---------- data gathering (grounding) ----------


def _org_cond(org_id, alias: str = "") -> tuple[str, list]:
    """Return (sql_fragment, params) for org scoping."""
    if org_id:
        return f" AND {alias + '.' if alias else ''}org_id = %s", [org_id]
    return "", []


def _recent_incidents(db, limit: int = 10, org_id: int | None = None) -> list[dict]:
    frag, params = _org_cond(org_id)
    rows = db.execute(
        "SELECT id, incident_ref, title, description, severity, incident_type, status, "
        f"root_cause FROM incidents WHERE 1=1{frag} ORDER BY id DESC LIMIT %s",
        params + [limit]).fetchall()
    return [dict(r) for r in rows]


def _incident_detail(db, incident_id: int) -> dict | None:
    row = db.execute("SELECT * FROM incidents WHERE id = %s", (incident_id,)).fetchone()
    return dict(row) if row else None


def _risk_summary(db, org_id: int | None = None) -> list[dict]:
    frag, params = _org_cond(org_id)
    rows = db.execute(
        "SELECT risk_ref, hazard_description, risk_category, likelihood, impact, "
        f"residual_score, status FROM risks WHERE 1=1{frag} "
        "ORDER BY residual_score DESC LIMIT 15", params).fetchall()
    return [dict(r) for r in rows]


def _training_summary(db) -> list[dict]:
    rows = db.execute(
        "SELECT competency_name, COUNT(*) AS n FROM competency_matrix "
        "GROUP BY competency_name ORDER BY n DESC LIMIT 15").fetchall()
    return [dict(r) for r in rows]


def _monthly_trend(db, months: int = 12, org_id: int | None = None) -> list[dict]:
    frag, params = _org_cond(org_id)
    rows = db.execute(
        "SELECT substr(reported_at, 1, 7) AS month, COUNT(*) AS n, "
        "SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) AS critical "
        f"FROM incidents WHERE 1=1{frag} GROUP BY month ORDER BY month DESC LIMIT %s",
        params + [months]).fetchall()
    return [dict(r) for r in rows]


def _deadline_snapshot(db, org_id: int | None = None) -> list[dict]:
    frag, params = _org_cond(org_id)
    rows = db.execute(
        "SELECT incident_ref, title, severity, statutory_deadline, status "
        f"FROM incidents WHERE statutory_deadline IS NOT NULL AND status != 'closed'{frag} "
        "ORDER BY statutory_deadline LIMIT 8", params).fetchall()
    return [dict(r) for r in rows]


def _overdue_items(db, org_id: int | None = None) -> list[dict]:
    frag, params = _org_cond(org_id)
    rows = db.execute(
        "SELECT title, age_days, escalation_threshold, status FROM key_issues "
        f"WHERE status IN ('open','in_progress'){frag} ORDER BY age_days DESC LIMIT 8",
        params).fetchall()
    return [dict(r) for r in rows]


# ---------- prompt builders ----------


def _fmt(items: list[dict]) -> str:
    return json.dumps(items, default=str, indent=1)


async def incident_copilot(incident_id: int, org_id: int | None = None) -> dict:
    """FNR-SHE-022 enhancement: similar incidents, investigation questions, draft report."""
    db = get_db()
    try:
        incident = _incident_detail(db, incident_id)
        if not incident:
            return {"ok": False, "message": "incident not found"}
        # tenant guard: only see the incident if it belongs to your org
        if org_id and incident.get("org_id") and incident["org_id"] != org_id:
            return {"ok": False, "message": "incident not found"}
        similar = [i for i in _recent_incidents(db, 20, org_id)
                   if i["id"] != incident_id and
                   (i.get("incident_type") == incident.get("incident_type")
                    or i.get("severity") == incident.get("severity"))][:5]
        prompt = (
            f"Incident: {incident.get('title')}\n"
            f"Description: {incident.get('description')}\n"
            f"Severity: {incident.get('severity')} | Type: {incident.get('incident_type')}\n\n"
            f"Similar historical incidents:\n{_fmt(similar)}\n\n"
            "Tasks:\n"
            "1. List 5 investigation questions tailored to this incident.\n"
            "2. Draft a preliminary incident report for SHE Officer review, "
            "based ONLY on the data above.\n"
            "3. Note what data is missing that an investigation should collect."
        )
        reply = await ask_ai(prompt, max_tokens=2500)
        return {"ok": True, "incident_ref": incident.get("incident_ref"), "result": reply}
    finally:
        db.close()


async def root_cause_assistant(incident_id: int, org_id: int | None = None) -> dict:
    """5-Why analysis from incident description."""
    db = get_db()
    try:
        incident = _incident_detail(db, incident_id)
        if not incident:
            return {"ok": False, "message": "incident not found"}
        if org_id and incident.get("org_id") and incident["org_id"] != org_id:
            return {"ok": False, "message": "incident not found"}
        prompt = (
            f"Incident: {incident.get('title')}\n"
            f"Description: {incident.get('description')}\n"
            f"Reported root cause (if any): {incident.get('root_cause') or 'none recorded'}\n\n"
            "Using the 5-Why method, identify the likely root causes. "
            "Format as a numbered Why chain. Only use the data provided."
        )
        reply = await ask_ai(prompt, max_tokens=1500)
        return {"ok": True, "incident_ref": incident.get("incident_ref"), "result": reply}
    finally:
        db.close()


async def draft_corrective_actions(incident_id: int, org_id: int | None = None) -> dict:
    """AI drafts corrective/preventive actions as structured records."""
    db = get_db()
    try:
        incident = _incident_detail(db, incident_id)
        if not incident:
            return {"ok": False, "message": "incident not found"}
        if org_id and incident.get("org_id") and incident["org_id"] != org_id:
            return {"ok": False, "message": "incident not found"}
        prompt = (
            f"Incident: {incident.get('title')}\n"
            f"Root cause: {incident.get('root_cause') or 'not recorded'}\n\n"
            "Propose 2-4 corrective/preventive actions. Return ONLY a JSON array of objects "
            'with keys: title, description, type ("corrective"|"preventive"), '
            "suggested_role, due_in_days. No prose."
        )
        raw = await ask_ai(prompt, max_tokens=1200)
        actions = _safe_json_array(raw)
        return {"ok": True, "incident_ref": incident.get("incident_ref"), "draft_actions": actions}
    finally:
        db.close()


async def predictive_risk(org_id: int | None = None) -> dict:
    """Monthly trend analysis -> risk forecast."""
    db = get_db()
    try:
        trend = _monthly_trend(db, org_id=org_id)
        risks = _risk_summary(db, org_id)
        prompt = (
            f"Incident trend (last {len(trend)} months):\n{_fmt(trend)}\n\n"
            f"Open risk register (top by residual score):\n{_fmt(risks)}\n\n"
            "Produce a concise risk forecast for the next month: which risk "
            "categories are trending up, and what preventive actions are "
            "suggested. Only use the data provided."
        )
        reply = await ask_ai(prompt, max_tokens=1500)
        return {"ok": True, "result": reply}
    finally:
        db.close()


async def training_gap_detection(org_id: int | None = None) -> dict:
    """Cross-reference incidents with training records."""
    db = get_db()
    try:
        incidents = _recent_incidents(db, 15, org_id)
        competencies = _training_summary(db)
        prompt = (
            f"Recent incidents:\n{_fmt(incidents)}\n\n"
            f"Staff competencies (name -> count certified):\n{_fmt(competencies)}\n\n"
            "Identify competency gaps: which incident themes lack matching "
            "training coverage? Suggest training needs. Only use the data provided."
        )
        reply = await ask_ai(prompt, max_tokens=1500)
        return {"ok": True, "result": reply}
    finally:
        db.close()


async def statutory_report_generator(report_type: str, org_id: int | None = None) -> dict:
    """Auto-draft NSSA / EMA / ZRP submissions from live data."""
    db = get_db()
    try:
        incidents = _recent_incidents(db, 10, org_id)
        prompt = (
            f"Draft a statutory {report_type.upper()} submission for a "
            f"telecommunications company, using ONLY these incidents:\n"
            f"{_fmt(incidents)}\n\n"
            "Include: incident summary table, statutory reporting obligations, "
            "and a highlighted data-gaps section for SHE Manager review."
        )
        reply = await ask_ai(prompt, max_tokens=2500)
        return {"ok": True, "report_type": report_type, "result": reply}
    finally:
        db.close()


async def chat(question: str, org_id: int | None = None) -> dict:
    """Grounded free-text Q&A over platform data."""
    db = get_db()
    try:
        context = {
            "open_incidents": _recent_incidents(db, 10, org_id),
            "top_risks": _risk_summary(db, org_id),
            "deadlines": _deadline_snapshot(db, org_id),
            "overdue_items": _overdue_items(db, org_id),
            "competencies": _training_summary(db),
        }
        prompt = (
            f"Platform data snapshot:\n{_fmt(context)}\n\n"
            f"Question: {question}\n\n"
            "Answer ONLY from the data snapshot. If the data does not contain "
            "the answer, say so and suggest what data would help."
        )
        reply = await ask_ai(prompt, max_tokens=2000)
        return {"ok": True, "result": reply}
    finally:
        db.close()


async def daily_briefing(org_id: int | None = None) -> dict:
    """Page-load briefing: deadlines, overdue, compliance alerts."""
    db = get_db()
    try:
        snapshot = {
            "upcoming_deadlines": _deadline_snapshot(db, org_id),
            "overdue_items": _overdue_items(db, org_id),
            "top_risks": _risk_summary(db, org_id)[:5],
        }
        prompt = (
            f"Generate a 5-bullet daily SHE briefing from this snapshot:\n"
            f"{_fmt(snapshot)}\n\n"
            "Order by urgency. Only use the data provided."
        )
        reply = await ask_ai(prompt, max_tokens=1000)
        return {"ok": True, "result": reply}
    finally:
        db.close()
