"""Task 9: the scorer -- the KPI contract every runner is judged by, plus the
normalized ``capture`` metric.

The property this whole task exists to protect: ``weekly_cost``/``score_run``
must compute cost via the EXACT same formula
(procurement + holding + shortage + fixed order cost) that
``src/simworld/runners.py``'s MILP oracle already optimizes, with the same
default parameters (``order_cost=75.0, holding_rate=0.25,
shortage_penalty_rate=1.0``). If the formulas drift apart, the oracle stops
being a genuine ceiling relative to what the scorer measures, and ``capture``
becomes meaningless.

Per the standing lesson on this plan (every prior task shipped at least one
test that passed against a provably wrong implementation): tests below that
target a specific formula component pin EXACT hand-computed numbers, not
range checks, and were each confirmed (by actually mutating scorer.py,
re-running, and reverting) to fail against the specific wrong implementation
named in their docstring. See task-9-report.md for the full replay log.
"""

import dataclasses
import inspect

import numpy as np
import pytest

from src.simworld.runners import apply_policy, naive_policy, oracle_orders
from src.simworld.scorer import (
    Degradation,
    Kpis,
    _anchoring,
    _demand_chasing,
    capture,
    degradation,
    score_run,
    weekly_cost,
)
from src.simworld.world import Sku, Supplier, WeekOutcome, World, advance, generate_world, initial_state


def _run(strategy, seed=4, weeks=30, n_skus=6):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(w.weeks):
        st, out = advance(w, st, strategy(w, st, outs))
        outs.append(out)
    return w, outs


def _naive(w, st, outs):
    return apply_policy(w, st, naive_policy(w, outs)) if len(outs) >= 4 else {}


def _oracle(w, st, outs):
    return oracle_orders(w, st)


# ---------------------------------------------------------------------------
# Hand-computed fixture: 2 SKUs, 2 weeks, every field chosen so every Kpis
# field (and both weekly costs) can be computed independently by hand. This
# is the fixture the report's replay log exercises most heavily.
#
# SKU A: unit_cost=10, price=25 (margin=15)
# SKU B: unit_cost=4,  price=10 (margin=6)
#
# Week 0: A orders 5 (on_hand_end=3, demand=5, sold=5, lost=0)
#         B orders 8 (on_hand_end=6, demand=8, sold=8, lost=0)
#         -> BOTH skus order this week, deliberately, so a "fixed cost once
#         per calendar week" bug (instead of once per SKU-week) is
#         distinguishable from the correct "once per SKU-week" formula.
# Week 1: A orders 0 (on_hand_end=1, demand=6, sold=4, lost=2)
#         B orders 0 (on_hand_end=0, demand=3, sold=0, lost=3)
#         -> NEITHER sku orders, so this week contributes zero fixed cost
#         either way (the discriminating week is week 0).
#
# Hand-computed (order_cost=75, holding_rate=0.25, shortage_penalty_rate=1.0,
# weekly_holding = unit_cost * 0.25 / 52):
#   weekly_holding_A = 10 * 0.25 / 52 = 0.04807692307692308
#   weekly_holding_B =  4 * 0.25 / 52 = 0.019230769230769232
#
#   week0: procurement = 5*10 + 8*4       = 82
#          holding     = 3*weekly_holding_A + 6*weekly_holding_B
#                      = 0.14423076923076922 + 0.11538461538461539
#                      = 0.2596153846153846
#          shortage    = 0*15 + 0*6        = 0
#          fixed       = 75 * 2 (A and B both ordered>0)  = 150
#          week0 total = 82 + 0.2596153846153846 + 0 + 150 = 232.25961538461536
#
#   week1: procurement = 0
#          holding     = 1*weekly_holding_A + 0*weekly_holding_B
#                      = 0.04807692307692308
#          shortage    = 2*15 + 3*6        = 30 + 18 = 48
#          fixed       = 75 * 0 (neither ordered)        = 0
#          week1 total = 0 + 0.04807692307692308 + 48 + 0 = 48.04807692307692
#
#   totals: procurement=82, holding=0.3076923076923077, shortage=48,
#           fixed=150, total_cost=280.3076923076923
#
#   service_level = fraction of the 4 sku-weeks with lost<=0 = 2/4 = 0.5
#   fill_rate = sold.sum()/demand.sum() = (5+8+4+0)/(5+8+6+3) = 17/22
#             = 0.7727272727272727
#   unfulfilled_units = lost.sum() = 0+0+2+3 = 5.0
#   mean_on_hand = mean([3,6,1,0]) = 2.5
#   revenue = 5*25 + 8*10 + 4*25 + 0*10 = 125+80+100+0 = 305.0
#   profit = 305.0 - 280.3076923076923 = 24.692307692307693
#
#   order_series (sum across skus per week) = [13.0, 0.0]
#   demand_series (sum across skus per week) = [13.0, 9.0]
#   bullwhip = var([13,0]) / var([13,9]) = 42.25 / 4.0 = 10.5625
#   anchoring = |mean([13,0]) - mean([13,9])| / mean([13,9])
#             = |6.5 - 11.0| / 11.0 = 4.5/11.0 = 0.4090909090909091
#   demand_chasing = 0.0 (len(orders)==2 < 3, the short-series guard fires)
# ---------------------------------------------------------------------------

