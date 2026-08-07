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
sound, not that the business outcome is good). :func:`verify` is the other
half: it guards what the plan is allowed to CLAIM.

**The silent zero this module used to sign.** ``reorder_point`` and
``incoming_po`` are optional columns, and an absent one used to reach
``SkuPurchaseInputs`` as ``0.0``. A reorder buffer of ``0.0`` is a buffer
every plan clears, so the service-level check passed for free and the deck
reported "3/3 coherence check(s) passed ... no cross-domain conflict detected"
off a number nobody had supplied. Present-but-empty was worse: the column
NAME existed, so the cell reached the check as ``nan`` and manufactured a
citable-looking stockout ("below its nan-unit reorder buffer"). Both shapes
are one defect -- a check that resolves BY THE ABSENCE of the number it exists
to compare against certifies a plan nobody verified.

The fix splits the work the way ``jobs/risk_job.py`` splits it for TTR/TTS.
This module -- the only layer that sees the CSV cell -- records, per SKU, WHICH
optional inputs the file actually stated (``prepare``'s ``unsupplied_inputs``);
``src.sop_engine.coherence`` gains nothing but the vocabulary to express a
check with no verdict (``UnevaluableCheck``). Every claim downstream is then
made from :class:`CoherenceTally`, which counts evaluated results and declared
absences separately, and never from ``plan.n_checks_passed``, which is the
engine's arithmetic over placeholders. A ``0.0`` the client DID state is a
policy and is judged like any other number -- refusing to judge it would be
the mirror-image dishonesty.
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
from src.sop_engine.coherence import (
    CHECK_BUDGET_FEASIBILITY,
    CHECK_PROMO_COVERAGE,
    CHECK_SERVICE_LEVEL,
    NOT_EVALUABLE,
    STATUS_FAILED,
    STATUS_PASSED,
    CoherenceResult,
    UnevaluableCheck,
)
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

# -- coherence verdict vocabulary ---------------------------------------------
# Named like jobs/cross_impact_job.py's: INDETERMINATE is a first-class outcome,
# not an error, and it outranks the clean verdict on purpose.
COHERENT = "coherent"
INCOHERENT = "incoherent"
INDETERMINATE = "indeterminate"
VERDICTS = (COHERENT, INCOHERENT, INDETERMINATE)

# Which purchase input each coherence check's PER-SKU verdict rests on. ``None``
# means the check reads no per-SKU purchase input at all (the budget check is
# portfolio-level by construction). A check kind absent from this map has no
# declared grounding and :func:`_grounding_column` raises rather than assume it
# is safe -- packs/source_to_pay/recovery.py's ``_unquantified`` rule: a code
# nobody classified must not fall through as zero.
_GROUNDING_COLUMN: dict[str, str | None] = {
    CHECK_SERVICE_LEVEL: "reorder_point",
    CHECK_PROMO_COVERAGE: "incoming_po",
    CHECK_BUDGET_FEASIBILITY: None,
}

# What one NOT-EVALUABLE check costs the deck's confidence. Deliberately local
# rather than imported, for the reason jobs/cross_impact_job.py states for its
# own: nosilo_core.guided.COVERAGE_PENALTY has no key for this gap and raises on
# an unknown one, by design.
_UNEVALUABLE_PENALTY = 0.20
_CONFIDENCE_FLOOR = 0.10
# This capability's ceiling when the purchase inputs are NOT the problem.
_DECK_BASE_CONFIDENCE = 0.75

# The arithmetic value an unstated optional input takes in SkuPurchaseInputs,
# whose fields are plain floats. Never a reading -- see :func:`_or_placeholder`.
_PLACEHOLDER = 0.0

_CITATION_KEYWORDS = (
    "sales and operations planning", "s&op", "demand supply reconciliation",
    "integrated business planning", "aggregate planning", "demand plan",
)

# These L3 citations ground the S&OP *method* (which books back integrated
# demand-supply planning), not the client's specific numbers -- so they are
# grounded on the fixed S&OP keyword set above, NOT on the free-text brief, and
# are the same canonical sources for every integrated-plan deliverable. Two
# defects (3.0-audit finding #7) drove this design, both reproduced against the
# committed graph:
#   1. RECALL. The S&OP concept cluster is fragmented across several disconnected
#      components (the code-bridged `vollmann_sop` anchor is islanded from the
#      `aggregate_planning`/`sales_and_operations_planning` cluster). Grounding a
#      pool of only the top-3 candidates could land all three in different islands
#      and every one be dropped by the (deliberately strict) 2-hop citation gate,
#      silently degrading realistic briefs to ZERO citations (6/8 in a battery).
#      Fixed by grounding a WIDER pool so on-topic candidates ranked below #3 still
#      reach the gate, then showing a tight, capped set.
#   2. PRECISION. Feeding the free-text brief into grounding (where it carries more
#      weight than the keyword set) let incidental brief tokens pass the 2-hop gate
#      -- which is itself permissive for this anchor (`aggregate_planning` reaches
#      ~19% of the graph within 2 hops via the shared book-hub node). A brief
#      saying "budget cap" surfaced an emissions "cap-and-trade" citation; an
#      EOQ-flavored brief surfaced EOQ/reorder-point over the real S&OP nodes.
#      Fixed by grounding on the keyword set only, so the citations are
#      deterministic and on-topic regardless of the client's wording.
# The gate itself (anchors, MAX_HOPS, MIN_CITATIONS) is untouched.
_CANDIDATE_POOL = 6   # candidates grounded and offered to the strict gate
_MAX_CITATIONS = 3    # kept, on-topic survivors ultimately shown (tight display)


@dataclass(frozen=True)
class CoherenceTally:
    """The coherence checks split into the three answers they can have: a pass,
    a fail, and the absence of either.

    The count that used to ship read "N/M passed" with M = every check RUN. A
    check nobody could evaluate inflated both sides of that fraction, so the
    fraction certified itself. Here M is what was EVALUATED, and the checks that
    were not are counted apart, named, and carried with the input whose absence
    cost them their verdict. The counts are derived, never stored, so no caller
    can hand this object a total that its own contents contradict.
    """

    passed: tuple[CoherenceResult, ...]
    failed: tuple[CoherenceResult, ...]
    unevaluable: tuple[UnevaluableCheck, ...]

    @property
    def n_passed(self) -> int:
        return len(self.passed)

    @property
    def n_failed(self) -> int:
        return len(self.failed)

    @property
    def n_evaluated(self) -> int:
        return len(self.passed) + len(self.failed)

    @property
    def n_unevaluable(self) -> int:
        return len(self.unevaluable)

    @property
    def n_total(self) -> int:
        return self.n_evaluated + self.n_unevaluable

    @property
    def missing_columns(self) -> tuple[str, ...]:
        """The distinct inputs whose absence cost a check its verdict, for the ask."""
        return tuple(sorted({u.column for u in self.unevaluable}))

    @property
    def headline(self) -> str:
        """The one line every summary, deck and KPI derives from, so the gap cannot
        survive in one rendering and vanish in another."""
        head = (
            "no coherence check could be evaluated" if not self.n_evaluated
            else f"{self.n_passed}/{self.n_evaluated} evaluated coherence check(s) passed"
        )
        if self.n_failed:
            head += f", {self.n_failed} FAILED"
        if self.unevaluable:
            head += (f", {self.n_unevaluable} NOT EVALUABLE "
                     f"({', '.join(self.missing_columns)} not supplied)")
        return head


def _verdict(tally: CoherenceTally) -> str:
    """First match wins. INDETERMINATE outranks INCOHERENT and COHERENT alike: a
    plan with an unmeasured check has not been found coherent OR incoherent, and
    a declared gap must never be reported as either (jobs/cross_impact_job.py's
    ``_global_verdict``)."""
    if tally.unevaluable:
        return INDETERMINATE
    if tally.failed:
        return INCOHERENT
    return COHERENT


@dataclass(frozen=True)
class IntegratedPlanBundle:
    """The full A5 v1 result: the forecast step's own report (never hidden --
    a client can see the underlying per-SKU forecasts) plus the integrated
    plan (demand/purchase/coherence) and which forecasted SKUs were excluded
    for lack of matching purchase-plan data (never a silent drop -- Golden
    Rule 14).

    ``tally``/``verdict`` are the provenance-aware reading of ``plan.checks``;
    ``unsupplied_inputs`` is the evidence they rest on, carried so :func:`verify`
    can re-derive both instead of taking their word for it (the same stored-not-
    derived split ``jobs/cross_impact_job.py`` keeps for its coordination
    verdict).
    """

    forecast_report: ForecastJobReport
    plan: IntegratedPlanReport
    dropped_no_purchase_data: tuple[str, ...]
    summary: str
    tally: CoherenceTally
    verdict: str
    # product_id -> the optional purchase inputs the stock file did NOT state.
    unsupplied_inputs: dict[str, frozenset[str]]

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"unknown coherence verdict {self.verdict!r} (expected one of {VERDICTS})")
        if not self.summary.strip():
            raise ValueError("an integrated plan must carry a legible summary")


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _cell(row: pd.Series, col: str | None) -> float | None:
    """The number the stock file actually held, or None when it held nothing.

    An absent COLUMN and an empty CELL are the same answer -- "nobody wrote this
    down" -- and both must stay distinguishable from a 0 the client did write.
    Testing the column name alone was the defect's second shape: it read ``nan``
    and fabricated a stockout out of it.
    """
    if not col or not pd.notna(row[col]):
        return None
    return float(row[col])


