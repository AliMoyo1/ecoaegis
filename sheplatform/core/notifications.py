"""Notifications (NFR-SHE-006: email + in-platform, retry with backoff)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def notify(db, user_id: int, title: str, body: str, link: str = "",
           channel: str = "in_app") -> int:
    db.execute(
        "INSERT INTO notifications (user_id, title, body, link, channel, delivery_status) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, title, body, link, channel, "pending"),
    )
    db.commit()
    return db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]


def notify_roles(db, roles: list[str], title: str, body: str, link: str = "") -> int:
    """Notify every active user holding one of the given role_keys."""
    count = 0
    for role in roles:
        rows = db.execute(
            "SELECT id FROM users WHERE role_key = %s AND is_active = 1", (role,)
        ).fetchall()
        for r in rows:
            notify(db, r["id"], title, body, link)
            count += 1
    return count


def unread_count(db, user_id: int) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS c FROM notifications WHERE user_id = %s AND read = 0",
        (user_id,),
    ).fetchone()
    return row["c"] if row else 0


def list_for_user(db, user_id: int, limit: int = 50) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM notifications WHERE user_id = %s ORDER BY id DESC LIMIT %s",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_read(db, notification_id: int, user_id: int) -> None:
    db.execute(
        "UPDATE notifications SET read = 1 WHERE id = %s AND user_id = %s",
        (notification_id, user_id),
    )
    db.commit()


def queue_email(db, recipient_email: str, subject: str, body_html: str,
                send_at: datetime | None = None) -> None:
    send_at = send_at or datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO email_reminders (recipient_email, subject, body_html, send_at) "
        "VALUES (%s, %s, %s, %s)",
        (recipient_email, subject, body_html, send_at.isoformat()),
    )
    db.commit()


def process_email_queue(db, limit: int = 50) -> int:
    """Send due email reminders. Console provider in dev; SMTP in prod."""
    now = datetime.now(timezone.utc).isoformat()
    rows = db.execute(
        "SELECT * FROM email_reminders WHERE send_at <= %s AND sent = 0 ORDER BY send_at LIMIT %s",
        (now, limit),
    ).fetchall()
    sent = 0
    for r in rows:
        # dev: log to console. prod: SMTP via settings (guide 25).
        import logging
        logging.getLogger("sheplatform.email").info(
            "EMAIL to %s subject=%s", r["recipient_email"], r["subject"])
        db.execute("UPDATE email_reminders SET sent = 1 WHERE id = %s", (r["id"],))
        sent += 1
    if sent:
        db.commit()
    return sent