def _two_sku_two_week_fixture():
    sku_a = Sku(sku="SKU-A", unit_cost=10.0, price=25.0, supplier_idx=0)
    sku_b = Sku(sku="SKU-B", unit_cost=4.0, price=10.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=1, otif=1.0)
    w = World(seed=0, weeks=2, regime="seasonal_trend", skus=(sku_a, sku_b),
              suppliers=(supplier,), demand=np.zeros((2, 2)),
              real_lead_weeks=np.ones((2, 2), dtype=int))
    outs = [
        WeekOutcome(week=0, demand=np.array([5.0, 8.0]), sold=np.array([5.0, 8.0]),
                    lost=np.array([0.0, 0.0]), received=np.array([0.0, 0.0]),
                    ordered=np.array([5.0, 8.0]), on_hand_end=np.array([3.0, 6.0])),
        WeekOutcome(week=1, demand=np.array([6.0, 3.0]), sold=np.array([4.0, 0.0]),
                    lost=np.array([2.0, 3.0]), received=np.array([0.0, 0.0]),
                    ordered=np.array([0.0, 0.0]), on_hand_end=np.array([1.0, 0.0])),
    ]
    return w, outs


def test_weekly_cost_matches_the_hand_computed_per_week_totals():
    """Pins the exact per-week totals derived above. Discriminates against:
    - dropping the /WEEKS_PER_YEAR from the holding formula (holding would be
      52x too large: week0 holding would be ~13.5 instead of ~0.26);
    - swapping margin for price in the shortage term (week1 shortage would be
      2*25+3*10=80 instead of 48);
    - counting the fixed order cost once per calendar week instead of once
      per SKU-week (week0 fixed would be 75 instead of 150, since A and B
      both order in week 0 -- this is the discriminating week by design).
    Confirmed by replay: mutating each of these three lines independently in
    ``weekly_cost`` made this exact assertion fail; reverting restored the
    pass (see task-9-report.md).
    """
    w, outs = _two_sku_two_week_fixture()
    costs = weekly_cost(w, outs)
    assert costs == pytest.approx([232.25961538461536, 48.04807692307692])


def test_score_run_hand_computed_kpis_for_a_known_two_week_fixture():
    """Same fixture as above, but through the full ``Kpis`` contract. Pins
    every single field by hand -- not a "looks reasonable" range check.
    Discriminates against the same three cost-formula mutants as the test
    above, PLUS:
    - forgetting to subtract unit_cost from price for margin/revenue (would
      change shortage_cost and profit);
    - computing service_level/fill_rate/mean_on_hand over the wrong axis or
      the wrong array (sold vs demand vs on_hand_end swapped);
    - an inverted bullwhip ratio (var(demand)/var(orders) instead of
      var(orders)/var(demand)) -- 4.0/42.25=0.0947... vs the correct 10.5625,
      not the same number by coincidence;
    - a missing abs() in anchoring (would flip sign only if orders < demand,
      which is exactly this fixture's case: without abs(), anchoring would be
      -0.409... instead of +0.409...).
    Confirmed by replay for each (see task-9-report.md).
    """
    w, outs = _two_sku_two_week_fixture()
    k = score_run(w, outs)

    assert k.weeks == 2
    assert k.service_level == pytest.approx(0.5)
    assert k.fill_rate == pytest.approx(17.0 / 22.0)
    assert k.unfulfilled_units == pytest.approx(5.0)
    assert k.mean_on_hand == pytest.approx(2.5)
    assert k.procurement_cost == pytest.approx(82.0)
    assert k.holding_cost == pytest.approx(0.3076923076923077)
    assert k.shortage_cost == pytest.approx(48.0)
    assert k.fixed_order_cost == pytest.approx(150.0)
    assert k.total_cost == pytest.approx(280.3076923076923)
    assert k.revenue == pytest.approx(305.0)
    assert k.profit == pytest.approx(24.692307692307693)
    assert k.bullwhip == pytest.approx(10.5625)
    assert k.anchoring == pytest.approx(0.4090909090909091)
    assert k.demand_chasing == pytest.approx(0.0)  # short-series guard (2 < 3)


def test_kpis_is_frozen():
    """Per the global constraint, ``Kpis`` must be ``frozen=True`` matching
    the rest of ``src/simworld/``'s dataclasses (``Policy``, ``WeekOutcome``,
    ``World``). Discriminates against a mutant that drops ``frozen=True``
    from the ``@dataclass`` decorator: confirmed by replay, that mutant lets
    the attribute assignment below succeed silently instead of raising
    ``dataclasses.FrozenInstanceError`` (see task-9-report.md).
    """
    w, outs = _two_sku_two_week_fixture()
    k = score_run(w, outs)
    assert isinstance(k, Kpis)
    with pytest.raises(dataclasses.FrozenInstanceError):
        k.total_cost = 0.0


def test_total_cost_decomposes_exactly():
    w, outs = _run(_naive)
    k = score_run(w, outs)
    parts = (k.procurement_cost + k.holding_cost + k.shortage_cost + k.fixed_order_cost)
    assert k.total_cost == pytest.approx(parts)


def test_weekly_cost_sums_to_total_cost():
    w, outs = _run(_naive)
    k = score_run(w, outs)
    assert weekly_cost(w, outs).sum() == pytest.approx(k.total_cost)


def test_weekly_cost_has_one_entry_per_week():
    w, outs = _run(_naive, weeks=30)
    assert weekly_cost(w, outs).shape == (30,)


