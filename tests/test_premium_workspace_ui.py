"""Regression contracts for the premium command workspace."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_theme_is_applied_before_css_and_login_is_dark_only():
    shell = _read("sheplatform/templates/base_shell.html")
    boot = _read("sheplatform/static/js/theme-boot.js")
    foundation = _read("sheplatform/static/ui_foundation/foundation.css")

    assert shell.index("/static/js/theme-boot.js") < shell.index("/static/css/app.css")
    assert '<html lang="en" data-theme="dark">' in shell
    assert "{{ theme or 'dark' }}" in shell
    assert 'window.location.pathname === "/login"' in boot
    assert 'let theme = "dark"' in boot
    assert ".login-page .theme-toggle" in foundation


def test_sidebar_groups_persist_state_and_keep_users_in_settings():
    shell = _read("sheplatform/templates/base_shell.html")
    foundation = _read("sheplatform/static/ui_foundation/foundation.js")

    for group in ("command", "safety", "assurance", "intelligence", "settings"):
        assert f'data-sidebar-group="{group}"' in shell
    settings_at = shell.index('data-sidebar-group="settings"')
    users_at = shell.index("'User management'")
    assert users_at > settings_at
    assert "ecoaegis.sidebar.groups.v1" in foundation
    assert "ecoaegis.sidebar.scroll.v1" in foundation
    assert "sessionStorage.setItem(scrollKey" in foundation
    assert "sidebarScroll.scrollTop" in foundation


def test_dashboard_uses_live_stats_in_concept_structure():
    dashboard = _read("sheplatform/modules/launcher/templates/dashboard.html")
    source = _read("sheplatform/static/js/dashboard.js")

    for component in (
        "command-hero",
        "command-metrics",
        "command-grid",
        "command-queues",
        "command-shortcuts",
    ):
        assert component in dashboard
    assert "{{ stats.active_incidents }}" in dashboard
    assert "{{ stats.high_risks }}" in dashboard
    assert "{{ stats.pending_approvals }}" in dashboard
    assert "{{ stats | tojson }}" in dashboard
    assert 'document.getElementById("dashboard-data")' in source
    assert "Math.random" not in source


def test_permit_workspace_has_tabs_real_summary_and_input_tray():
    template = _read("sheplatform/modules/permit_to_work/templates/index.html")
    source = _read("sheplatform/static/js/permits.js")
    routes = _read("sheplatform/modules/permit_to_work/routes.py")

    assert 'role="tablist"' in template
    assert 'id="permit-overview-panel"' in template
    assert 'id="permit-register-panel"' in template
    assert 'data-input-tray' in template
    assert '{% if can_create %}' in template
    assert 'has_capability(request.state.user, "ptw.create")' in routes
    assert 'fetch(API + "/list")' in source
    assert "countsFor(permits)" in source
    assert ".innerHTML" not in source
    assert "Math.random" not in source


def test_form_first_pages_receive_progressive_input_trays():
    source = _read("sheplatform/static/ui_foundation/foundation.js")
    css = _read("sheplatform/static/ui_foundation/foundation.css")

    assert 'document.querySelectorAll(".content > .card")' in source
    assert 'card.querySelector("form")' in source
    assert "data-no-input-tray" in source
    assert ".input-tray.is-open" in css
    assert ".input-tray-overlay" in css
    assert "prefers-reduced-motion" in css
