"""Task 6: JobResult exposes the tool's structured report object to in-process
callers (the src/simworld/ harness) — populated only on the success path, and
never leaked onto a serialized/HTTP surface.

Task 7 (appended below): the floor (naive fixed reorder point) and the ceiling
(oracle with perfect foresight) runners that will compete against Kern in
Task 9's capture metric. Filename is inherited from Task 6's brief, which
chose this path for its own (unrelated) JobResult.report tests.
"""

import inspect

import numpy as np
import pandas as pd
import pytest

from scm_agent.orchestrator import Orchestrator
from scm_agent.types import STATUS_NEEDS_DATA, STATUS_OK, JobResult
from src.simworld.runners import Policy, _oracle_order_for_sku, apply_policy, naive_policy, oracle_orders
from src.simworld.world import Sku, Supplier, World, WorldState, advance, generate_world, initial_state

pytest.importorskip("fastapi")
# /api/jobs is a multipart Form route; importing webapp.app raises without
# python-multipart installed. Same guard as tests/test_webapp.py: try the
# canonical name first (python-multipart >= 0.0.26) to avoid the
# PendingDeprecationWarning on the legacy `import multipart` alias.
try:
    import python_multipart  # noqa: F401

    _HAS_MULTIPART = True
except ImportError:
    try:
        import multipart  # noqa: F401

        _HAS_MULTIPART = True
    except ImportError:
        _HAS_MULTIPART = False

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as webapp_app_module  # noqa: E402
import webapp.mcp_server as mcp_server_module  # noqa: E402
from webapp.app import app  # noqa: E402

requires_multipart = pytest.mark.skipif(not _HAS_MULTIPART, reason="python-multipart not installed")

client = TestClient(app)


def _demand_csv(tmp_path):
    rows = []
    for week in range(20):
        for sku in ("SKU-1", "SKU-2"):
            rows.append({
                "date": pd.Timestamp("2025-01-06") + pd.Timedelta(weeks=week),
                "product_id": sku,
                "quantity": 10 + week % 5,
                "unit_cost": 12.0,
                "lead_time_days": 14,
            })
    csv_path = tmp_path / "demand.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


def test_jobresult_report_defaults_to_none():
    r = JobResult(status="ok", tool=None, confidence=1.0, deliverables={}, summary="")
    assert r.report is None


def test_orchestrator_populates_report_on_success(tmp_path):
    csv = _demand_csv(tmp_path)

    orch = Orchestrator(clients_root=None)
    result = orch.run(
        "set reorder points and safety stock for these SKUs",
        data_path=str(csv),
        job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )
    assert result.status == STATUS_OK, result.summary
    assert result.report is not None
    assert hasattr(result.report, "recommendations")

    recs = result.report.recommendations
    assert recs, "expected at least one per-SKU recommendation"
    for rec in recs:
        assert rec.product_id
        assert rec.reorder_point is not None
        # (s,Q) carries order_quantity, (R,S) carries order_up_to — every
        # recommendation must carry at least one of the two policy numbers
        # the harness reads.
        assert rec.order_quantity is not None or rec.order_up_to is not None


def test_orchestrator_leaves_report_none_on_a_failing_path(tmp_path):
    """The success/failure asymmetry is load-bearing: a later task uses
    `result.report is None` to decide whether Kern produced a usable policy at
    all. An implementation that populates `report` on every return path would
    pass the happy-path test above but must fail here — a request with no data
    file fails with needs_data, and JobResult.report must stay None.
    """
    orch = Orchestrator(clients_root=None)
    result = orch.run(
        "set reorder points and safety stock for these SKUs",
        job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )
    assert result.status == STATUS_NEEDS_DATA, result.summary
    assert result.report is None