def test_weekly_cost_and_score_run_agree_on_empty_outcomes_by_both_raising():
    """``weekly_cost(...).sum() == score_run(...).total_cost`` is documented
    to hold for any run (see ``test_weekly_cost_sums_to_total_cost``); the two
    functions must therefore agree at the empty-outcomes boundary too, not
    just for non-empty runs. Before this fix, ``score_run([])`` raised
    ``ValueError`` while ``weekly_cost([])`` silently returned an empty
    array -- "no weeks to score" is a genuine caller error for both, not a
    valid zero-cost answer for one of them. Confirmed by replay: before the
    fix, the ``weekly_cost`` assertion below failed because no exception was
    raised (see task-9-report.md).
    """
    w, _ = _two_sku_two_week_fixture()
    with pytest.raises(ValueError):
        score_run(w, [])
    with pytest.raises(ValueError):
        weekly_cost(w, [])


def test_fill_rate_is_between_zero_and_one():
    w, outs = _run(_naive)
    k = score_run(w, outs)
    assert 0.0 <= k.fill_rate <= 1.0


def test_a_runner_that_never_orders_has_a_worse_fill_rate_than_the_oracle():
    w_a, outs_a = _run(lambda w, s, o: {})
    w_b, outs_b = _run(_oracle)
    assert score_run(w_a, outs_a).fill_rate < score_run(w_b, outs_b).fill_rate


def _reference_oracle_style_total_cost(world, outcomes, *, order_cost=75.0,
                                        holding_rate=0.25, shortage_penalty_rate=1.0):
    """Independent re-implementation of the SAME formula the oracle's MILP
    optimizes (procurement + holding + shortage + fixed order cost), written
    from scratch here rather than imported from ``scorer.py`` or
    ``runners.py`` -- this is the cross-check that the scorer and the oracle
    never silently drift apart, per the task's own load-bearing warning."""
    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    price = np.array([s.price for s in world.skus], dtype=float)
    margin = price - unit_cost
    weekly_holding = unit_cost * holding_rate / 52.0

    total = 0.0
    for o in outcomes:
        procurement = float((o.ordered * unit_cost).sum())
        holding = float((o.on_hand_end * weekly_holding).sum())
        shortage = float((o.lost * margin * shortage_penalty_rate).sum())
        fixed = float(order_cost * int((o.ordered > 0).sum()))
        total += procurement + holding + shortage + fixed
    return total


def test_score_run_total_cost_matches_an_independently_written_oracle_style_formula():
    """Verifies internal formula correctness against a hand-derived reference,
    exercised end to end: run the oracle for real (through
    ``oracle_orders``/``advance``, the exact same machinery ``runners.py``'s own
    cost-comparison test uses) and confirm ``score_run``'s ``total_cost`` matches
    a formula written independently in this file, not derived by importing the
    implementation under test, on a real simulated run rather than just the
    small hand-picked fixture above.

    **What this test verifies, precisely (corrected from an earlier, now-stale
    claim in this docstring):** ``_reference_oracle_style_total_cost`` hardcodes
    its own independent literal copies of ``52.0``, ``75.0``, ``0.25`` and
    ``1.0`` -- it never reads any of those four numbers from ``runners.py`` or
    ``scorer.py``. Whether this test can detect a given drift in
    ``runners.py`` therefore depends entirely on whether ``score_run``'s side
    of the comparison also moves when that value changes:

    - ``WEEKS_PER_YEAR``: ``scorer.py`` does ``from src.simworld.runners import
      WEEKS_PER_YEAR`` (no longer its own literal), so ``score_run``'s side
      DOES move with ``runners.py``'s constant while this test's reference
      formula's hardcoded ``52.0`` does not -- the two totals stop matching and
      this test FAILS. Confirmed by replay: temporarily setting
      ``runners.py``'s ``WEEKS_PER_YEAR`` to ``26.0`` makes this exact test
      fail (``81596.82... != 81037.94...``), reverted immediately after (see
      task-9-report.md).
    - ``oracle_orders``'s ``order_cost``/``holding_rate``/``shortage_penalty_rate``
      defaults: ``score_run`` still hardcodes its own literal
      ``75.0``/``0.25``/``1.0`` as its keyword defaults, matching this test's
      reference formula's hardcoded values exactly, so mutating
      ``oracle_orders``'s defaults changes what the (real) oracle produces but
      changes neither side of THIS test's comparison -- this test stays green
      through that drift. It is instead
      ``test_oracle_and_scorer_share_the_same_cost_defaults`` (the
      ``inspect.signature``-based test below) that catches this class of
      drift.
    """
    w, outs = _run(_oracle, seed=9, weeks=20, n_skus=5)
    k = score_run(w, outs)
    assert k.total_cost == pytest.approx(_reference_oracle_style_total_cost(w, outs))


