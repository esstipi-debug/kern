"""The OPTIONAL inputs of A5's playbook: everything a caller may hand it beyond
the two CSVs it cannot run without, and the rule each one degrades by when it is
absent, unreadable, or turned off.

Split out of ``jobs/integrated_plan.py``, which composes the pipeline and had
grown past the repo's 800-line file ceiling. The cut is along the axis, not along
an arbitrary line count: the three functions here are the whole of what
``prepare`` reads from ``params`` rather than from the required stock file --
``price_history_path`` (P2 demand shaping), ``include_liquidation`` (P4 demand
shaping), and ``as_of`` (the date the cash calendar is anchored to).
``src/sop_engine/`` is split by axis the same way.

What makes them ONE axis is the contract they share, not the shape of their
return values: an optional input must never be able to kill a plan that would
otherwise succeed, and must never be silently invented either. P2 and P4 degrade
to "no demand shaping" and say so through the empty result the deck reports on;
``_as_of`` refuses to guess, because the one fallback available to it
(``date.today()``) would move every payment date on every re-run.

The stock file's own column sniffing deliberately stayed behind in
``jobs/integrated_plan.py``: those columns are the REQUIRED intake, they carry the
supplier payment condition, and they are the layer that records which optional
purchase inputs went unstated -- a different question from this module's.

Every name here travels UNCHANGED from the file it came out of, underscore
prefixes included, so the move can be checked by diffing bodies instead of being
re-read as a rewrite. ``jobs/intake.py``'s ``_norm``, imported by two other jobs,
is the same shape.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from jobs import markdown_liquidation_job as mlq
from jobs.pricing import prepare_pricing
from src.elasticity_batch import estimate_portfolio_elasticities
from src.liquidation import LiquidationLine
from src.price_optimizer import PriceOptimizationResult, optimize_portfolio_prices


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


def _as_of(value: object) -> date | None:
    """The date the cash calendar is anchored to, or ``None`` for a calendar in
    weeks relative to the order date.

    Never ``date.today()``: a deliverable whose payment dates move every time it
    is re-run cannot be reconciled against the one the client already has.
    Raises on an unreadable value rather than falling back to a relative
    calendar, which would silently drop the dates the caller asked for.

    ``datetime`` (and ``pd.Timestamp``, which subclasses it) is NARROWED to its
    date before the check for ``date``, because both are ``date`` subclasses and
    an ``isinstance(value, date)`` test alone lets the clock through: the week
    axis is built by adding whole weeks and calling ``isoformat()``
    (``cash_plan._week_start``), so a stray ``00:00:00`` turns the CSV's
    ``week_start`` column into ``2026-09-07T00:00:00`` for one caller and
    ``2026-09-07`` for the next -- the same date in two shapes, in a column a
    client reconciles on. ``src.liquidation_calendar`` narrows the same way.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
