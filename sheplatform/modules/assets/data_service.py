"""Asset register + telemetry ingestion data service (guide C4).

Run-hours are cumulative (a generator hour-meter reports a running total,
never a delta) - a reading SETS assets.total_run_hours, it never adds to
it, so a retried/duplicate transmission can't double-count.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sheplatform.core.notifications import notify_roles
from sheplatform.database import resolve_org

MIN_READING_INTERVAL_SECONDS = 60
FUEL_DROP_ANOMALY_THRESHOLD_PCT = 15
FUEL_DROP_ANOMALY_MAX_RUN_HOURS = 0.5


def create_asset(db, *, asset_ref: str, name: str, asset_type: str, site_id: int | None = None,
                 install_date: str = "", service_interval_hours: float | None = None,
                 esg_kpi_code: str = "", created_by: int | None = None,
                 org_id: int | None = None) -> dict:
    org_id = resolve_org(db, org_id, created_by)
    db.execute(
        "INSERT INTO assets (asset_ref, name, asset_type, site_id, install_date, "
        "service_interval_hours, esg_kpi_code, org_id, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (asset_ref, name, asset_type, site_id, install_date or None,
         service_interval_hours, esg_kpi_code or None, org_id, created_by))
    db.commit()
    return dict(db.execute("SELECT * FROM assets WHERE asset_ref = %s", (asset_ref,)).fetchone())


def list_assets(db, org_id: int | None) -> list[dict]:
    """Fails closed: no org, no rows."""
    if not org_id:
        return []
    rows = db.execute("SELECT * FROM assets WHERE org_id = %s ORDER BY name", (org_id,)).fetchall()
    return [dict(r) for r in rows]


def get_asset(db, asset_id: int) -> dict | None:
    row = db.execute("SELECT * FROM assets WHERE id = %s", (asset_id,)).fetchone()
    return dict(row) if row else None


def get_asset_dashboard_summary(db, org_id: int | None) -> dict:
    """Main dashboard tile summary. Fails closed: no org, all zeroes."""
    if not org_id:
        return {"assets_tracked": 0, "open_maintenance_tasks": 0}
    assets_tracked = db.execute(
        "SELECT COUNT(*) FROM assets WHERE org_id = %s AND status = 'active'", (org_id,)).fetchone()[0]
    open_maintenance_tasks = db.execute(
        "SELECT COUNT(*) FROM asset_maintenance_tasks WHERE org_id = %s AND status = 'open'", (org_id,)).fetchone()[0]
    return {"assets_tracked": assets_tracked, "open_maintenance_tasks": open_maintenance_tasks}


def list_readings(db, asset_id: int, limit: int = 50) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM asset_readings WHERE asset_id = %s ORDER BY recorded_at DESC LIMIT %s",
        (asset_id, limit)).fetchall()
    return [dict(r) for r in rows]


def list_maintenance_tasks(db, org_id: int | None, status: str | None = None) -> list[dict]:
    """Fails closed: no org, no rows."""
    if not org_id:
        return []
    sql = ("SELECT t.*, a.name AS asset_name, a.asset_ref FROM asset_maintenance_tasks t "
           "JOIN assets a ON a.id = t.asset_id WHERE t.org_id = %s")
    params = [org_id]
    if status:
        sql += " AND t.status = %s"
        params.append(status)
    sql += " ORDER BY t.created_at DESC"
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def complete_maintenance(db, task_id: int, org_id: int | None, user_id: int) -> dict:
    """Marks the task done AND resets the asset's service baseline, so the
    next interval is measured from now, not from the original install."""
    task = db.execute(
        "SELECT * FROM asset_maintenance_tasks WHERE id = %s AND org_id = %s AND status = 'open'",
        (task_id, org_id)).fetchone()
    if task is None:
        return {"ok": False, "message": "no open maintenance task found"}
    task = dict(task)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE asset_maintenance_tasks SET status = 'completed', completed_at = %s, completed_by = %s "
        "WHERE id = %s", (now, user_id, task_id))
    asset = get_asset(db, task["asset_id"])
    db.execute(
        "UPDATE assets SET hours_at_last_service = %s, last_serviced_at = %s WHERE id = %s",
        (asset["total_run_hours"], now, task["asset_id"]))
    db.commit()
    return {"ok": True}


# ---- API keys (mirrors modules/esg_kpi/csv_service.py's pattern exactly) ----

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(db, *, name: str, org_id: int, created_by: int | None = None) -> dict:
    raw = "ask_" + secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO asset_api_keys (name, key_hash, org_id, created_by) VALUES (%s,%s,%s,%s)",
        (name, _hash_key(raw), org_id, created_by))
    db.commit()
    row = db.execute("SELECT * FROM asset_api_keys WHERE key_hash = %s", (_hash_key(raw),)).fetchone()
    return {"ok": True, "api_key": raw, "record": dict(row)}


def verify_api_key(db, key: str) -> dict | None:
    row = db.execute(
        "SELECT * FROM asset_api_keys WHERE key_hash = %s AND is_active = TRUE", (_hash_key(key),)).fetchone()
    return dict(row) if row else None


# ---- Telemetry ingest ----

def _feed_esg_kpi(db, asset: dict, run_hours: float, org_id: int) -> None:
    """Best-effort: an asset optionally names the ESG KPI its readings feed
    (guide C4 step 2). No code, or no matching KPI row in this org, and
    this step does nothing - a reading is never blocked on it.
    """
    if not asset.get("esg_kpi_code"):
        return
    kpi = db.execute(
        "SELECT id FROM esg_kpis WHERE kpi_code = %s AND org_id = %s",
        (asset["esg_kpi_code"], org_id)).fetchone()
    if kpi is None:
        return
    from sheplatform.modules.esg_kpi.data_service import record_kpi_entry
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    record_kpi_entry(db, kpi_id=kpi["id"], period=period, actual_value=run_hours, org_id=org_id)


def record_telemetry(db, *, asset_ref: str, run_hours: float | None = None,
                     fuel_level_pct: float | None = None, recorded_at: str | None = None,
                     org_id: int | None = None) -> dict:
    """Guide C4 step 2. Range-validates, flags fuel-theft-shaped anomalies
    (a fuel drop with no corresponding run-time), rate-limits sub-minute
    readings, updates the asset's cumulative run-hours, raises a
    maintenance task when the service interval is exceeded, and
    best-effort feeds an ESG KPI. Fails closed: an asset_ref that doesn't
    resolve inside the caller's own org is rejected outright.
    """
    if not org_id:
        return {"ok": False, "message": "no organisation"}
    asset = db.execute(
        "SELECT * FROM assets WHERE asset_ref = %s AND org_id = %s", (asset_ref, org_id)).fetchone()
    if asset is None:
        return {"ok": False, "message": "unknown asset for this organisation"}
    asset = dict(asset)

    if run_hours is not None and run_hours < 0:
        return {"ok": False, "message": "run_hours cannot be negative"}
    if fuel_level_pct is not None and not (0 <= fuel_level_pct <= 100):
        return {"ok": False, "message": "fuel_level_pct must be between 0 and 100"}

    if recorded_at:
        try:
            datetime.fromisoformat(recorded_at)
        except ValueError:
            return {"ok": False, "message": "recorded_at must be a valid ISO 8601 timestamp"}
    else:
        recorded_at = datetime.now(timezone.utc).isoformat()

    last = db.execute(
        "SELECT * FROM asset_readings WHERE asset_id = %s ORDER BY recorded_at DESC LIMIT 1",
        (asset["id"],)).fetchone()
    last = dict(last) if last else None
    if last is not None:
        gap = (datetime.fromisoformat(recorded_at) - datetime.fromisoformat(last["recorded_at"])).total_seconds()
        if gap < MIN_READING_INTERVAL_SECONDS:
            return {"ok": False, "message": "reading rejected: too soon after the last one for this asset"}

    is_anomaly, anomaly_reason = False, None
    if last is not None and fuel_level_pct is not None and last.get("fuel_level_pct") is not None:
        fuel_drop = last["fuel_level_pct"] - fuel_level_pct
        hours_moved = None
        if run_hours is not None and last.get("run_hours") is not None:
            hours_moved = run_hours - last["run_hours"]
        if fuel_drop >= FUEL_DROP_ANOMALY_THRESHOLD_PCT and (hours_moved is None or hours_moved <= FUEL_DROP_ANOMALY_MAX_RUN_HOURS):
            is_anomaly = True
            anomaly_reason = f"fuel dropped {fuel_drop:.1f}% with no corresponding run-time - possible fuel theft"

    db.execute(
        "INSERT INTO asset_readings (asset_id, run_hours, fuel_level_pct, recorded_at, "
        "is_anomaly, anomaly_reason, org_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (asset["id"], run_hours, fuel_level_pct, recorded_at, is_anomaly, anomaly_reason, org_id))
    db.commit()
    reading = dict(db.execute(
        "SELECT * FROM asset_readings WHERE asset_id = %s ORDER BY id DESC LIMIT 1",
        (asset["id"],)).fetchone())

    maintenance_task = None
    if run_hours is not None:
        db.execute("UPDATE assets SET total_run_hours = %s WHERE id = %s", (run_hours, asset["id"]))
        db.commit()
        interval = asset.get("service_interval_hours")
        if interval:
            hours_since_service = run_hours - (asset.get("hours_at_last_service") or 0)
            if hours_since_service >= interval:
                existing_open = db.execute(
                    "SELECT id FROM asset_maintenance_tasks WHERE asset_id = %s AND status = 'open'",
                    (asset["id"],)).fetchone()
                if existing_open is None:
                    db.execute(
                        "INSERT INTO asset_maintenance_tasks (asset_id, title, reason, org_id) "
                        "VALUES (%s,%s,%s,%s)",
                        (asset["id"], f"Service due: {asset['name']}",
                         f"{hours_since_service:.1f} hours since last service (interval: {interval})", org_id))
                    db.commit()
                    maintenance_task = dict(db.execute(
                        "SELECT * FROM asset_maintenance_tasks WHERE asset_id = %s ORDER BY id DESC LIMIT 1",
                        (asset["id"],)).fetchone())
                    notify_roles(db, ["she_manager"], f"Maintenance due: {asset['name']}",
                                maintenance_task["reason"], link=f"/assets/api/{asset['id']}")

        _feed_esg_kpi(db, asset, run_hours, org_id)

    return {"ok": True, "reading": reading, "maintenance_task": maintenance_task}