def test_oracle_and_scorer_share_the_same_cost_defaults():
    """The actual guard against the drift the docstring above disclaims:
    ``oracle_orders``'s (``runners.py``) default ``order_cost``,
    ``holding_rate`` and ``shortage_penalty_rate`` must equal ``score_run``'s
    (``scorer.py``) defaults for the same three parameters -- if they ever
    drift apart, the oracle stops being a genuine ceiling for what the scorer
    measures, which is the entire point of this module. Confirmed by replay:
    changing ``oracle_orders``'s ``order_cost`` default from ``75.0`` to
    ``175.0`` (with no matching change in ``score_run``) makes this test fail,
    even though every other test in this suite and in
    ``tests/test_simworld_runners.py`` stays fully green (see
    task-9-report.md).
    """
    oracle_params = inspect.signature(oracle_orders).parameters
    scorer_params = inspect.signature(score_run).parameters
    for name in ("order_cost", "holding_rate", "shortage_penalty_rate"):
        assert oracle_params[name].default == scorer_params[name].default, (
            f"{name}: oracle_orders default {oracle_params[name].default!r} != "
            f"score_run default {scorer_params[name].default!r}"
        )


def test_oracle_costs_less_than_naive_over_a_full_run():
    w_n, outs_n = _run(_naive)
    w_o, outs_o = _run(_oracle)
    assert score_run(w_o, outs_o).total_cost < score_run(w_n, outs_n).total_cost


# ---------------------------------------------------------------------------
# Bias metrics (AIM-Bench): bullwhip, anchoring, demand_chasing.
# ---------------------------------------------------------------------------

def test_bullwhip_of_a_constant_order_stream_is_near_zero():
    w, outs = _run(lambda wo, s, o: {sk.sku: 10.0 for sk in wo.skus})
    assert score_run(w, outs).bullwhip < 0.1


def test_bullwhip_hand_computed_pinned_ratio():
    """4-week, 1-sku synthetic sequence where orders are exactly 2x demand
    every week: var(orders) = 4*var(demand) by the scaling property of
    variance, so bullwhip must be exactly 4.0 -- not just "greater than 1"
    (which a formula using std instead of var, or an inverted ratio in the
    lucky direction, could also satisfy by coincidence). Discriminates
    against using ``np.std`` in place of ``np.var`` (would give 2.0 instead
    of 4.0) and against the inverted ratio var(demand)/var(orders) (would
    give 0.25 instead of 4.0). Confirmed by replay (see task-9-report.md).
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=2.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=1, otif=1.0)
    w = World(seed=0, weeks=4, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=np.zeros((1, 4)), real_lead_weeks=np.ones((1, 4), dtype=int))
    demand_vals = [10.0, 20.0, 30.0, 40.0]
    order_vals = [20.0, 40.0, 60.0, 80.0]
    outs = [
        WeekOutcome(week=i, demand=np.array([demand_vals[i]]), sold=np.array([demand_vals[i]]),
                    lost=np.array([0.0]), received=np.array([0.0]),
                    ordered=np.array([order_vals[i]]), on_hand_end=np.array([0.0]))
        for i in range(4)
    ]
    assert score_run(w, outs).bullwhip == pytest.approx(4.0)


def test_bullwhip_is_zero_when_demand_has_no_variance_even_if_orders_do():
    """Guards the ``if demand_var > 0 else 0.0`` branch directly. Demand is
    constant (var=0) while orders vary wildly -- without the guard this is a
    division by zero (inf or nan), not 0.0. Discriminates against a mutant
    that deletes the guard: confirmed by replay, the unguarded formula raises
    a numpy RuntimeWarning and returns ``inf`` for this exact fixture instead
    of 0.0 (see task-9-report.md).
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=2.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=1, otif=1.0)
    w = World(seed=0, weeks=3, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=np.zeros((1, 3)), real_lead_weeks=np.ones((1, 3), dtype=int))
    outs = [
        WeekOutcome(week=i, demand=np.array([10.0]), sold=np.array([10.0]),
                    lost=np.array([0.0]), received=np.array([0.0]),
                    ordered=np.array([float(i) * 50.0]), on_hand_end=np.array([0.0]))
        for i in range(3)
    ]
    assert score_run(w, outs).bullwhip == pytest.approx(0.0)


def test_anchoring_is_zero_when_orders_match_mean_demand():
    w, outs = _run(lambda wo, s, o: {})
    mean_d = float(np.stack([o.demand for o in outs], axis=1).sum(axis=0).mean())
    w2, outs2 = _run(lambda wo, s, o: {wo.skus[0].sku: mean_d})
    k = score_run(w2, outs2)
    assert k.anchoring < 0.05


def test_anchoring_hand_computed_pinned_value():
    """Directly against the helper: demand mean=20, orders mean=25 ->
    anchoring = |25-20|/20 = 0.25 exactly. Discriminates against a missing
    ``abs()`` (would still be +0.25 here since orders>demand, so this test
    alone would not catch a missing abs -- covered instead by the
    hand-computed Kpis fixture above, where orders<demand) and against
    dividing by ``orders.mean()`` instead of ``demand.mean()`` (would give
    5/25=0.2 instead of 0.25). Confirmed by replay (see task-9-report.md).
    """
    orders = np.array([25.0, 25.0, 25.0])
    demand = np.array([10.0, 20.0, 30.0])
    assert _anchoring(orders, demand) == pytest.approx(0.25)


def test_anchoring_is_zero_when_mean_demand_is_zero():
    """Guards the ``if mean_d <= 0: return 0.0`` branch. Without it, this is
    a division by zero. Confirmed by replay: the unguarded formula returns
    ``inf`` (or ``nan`` if orders.mean() is also 0) for this exact input
    instead of 0.0 (see task-9-report.md).
    """
    orders = np.array([5.0, 5.0])
    demand = np.array([0.0, 0.0])
    assert _anchoring(orders, demand) == pytest.approx(0.0)


