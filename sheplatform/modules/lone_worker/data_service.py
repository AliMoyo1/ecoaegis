"""Lone worker / man-down data service (guide C2).

A check-in session is the reliable core: a worker states an expected
duration, the server expects a check-in by that deadline regardless of
whether the phone stays online (modules/lone_worker/scheduler.py enforces
this - it runs server-side, not on the worker's device).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sheplatform.database import resolve_org


def _next_session_ref(db) -> str:
    year = datetime.now(timezone.utc).strftime("%Y")
    prefix = f"LWC-{year}-"
    row = db.execute(
        "SELECT session_ref FROM lone_worker_checkins WHERE session_ref LIKE %s ORDER BY id DESC LIMIT 1",
        (f"{prefix}%",),
    ).fetchone()
    if row is None:
        return f"{prefix}001"
    match = re.search(r"(\d+)$", row["session_ref"])
    nxt = (int(match.group(1)) if match else 0) + 1
    return f"{prefix}{nxt:03d}"


def start_checkin(db, *, worker_id: int, expected_duration_minutes: int,
                  location: str = "", latitude: float | None = None,
                  longitude: float | None = None,
                  nominated_contact_name: str = "", nominated_contact_phone: str = "",
                  org_id: int | None = None) -> dict:
    org_id = resolve_org(db, org_id, worker_id)
    ref = _next_session_ref(db)
    now = datetime.now(timezone.utc)
    expected_checkin_at = (now + timedelta(minutes=expected_duration_minutes)).isoformat()
    db.execute(
        "INSERT INTO lone_worker_checkins (session_ref, worker_id, expected_duration_minutes, "
        "location, latitude, longitude, nominated_contact_name, nominated_contact_phone, "
        "started_at, expected_checkin_at, status, org_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)",
        (ref, worker_id, expected_duration_minutes, location or None, latitude, longitude,
         nominated_contact_name or None, nominated_contact_phone or None,
         now.isoformat(), expected_checkin_at, org_id))
    db.commit()
    row = db.execute(
        "SELECT * FROM lone_worker_checkins WHERE session_ref = %s", (ref,)).fetchone()
    return dict(row)


def get_checkin(db, session_id: int) -> dict | None:
    row = db.execute(
        "SELECT * FROM lone_worker_checkins WHERE id = %s", (session_id,)).fetchone()
    return dict(row) if row else None


def _owned_active_session(db, session_id: int, worker_id: int) -> dict | None:
    row = db.execute(
        "SELECT * FROM lone_worker_checkins WHERE id = %s AND worker_id = %s AND status = 'active'",
        (session_id, worker_id)).fetchone()
    return dict(row) if row else None


def check_in(db, session_id: int, worker_id: int) -> dict:
    """One-tap 'I'm safe'. Only the worker who started the session can close it."""
    session = _owned_active_session(db, session_id, worker_id)
    if session is None:
        return {"ok": False, "message": "no active session found for this worker"}
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE lone_worker_checkins SET status = 'checked_in', last_checkin_at = %s WHERE id = %s",
        (now, session_id))
    db.commit()
    return {"ok": True, "session": get_checkin(db, session_id)}


def extend_checkin(db, session_id: int, worker_id: int, additional_minutes: int) -> dict:
    """False-alarm handling: push the deadline out without ending the session."""
    session = _owned_active_session(db, session_id, worker_id)
    if session is None:
        return {"ok": False, "message": "no active session found for this worker"}
    if additional_minutes <= 0:
        return {"ok": False, "message": "additional_minutes must be positive"}
    current_deadline = datetime.fromisoformat(session["expected_checkin_at"])
    new_deadline = (current_deadline + timedelta(minutes=additional_minutes)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE lone_worker_checkins SET expected_checkin_at = %s, last_checkin_at = %s WHERE id = %s",
        (new_deadline, now, session_id))
    db.commit()
    return {"ok": True, "session": get_checkin(db, session_id)}


def cancel_checkin(db, session_id: int, worker_id: int) -> dict:
    session = _owned_active_session(db, session_id, worker_id)
    if session is None:
        return {"ok": False, "message": "no active session found for this worker"}
    db.execute("UPDATE lone_worker_checkins SET status = 'cancelled' WHERE id = %s", (session_id,))
    db.commit()
    return {"ok": True, "session": get_checkin(db, session_id)}


def list_active_checkins(db, org_id: int | None) -> list[dict]:
    """Fails closed: no org, no rows."""
    if not org_id:
        return []
    rows = db.execute(
        "SELECT * FROM lone_worker_checkins WHERE org_id = %s AND status = 'active' "
        "ORDER BY expected_checkin_at", (org_id,)).fetchall()
    return [dict(r) for r in rows]
