"""ESG KPI ingestion tests (B3)."""
from __future__ import annotations

import io

from sheplatform.core.auth import hash_password
from sheplatform.modules.esg_kpi import csv_service, data_service


def _login(client, email, password="Test1234!"):
    return client.post("/login", data={"email": email, "password": password})


def _mk_user(db, role_key, email, org_id=1):
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "Test", "User", role_key, org_id))
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def test_csv_parsing_with_mapping(client, db):
    officer = _mk_user(db, "she_officer", "esg1@test.com")
    data_service.seed_kpis(db, org_id=1)
    co2 = db.execute("SELECT id FROM esg_kpis WHERE kpi_code = 'ESG-ENV-01'").fetchone()["id"]

    csv_service.create_mapping(
        db, mapping_name="env_monthly",
        mappings=[{"source_column": "CO2_tCO2e", "kpi_id": co2, "transform": "value"}],
        org_id=1, created_by=officer["id"])

    csv_text = "period,CO2_tCO2e\n2026-08,123.45\n2026-09,not_a_number\n"
    result = csv_service.parse_csv_upload(
        db, file_bytes=csv_text.encode(), file_name="env_2026-08.csv",
        mapping_name="env_monthly", org_id=1, created_by=officer["id"])

    assert result["ok"]
    assert result["rows_total"] == 2
    valid = [r for r in result["preview"] if r["status"] == "valid"]
    anomalous = [r for r in result["preview"] if r["status"] == "anomalous"]
    assert len(valid) == 1
    assert valid[0]["actual_value"] == 123.45
    assert len(anomalous) == 1
    assert "non-numeric" in anomalous[0]["anomaly_reason"]


def test_csv_detects_duplicates(client, db):
    officer = _mk_user(db, "she_officer", "esg2@test.com")
    data_service.seed_kpis(db, org_id=1)
    co2 = db.execute("SELECT id FROM esg_kpis WHERE kpi_code = 'ESG-ENV-01'").fetchone()["id"]

    csv_service.create_mapping(
        db, mapping_name="env_monthly",
        mappings=[{"source_column": "CO2_tCO2e", "kpi_id": co2, "transform": "value"}],
        org_id=1, created_by=officer["id"])

    # seed existing entry
    csv_service.record_api_kpi_payload(
        db, key_record={"id": 0, "name": "manual", "org_id": 1, "scopes": '["esg.ingest"]'},
        payload={"period": "2026-08", "entries": [{"kpi_code": "ESG-ENV-01", "actual_value": 100}]},
        created_by=officer["id"])

    csv_text = "period,CO2_tCO2e\n2026-08,123.45\n"
    result = csv_service.parse_csv_upload(
        db, file_bytes=csv_text.encode(), file_name="env_2026-08.csv",
        mapping_name="env_monthly", org_id=1, created_by=officer["id"])

    csv_service.detect_duplicates(db, result["upload_id"])
    rows = csv_service.get_upload_rows(db, result["upload_id"])
    assert any(r["status"] == "duplicate" for r in rows)


def test_csv_commit_creates_entries(client, db):
    officer = _mk_user(db, "she_officer", "esg3@test.com")
    data_service.seed_kpis(db, org_id=1)
    co2 = db.execute("SELECT id FROM esg_kpis WHERE kpi_code = 'ESG-ENV-01'").fetchone()["id"]

    csv_service.create_mapping(
        db, mapping_name="env_monthly",
        mappings=[{"source_column": "CO2_tCO2e", "kpi_id": co2, "transform": "value"}],
        org_id=1, created_by=officer["id"])

    csv_text = "period,CO2_tCO2e\n2026-08,123.45\n"
    result = csv_service.parse_csv_upload(
        db, file_bytes=csv_text.encode(), file_name="env_2026-08.csv",
        mapping_name="env_monthly", org_id=1, created_by=officer["id"])

    commit = csv_service.commit_upload(db, result["upload_id"], created_by=officer["id"])
    assert commit["ok"]
    assert commit["committed"] == 1

    entry = db.execute(
        "SELECT * FROM esg_kpi_entries WHERE kpi_id = %s AND period = %s",
        (co2, "2026-08")).fetchone()
    assert entry is not None
    assert entry["actual_value"] == 123.45
    assert entry["source_upload_id"] == result["upload_id"]


def test_api_key_ingest(client, db):
    officer = _mk_user(db, "she_officer", "esg4@test.com")
    data_service.seed_kpis(db, org_id=1)
    key_result = csv_service.create_api_key(
        db, name="ops-system", org_id=1, created_by=officer["id"])
    assert key_result["ok"]
    key = key_result["api_key"]

    resp = client.post("/esg/api/ingest", json={
        "period": "2026-08",
        "entries": [
            {"kpi_code": "ESG-ENV-01", "actual_value": 99.9},
            {"kpi_code": "ESG-ENV-01", "actual_value": "bad"},
        ]
    }, headers={"X-ESG-API-Key": key})

    assert resp.status_code == 201
    data = resp.json()
    assert data["ok"]
    assert data["committed"] == 1
    assert len(data["skipped"]) == 1


def test_api_key_ingest_requires_key(client):
    resp = client.post("/esg/api/ingest", json={"period": "2026-08", "entries": []})
    assert resp.status_code == 401


def test_csv_upload_endpoint(client, db):
    officer = _mk_user(db, "she_officer", "esg5@test.com")
    data_service.seed_kpis(db, org_id=1)
    co2 = db.execute("SELECT id FROM esg_kpis WHERE kpi_code = 'ESG-ENV-01'").fetchone()["id"]

    _login(client, officer["email"])
    csrf = client.cookies.get("she_csrf", "")

    mapping = [{"source_column": "CO2_tCO2e", "kpi_id": co2, "transform": "value"}]
    client.post("/esg/api/mappings", data={
        "mapping_name": "env_monthly",
        "mappings_json": str(mapping).replace("'", '"')
    }, headers={"X-CSRF-Token": csrf})

    csv_bytes = b"period,CO2_tCO2e\n2026-08,55.5\n"
    resp = client.post("/esg/api/csv/upload", data={
        "mapping_name": "env_monthly",
    }, files={"file": ("env.csv", io.BytesIO(csv_bytes), "text/csv")},
    headers={"X-CSRF-Token": csrf})

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["ok"]
    assert data["rows_total"] == 1
    assert any(r["status"] == "valid" for r in data["preview"])
