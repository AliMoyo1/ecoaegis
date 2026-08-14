"""Jinja2 templating helper (guide 3: server-rendered HTML shells)."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def render(request, template_name: str, context: dict | None = None):
    """Render with default context: theme from cookie (server-side initial value)."""
    ctx = dict(context or {})
    theme = request.cookies.get("she_theme")
    if theme not in ("light", "dark"):
        theme = "light"
    ctx.setdefault("theme", theme)
    return templates.TemplateResponse(request, template_name, ctx)

# Make module templates resolvable
# Module templates resolve as <module>/<template>.html via the modules root
# (e.g. incidents/index.html -> modules/incidents/templates/index.html).
_modules_root = BASE_DIR / "modules"
if str(_modules_root) not in templates.env.loader.searchpath:
    templates.env.loader.searchpath.append(str(_modules_root))

# Keep per-module dirs too: launcher's bare names (login.html, dashboard.html)
# resolve against its own directory first.
_tpl_dirs = [
    BASE_DIR / "modules" / "launcher" / "templates",
    BASE_DIR / "modules" / "incidents" / "templates",
    BASE_DIR / "modules" / "risk_register" / "templates",
    BASE_DIR / "modules" / "vendor_compliance" / "templates",
    BASE_DIR / "modules" / "permit_to_work" / "templates",
    BASE_DIR / "modules" / "community_complaints" / "templates",
    BASE_DIR / "modules" / "eia" / "templates",
    BASE_DIR / "modules" / "emergency" / "templates",
    BASE_DIR / "modules" / "training" / "templates",
    BASE_DIR / "modules" / "reporting" / "templates",
    BASE_DIR / "modules" / "external_comms" / "templates",
    BASE_DIR / "modules" / "workplan" / "templates",
    BASE_DIR / "modules" / "esg_kpi" / "templates",
    BASE_DIR / "modules" / "stakeholder" / "templates",
    BASE_DIR / "modules" / "evidence" / "templates",
    BASE_DIR / "modules" / "ai" / "templates",
    BASE_DIR / "modules" / "capa" / "templates",
    BASE_DIR / "modules" / "inspections" / "templates",
    BASE_DIR / "modules" / "observations" / "templates",
    BASE_DIR / "modules" / "documents" / "templates",
    BASE_DIR / "modules" / "compliance" / "templates",
    BASE_DIR / "modules" / "contractors" / "templates",
    BASE_DIR / "modules" / "chemicals" / "templates",
    BASE_DIR / "modules" / "benchmark" / "templates",
    BASE_DIR / "modules" / "statutory_reporting" / "templates",
    BASE_DIR / "modules" / "external_integration" / "templates",
]
for d in _tpl_dirs:
    if str(d) not in templates.env.loader.searchpath:
        templates.env.loader.searchpath.append(str(d))