def test_a_constant_order_stream_does_not_chase_demand():
    w, outs = _run(lambda wo, s, o: {sk.sku: 10.0 for sk in wo.skus})
    assert score_run(w, outs).demand_chasing == pytest.approx(0.0)


def test_ordering_exactly_last_weeks_sales_chases_demand_hard():
    """Deviation from the brief, found and resolved during TDD (not a formula
    bug): the brief's literal fixture uses ``_run``'s defaults
    (seed=4, weeks=30, n_skus=6). Running the echo policy against that exact
    fixture through the CURRENT ``world.py``/``scorer.py`` gives
    ``demand_chasing == 0.3586103451254904`` (fill_rate=0.767,
    unfulfilled_units=1018.0), well under the brief's asserted ``> 0.7`` --
    confirmed by direct measurement, not a typo in this test file.

    The reason is real, not a bug: the echo policy orders exactly last
    week's ACTUAL SOLD units, never last week's true demand. Once a stockout
    happens, sold < demand permanently (lost sales, no backorder, per
    ``world.advance``'s docstring), so the policy starts chasing an
    already-depleted signal instead of true demand -- and over 30 weeks of a
    trending-up ``seasonal_trend`` regime with real lead times running up to
    6-7 weeks for this seed, the shortfall compounds. The correlation this
    metric measures (order_t vs demand_{t-1}, not order_t vs sold_{t-1}) is
    therefore genuinely diluted by that compounding gap -- it is not
    something Task 9's formula gets wrong, it is a property of this
    particular seed/horizon combination that the brief's author evidently
    never ran before writing ``0.7`` into the spec.

    Swept seeds 1-40 at several horizons directly to confirm the metric DOES
    reach near-1.0 correlation once stockouts are negligible (sold ~= demand,
    so chasing last week's sold really is chasing last week's demand): at
    weeks=8, seed=11, fill_rate=0.9992 and demand_chasing=1.0 exactly. That
    is the fixture used below -- same echo policy, same intent as the brief,
    a horizon/seed pair verified (not assumed) to isolate the chasing
    pathology from the unrelated compounding-stockout effect.
    """
    def echo(w, st, outs):
        if not outs:
            return {}
        return {s.sku: float(outs[-1].sold[i]) for i, s in enumerate(w.skus)}

    w, outs = _run(echo, seed=11, weeks=8, n_skus=6)
    assert score_run(w, outs).demand_chasing > 0.7


def test_demand_chasing_hand_computed_perfect_correlation():
    """Directly against the helper: orders[1:] = [2,4,6,8] is an exact 2x
    linear function of demand[:-1] = [1,2,3,4], so the Pearson correlation is
    exactly 1.0 -- not just "greater than 0.7" (which a formula correlating
    the wrong pair of slices, e.g. orders[:-1] vs demand[:-1] -- same-week
    instead of lagged -- could also satisfy by coincidence on some inputs).
    The first entry of each array (999) is deliberately excluded by the
    slicing under test (orders[1:] drops index 0; demand[:-1] drops the last
    index) so it cannot contaminate the correlation. Discriminates against a
    mutant that correlates ``orders[:-1]`` with ``demand[:-1]`` (same-week,
    no lag): for this fixture that pairs [999,2,4,6] against [1,2,3,4],
    which is not a perfect correlation (confirmed by replay, r != 1.0; see
    task-9-report.md).
    """
    orders = np.array([999.0, 2.0, 4.0, 6.0, 8.0])
    demand = np.array([1.0, 2.0, 3.0, 4.0, 999.0])
    assert _demand_chasing(orders, demand) == pytest.approx(1.0)


def test_demand_chasing_is_zero_for_a_series_shorter_than_three_weeks():
    """Guards the ``if len(orders) < 3: return 0.0`` branch directly -- at
    len==1, not len==2. len==2 was tried FIRST and found NOT to discriminate
    (confirmed by replay): ``orders[1:]``/``demand[:-1]`` on a length-2 input
    are themselves length-1 slices, and a single-element array's ``.std()``
    is always exactly 0.0 (no variance possible), so the SEPARATE
    ``x.std() == 0 or y.std() == 0`` guard already short-circuits to 0.0
    regardless of whether the length guard exists -- removing the length
    guard left this test passing unchanged. At len==1, ``orders[1:]`` and
    ``demand[:-1]`` are both EMPTY arrays: an empty array's ``.std()`` is
    ``nan`` (not ``0.0``, confirmed directly), so the zero-variance guard
    does NOT fire, and without the length guard ``np.corrcoef`` on two empty
    arrays returns an all-NaN 2x2 matrix (with numpy RuntimeWarnings) --
    ``nan != 0.0`` fails this assertion outright. This is the genuinely
    load-bearing boundary. Confirmed by replay both ways (see
    task-9-report.md).
    """
    orders = np.array([5.0])
    demand = np.array([3.0])
    assert _demand_chasing(orders, demand) == pytest.approx(0.0)


