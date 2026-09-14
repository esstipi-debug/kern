"""The MCP tool surface, as data: one spec per exposed tool.

This file is the source of truth for WHAT the read-only MCP server exposes
(docs/MCP_SERVER.md summarizes it; `webapp/mcp_server.py` registers each spec
in a loop). Every spec bridges to the same orchestrator entry point with
`job_type=spec.job_type`, so adding a tool here = adding one spec, no server
code edits.

Deliberately NOT here (see docs/MCP_SERVER.md "What's NOT here yet"):
- `odoo_replenishment`, `excel_replenishment` - writeback tools; they mutate a
  client's system of record and stay behind the direct-client safety plane
  (`nosilo_core/writeback.py`), never on this anonymous-remote surface.
- `repricing` - same rule, one step earlier. The tool itself only STAGES (it
  never imports `apply_repricing`, stages against an in-memory copy of the
  client's own current prices, and always returns HANDOFF), so nothing it does
  writes anywhere. It is still off this surface on purpose: its entire subject
  is a proposed change to a client's live prices, its output is a changeset
  shaped to be applied, and an anonymous caller has no business composing one
  against someone else's catalogue. Autonomy over a brand-new external write
  surface is earned with A4 evidence (`jobs/repricing.py`'s own Golden Rule 11
  note); a remote surface is not where that starts.
- `leadership_chain` - takes a CHAIN scores dict / LLM-scored brief, not
  tabular rows; the rows->CSV bridge feeds it nothing usable.
- `warehouse_layout` - a params-driven 3D layout generator (requires_data=False,
  ignores rows) whose deliverable is an HTML viewer, not a data analysis. Contrast
  `flow_map`, which IS exposed below: its input is tabular movement rows and its
  primary output is a data analysis (measured transit per lane); the self-contained
  HTML sankey map it also writes is an extra visualization of that analysis, not
  the whole deliverable the way warehouse_layout's 3D viewer is.
- `intake_scan` - takes a directory path and reads every file inside it; on an
  anonymous remote MCP surface that is an arbitrary-file-read primitive, not a
  data-analysis input. See docs/MCP_SERVER.md's "What's NOT here yet" for the
  full reasoning (same class of risk the `org_friction` `*_path` guard closes).
- `procedure_draft` - a params-driven SOP-template generator (requires_data=False,
  ignores rows), same rule as `warehouse_layout` above: nothing for the rows->CSV
  bridge to feed it. `procedure_audit` IS exposed below; it reads a client's
  tabular procedure register.
- `iso_ops` - plant-profile JSON/params (detect, propose floor tasks, evaluate
  order evidence). HTTP `POST /api/detectar` already covers the same contract;
  the rows->CSV bridge has nothing to feed a ficha, and this surface must not
  look like a certification API. Floor evidence stays on HTTP; the MCP family
  below is observation of tabular evidence (`kern_iso_sop_audit`,
  `kern_audit_evidence`, `kern_iso_scor_audit`).

Each description is the user-facing MCP contract: what the tool does, the
canonical columns its job's `prepare()` matches (aliases are tolerated -
case/spacing variants of these names), the params it reads, and what comes
back. Column names below are copied from the matching `jobs/<x>_job.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MCPToolSpec:
    """One read-only analysis tool on the MCP surface."""

    name: str  # MCP tool name shown to clients (linchpin_*)
    job_type: str  # Tool.key in scm_agent/tools.py (orchestrator job_type override)
    title: str  # human-readable annotation title
    description: str  # full calling contract (docstring-style)


_RETURNS = (
    'Returns: JSON with status ("ok"/"needs_data"/"needs_clarification"/"qa_failed"/'
    '"error"), summary, confidence, citations, and report_markdown (the full '
    'client-ready analysis as markdown) when status is "ok".'
)

TOOL_SPECS: tuple[MCPToolSpec, ...] = (
    # -- the original Phase A eight ------------------------------------------------
    MCPToolSpec(
        name="linchpin_inventory_optimize",
        job_type="inventory_optimization",
        title="Optimize Inventory Policy",
        description=(
            "Forecast demand and recommend (s,Q)/(R,S) reorder policies + a budget-fit "
            "inventory plan, per SKU.\n\n"
            "Rows need columns: date, product_id, quantity (required); unit_cost, "
            "lead_time_days (optional, improve the plan when present). One row per "
            'SKU-period, e.g. {"date": "2026-01-01", "product_id": "SKU-A", "quantity": 42, '
            '"unit_cost": 12.5, "lead_time_days": 7}.\n\n'
            "Useful params: service_level (0-1, default 0.95), holding_rate (default 0.25), "
            "order_cost (default 75.0), budget (optional spend cap), periods_per_year "
            "(default 52.0).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_classify_abc_xyz",
        job_type="abc_xyz",
        title="ABC-XYZ Classify SKUs",
        description=(
            "Classify SKUs into the 9-cell ABC (value) x XYZ (demand variability) matrix "
            "and assign a review policy + service-level target per cell.\n\n"
            "Rows need columns: product_id, quantity (demand history), unit_cost. One row "
            'per SKU-period, e.g. {"product_id": "SKU-A", "quantity": 42, "unit_cost": 12.5}.\n\n'
            "Useful params: abc_thresholds (default [0.80, 0.95], cumulative value share "
            "cut points), cv_cuts (default [0.5, 1.0], coefficient-of-variation cut points).\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_newsvendor_order_quantity",
        job_type="newsvendor",
        title="Newsvendor Order Quantity",
        description=(
            "Set the profit-maximizing one-shot order quantity per SKU for perishable, "
            "seasonal, or spare-part demand (the critical-ratio newsvendor model).\n\n"
            "Rows need columns: product_id, a mean-demand column, price, unit_cost. A "
            "demand-std column and salvage/goodwill columns are optional but sharpen the "
            'result, e.g. {"product_id": "SKU-A", "mean_demand": 100, "std_demand": 20, '
            '"price": 30, "unit_cost": 12, "salvage_value": 4}.\n\n' + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_forecast_demand",
        job_type="forecast",
        title="Forecast Demand & Method Fit",
        description=(
            "Segment SKUs by forecastability, auto-select and backtest the matching "
            "forecasting method (SES/Croston, or AutoETS/TSB when installed), and quantify "
            "forecast value-add versus a naive baseline.\n\n"
            "Rows need columns: product_id, a quantity column, and a period column (date "
            'or sequential period index). One row per SKU-period, e.g. {"product_id": '
            '"SKU-A", "period": "2026-W01", "quantity": 42}.\n\n'
            "Useful params: holdout_fraction (default 0.25, share of history held out for "
            "backtesting), min_backtest_periods (default 4).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_financial_kpis",
        job_type="financial_kpis",
        title="Inventory Financial KPIs",
        description=(
            "Roll up the per-SKU finance pack: inventory turns, DIO, GMROI, sell-through, "
            "inventory-to-sales, cash-to-cash, and flag the weakest-GMROI SKUs.\n\n"
            "Rows need columns: product_id, a COGS column, an inventory-value column. Margin, "
            "units-sold, units-on-hand, and net-sales columns are optional but improve "
            'coverage, e.g. {"product_id": "SKU-A", "cogs": 500, "inventory_value": 1200, '
            '"units_sold": 40, "units_on_hand": 15}.\n\n'
            "Useful params: dso_days, dpo_days, dio_days (working-capital cash-cycle inputs).\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_price_optimize",
        job_type="pricing",
        title="Optimize Price per SKU",
        description=(
            "Estimate per-SKU price elasticity from a price/quantity history and "
            "recommend a margin-maximizing price.\n\n"
            "Rows need columns: product_id, a price column, a quantity-sold column, one row "
            "per SKU-period (price changes over time are what identify elasticity), e.g. "
            '{"product_id": "SKU-A", "date": "2026-01-01", "price": 29.99, "quantity": 120}.\n\n'
            "Useful params: cost_ratio (default 0.6, used to impute unit cost when not "
            "directly observable - lowers confidence on the affected SKUs).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_audit_data_quality",
        job_type="data_quality",
        title="Audit Product Master Data Quality",
        description=(
            "Audit a product master for duplicate SKUs (shared GTIN or fuzzy name match), "
            "invalid GTIN/UPC check digits, and completeness gaps, then rank remediation.\n\n"
            "Rows need columns: product_id, a product-name column. GTIN and unit-cost "
            'columns are optional but sharpen the audit, e.g. {"product_id": "SKU-A", '
            '"name": "Widget 12mm", "gtin": "012345678905"}.\n\n'
            "Useful params: name_threshold (default 90.0, fuzzy-match similarity cutoff "
            "0-100 for flagging near-duplicate names).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_whatif_sensitivity",
        job_type="whatif",
        title="What-If Sensitivity Sweep",
        description=(
            "Sweep a planning assumption (demand, holding cost, lead time, ...) over a "
            "low/high band against the inventory policy's cost, ranking drivers by impact "
            "(a tornado chart's underlying data) and bounding the optimistic/pessimistic case.\n\n"
            "Rows need columns: a driver-name column, low and high band columns, plus a "
            'base-value/unit column, e.g. {"driver": "demand", "low": -0.15, "high": 0.20, '
            '"base_value": 100, "unit": "units/period"}.\n\n'
            "Useful params: metric (default \"annual_cost\"), budget_pct (default 0.10), "
            "maximize (default false).\n\n" + _RETURNS
        ),
    ),
    # -- cost, margin & finance -----------------------------------------------------
    MCPToolSpec(
        name="linchpin_cost_to_serve",
        job_type="cost_to_serve",
        title="Cost-to-Serve & Working Capital",
        description=(
            "Allocate the true cost to serve each customer segment / channel, flag "
            "loss-making segments, and size the working-capital cash-release opportunity.\n\n"
            "Rows need columns: segment (or channel), sales (revenue). Optional: quantity, "
            "order_id (order-level cost allocation), cogs, returns_units, shipping/freight "
            'cost. One row per order line or aggregate, e.g. {"segment": "Retail", '
            '"sales": 1200.0, "cogs": 700.0, "order_id": "SO-77"}.\n\n'
            "Useful params: cost_ratio (default 0.6, imputes COGS when absent), "
            "cost_per_order, cost_per_unit_shipped, return_handling_per_unit, "
            "dso_days / dpo_days / dio_days (cash-cycle inputs).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_landed_cost",
        job_type="landed_cost",
        title="Landed-Cost Study",
        description=(
            "Compute the Incoterm-aware fully landed cost per SKU (goods + freight + "
            "insurance + duty + handling + broker) and each component's share.\n\n"
            "Rows need columns: sku, unit_cost, qty. Optional: freight, insurance, "
            'duty_rate, handling, broker_fee, incoterm. E.g. {"sku": "SKU-A", '
            '"unit_cost": 8.4, "qty": 500, "freight": 420, "duty_rate": 0.06, '
            '"incoterm": "FOB"}.\n\n' + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_earned_value",
        job_type="earned_value",
        title="Earned Value (Project Control)",
        description=(
            "Roll up project cost/schedule control: SV, CV, SPI, CPI per task and for the "
            "whole project, flagging the worst work packages.\n\n"
            "Rows need columns: task, planned (PV / budgeted cost of work scheduled), "
            "earned (EV / budgeted cost of work performed), actual (AC / actual cost). "
            'E.g. {"task": "Rack install", "planned": 10000, "earned": 8000, "actual": 9500}.\n\n'
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_learning_curve",
        job_type="learning_curve",
        title="Learning-Curve Cost-Down",
        description=(
            "Project unit and cumulative cost at volume from a first-unit cost and a "
            "learning rate (Yx = K * x^n cost-down curve), per product.\n\n"
            "Rows need columns: product, first_unit_cost, learning_rate (e.g. 0.85 for an "
            '85% curve), planned_volume. E.g. {"product": "Assembly-X", '
            '"first_unit_cost": 1200, "learning_rate": 0.85, "planned_volume": 250}.\n\n'
            + _RETURNS
        ),
    ),
    # -- inventory health & disposition ----------------------------------------------
    MCPToolSpec(
        name="linchpin_excess_obsolete",
        job_type="excess_obsolete",
        title="Excess & Obsolete (E&O) Stock",
        description=(
            "Classify stock as healthy / excess / dead from days-of-cover and idle time, "
            "recommend a disposition per SKU, and total the cash tied up.\n\n"
            "Rows need columns: product_id, on_hand, daily_demand. Optional: unit_cost "
            "(values the exposure), days_since_last_sale (idle-time signal). E.g. "
            '{"product_id": "SKU-A", "on_hand": 900, "daily_demand": 1.2, "unit_cost": 7.5, '
            '"days_since_last_sale": 210}.\n\n'
            "Useful params: target_cover_days (default 90.0), dead_threshold_days.\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_markdown_liquidation",
        job_type="markdown_liquidation",
        title="Markdown Liquidation Plan",
        description=(
            "Cross E&O classification with clearance pricing: per liquidation-candidate "
            "SKU, a clearance price, expected weeks-to-clear, and cash recovered vs. "
            "salvage.\n\n"
            "Rows: the same stock shape as the E&O tool - product_id, on_hand, daily_demand "
            "required; unit_cost, days_since_last_sale optional.\n\n"
            "Useful params: default_markdown_pct (default 0.5), salvage_recovery_pct, "
            "horizon_weeks, floor_ratio. Note: elasticity-fitted clearance pricing needs a "
            "price-history file on disk (params.price_history_path), which an inline-rows "
            "MCP call cannot supply - over MCP the plan prices with the default-markdown "
            "heuristic, which is still a valid, QA-gated plan.\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_fefo_expiry",
        job_type="fefo",
        title="Lot Expiry & FEFO Disposition",
        description=(
            "Age lot-level stock by shelf life: FEFO issue order, units that will expire "
            "unsold at the current run rate, and markdown-vs-scrap disposition per lot.\n\n"
            "Rows need columns: product_id, lot_id, quantity, plus EITHER days_to_expiry "
            'OR expiry_date (dates require params.as_of). E.g. {"product_id": "SKU-A", '
            '"lot_id": "L-31", "quantity": 400, "days_to_expiry": 21, "daily_demand": 12}. '
            "Optional: unit_cost, unit_price, daily_demand.\n\n"
            'Useful params: as_of ("YYYY-MM-DD", required when rows carry expiry_date '
            "instead of days_to_expiry).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_inventory_record_accuracy",
        job_type="reconciliation",
        title="Inventory Record Accuracy (IRA)",
        description=(
            "Reconcile system inventory records against physical counts: IRA within a "
            "tolerance band, variance value in currency, and the worst lines to fix first.\n\n"
            "Rows need columns: product_id, system_qty, physical_qty. Optional: unit_cost "
            '(values the variance). E.g. {"product_id": "SKU-A", "system_qty": 120, '
            '"physical_qty": 114, "unit_cost": 9.8}.\n\n'
            "Useful params: tolerance_pct (default 0.0), tolerance_units (default 0.0).\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_cycle_count_plan",
        job_type="cycle_count",
        title="Cycle-Count Program",
        description=(
            "Build an ABC-weighted cycle-count program: count frequency per class, an "
            "evenly spread schedule, and a balanced daily counting workload.\n\n"
            "Rows need columns: product_id, plus ONE of - an abc column (values A/B/C), an "
            "annual_value column, or annual_demand + unit_cost (value is derived). E.g. "
            '{"product_id": "SKU-A", "annual_demand": 4800, "unit_cost": 12.5}.\n\n'
            "Useful params: working_days (default 250), abc_thresholds (default [0.80, 0.95]).\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_returns_disposition",
        job_type="returns",
        title="Returns & Reverse Logistics",
        description=(
            "Rank each returned lot's disposition (restock / refurbish / liquidate / scrap) "
            "by net recovery; report recovery rate, value at risk, and the return-reason "
            "Pareto.\n\n"
            "Rows need columns: product_id, returned_units, unit_cost. Optional: reason, "
            'resale_value, sellable. E.g. {"product_id": "SKU-A", "returned_units": 30, '
            '"unit_cost": 12.5, "reason": "damaged box", "resale_value": 25.0}.\n\n'
            "Useful params: restock_handling_per_unit, refurbish_cost_per_unit, "
            "refurbish_resale_factor (default 0.6), liquidation_recovery_pct (default 0.2), "
            "scrap_cost_per_unit.\n\n" + _RETURNS
        ),
    ),
    # -- planning & policies ----------------------------------------------------------
    MCPToolSpec(
        name="linchpin_sop_plan",
        job_type="sop",
        title="Sales & Operations Planning (S&OP)",
        description=(
            "Run one S&OP/IBP cycle from a demand history: consensus demand plan, then "
            "chase vs. level vs. hybrid supply strategies compared on cost, with a ranked "
            "recommendation.\n\n"
            "Rows need columns: a date/period column and a demand quantity column; one row "
            "per period or transaction (history is resampled to params.freq). Needs at "
            'least 2 periods after resampling. E.g. {"date": "2026-01-05", "quantity": 340}.\n\n'
            'Useful params: freq (pandas offset alias, default "MS" = monthly), '
            "opening_inventory, target, holding_cost (default 1.0), shortage_cost "
            "(default 5.0), capacity_change_cost (default 2.0).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_ddmrp_buffers",
        job_type="ddmrp",
        title="DDMRP Buffer Plan",
        description=(
            "Size DDMRP red/yellow/green buffers per part and compute the net-flow "
            "planning signal (which parts to reorder now, and how much).\n\n"
            "Rows need columns: part_id, adu (average daily usage), dlt (decoupled lead "
            "time, days). Optional: ltf, vf, moq, order_cycle_days, on_hand, on_order, "
            'qualified_demand. E.g. {"part_id": "P-9", "adu": 14, "dlt": 12, "on_hand": 220, '
            '"on_order": 100}.\n\n'
            "Useful params: ltf (lead-time factor, default 0.5), vf (variability factor, "
            "default 0.5).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_multi_echelon_stock",
        job_type="multi_echelon",
        title="Multi-Echelon Safety-Stock Placement",
        description=(
            "Place safety stock across a serial supply chain (e.g. factory -> DC -> store) "
            "with the Guaranteed-Service Model: where to hold buffer and how much, versus "
            "the naive hold-at-every-stage baseline.\n\n"
            "Rows: one per stage in sequence, columns stage, lead_time, holding_cost (per "
            "unit per period). Optional: order (explicit stage sequence). End-customer "
            "demand comes from mean_demand and demand_std columns (first row) or from "
            'params. E.g. {"stage": "DC", "lead_time": 5, "holding_cost": 1.2}.\n\n'
            "Useful params: service_level (default 0.95), mean_demand, demand_std (required "
            "if not present as columns), review_period.\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_drp_plan",
        job_type="drp",
        title="Distribution Requirements Planning (DRP)",
        description=(
            "Build the time-phased DRP grid per branch/DC (projected on-hand, planned "
            "orders per period) and roll branches up into the central DC's gross "
            "requirements.\n\n"
            "Rows: long format, one per branch-period, columns branch, period, demand. "
            'Optional: on_hand, lead_time, safety_stock, lot_size. E.g. {"branch": "North", '
            '"period": 1, "demand": 120, "on_hand": 300, "lead_time": 2}.\n\n' + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_simulate_policy",
        job_type="simulation",
        title="Simulation-Optimized (R,S) Policy",
        description=(
            "Search for the (R,S) inventory policy (review period + order-up-to level) "
            "that minimizes simulated total cost (holding + ordering + backorder), via "
            "Monte-Carlo simulation per SKU - useful when demand is too lumpy for "
            "closed-form formulas.\n\n"
            "Rows need columns: product_id, mean_demand, std_demand, lead_time (periods). "
            "Optional per-row: holding_cost, order_cost, backorder_cost, review_period. "
            'E.g. {"product_id": "SKU-A", "mean_demand": 40, "std_demand": 18, "lead_time": 2}.\n\n'
            "Useful params: periods (simulation horizon), holding_cost, order_cost, "
            "backorder_cost, review_period.\n\n" + _RETURNS
        ),
    ),
    # -- sourcing, quality & risk ------------------------------------------------------
    MCPToolSpec(
        name="linchpin_supplier_sourcing",
        job_type="sourcing",
        title="Supplier Sourcing & Selection",
        description=(
            "Score suppliers on OTIF, lead time, quality (defect PPM) and price, then rank "
            "them (TOPSIS multi-criteria) into a recommended award decision.\n\n"
            "Rows need columns (one row per delivery / PO line): supplier, plus every field "
            "that decides the award - on_time, in_full, lead_time_days, units, defects. "
            "These are REQUIRED, not optional: a missing one used to score as zero and "
            "produced an all-zero scorecard that still named a winner. unit_price is "
            'genuinely optional (it adds a price criterion). E.g. {"supplier": "Acme", '
            '"on_time": 1, "in_full": 1, "lead_time_days": 9, "units": 500, "defects": 3, '
            '"unit_price": 4.2}.\n\n'
            "A raw ERP export works too: pass order/delivery dates plus either a promised "
            "date or an explicit sla_days, and on_time / in_full / lead_time_days are "
            "derived (params: order_date_col, delivery_date_col, promised_date_col, "
            "sla_days, received_qty_col, in_full_status_col + in_full_ok_values). "
            "Anything that cannot be derived is refused by name as needs_data.\n\n"
            "Useful params: weights (dict; defaults otif 0.4 / lead_time 0.2 / ppm 0.2 / "
            "price 0.2).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_acceptance_sampling",
        job_type="acceptance_sampling",
        title="Acceptance Sampling (Receiving Quality)",
        description=(
            "Design the smallest receiving-inspection sampling plan (inspect n units, "
            "accept at most c defects) per part from AQL/LTPD quality targets, plus the "
            "inspect-vs-skip break-even.\n\n"
            "Rows need columns: part, aql, ltpd (fractions - e.g. AQL 0.01, LTPD 0.05). "
            'E.g. {"part": "Casting-7", "aql": 0.01, "ltpd": 0.05}.\n\n'
            "Useful params: producer_risk (default 0.05), consumer_risk (default 0.10).\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_risk_assessment",
        job_type="risk",
        title="Supply-Chain Risk Assessment",
        description=(
            "Score a supply-chain risk register (likelihood x impact), rank by expected "
            "monetary value and FMEA-style priority, and lay out the 5x5 heatmap plus a "
            "mitigation plan.\n\n"
            "Rows need columns: name, likelihood (0-1 annual probability), impact_value "
            "(currency loss if it hits). Optional: category, exposure, time_to_recover, "
            'time_to_survive, owner. E.g. {"name": "Port strike", "likelihood": 0.15, '
            '"impact_value": 250000, "category": "logistics"}.\n\n'
            "Useful params: severity_thresholds, mitigations (dict keyed by risk name).\n\n"
            + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_efficiency_benchmark",
        job_type="dea",
        title="Efficiency Benchmarking (DEA)",
        description=(
            "Benchmark comparable units (suppliers, warehouses, stores, lines) on a "
            "best-practice efficiency frontier (input-oriented CCR DEA) - the data sets "
            "the weights, not the analyst - and rank the laggards with peer references.\n\n"
            "Rows: one per unit. Columns: a unit-name column (unit / dmu / name / supplier "
            "/ warehouse / store), plus input columns prefixed input_ (resources consumed) "
            'and output columns prefixed output_ (results produced). E.g. {"unit": "DC-1", '
            '"input_labor_hours": 1200, "input_sqm": 800, "output_lines_shipped": 90000}.\n\n'
            "Useful params: input_cols, output_cols (explicit column-name lists override "
            "the prefix auto-detect).\n\n" + _RETURNS
        ),
    ),
    # -- operations: flow, staffing & scheduling ---------------------------------------
    MCPToolSpec(
        name="linchpin_queuing_staffing",
        job_type="queuing",
        title="Queuing / Staffing Optimization",
        description=(
            "Size each service point (dock door, pack station, help desk) to the "
            "cost-optimal number of servers from arrival and service rates (Erlang-C "
            "waiting-line math), trading waiting cost against staffing cost.\n\n"
            "Rows need columns: station, arrival_rate, service_rate (per server, same time "
            'unit as arrivals). Optional per-row overrides: wait_cost, server_cost. E.g. '
            '{"station": "Receiving dock", "arrival_rate": 8, "service_rate": 3}.\n\n'
            "Useful params: wait_cost (default 10.0/unit/period), server_cost (default "
            "5.0/server/period), max_servers (default 30).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_job_sequencing",
        job_type="scheduling",
        title="Job Sequencing",
        description=(
            "Sequence a queue of jobs on one resource: recommends the run order (SPT / EDD "
            "/ FCFS ... compared head-to-head) with the flow-time vs. on-time trade-off "
            "quantified.\n\n"
            "Rows need columns: job, processing_time. Optional: due_date (enables "
            'lateness-based rules). E.g. {"job": "WO-101", "processing_time": 4.5, '
            '"due_date": 12}.\n\n'
            'Useful params: objective (default "auto").\n\n' + _RETURNS
        ),
    ),
    # -- network, logistics & warehouse -------------------------------------------------
    MCPToolSpec(
        name="linchpin_transport_mode_select",
        job_type="transportation",
        title="Transport-Mode Selection & Lane Freight",
        description=(
            "Pick the cheapest feasible transport mode (parcel / LTL / FTL / intermodal) "
            "per shipment from a configurable rate card, and rank lanes by freight spend.\n\n"
            "Rows need columns: weight_kg, distance_km (one row per shipment). Optional: "
            'shipment_id, lane, units, order_value. E.g. {"shipment_id": "SH-9", '
            '"weight_kg": 4200, "distance_km": 850, "lane": "MAD->BCN"}.\n\n'
            "Useful params: rate-card overrides (advanced; defaults model typical "
            "parcel/LTL/FTL/intermodal rates).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_facility_location",
        job_type="facility_location",
        title="Facility Location (Network Design)",
        description=(
            "Place one facility (DC, plant, cross-dock) to minimize demand-weighted "
            "travel: center-of-gravity plus the exact 1-median (Weiszfeld), compared "
            "against the nearest existing point.\n\n"
            "Rows: one per demand point, columns x, y (or lon/lat). Optional: name, weight "
            '(demand/volume per point - defaults to 1). E.g. {"name": "Store-12", '
            '"lon": -3.7, "lat": 40.4, "weight": 1200}.\n\n' + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_warehouse_slotting",
        job_type="slotting",
        title="Warehouse Slotting (COI + Affinity)",
        description=(
            "Assign SKUs to warehouse pick zones by cube-per-order index (COI) and "
            "co-locate frequently co-ordered SKUs (affinity pairs), from order-line "
            "history.\n\n"
            "Rows: one per order line, columns order_id, product_id. Optional: unit_volume "
            '(enables cube-aware slotting). E.g. {"order_id": "SO-1001", "product_id": '
            '"SKU-A", "unit_volume": 0.02}.\n\n' + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_vehicle_routing",
        job_type="vehicle_routing",
        title="Vehicle Routing & Scheduling",
        description=(
            "Group delivery stops into capacity-feasible vehicle routes and sequence them "
            "(Clarke-Wright savings vs. sweep - the cheaper plan wins against a "
            "one-truck-per-stop baseline), with optional time-window feasibility flags.\n\n"
            "Rows: one per stop, columns x, y (or lon/lat). Optional: stop_id (must be "
            "unique), demand, service_time, tw_start, tw_end. E.g. "
            '{"stop_id": "C-4", "x": 12.1, "y": 3.4, "demand": 18}.\n\n'
            "REQUIRED param: capacity (max demand units per vehicle) - without it the tool "
            "returns needs_data. Useful params: depot, speed.\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_flow_map",
        job_type="flow_map",
        title="Map Observed Flows & Measure Transit",
        description=(
            "Reconstruct the supplier-to-store network from recorded movements and measure "
            "transit time and its spread on every lane.\n\n"
            "Rows need columns: fecha_salida (or ship_date / departure_date), fecha_llegada "
            "(or receipt_date / arrival_date), origen (or origin / from), destino (or "
            "destination / to), producto (or sku / product_id), cantidad (or quantity / qty); "
            "costo_unitario (or unit_cost) is optional and values the buffer. One row per "
            'shipment leg, e.g. {"ship_date": "2026-01-01", "receipt_date": "2026-01-04", '
            '"origin": "SUP-1", "destination": "DC-1", "sku": "SKU-A", "quantity": 120}.\n\n'
            "Useful params: min_observations (default 8 -- lanes below it report no transit "
            "statistic at all, by design), service_level (0-1, default 0.95).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_org_friction_audit",
        job_type="org_friction_audit",
        title="Audit Organizational Friction (Hidden Factory & Ratchet)",
        description=(
            "Audit a client's own control points for the political layer no data-quality "
            "tool sees: numbers that disagree across two channels, control points nobody "
            "owns, and informal workarounds nobody has classified. Every finding comes back "
            "as a ranked question for a human to go confirm - never a verdict on a person, "
            "and never an autonomous action.\n\n"
            "Rows need column: name (or kpi / checkpoint / punto_control). Optional, and "
            "each one unlocks a different detector: official_value + shadow_value (the two "
            "independent readings of the same number), owner (blank = nobody accountable), "
            "workaround_present (true/yes/si/1/x), workaround_classification (essential / "
            'hindrance). One row per KPI or checkpoint, e.g. {"name": "OTIF", '
            '"official_value": 97.0, "shadow_value": 81.5, "owner": "", '
            '"workaround_present": "si", "workaround_classification": ""}.\n\n'
            "Useful param: shadow_delta_threshold (relative official-vs-shadow gap that "
            "counts as a hidden factory).\n\n"
            "NOT available on this surface: the Goodhart/ratchet detector, which needs a "
            "second target-vs-actual history file. That is supplied to the direct tool as "
            "params['history_path'], and this remote surface drops every *_path override on "
            "purpose (see `strip_filesystem_params` in webapp/mcp_server.py) - so a call "
            "here runs three of the four detectors, never four.\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="linchpin_procedure_audit",
        job_type="procedure_audit",
        title="Audit the Procedure Register (SOP Document Control)",
        description=(
            "Audit a client's exported procedure register for overdue reviews, missing "
            "owners, an owner who is also the approver, procedures marked approved with "
            "no approver on file, procedures with no linked KPI, and unversioned "
            "documents. Always ends in a HANDOFF naming the owner and the due date. "
            "Prepares evidence for a registrar's auditor; does not certify ISO.\n\n"
            "Rows need columns: name, last_reviewed_at. Optional: owner, approver, "
            "version, approved_at, review_cadence_days, status, linked_kpi. E.g. "
            '{"name": "Receiving Inspection", "last_reviewed_at": "2025-06-01", '
            '"owner": "Warehouse Lead", "review_cadence_days": 365}.\n\n'
            'Useful params: as_of ("YYYY-MM-DD" - without it the server clock is used, '
            "and the report declares that).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="kern_iso_sop_audit",
        job_type="procedure_audit",
        title="ISO SOP Register Audit (alias of procedure_audit)",
        description=(
            "Kern alias of linchpin_procedure_audit: audit a client's exported SOP "
            "register (ISO 9001 documented-information metadata). Always HANDOFF. "
            "Prepares evidence for a registrar's auditor; Kern does not certify ISO.\n\n"
            "Rows need columns: name, last_reviewed_at. Optional: owner, approver, "
            "version, approved_at, review_cadence_days, status, linked_kpi.\n\n"
            'Useful params: as_of ("YYYY-MM-DD").\n\n' + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="kern_audit_evidence",
        job_type="audit_evidence",
        title="Audit Evidence Workpaper (MUS + GL tie-out)",
        description=(
            "Prepare a re-performable inventory/COGS workpaper: Monetary Unit Sampling, "
            "optional GL vs subledger tie-out, IPE attestation, EvidenceRecord lineage. "
            "Always HANDOFF (fieldwork + sign-off remain) or ESCALATED. Not a signed "
            "audit opinion. Reviewed-by stays blank.\n\n"
            "Rows need columns: unit_id, book_value. Optional source (gl / subledger) "
            "puts both ledgers in one table -- this surface drops *_path, so do not "
            "send a separate subledger file. Optional audited_value keys fieldwork. "
            'E.g. {"unit_id": "SKU-A", "book_value": 25000, "source": "gl"}.\n\n'
            "Required params: tolerable_misstatement (auditor judgment; Kern never "
            "derives it). Useful: risk_of_incorrect_acceptance (default 0.05), "
            "expected_misstatement (default 0), selection_seed (default 0), tolerance "
            "(GL tie-out, default 0).\n\n" + _RETURNS
        ),
    ),
    MCPToolSpec(
        name="kern_iso_scor_audit",
        job_type="scor_iso_audit",
        title="SCOR/ISO Evidence Pack (not a certificate)",
        description=(
            "Compose existing measurements into one EvidenceRecord: SCOR scorecard "
            "(Perfect Order / OTIF RL.1.1, cost-to-serve, cash-to-cash) plus an "
            "ISO 9001/28000 checklist (observed / gap / no_evaluable) and a Lineage "
            "sheet. Always HANDOFF. Kern prepares evidence for a registrar's auditor; "
            "it does not certify ISO or SCOR.\n\n"
            "Rows need a dataset column tagging each block: financial / "
            "cost_to_serve / sourcing / orders / procedures / risk. Include at least "
            "one recognised panel. E.g. {\"dataset\": \"financial\", \"product_id\": "
            "\"P1\", \"cogs\": 50000, \"avg_inventory_value\": 12000}.\n\n"
            "Useful params: as_of (YYYY-MM-DD, for OTIF), dso_days, dpo_days "
            "(cash-to-cash; without them C2C is reported no_evaluable).\n\n" + _RETURNS
        ),
    ),
)