@requires_multipart
def test_webapp_payload_does_not_leak_the_report(tmp_path):
    """Behavioral proof, not just a source grep: build a real /api/jobs
    response for a request that succeeds (so JobResult.report is populated,
    per the test above) and confirm the field that actually ships over HTTP
    never carries it.
    """
    csv = _demand_csv(tmp_path)
    with open(csv, "rb") as fh:
        r = client.post(
            "/api/jobs",
            data={
                "brief": "set reorder points and safety stock for these SKUs",
                "job_type": "inventory_optimization",
            },
            files={"file": ("demand.csv", fh, "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "report" not in body


def test_webapp_source_never_serializes_jobresult_wholesale():
    """Cheap backstop on top of the behavioral test above (runs even without
    python-multipart installed): neither webapp/app.py nor webapp/mcp_server.py
    ever calls asdict() on the whole JobResult — only on result.guided, which
    is itself always JSON-safe.
    """
    for module in (webapp_app_module, mcp_server_module):
        src = inspect.getsource(module)
        assert "asdict(result)" not in src


def _world_and_history(weeks=16, n_skus=4, seed=5, n_advance=10):
    """Advances ``n_advance`` weeks (default 10, not the original 4) with no
    orders ever placed, so the on-hand stock ``initial_state`` seeds (~4
    weeks of average demand) genuinely runs out partway through and a real
    stockout (``sold != demand``) is exercised for every test in this module
    that consumes this fixture's ``st``/``outs`` -- not just the naive
    lookback test below.
    """
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(n_advance):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, st, outs


def test_naive_policy_covers_five_weeks_of_the_last_four_weeks_average():
    """Not decorative: with only 4 fixture weeks, ``outs[-4:]`` WAS the
    entire history, so the original version of this test could not tell
    ``NAIVE_LOOKBACK_WEEKS=4`` apart from ``=99``/"all history" -- and every
    fixture week sold exactly what was demanded (no stockout), so it could
    not tell ``sold`` from ``demand`` either. The 10-week fixture above
    genuinely stocks out for every sku from week 4 onward (seed=5, n_skus=4:
    sku0 sells [33, 27, 37, 37, 0, 0, 0, 0, 0, 0] against demand
    [33, 27, 37, 37, 36, 32, 38, 34, 33, 46] -- confirmed by direct
    simulation, not derived here). The last 4 *sold* weeks (indices 6-9) are
    therefore genuinely zero: a hand-computed literal (0.0), not the
    implementation's own ``np.mean`` formula recomputed inline. The
    all-history average (13.4) is very different from the last-4 average
    (0.0), so a ``NAIVE_LOOKBACK_WEEKS=99``/unbounded mutant would compute a
    nonzero reorder point here and fail.
    """
    w, _, outs = _world_and_history()
    pol = naive_policy(w, outs)
    sku0 = w.skus[0].sku
    assert [round(float(o.sold[0]), 1) for o in outs] == [
        33.0, 27.0, 37.0, 37.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    ]
    assert pol.reorder_point[sku0] == pytest.approx(0.0)


def test_naive_policy_never_announces():
    w, _, outs = _world_and_history()
    assert naive_policy(w, outs).announced is False


def test_apply_policy_orders_up_to_the_level_when_below_the_reorder_point():
    w, st, outs = _world_and_history()
    sku0 = w.skus[0].sku
    pol = Policy(
        reorder_point={s.sku: 1e9 for s in w.skus},
        order_up_to={s.sku: 1e9 for s in w.skus},
        announced=False, note="",
    )
    orders = apply_policy(w, st, pol)
    assert orders[sku0] > 0


def test_apply_policy_orders_when_position_is_exactly_at_the_reorder_point():
    """Not in the brief; added after replaying a `<` vs `<=` mutation of
    ``apply_policy`` and finding every brief-given test still passed for this
    seed (both boundary cases happened to land at 0 or above the reorder
    point, never exactly on it by construction). The docstring says "al o por
    debajo" (at or below), so position == reorder_point must still trigger an
    order — this pins that boundary directly instead of relying on an
    incidental seed value.
    """
    w, st, outs = _world_and_history()
    rop = 50.0
    pol = Policy(
        reorder_point={s.sku: rop for s in w.skus},
        order_up_to={s.sku: rop + 10.0 for s in w.skus},
        announced=False, note="",
    )
    at_rop_state = WorldState(week=st.week, on_hand=np.full(len(w.skus), rop), in_transit=())
    orders = apply_policy(w, at_rop_state, pol)
    sku0 = w.skus[0].sku
    assert orders[sku0] == pytest.approx(10.0)


def test_apply_policy_does_not_order_between_reorder_point_and_order_up_to():
    """Not in the brief. Every Policy the brief's own tests construct sets
    ``order_up_to == reorder_point``, so a mutant that deletes the
    ``if position <= rop`` gate entirely (replacing it with an unconditional
    ``qty = target - position``) passes every brief-given test unchanged —
    confirmed by actual replay, not reasoning. That mutant would break the
    (s, S) hysteresis apply_policy's docstring promises: a position strictly
    between the reorder point and the order-up-to level must NOT trigger an
    order, only a position at or below the reorder point does.
    """
    w, st, outs = _world_and_history()
    pol = Policy(
        reorder_point={s.sku: 50.0 for s in w.skus},
        order_up_to={s.sku: 100.0 for s in w.skus},
        announced=False, note="",
    )
    between_state = WorldState(week=st.week, on_hand=np.full(len(w.skus), 70.0), in_transit=())
    assert apply_policy(w, between_state, pol) == {}


def test_apply_policy_orders_nothing_when_well_stocked():
    w, st, outs = _world_and_history()
    pol = Policy(
        reorder_point={s.sku: 0.0 for s in w.skus},
        order_up_to={s.sku: 0.0 for s in w.skus},
        announced=False, note="",
    )
    assert apply_policy(w, st, pol) == {}


def test_apply_policy_counts_in_transit_so_it_does_not_double_order():
    """Not decorative: the original version only asserted
    ``sum(second) < sum(first)``, which a half-pipeline-credit bug also
    satisfies (replay: on_hand=20, in_transit=130 credited at 50% gives
    position 85 <= rop 100, so it still orders 65 -- less than the first
    order's 130, so the old assertion passes even though 65 is wrong).
    Pins the exact quantities instead, on a synthetic ``WorldState`` with a
    known ``on_hand`` and a known ``in_transit`` amount, the same style as
    ``test_oracle_uses_the_real_lead_time_not_the_supplier_promise``.
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=2.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=2, otif=1.0)
    w = World(seed=0, weeks=5, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=np.zeros((1, 5)), real_lead_weeks=np.full((1, 5), 2, dtype=int))
    pol = Policy(reorder_point={"SKU-X": 100.0}, order_up_to={"SKU-X": 150.0},
                 announced=False, note="")

    # position = 20 + 0 = 20 <= rop(100) -> order up to 150 -> exactly 130.
    st1 = WorldState(week=0, on_hand=np.array([20.0]), in_transit=())
    first = apply_policy(w, st1, pol)
    assert first["SKU-X"] == pytest.approx(130.0)

    # Same on_hand, but the 130 units just ordered are now known to be in
    # transit: position = 20 + 130 = 150 == order_up_to exactly -> no second
    # order at all. A half-credit bug would see position 85 (<=100) and
    # order 65; an ignored-pipeline bug would see position 20 and order 130
    # again -- the literal double-order this test is named for.
    st2 = WorldState(week=1, on_hand=np.array([20.0]), in_transit=((0, 2, 130.0),))
    second = apply_policy(w, st2, pol)
    assert second == {}


def test_oracle_orders_the_hand_computed_optimum_when_ordering_is_obviously_worth_it():
    """Task 7 MILP rewrite, test 1/8. 1 SKU, 4 weeks, lead=1 (an order placed
    in week t arrives in week t+1 -- matching how ``src/simworld/world.py``'s
    ``advance`` actually works: it credits ``received`` (arrivals) BEFORE
    appending the newly-placed order to ``in_transit``, so a ``lead=0`` order
    in this harness's real physics arrives the NEXT week, not the same week.
    A ``lead=0`` fixture would silently assert a value under physics the real
    world never produces -- ``generate_world`` only ever samples
    ``promised_lead_weeks in {1, 2, 3, 4}``, and slip only adds more -- so
    ``lead=1`` is the smallest lead this harness can actually realize),
    constant demand of 10/week, margin=99 (price 100, unit_cost 1) -- selling
    is overwhelmingly profitable relative to the tiny weekly holding cost
    (1 * 0.25 / 52 = ~0.0048/unit-week) and the $75 fixed order cost.

    With lead=1, nothing can arrive before week 1, so week 0's demand (10
    units) is an unavoidable total loss no matter what is ordered -- a fixed
    cost across every candidate plan below, so it does not affect which plan
    is cheapest. The real decision is how to cover weeks 1-3 (30 units total)
    using the 3 orderable weeks (0, 1, 2, arriving 1, 2, 3 respectively).
    Hand-enumerated alternatives (exhaustive over the economically sane
    candidates): ordering 10 at each of the 3 orderable weeks pays the $75
    fixed cost 3 times (225 total) with zero holding (each order arrives just
    in time and sells out immediately); ordering all 30 at week 0 pays the
    $75 fixed cost once (75 total) plus trivial holding (20 units held
    through week 1's close + 10 units held through week 2's close =
    0.0048 * 30 = ~0.14) for total ~75.14 -- far cheaper than 225. Every
    intermediate split (e.g. 20 then 10) lands strictly between those two.
    So consolidating into a single week-0 order of the full 30 units is the
    genuine cost-minimizing plan, not just "order enough" -- confirmed by
    direct replay of the shipped MILP, not derived by hand alone.

    Discriminates against a mutant that drops the fixed order-cost term
    (``c[off_y:off_y + n_y] = order_cost`` deleted/zeroed): with no
    consolidation incentive, the solver has no reason to buy ahead of need
    and returns 10.0 (just enough to cover week 1, the earliest coverable
    week) instead of 30.0 -- confirmed by replay (see task-7-report.md).
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=100.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=1, otif=1.0)
    demand = np.full((1, 4), 10.0)
    real_lead = np.full((1, 4), 1, dtype=int)
    w = World(seed=0, weeks=4, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)
    st = WorldState(week=0, on_hand=np.array([0.0]), in_transit=())
    orders = oracle_orders(w, st)
    assert orders["SKU-X"] == pytest.approx(30.0)


def test_oracle_accepts_a_shortage_when_covering_it_costs_more_than_it_saves():
    """Task 7 MILP rewrite, test 2/8. Reference scenario from the brief:
    unit_cost=5, price=10 (margin == unit_cost, the accounting break-even
    point for this cost model -- buying a unit to avoid losing it costs
    exactly as much as the shortage penalty it avoids, before even counting
    holding or the fixed order cost), demand constant 10/week, lead=3,
    on_hand=0. Covering the shortfall would require holding inventory for
    multiple weeks (real cost) and paying the $75 fixed order fee, for zero
    net accounting benefit -- so the genuinely cost-minimizing plan orders
    NOTHING and accepts the loss. This is the exact property this whole task
    revision exists to protect: a heuristic that always "covers the window"
    would order the full 40 units (10/week x 4 weeks to the arrival week)
    here regardless of whether it pays -- that is precisely what the OLD
    myopic ``oracle_orders`` did (confirmed by replay against this scenario
    before the rewrite: it returned 40.0, not 0.0).

    Discriminates against a mutant that uses ``price`` instead of ``margin``
    for the shortage-avoidance coefficient (``c[off_s:...] = -price`` instead
    of ``-margin``) -- a very plausible real bug (conflating "the value of a
    sale" with "the value of NOT losing a sale that was already going to
    happen"). That mutant returns 70.0 for this exact scenario instead of 0.0
    -- confirmed by replay (see task-7-report.md).
    """
    sku = Sku(sku="SKU-Z", unit_cost=5.0, price=10.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=3, otif=1.0)
    demand = np.full((1, 10), 10.0)
    real_lead = np.full((1, 10), 3, dtype=int)
    w = World(seed=0, weeks=10, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)
    st = WorldState(week=0, on_hand=np.array([0.0]), in_transit=())
    orders = oracle_orders(w, st)
    assert orders == {}


def test_oracle_orders_less_when_the_pipeline_already_covers_future_demand():
    """Task 7 MILP rewrite, test 3/8. 1 SKU, 6 weeks, lead=1, constant demand
    of 20/week, on_hand0=5 (too little to cover even week 0), margin=18
    (price 20, unit_cost 2) -- comfortably worth ordering.

    Without any committed pipeline, week 0 is an unavoidable partial
    shortfall (nothing can arrive before week 1 regardless of what is
    ordered), and the cost-minimizing plan consolidates the rest of the
    horizon's demand (weeks 1-5, 100 units) into a single week-0 order of
    100 (one $75 fixed fee beats several, same reasoning as the hand-computed
    test above) -- confirmed by replay.

    With a 100-unit shipment already committed to arrive in week 2
    (``in_transit=((0, 2, 100.0),)``), that pipeline alone covers weeks 2-5
    (80 units) with 20 to spare -- the ONLY genuine gap left is week 1 (20
    units, since week 0's on_hand (5) is exhausted by week 0's own demand and
    the pipeline doesn't land until week 2). The cost-minimizing order is
    therefore exactly 20, not 100 -- pinning both numbers, not just
    asserting "less than", so a bug that gets the DIRECTION right but the
    MAGNITUDE wrong still fails.

    Discriminates against a mutant that drops the ``committed_receipt`` term
    from the inventory-balance constraint (i.e. the MILP never learns about
    ``in_transit`` at all): the with-pipeline case would then also return
    100.0, identical to the no-pipeline case -- confirmed by replay (see
    task-7-report.md).
    """
    sku = Sku(sku="SKU-P", unit_cost=2.0, price=20.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=1, otif=1.0)
    demand = np.full((1, 6), 20.0)
    real_lead = np.full((1, 6), 1, dtype=int)
    w = World(seed=0, weeks=6, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)

    st_no_pipeline = WorldState(week=0, on_hand=np.array([5.0]), in_transit=())
    without_pipeline = oracle_orders(w, st_no_pipeline)
    assert without_pipeline["SKU-P"] == pytest.approx(100.0)

    st_with_pipeline = WorldState(week=0, on_hand=np.array([5.0]), in_transit=((0, 2, 100.0),))
    with_pipeline = oracle_orders(w, st_with_pipeline)
    assert with_pipeline["SKU-P"] == pytest.approx(20.0)
    assert with_pipeline["SKU-P"] < without_pipeline["SKU-P"]


def test_oracle_uses_the_real_lead_time_not_the_supplier_promise():
    """Task 7 MILP rewrite, test 4/8 (same intent as the pre-MILP test this
    replaces, re-derived for the new cost model). 1 SKU, 5 weeks, constant
    demand 10/week, margin=99 (price 100, unit_cost 1, same "obviously worth
    covering" economics as the hand-computed test above), promised_lead=2 but
    real_lead=4 (deliberately different).

    With the REAL lead (4), a week-0 order is the only one that can possibly
    arrive within the 5-week horizon (arrival = 0+4 = 4 < 5; every later
    week's order would arrive at or past week 5, outside the horizon and
    therefore worthless) -- so the only week that can ever be covered is week
    4, and the cost-minimizing order is exactly 10.0 (that week's demand).
    Weeks 0-3 are an unavoidable total loss no matter what is ordered.

    If the implementation instead read the supplier's PROMISED lead (2), weeks
    0, 1, and 2 would all become orderable (arrivals 2, 3, 4, all < 5), which
    is a structurally different and much larger MILP with a different optimal
    week-0 order -- so this does not merely tweak a number, it changes which
    decision variables even exist. Discriminates against a mutant that reads
    ``world.suppliers[sku.supplier_idx].promised_lead_weeks`` instead of
    ``world.real_lead_weeks[i]`` -- confirmed by replay to return a different,
    non-10.0 quantity (see task-7-report.md for the exact value observed).
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=100.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=2, otif=1.0)
    demand = np.full((1, 5), 10.0)
    real_lead = np.full((1, 5), 4, dtype=int)  # always 4, promised is 2 - deliberately different
    w = World(seed=0, weeks=5, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)
    st = WorldState(week=0, on_hand=np.array([0.0]), in_transit=())
    orders = oracle_orders(w, st)
    assert orders["SKU-X"] == pytest.approx(10.0)


def test_oracle_orders_boundary_cases_without_crashing_or_going_negative():
    """Task 7 MILP rewrite, test 5/8. Three edges in one test, all cheap:

    1. ``state.week >= world.weeks`` returns ``{}`` immediately (the
       "simulation already ended" guard, unchanged from the pre-MILP oracle).
    2. The last week of the horizon with any positive lead has NO orderable
       week at all (``m == 0`` inside ``_oracle_order_for_sku``: an order
       placed now cannot arrive before the horizon ends), so that SKU is
       correctly absent from the returned dict instead of crashing or
       returning a bogus 0.0 entry.
    3. A broader sweep of several generated worlds/weeks never produces a
       negative order quantity from ``_oracle_order_for_sku`` (the per-SKU
       solver call), called directly rather than through ``oracle_orders``:
       ``oracle_orders`` gates its dict with ``if qty > 0``, which would
       trivially satisfy "never negative" on the PUBLIC return value even
       with a broken clamp (a tiny negative slips under a ``> 0`` check just
       as readily as it would under a correct one) -- confirmed by replay,
       this exact test PASSED against a clamp-removing mutant when written
       against ``oracle_orders``' output, and only started discriminating
       once rewritten against the private helper directly. This is not a
       vacuous check even so: the raw MILP solution can carry ~1e-14-scale
       negative floating-point noise (HiGHS returning e.g. -1.42e-14 instead
       of exactly 0.0 for a should-be-zero variable) -- confirmed at seeds 6,
       7, and 14 below (n_skus=5, weeks=10); seeds 1-5 at this same fixture
       size never happened to trigger it, which is why this sweep pins the
       specific seeds known to reproduce it rather than an arbitrary range.
       The clamp is load-bearing, not decorative; a mutant that removes it
       fails this sweep (see task-7-report.md).
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=100.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=1, otif=1.0)
    demand = np.full((1, 5), 10.0)
    real_lead = np.full((1, 5), 1, dtype=int)
    w = World(seed=0, weeks=5, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)

    st_past = WorldState(week=5, on_hand=np.array([0.0]), in_transit=())
    assert oracle_orders(w, st_past) == {}

    st_last = WorldState(week=4, on_hand=np.array([0.0]), in_transit=())
    assert oracle_orders(w, st_last) == {}

    for seed in (6, 7, 14):
        world = generate_world(seed=seed, n_skus=5, n_suppliers=2, weeks=10, regime="seasonal_trend")
        state = initial_state(world)
        for _ in range(world.weeks):
            for i, sku in enumerate(world.skus):
                in_transit_i = [
                    (arrival, qty) for sku_idx, arrival, qty in state.in_transit if sku_idx == i
                ]
                raw_qty = _oracle_order_for_sku(
                    demand_full=world.demand[i],
                    real_lead_full=world.real_lead_weeks[i],
                    unit_cost=sku.unit_cost,
                    price=sku.price,
                    on_hand0=float(state.on_hand[i]),
                    in_transit=in_transit_i,
                    t0=state.week,
                    horizon=world.weeks,
                    order_cost=75.0,
                    holding_rate=0.25,
                    shortage_penalty_rate=1.0,
                )
                assert raw_qty >= 0.0
            orders = oracle_orders(world, state)
            state, _ = advance(world, state, orders)