def test_demand_chasing_is_zero_when_demand_has_no_variance():
    """Guards the ``y.std() == 0`` half of the zero-variance check
    specifically (as opposed to ``x.std() == 0``, already covered by the
    brief's constant-order-stream test above). Orders vary; demand is
    constant. Without this half of the guard, ``np.corrcoef`` divides by a
    zero standard deviation and returns NaN, which fails an ``== 0.0``
    comparison outright. Confirmed by replay (see task-9-report.md).
    """
    orders = np.array([1.0, 9.0, 3.0, 20.0])
    demand = np.array([4.0, 4.0, 4.0, 4.0])
    assert _demand_chasing(orders, demand) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# capture: the normalized metric between floor and ceiling.
# ---------------------------------------------------------------------------

def test_capture_is_zero_at_the_floor_and_one_at_the_ceiling():
    assert capture(floor_cost=100.0, kern_cost=100.0, oracle_cost=50.0) == pytest.approx(0.0)
    assert capture(floor_cost=100.0, kern_cost=50.0, oracle_cost=50.0) == pytest.approx(1.0)
    assert capture(floor_cost=100.0, kern_cost=75.0, oracle_cost=50.0) == pytest.approx(0.5)


def test_capture_can_be_negative_when_worse_than_the_floor():
    assert capture(floor_cost=100.0, kern_cost=120.0, oracle_cost=50.0) < 0.0


def test_capture_above_one_raises_because_the_oracle_is_unreachable():
    """Pins the load-bearing behavior: beating the oracle must raise loudly,
    never clamp to 1.0 or silently return a number > 1. Confirmed by replay:
    a mutant that clamps with ``min(value, 1.0)`` instead of raising makes
    this test fail (it returns 1.0 without raising) -- see task-9-report.md.
    """
    with pytest.raises(ValueError, match="oraculo"):
        capture(floor_cost=100.0, kern_cost=40.0, oracle_cost=50.0)


def test_capture_with_a_degenerate_span_raises():
    """floor_cost == oracle_cost must raise with "span" in the message, not
    silently produce inf/nan. Confirmed by replay: a mutant that removes the
    ``span <= 0`` guard makes this test fail with a ZeroDivisionError instead
    of the expected ValueError (see task-9-report.md).
    """
    with pytest.raises(ValueError, match="span"):
        capture(floor_cost=50.0, kern_cost=50.0, oracle_cost=50.0)


def test_capture_raises_on_nan_input_instead_of_returning_nan_silently():
    """``capture``'s own docstring commits to failing loud, never silent
    ("Falla ruidosa, no silenciosa"). Before this guard, a NaN input passed
    both the ``span <= 0`` and ``value > 1.0`` comparisons -- both evaluate
    to ``False`` for NaN under IEEE 754 -- so neither raise fired and the
    function returned ``nan`` in silence. Confirmed by replay: without the
    ``math.isfinite`` guard, this exact call returns ``nan`` instead of
    raising (see task-9-report.md).
    """
    with pytest.raises(ValueError, match="finit"):
        capture(floor_cost=float("nan"), kern_cost=50.0, oracle_cost=40.0)


def test_capture_with_an_inverted_span_also_raises():
    """Not in the brief. The floor must cost MORE than the oracle for the
    span to be meaningful; if the floor somehow costs LESS than the oracle
    (span < 0, not just == 0), that is just as degenerate as span == 0 and
    must raise too, not silently produce a nonsensical negative-over-negative
    ratio that could coincidentally land in a plausible range. Discriminates
    against a mutant that only guards ``span == 0`` (e.g. ``if span == 0:``
    instead of ``if span <= 0:``): confirmed by replay, that mutant lets this
    exact call through and returns a finite (but meaningless) number instead
    of raising (see task-9-report.md).
    """
    with pytest.raises(ValueError, match="span"):
        capture(floor_cost=40.0, kern_cost=30.0, oracle_cost=50.0)


# ---------------------------------------------------------------------------
# Task 10: degradation -- per-period attribution of excess cost into the part
# Kern announced and the part it ate in silence.
#
# The property this task exists to protect: attribution is PER-WEEK, using
# the SAME-SEED clean (level 0) vs dirty (level N) weekly cost arrays, never
# a global total-vs-total comparison. The excess of a single week can be
# NEGATIVE (dirt occasionally makes a week cheaper by accident) and must be
# summed with its sign, never clamped to zero -- clamping would silently
# inflate the reported "silent" drop, exactly the class of bug this project
# has a documented history of catching (PR #193's safety-stock NaN-to-zero
# collapse).
#
# Every test below was confirmed by replay: the implementation was mutated
# the specific way the docstring names, the test was re-run and observed to
# fail, then the mutation was reverted and the test re-confirmed passing.
# ---------------------------------------------------------------------------

def test_no_extra_cost_means_no_drop():
    """Identical clean/dirty weekly costs -> zero drop, zero silence, no
    matter what the announced flags say. This is the simplest possible
    sanity fixture (from the brief) and does not by itself discriminate much,
    but it pins the zero-excess boundary explicitly rather than assuming it
    falls out of the other tests.
    """
    clean = np.array([10.0, 10.0, 10.0])
    d = degradation(clean, clean.copy(), [False, False, False], level=2)
    assert d.drop == pytest.approx(0.0)
    assert d.silent == pytest.approx(0.0)
    assert d.announced == pytest.approx(0.0)
    assert d.weeks_announced == 0
    assert d.level == 2


