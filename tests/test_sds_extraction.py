"""SDS extraction tests (guide B2).

Tests the extraction service with a stubbed AI client so no network calls are
made, and the HTTP upload/apply endpoints through TestClient.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

import asyncio
import pytest
from fastapi.testclient import TestClient


def _mk_user(db, role, email):
    from sheplatform.core.auth import hash_password
    db.execute(
        "INSERT INTO users (email, password_hash, first_name, last_name, role_key, org_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (email, hash_password("Test1234!"), "F", "L", role, 1),
    )
    db.commit()
    return dict(db.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone())


def _make_pdf_bytes(text: str) -> bytes:
    """Build a minimal valid PDF containing the given text using reportlab."""
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 700
    for i, line in enumerate(text.split("\n")):
        c.drawString(72, y - i * 20, line)
    c.save()
    return buf.getvalue()


def test_extract_sds_from_pdf_returns_fields():
    from sheplatform.modules.chemicals.sds_extraction import extract_sds_from_pdf
    pdf = _make_pdf_bytes(
        "SECTION 1: Identification\nProduct name: TestSolv\nManufacturer: Acme Chemicals\n"
        "SECTION 2: Hazards\nH225: Highly flammable liquid and vapour\nSignal word: Danger\n"
        "SECTION 7: Handling and storage\nStore in a cool, dry place.\n"
        "SECTION 8: Exposure controls\nWear gloves and goggles."
    )
    stub_json = json.dumps({
        "product_name": "TestSolv",
        "manufacturer": "Acme Chemicals",
        "cas_number": "67-63-0",
        "ghs_signal_word": "Danger",
        "ghs_hazard_statements": ["H225: Highly flammable liquid and vapour"],
        "ghs_pictograms": ["flame"],
        "ppe_required": ["gloves", "goggles"],
        "storage_conditions": "Store in a cool, dry place.",
        "first_aid_measures": None,
        "review_period_years": 5,
    })
    with patch("sheplatform.modules.chemicals.sds_extraction.ask_ai", return_value=stub_json):
        result = asyncio.run(extract_sds_from_pdf(pdf))
    assert result["ok"] is True
    assert result["source"] == "pdfplumber"
    assert result["fields"]["product_name"] == "TestSolv"
    assert "flame" in result["fields"]["ghs_pictograms"]


def test_apply_sds_fields_maps_extracted_data():
    from sheplatform.modules.chemicals.sds_extraction import apply_sds_fields
    chemical = {"name": "", "cas_number": "", "supplier": "", "sds_extracted": "{}"}
    fields = {
        "product_name": "TestSolv",
        "cas_number": "67-63-0",
        "manufacturer": "Acme Chemicals",
        "ppe_required": ["gloves", "goggles"],
        "storage_conditions": "Cool, dry",
    }
    updates = apply_sds_fields(chemical, fields)
    assert updates["name"] == "TestSolv"
    assert updates["cas_number"] == "67-63-0"
    assert updates["supplier"] == "Acme Chemicals"
    assert updates["sds_extracted"]["ppe_required"] == ["gloves", "goggles"]


def test_sds_upload_endpoint(client, db):
    officer = _mk_user(db, "she_officer", "sds1@test.com")
    resp = client.post("/login", data={
        "email": officer["email"], "password": "Test1234!"})
    assert resp.status_code in (200, 302, 303)
    csrf = client.cookies.get("she_csrf")
    client.headers["X-CSRF-Token"] = csrf

    # Create a chemical through the API
    resp = client.post("/chemicals/api/create", data={"name": "TestSolv"})
    assert resp.status_code == 200
    chem = resp.json()["chemical"]

    pdf = _make_pdf_bytes("Product name: TestSolv\nH225: Highly flammable\nLine 2: product TestSolv hazard H225\nLine 3: product TestSolv hazard H225\nLine 4: product TestSolv hazard H225\nLine 5: product TestSolv hazard H225\nLine 6: product TestSolv hazard H225")
    stub_json = json.dumps({
        "product_name": "TestSolv",
        "cas_number": "67-63-0",
        "manufacturer": "Acme Chemicals",
        "ghs_hazard_statements": ["H225: Highly flammable"],
        "ppe_required": ["gloves"],
        "storage_conditions": "Cool",
    })
    with patch("sheplatform.modules.chemicals.sds_extraction.ask_ai", return_value=stub_json):
        resp = client.post(
            f"/chemicals/api/{chem['id']}/sds-upload",
            files={"file": ("sds.pdf", io.BytesIO(pdf), "application/pdf")},
            data={"sds_review_date": "2027-01-01"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["extraction"]["fields"]["product_name"] == "TestSolv"

    # Apply reviewed fields
    resp = client.post(
        f"/chemicals/api/{chem['id']}/sds-apply",
        data={
            "hazard_class": "flammable",
            "cas_number": "67-63-0",
            "supplier": "Acme Chemicals",
            "sds_status": "current",
            "extracted_json": json.dumps(data["extraction"]["fields"]),
        })
    assert resp.status_code == 200
    chem_out = resp.json()["chemical"]
    assert chem_out["hazard_class"] == "flammable"
    assert chem_out["cas_number"] == "67-63-0"
