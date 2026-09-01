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


def test_navigation_hides_unenhanced_workspace_until_foundation_is_ready():
    shell = _read("sheplatform/templates/base_shell.html")
    boot = _read("sheplatform/static/js/theme-boot.js")
    source = _read("sheplatform/static/ui_foundation/foundation.js")
    css = _read("sheplatform/static/ui_foundation/foundation.css")

    assert 'class="workspace-boot"' in shell
    assert 'root.classList.add("ui-booting")' in boot
    assert "__ecoaegisRevealUI" in boot
    assert "2500" in boot
    assert "html.ui-booting #main-content" in css
    assert "html.ui-booting .sidebar-groups" in css
    assert "restoreSidebarState(revealUI)" in source
    assert source.index("enhanceInputTrays();") < source.index("restoreSidebarState(revealUI)")


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


def test_asset_assurance_starts_the_analytics_first_register_pattern():
    template = _read("sheplatform/modules/assets/templates/index.html")
    source = _read("sheplatform/static/js/assets.js")

    assert "{% block topbar_title %}Asset assurance{% endblock %}" in template
    assert 'class="command-metrics asset-metrics"' in template
    assert 'role="tablist"' in template
    for panel in ("asset-overview-panel", "asset-register-panel", "asset-maintenance-panel"):
        assert f'id="{panel}"' in template
    assert 'id="asset-health-donut"' in template
    assert 'data-input-tray-open="asset-register-tray"' in template
    assert 'id="asset-register-tray" data-input-tray' in template
    assert 'id="asset-key-tray" data-input-tray' in template
    assert "Promise.all" in source
    assert "renderAssetMix" in source
    assert "renderMaintenancePreview" in source
    assert ".innerHTML" not in source
    assert "Math.random" not in source


def test_user_management_is_an_authenticated_settings_workspace():
    routes = _read("sheplatform/modules/launcher/routes_admin.py")
    template = _read("sheplatform/modules/launcher/templates/users.html")
    source = _read("sheplatform/static/js/users.js")

    assert '"user": request.state.user' in routes
    assert "RedirectResponse" in routes
    assert "user_summary" in routes
    assert "{% block topbar_title %}Identity and access{% endblock %}" in template
    assert 'data-input-tray-open="create-user-tray"' in template
    assert 'data-input-tray-label="Add user"' in template
    assert 'action="/admin/users/create"' in template
    assert 'name="csrf_token"' in template
    assert 'id="user-search"' in template
    assert "Math.random" not in source
    assert "innerHTML" not in source


def test_first_party_typography_uses_premium_sans_fonts_only():
    fonts = _read("sheplatform/static/fonts/fonts.css")
    app = _read("sheplatform/static/css/app.css")
    foundation = _read("sheplatform/static/ui_foundation/foundation.css")
    service_worker = _read("sheplatform/static/js/sw.js")

    assert "font-family: 'Plus Jakarta Sans'" in fonts
    assert "font-family: 'DM Sans'" in fonts
    assert "plus-jakarta-sans-400.woff2" in fonts
    assert "jetbrains-mono" not in fonts.casefold()
    assert not (ROOT / "sheplatform/static/fonts/jetbrains-mono-400.woff2").exists()
    assert "--display: 'Plus Jakarta Sans'" in app
    assert "font-family: var(--font)" in app
    assert "text-transform: none" in app
    assert "--display: \"Plus Jakarta Sans\"" in foundation
    assert "ecoAegis-shell-v6" in service_worker
    assert "/static/fonts/fonts.css?v=20260901-ui3" in _read(
        "sheplatform/templates/base_shell.html"
    )
    assert "/static/ui_foundation/foundation.css?v=20260901-ui3" in _read(
        "sheplatform/templates/base_shell.html"
    )

    forbidden = (
        "JetBrains Mono",
        "jetbrains-mono",
        "var(--mono)",
        "Bahnschrift",
        "Arial Narrow",
        "font-stretch: semi-condensed",
    )
    for path in (ROOT / "sheplatform").rglob("*"):
        if path.suffix not in {".css", ".html", ".js", ".py"}:
            continue
        if "vendor" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert not any(token in source for token in forbidden), path