def test_all_the_extra_cost_in_announced_weeks_leaves_zero_silence():
    """All the excess cost falls in the one week Kern flagged -> announced
    equals the full drop and silent is exactly zero. Discriminates against a
    mutant that always attributes 100% of the excess to ``silent`` regardless
    of the flags (e.g. a stub that ignores ``announced_weeks`` entirely):
    confirmed by replay, forcing ``announced = 0.0`` unconditionally makes
    ``d.announced == pytest.approx(20.0)`` fail (got 0.0) and
    ``d.silent == pytest.approx(0.0)`` fail (got 20.0); reverted after.
    """
    clean = np.array([10.0, 10.0, 10.0])
    dirty = np.array([10.0, 30.0, 10.0])
    d = degradation(clean, dirty, [False, True, False], level=2)
    assert d.drop == pytest.approx(20.0)
    assert d.announced == pytest.approx(20.0)
    assert d.silent == pytest.approx(0.0)
    assert d.weeks_announced == 1


def test_extra_cost_in_a_silent_week_is_counted_as_silence():
    """Mirror of the test above with the flag turned off: the same excess
    cost, unflagged, must land entirely in ``silent``. Discriminates against
    a mutant that always attributes 100% of the excess to ``announced``
    regardless of the flags (e.g. treating every week as announced):
    confirmed by replay, forcing ``announced = drop`` unconditionally makes
    ``d.silent == pytest.approx(20.0)`` fail (got 0.0) and
    ``d.announced == pytest.approx(0.0)`` fail (got 20.0); reverted after.
    """
    clean = np.array([10.0, 10.0, 10.0])
    dirty = np.array([10.0, 30.0, 10.0])
    d = degradation(clean, dirty, [False, False, False], level=3)
    assert d.silent == pytest.approx(20.0)
    assert d.announced == pytest.approx(0.0)
    assert d.weeks_announced == 0


def test_a_negative_week_is_summed_with_its_sign_not_clipped():
    """Two weeks, +20 and -8: the correct signed sum is 12, never 28 (which
    an ``abs()``-based sum would give) and never 20 (which a per-week
    ``max(0, excess)`` clamp would give, discarding the -8 entirely).
    Discriminates against BOTH of those wrong implementations at once.
    Confirmed by replay: mutating ``drop = float(excess.sum())`` to
    ``drop = float(np.abs(excess).sum())`` made this fail with 28.0 != 12.0;
    mutating it to ``drop = float(np.maximum(excess, 0.0).sum())`` made it
    fail with 20.0 != 12.0. Both reverted after.
    """
    clean = np.array([10.0, 10.0])
    dirty = np.array([30.0, 2.0])   # +20 and -8
    d = degradation(clean, dirty, [False, False], level=3)
    assert d.drop == pytest.approx(12.0)


def test_a_negative_total_drop_is_reported_as_negative_not_zero():
    """Not in the brief. Dirt can make EVERY week cheaper on net, so the
    total ``drop`` itself can be negative -- and must be reported as such,
    not floored at zero. This is a distinct case from the mixed-sign test
    above (there the total was still positive); here the total itself is
    negative. Discriminates against a mutant that clamps the final ``drop``
    with ``max(0.0, ...)`` at the end (as opposed to per-week): confirmed by
    replay, wrapping the return's ``drop=drop`` in ``max(0.0, drop)`` made
    ``d.drop == pytest.approx(-8.0)`` fail (got 0.0); reverted after.
    """
    clean = np.array([10.0, 10.0])
    dirty = np.array([5.0, 7.0])   # -5 and -3
    d = degradation(clean, dirty, [False, False], level=1)
    assert d.drop == pytest.approx(-8.0)


def test_silent_can_be_negative_when_unannounced_weeks_are_net_cheaper():
    """Not in the brief. Kern announces during the one costly week (+20) but
    a DIFFERENT, unflagged week happens to get cheaper (-10) by accident.
    ``silent`` must reflect that net-negative contribution from the
    unannounced weeks (-10), not be floored at zero. Discriminates against a
    mutant that clamps ``silent`` with ``max(0.0, drop - announced)``:
    confirmed by replay, that mutant made ``d.silent == pytest.approx(-10.0)``
    fail (got 0.0) while ``d.drop``/``d.announced`` stayed correct; reverted
    after. Also discriminates against a mutant using
    ``silent = abs(drop - announced)``: confirmed by replay, that mutant
    produced ``10.0`` instead of ``-10.0``; reverted after.
    """
    clean = np.array([100.0, 100.0])
    dirty = np.array([120.0, 90.0])   # +20 (announced), -10 (silent)
    d = degradation(clean, dirty, [True, False], level=2)
    assert d.drop == pytest.approx(10.0)
    assert d.announced == pytest.approx(20.0)
    assert d.silent == pytest.approx(-10.0)


def test_announced_sum_uses_signed_excess_even_when_an_announced_week_is_cheaper():
    """Not in the brief. Both weeks are flagged announced, but one of them
    is a net-negative (cheaper) week. ``announced`` must be the SIGNED sum
    (+20 + -15 = 5), not the sum of absolute values (35). Discriminates
    against a mutant using ``announced = float(np.abs(excess)[flags].sum())``:
    confirmed by replay, that mutant produced ``35.0`` instead of ``5.0``
    for this exact fixture; reverted after.
    """
    clean = np.array([50.0, 50.0])
    dirty = np.array([70.0, 35.0])   # +20 and -15, both announced
    d = degradation(clean, dirty, [True, True], level=1)
    assert d.drop == pytest.approx(5.0)
    assert d.announced == pytest.approx(5.0)
    assert d.silent == pytest.approx(0.0)


