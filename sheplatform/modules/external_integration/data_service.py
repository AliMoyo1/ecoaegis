"""B5 external integration and portal submission data service.

Implements ARCHITECTURE.md Section 5 + 6:
- Outbound stub dispatcher (ThemisIQ, ERP, LMS, EMA/NSSA/ZRP portals, Board, Comms)
- Inbound webhook verification for ThemisIQ signed webhooks
- themisiq_links entity mapping
- integration_queue + integration_logs
- submission_deliveries for statutory reports

All real network calls are stubbed in this slice; we record every attempt to
integration_logs so the platform can replay/review.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sheplatform.database import get_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _get_secret(endpoint_id: int, secret_name: str = "api_key") -> str | None:
    with get_db() as db:
        row = db.execute(
            "SELECT secret_value FROM integration_secrets WHERE endpoint_id=%s AND secret_name=%s",
            (endpoint_id, secret_name),
        ).fetchone()
        return row["secret_value"] if row else None


def _log_db(
    db,
    endpoint_key: str,
    direction: str,
    idempotency_key: str | None,
    request_payload: dict,
    response_payload: dict | None,
    status_code: int | None,
    success: bool,
    error_message: str | None,
    duration_ms: int = 0,
):
    db.execute(
        """INSERT INTO integration_logs
           (endpoint_key, direction, idempotency_key, request_payload,
            response_payload, status_code, success, error_message, duration_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            endpoint_key,
            direction,
            idempotency_key,
            json.dumps(request_payload, default=str),
            json.dumps(response_payload or {}, default=str),
            status_code,
            success,
            error_message,
            duration_ms,
        ),
    )


def _log(
    endpoint_key: str,
    direction: str,
    idempotency_key: str | None,
    request_payload: dict,
    response_payload: dict | None,
    status_code: int | None,
    success: bool,
    error_message: str | None,
    duration_ms: int = 0,
):
    with get_db() as db:
        _log_db(
            db, endpoint_key, direction, idempotency_key, request_payload,
            response_payload, status_code, success, error_message, duration_ms,
        )


def _enqueue(
    endpoint_key: str,
    entity_type: str,
    entity_id: int,
    operation: str,
    payload: dict,
    created_by: int | None,
) -> int:
    idem = str(uuid.uuid4())
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO integration_queue
               (endpoint_key, entity_type, entity_id, operation, payload,
                idempotency_key, status, next_retry_at, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s)
               RETURNING id""",
            (
                endpoint_key,
                entity_type,
                entity_id,
                operation,
                json.dumps(payload, default=str),
                idem,
                None,
                created_by,
            ),
        )
        return cur.fetchone()["id"]


def _queue_next_retry(queue_id: int, error: str):
    with get_db() as db:
        row = db.execute(
            "SELECT attempts FROM integration_queue WHERE id=%s", (queue_id,)
        ).fetchone()
        attempts = (row["attempts"] if row else 0) + 1
        backoff_seconds = min(2 ** attempts, 3600)
        next_retry = (datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)).isoformat()
        db.execute(
            """UPDATE integration_queue
               SET attempts=%s, last_error=%s, status='pending',
                   next_retry_at=%s, updated_at=%s
               WHERE id=%s""",
            (attempts, error, next_retry, _utcnow(), queue_id),
        )


# ---------------------------------------------------------------------------
# Endpoint management
# ---------------------------------------------------------------------------
def list_endpoints(org_id: int | None = None) -> list[dict]:
    with get_db() as db:
        sql = "SELECT * FROM integration_endpoints"
        params: list[Any] = []
        if org_id is not None:
            sql += " WHERE org_id=%s"
            params.append(org_id)
        sql += " ORDER BY system_type, name"
        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_endpoint(endpoint_key: str, org_id: int | None = None) -> dict | None:
    with get_db() as db:
        sql = "SELECT * FROM integration_endpoints WHERE endpoint_key=%s"
        params: list[Any] = [endpoint_key]
        if org_id is not None:
            sql += " AND org_id=%s"
            params.append(org_id)
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else None


def create_endpoint(
    endpoint_key: str,
    name: str,
    system_type: str,
    direction: str,
    base_url: str | None,
    auth_type: str,
    auth_config: dict,
    headers: dict,
    timeout_seconds: int,
    rate_limit_per_minute: int,
    org_id: int,
    created_by: int,
) -> int:
    with get_db() as db:
        cur = db.execute(
            """INSERT INTO integration_endpoints
               (endpoint_key, name, system_type, direction, base_url, auth_type,
                auth_config, headers, timeout_seconds, rate_limit_per_minute,
                org_id, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                endpoint_key,
                name,
                system_type,
                direction,
                base_url,
                auth_type,
                json.dumps(auth_config, default=str),
                json.dumps(headers, default=str),
                timeout_seconds,
                rate_limit_per_minute,
                org_id,
                created_by,
            ),
        )
        return cur.fetchone()["id"]


