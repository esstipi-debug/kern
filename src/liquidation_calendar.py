"""Liquidation calendar (Linchpin 3.0 PR-19, plan section 7 P4 ``promo_liquidation``
v2) -- turns ``src.liquidation.plan_liquidation``'s per-SKU disposition PLAN into a
per-SKU disposition SCHEDULE, gated by two things that must be true before a
markdown step is allowed to ship: the EU/UK Omnibus discount-reference check
(``src.pricing_guardrails``, PR-17) and a competitive-floor check against
pricing_intel's confirmed competitor prices (PR-10/PR-14, Fase B).

This module extends ``src/liquidation.py`` and ``jobs/markdown_liquidation_job.py``
(both already shipped -- PR #124, before the 3.0 plan even started); it does NOT
touch either module's own per-SKU disposition math (``plan_liquidation``'s choice
of elasticity/default-markdown/salvage and its price/weeks-to-clear numbers are
read here verbatim, never recomputed). Three additive layers on top of the
existing ``LiquidationReport``:

1. **Calendar.** :func:`build_liquidation_calendar` sequences the report's lines
   (already ranked cash-at-risk descending) into launch weeks (``steps_per_week``
   SKUs start per week, in that same rank order -- highest exposure first) and
   projects a per-week cash-recovery curve from each step's own
   ``recovered_value``/``weeks_to_clear`` (reusing those fields verbatim, per plan
   section 7's own instruction: "reuse LiquidationReport's existing
   recovered_value/at_risk_value fields"). A step whose ``weeks_to_clear`` is
   infinite (salvage/write-down -- ``src.liquidation.SALVAGE``) recovers as a
   lump sum in its launch week rather than being divided by infinity (which would
   silently never show up in a finite calendar -- Golden Rule 14).

2. **Competitive floor (PR-10/PR-14 -> this module).** Following the same
   discipline ``src/price_optimizer.py``'s ``CompetitorPriceContext`` already
   established for P2 ("this module never reads the ledger itself... callers
   resolve this from an already-fetched pricing_intel ledger record"), this is a
   PURE function: it takes an already-resolved ``competitor_contexts`` mapping
   (``product_id -> CompetitorPriceContext``, the SAME dataclass P2 uses -- no
   second competitor-context type is built here) rather than importing
   ``PriceLedger``/``SkuMap`` and doing SQLite I/O itself. The I/O side --
   ``SkuMap.latest_confirmed_for_product`` + ``PriceLedger.latest_for_product`` --
   lives in ``jobs/markdown_liquidation_job.py::resolve_competitor_contexts``
   (jobs/ is where this repo's data-prep I/O belongs, matching
   ``src/`` = pure functions only). A clearance price at/above the confirmed
   competitor's floor is auto-confirmed; a price BELOW it is never silently
   shipped -- it is flagged for a human decision, and proceeds only if the
   caller supplies an explicit ``undercut_reasons[product_id]`` string (e.g.
   "matching competitor floor" vs "clearing dead stock regardless" are
   different, auditable decisions -- plan section 7's own framing).

3. **Omnibus gate (PR-17 -> this module).** "Every liquidation markdown IS a
   discount announcement" (plan section 7): for every priced line (elasticity or
   default-markdown -- salvage lines carry no price, so no discount is being
   announced and this check does not apply to them), this module computes the
   REFERENCE price the calendar would cite as the "before" price -- the median
   of the SAME ``price_history`` positive prices ``plan_liquidation`` itself used
   to pick that SKU's clearance price (a tiny local re-derivation of
   ``src.liquidation``'s private ``_current_price`` median, not an import of it --
   this module never reaches into ``liquidation.py``'s internals) -- and runs the
   resulting stated discount percentage through
   ``src.pricing_guardrails.validate_discount_reference`` (which is itself
   ``prior_price_30d_lowest()``-based: EU/UK compare that stated percentage
   against the OFFICIALLY tracked ``src.state`` 30-day-lowest own price, not
   against this module's local reference -- exactly the drift the Aldi Sud gate
   exists to catch). No parallel compliance gate is built here; the exact same
   ``src.pricing_guardrails`` primitive PR-17 built for P3 repricing is reused
   verbatim. A step that fails -- HARD-gate block, SOFT-gate warning, or simply
   "no price history was supplied so the reference can't be computed at all" --
   is EXCLUDED from the shippable calendar and reported separately (never
   silently dropped -- ``LiquidationCalendar.total_recovered_excluded`` and
   ``total_at_risk_excluded`` keep that cash visible, and every excluded
   ``CalendarStep`` carries its own ``exclusion_reason``).

Pure/deterministic except for the SAME read-only ``src.state`` carve-out
``src.pricing_guardrails`` itself already uses (no network I/O, no writeback).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median

from src.liquidation import LiquidationLine, LiquidationReport, PriceHistory
from src.price_optimizer import CompetitorPriceContext
from src.pricing_guardrails import (
    DEFAULT_MARKETS_CONFIG_DIR,
    DiscountComplianceError,
    DiscountValidation,
    load_market_rules,
    validate_discount_reference,
)
from src.state.store import StateStore

DEFAULT_STEPS_PER_WEEK = 5


@dataclass(frozen=True)
class CompetitorFloorCheck:
    """Outcome of comparing one step's planned clearance price against the
    latest observed price of its confirmed pricing_intel competitor match
    (the CHEAPEST of possibly several confirmed sites -- the binding
    constraint: if any one confirmed competitor is cheaper, undercutting
    THAT one still needs a reason, per-site comparisons would let a caller
    cherry-pick a more favorable site)."""

    site: str
    competitor_price: float
    observed_at: str  # ISO timestamp (CompetitorPriceContext.observed_at)
    acquisition_tier: str
    at_or_above_floor: bool
    reason: str | None  # populated only when below-floor AND a caller-supplied reason exists


@dataclass(frozen=True)
class CalendarStep:
    """One SKU's scheduled disposition: WHEN it launches, its own
    ``plan_liquidation`` numbers (read verbatim, never recomputed), and the
    two gates it had to clear to be ``included`` in the shippable calendar."""

    product_id: str
    classification: str          # excess | dead (LiquidationLine.classification)
    method: str                  # elasticity | default_discount | salvage_heuristic
    launch_week: int             # 0-indexed week (relative to `as_of`) this step fires
    duration_weeks: int          # weeks the recovery is spread over (1 for a lump sum)
    clearance_price: float | None
    units_to_clear: float
    at_risk_value: float
    recovered_value: float
    weeks_to_clear: float        # verbatim from the source LiquidationLine
    compliance: DiscountValidation | None      # None: not applicable (salvage) or unavailable
    competitor_check: CompetitorFloorCheck | None  # None: no confirmed competitor match/price
    included: bool               # False => excluded from the shippable calendar
    exclusion_reason: str | None  # populated iff included is False; never silent (Golden Rule 14)


@dataclass(frozen=True)
class WeekEntry:
    """One calendar week's aggregate across every INCLUDED step."""

    week: int
    steps_launched: int
    recovered_this_week: float
    cumulative_recovered: float


