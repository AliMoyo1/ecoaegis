"""SEC-SHE-006: IP allowlist for machine integration endpoints."""
from __future__ import annotations

IP_BLOCK = "source IP not allowed"


def _set(monkeypatch, allowlist="", trust_xff=False):
    monkeypatch.setattr("sheplatform.config.settings.INTEGRATION_IP_ALLOWLIST", allowlist)
    monkeypatch.setattr("sheplatform.config.settings.TRUST_FORWARDED_FOR", trust_xff)


class TestIntegrationIpAllowlist:
    def test_blocked_when_source_ip_not_in_allowlist(self, client, monkeypatch):
        _set(monkeypatch, allowlist="203.0.113.0/24")
        # TestClient's client host is "testclient" (not in the range) -> blocked
        resp = client.post("/esg/api/ingest", json={"entries": []})
        assert resp.status_code == 403
        assert resp.json()["detail"] == IP_BLOCK

    def test_allowed_ip_passes_the_gate(self, client, monkeypatch):
        _set(monkeypatch, allowlist="203.0.113.5", trust_xff=True)
        resp = client.post("/esg/api/ingest", json={"entries": []},
                           headers={"X-Forwarded-For": "203.0.113.5"})
        # the IP gate lets it through; whatever the endpoint then returns for a
        # missing API key, it is NOT the IP block.
        assert resp.json().get("detail") != IP_BLOCK

    def test_wrong_forwarded_ip_is_blocked(self, client, monkeypatch):
        _set(monkeypatch, allowlist="203.0.113.5", trust_xff=True)
        resp = client.post("/esg/api/ingest", json={"entries": []},
                           headers={"X-Forwarded-For": "198.51.100.9"})
        assert resp.status_code == 403 and resp.json()["detail"] == IP_BLOCK

    def test_empty_allowlist_allows_all(self, client, monkeypatch):
        _set(monkeypatch, allowlist="")  # default: off
        resp = client.post("/esg/api/ingest", json={"entries": []})
        assert resp.json().get("detail") != IP_BLOCK

    def test_non_integration_path_unaffected(self, client, monkeypatch):
        _set(monkeypatch, allowlist="203.0.113.0/24")
        # a normal (non-integration) path is never IP-gated
        resp = client.get("/login")
        assert resp.status_code == 200