def _or_placeholder(value: float | None) -> float:
    """The arithmetic value ``SkuPurchaseInputs``' float field demands when nothing
    was stated -- NOT a reading. ``unsupplied_inputs``, built alongside it, is what
    every claim about this SKU is judged against."""
    return _PLACEHOLDER if value is None else value


def _prepare_sku_inputs(
    df: pd.DataFrame, params: dict
) -> tuple[dict[str, SkuPurchaseInputs], dict[str, frozenset[str]]]:
    """Sniff the stock/purchase-inputs columns and build one
    :class:`~src.sop_engine.purchase_plan.SkuPurchaseInputs` per row, PLUS -- per
    SKU -- which of the optional inputs the file did not state.

    ``product_id``/``on_hand``/``unit_cost`` are required. ``reorder_point``/
    ``incoming_po``/``minimum_order_quantity``/``order_multiple`` are optional and
    enter the engine as ``0.0`` when unstated, because the engine's fields are
    plain floats -- but that ``0.0`` is a placeholder, and the second return value
    is what keeps it out of every downstream verdict. The provenance is recorded
    HERE, at the boundary that saw the cell, rather than teaching the shared
    coherence engine about absence (``jobs/risk_job.py``'s split, verbatim).
    """
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

    optional_cols = {
        "reorder_point": _pick_column(df, params.get("reorder_col"), _REORDER_COLS),
        "incoming_po": _pick_column(df, params.get("incoming_po_col"), _INCOMING_PO_COLS),
        "minimum_order_quantity": _pick_column(df, params.get("moq_col"), _MOQ_COLS),
        "order_multiple": _pick_column(df, params.get("order_multiple_col"), _MULTIPLE_COLS),
    }

    out: dict[str, SkuPurchaseInputs] = {}
    unsupplied: dict[str, frozenset[str]] = {}
    for _, row in df.iterrows():
        pid = str(row[product]).strip()
        stated = {name: _cell(row, col) for name, col in optional_cols.items()}
        unsupplied[pid] = frozenset(name for name, value in stated.items() if value is None)
        out[pid] = SkuPurchaseInputs(
            product_id=pid,
            on_hand=float(row[on_hand]),
            unit_cost=float(row[cost]),
            reorder_point=_or_placeholder(stated["reorder_point"]),
            incoming_po=_or_placeholder(stated["incoming_po"]),
            minimum_order_quantity=_or_placeholder(stated["minimum_order_quantity"]),
            order_multiple=_or_placeholder(stated["order_multiple"]),
        )
    return out, unsupplied


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