def test_per_week_attribution_on_a_five_week_mixed_sign_fixture_pins_exact_split():
    """The test this whole task's design most needs: a synthetic, hand-
    computable per-week cost sequence where the flags do NOT follow a
    trivial pattern (not all-True, not all-False, not one-flag-in-three),
    specifically so that an off-by-one error reading the WRONG week's
    ``announced`` flag against a given week's excess produces a DIFFERENT
    number than the correct one -- not a coincidentally-matching one.

    excess = dirty - clean = [40, -5, 20, -2, 10]
    flags  =                 [T,   F,  T,  F,  T]   (weeks 0, 2, 4 announced)

    Hand computation:
      drop            = 40 - 5 + 20 - 2 + 10 = 63
      announced (0,2,4) = 40 + 20 + 10       = 70
      silent (1,3)      = -5 + -2            = -7
      drop - announced  = 63 - 70            = -7  (matches silent, as it must
                                                       by definition -- this is
                                                       a consistency check, not
                                                       the discriminating one)

    Discriminates against an off-by-one that reads ``announced_weeks``
    shifted by one position relative to ``excess`` (e.g. ``np.roll(flags,
    -1)``, attributing week t's excess using week t+1's flag, wrapping
    around): confirmed by replay, that mutant computes ``announced`` over
    indices {1, 3, 4} instead of {0, 2, 4}, giving
    ``-5 + -2 + 10 == 3.0`` instead of the correct ``70.0`` -- a very
    different number, not a coincidental near-match. Reverted after.
    """
    clean = np.array([10.0, 10.0, 10.0, 10.0, 10.0])
    dirty = np.array([50.0, 5.0, 30.0, 8.0, 20.0])
    flags = [True, False, True, False, True]
    d = degradation(clean, dirty, flags, level=3)
    assert d.drop == pytest.approx(63.0)
    assert d.announced == pytest.approx(70.0)
    assert d.silent == pytest.approx(-7.0)
    assert d.weeks_announced == 3


def test_degradation_is_frozen():
    """Per the global constraint, ``Degradation`` must be ``frozen=True``
    matching the rest of this module's dataclasses (``Kpis``). Discriminates
    against a mutant that drops ``frozen=True`` from the ``@dataclass``
    decorator: confirmed by replay, that mutant lets the attribute
    assignment below succeed silently instead of raising
    ``dataclasses.FrozenInstanceError``; reverted after.
    """
    clean = np.array([10.0, 10.0])
    d = degradation(clean, clean.copy(), [False, False], level=0)
    assert isinstance(d, Degradation)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.drop = 999.0


def test_mismatched_lengths_raise():
    """``clean_weekly`` and ``dirty_weekly`` of different length must raise
    before any per-week arithmetic happens. This is NOT just a defensive
    nicety: numpy's broadcasting rules silently accept a length-1 array
    against a length-2 one (``(1,)`` broadcasts against ``(2,)``), so without
    an explicit shape check, ``dirty - clean`` does not raise at all -- it
    silently produces a wrong-but-finite answer. Confirmed by replay:
    removing the ``clean.shape != dirty.shape`` guard made this exact call
    return ``Degradation(level=1, drop=-1.0, announced=0.0, silent=-1.0,
    weeks_announced=0)`` instead of raising (``DID NOT RAISE ValueError``);
    reverted after.
    """
    with pytest.raises(ValueError, match="largo"):
        degradation(np.array([1.0, 2.0]), np.array([1.0]), [False, False], level=1)


def test_mismatched_announced_length_raises():
    """``announced_weeks`` must have exactly one flag per week, and must
    raise the module's own explicit, documented ``ValueError`` (matching
    "largo") rather than whatever numpy happens to do internally. Confirmed
    by replay: removing this length-check guard does NOT silently truncate
    as might be assumed -- numpy's boolean indexing raises its own
    ``IndexError`` ("boolean index did not match indexed array along axis 0")
    instead. That is still a bug relative to this function's contract: an
    ``IndexError`` with no "largo" in its message does not satisfy
    ``pytest.raises(ValueError, match="largo")``, so this exact test fails
    (with the ``IndexError`` propagating instead) when the guard is removed;
    reverted after.
    """
    with pytest.raises(ValueError, match="largo"):
        degradation(np.array([1.0, 2.0]), np.array([1.0, 2.0]), [False], level=1)


def test_zero_length_weeks_raise():
    """``degradation`` must agree with this same module's ``weekly_cost`` at
    the zero-week boundary (see
    ``test_weekly_cost_and_score_run_agree_on_empty_outcomes_by_both_raising``):
    "cero semanas" is a real caller error, not a valid answer -- and here the
    invalid answer would be especially misleading. Before this guard, this
    exact call silently returned ``Degradation(level=0, drop=0.0,
    announced=0.0, silent=0.0, weeks_announced=0)`` -- "Kern degraded not at
    all and hid nothing" -- instead of raising. Confirmed by replay: removing
    the zero-length guard makes the assertion below fail with "DID NOT RAISE
    ValueError" (the all-zeros ``Degradation`` is returned instead); reverted
    after.
    """
    with pytest.raises(ValueError, match="no hay semanas"):
        degradation(np.array([]), np.array([]), [], level=0)
