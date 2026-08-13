"""Authentication: bcrypt password hashing, itsdangerous session tokens.

Guide 5.3 - follows ThemisIQ pattern exactly. Sessions are stored hashed in
the DB; the raw token goes to the client cookie.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from sheplatform.config import settings
from sheplatform.database import get_db


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_session(db, user_id: int, ip: str = "", user_agent: str = "") -> str:
    raw = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS)
    db.execute(
        "INSERT INTO sessions (user_id, token_hash, ip_address, user_agent, mfa_verified, expires_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, token_hash, ip, user_agent, False, expires.isoformat()),
    )
    db.commit()
    return raw


def get_session_user(db, raw_token: str) -> dict | None:
    if not raw_token:
        return None
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    row = db.execute(
        "SELECT s.id AS session_id, u.id AS id, u.email, u.first_name, u.last_name, "
        "u.role_key, u.org_id, u.is_active, u.mfa_enabled, s.mfa_verified, s.expires_at "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = %s AND s.expires_at > %s",
        (token_hash, datetime.now(timezone.utc).isoformat()),
    ).fetchone()
    if row is None:
        return None
    # delete expired sessions for this user while we are here
    db.execute("DELETE FROM sessions WHERE user_id = %s AND expires_at <= %s",
               (row["id"], datetime.now(timezone.utc).isoformat()))
    db.commit()
    return dict(row)


def get_user_by_email(db, email: str) -> dict | None:
    row = db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(db, user_id: int) -> dict | None:
    row = db.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    return dict(row) if row else None


def update_last_login(db, user_id: int) -> None:
    db.execute("UPDATE users SET last_login = %s WHERE id = %s",
               (datetime.now(timezone.utc).isoformat(), user_id))
    db.commit()


def destroy_session(db, raw_token: str) -> None:
    if not raw_token:
        return
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    db.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
    db.commit()


# ---- Login rate limiting (audit S4 fix) ----
# guide 5.5 spec: 5 failed attempts per identifier (IP) per 5-minute window.
RATE_LIMIT_WINDOW_MINUTES = 5
RATE_LIMIT_MAX_ATTEMPTS = 5


def record_failed_login(db, identifier: str) -> None:
    # Written explicitly (not DEFAULT NOW()): SQLite's datetime('now') formats
    # as 'YYYY-MM-DD HH:MM:SS' (space, no offset), which does not compare
    # correctly as a string against Python's isoformat() 'T'-separated cutoff
    # below. Same convention as sla_deadline/expires_at elsewhere in core/.
    db.execute(
        "INSERT INTO login_attempts (identifier, created_at) VALUES (%s, %s)",
        (identifier, datetime.now(timezone.utc).isoformat()))
    db.commit()


def is_login_rate_limited(db, identifier: str) -> bool:
    if not identifier:
        return False
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=RATE_LIMIT_WINDOW_MINUTES)).isoformat()
    row = db.execute(
        "SELECT COUNT(*) AS c FROM login_attempts WHERE identifier = %s AND created_at > %s",
        (identifier, cutoff),
    ).fetchone()
    return (row["c"] if row else 0) >= RATE_LIMIT_MAX_ATTEMPTS
