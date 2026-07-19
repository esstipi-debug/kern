"""Static content data for GET /how-it-works (webapp/how_it_works_page.py).

Every count here is verified against the actual code, not copied from a
possibly-stale doc: 41 tools = build_default_registry()'s 41 register()
calls in scm_agent/tools.py. The domain-area grouping matches README.md's
"All 40 capabilities" table (corrected here to 41 -- launch_readiness,
tool #41, postdates that table's last refresh). The SCOR Digital Standard
bucket per tool is grounded in documentation/KERN_NIVEL_REFERENCIA_SCM.md
Section 2 (itself generated from a direct code read on 2026-07-17),
cross-checked during this feature's design phase by a 45-agent adversarial
verification workflow against the tools' actual source -- see
docs/superpowers/specs/2026-07-19-how-it-works-page-design.md Section 4.2
for the two gaps that document's own table had (reconciliation/
odoo_replenishment/excel_replenishment bundled under the tool they extend;
launch_readiness added to Plan; leadership_chain marked as genuinely outside
SCOR's scope rather than force-fit).

If a future PR changes the tool count, add/remove/re-bucket a tool here in
the SAME PR -- this module is the single source of truth the donuts and
tallies are computed from, precisely so the numbers can never drift from
what render_how_it_works_html() actually draws.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolInfo:
    key: str
    label: str
    domain_area: str
    scor_bucket: str
    one_liner: str


DOMAIN_AREA_ORDER: tuple[str, ...] = (
    "Inventory & replenishment",
    "Network & logistics",
    "Inventory control & health",
    "Pricing & finance",
    "Demand & classification",
    "Procurement & sourcing",
    "Returns, risk & benchmarking",
    "Planning cadence & projects",
    "Leadership",
)

SCOR_BUCKET_ORDER: tuple[str, ...] = (
    "Plan",
    "Order/Fulfill",
    "Orchestrate",
    "Transform",
    "Source",
    "Return",
    "Outside SCOR scope",
)

TOOLS: tuple[ToolInfo, ...] = (
    # -- Inventory & replenishment (9) --
    ToolInfo("inventory_optimization", "Inventory Optimization", "Inventory & replenishment", "Plan",
             "Forecast -> (s,Q)/(R,S) policy -> budget-constrained portfolio allocation."),
    ToolInfo("newsvendor", "Newsvendor", "Inventory & replenishment", "Plan",
             "Single-period order quantity via the critical-ratio rule, for perishable/seasonal demand."),
    ToolInfo("multi_echelon", "Multi-Echelon", "Inventory & replenishment", "Plan",
             "Guaranteed-Service Model safety-stock placement across a serial chain."),
    ToolInfo("ddmrp", "DDMRP", "Inventory & replenishment", "Plan",
             "Demand Driven MRP buffer zones and a decoupled lead time."),
    ToolInfo("simulation", "Simulation", "Inventory & replenishment", "Plan",
             "Monte Carlo validation of an inventory policy: fill rate, backorders, lost sales."),
    ToolInfo("digital_twin", "Digital Twin", "Inventory & replenishment", "Orchestrate",
             "A network scenario factory that feeds what-if analysis across the suite."),
    ToolInfo("drp", "DRP", "Inventory & replenishment", "Order/Fulfill",
             "Distribution Requirements Planning: a timed, multi-echelon replenishment grid."),
    ToolInfo("odoo_replenishment", "Odoo Replenishment", "Inventory & replenishment", "Plan",
             "Live Odoo ERP read plus staged, reversible replenishment writeback."),
    ToolInfo("excel_replenishment", "Excel Replenishment", "Inventory & replenishment", "Plan",
             "Client spreadsheet read plus staged, reversible replenishment writeback."),
    # -- Inventory control & health (6) --
    ToolInfo("cycle_count", "Cycle Count", "Inventory control & health", "Plan",
             "Cycle-counting program design by ABC class, tracking inventory record accuracy."),
    ToolInfo("reconciliation", "Reconciliation", "Inventory control & health", "Plan",
             "Reconciles inventory records against a physical count."),
    ToolInfo("excess_obsolete", "Excess & Obsolete", "Inventory control & health", "Plan",
             "Classifies SKUs healthy/excess/dead and sizes the cash tied up in slow stock."),
    ToolInfo("markdown_liquidation", "Markdown & Liquidation", "Inventory control & health", "Return",
             "Clearance pricing for excess stock, with a competitor price floor."),
    ToolInfo("fefo", "FEFO", "Inventory control & health", "Order/Fulfill",
             "First-Expired-First-Out issue order plus a markdown-vs-scrap disposition."),
    ToolInfo("data_quality", "Data Quality", "Inventory control & health", "Orchestrate",
             "Master/inventory data quality checks, including GTIN validation."),
    # -- Procurement & sourcing (3) --
    ToolInfo("sourcing", "Sourcing", "Procurement & sourcing", "Source",
             "Supplier scorecarding via OTIF/DIFOT and defect rate."),
    ToolInfo("landed_cost", "Landed Cost", "Procurement & sourcing", "Source",
             "Total cost to destination, Incoterm-aware."),
    ToolInfo("acceptance_sampling", "Acceptance Sampling", "Procurement & sourcing", "Source",
             "Incoming-quality sampling plan by AQL/LTPD."),
    # -- Network & logistics (7) --
    ToolInfo("facility_location", "Facility Location", "Network & logistics", "Order/Fulfill",
             "Cost-minimizing single-facility siting via center-of-gravity and Weiszfeld."),
    ToolInfo("transportation", "Transportation", "Network & logistics", "Order/Fulfill",
             "Mode selection (parcel/LTL/FTL/intermodal) and the LTL-to-FTL break-even."),
    ToolInfo("vehicle_routing", "Vehicle Routing", "Network & logistics", "Order/Fulfill",
             "Vehicle routing via Clarke-Wright savings and the sweep algorithm."),
    ToolInfo("warehouse_layout", "Warehouse Layout", "Network & logistics", "Order/Fulfill",
             "A parametric, navigable 3D warehouse layout: racks, aisles, docks, yard."),
    ToolInfo("slotting", "Slotting", "Network & logistics", "Order/Fulfill",
             "Warehouse slotting by cube-per-order index and pick affinity."),
    ToolInfo("queuing", "Queuing", "Network & logistics", "Transform",
             "Cost-optimal server counts via an M/M/c queuing model."),
    ToolInfo("scheduling", "Scheduling", "Network & logistics", "Transform",
             "Job dispatching rules (SPT/EDD/FCFS/LPT) to minimize flow time or lateness."),
    # -- Pricing & finance (6) --
    ToolInfo("pricing", "Pricing", "Pricing & finance", "Orchestrate",
             "Price elasticity to a margin-maximizing price point."),
    ToolInfo("price_intelligence", "Price Intelligence", "Pricing & finance", "Orchestrate",
             "One-shot competitor price position analysis."),
    ToolInfo("price_watch", "Price Watch", "Pricing & finance", "Orchestrate",
             "Recurring, read-only competitor price monitoring with auto-onboarding."),
    ToolInfo("financial_kpis", "Financial KPIs", "Pricing & finance", "Orchestrate",
             "Inventory turns, DIO, GMROI, cash-to-cash."),
    ToolInfo("cost_to_serve", "Cost to Serve", "Pricing & finance", "Order/Fulfill",
             "Cost-to-serve by customer/channel, including the whale-curve view."),
    ToolInfo("learning_curve", "Learning Curve", "Pricing & finance", "Transform",
             "Wright's-law cost projection over cumulative production volume."),
    # -- Returns, risk & benchmarking (3) --
    ToolInfo("returns", "Returns", "Returns, risk & benchmarking", "Return",
             "Reverse-logistics disposition (restock/refurbish/liquidate/scrap)."),
    ToolInfo("risk", "Risk", "Returns, risk & benchmarking", "Orchestrate",
             "A supply chain risk register: EMV, FMEA, and a TTR-vs-TTS resilience gap."),
    ToolInfo("dea", "DEA", "Returns, risk & benchmarking", "Orchestrate",
             "Data Envelopment Analysis efficiency benchmarking."),
    # -- Demand & classification (3) --
    ToolInfo("abc_xyz", "ABC-XYZ", "Demand & classification", "Plan",
             "ABC-XYZ classification into a 9-cell policy matrix."),
    ToolInfo("forecast", "Forecast", "Demand & classification", "Plan",
             "Demand forecasting, including intermittent-demand methods."),
    ToolInfo("whatif", "What-If", "Demand & classification", "Plan",
             "Sensitivity analysis: tornado charts, best/worst case, break-even."),
    # -- Planning cadence & projects (3) --
    ToolInfo("sop", "S&OP", "Planning cadence & projects", "Plan",
             "Aggregate Sales & Operations Planning: Chase/Level/Hybrid strategies."),
    ToolInfo("earned_value", "Earned Value", "Planning cadence & projects", "Transform",
             "Project earned-value management: SV/CV/SPI/CPI."),
    ToolInfo("launch_readiness", "Launch Readiness", "Planning cadence & projects", "Plan",
             "Campaign launch-date readiness versus lead time and coverage."),
    # -- Leadership (1) --
    ToolInfo("leadership_chain", "Leadership Chain", "Leadership", "Outside SCOR scope",
             "A CHAIN leadership profile and directives -- an organizational assessment, "
             "not a supply chain operation."),
)


def tally_by_domain_area() -> dict[str, int]:
    counts = Counter(t.domain_area for t in TOOLS)
    return {area: counts.get(area, 0) for area in DOMAIN_AREA_ORDER}


def tally_by_scor_bucket() -> dict[str, int]:
    counts = Counter(t.scor_bucket for t in TOOLS)
    return {bucket: counts.get(bucket, 0) for bucket in SCOR_BUCKET_ORDER}


@dataclass(frozen=True)
class CertCoverage:
    name: str
    body: str
    level: str  # "High" | "Medium-high" | "Partial"
    covered: tuple[str, ...]
    gaps: tuple[str, ...]


CERTIFICATIONS: tuple[CertCoverage, ...] = (
    CertCoverage(
        "CPIM", "ASCM", "High",
        covered=(
            "Demand forecasting, including intermittent demand",
            "EOQ and volume discounts",
            "Safety stock from forecast-error sigma",
            "(s,Q) and (R,S) policies",
            "Newsvendor model",
            "Multi-echelon Guaranteed-Service Model",
            "DDMRP",
            "ABC-XYZ classification",
            "Aggregate S&OP",
            "DRP",
            "Policy simulation",
            "Cycle counting / inventory record accuracy",
            "Job sequencing",
            "Learning curve",
        ),
        gaps=(
            "Detailed MRP-II / multi-level BOM explosion",
            "Formal master production scheduling (MPS)",
            "Order-level plant capacity management",
        ),
    ),
    CertCoverage(
        "CLTD", "ASCM", "High",
        covered=(
            "DRP",
            "Facility location",
            "Mode selection and LTL/FTL break-even",
            "Vehicle routing (CVRP)",
            "Warehouse layout and slotting",
            "FEFO / expiry management",
            "Cost-to-serve",
            "Landed cost",
            "Reverse logistics",
        ),
        gaps=(
            "Customs / international trade compliance beyond Incoterm + tariff",
            "Operational WMS/TMS",
            "Multi-facility network optimization (only single-facility siting today)",
            "Global trade compliance",
        ),
    ),
    CertCoverage(
        "CSCP", "ASCM", "Medium-high",
        covered=(
            "End-to-end integration via the orchestrator",
            "SCOR process mapping",
            "Technology: digital twin, agent, writeback",
            "Metrics: cash-to-cash, cost-to-serve, DEA",
            "TTR/TTS risk",
        ),
        gaps=(
            "Computable sustainability (only unstructured knowledge, no calculation engine)",
            "Network/chain design at MILP scale",
            "Customer collaboration / CRM",
            "Strategic supplier relationship management",
        ),
    ),
    CertCoverage(
        "SCPro", "CSCMP", "Medium-high",
        covered=(
            "Network analysis: facility location, digital twin",
            "KPIs and benchmarking: DEA, financial KPIs",
            "Problem-solving: decision support, MCDM, what-if",
            "Project management: earned value",
        ),
        gaps=(
            "Network analysis at scale (joint optimization)",
            "Live external data integration (most calculation is offline by design)",
            "Sustainability",
        ),
    ),
    CertCoverage(
        "CPSM", "ISM", "Partial",
        covered=(
            "Supplier performance: OTIF/PPM",
            "Landed cost / TCO",
            "Multi-criteria supplier selection: BWM/TOPSIS",
            "Acceptance sampling",
            "Change/negotiation analysis",
            "Supply risk",
        ),
        gaps=(
            "Deep supplier relationship management (Kraljic segmentation, power matrix, "
            "supplier development)",
            "Contract lifecycle management",
            "Category strategy",
            "End-to-end strategic sourcing",
            "Supplier ethics/diversity",
            "Spend analysis",
        ),
    ),
)


@dataclass(frozen=True)
class IsoClause:
    clause: str
    kern_behavior: str


ISO_9001_CLAUSES: tuple[IsoClause, ...] = (
    IsoClause("4.4 / 8.1 Process approach",
              "A fixed, self-describing pipeline: every capability is a Tool with "
              "prepare/run/qa/deliver -- the same sequence for all 41."),
    IsoClause("7.5 Documented information",
              "An EvidenceRecord (SHA-256 of inputs, control totals, formula versions, QA "
              "attestations); a writeback AuditEntry; a Keep-a-Changelog CHANGELOG; L3 "
              "citations on every deliverable."),
    IsoClause("8.5.1 Control of production",
              "Grounding, confidence, and guided options on every deliverable; personas/KPIs "
              "per mode."),
    IsoClause("8.5.2 Traceability",
              "Partial: data_quality validates GTIN (GS1); lots/ gives FEFO plus expiry. Lot "
              "genealogy is not yet built."),
    IsoClause("8.6 Release of products",
              "A double gate: the tool's own QA, plus a citation_gate in packages."),
    IsoClause("8.7 Control of nonconforming outputs",
              "QA fails => zero deliverables. A package's runner computes everything first "
              "and only writes if every step passes."),
    IsoClause("8.5.6 Control of changes",
              "Writeback stage -> signed, time-boxed approval -> apply -> rollback. Autonomy "
              "config changes only via a signed Changeset."),
    IsoClause("9.1 Monitoring and measurement",
              "forecast_metrics, financial_kpis, dea, reliability.py."),
    IsoClause("10 Continual improvement",
              "A verify/backtest loop feeding a ToolReliabilityReport, which drives "
              "evidence-based autonomy promotion."),
)

ISO_28000_ELEMENTS: tuple[IsoClause, ...] = (
    IsoClause("Risk assessment",
              "risk.py: EMV, FMEA RPN, a risk heatmap, and the TTR-vs-TTS gap; risk_period "
              "for lead-time risk."),
    IsoClause("System security controls",
              "Bounded parameters, client allowlisting, path-traversal defense, a 25 MB "
              "upload cap, rate limiting, constant-time API-key comparison, CSP, "
              "formula-injection defusing."),
    IsoClause("Change authorization",
              "Signed, time-boxed (HMAC) approvals tied to an idempotency + content hash; "
              "autonomy tiers with human gates; SLA-bound escalation."),
    IsoClause("Continuity / fail-safe",
              "A hardening flag refuses to boot without required controls; the "
              "never-unprotected contract; idempotent apply plus rollback; graceful "
              "degradation without an LLM key."),
)


@dataclass(frozen=True)
class Gap:
    name: str
    current_state: str
    standard: str


HONEST_GAPS: tuple[Gap, ...] = (
    Gap(
        "Computable sustainability",
        "Only unstructured knowledge (a book in the graph); no carbon/GHG calculation "
        "engine yet, even though the pricing/logistics layer already names sustainability "
        "as a persona concern.",
        "SCOR ES, CSCP, ISO 14001",
    ),
    Gap(
        "Deep supplier relationship management",
        "Supplier scorecarding is OTIF/DIFOT plus defect rate only; no Kraljic "
        "segmentation, power analysis, or supplier development.",
        "CPSM, CSCP",
    ),
    Gap(
        "Lot-level traceability / genealogy",
        "GTIN validation plus FEFO/expiry exist; no one-up/one-down chain of custody or "
        "EPCIS event log.",
        "ISO 9001 8.5.2, food/pharma",
    ),
    Gap(
        "Broad regulatory compliance",
        "Strong on pricing (EU/UK Omnibus) and customs (Incoterm); missing trade "
        "compliance, HS classification, denied-party screening.",
        "CLTD, CPSM",
    ),
    Gap(
        "Quantified resilience",
        "A qualitative TTR-vs-TTS gap exists in the risk register; no network stress-test "
        "with recovery time/impact simulation.",
        "SCOR AG (agility)",
    ),
    Gap(
        "Network optimization at scale",
        "Only single-facility siting (Weiszfeld); no multi-facility p-median/MILP.",
        "CLTD, SCPro",
    ),
)
