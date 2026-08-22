"""Static supply-chain and cost-safety contracts for the Phase 4 browser map."""
from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "sheplatform" / "static" / "vendor" / "mapbox-gl" / "3.28.1"


def test_vendored_mapbox_assets_match_reviewed_hashes():
    expected = {
        "mapbox-gl-csp.js": "a6ac10117c10915fb83d2c87ec9534c1f0f47f3e268ff346893c2df925de4c1a",
        "mapbox-gl-csp-worker.js": "a9a7602a54fd02c4a9b8e14e89d3873ce088e790f7f509d7ef2587915ed5d9c0",
        "mapbox-gl.css": "6689604c602eb06c13f0d6ed1e4972c7bf87074169252ee1d6262a93d419da2e",
        "LICENSE.txt": "c24eff481bf098c82fda9949b2d982589df8b36db11fffa49653d4afe1903998",
    }
    for name, digest in expected.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest
    sums = (VENDOR / "SHA256SUMS.txt").read_text(encoding="utf-8")
    for name, digest in expected.items():
        assert f"{digest}  {name}" in sums


def test_command_map_preserves_cost_and_privacy_invariants():
    source = (ROOT / "sheplatform" / "static" / "js" / "mapbox-command-map.js").read_text(
        encoding="utf-8")
    admin = (ROOT / "sheplatform" / "static" / "js" / "mapbox-map-admin.js").read_text(
        encoding="utf-8")
    assert source.count("new mapboxgl.Map(") == 1
    assert "mapboxgl.supported({ failIfMajorPerformanceCaveat: true })" in source
    assert 'fetch("/map/api/provider-session"' in source
    assert 'fetch("/map/api/metrics/provider-failure"' in source
    assert source.count('"X-CSRF-Token": csrfToken()') >= 2
    assert "AbortController" in source and "loadGeneration" in source
    assert "map.setStyle(" in source and 'map.on("style.load", restoreCachedSources)' in source
    assert "/map/api/manifest" in source and "/map/api/layer/" not in source
    assert "innerHTML" not in source
    assert "innerHTML" not in admin


def test_map_template_uses_only_local_versioned_mapbox_assets():
    template = (ROOT / "sheplatform" / "modules" / "map" / "templates" / "index.html").read_text(
        encoding="utf-8")
    assert "/static/vendor/mapbox-gl/3.28.1/mapbox-gl-csp.js" in template
    assert "/static/vendor/mapbox-gl/3.28.1/mapbox-gl.css" in template
    assert "api.mapbox.com/mapbox-gl-js" not in template
    assert 'data-provider-page-nonce="{{ map_provider_page_nonce }}"' in template


def test_service_worker_never_caches_authenticated_pages():
    source = (ROOT / "sheplatform" / "static" / "js" / "sw.js").read_text(
        encoding="utf-8")
    precache = source.split("const SHELL_ASSETS = [", 1)[1].split("];", 1)[0]
    assert "'/dashboard'" not in precache
    assert "'/incidents'" not in precache
    assert "'/observations'" not in precache
    assert "'/static/css/app.css'" in precache
    assert "request.mode === 'navigate'" in source
    assert "caches.match('/dashboard')" not in source