@dataclass(frozen=True)
class LiquidationCalendar:
    steps: tuple[CalendarStep, ...]           # every source line, in the report's own rank order
    weekly_schedule: tuple[WeekEntry, ...]    # INCLUDED steps only, week 0 .. last active week
    market: str
    as_of: str                                # ISO date
    steps_per_week: int
    n_steps: int
    n_included: int
    n_excluded: int
    n_omnibus_blocked: int
    n_competitor_flagged: int
    total_at_risk: float             # passthrough of report.total_at_risk (informational)
    total_recovered_planned: float   # sum of recovered_value over INCLUDED steps only
    total_at_risk_excluded: float    # cash at risk sitting in EXCLUDED steps (never hidden)
    total_recovered_excluded: float  # recovery that does NOT ship until resolved
    summary: str


def _reference_price(price_history: PriceHistory | None, product_id: str) -> float | None:
    """The "before" price a markdown announcement for ``product_id`` would
    cite -- median of the strictly-positive prices in ``price_history``,
    mirroring ``src.liquidation``'s private ``_current_price`` (kept as a
    small local re-derivation, not a cross-module import of a private
    helper -- see module docstring). ``None`` when there is nothing to
    compute a reference from."""
    entry = (price_history or {}).get(product_id)
    if not entry:
        return None
    positive = [float(p) for p in entry[0] if float(p) > 0]
    return median(positive) if positive else None


def _omnibus_result(
    line: LiquidationLine,
    *,
    market: str,
    as_of: date | datetime,
    store: StateStore | None,
    channel: str | None,
    markets_dir: Path | str,
    price_history: PriceHistory | None,
) -> tuple[DiscountValidation | None, str | None]:
    """Returns ``(validation, exclusion_reason)``. ``exclusion_reason`` is
    ``None`` when the step is Omnibus-clear, or when the check does not
    apply at all (a salvage line announces no price -- ``clearance_price``
    is ``None``)."""
    if line.clearance_price is None:
        return None, None

    reference = _reference_price(price_history, line.product_id)
    if reference is None or reference <= 0:
        return None, (
            f"no price history for '{line.product_id}' was supplied to the calendar -- cannot "
            "compute a discount reference (Omnibus check unavailable, plan Golden Rule 14)"
        )

    stated_pct = round(100.0 * (1.0 - line.clearance_price / reference), 2)
    try:
        validation = validate_discount_reference(
            product_id=line.product_id,
            new_price=line.clearance_price,
            stated_discount_pct=stated_pct,
            market=market,
            as_of=as_of,
            store=store,
            channel=channel,
            markets_dir=markets_dir,
        )
    except DiscountComplianceError as exc:
        validation = exc.validation

    if not validation.passed:
        return validation, f"Omnibus compliance check failed: {validation.reason}"
    return validation, None


