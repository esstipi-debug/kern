"""Automated QA — verify a JobReport's numbers before it reaches a client.

The first gate of the human-in-the-loop model: catch any internal inconsistency
(bad investment math, infeasible-but-flagged-feasible, negative safety stock,
out-of-range allocation) so the human only reviews sound output.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from nosilo_core.guided import EXECUTED, GuidedOutcome, verify_guided
from src.constraints import is_feasible_lot
from src.forecasting import MIN_PERIODS_FOR_POLICY
from src.sop_engine.coherence import CHECK_BUDGET_FEASIBILITY, CHECK_PROMO_COVERAGE, CHECK_SERVICE_LEVEL

from . import forecast_job
from .inventory_optimization import JobReport
from .leadership import DIMS, ChainProfile
from .pricing import PricingReport

if TYPE_CHECKING:  # avoid a real circular import: jobs.digest_job -> scm_agent.events
    # -> scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    # `from __future__ import annotations` above already makes the verify_digest type
    # hint a lazy string, so this import only ever runs for a type checker, never at
    # module load time.
    from .digest_job import DigestResult

    # Same hazard, same fix: jobs.integrated_plan -> scm_agent.knowledge/citation_gate ->
    # scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    from .integrated_plan import IntegratedPlanBundle

    # Same hazard, same fix: jobs.price_intelligence -> src.pricing_intel.sanity ->
    # scm_agent.events -> scm_agent (package __init__) -> scm_agent.tools -> here.
    from .price_intelligence import PriceIntelReport

    # Same hazard, same fix: jobs.price_watch -> scm_agent.events ->
    # scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    from .price_watch import PriceWatchCycleReport

TOL = 1e-6
PRICE_INTEL_COVERAGE_MIN = 0.60  # plan section 6.9 item 2: ">=60% SKUs con >=1 competidor confirmado"

#: policy_kinds that carry NO quantity by design -- the engine declined to size them
#: (degenerate demand, degenerate cost, or too little history). Everything else is a
#: "priced" line: a number an operator is being asked to act on, and therefore subject
#: to the evidence invariants in verify() below.
UNPRICED_POLICY_KINDS = frozenset({
    "skipped_zero_demand", "skipped_zero_cost", "insufficient_history",
})

#: Los campos que describen una politica dimensionada. jobs/inventory_optimization.py
#: los emite en ``None`` exactamente cuando no pudo dimensionarla.
SIZED_POLICY_FIELDS = ("forecast", "reorder_point", "safety_stock", "z_factor")

#: La unica policy_kind sin precio cuyos CEROS son una medicion y no una ausencia:
#: la demanda agregada se midio y dio cero, asi que un pronostico de 0 y un
#: disparador de 0 son lo que el motor calculo. Las otras dos no calcularon nada.
MEASURED_ZERO_POLICY_KINDS = frozenset({"skipped_zero_demand"})
_REQUIRED_INTEGRATED_PLAN_CHECKS = {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}


def _sized_field_issues(r) -> list[str]:
    """La ausencia de politica se chequea en las DOS direcciones.

    Hasta el 2026-09-19 una negativa del motor salia con `reorder_point`,
    `safety_stock`, `z_factor` y `forecast` en 0.0, y el aviso viajaba por otro
    canal (`policy_kind`, un residual). Todo consumidor que leia el NUMERO leia lo
    contrario de lo que decia la prosa: `src/simworld/runners.py` lo ejecutaba como
    "no pedir nunca" y el Tower emitia `dead_stock` sobre un SKU que vendia todas
    las semanas. Un 0 significa "medido, y la respuesta es ninguno"; una negativa
    no midio nada.

    Por eso esta compuerta prohibe las dos formas de mentir: una linea sin politica
    que traiga un numero, y una linea con politica a la que le falte uno. La primera
    es la que costo el defecto; la segunda es lo que impide arreglarlo anulando
    todo.
    """
    unpriced = r.policy_kind in UNPRICED_POLICY_KINDS
    declined = unpriced and r.policy_kind not in MEASURED_ZERO_POLICY_KINDS
    out: list[str] = []
    for field in SIZED_POLICY_FIELDS:
        value = getattr(r, field, None)
        if declined:
            if value is not None:
                out.append(
                    f"{r.product_id}: {r.policy_kind} carries a {field} of {value:.4g} - "
                    "the engine declined to size this SKU, so the absence must be null, "
                    "not a number an operator or a downstream job can act on"
                )
        elif value is None:
            out.append(
                f"{r.product_id}: {r.policy_kind} has no {field}; only a declined SKU "
                "may leave a sized field null"
            )
    return out


def verify(report: JobReport) -> list[str]:
    """Return a list of QA issues. Empty list = passed."""
    issues: list[str] = []

    if not report.recommendations:
        issues.append("report has no SKU recommendations")

    for r in report.recommendations:
        if abs(r.investment - (r.cycle_investment + r.ss_investment)) > max(TOL, abs(r.investment) * 1e-9):
            issues.append(f"{r.product_id}: investment != cycle + safety")
        issues.extend(_sized_field_issues(r))
        if r.safety_stock is not None and r.safety_stock < -TOL:
            issues.append(f"{r.product_id}: negative safety stock")
        if r.reorder_point is not None and r.reorder_point < -TOL:
            issues.append(f"{r.product_id}: negative reorder point")
        if r.policy_kind == "(s, Q)" and (r.order_quantity is None or r.order_quantity <= 0):
            issues.append(f"{r.product_id}: (s,Q) without a positive order quantity")
        issues.extend(_lot_issues(r))
        issues.extend(_position_issues(r))
        if r.policy_kind == "(R, S)" and (r.order_up_to is None or r.order_up_to <= 0):
            issues.append(f"{r.product_id}: (R,S) without a positive order-up-to level")
        # the lead-only reorder must stay below order-up-to for (R,S)
        if r.policy_kind == "(R, S)" and r.order_up_to is not None and r.reorder_point > r.order_up_to + TOL:
            issues.append(f"{r.product_id}: reorder point exceeds order-up-to level")

        # ---- evidence invariants: does this number rest on anything? ----
        # These are the second line behind jobs/inventory_optimization.py's
        # minimum-history gate. The gate can be bypassed (a caller lowering
        # min_periods, a future refactor dropping the branch, a report assembled
        # by hand); these cannot, because they are checked on the record itself.
        if r.policy_kind not in UNPRICED_POLICY_KINDS:
            n_periods = getattr(r, "n_periods", 0)
            if n_periods < MIN_PERIODS_FOR_POLICY:
                issues.append(
                    f"{r.product_id}: priced policy built on {n_periods} period(s) of demand "
                    f"history, below the {MIN_PERIODS_FOR_POLICY}-period minimum"
                )

        # sigma_source 'unavailable' means NO dispersion was estimable. A positive
        # buffer cannot come from that - z_alpha * sigma * sqrt(tau) with no sigma
        # is a fabricated number. The converse (unavailable + zero buffer) is the
        # legitimate measured-flat-history case and must keep passing: see
        # src/forecasting.py's to_engine_inputs "measured, not missing" branch.
        if (getattr(r, "sigma_source", "") == "unavailable"
                and r.safety_stock is not None and r.safety_stock > TOL):
            issues.append(
                f"{r.product_id}: safety stock {r.safety_stock:.4g} with no estimable "
                "dispersion (sigma_source='unavailable')"
            )

    if not (0.0 - TOL <= report.safety_stock_scale <= 1.0 + TOL):
        issues.append(f"safety_stock_scale out of [0,1]: {report.safety_stock_scale}")
    if report.final_investment > report.requested_investment + max(TOL, report.requested_investment * 1e-9):
        issues.append("final investment exceeds requested")

    if report.budget is not None:
        if report.feasible and report.final_investment > report.budget + 1.0:
            issues.append("flagged feasible but final investment exceeds budget")
        if not report.feasible and report.cycle_floor <= report.budget + TOL:
            issues.append("flagged infeasible but cycle-stock floor fits the budget")

    sku_sum = sum(r.investment for r in report.recommendations)
    if report.budget is None and abs(sku_sum - report.requested_investment) > max(1.0, sku_sum * 1e-6):
        issues.append("requested investment != sum of SKU investments")

    return issues


def _lot_issues(r) -> list[str]:
    """Supplier-terms invariants on one (s,Q) record that carries a rule.

    The lot Kern prints must be one the supplier accepts (on the multiple, at or
    above the MOQ), the unconstrained EOQ it was derived from must still be on
    the record and positive, and the runner-up lot -- the second terminal -- must
    be feasible too and never cheaper than the one chosen, or the choice was
    wrong. A rule on an (R,S) line is carried, not applied, so nothing is
    checked there beyond the record naming it.
    """
    rule = getattr(r, "lot_rule", "")
    if not rule or r.policy_kind != "(s, Q)" or r.order_quantity is None:
        return []
    issues: list[str] = []
    moq, multiple = getattr(r, "lot_moq", 0.0), getattr(r, "lot_multiple", 0.0)
    terms = dict(minimum_order_quantity=moq, order_multiple=multiple)
    if not is_feasible_lot(r.order_quantity, **terms):
        issues.append(
            f"{r.product_id}: order quantity {r.order_quantity:.4g} violates the supplier "
            f"terms ({rule})")
    q0 = getattr(r, "order_quantity_unconstrained", None)
    if q0 is None or q0 <= 0:
        issues.append(f"{r.product_id}: supplier terms applied but no unconstrained Q on record")
    if getattr(r, "lot_penalty_usd", 0.0) < -TOL:
        issues.append(f"{r.product_id}: negative lot penalty")
    alt = getattr(r, "lot_alternative_qty", None)
    if alt is not None:
        if not is_feasible_lot(alt, **terms):
            issues.append(
                f"{r.product_id}: alternative lot {alt:.4g} violates the supplier terms ({rule})")
        alt_penalty = getattr(r, "lot_alternative_penalty_usd", None)
        if alt_penalty is None or alt_penalty + TOL < getattr(r, "lot_penalty_usd", 0.0):
            issues.append(
                f"{r.product_id}: the alternative lot is cheaper than the chosen one")
    return issues


def _position_issues(r) -> list[str]:
    """Stock-position invariants on one record.

    Without stock on hand nothing downstream may claim a position, an on-order
    figure or a due-now signal. With one, the position must be the DDMRP identity
    (on hand + on order, zero when unmeasured), and the due-now signal must be
    exactly what the policy says at that position: (s,Q) orders Q at or below s,
    (R,S) orders up to S below S, nothing otherwise.
    """
    on_hand = getattr(r, "on_hand", None)
    on_order = getattr(r, "on_order", None)
    position = getattr(r, "inventory_position", None)
    due = getattr(r, "order_now", None)
    qty = getattr(r, "order_qty_now", None)
    issues: list[str] = []
    if on_hand is None:
        if any(v is not None for v in (on_order, position, due, qty)):
            issues.append(f"{r.product_id}: position fields set without stock on hand")
        return issues
    expected = on_hand + (on_order or 0.0)
    if position is None or abs(position - expected) > TOL:
        issues.append(f"{r.product_id}: inventory position != on hand + on order")
        return issues
    if due is None:
        if qty is not None:
            issues.append(f"{r.product_id}: order quantity due now without a due-now signal")
        return issues
    if qty is None or qty < -TOL:
        issues.append(f"{r.product_id}: due-now signal without a non-negative quantity")
        return issues
    if r.policy_kind == "(s, Q)" and r.order_quantity is not None:
        should = position <= r.reorder_point
        want = r.order_quantity if should else 0.0
    elif r.policy_kind == "(R, S)" and r.order_up_to is not None:
        should = position < r.order_up_to
        want = max(0.0, r.order_up_to - position) if should else 0.0
    else:
        issues.append(f"{r.product_id}: due-now signal on a policy that cannot order")
        return issues
    if bool(due) != should:
        issues.append(f"{r.product_id}: order_now={due} contradicts the position vs trigger")
    if abs(qty - want) > TOL:
        issues.append(f"{r.product_id}: order_qty_now {qty:.4g} != what the policy orders ({want:.4g})")
    return issues


def verify_pricing(report: PricingReport) -> list[str]:
    """Return a list of QA issues for a pricing report. Empty list = passed."""
    issues: list[str] = []
    if not report.recommendations:
        issues.append("pricing report has no recommendations")

    for r in report.recommendations:
        if r.current_price < -TOL:
            issues.append(f"{r.product_id}: negative current price")
        if r.action in {"raise", "lower"}:
            if r.optimal_price is None or r.optimal_price <= 0:
                issues.append(f"{r.product_id}: actionable but no positive optimal price")
            elif r.optimal_price <= r.unit_cost:
                issues.append(f"{r.product_id}: optimal price at or below unit cost")
            if r.elasticity >= -1:
                issues.append(f"{r.product_id}: actionable but demand is inelastic")
            if r.confident and r.profit_uplift_pct is not None and r.profit_uplift_pct < -0.5:
                issues.append(f"{r.product_id}: 'optimal' price lowers modeled profit")
        elif r.action == "inelastic" and r.elasticity < -1:
            issues.append(f"{r.product_id}: flagged inelastic but elasticity < -1")
        elif r.action == "insufficient_data" and r.optimal_price is not None:
            issues.append(f"{r.product_id}: insufficient data but an optimal price was set")

    return issues


def verify_leadership(profile: ChainProfile) -> list[str]:
    """Return a list of QA issues for a CHAIN profile. Empty list = passed."""
    issues: list[str] = []
    codes = {code for code, _ in DIMS}

    if set(profile.scores) != codes:
        issues.append("profile is missing CHAIN dimensions")
    for code, val in profile.scores.items():
        if not 0 <= val <= 4:
            issues.append(f"{code}: score out of 0..4")

    expected_avg = sum(profile.scores.values()) / len(profile.scores) if profile.scores else 0.0
    if abs(profile.average - expected_avg) > 1e-9:
        issues.append("average does not match scores")

    if profile.scores:
        expected_gap = max(profile.scores.values()) - min(profile.scores.values())
        if profile.gap != expected_gap:
            issues.append("gap does not match scores")
        if profile.lever_level != min(profile.scores.values()):
            issues.append("priority lever is not the lowest-scoring dimension")

    if not profile.archetype:
        issues.append("missing archetype")
    return issues


def leadership_passed(profile: ChainProfile) -> bool:
    return not verify_leadership(profile)


def verify_digest(result: DigestResult) -> list[str]:
    """Return a list of QA issues for a daily digest (Linchpin 3.0 PR-3). Empty
    list = passed. The invariant that matters here is "no fabricated data"
    (plan rule 14): the per-type breakdown must actually sum to the reported
    total, an empty ledger must not be reported as having events, and a
    message that claims events happened must actually mention a count."""
    issues: list[str] = []

    if result.event_count < 0:
        issues.append("digest: negative event_count")
    if any(v < 0 for v in result.counts_by_type.values()):
        issues.append("digest: negative count in counts_by_type")

    counted = sum(result.counts_by_type.values())
    if counted != result.event_count:
        issues.append(f"digest: counts_by_type sums to {counted} but event_count is {result.event_count}")

    if not result.message.strip():
        issues.append("digest: empty message")
    elif result.event_count == 0 and "no events" not in result.message:
        issues.append("digest: zero events but message does not say so")
    elif result.event_count > 0 and str(result.event_count) not in result.message:
        issues.append("digest: message does not mention the actual event count")

    return issues


def digest_passed(result: DigestResult) -> bool:
    return not verify_digest(result)


def verify_price_intel(report: PriceIntelReport) -> list[str]:
    """QA invariants for the price-intelligence one-shot deliverable (plan
    section 6.9 item 2). Empty list = passed:

    - coverage: >=60% of products must have >=1 accepted competitor
      observation to ship at all (below that, the position matrix is too
      sparse to be a defensible deliverable).
    - zero quarantined/discarded rows leak into the accepted offers --
      they are reported in their own section (golden rule 14), never
      shipped as if trustworthy.
    - average freshness of the accepted observations stays within the
      report's own stated SLA.
    """
    issues: list[str] = []

    if report.n_products <= 0:
        issues.append("price_intel: no products in scope")
    if not (0.0 - TOL <= report.coverage_pct <= 1.0 + TOL):
        issues.append(f"price_intel: coverage_pct out of [0,1]: {report.coverage_pct}")
    elif report.coverage_pct < PRICE_INTEL_COVERAGE_MIN - TOL:
        issues.append(
            f"price_intel: coverage {report.coverage_pct * 100:.0f}% is below the "
            f"{PRICE_INTEL_COVERAGE_MIN * 100:.0f}% minimum required to ship"
        )

    accepted_refs = {(o.site, o.competitor_sku_ref) for o in report.offers}
    tainted_refs = {(r.site, r.competitor_url) for r in report.rows if r.status in ("quarantined", "discarded")}
    leaked = tainted_refs & accepted_refs
    if leaked:
        issues.append(f"price_intel: {len(leaked)} quarantined/discarded row(s) leaked into the accepted offers")

    if not (0.0 - TOL <= report.quarantine_rate <= 1.0 + TOL):
        issues.append(f"price_intel: quarantine_rate out of [0,1]: {report.quarantine_rate}")

    if report.avg_freshness_hours < -TOL:
        issues.append(f"price_intel: negative avg_freshness_hours: {report.avg_freshness_hours}")
    elif report.avg_freshness_hours > report.sla_hours + TOL:
        issues.append(
            f"price_intel: average freshness {report.avg_freshness_hours:.1f}h exceeds the "
            f"{report.sla_hours:.1f}h SLA"
        )

    return issues


def price_intel_passed(report: PriceIntelReport) -> bool:
    return not verify_price_intel(report)


def coverage_gate(outcome: GuidedOutcome) -> list[str]:
    """Deliverable coverage gate (plan §2.14). Empty list = the deliverable is covered.

    Extends the never-unprotected contract (``verify_guided``) with the residual-block
    requirements: a non-executed result must spell out the human residual - every
    handoff states the risk if skipped, and every escalation routes to a named human
    with an SLA. This is the QA-layer guard against a silent dead end.
    """
    issues = list(verify_guided(outcome))

    for h in outcome.handoffs:
        if not h.risk_if_skipped.strip():
            issues.append(f"handoff '{h.title}' does not state the risk if skipped")

    e = outcome.escalation
    if e is not None:
        if not e.route_to.strip():
            issues.append("escalation has no route_to (named human/role)")
        if not e.sla.strip():
            issues.append("escalation has no SLA")
        if not e.reason.strip():
            issues.append("escalation has no reason")

    return issues


def covered(outcome: GuidedOutcome) -> bool:
    return not coverage_gate(outcome)


def verify_integrated_plan(bundle: IntegratedPlanBundle) -> list[str]:
    """QA gate for A5's integrated plan (Linchpin 3.0 PR-20). Empty list =
    passed. Checks STRUCTURAL soundness (finite numbers, all 3 required
    coherence-check kinds present, internally-consistent counts) -- a
    FAILED coherence check is an intended FINDING this deliverable reports
    to a human (see ``jobs.integrated_plan``'s module docstring), never
    itself a QA failure that blocks the deliverable. A NOT-EVALUABLE check
    is likewise a valid, declared delivery here: whether the plan is allowed
    to CLAIM coherence on top of one is ``jobs.integrated_plan.verify``'s
    question, not this gate's.

    ``plan.n_checks_passed`` and ``bundle.tally.n_passed`` are deliberately NOT
    compared: they count different things on purpose. The first is the engine's
    arithmetic over every check it ran, including any it resolved against a
    placeholder input; the second counts only the checks that could be evaluated.
    Reconciling them would mean deciding the engine's naive count is the truth,
    which is the defect the tally exists to undo.
    """
    issues: list[str] = list(forecast_job.verify(bundle.forecast_report))
    plan = bundle.plan

    if plan.n_skus <= 0:
        issues.append("integrated_plan: no SKUs in the plan")
    if plan.n_checks != len(plan.checks):
        issues.append("integrated_plan: n_checks does not match len(checks)")
    if plan.n_checks_passed + plan.n_checks_failed != plan.n_checks:
        issues.append("integrated_plan: n_checks_passed + n_checks_failed != n_checks")

    # The tally must account for every check the engine produced, exactly once:
    # a result quietly dropped from all three buckets would shrink the "N/M
    # evaluated" denominator with nothing to show for it.
    tally = bundle.tally
    if tally.n_total != len(plan.checks):
        issues.append(
            f"integrated_plan: the coherence tally accounts for {tally.n_total} check(s) but "
            f"the plan holds {len(plan.checks)}")
    tallied = sorted(
        [(c.check, c.product_id) for c in tally.passed + tally.failed]
        + [(u.check, u.product_id) for u in tally.unevaluable]
    )
    if tallied != sorted((c.check, c.product_id) for c in plan.checks):
        issues.append(
            "integrated_plan: the coherence tally is not a partition of the plan's checks "
            f"(tallied {tallied})")

    present_kinds = {c.check for c in plan.checks}
    missing_kinds = _REQUIRED_INTEGRATED_PLAN_CHECKS - present_kinds
    if missing_kinds:
        issues.append(f"integrated_plan: missing required coherence check kind(s): {sorted(missing_kinds)}")

    for line in plan.demand_plan:
        if not math.isfinite(line.base_forecast) or line.base_forecast < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid base_forecast")
        if not math.isfinite(line.shaped_demand) or line.shaped_demand < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid shaped_demand")

    for line in plan.purchase_plan:
        if not math.isfinite(line.recommended_order) or line.recommended_order < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid recommended_order")
        if not math.isfinite(line.order_value) or line.order_value < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid order_value")

    return issues


def integrated_plan_passed(bundle: IntegratedPlanBundle) -> bool:
    return not verify_integrated_plan(bundle)


def verify_price_watch(report: PriceWatchCycleReport) -> list[str]:
    """QA gate for the discovery-assisted watch cycle (Task 11 / PR-11's agent-
    tool wiring). Empty list = passed. STRUCTURAL checks only -- no new
    judgment threshold is invented here (unlike ``verify_price_intel``'s own
    60% coverage bar, a DIFFERENT, already-approved deliverable with its own
    bar): pairs_checked matches the outcome count (golden rule 14), every
    outcome's status is one of the honest, enumerated values
    ``jobs.price_monitor.accept_observation`` actually returns, and every
    outcome carries a non-empty, citable reason.

    R5, checked AGAIN at this gate (not just ``scm_agent.tool_options.
    price_watch_options``): every ``pending_escalations`` entry (Task 9's
    ceiling-raise request) must honor the never-unprotected contract
    (``coverage_gate``, which extends ``verify_guided`` with the residual/
    handoff/escalation completeness checks) and must NEVER be status
    ``EXECUTED`` -- a tier raise is never silently applied.
    """
    issues: list[str] = []

    if report.pairs_checked != len(report.outcomes):
        issues.append(
            f"price_watch: pairs_checked ({report.pairs_checked}) != len(outcomes) ({len(report.outcomes)})"
        )

    valid_statuses = {"accepted", "quarantined", "discarded", "skipped"}
    for o in report.outcomes:
        if o.status not in valid_statuses:
            issues.append(f"price_watch: {o.site}/{o.competitor_sku_ref}: invalid status {o.status!r}")
        if not o.reason:
            issues.append(f"price_watch: {o.site}/{o.competitor_sku_ref}: outcome has no reason")

    for guided in report.pending_escalations:
        if guided.status == EXECUTED:
            issues.append(
                "price_watch: a pending ceiling-raise escalation reports status EXECUTED -- R5 violation"
            )
        issues.extend(f"price_watch: pending escalation: {msg}" for msg in coverage_gate(guided))

    return issues


def price_watch_passed(report: PriceWatchCycleReport) -> bool:
    return not verify_price_watch(report)