def _grounding_column(result: CoherenceResult) -> str | None:
    """Which purchase input this check's per-SKU verdict RESTS ON, or ``None``
    when it rests on no per-SKU purchase input at all.

    Raises for a check kind nobody classified. A fourth check added to the engine
    must not default to "evaluable": that default is precisely how a verdict built
    on a filled-in number ships unnoticed, so the cost of adding a check is one
    line in ``_GROUNDING_COLUMN`` (the rule ``packs/source_to_pay/recovery.py``'s
    ``_unquantified`` already enforces for exception codes).
    """
    try:
        return _GROUNDING_COLUMN[result.check]
    except KeyError:
        raise KeyError(
            f"{result.check!r} has no declared grounding column: classify it explicitly "
            "in _GROUNDING_COLUMN (the purchase input its verdict reads, or None if it "
            "reads none) rather than letting it pass as evaluable by default"
        ) from None


def _unsupplied_grounding(
    result: CoherenceResult, unsupplied: dict[str, frozenset[str]]
) -> str | None:
    """The input this result's verdict rests on AND nobody supplied, or ``None``
    when the verdict stands on numbers that were actually stated.

    A PORTFOLIO-level result (``product_id is None``) reads no SKU's purchase
    inputs -- the promo check's "no SKU carries a demand lift, check not
    applicable" is a real, self-declared verdict about the demand plan, and the
    budget check is portfolio-level by construction -- so neither can be voided
    by a column some SKU happens to be missing.
    """
    column = _grounding_column(result)
    if column is None or result.product_id is None:
        return None
    return column if column in unsupplied.get(result.product_id, frozenset()) else None


