"""Contracts for the reusable EcoAegis application UI foundation."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "sheplatform" / "static" / "ui_foundation"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_foundation_assets_are_local_and_complete():
    required = [
        FOUNDATION / "foundation.css",
        FOUNDATION / "foundation.js",
        FOUNDATION / "assets" / "econet-wireless-logo.png",
        FOUNDATION / "assets" / "earth-horizon-clean.png",
        FOUNDATION / "assets" / "earth-horizon.mp4",
    ]
    assert all(path.is_file() and path.stat().st_size > 0 for path in required)
    for path in required[:2]:
        source = _read(path)
        assert "http://" not in source
        assert "https://" not in source
        assert "—" not in source


def test_shell_loads_foundation_after_existing_security_shell():
    shell = _read(ROOT / "sheplatform" / "templates" / "base_shell.html")
    assert shell.index("/static/css/app.css") < shell.index(
        "/static/ui_foundation/foundation.css")
    assert shell.index("/static/js/shell.js") < shell.index(
        "/static/ui_foundation/foundation.js")
    assert 'class="{% block body_class %}{% endblock %}"' in shell
    assert 'href="#main-content"' in shell
    assert 'id="main-content"' in shell
    assert 'data-profile-avatar' in shell
    assert "/static/ui_foundation/assets/econet-wireless-logo.png" in shell
    assert '<link rel="icon" type="image/png"' in shell
    assert 'action="/logout"' in shell
    assert 'name="csrf_token"' in shell


def test_login_keeps_real_auth_contract_and_local_media():
    login = _read(
        ROOT / "sheplatform" / "modules" / "launcher" / "templates" / "login.html")
    assert "{% block body_class %}login-page{% endblock %}" in login
    assert 'method="post" action="/login"' in login
    assert 'name="email"' in login
    assert 'name="password"' in login
    assert 'autocomplete="username"' in login
    assert 'autocomplete="current-password"' in login
    assert 'role="alert"' in login
    assert "/static/ui_foundation/assets/earth-horizon.mp4" in login
    assert "/static/ui_foundation/assets/earth-horizon-clean.png" in login
    assert "safety.operations@" not in login
    assert "value=" not in login
    assert "—" not in login


def test_command_map_uses_private_live_contracts_and_explicit_states():
    template = _read(
        ROOT / "sheplatform" / "modules" / "map" / "templates" / "index.html")
    source = _read(ROOT / "sheplatform" / "static" / "js" / "mapbox-command-map.js")
    assert "{% block body_class %}map-page{% endblock %}" in template
    for element_id in [
        "map-data-status",
        "map-data-retry",
        "map-summary-located",
        "map-summary-unlocated",
        "map-summary-layers",
        "map-layer-options",
    ]:
        assert f'id="{element_id}"' in template
    assert "/map/api/manifest" in source
    assert "spec.endpoint" in source
    assert "collection.meta?.returned" in source
    assert "collection.meta?.unlocated" in source
    assert 'error.dataState = [401, 403].includes(response.status) ? "denied" : "error"' in source
    for state in ["loading", "ready", "empty", "warning", "denied", "error"]:
        assert f'"{state}"' in source or f'data-state="{state}"' in template
    assert "Math.random" not in source
    assert "innerHTML" not in source


def test_service_worker_precaches_public_foundation_not_tenant_pages():
    source = _read(ROOT / "sheplatform" / "static" / "js" / "sw.js")
    precache = source.split("const SHELL_ASSETS = [", 1)[1].split("];", 1)[0]
    assert "'/static/ui_foundation/foundation.css'" in precache
    assert "'/static/ui_foundation/foundation.js'" in precache
    assert "'/static/ui_foundation/assets/econet-wireless-logo.png'" in precache
    assert "'/dashboard'" not in precache
    assert "'/map'" not in precache
    assert "request.mode === 'navigate'" in source
