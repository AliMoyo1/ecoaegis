"""ThemisIQ integration unit tests (spec Section 13.1 - no network).

1. _map_she_to_themis produces a valid RiskCreate body for every category/status/treatment
2. hash_body: same input -> same hash; different when any mapped field changes
3. Signature verification: accepts correct, rejects tampered/wrong-secret/missing
4. Threshold: residual-11 no sync, residual-12 sync, regulatory any score, manual flag
"""
from __future__ import annotations

import hashlib
import hmac

from sheplatform.modules.integration import mapping
from sheplatform.modules.integration.routes import _verify_signature


def _risk(**overrides):
    base = {
        "id": 1,
        "hazard_description": "Chemical spill risk at warehouse",
        "risk_category": "operational",
        "likelihood": 3,
        "impact": 5,
        "managerial_response": "mitigate",
        "existing_controls": "spill kits, training",
        "status": "open",
        "review_date": None,
        "residual_score": 12.0,
        "inherent_score": 15.0,
        "control_effectiveness": 4,
        "origin_system": "she",
        "source_type": "incident",
    }
    base.update(overrides)
    return base


class TestMapping:
    def test_valid_payload_all_categories(self):
        for she_cat in ("operational", "financial", "regulatory", "strategic"):
            body = mapping.map_she_to_themis(_risk(risk_category=she_cat))
            assert body["title"] and len(body["title"]) <= 500
            assert body["source_module"] == "SHE"
            assert body["source_entity_type"] == "she_risk"
            assert body["source_entity_id"] == 1
            assert body["category"] == she_cat
            assert 1 <= body["likelihood"] <= 5
            assert 1 <= body["impact"] <= 5
            assert body["treatment"] in ("accept", "mitigate", "transfer", "avoid")

    def test_status_mapping(self):
        assert mapping.map_status("open") == "open"
        assert mapping.map_status("under_review") == "open"
        assert mapping.map_status("monitoring") == "mitigated"
        assert mapping.map_status("mitigated") == "mitigated"

    def test_treatment_mapping(self):
        assert mapping.map_treatment("accept") == "accept"
        assert mapping.map_treatment("mitigate") == "mitigate"
        assert mapping.map_treatment("transfer") == "transfer"
        assert mapping.map_treatment("avoid") == "avoid"
        assert mapping.map_treatment(None) == "mitigate"

    def test_residual_note_in_treatment_plan(self):
        # spec 6.4: residual score + control effectiveness travel as a note
        body = mapping.map_she_to_themis(_risk())
        assert "Residual score 12.0 (inherent 15.0 / CE 4)" in body["treatment_plan"]
        assert "Controls: spill kits, training" in body["treatment_plan"]

    def test_no_pii_fields_in_payload(self):
        # spec 5: never syncs emails, phone numbers, or ID numbers
        body = mapping.map_she_to_themis(_risk(
            hazard_description="Contact j.smith@econet.co.zw or 0771234567 (ID 29-1234567K50)"))
        assert "j.smith@econet.co.zw" not in body["description"]
        assert "0771234567" not in body["description"]
        assert "29-1234567K50" not in body["description"]
        assert "[email]" in body["description"]
        assert "[phone]" in body["description"]


class TestHashing:
    def test_deterministic(self):
        a = mapping.hash_body(mapping.map_she_to_themis(_risk()))
        b = mapping.hash_body(mapping.map_she_to_themis(_risk()))
        assert a == b

    def test_changes_when_mapped_field_changes(self):
        h1 = mapping.hash_body(mapping.map_she_to_themis(_risk(impact=3)))
        h2 = mapping.hash_body(mapping.map_she_to_themis(_risk(impact=5)))
        assert h1 != h2


class TestSignature:
    def test_accepts_correct_signature(self, monkeypatch):
        secret = "test-shared-secret"
        monkeypatch.setattr("sheplatform.config.settings.THEMIS_WEBHOOK_SECRET", secret)
        body = b'{"event_type": "erm.risk.escalated"}'
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        assert _verify_signature(body, sig) is True

    def test_rejects_tampered_body(self, monkeypatch):
        secret = "test-shared-secret"
        monkeypatch.setattr("sheplatform.config.settings.THEMIS_WEBHOOK_SECRET", secret)
        body = b'{"event_type": "erm.risk.escalated"}'
        sig = "sha256=" + hmac.new(secret.encode(), b'{"event_type": "erm.risk.closed"}',
                                   hashlib.sha256).hexdigest()
        assert _verify_signature(body, sig) is False

    def test_rejects_wrong_secret(self, monkeypatch):
        monkeypatch.setattr("sheplatform.config.settings.THEMIS_WEBHOOK_SECRET", "secret-a")
        body = b'{"event_type": "erm.risk.escalated"}'
        sig = "sha256=" + hmac.new(b"secret-b", body, hashlib.sha256).hexdigest()
        assert _verify_signature(body, sig) is False

    def test_rejects_missing_header(self, monkeypatch):
        monkeypatch.setattr("sheplatform.config.settings.THEMIS_WEBHOOK_SECRET", "secret")
        assert _verify_signature(b"{}", "") is False


class TestThreshold:
    def test_residual_11_does_not_sync(self):
        assert mapping.is_corporate(_risk(residual_score=11.0, risk_category="operational")) is False

    def test_residual_12_syncs(self):
        assert mapping.is_corporate(_risk(residual_score=12.0, risk_category="operational")) is True

    def test_regulatory_any_score_syncs(self):
        assert mapping.is_corporate(_risk(residual_score=1.0, risk_category="regulatory")) is True
        assert mapping.is_corporate(_risk(residual_score=0.0, risk_category="strategic")) is True

    def test_manual_flag_syncs(self):
        assert mapping.is_corporate(_risk(residual_score=3.0, risk_category="operational",
                                          corporate_flag=True)) is True

    def test_loop_guard(self):
        assert mapping.is_themis_origin(_risk(origin_system="themisiq")) is True
        assert mapping.is_themis_origin(_risk(origin_system="she")) is False