def upsert_endpoint_secret(endpoint_id: int, secret_name: str, secret_value: str):
    with get_db() as db:
        existing = db.execute(
            "SELECT id FROM integration_secrets WHERE endpoint_id=%s AND secret_name=%s",
            (endpoint_id, secret_name),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE integration_secrets SET secret_value=%s, updated_at=%s WHERE id=%s",
                (secret_value, _utcnow(), existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO integration_secrets (endpoint_id, secret_name, secret_value) VALUES (%s, %s, %s)",
                (endpoint_id, secret_name, secret_value),
            )


# ---------------------------------------------------------------------------
# ThemisIQ webhook verification (ARCHITECTURE.md 6.3)
# ---------------------------------------------------------------------------
def verify_themisiq_signature(raw_body: bytes, signature_header: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature from ThemisIQ outbound webhook."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    received = signature_header[7:]
    return hmac.compare_digest(expected, received)


def handle_themisiq_webhook(payload: dict) -> dict:
    """Process an inbound ThemisIQ event and maintain cross-module links."""
    event_type = payload.get("event_type", "")
    source_entity_type = payload.get("source_entity_type")
    source_entity_id = payload.get("source_entity_id")
    data = payload.get("data") or {}

    if event_type.startswith("erm.risk"):
        return _mirror_erm_event(event_type, source_entity_type, source_entity_id, data)
    if event_type.startswith("orm.event"):
        return _mirror_orm_event(event_type, source_entity_type, source_entity_id, data)
    if event_type.startswith("bcm.incident"):
        return {"handled": False, "reason": "bcm incident mirroring not yet implemented"}
    return {"handled": False, "reason": "unrecognised event type"}


def _mirror_erm_event(event_type: str, source_entity_type: str | None, source_entity_id: int | None, data: dict) -> dict:
    with get_db() as db:
        existing = db.execute(
            """SELECT id FROM themisiq_links
               WHERE themis_entity_type=%s AND themis_entity_id=%s AND relationship='related'""",
            (source_entity_type, source_entity_id),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE themisiq_links SET last_synced_at=%s, last_sync_hash=%s, sync_error=NULL WHERE id=%s",
                (_utcnow(), _hash_payload(data), existing["id"]),
            )
            return {"handled": True, "action": "updated", "link_id": existing["id"]}
        cur = db.execute(
            """INSERT INTO themisiq_links
               (she_entity_type, she_entity_id, themis_entity_type, themis_entity_id,
                relationship, direction, last_synced_at, last_sync_hash)
               VALUES (%s, %s, %s, %s, 'related', 'themis_to_she', %s, %s)
               RETURNING id""",
            (
                "risk",
                data.get("she_risk_id") or source_entity_id,
                source_entity_type,
                source_entity_id,
                _utcnow(),
                _hash_payload(data),
            ),
        )
        return {"handled": True, "action": "created", "link_id": cur.fetchone()["id"]}


def _mirror_orm_event(event_type: str, source_entity_type: str | None, source_entity_id: int | None, data: dict) -> dict:
    with get_db() as db:
        existing = db.execute(
            """SELECT id FROM themisiq_links
               WHERE themis_entity_type=%s AND themis_entity_id=%s AND relationship='derived_from'""",
            (source_entity_type, source_entity_id),
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE themisiq_links SET last_synced_at=%s, last_sync_hash=%s, sync_error=NULL WHERE id=%s",
                (_utcnow(), _hash_payload(data), existing["id"]),
            )
            return {"handled": True, "action": "updated", "link_id": existing["id"]}
        cur = db.execute(
            """INSERT INTO themisiq_links
               (she_entity_type, she_entity_id, themis_entity_type, themis_entity_id,
                relationship, direction, last_synced_at, last_sync_hash)
               VALUES (%s, %s, %s, %s, 'derived_from', 'themis_to_she', %s, %s)
               RETURNING id""",
            (
                "incident",
                data.get("she_incident_id") or source_entity_id,
                source_entity_type,
                source_entity_id,
                _utcnow(),
                _hash_payload(data),
            ),
        )
        return {"handled": True, "action": "created", "link_id": cur.fetchone()["id"]}


# ---------------------------------------------------------------------------
# Outbound stubs
# ---------------------------------------------------------------------------
def push_to_themisiq(
    endpoint_key: str,
    entity_type: str,
    entity_id: int,
    payload: dict,
    created_by: int | None,
) -> dict:
    """Enqueue a push to ThemisIQ and stub a 202 response."""
    queue_id = _enqueue(endpoint_key, entity_type, entity_id, "push", payload, created_by)
    _log(
        endpoint_key,
        "outbound",
        payload.get("idempotency_key"),
        payload,
        {"status": "accepted", "queue_id": queue_id},
        202,
        True,
        None,
        0,
    )
    return {"queue_id": queue_id, "status": "queued", "upstream_status": 202}


def submit_report_to_channel(report_id: int, channel_key: str, created_by: int | None) -> dict:
    """Queue a statutory report submission via a registered channel."""
    idem = f"submit-{report_id}-{channel_key}-{_utcnow()}"
    with get_db() as db:
        channel = db.execute(
            "SELECT * FROM submission_channels WHERE channel_key=%s", (channel_key,)
        ).fetchone()
        if not channel:
            raise ValueError(f"Unknown submission channel: {channel_key}")
        report = db.execute(
            "SELECT id, status, rendered_text FROM statutory_reports WHERE id=%s", (report_id,)
        ).fetchone()
        if not report:
            raise ValueError(f"Unknown report: {report_id}")
        if report["status"] != "submitted":
            raise ValueError("Report must be submitted before delivery")
        cur = db.execute(
            """INSERT INTO submission_deliveries
               (report_id, channel_id, channel_key, status, tracking_ref)
               VALUES (%s, %s, %s, 'queued', %s)
               RETURNING id""",
            (report_id, channel["id"], channel_key, idem),
        )
        delivery_id = cur.fetchone()["id"]
    payload = {
        "delivery_id": delivery_id,
        "report_id": report_id,
        "channel_key": channel_key,
        "channel_type": channel["channel_type"],
        "authority": channel["authority"],
        "rendered_text": report["rendered_text"],
        "idempotency_key": idem,
    }
    channel_dict = dict(channel)
    endpoint_key = channel_dict.get("endpoint_id") and _endpoint_key_by_id(channel_dict["endpoint_id"])
    if endpoint_key:
        queue_id = _enqueue(endpoint_key, "submission_delivery", delivery_id, "submit", payload, created_by)
        payload["queue_id"] = queue_id
    _log(
        channel_key,
        "outbound",
        idem,
        payload,
        {"status": "queued", "delivery_id": delivery_id},
        202,
        True,
        None,
        0,
    )
    return {"delivery_id": delivery_id, "status": "queued", "channel_key": channel_key}


def _endpoint_key_by_id(endpoint_id: int) -> str | None:
    with get_db() as db:
        row = db.execute(
            "SELECT endpoint_key FROM integration_endpoints WHERE id=%s", (endpoint_id,)
        ).fetchone()
        return row["endpoint_key"] if row else None


def _record_submission_status_db(db, delivery_id: int, status: str, response_payload: dict | None, error_message: str | None):
    db.execute(
        """UPDATE submission_deliveries
           SET status=%s, response_payload=%s, error_message=%s,
               acknowledged_at=CASE WHEN %s='acknowledged' THEN %s ELSE acknowledged_at END,
               dispatched_at=CASE WHEN %s IN ('sent','delivered','acknowledged','failed','rejected') THEN COALESCE(dispatched_at, %s) ELSE dispatched_at END,
               updated_at=%s
           WHERE id=%s""",
        (
            status,
            json.dumps(response_payload or {}, default=str),
            error_message,
            status,
            _utcnow(),
            status,
            _utcnow(),
            _utcnow(),
            delivery_id,
        ),
    )


def record_submission_status(delivery_id: int, status: str, response_payload: dict | None, error_message: str | None):
    """Update a submission delivery after a stubbed upstream response."""
    with get_db() as db:
        _record_submission_status_db(db, delivery_id, status, response_payload, error_message)


def list_submission_channels(org_id: int | None = None) -> list[dict]:
    with get_db() as db:
        sql = "SELECT * FROM submission_channels"
        params: list[Any] = []
        if org_id is not None:
            sql += " WHERE org_id=%s"
            params.append(org_id)
        sql += " ORDER BY authority, name"
        rows = db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


_DEFAULT_CHANNEL_TYPES = {
    "nssa_email": ("email", "nssa_portal"),
    "ema_portal": ("portal", "ema_portal"),
    "zrp_email": ("email", "zrp_portal"),
}


def seed_default_channels(org_id: int):
    """Create default statutory submission channels for Zimbabwe regulators."""
    defaults = [
        ("nssa_email", "NSSA Email", "NSSA"),
        ("ema_portal", "EMA Portal", "EMA"),
        ("zrp_email", "ZRP Email", "ZRP"),
    ]
    with get_db() as db:
        for key, name, authority in defaults:
            ctype, system_type = _DEFAULT_CHANNEL_TYPES[key]
            ep = db.execute(
                "SELECT id FROM integration_endpoints WHERE endpoint_key=%s",
                (key,),
            ).fetchone()
            if not ep:
                cur = db.execute(
                    """INSERT INTO integration_endpoints
                       (endpoint_key, name, system_type, direction, auth_type, base_url, org_id, active)
                       VALUES (%s, %s, %s, 'outbound', 'none', NULL, %s, TRUE)
                       RETURNING id""",
                    (key, name, system_type, org_id),
                )
                ep_id = cur.fetchone()["id"]
            else:
                ep_id = ep["id"]
            exists = db.execute(
                "SELECT id FROM submission_channels WHERE channel_key=%s AND org_id=%s",
                (key, org_id),
            ).fetchone()
            if not exists:
                db.execute(
                    """INSERT INTO submission_channels
                       (channel_key, name, authority, channel_type, endpoint_id, org_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (key, name, authority, ctype, ep_id, org_id),
                )


# ---------------------------------------------------------------------------
# Queue processing (called by scheduler/cron)
# ---------------------------------------------------------------------------
def process_pending_queue(limit: int = 10) -> list[dict]:
    """Mark pending queue items as completed after a stubbed attempt."""
    results = []
    with get_db() as db:
        rows = db.execute(
            """SELECT * FROM integration_queue
               WHERE status='pending' AND (next_retry_at IS NULL OR next_retry_at <= %s)
               ORDER BY created_at
               LIMIT %s""",
            (_utcnow(), limit),
        ).fetchall()
        for row in rows:
            # Stub: pretend upstream accepted everything; real implementation swaps here.
            db.execute(
                """UPDATE integration_queue
                   SET status='completed', attempts=attempts+1, updated_at=%s
                   WHERE id=%s""",
                (_utcnow(), row["id"]),
            )
            _log_db(
                db,
                row["endpoint_key"],
                "outbound",
                row["idempotency_key"],
                json.loads(row["payload"] or "{}"),
                {"status": "completed"},
                200,
                True,
                None,
                0,
            )
            if row["entity_type"] == "submission_delivery":
                _record_submission_status_db(db, row["entity_id"], "sent", {"upstream": "accepted"}, None)
            results.append({"queue_id": row["id"], "status": "completed"})
    return results



def get_submission_delivery(delivery_id: int) -> dict | None:
    with get_db() as db:
        row = db.execute(
            "SELECT * FROM submission_deliveries WHERE id=%s", (delivery_id,)
        ).fetchone()
        return dict(row) if row else None
