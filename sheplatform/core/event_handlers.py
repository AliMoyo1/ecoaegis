"""Event handler registration (guide 21).

Importing this module registers every @on handler. Import it once at startup
(and in test conftest) so handlers are live before any event fires.
"""
from __future__ import annotations

# Importing these modules runs their @events.on(...) registrations.
import sheplatform.modules.risk_register.data_service  # noqa: F401  (incident.closed -> risk)
import sheplatform.modules.community_complaints.event_handlers  # noqa: F401  (grievance.closed -> risk + drill)
import sheplatform.modules.eia.event_handlers  # noqa: F401  (eia.rejected -> risk + alerts)
import sheplatform.modules.emergency.event_handlers  # noqa: F401  (post_crisis -> EPRP queue)
import sheplatform.modules.reporting.event_handlers  # noqa: F401  (report.approved -> training need)
import sheplatform.modules.training.event_handlers  # noqa: F401  (incident.closed -> training need + key issue, BRN-009)
import sheplatform.modules.integration.sync_handlers  # noqa: F401  (risk.created/updated -> ThemisIQ sync)