def _competitor_result(
    line: LiquidationLine,
    *,
    competitor_contexts: dict[str, CompetitorPriceContext] | None,
    undercut_reasons: dict[str, str] | None,
) -> tuple[CompetitorFloorCheck | None, str | None]:
    """Returns ``(check, exclusion_reason)``. ``check`` is ``None`` when the
    step has no price (salvage) or no confirmed competitor match/price at
    all -- v1 fallback behavior, this PR is additive (plan QA row)."""
    if line.clearance_price is None:
        return None, None
    context = (competitor_contexts or {}).get(line.product_id)
    if context is None:
        return None, None

    at_or_above = line.clearance_price >= context.competitor_price
    if at_or_above:
        check = CompetitorFloorCheck(
            site=context.site, competitor_price=context.competitor_price,
            observed_at=context.observed_at.isoformat(), acquisition_tier=context.acquisition_tier,
            at_or_above_floor=True, reason=None,
        )
        return check, None

    reason = (undercut_reasons or {}).get(line.product_id)
    check = CompetitorFloorCheck(
        site=context.site, competitor_price=context.competitor_price,
        observed_at=context.observed_at.isoformat(), acquisition_tier=context.acquisition_tier,
        at_or_above_floor=False, reason=reason if reason and reason.strip() else None,
    )
    if check.reason is not None:
        return check, None  # a documented decision -- allowed to proceed (never a SILENT undercut)

    return check, (
        f"clearance price {line.clearance_price:g} undercuts confirmed competitor '{context.site}' "
        f"at {context.competitor_price:g} with no documented reason -- needs a human decision"
    )


def _step_duration_weeks(weeks_to_clear: float) -> int:
    """Weeks the recovery is spread over, starting at the step's launch
    week. ``inf`` (salvage/dead -- no demand curve to spread over) recovers
    as a ONE-week lump sum rather than being divided by infinity, which
    would compute a per-week amount of zero and never actually show up in a
    finite calendar (silently capping a real number -- Golden Rule 14).
    Rounded to 6 decimals before ceiling so ordinary floating-point noise
    (e.g. an exact 13-week clear surfacing as ``13.000000000000002`` from
    ``plan_liquidation``'s own division/sqrt chain) never pads the schedule
    by a whole extra week it doesn't actually need."""
    if math.isinf(weeks_to_clear) or weeks_to_clear <= 0:
        return 1
    return max(1, math.ceil(round(weeks_to_clear, 6)))