def _absence_reason(result: CoherenceResult, column: str) -> str:
    return (
        f"{result.product_id}: the stock file states no {column}, so this check has no "
        f"number to compare against -- neither a pass nor a fail can be stated from an "
        f"unsupplied {column} (supply it, or the 0 you actually mean by it)."
    )


def _tally(
    checks: tuple[CoherenceResult, ...], unsupplied: dict[str, frozenset[str]]
) -> CoherenceTally:
    """Split the engine's results into verdicts and declared absences.

    Every result the engine produced is accounted for exactly once: a check whose
    grounding input was never supplied is not dropped, it is re-declared as the
    absence it always was, with the column named.
    """
    passed: list[CoherenceResult] = []
    failed: list[CoherenceResult] = []
    unevaluable: list[UnevaluableCheck] = []
    for c in checks:
        column = _unsupplied_grounding(c, unsupplied)
        if column is not None:
            unevaluable.append(
                UnevaluableCheck(c.check, c.product_id, column, _absence_reason(c, column))
            )
        elif c.passed:
            passed.append(c)
        else:
            failed.append(c)
    return CoherenceTally(tuple(passed), tuple(failed), tuple(unevaluable))


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
    sku_inputs, unsupplied_inputs = _prepare_sku_inputs(stock_df, params)
    unit_costs = {pid: inp.unit_cost for pid, inp in sku_inputs.items()}

    price_shifts = _build_price_shifts(params, unit_costs)
    liquidation_lines = _build_liquidation_lines(stock_df, params)

    budget = params.get("budget")
    return {
        "forecast_report": forecast_report,
        "forecast": forecast,
        "sku_inputs": sku_inputs,
        # Which optional purchase inputs the stock file did NOT state, per SKU.
        # Travels with sku_inputs because the 0.0 inside those is a placeholder
        # and this is the only thing that says so.
        "unsupplied_inputs": unsupplied_inputs,
        "price_shifts": price_shifts,
        "liquidation_lines": liquidation_lines,
        "budget": float(budget) if budget is not None else None,
    }