def test_oracle_orders_does_not_mutate_world_or_state():
    """Task 7 MILP rewrite, test 6/8. Same established pattern as
    ``test_lens_never_mutates_outcomes_or_real_lead_weeks`` and the
    ``advance`` mutation check in ``tests/test_simworld_world.py``: snapshot
    the ndarrays the MILP reads, call it, and confirm nothing shifted. The
    MILP slices ``demand``/``real_lead_weeks`` and wraps each slice in
    ``np.asarray(..., dtype=...)``; since both arrays already carry a
    matching dtype, ``np.asarray`` returns the SAME buffer rather than a
    copy -- confirmed with ``np.shares_memory`` -- so an in-place write to
    the local ``lead``/``demand`` variables inside ``_oracle_order_for_sku``
    (e.g. a defensive-looking ``lead[:] = 0`` or a clip) would silently
    corrupt ``world.real_lead_weeks``/``world.demand`` for every other
    runner sharing this World this week. Confirmed by replay: a mutant doing
    exactly that fails this test (see task-7-report.md).
    """
    w = generate_world(seed=3, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    st = WorldState(week=2, on_hand=w.demand[:, 0].copy(), in_transit=((1, 4, 15.0), (3, 5, 8.0)))

    demand_before = w.demand.copy()
    real_lead_before = w.real_lead_weeks.copy()
    on_hand_before = st.on_hand.copy()
    in_transit_before = st.in_transit

    oracle_orders(w, st)

    assert np.array_equal(w.demand, demand_before)
    assert np.array_equal(w.real_lead_weeks, real_lead_before)
    assert np.array_equal(st.on_hand, on_hand_before)
    assert st.in_transit == in_transit_before


def _oracle_total_cost(world, outcomes, *, order_cost=75.0, holding_rate=0.25,
                        shortage_penalty_rate=1.0):
    """Local stand-in for Task 9's ``src/simworld/scorer.py`` (``weekly_cost``/
    ``score_run``), which does not exist yet. Deliberately the same formula
    documented in the plan for that module (procurement + holding + shortage +
    fixed order cost) so this test measures the same thing Task 9 will --
    written here, not imported, per the brief."""
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


def test_oracle_beats_naive_on_total_cost_across_a_small_seed_sweep():
    """Task 7 MILP rewrite, test 7/8. This is the property that replaces the
    old lost-sales sweep (deleted above along with
    ``KNOWN_MYOPIC_WINDOW_GAP_SEEDS``): a genuinely cost-minimizing oracle is
    validated against COST, not lost units -- a correct oracle sometimes
    accepts more lost units than the naive floor when doing so is cheaper
    (see the shortage-acceptance test above), so a lost-sales invariant is
    the wrong one to assert here.

    Scoped at 5 seeds, 8 SKUs, 24 weeks -- not the controller's validated
    25-seed/8-SKU/40-week sweep, but wide enough to still catch a real
    regression. A smaller scope (4 SKUs, 16 weeks) was tried first and found
    NOT to discriminate: replaying the ORIGINAL myopic-window oracle (the
    design this MILP replaced, which genuinely lost to naive on cost in some
    worlds) through this exact test's own cost metric produced 0 losses at
    4-SKU/16-week, and still 0 losses even at 15 seeds with the same
    n_skus/weeks -- so that smaller scope would silently pass even if a
    future change accidentally reverted the oracle to a myopic design. At
    8 SKUs/24 weeks the old myopic oracle loses to naive on seed 1 -- this
    scope actually catches the regression it exists to guard against.
    Confirmed by direct replay to run in a few seconds for this fixture size
    (not sub-second any more, but still fast enough for a unit test).

    Discriminates against a mutant that drops the ``committed_receipt`` term
    (the same pipeline-crediting bug the dedicated pipeline test above
    targets in isolation): at this seed range it causes genuinely worse cost
    outcomes, not just a different single-call number -- confirmed by replay
    at the ORIGINAL 4-SKU/16-week scope, the oracle LOSES to naive on seeds 2
    and 4 of that exact sweep once that term is dropped (naive/oracle:
    seed 2 = 31710/43482, seed 4 = 86613/88768; see task-7-report.md),
    because redundant re-ordering within an already-covered pipeline window
    inflates procurement, holding, and fixed order costs simultaneously.
    """
    def run(world, strategy):
        st = initial_state(world)
        outs = []
        for _ in range(world.weeks):
            orders = strategy(world, st, outs)
            st, out = advance(world, st, orders)
            outs.append(out)
        return outs

    def naive_s(wo, s, o):
        return apply_policy(wo, s, naive_policy(wo, o)) if len(o) >= 4 else {}

    def oracle_s(wo, s, o):
        return oracle_orders(wo, s)

    losers = {}
    for seed in range(1, 6):
        w = generate_world(seed=seed, n_skus=8, n_suppliers=2, weeks=24, regime="seasonal_trend")
        naive_cost = _oracle_total_cost(w, run(w, naive_s))
        oracle_cost = _oracle_total_cost(w, run(w, oracle_s))
        if oracle_cost > naive_cost:
            losers[seed] = (naive_cost, oracle_cost)

    assert not losers, (
        f"oracle failed to beat-or-tie naive on TOTAL COST for seeds "
        f"(seed: (naive_cost, oracle_cost)): {losers}"
    )


def test_oracle_orders_is_deterministic_across_repeated_calls():
    """Task 7 MILP rewrite, test 8/8. Same ``world``/``state``, called
    repeatedly -- verified empirically rather than assumed, per the brief:
    the "same seed, same world" reproducibility invariant this whole harness
    depends on would break silently if the solver were ever nondeterministic
    across repeated solves of the same problem.

    Discriminates directly against a mutant that adds any per-call
    randomness to the returned quantity (confirmed by replay: 5 calls in a
    row without the mutant are bit-identical for this fixture; with a
    corrupting mutant added after the solve, two calls differ on every SKU).
    A milder, more realistic mutant was also tried and is worth recording
    as a real finding, not just a passing check: adding a tiny (1e-9) random
    perturbation to the MILP's objective coefficients -- the kind of
    tie-breaking noise a solver-level source of nondeterminism might look
    like -- did NOT change the result for this fixture across 5 repeated
    calls. That is reassuring (this instance's optimum is not on a knife's
    edge) but is evidence about THIS fixture, not a proof that HiGHS itself
    is deterministic across all inputs/platforms/thread counts -- see
    task-7-report.md for the concern as stated to the controller.
    """
    w = generate_world(seed=7, n_skus=5, n_suppliers=2, weeks=14, regime="seasonal_trend")
    st = WorldState(week=3, on_hand=w.demand[:, 0].copy(), in_transit=((2, 6, 12.0),))

    first = oracle_orders(w, st)
    second = oracle_orders(w, st)
    assert first == second
