from __future__ import annotations

from examples.run_world_sim import simulate
from scm_agent.events import EventLedger
from scm_agent.monitors import run_all_monitors
from src.simworld.runners import Policy, kern_policy, naive_policy
from src.simworld.world import generate_world
from src.state.store import StateStore
from src.state.system_state import history
from src.verify.backtest import match_decision_actuals, run_forecast_backtest

EMPTY = Policy(reorder_point={}, order_up_to={}, announced=False, note="", status="ok")


def _kern_plan(agent_dir, seed):
    def plan(world, outcomes, week):
        if len(outcomes) < 4:
            return EMPTY, False
        pol = kern_policy(world, outcomes, level=0, seed=seed,
                          out_dir=agent_dir / f"w{week}", forced=True)
        return pol, pol.announced
    return plan


def test_simulate_writes_all_four_domains_when_a_state_store_is_given(tmp_path):
    world = generate_world(seed=7, n_skus=4, n_suppliers=1, weeks=12, regime="seasonal_trend")
    store = StateStore(tmp_path / "state")

    simulate(world, _kern_plan(tmp_path / "agent", seed=7), replan_every=4, state_store=store)

    assert len(history("stock", store=store)) == 12
    assert len(history("outcomes", store=store)) == 12
    assert len(history("decisions", store=store)) == 12
    assert len(history("forecast", store=store)) >= 1


def test_the_three_general_consumers_return_non_empty_against_the_bridge(tmp_path):
    world = generate_world(seed=7, n_skus=4, n_suppliers=1, weeks=12, regime="seasonal_trend")
    store = StateStore(tmp_path / "state")

    simulate(world, _kern_plan(tmp_path / "agent", seed=7), replan_every=4, state_store=store)

    events = run_all_monitors(store=store, ledger=EventLedger(":memory:"))
    forecast_results = run_forecast_backtest(store=store)
    decision_obs = match_decision_actuals(
        history("decisions", store=store), history("outcomes", store=store),
        decision_value_column="order_qty",
    )

    assert len(events) > 0
    assert len(forecast_results) > 0
    assert len(decision_obs) > 0


def test_state_store_none_does_not_change_existing_behavior():
    world = generate_world(seed=3, n_skus=3, n_suppliers=1, weeks=8, regime="seasonal_trend")

    def naive_plan(w, outs, week):
        if len(outs) < 4:
            return EMPTY, False
        return naive_policy(w, outs), False

    outs_default, flags_default = simulate(world, naive_plan, replan_every=4)
    outs_none, flags_none = simulate(world, naive_plan, replan_every=4, state_store=None)

    assert [o.on_hand_end.tolist() for o in outs_default] == [o.on_hand_end.tolist() for o in outs_none]
    assert flags_default == flags_none