def build_liquidation_calendar(
    report: LiquidationReport,
    *,
    market: str,
    as_of: date | datetime,
    store: StateStore | None = None,
    channel: str | None = None,
    markets_dir: Path | str = DEFAULT_MARKETS_CONFIG_DIR,
    price_history: PriceHistory | None = None,
    competitor_contexts: dict[str, CompetitorPriceContext] | None = None,
    undercut_reasons: dict[str, str] | None = None,
    steps_per_week: int = DEFAULT_STEPS_PER_WEEK,
) -> LiquidationCalendar:
    """Sequence ``report``'s lines into a launch-week calendar with expected
    per-week and cumulative cash recovery, gated by the Omnibus discount
    check (PR-17) and the pricing_intel competitive floor (PR-10/PR-14).

    ``market`` is REQUIRED (no default -- Golden Rule 12, jurisdiction is
    declarative configuration a caller must consciously choose, never a
    silently-assumed one) and is validated up front via
    ``load_market_rules`` so an unconfigured jurisdiction fails fast rather
    than mid-loop on the first priced line. ``store`` should be an isolated
    ``StateStore`` in tests (never the process-wide default -- same
    convention ``src.pricing_guardrails`` itself documents).

    ``price_history`` should be the SAME mapping passed to
    ``plan_liquidation`` (or an equivalent one) -- it is how the Omnibus
    check recovers the "before" price a markdown announcement would cite;
    omitting it does not crash the build, it EXCLUDES every priced line with
    a clear reason instead (never assumes compliance -- Golden Rule 14).

    ``competitor_contexts`` (``product_id -> CompetitorPriceContext``) and
    ``undercut_reasons`` (``product_id -> reason string``) are both OPTIONAL
    and additive: a SKU absent from ``competitor_contexts`` gets no
    competitive check at all (the existing v1 behavior, unaffected by this
    PR) rather than being penalized for a match pricing_intel never made.

    Duplicate ``product_id``s in ``report.lines`` (``plan_liquidation``'s own
    documented behavior for duplicate input rows) are each evaluated
    independently, exactly like the source report itself.
    """
    if steps_per_week <= 0:
        raise ValueError("steps_per_week must be > 0")
    load_market_rules(market, config_dir=markets_dir)  # fail fast on an unconfigured jurisdiction

    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    normalized_market = market.strip().lower()

    steps: list[CalendarStep] = []
    weekly_recovery: dict[int, float] = {}
    weekly_launches: dict[int, int] = {}
    n_omnibus_blocked = 0
    n_competitor_flagged = 0
    total_at_risk_excluded = 0.0
    total_recovered_excluded = 0.0

    for index, line in enumerate(report.lines):
        launch_week = index // steps_per_week

        compliance, omnibus_reason = _omnibus_result(
            line, market=market, as_of=as_of, store=store, channel=channel,
            markets_dir=markets_dir, price_history=price_history,
        )
        competitor_check, competitor_reason = _competitor_result(
            line, competitor_contexts=competitor_contexts, undercut_reasons=undercut_reasons,
        )
        if omnibus_reason is not None:
            n_omnibus_blocked += 1
        if competitor_reason is not None:
            n_competitor_flagged += 1

        reasons = [r for r in (omnibus_reason, competitor_reason) if r]
        exclusion_reason = "; ".join(reasons) if reasons else None
        included = exclusion_reason is None
        duration = _step_duration_weeks(line.weeks_to_clear)

        steps.append(CalendarStep(
            product_id=line.product_id, classification=line.classification, method=line.method,
            launch_week=launch_week, duration_weeks=duration, clearance_price=line.clearance_price,
            units_to_clear=line.units_to_clear, at_risk_value=line.at_risk_value,
            recovered_value=line.recovered_value, weeks_to_clear=line.weeks_to_clear,
            compliance=compliance, competitor_check=competitor_check,
            included=included, exclusion_reason=exclusion_reason,
        ))

        if included:
            weekly_launches[launch_week] = weekly_launches.get(launch_week, 0) + 1
            per_week_amount = line.recovered_value / duration
            for week in range(launch_week, launch_week + duration):
                weekly_recovery[week] = weekly_recovery.get(week, 0.0) + per_week_amount
        else:
            total_at_risk_excluded += line.at_risk_value
            total_recovered_excluded += line.recovered_value

    max_week = max(weekly_recovery.keys(), default=-1)
    weekly_schedule: list[WeekEntry] = []
    cumulative = 0.0
    for week in range(max_week + 1):
        recovered_this_week = weekly_recovery.get(week, 0.0)
        cumulative += recovered_this_week
        weekly_schedule.append(WeekEntry(
            week=week, steps_launched=weekly_launches.get(week, 0),
            recovered_this_week=recovered_this_week, cumulative_recovered=cumulative,
        ))

    n_included = sum(1 for s in steps if s.included)
    n_excluded = len(steps) - n_included
    total_recovered_planned = sum(s.recovered_value for s in steps if s.included)

    summary = (
        f"Liquidation calendar over {len(steps)} step(s) starting {as_of_date.isoformat()} "
        f"(market '{normalized_market}'): {n_included} shippable across {len(weekly_schedule)} "
        f"week(s), recovering ~{total_recovered_planned:,.0f}; {n_excluded} excluded "
        f"({n_omnibus_blocked} Omnibus-blocked, {n_competitor_flagged} competitor-undercut without "
        f"a documented reason) worth ~{total_recovered_excluded:,.0f} pending resolution."
    )

    return LiquidationCalendar(
        steps=tuple(steps), weekly_schedule=tuple(weekly_schedule), market=normalized_market,
        as_of=as_of_date.isoformat(), steps_per_week=steps_per_week, n_steps=len(steps),
        n_included=n_included, n_excluded=n_excluded, n_omnibus_blocked=n_omnibus_blocked,
        n_competitor_flagged=n_competitor_flagged, total_at_risk=report.total_at_risk,
        total_recovered_planned=total_recovered_planned,
        total_at_risk_excluded=total_at_risk_excluded,
        total_recovered_excluded=total_recovered_excluded, summary=summary,
    )
