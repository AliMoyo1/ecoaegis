"""Safety and smoke coverage for the isolated Phase 5 benchmark harness."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "map_phase5_benchmark.py"


def test_phase5_benchmark_smoke_profile_runs_in_isolation(tmp_path):
    report_path = tmp_path / "phase5-smoke.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--profile", "smoke",
            "--table-rows", "200",
            "--located-records", "50",
            "--moves", "3",
            "--concurrent-admissions", "4",
            "--admission-limit", "2",
            "--output", str(report_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["fixture"]["table_rows"] == 200
    assert report["query"]["max_returned"] == 50
    assert report["admission_concurrency"]["recorded_loads"] == 2


def test_phase5_benchmark_refuses_an_existing_database(tmp_path):
    protected = tmp_path / "must-not-touch.db"
    protected.write_bytes(b"existing-user-data")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--database-path", str(protected)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "refusing to use existing database path" in result.stderr
    assert protected.read_bytes() == b"existing-user-data"
