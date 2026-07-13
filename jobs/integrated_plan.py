"""A5 integrated planning v1 playbook (Linchpin 3.0 PR-20, plan section 5 A5
``balance``): a demand-history CSV + a stock/purchase-inputs CSV (+ optional
price history) -> ONE integrated deliverable that sequences the forecast,
demand shaping, and purchase plan into a single narrative with citable
cross-domain coherence checks.

**Scope boundary vs. the existing "sop" tool (READ BEFORE TOUCHING EITHER
MODULE).** ``src/sop.py`` + ``jobs/sop_job.py`` already answer a DIFFERENT
question: given ONE demand horizon, which of chase/level/hybrid aggregate
PRODUCTION strategies is cheapest (Chopra & Meindl's classic aggregate-
planning trade-off). This module answers a DIFFERENT question again: given
the LATEST forecast, any price-cut-implied demand shift, and the existing
purchase/inventory-constraint machinery, does the resulting plan actually
COHERE across those domains (does procurement have a PO to cover a planned
markdown's demand lift, does the budget fit, does inventory clear the
reorder point)? Neither module imports the other; ``src/sop.py`` and
``jobs/sop_job.py`` are UNTOUCHED by this PR and are not called from here --
the existing "sop" tool's aggregate-planning question is a different, later
step an operator could layer on top of THIS module's purchase plan, not an
input this v1 pipeline needs. (See ``src/sop_engine/__init__.py``'s module
docstring for the same boundary stated from the engine side.)

**Not registered as an agent Tool (see ``scm_agent/tools.py``).** Every
other registered Tool takes ONE ``request.data_path`` CSV; this playbook's
natural shape needs at minimum TWO files (a demand-history CSV for the
forecast step and a stock/purchase-inputs CSV for the purchase step), plus
two more OPTIONAL ones (price history for P2, and the same stock CSV doubles
as P4's liquidation input when it carries ``daily_demand`` too) -- that
multi-file shape does not fit the registry's single-``data_path`` contract
without either forcing an awkward one-CSV-with-everything schema or a
speculative multi-file convention no other tool needs yet (YAGNI). This
mirrors how ``jobs/package_deliverable.py`` composes several tools' own
outputs into one deck without itself being a registered Tool -- except this
module composes ``jobs/`` FUNCTIONS directly (forecast_job, elasticity_batch,
price_optimizer, markdown_liquidation_job, ``src.sop_engine``) rather than
running other tools through the ``Orchestrator``/registry, matching how
``jobs/markdown_liquidation_job.py`` itself already composes
``jobs.excess_obsolete_job`` and ``jobs.pricing`` directly. The operator
entry point is a CLI (``examples/run_integrated_plan.py``), the same shape
``examples/run_price_intel.py`` already established for a job with its own
multi-file intake and its own ``write_deliverable``.

**Pipeline (strictly sequential -- see ``src.sop_engine.engine`` for why):**

1. Forecast: ``jobs.forecast_job.prepare`` + ``run`` over the demand-history
   CSV -- v1's "reconciliation" is exactly reading this latest run (plan
   section 5's own scoping; no hierarchical reconciliation machinery).
2. Demand shaping (P2): if ``params['price_history_path']`` is given, batch-
   fits elasticity (``src.elasticity_batch``) and optimizes prices
   (``src.price_optimizer``) -- PR-16's P2, reused directly (no dedicated P2
   job exists yet in this repo; this is its first jobs/-level consumer for
   demand shaping specifically, alongside the existing
   ``jobs.repricing``/``jobs.markdown_liquidation_job`` consumers).
3. Demand shaping (P4): the SAME stock CSV, when it also carries
   ``daily_demand`` (E&O's own required column), is run through
   ``jobs.markdown_liquidation_job`` to surface any liquidation-implied
   demand lift -- best-effort: gracefully skipped (never a hard failure) when
   the stock CSV lacks E&O's required columns, via ``params['include_
   liquidation'] = False`` or missing ``daily_demand``.
4. Purchase plan (step 3): ``src.sop_engine.purchase_plan`` against the
   stock CSV's on_hand/unit_cost/reorder_point/incoming_po/MOQ/order_multiple.
5. Coherence checks (step 4): ``src.sop_engine.coherence`` -- the >=3 citable
   checks.
6. ONE deliverable (:func:`write_deliverable`): E4 (lang)/E5 (citation
   gate)/E6 (branding), per Golden Rule 13 -- not four tool reports stapled
   together.

QA (``jobs.qa.verify_integrated_plan``/``integrated_plan_passed``) checks
structural soundness (finite numbers, all 3 required check kinds present,
internally-consistent counts) -- a FAILED coherence check is an intended
FINDING this deliverable reports to a human, not a QA failure that blocks
the deliverable (mirrors every other job's QA gate: it guards the DATA is
sound, not that the business outcome is good).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs import forecast_job
from jobs import markdown_liquidation_job as mlq
from jobs.forecast_job import ForecastJobReport
from jobs.pricing import prepare_pricing
from scm_agent.citation_gate import filter_citations
from scm_agent.knowledge import KnowledgeBase
from src import i18n
from src.deliverable import DEFAULT_BRANDING, Branding, DataSource, Deliverable, Finding, Kpi
from src.elasticity_batch import estimate_portfolio_elasticities
from src.export import write_summary_csv
from src.liquidation import LiquidationLine
from src.price_optimizer import PriceOptimizationResult, optimize_portfolio_prices
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE
from src.sop_engine.engine import IntegratedPlanReport, run_integrated_plan
from src.sop_engine.purchase_plan import SkuPurchaseInputs

TOOL_KEY = "sop"  # A5 is S&OP-flavored demand/supply reconciliation; reuses the already-verified
# "sop" anchor concepts (vollmann_sop, aggregate_planning) in scm_agent/citation_gate.py's
# TOOL_CONCEPTS map -- no new entry needed for this playbook.

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ONHAND_COLS = ("on_hand", "quantity", "qty", "stock", "units", "On Hand")
_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")
_REORDER_COLS = ("reorder_point", "rop", "reorder_pt", "reorder_level")
_INCOMING_PO_COLS = ("incoming_po", "po_incoming", "on_order", "in_transit", "open_po")
_MOQ_COLS = ("minimum_order_quantity", "moq", "min_order_qty")
_MULTIPLE_COLS = ("order_multiple", "case_pack", "pack_size")

_CITATION_KEYWORDS = (
    "sales and operations planning", "s&op", "demand supply reconciliation",
    "integrated business planning", "aggregate planning", "demand plan",
)


@dataclass(frozen=True)
class IntegratedPlanBundle:
    """The full A5 v1 result: the forecast step's own report (never hidden --
    a client can see the underlying per-SKU forecasts) plus the integrated
    plan (demand/purchase/coherence) and which forecasted SKUs were excluded
    for lack of matching purchase-plan data (never a silent drop -- Golden
    Rule 14)."""

    forecast_report: ForecastJobReport
    plan: IntegratedPlanReport
    dropped_no_purchase_data: tuple[str, ...]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _prepare_sku_inputs(df: pd.DataFrame, params: dict) -> dict[str, SkuPurchaseInputs]:
    """Sniff the stock/purchase-inputs columns and build one
    :class:`~src.sop_engine.purchase_plan.SkuPurchaseInputs` per row.
    ``product_id``/``on_hand``/``unit_cost`` are required; ``reorder_point``/
    ``incoming_po``/``minimum_order_quantity``/``order_multiple`` default to
    ``0.0`` when the column is absent (documented, not silently invented --
    a caller who wants a real reorder buffer or a real committed PO must
    supply it)."""
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    on_hand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    missing = [n for n, c in (("product_id", product), ("on_hand", on_hand), ("unit_cost", cost)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)} for the purchase-plan inputs; pass them in params "
            f"(columns seen: {cols})"
        )

    reorder = _pick_column(df, params.get("reorder_col"), _REORDER_COLS)
    incoming = _pick_column(df, params.get("incoming_po_col"), _INCOMING_PO_COLS)
    moq = _pick_column(df, params.get("moq_col"), _MOQ_COLS)
    multiple = _pick_column(df, params.get("order_multiple_col"), _MULTIPLE_COLS)

    out: dict[str, SkuPurchaseInputs] = {}
    for _, row in df.iterrows():
        pid = str(row[product]).strip()
        out[pid] = SkuPurchaseInputs(
            product_id=pid,
            on_hand=float(row[on_hand]),
            unit_cost=float(row[cost]),
            reorder_point=float(row[reorder]) if reorder else 0.0,
            incoming_po=float(row[incoming]) if incoming else 0.0,
            minimum_order_quantity=float(row[moq]) if moq else 0.0,
            order_multiple=float(row[multiple]) if multiple else 0.0,
        )
    return out


def _build_price_shifts(params: dict, unit_costs: dict[str, float]) -> dict[str, PriceOptimizationResult]:
    """PR-16's P2 (batch elasticity + price optimizer), reused directly for
    demand shaping -- ``None``/empty when no ``price_history_path`` is given
    or it can't be turned into usable history (an OPTIONAL enhancement, same
    graceful-degradation contract ``markdown_liquidation_job._load_price_
    history`` already established: a broken optional file must not abort a
    plan that would otherwise succeed)."""
    path = params.get("price_history_path")
    if not path:
        return {}
    try:
        demand = prepare_pricing(path, period=str(params.get("price_period", "W")))
    except (ValueError, FileNotFoundError):
        return {}
    fits = estimate_portfolio_elasticities(demand)
    current_prices = {
        str(pid): float(grp["price"].median()) for pid, grp in demand.groupby("product_id")
    }
    landed_costs = {pid: cost for pid, cost in unit_costs.items() if pid in fits}
    if not landed_costs:
        return {}
    return optimize_portfolio_prices(
        fits, landed_costs=landed_costs, current_prices=current_prices,
        min_margin_pct=float(params.get("min_margin_pct", 0.0)),
        price_increment=float(params.get("price_increment", 0.0)),
    )


def _build_liquidation_lines(stock_df: pd.DataFrame, params: dict) -> dict[str, LiquidationLine]:
    """PR-19's P4 liquidation report, reused directly for demand shaping --
    best-effort against the SAME stock CSV: skipped (empty dict, never a
    hard failure) when ``params['include_liquidation']`` is explicitly
    ``False`` or the stock CSV lacks E&O's own required columns (product_id/
    on_hand/daily_demand)."""
    if params.get("include_liquidation", True) is False:
        return {}
    try:
        payload = mlq.prepare_records(stock_df, params)
    except ValueError:
        return {}
    report = mlq.run(payload)
    return {line.product_id: line for line in report.lines}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the demand-history CSV (forecast, step 1) and the required
    ``params['stock_path']`` CSV (purchase inputs, step 3 -- optionally also
    P4's liquidation input, step 2), plus the optional
    ``params['price_history_path']`` (P2, step 2)."""
    params = params or {}
    stock_path = params.get("stock_path")
    if not stock_path:
        raise ValueError(
            "params['stock_path'] is required: a CSV with product_id, on_hand, unit_cost "
            "[, reorder_point, incoming_po, minimum_order_quantity, order_multiple, daily_demand]"
        )

    series_by_name = forecast_job.prepare(data_path, params)
    forecast_report = forecast_job.run(
        series_by_name,
        holdout_fraction=params.get("holdout_fraction", 0.25),
        min_backtest_periods=params.get("min_backtest_periods", 4),
    )
    forecast = {s.name: s.forecast for s in forecast_report.skus}

    stock_df = pd.read_csv(stock_path)
    sku_inputs = _prepare_sku_inputs(stock_df, params)
    unit_costs = {pid: inp.unit_cost for pid, inp in sku_inputs.items()}

    price_shifts = _build_price_shifts(params, unit_costs)
    liquidation_lines = _build_liquidation_lines(stock_df, params)

    budget = params.get("budget")
    return {
        "forecast_report": forecast_report,
        "forecast": forecast,
        "sku_inputs": sku_inputs,
        "price_shifts": price_shifts,
        "liquidation_lines": liquidation_lines,
        "budget": float(budget) if budget is not None else None,
    }


