# EcoAegis vs EHS Competitors - Feature Benchmark (live research)

**Date:** 2026-08-12
**Method:** Live browser research of competitor sites (Cority, Mitti by SafetyCulture, VelocityEHS) + comparison against the live EcoAegis build (19 modules, 103 tests passing).

---

## 1. Competitors researched (live)

### Cority (CorityOne) - enterprise EHS+ platform
- "One Trusted, Converged EHS+ Software Platform" - safety, health, environmental, quality, ESG
- **Cortex AI**: secure, explainable, human-in-the-loop AI across EHS workflows
- **Audits & Inspections**: standardize audits and inspections for compliance consistency
- **BI & Analytics**: monitor KPIs, analyze trends, predictive tools
- **Compliance Management**: centralize obligations, automate updates, align to ISO 45001/OSHA/EPA/RIDDOR
- **Document Control**: policies and SOPs, audit-ready documentation
- **Environmental modules**: Air Emissions, Waste Management, Water Management, Chemical Management
- **Workforce Health, ESG & Sustainability, Product Quality**
- Geographic map with pins (inspections, supplier locations), incident-by-year charts, emissions pie
- "Always Audit Ready" positioning

### Mitti by SafetyCulture - frontline operations (mobile-first)
- **AI Assistant**: create checklists/training, surface info in seconds
- **Inspections**: checklists into connected workflows (380K inspections completed daily)
- **AI Issue Capture**: photos or voice notes into actionable issues
- **Issue Reporting**: anyone flags what they see, reaches right person fast
- **Task Management**: "Don't just spot the problem, fix it and close it out" (CAPA)
- **Investigations**: root cause, "stop it happening twice"
- **Lone Worker**: know solo workers are safe
- **Training**: on-the-job, build in minutes
- **Contractor Management**: site-ready before they step on-site
- **Asset Maintenance**: track every asset, real time
- **5 Star Benchmarking**: benchmark performance across every site
- **Communications**: message whole team with read receipts
- **Document Management**: one place, on site or offline
- **Analytics, Sensors & IoT, Agents, Integrations**
- Mobile-first with photo capture (3.4M photos uploaded daily)

### VelocityEHS (Accelerate platform)
- **AI**: VelocityAI, human-centered, "See what matters before it matters most"
- **Safety Management, Industrial Ergonomics, Chemical Management (SDS), Operational Risk, Contractor Safety & Permit to Work, Environmental Compliance, Sustainability, Industrial Hygiene**
- Protects 10M+ workers; positioning: predictive, connected, intelligence everywhere

---

## 2. Capability matrix

| Capability | Cority | Mitti | VelocityEHS | EcoAegis |
|---|---|---|---|---|
| Incident management | Yes | Yes | Yes | Yes (SHEIMI + 48h deadlines) |
| Risk register | Yes | No | Yes | Yes (residual scoring, heat map) |
| Corrective actions (CAPA) | Yes | Yes (Task Mgmt) | Yes | **Data only, no module** |
| Inspections w/ checklists | Yes | Yes (core) | Yes | **Data only, no module** |
| Observations / issue reporting | Yes | Yes (core) | Yes | **No** |
| Near-miss capture | Yes | Yes | Yes | Yes (type) but no quick-capture |
| Permit to Work | Yes | No | Yes | Yes (4-step approval) |
| Contractor management | Yes | Yes | Yes | Partial (vendors) |
| Training & competency | Yes | Yes | Yes | Yes (+ matrix, refreshers) |
| Chemical / SDS register | Yes | No | Yes | **No** |
| Environmental monitoring (air/water/waste) | Yes | No | Yes | Partial (EIA + ESG) |
| Industrial hygiene / ergonomics | Yes | No | Yes | **No** |
| Document control (SOPs, policies) | Yes | Yes | Yes | **No** (evidence vault is files only) |
| Compliance obligations register | Yes | Partial | Yes | Partial (deadlines) |
| ESG KPI tracking | Yes | Partial | Yes | Yes (12 KPIs + RAG) |
| Audit-ready evidence | Yes | Yes | Yes | Partial (vault, hash-verified) |
| AI copilot | Cortex AI | AI Assistant | VelocityAI | Yes (6 features, grounded) |
| AI photo/voice capture | No | Yes | No | **No** |
| Mobile / offline | Partial | Yes (core) | Partial | Responsive web only |
| Geographic map view | Yes | No | No | **No** |
| Site benchmarking | Yes | Yes (5-star) | Yes | **No** |
| Lone worker | No | Yes | No | **No** |
| Communications w/ read receipts | No | Yes | No | In-app notifications only |
| Asset maintenance | No | Yes | No | **No** |
| Schedulers / alerts | Yes | Yes | Yes | Yes (4 schedulers) |
| Dashboard analytics | Yes | Yes | Yes | Yes (new: charts, heat map) |

---

## 3. Gap analysis - where EcoAegis is behind

### High priority (core EHS workflows missing)
1. **Corrective Actions (CAPA) module** - the table exists; every competitor has it; auditors demand closed-loop CA with verification. Biggest gap.
2. **Inspections module with checklists** - table exists; Mitti's entire product is built on it.
3. **Observations / quick issue capture** - the modern "see something, say something" differentiator; feeds near-miss ratio.
4. **Document control** - SOPs, policies, versions, acknowledgement tracking (evidence vault is a start, needs structure).

### Medium priority (competitive parity)
5. **Contractor management** - extend vendors: site-readiness, training records, inductions before site access.
6. **Compliance obligations register** - track all statutory obligations (NSSA, EMA, ZRP, labour) with owners and renewal dates; EcoAegis has deadlines only on incidents.
7. **Chemical / SDS register** - chemicals inventory with safety data sheets (Econet has significant chemical exposure - towers, batteries, generators).
8. **Site benchmarking** - compare performance across Econet sites (Harare, Bulawayo, Mutare, etc.) - huge management value.

### Lower priority / aspirational
9. **AI photo/voice capture** - photos of hazards auto-classified into issues.
10. **Lone worker monitoring** - field technicians are a real Econet use case.
11. **Geographic map view** - incidents/risks on a Zim map.
12. **Mobile app / offline** - field data collection without connectivity.
13. **Asset maintenance register** - generators, vehicles, tower equipment.
14. **Ergonomics / industrial hygiene modules** - office ergonomics assessments, noise/dust monitoring.

---

## 4. What NOT to build (ThemisIQ boundary - keep separation)

- Enterprise/corporate risk register (ERM) - ThemisIQ's domain; EcoAegis pushes material risks up via the integration
- GRC audits / policy management at corporate level - ThemisIQ GRID
- Board reporting pipelines - ThemisIQ
- Data breach / Sentinel monitoring - ThemisIQ

---

## 5. Recommended build order (respecting scope)

1. **CAPA module** (corrective actions + verification workflow) - closes the biggest audit gap
2. **Inspections module** (checklists, findings, close-out) - schema already exists
3. **Observations quick-capture** (hazard reporting for all employees) - feeds near-miss ratio
4. **Document control** (SOP library with versions + acknowledgement)
5. **Compliance obligations register** (statutory calendar across regulators)
6. **Contractor management** (site readiness on top of vendors)
7. **Chemical/SDS register**
8. **Site benchmarking + geographic map** (once multi-site data exists)

*End of benchmark.*