def run(payload: dict) -> IntegratedPlanBundle:
    """Run the sequential A5 v1 pipeline. Forecasted SKUs without a matching
    stock-file row are excluded from the purchase/coherence steps -- named
    in ``dropped_no_purchase_data``, never silently dropped.

    ``payload['unsupplied_inputs']`` is REQUIRED, with no default: a caller that
    did not build its payload with :func:`prepare` cannot tell an absent input
    from a stated zero, and defaulting to "everything was supplied" would quietly
    restore the exact defect this module was fixed for.
    """
    if "unsupplied_inputs" not in payload:
        raise KeyError(
            "payload['unsupplied_inputs'] is required: it records, per SKU, which optional "
            "purchase inputs the stock file never stated. Build the payload with prepare(); "
            "assuming everything was supplied is how a coherence verdict standing on a 0.0 "
            "nobody wrote gets signed as coherent"
        )
    forecast: dict[str, float] = payload["forecast"]
    sku_inputs: dict[str, SkuPurchaseInputs] = payload["sku_inputs"]
    unsupplied_inputs: dict[str, frozenset[str]] = payload["unsupplied_inputs"]

    common = sorted(set(forecast) & set(sku_inputs))
    dropped = tuple(sorted(set(forecast) - set(sku_inputs)))
    restricted_forecast = {pid: forecast[pid] for pid in common}

    plan = run_integrated_plan(
        restricted_forecast, sku_inputs,
        price_shifts=payload["price_shifts"], liquidation_lines=payload["liquidation_lines"],
        budget=payload["budget"],
    )
    tally = _tally(plan.checks, unsupplied_inputs)

    # NOT plan.summary: that sentence counts every check the engine RAN as a
    # verdict, including the ones it resolved against a placeholder. The summary
    # is the one line a caller is guaranteed to read, so the gap is declared in
    # it (jobs/risk_job.py:363's rule) and verify() below refuses a summary that
    # drops it.
    summary = (
        f"{payload['forecast_report'].summary} "
        f"Integrated plan over {plan.n_skus} SKU(s): {tally.headline}."
    )
    if dropped:
        shown = ", ".join(dropped[:5]) + ("..." if len(dropped) > 5 else "")
        summary += f" {len(dropped)} forecast SKU(s) excluded for lack of purchase-plan data: {shown}."

    return IntegratedPlanBundle(
        forecast_report=payload["forecast_report"], plan=plan,
        dropped_no_purchase_data=dropped, summary=summary,
        tally=tally, verdict=_verdict(tally), unsupplied_inputs=unsupplied_inputs,
    )


