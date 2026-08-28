"""Atomic, privacy-minimal Mapbox browser-session admission.

The provider account is billed when the browser constructs a Mapbox Map. This
service is therefore the only path that releases the public browser token. It
stores one opaque admission identifier plus provider, UTC month, organisation,
and decision. It never stores a token, viewport, coordinate, feature, IP,
referrer, or browser identifier.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from sheplatform.config import settings
from sheplatform.core.audit import log_audit

PROVIDER = "mapbox"
NONCE_SALT = "ecoaegis-map-provider-v1"


class InvalidProviderNonce(ValueError):
    """Raised when a page nonce is missing, expired, or belongs elsewhere."""


@dataclass(frozen=True)
class AdmissionDecision:
    admitted: bool
    repeated: bool = False


def _session_binding(session_token: str) -> str:
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY, salt=NONCE_SALT)


def issue_page_nonce(*, user_id: int, org_id: int, session_token: str) -> str:
    """Return a signed, random, short-lived nonce tied to one session and org."""
    if not user_id or not org_id or not session_token:
        raise InvalidProviderNonce("A valid tenant session is required")
    payload = {
        "admission_id": secrets.token_urlsafe(32),
        "user_id": int(user_id),
        "org_id": int(org_id),
        "session": _session_binding(session_token),
    }
    return _serializer().dumps(payload)


def _decode_page_nonce(*, nonce: str, user_id: int, org_id: int,
                       session_token: str) -> str:
    if not nonce or not user_id or not org_id or not session_token:
        raise InvalidProviderNonce("The map provider request is invalid")
    try:
        payload = _serializer().loads(
            nonce, max_age=settings.MAP_PROVIDER_NONCE_TTL_SECONDS)
    except SignatureExpired as exc:
        raise InvalidProviderNonce("The map provider request expired; reload the page") from exc
    except BadSignature as exc:
        raise InvalidProviderNonce("The map provider request is invalid") from exc
    expected = (
        int(user_id), int(org_id), _session_binding(session_token)
    )
    actual = (
        payload.get("user_id"), payload.get("org_id"), payload.get("session")
    )
    admission_id = payload.get("admission_id")
    if actual != expected or not isinstance(admission_id, str) or len(admission_id) < 32:
        raise InvalidProviderNonce("The map provider request is invalid")
    return admission_id


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _begin_write(db) -> None:
    if not settings.is_postgres():
        db.execute("BEGIN IMMEDIATE")


def _existing_decision(db, admission_id: str, month: str, org_id: int):
    row = db.execute(
        "SELECT provider, billing_month_utc, org_id, decision "
        "FROM map_provider_admissions WHERE admission_id = %s",
        (admission_id,),
    ).fetchone()
    if row is None:
        return None
    if (row["provider"], row["billing_month_utc"], int(row["org_id"])) != (
            PROVIDER, month, int(org_id)):
        raise InvalidProviderNonce("The map provider request is invalid")
    return AdmissionDecision(row["decision"] == "admitted", repeated=True)


def _audit_budget_transition(db, *, action: str, user_id: int, org_id: int,
                             month: str, before: int, after: int) -> None:
    """Record a privacy-minimal threshold transition in the audit hash chain."""
    log_audit(
        db,
        user_id=user_id,
        org_id=org_id,
        action=f"map.provider.{action}",
        entity_type="map_provider_monthly_usage",
        old_value={"provider": PROVIDER, "billing_month_utc": month,
                   "admitted_loads": before},
        new_value={"provider": PROVIDER, "billing_month_utc": month,
                   "admitted_loads": after},
        commit=False,
    )


def admit_provider_session(db, *, nonce: str, user_id: int, org_id: int,
                           session_token: str) -> AdmissionDecision:
    """Atomically admit at most one billed initialization for a page nonce."""
    admission_id = _decode_page_nonce(
        nonce=nonce, user_id=user_id, org_id=org_id, session_token=session_token)
    now = _utc_now()
    month = now.strftime("%Y-%m")
    now_text = now.isoformat()
    _begin_write(db)
    try:
        existing = _existing_decision(db, admission_id, month, org_id)
        if existing is not None:
            db.commit()
            return existing

        db.execute(
            "INSERT INTO map_provider_monthly_usage "
            "(provider, billing_month_utc, admitted_loads, updated_at) "
            "VALUES (%s,%s,0,%s) ON CONFLICT (provider, billing_month_utc) DO NOTHING",
            (PROVIDER, month, now_text),
        )
        lock_sql = (
            "SELECT admitted_loads, blocked_recorded_at FROM map_provider_monthly_usage "
            "WHERE provider = %s AND billing_month_utc = %s"
        )
        if settings.is_postgres():
            lock_sql += " FOR UPDATE"
        usage = db.execute(lock_sql, (PROVIDER, month)).fetchone()
        if usage is None:
            raise RuntimeError("Map provider usage counter could not be locked")

        # PostgreSQL callers may have waited on the shared monthly row after
        # their first idempotency check. Recheck while holding that row lock.
        existing = _existing_decision(db, admission_id, month, org_id)
        if existing is not None:
            db.commit()
            return existing

        count = int(usage["admitted_loads"])
        if count >= settings.MAP_PROVIDER_MONTHLY_LIMIT:
            first_block = usage["blocked_recorded_at"] is None
            db.execute(
                "INSERT INTO map_provider_admissions "
                "(admission_id, provider, billing_month_utc, org_id, decision, created_at) "
                "VALUES (%s,%s,%s,%s,'denied',%s)",
                (admission_id, PROVIDER, month, org_id, now_text),
            )
            db.execute(
                "UPDATE map_provider_monthly_usage SET "
                "blocked_recorded_at = COALESCE(blocked_recorded_at, %s), updated_at = %s "
                "WHERE provider = %s AND billing_month_utc = %s",
                (now_text, now_text, PROVIDER, month),
            )
            if first_block:
                _audit_budget_transition(
                    db, action="blocked", user_id=user_id, org_id=org_id,
                    month=month, before=count, after=count)
            db.commit()
            return AdmissionDecision(False)

        new_count = count + 1
        crossed_warning = (
            count < settings.MAP_PROVIDER_WARNING_LOADS <= new_count)
        crossed_critical = (
            count < settings.MAP_PROVIDER_CRITICAL_LOADS <= new_count)
        db.execute(
            "UPDATE map_provider_monthly_usage SET admitted_loads = %s, "
            "warning_recorded_at = CASE WHEN %s >= %s THEN "
            "COALESCE(warning_recorded_at, %s) ELSE warning_recorded_at END, "
            "critical_recorded_at = CASE WHEN %s >= %s THEN "
            "COALESCE(critical_recorded_at, %s) ELSE critical_recorded_at END, "
            "updated_at = %s WHERE provider = %s AND billing_month_utc = %s",
            (new_count, new_count, settings.MAP_PROVIDER_WARNING_LOADS, now_text,
             new_count, settings.MAP_PROVIDER_CRITICAL_LOADS, now_text,
             now_text, PROVIDER, month),
        )
        db.execute(
            "INSERT INTO map_provider_admissions "
            "(admission_id, provider, billing_month_utc, org_id, decision, created_at) "
            "VALUES (%s,%s,%s,%s,'admitted',%s)",
            (admission_id, PROVIDER, month, org_id, now_text),
        )
        if crossed_warning:
            _audit_budget_transition(
                db, action="warning", user_id=user_id, org_id=org_id,
                month=month, before=count, after=new_count)
        if crossed_critical:
            _audit_budget_transition(
                db, action="critical", user_id=user_id, org_id=org_id,
                month=month, before=count, after=new_count)
        db.commit()
        return AdmissionDecision(True)
    except Exception:
        db.rollback()
        raise


def prune_old_admissions(db, *, months_to_keep: int = 2) -> int:
    """Remove opaque idempotency rows older than the current and prior month."""
    if months_to_keep != 2:
        raise ValueError("EcoAegis retains exactly two admission months")
    now = _utc_now()
    current_month_index = now.year * 12 + now.month - 1
    oldest_index = current_month_index - (months_to_keep - 1)
    oldest_month = f"{oldest_index // 12:04d}-{oldest_index % 12 + 1:02d}"
    cursor = db.execute(
        "DELETE FROM map_provider_admissions WHERE billing_month_utc < %s",
        (oldest_month,),
    )
    db.commit()
    return cursor.rowcount


def estimated_monthly_cost_usd(admitted_loads: int) -> float:
    """Current GL JS tier estimate through the enforced 200,000 hard maximum."""
    count = max(0, min(int(admitted_loads), 200_000))
    if count <= 50_000:
        return 0.0
    if count <= 100_000:
        return round((count - 50_000) * 0.005, 2)
    return round(250 + (count - 100_000) * 0.004, 2)


def provider_budget_summary(db) -> dict:
    """Return the provider-account budget view for designated administrators."""
    month = _utc_now().strftime("%Y-%m")
    row = db.execute(
        "SELECT admitted_loads, warning_recorded_at, critical_recorded_at, "
        "blocked_recorded_at, updated_at FROM map_provider_monthly_usage "
        "WHERE provider = %s AND billing_month_utc = %s",
        (PROVIDER, month),
    ).fetchone()
    count = int(row["admitted_loads"]) if row else 0
    if count >= settings.MAP_PROVIDER_MONTHLY_LIMIT:
        state = "blocked"
    elif count >= settings.MAP_PROVIDER_CRITICAL_LOADS:
        state = "critical"
    elif count >= settings.MAP_PROVIDER_WARNING_LOADS:
        state = "warning"
    else:
        state = "healthy"
    return {
        "provider": PROVIDER,
        "billing_month_utc": month,
        "state": state,
        "admitted_loads": count,
        "remaining_loads": max(0, settings.MAP_PROVIDER_MONTHLY_LIMIT - count),
        "warning_loads": settings.MAP_PROVIDER_WARNING_LOADS,
        "critical_loads": settings.MAP_PROVIDER_CRITICAL_LOADS,
        "monthly_limit": settings.MAP_PROVIDER_MONTHLY_LIMIT,
        "estimated_cost_usd": estimated_monthly_cost_usd(count),
        "warning_recorded_at": row["warning_recorded_at"] if row else None,
        "critical_recorded_at": row["critical_recorded_at"] if row else None,
        "blocked_recorded_at": row["blocked_recorded_at"] if row else None,
        "updated_at": row["updated_at"] if row else None,
    }
