"""Lone worker scheduler (guide C2): missed check-in escalation.

Runs server-side on a timer, same shape as modules/incidents/scheduler.py.
This is deliberately the reliable core: it does not depend on the worker's
phone being online, only on the deadline the worker set when they checked
in - a silent or dead phone at a remote tower still escalates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from sheplatform.core.messaging import send_sms
from sheplatform.core.notifications import notify_roles

logger = logging.getLogger("sheplatform.scheduler")


def escalate_session(db, session: dict) -> dict:
    """Shared by the scheduled lapsed-check-in sweep and the man-down route
    (an early, worker/device-triggered escalation) - one escalation path,
    two triggers.
    """
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE lone_worker_checkins SET status = 'escalated', escalated_at = %s WHERE id = %s",
        (now, session["id"]))
    db.commit()

    worker = db.execute("SELECT * FROM users WHERE id = %s", (session["worker_id"],)).fetchone()
    worker_name = f"{worker['first_name']} {worker['last_name']}" if worker else "A worker"
    location = session.get("location") or "location not recorded"
    coords = ""
    if session.get("latitude") and session.get("longitude"):
        coords = f" (last known GPS: {session['latitude']}, {session['longitude']})"
    message = (
        f"{worker_name} missed their lone-worker check-in ({session['session_ref']}). "
        f"Last known location: {location}{coords}."
    )

    notify_roles(db, ["she_manager"], f"Lone worker check-in missed: {session['session_ref']}",
                message, link=f"/lone-worker/api/{session['id']}")

    sms_result = {"ok": False, "message": "no nominated contact"}
    if session.get("nominated_contact_phone"):
        sms_result = send_sms(session["nominated_contact_phone"], message)

    return {"escalated": True, "session_id": session["id"], "sms": sms_result}


def check_lapsed_checkins(db) -> list[dict]:
    """Active sessions past their deadline: escalate every one."""
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT * FROM lone_worker_checkins WHERE status = 'active' AND expected_checkin_at < %s",
        (now,)).fetchall()
    results = []
    for r in rows:
        results.append(escalate_session(db, dict(r)))
    return results


def start_scheduler(db_factory):
    scheduler = BackgroundScheduler()

    def job_lapsed_checkins():
        db = db_factory()
        try:
            alerts = check_lapsed_checkins(db)
            if alerts:
                logger.info("lone worker escalations: %s", len(alerts))
        finally:
            db.close()

    scheduler.add_job(job_lapsed_checkins, "interval", minutes=2, id="lone_worker_checkin_check")
    scheduler.start()
    return scheduler