def verify(bundle: IntegratedPlanBundle) -> list[str]:
    """What this plan is ALLOWED TO CLAIM. Empty list = it may ship as it stands.

    Bites both ways on purpose (``jobs/fefo_job.py``'s rule): it vetoes a verdict
    counted from a number nobody supplied AND a gap declared where the number was
    supplied, so neither "call everything evaluable" nor "call everything a gap"
    is a passing implementation. The GAP itself is never the issue -- a plan
    reporting INDETERMINATE with the missing column named and its confidence
    degraded is a valid delivery. What must never ship is a coherence certificate
    resting on an input the client never sent.

    Structural soundness is ``jobs.qa.verify_integrated_plan``'s job, not this one.
    """
    issues: list[str] = []
    tally = bundle.tally

    honest = _verdict(tally)
    if bundle.verdict != honest:
        issues.append(
            f"verdict {bundle.verdict!r} contradicts the tally it claims to summarise "
            f"({tally.n_passed} passed, {tally.n_failed} failed, {tally.n_unevaluable} not "
            f"evaluable) -- the honest verdict is {honest!r}")
    if tally.unevaluable and bundle.verdict == COHERENT:
        issues.append(
            f"a clean verdict cannot be issued while {tally.n_unevaluable} coherence check(s) "
            f"are NOT EVALUABLE ({', '.join(tally.missing_columns)} not supplied)")

    # Re-derive both sides from the evidence instead of believing the tally.
    for result in tally.passed + tally.failed:
        column = _unsupplied_grounding(result, bundle.unsupplied_inputs)
        if column is not None:
            issues.append(
                f"{result.check} for {result.product_id} is counted as an evaluated verdict "
                f"but rests on a 0.0 nobody stated: the stock file supplies no {column}")
    for gap in tally.unevaluable:
        if gap.column not in bundle.unsupplied_inputs.get(gap.product_id, frozenset()):
            issues.append(
                f"fabricated gap: {gap.check} for {gap.product_id} is declared NOT EVALUABLE "
                f"on {gap.column}, which the stock file did supply -- refusing to judge a "
                f"number the client stated is the mirror image of judging one they did not")

    if tally.unevaluable and tally.headline not in bundle.summary:
        issues.append(
            "the summary reports the plan without disclosing that "
            f"{tally.n_unevaluable} check(s) are NOT EVALUABLE -- the text must not read "
            "clean when the basis is missing")

    return issues


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
            "constraint_extra_units": round(line.constraint_extra_units, 2),
            "constraint_cost_delta": round(line.constraint_cost_delta, 2),
            "constraint_extra_cover_periods": round(line.constraint_extra_cover_periods, 2),
            "constraint_verdict": line.constraint_verdict,
            "reorder_buffer": round(line.reorder_buffer, 2),
            "projected_position": round(line.projected_position, 2),
            "order_value": round(line.order_value, 2),
        }
        for line in plan.purchase_plan
    ]
    # A check with no verdict gets an EMPTY passed cell and says why in its message
    # (jobs/cross_impact_job.write's rule): a spreadsheet reader scanning the column
    # must see a blank, because True and False are both claims here and "False" would
    # read as a measured incoherence nobody measured.
    gaps = {(u.check, u.product_id): u for u in bundle.tally.unevaluable}
    check_rows = [
        {
            "check": c.check,
            "passed": "" if (c.check, c.product_id) in gaps else c.passed,
            "status": (
                NOT_EVALUABLE if (c.check, c.product_id) in gaps
                else (STATUS_PASSED if c.passed else STATUS_FAILED)
            ),
            "product_id": c.product_id or "",
            "message": (
                gaps[(c.check, c.product_id)].reason if (c.check, c.product_id) in gaps
                else c.message
            ),
        }
        for c in plan.checks
    ]
    return {
        "demand_plan": write_summary_csv(demand_rows, d / "demand_plan.csv"),
        "purchase_plan": write_summary_csv(purchase_rows, d / "purchase_plan.csv"),
        "coherence_checks": write_summary_csv(check_rows, d / "coherence_checks.csv"),
    }


def _degrade(base: float, tally: CoherenceTally) -> float:
    """Cap a caller-supplied confidence by the coherence coverage it does not have.

    The caller's optimism does not survive contact with a missing input: a plan with
    an unmeasured check cannot claim the certainty of one whose checks were all
    measured, however sure the caller was (jobs/intake_scan_job.py's rule for its own
    registry coverage, jobs/risk_job.py's for its resilience test).
    """
    return max(_CONFIDENCE_FLOOR, base - _UNEVALUABLE_PENALTY * tally.n_unevaluable)