def run(payload: dict) -> IntegratedPlanBundle:
    """Run the sequential A5 v1 pipeline. Forecasted SKUs without a matching
    stock-file row are excluded from the purchase/coherence steps -- named
    in ``dropped_no_purchase_data``, never silently dropped."""
    forecast: dict[str, float] = payload["forecast"]
    sku_inputs: dict[str, SkuPurchaseInputs] = payload["sku_inputs"]

    common = sorted(set(forecast) & set(sku_inputs))
    dropped = tuple(sorted(set(forecast) - set(sku_inputs)))
    restricted_forecast = {pid: forecast[pid] for pid in common}

    plan = run_integrated_plan(
        restricted_forecast, sku_inputs,
        price_shifts=payload["price_shifts"], liquidation_lines=payload["liquidation_lines"],
        budget=payload["budget"],
    )

    summary = f"{payload['forecast_report'].summary} {plan.summary}"
    if dropped:
        shown = ", ".join(dropped[:5]) + ("..." if len(dropped) > 5 else "")
        summary += f" {len(dropped)} forecast SKU(s) excluded for lack of purchase-plan data: {shown}."

    return IntegratedPlanBundle(
        forecast_report=payload["forecast_report"], plan=plan,
        dropped_no_purchase_data=dropped, summary=summary,
    )


