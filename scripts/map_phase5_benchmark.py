"""Isolated Phase 5 geospatial query and provider-admission benchmark.

The script creates a new disposable SQLite database unless an unused explicit
path is supplied. It never opens, migrates, or deletes an existing database.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gc
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter
import tracemalloc


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(
        len(ordered) - 1,
        max(0, math.ceil(len(ordered) * percentile) - 1),
    )
    return ordered[index]


def _git_commit() -> str:
    configured_revision = os.getenv("ECOAEGIS_VCS_REF", "").strip()
    if configured_revision:
        return configured_revision
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=C:/Projects/ecoaegis", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unavailable"
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _git_worktree_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "-c", "safe.directory=C:/Projects/ecoaegis", "status", "--porcelain"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _insert_fixture(db, *, table_rows: int, located_records: int) -> dict:
    from sheplatform.core.auth import hash_password

    db.execute(
        "INSERT INTO organisations (name, slug) VALUES (%s,%s)",
        ("Phase 5 Benchmark", "phase-5-benchmark"),
    )
    org_id = db.execute(
        "SELECT id FROM organisations WHERE slug = %s", ("phase-5-benchmark",)
    ).fetchone()["id"]
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s,%s,'Phase','Benchmark','she_manager',%s)",
        ("phase5-benchmark@invalid.local", hash_password("BenchmarkOnly123!"), org_id),
    )
    user_id = db.execute(
        "SELECT id FROM users WHERE email = %s",
        ("phase5-benchmark@invalid.local",),
    ).fetchone()["id"]
    db.commit()

    insert_sql = (
        "INSERT INTO incidents "
        "(incident_ref, title, severity, status, incident_type, latitude, longitude, "
        "occurred_at, org_id) VALUES (?,?,?,?,?,?,?,?,?)"
    )
    now = datetime.now(timezone.utc).isoformat()
    raw = getattr(db, "_conn", None)
    if raw is None:
        raise RuntimeError("The isolated benchmark currently requires the SQLite adapter")
    for start in range(0, table_rows, 5000):
        rows = []
        for index in range(start, min(start + 5000, table_rows)):
            if index < located_records:
                latitude = -17.90 + ((index % 100) * 0.001)
                longitude = 30.95 + (((index // 100) % 100) * 0.001)
            else:
                latitude = -22.0 - ((index % 100) * 0.001)
                longitude = 27.0 - (((index // 100) % 100) * 0.001)
            rows.append((
                f"P5-{index:07d}",
                f"Benchmark incident {index}",
                "medium",
                "open",
                "near_miss",
                latitude,
                longitude,
                now,
                org_id,
            ))
        raw.executemany(insert_sql, rows)
        db.commit()
    return {"org_id": int(org_id), "user_id": int(user_id)}


def _query_benchmark(db, *, org_id: int, moves: int) -> dict:
    from sheplatform.modules.map import layer_service

    timings = []
    payload_bytes = []
    returned = []
    truncated = []
    tracemalloc.start()
    gc.collect()
    baseline_bytes = tracemalloc.get_traced_memory()[0]
    for index in range(moves):
        shift = (index % 5) * 0.0005
        bbox = layer_service.BBox(
            west=30.0 + shift,
            south=-19.0 + shift,
            east=32.0 + shift,
            north=-16.0 + shift,
        )
        started = perf_counter()
        collection = layer_service.get_layer_collection(
            db,
            layer_key="incidents",
            org_id=org_id,
            bbox=bbox,
            limit=2000,
        )
        timings.append((perf_counter() - started) * 1000)
        payload_bytes.append(len(json.dumps(collection, separators=(",", ":")).encode()))
        returned.append(int(collection["meta"]["returned"]))
        truncated.append(bool(collection["meta"]["truncated"]))
    gc.collect()
    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "moves": moves,
        "p50_ms": round(statistics.median(timings), 3),
        "p95_ms": round(_percentile(timings, 0.95), 3),
        "max_ms": round(max(timings), 3),
        "max_payload_bytes": max(payload_bytes),
        "max_returned": max(returned),
        "all_truncated": all(truncated),
        "memory_current_delta_bytes": max(0, current_bytes - baseline_bytes),
        "memory_peak_bytes": peak_bytes,
    }


def _admission_benchmark(*, user_id: int, org_id: int, attempts: int,
                         limit: int) -> dict:
    from sheplatform.config import settings
    from sheplatform.database import get_db
    from sheplatform.modules.map import provider_admission_service

    settings.MAP_PROVIDER_WARNING_LOADS = max(0, limit // 2)
    settings.MAP_PROVIDER_CRITICAL_LOADS = max(
        settings.MAP_PROVIDER_WARNING_LOADS + 1, limit - 1)
    settings.MAP_PROVIDER_MONTHLY_LIMIT = limit
    requests = []
    for index in range(attempts):
        session = f"phase5-concurrency-{index}"
        nonce = provider_admission_service.issue_page_nonce(
            user_id=user_id, org_id=org_id, session_token=session)
        requests.append((session, nonce))

    def admit(item):
        session, nonce = item
        connection = get_db()
        try:
            decision = provider_admission_service.admit_provider_session(
                connection,
                nonce=nonce,
                user_id=user_id,
                org_id=org_id,
                session_token=session,
            )
            return {"admitted": decision.admitted, "error": None}
        except Exception as exc:  # captured in report and fails the release gate
            return {"admitted": False, "error": type(exc).__name__}
        finally:
            connection.close()

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=min(25, attempts)) as pool:
        decisions = list(pool.map(admit, requests))
    elapsed_ms = (perf_counter() - started) * 1000
    db = get_db()
    try:
        usage = db.execute(
            "SELECT admitted_loads FROM map_provider_monthly_usage "
            "WHERE provider = 'mapbox'"
        ).fetchone()
    finally:
        db.close()
    errors = [item["error"] for item in decisions if item["error"]]
    admitted = sum(1 for item in decisions if item["admitted"])
    recorded = int(usage["admitted_loads"]) if usage else 0
    return {
        "attempts": attempts,
        "limit": limit,
        "admitted": admitted,
        "denied": attempts - admitted - len(errors),
        "recorded_loads": recorded,
        "errors": errors,
        "elapsed_ms": round(elapsed_ms, 3),
        "oversubscribed": admitted > limit or recorded > limit,
    }


def _profile_defaults(name: str) -> dict:
    if name == "release":
        return {
            "table_rows": 100_000,
            "located_records": 10_000,
            "moves": 50,
            "concurrent_admissions": 50,
            "admission_limit": 25,
        }
    return {
        "table_rows": 5_000,
        "located_records": 1_000,
        "moves": 10,
        "concurrent_admissions": 10,
        "admission_limit": 5,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated EcoAegis Phase 5 map benchmark")
    parser.add_argument("--profile", choices=("smoke", "release"), default="smoke")
    parser.add_argument("--table-rows", type=int)
    parser.add_argument("--located-records", type=int)
    parser.add_argument("--moves", type=int)
    parser.add_argument("--concurrent-admissions", type=int)
    parser.add_argument("--admission-limit", type=int)
    parser.add_argument(
        "--database-path",
        help="Unused SQLite path to create. Existing paths are always rejected.",
    )
    parser.add_argument("--output", help="Optional JSON report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    values = _profile_defaults(args.profile)
    for key in values:
        override = getattr(args, key)
        if override is not None:
            values[key] = override
    if not (1 <= values["located_records"] <= values["table_rows"]):
        raise SystemExit("located records must be between 1 and table rows")
    if not (1 <= values["admission_limit"] <= values["concurrent_admissions"]):
        raise SystemExit("admission limit must be between 1 and concurrent admissions")
    if values["moves"] < 1:
        raise SystemExit("moves must be positive")

    temp_dir = None
    if args.database_path:
        db_path = Path(args.database_path).resolve()
        if db_path.exists():
            raise SystemExit(f"refusing to use existing database path: {db_path}")
        db_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = tempfile.TemporaryDirectory(prefix="ecoaegis-phase5-")
        db_path = Path(temp_dir.name) / "benchmark.db"

    from sheplatform.config import settings
    from sheplatform.database import get_db, init_db

    settings.DATABASE_URL = ""
    settings.DB_PATH = str(db_path)
    settings.DEBUG = True
    settings.SECRET_KEY = "phase5-isolated-benchmark-secret"
    started = perf_counter()
    init_db()
    db = get_db()
    try:
        fixture = _insert_fixture(
            db,
            table_rows=values["table_rows"],
            located_records=values["located_records"],
        )
        fixture_seconds = perf_counter() - started
        query = _query_benchmark(
            db, org_id=fixture["org_id"], moves=values["moves"])
    finally:
        db.close()
    concurrency = _admission_benchmark(
        user_id=fixture["user_id"],
        org_id=fixture["org_id"],
        attempts=values["concurrent_admissions"],
        limit=values["admission_limit"],
    )

    checks = {
        "layer_p95_below_750_ms": query["p95_ms"] < 750,
        "payload_below_2_mb": query["max_payload_bytes"] < 2_000_000,
        "feature_limit_enforced": query["max_returned"] <= 2000,
        "truncation_state_correct": (
            query["all_truncated"] == (values["located_records"] > 2000)),
        "admission_has_no_errors": concurrency["errors"] == [],
        "admission_not_oversubscribed": not concurrency["oversubscribed"],
        "admission_matches_limit": (
            concurrency["admitted"] == concurrency["limit"] ==
            concurrency["recorded_loads"]),
    }
    report = {
        "profile": args.profile,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "git_worktree_dirty": _git_worktree_dirty(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "database": "isolated SQLite",
        "fixture": {**values, "build_seconds": round(fixture_seconds, 3)},
        "query": query,
        "admission_concurrency": concurrency,
        "checks": checks,
        "passed": all(checks.values()),
    }
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if temp_dir is not None:
        temp_dir.cleanup()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