def build_deck(
    bundle: IntegratedPlanBundle,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
    lang: str = "en",
    branding: Branding | None = None,
) -> Deliverable:
    """Compose ONE integrated narrative across forecast -> demand shaping ->
    purchase plan -> coherence checks (Golden Rule 13's E4/E6 hooks; never
    four tool reports stapled together).

    Every coherence claim comes from ``bundle.tally``, never from
    ``plan.n_checks_passed``/``n_checks``: those are the engine's arithmetic over
    inputs it had to treat as real, so on a stock file with no reorder_point they
    said 3/3 -- the most favourable statement this tool can make, issued from
    nothing. ``confidence`` is DERIVED when not supplied, and treated as a ceiling
    when it is.
    """
    plan = bundle.plan
    tally = bundle.tally
    shaped = [line for line in plan.demand_plan if line.source != NO_SHIFT_SOURCE]

    summary = (
        f"Integrated plan over {plan.n_skus} SKU(s), sequencing the latest forecast, any price-cut/"
        f"liquidation demand shaping, and the resulting purchase plan into one view: "
        f"{tally.headline}"
        + (" -- see Key Findings" if tally.failed or tally.unevaluable else "")
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

    for c in tally.failed[:5]:
        findings.append(Finding(f"Coherence check FAILED: {c.check}", c.message,
                                 impact="needs a human decision before this plan executes"))
    # The gap, its motive, and the ask -- one finding per check that lost its
    # verdict, never folded into the pass/fail count above.
    for gap in tally.unevaluable[:5]:
        findings.append(Finding(
            f"Coherence check NOT EVALUABLE: {gap.check}",
            gap.reason,
            impact=f"supply {gap.column} for {gap.product_id} and re-run -- until then this "
                   f"plan carries no {gap.check} verdict for it, in either direction",
        ))
    if not tally.failed and not tally.unevaluable:
        findings.append(Finding("Coherence checks (step 4)", f"{tally.headline}.",
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
        Kpi("Coherence checks passed", f"{tally.n_passed}/{tally.n_evaluated}",
            target=f"{tally.n_total}/{tally.n_total}",
            rationale="Cross-domain checks between the demand plan and the purchase plan, "
                      "counted over the ones that could be EVALUATED"),
        Kpi("Coherence checks NOT EVALUABLE", f"{tally.n_unevaluable}", target="0",
            rationale="Checks whose grounding purchase input the stock file never supplied: "
                      "no verdict was issued for them, in either direction"),
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
    if tally.unevaluable:
        recommendations = (
            f"Supply {', '.join(tally.missing_columns)} in the stock file and re-run: "
            f"{tally.n_unevaluable} coherence check(s) have no verdict without it, and this plan "
            "cannot be called coherent or incoherent until they do.",
        ) + recommendations

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
        confidence=_degrade(_DECK_BASE_CONFIDENCE if confidence is None else confidence, tally),
        residual=residual,
        prepared=prepared,
        lang=lang,
        branding=branding if branding is not None else DEFAULT_BRANDING,
    )


def gated_citations(
    brief: str = "",
    *,
    kb: KnowledgeBase | None = None,
    limit: int = _CANDIDATE_POOL,
    max_shown: int = _MAX_CITATIONS,
) -> tuple[str, ...]:
    """The E5-gated L3 citations for this playbook (golden rule 7 + the
    citation gate) -- reuses the "sop" ``TOOL_CONCEPTS`` anchors verbatim
    (no parallel citation mechanism, same pattern
    ``jobs/repricing.py``/``jobs/price_intelligence.py`` already established).

    These citations ground the S&OP *method*, so grounding runs on the fixed
    ``_CITATION_KEYWORDS`` set only -- ``brief`` is deliberately NOT fed into the
    query (see the module comment on ``_CANDIDATE_POOL``): the client's free-text
    wording carries more grounding weight than the keyword set and let incidental
    tokens surface off-topic citations past the permissive 2-hop gate. ``limit``
    grounds a wider candidate pool than we display so the strict gate can reach
    past the S&OP cluster's graph fragmentation; ``filter_citations`` preserves
    grounding rank, so ``[:max_shown]`` keeps the top on-topic survivors as a
    tight set. ``brief`` is retained in the signature for call-site stability."""
    kb = kb or KnowledgeBase()
    candidates = kb.ground_citations_detailed(_CITATION_KEYWORDS, "", limit=limit)
    return filter_citations(kb, TOOL_KEY, candidates).kept[:max_shown]


def write_deliverable(
    bundle: IntegratedPlanBundle,
    *,
    out_dir: str | Path,
    client: str = "Client",
    brief: str = "",
    prepared: str = "",
    lang: str = i18n.DEFAULT_LANG,
    branding: Branding | None = None,
    confidence: float | None = None,
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