def write_operational(bundle: IntegratedPlanBundle, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one CSV per pipeline step (demand
    plan, purchase plan, coherence checks) -- distinct schemas, so distinct
    files, all under the SAME output directory (never four separate tool
    reports; see the module docstring for what "one deliverable" means)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    plan = bundle.plan

    demand_rows = [
        {
            "product_id": line.product_id,
            "base_forecast": round(line.base_forecast, 2),
            "demand_shift_pct": ("inf" if math.isinf(line.demand_shift_pct) else round(line.demand_shift_pct, 1)),
            "shaped_demand": round(line.shaped_demand, 2),
            "source": line.source,
            "reason": line.reason,
        }
        for line in plan.demand_plan
    ]
    purchase_rows = [
        {
            "product_id": line.product_id,
            "shaped_demand": round(line.shaped_demand, 2),
            "on_hand": round(line.on_hand, 2),
            "incoming_po": round(line.incoming_po, 2),
            "recommended_order": round(line.recommended_order, 2),
            "reorder_buffer": round(line.reorder_buffer, 2),
            "projected_position": round(line.projected_position, 2),
            "order_value": round(line.order_value, 2),
        }
        for line in plan.purchase_plan
    ]
    check_rows = [
        {"check": c.check, "passed": c.passed, "product_id": c.product_id or "", "message": c.message}
        for c in plan.checks
    ]
    return {
        "demand_plan": write_summary_csv(demand_rows, d / "demand_plan.csv"),
        "purchase_plan": write_summary_csv(purchase_rows, d / "purchase_plan.csv"),
        "coherence_checks": write_summary_csv(check_rows, d / "coherence_checks.csv"),
    }


def build_deck(
    bundle: IntegratedPlanBundle,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.75,
    lang: str = "en",
    branding: Branding | None = None,
) -> Deliverable:
    """Compose ONE integrated narrative across forecast -> demand shaping ->
    purchase plan -> coherence checks (Golden Rule 13's E4/E6 hooks; never
    four tool reports stapled together)."""
    plan = bundle.plan
    failed = [c for c in plan.checks if not c.passed]
    shaped = [line for line in plan.demand_plan if line.source != NO_SHIFT_SOURCE]

    summary = (
        f"Integrated plan over {plan.n_skus} SKU(s), sequencing the latest forecast, any price-cut/"
        f"liquidation demand shaping, and the resulting purchase plan into one view: "
        f"{plan.n_checks_passed}/{plan.n_checks} coherence check(s) passed"
        + (f", {plan.n_checks_failed} FAILED -- see Key Findings" if plan.n_checks_failed else "")
        + "."
    )

    findings = [
        Finding(
            "Forecast (step 1)",
            bundle.forecast_report.summary,
            impact="the reconciled forecast this plan's purchase quantities are sized against",
        ),
    ]
    if shaped:
        names = ", ".join(f"{line.product_id} ({line.source}, {line.demand_shift_pct:+.0f}%)" for line in shaped[:5])
        findings.append(Finding(
            "SKUs with a demand-shaping signal (step 2)",
            f"{len(shaped)} SKU(s) carry a price-cut/markdown-implied demand shift: {names}"
            + ("..." if len(shaped) > 5 else ""),
            impact="these SKUs' purchase quantities (step 3) are sized off the SHAPED demand, not the raw forecast",
        ))
    else:
        findings.append(Finding(
            "SKUs with a demand-shaping signal (step 2)",
            "none this cycle -- every SKU's purchase plan is sized off its raw forecast.",
            impact="supply a price_history_path (P2) or a liquidation-shaped stock file (P4) to activate this step",
        ))

    for c in failed[:5]:
        findings.append(Finding(f"Coherence check FAILED: {c.check}", c.message,
                                 impact="needs a human decision before this plan executes"))
    if not failed:
        findings.append(Finding("Coherence checks (step 4)", f"all {plan.n_checks} check(s) passed.",
                                 impact="no cross-domain conflict detected this cycle"))

    if bundle.dropped_no_purchase_data:
        shown = ", ".join(bundle.dropped_no_purchase_data[:10])
        findings.append(Finding(
            "SKUs excluded for lack of purchase data",
            f"{len(bundle.dropped_no_purchase_data)} forecasted SKU(s) have no matching stock-file row "
            f"and were excluded from the purchase/coherence steps: {shown}"
            + ("..." if len(bundle.dropped_no_purchase_data) > 10 else ""),
            impact="never silently dropped -- supply the missing SKUs' on_hand/unit_cost to include them",
        ))

    total_order_value = sum(line.order_value for line in plan.purchase_plan)
    kpis = [
        Kpi("SKUs planned", f"{plan.n_skus}", rationale="Forecasted SKUs with matching purchase-plan data"),
        Kpi("Coherence checks passed", f"{plan.n_checks_passed}/{plan.n_checks}", target=f"{plan.n_checks}/{plan.n_checks}",
            rationale="Cross-domain checks between the demand plan and the purchase plan"),
        Kpi("SKUs demand-shaped", f"{len(shaped)}", rationale="Carry a price-cut/markdown-implied demand shift"),
        Kpi("Recommended purchase value", f"{total_order_value:,.0f}",
            rationale="Sum of recommended_order * unit_cost across the portfolio"),
    ]
    if plan.budget is not None:
        kpis.append(Kpi("Budget cap", f"{plan.budget:,.0f}", target="fit", rationale="Portfolio inventory-investment cap"))

    data_sources = (
        DataSource("Demand history (sku/period/quantity)", "ERP / sales history", "weekly"),
        DataSource("On-hand, unit cost, reorder point, incoming PO, MOQ/case pack", "WMS / procurement", "weekly"),
        DataSource("Price/quantity history (optional, P2 demand shaping)", "POS / order history", "weekly"),
        DataSource("Liquidation/E&O stock (optional, P4 demand shaping)", "WMS / E&O", "weekly"),
    )

    recommendations = (
        "Resolve every FAILED coherence check before executing this plan -- each cites the exact SKU "
        "and shortfall.",
        "Place the recommended_order quantities for SKUs whose promo-coverage check failed first -- "
        "they carry the nearest-term stockout risk.",
        "Re-run after the next forecast/price/liquidation cycle -- this is a single-pass v1 pipeline, "
        "not a continuously reconciled plan.",
    )

    residual = (
        "This is A5 v1: a strictly SEQUENTIAL pipeline (forecast -> demand shaping -> purchase plan -> "
        "coherence checks), not a joint/global optimization -- no simultaneous multi-step solve "
        "reconciles the steps against each other beyond the citable checks above (plan section 5's own "
        "anti-pattern warning: the joint solver is v2, gated on evidence this v1 pipeline does not have "
        "yet). A FAILED coherence check is a finding for a human to resolve (adjust the purchase plan, "
        "the promo timing, or the budget), never an auto-corrected plan. Executing any purchase order, "
        "price change, or liquidation step stays a separate, human-approved action in its own tool -- "
        "this plan does not write back to any system."
    )

    return Deliverable(
        title="Integrated Plan (Forecast -> Demand -> Purchase, S&OP-style)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared,
        lang=lang,
        branding=branding if branding is not None else DEFAULT_BRANDING,
    )


def gated_citations(brief: str = "", *, kb: KnowledgeBase | None = None, limit: int = 3) -> tuple[str, ...]:
    """The E5-gated L3 citations for this playbook (golden rule 7 + the
    citation gate) -- reuses the "sop" ``TOOL_CONCEPTS`` anchors verbatim
    (no parallel citation mechanism, same pattern
    ``jobs/repricing.py``/``jobs/price_intelligence.py`` already established)."""
    kb = kb or KnowledgeBase()
    candidates = kb.ground_citations_detailed(_CITATION_KEYWORDS, brief, limit=limit)
    return filter_citations(kb, TOOL_KEY, candidates).kept


def write_deliverable(
    bundle: IntegratedPlanBundle,
    *,
    out_dir: str | Path,
    client: str = "Client",
    brief: str = "",
    prepared: str = "",
    lang: str = i18n.DEFAULT_LANG,
    branding: Branding | None = None,
    confidence: float = 0.75,
    kb: KnowledgeBase | None = None,
) -> dict[str, Path]:
    """The full deliverable: 3 operational CSVs + ONE integrated report.md
    + workbook.xlsx (via ``Deliverable.write_all``) -- always E5-gates its
    citations. The standalone entry point used by
    ``examples/run_integrated_plan.py`` and this module's own tests."""
    citations = gated_citations(brief, kb=kb)
    deliverable = build_deck(
        bundle, client=client, prepared=prepared, citations=citations,
        confidence=confidence, lang=lang, branding=branding,
    )
    written = write_operational(bundle, out_dir, client)
    written.update(deliverable.write_all(out_dir))
    return written
