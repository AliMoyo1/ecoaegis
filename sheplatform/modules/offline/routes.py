"""Offline/PWA sync API (B1).

Accepts batched reports queued on the client while offline and applies them
idempotently on the server. Supports incidents, observations and (later)
any module exposing a create_* function that accepts idempotency_key.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sheplatform.core.middleware import require_auth
from sheplatform.database import get_db
from sheplatform.modules.incidents import data_service as incident_service
from sheplatform.modules.observations import data_service as observation_service

router = APIRouter(prefix="/api/offline-sync", tags=["offline"])


# Map item type -> (create callable, required capability)
_HANDLERS = {
    "incident": (incident_service.create_incident, "incident.create"),
    "observation": (observation_service.create_observation, "observation.create"),
}


@router.post("")
@require_auth
async def offline_sync(request: Request):
    """Receive a JSON list of queued items captured offline."""
    try:
        items = await request.json()
    except Exception as exc:
        return JSONResponse({"ok": False, "message": f"invalid json: {exc}"}, status_code=400)

    if not isinstance(items, list):
        return JSONResponse({"ok": False, "message": "payload must be a list"}, status_code=400)

    user = request.state.user
    org_id = user.get("org_id")
    user_id = user["id"]

    results = []
    db = get_db()
    try:
        for item in items:
            result = _apply_item(db, item, user_id, org_id)
            results.append(result)
        return JSONResponse({"ok": True, "processed": len(results), "results": results})
    except Exception as exc:  # pragma: no cover - safety net; individual errors returned
        return JSONResponse({"ok": False, "message": str(exc), "results": results}, status_code=500)
    finally:
        db.close()


def _apply_item(db, item: dict, user_id: int, org_id: int | None) -> dict:
    item_type = item.get("type")
    if item_type not in _HANDLERS:
        return {"ok": False, "type": item_type, "message": "unsupported type"}

    handler, _cap = _HANDLERS[item_type]
    data = item.get("data", {})
    if not isinstance(data, dict):
        return {"ok": False, "type": item_type, "message": "data must be an object"}

    data.setdefault("reported_by", user_id)
    if org_id and not data.get("org_id"):
        data["org_id"] = org_id
    # Map client idempotencyKey to server idempotency_key
    if item.get("idempotencyKey"):
        data.setdefault("idempotency_key", item["idempotencyKey"])

    try:
        record = handler(db, **data)
        return {
            "ok": True,
            "type": item_type,
            "id": record.get("id"),
            "ref": record.get("incident_ref") or record.get("obs_ref"),
            "idempotent": record.get("_idempotent", False),
        }
    except Exception as exc:
        return {"ok": False, "type": item_type, "message": str(exc)}
