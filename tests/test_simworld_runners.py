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
from src.simworld.runners import Policy, apply_policy, naive_policy, oracle_orders
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


def _world_and_history(weeks=12, n_skus=4, seed=5):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(4):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, st, outs


def test_naive_policy_covers_five_weeks_of_the_last_four_weeks_average():
    w, _, outs = _world_and_history()
    pol = naive_policy(w, outs)
    sku0 = w.skus[0].sku
    avg = float(np.mean([o.sold[0] for o in outs[-4:]]))
    assert pol.reorder_point[sku0] == pytest.approx(5.0 * avg)


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
    w, st, outs = _world_and_history()
    pol = Policy(
        reorder_point={s.sku: 500.0 for s in w.skus},
        order_up_to={s.sku: 500.0 for s in w.skus},
        announced=False, note="",
    )
    first = apply_policy(w, st, pol)
    st2, _ = advance(w, st, first)
    second = apply_policy(w, st2, pol)
    assert sum(second.values()) < sum(first.values())


def test_oracle_orders_exactly_the_demand_of_its_real_lead_window():
    w = generate_world(seed=9, n_skus=3, n_suppliers=1, weeks=20, regime="seasonal_trend")
    st = initial_state(w)
    orders = oracle_orders(w, st)
    lead = int(w.real_lead_weeks[0, 0])
    window = w.demand[0, 0:lead].sum()
    expected = max(0.0, window - st.on_hand[0])
    assert orders.get(w.skus[0].sku, 0.0) == pytest.approx(expected)


def test_oracle_uses_the_real_lead_time_not_the_supplier_promise():
    """Not in the brief. Replaying a mutant that reads
    ``world.suppliers[sku.supplier_idx].promised_lead_weeks`` instead of
    ``world.real_lead_weeks[i, t]`` showed it PASSES the brief's own oracle
    test unchanged: at seed=9, sku 0, t=0, the real lead happens to equal the
    promised lead (2 == 2) by chance of the RNG draw, so that single-seed
    check cannot tell the two data sources apart. This constructs a tiny
    synthetic World where the two are deliberately set apart, to pin that the
    oracle reads the ground truth and not the supplier's promise.
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=2.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=2, otif=1.0)
    demand = np.array([[10.0, 10.0, 10.0, 10.0, 10.0]])
    real_lead = np.array([[4, 4, 4, 4, 4]])  # always 4, promised is 2 - deliberately different
    w = World(seed=0, weeks=5, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)
    st = WorldState(week=0, on_hand=np.array([0.0]), in_transit=())
    orders = oracle_orders(w, st)
    # window must be the REAL lead (4 weeks of demand -> 40), not the promised (2 -> 20)
    assert orders["SKU-X"] == pytest.approx(40.0)


def test_oracle_counts_its_own_pipeline_so_it_does_not_reorder_the_same_window():
    """Not in the brief. Replaying a mutant that reads only
    ``state.on_hand`` (ignoring ``state.in_transit``) in ``oracle_orders``
    shows it passes every brief-given oracle test, INCLUDING
    ``test_oracle_beats_naive_on_lost_sales_over_a_full_run`` -- over-ordering
    can only reduce lost sales, never increase them, so a lost-sales-only
    metric cannot tell a genuinely cost-minimizing oracle apart from one that
    floods the pipeline with redundant orders every week within its own lead
    window. That is exactly the "not truly cost-minimizing ceiling" failure
    this task's property section calls Critical. Uses a fully controlled
    synthetic World (constant demand, constant 3-week lead) so the second
    week's order is pinned exactly, not just "less than" the first.
    """
    sku = Sku(sku="SKU-X", unit_cost=1.0, price=2.0, supplier_idx=0)
    supplier = Supplier(name="P", promised_lead_weeks=3, otif=1.0)
    demand = np.full((1, 10), 10.0)
    real_lead = np.full((1, 10), 3, dtype=int)
    w = World(seed=0, weeks=10, regime="seasonal_trend", skus=(sku,), suppliers=(supplier,),
              demand=demand, real_lead_weeks=real_lead)
    st = WorldState(week=0, on_hand=np.array([0.0]), in_transit=())

    first = oracle_orders(w, st)
    assert first["SKU-X"] == pytest.approx(30.0)  # 3 weeks x 10 units, nothing on hand

    st2, _ = advance(w, st, first)
    second = oracle_orders(w, st2)
    # the week-0 order (30) has not arrived yet (lead=3) but is fully in the
    # pipeline, and it exactly covers weeks 1-3's demand (also 30) -> no
    # further order should be placed this week.
    assert second.get("SKU-X", 0.0) == pytest.approx(0.0)


def test_oracle_beats_naive_on_lost_sales_over_a_full_run():
    w = generate_world(seed=4, n_skus=8, n_suppliers=2, weeks=40, regime="seasonal_trend")

    def run(strategy):
        st = initial_state(w)
        outs, lost = [], 0.0
        for _ in range(w.weeks):
            orders = strategy(w, st, outs)
            st, out = advance(w, st, orders)
            outs.append(out)
            lost += float(out.lost.sum())
        return lost

    naive_lost = run(lambda wo, s, o: apply_policy(wo, s, naive_policy(wo, o)) if len(o) >= 4 else {})
    oracle_lost = run(lambda wo, s, o: oracle_orders(wo, s))
    assert oracle_lost < naive_lost
