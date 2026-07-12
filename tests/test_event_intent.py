"""Tests for event -> tool routing (Linchpin 3.0 PR-4, F0 -- scm_agent/event_intent.py).

Guarantees under test (plan S4, "Criterio de aceptacion F0"):
- load_routing() parses the real config/event_routing.yaml into Route
  objects, and rejects a malformed routing table loudly (missing 'routes',
  a route missing required keys, an invalid autonomy_tier) instead of
  silently misrouting later;
- every route in the real config file names a tool that is actually
  registered in build_default_registry() -- "ruteo como dato" only works if
  the data is honest;
- resolve_route() finds the configured Route for a known event type and
  raises EventRoutingError for an unrouted one;
- inventory_from_stock_event() builds inventory_optimization's params from a
  stock_below_rop event's payload (data_path, client, a whitelisted set of
  overrides) and raises EventRoutingError when 'data_path' is missing;
- build_params() raises EventRoutingError for an unknown param_builder;
- END TO END (the F0 acceptance criterion, literally): a synthetic
  stock_below_rop Event, routed via the real config/event_routing.yaml, runs
  the real inventory_optimization tool through the real orchestrator's
  prepare->run->qa->deliver pipeline against synthetic demand data, produces
  a QA-gated JobResult (status=ok, not qa_failed/error), and notify() is
  invoked (monkeypatched -- no real webhook is ever touched);
- a non-ok result (e.g. needs_data) never triggers a notification.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scm_agent import event_intent as event_intent_module
from scm_agent import llm, tools
from scm_agent.event_intent import (
    DEFAULT_ROUTING_PATH,
    EventRoutingError,
    Route,
    build_params,
    excess_obsolete_from_state_stock_event,
    handle_event,
    inventory_from_state_stock_event,
    inventory_from_stock_event,
    load_routing,
    resolve_route,
)
from scm_agent.events import Event
from scm_agent.orchestrator import Orchestrator
from scm_agent.types import STATUS_OK

PORTFOLIO = "data/sample_demand_portfolio.csv"  # same fixture tests/test_scm_agent.py uses


def _rop_event(*, sku: str = "SKU-A", data_path: str = PORTFOLIO, extra_payload: dict | None = None) -> Event:
    payload = {"on_hand": 12.0, "reorder_point": 20.0, "data_path": data_path}
    if extra_payload:
        payload.update(extra_payload)
    return Event(
        type="stock_below_rop", severity="high", source="monitors",
        dedup_key=f"{sku}:stock_below_rop", sku=sku, payload=payload,
    )


def _test_orchestrator() -> Orchestrator:
    # Deterministic (no LLM narrative rewriting) + no client-profile lookup --
    # matching handle_event()'s own default trust boundary for system-
    # initiated (non-authenticated-client) runs. Mirrors tests/test_scm_agent.py's
    # _rules_orch() helper.
    return Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(), clients_root=None)


# -- load_routing(): parses the real config file, rejects malformed ones ----


def test_load_routing_parses_the_real_config_file():
    routes = load_routing(DEFAULT_ROUTING_PATH)

    assert "stock_below_rop" in routes
    route = routes["stock_below_rop"]
    assert route.tool == "inventory_optimization"
    assert route.param_builder == "inventory_from_stock_event"
    assert route.autonomy_tier in ("T1", "T2", "T3")


def test_load_routing_routes_to_a_tool_that_actually_exists_in_the_registry():
    """The whole point of 'ruteo como dato': the YAML's tool name must be a
    real key in build_default_registry(), not a typo nobody caught."""
    routes = load_routing(DEFAULT_ROUTING_PATH)
    registry_keys = {t.key for t in tools.build_default_registry().list()}

    for route in routes.values():
        assert route.tool in registry_keys


def test_load_routing_includes_the_pr5_monitor_routes():
    """PR-5 (scm_agent/monitors.py) adds 3 new routes alongside stock_below_rop
    -- rop_breach/stockout_projected -> inventory_optimization,
    excess_growing -> excess_obsolete -- each with its own state-rows param
    builder, WITHOUT touching the protected stock_below_rop route."""
    routes = load_routing(DEFAULT_ROUTING_PATH)

    assert routes["stock_below_rop"].param_builder == "inventory_from_stock_event"  # unchanged

    assert routes["rop_breach"].tool == "inventory_optimization"
    assert routes["rop_breach"].param_builder == "inventory_from_state_stock_event"
    assert routes["stockout_projected"].tool == "inventory_optimization"
    assert routes["stockout_projected"].param_builder == "inventory_from_state_stock_event"
    assert routes["excess_growing"].tool == "excess_obsolete"
    assert routes["excess_growing"].param_builder == "excess_obsolete_from_state_stock_event"


def test_default_routing_path_falls_back_to_a_repo_relative_config_path():
    # Same convention as jobs/scheduler.py's DEFAULT_JOBSTORE_PATH: an unset
    # env var falls back to a repo-relative default.
    assert DEFAULT_ROUTING_PATH.replace("\\", "/") == "config/event_routing.yaml"


def test_load_routing_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_routing(tmp_path / "does_not_exist.yaml")


def test_load_routing_rejects_missing_routes_key(tmp_path):
    path = tmp_path / "routing.yaml"
    path.write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(EventRoutingError, match="routes"):
        load_routing(path)


def test_load_routing_rejects_a_route_that_is_not_a_mapping(tmp_path):
    path = tmp_path / "routing.yaml"
    path.write_text("routes:\n  stock_below_rop: just_a_string\n", encoding="utf-8")

    with pytest.raises(EventRoutingError, match="mapping"):
        load_routing(path)


def test_load_routing_rejects_a_route_missing_required_keys(tmp_path):
    path = tmp_path / "routing.yaml"
    path.write_text("routes:\n  stock_below_rop:\n    tool: inventory_optimization\n", encoding="utf-8")

    with pytest.raises(EventRoutingError, match="missing"):
        load_routing(path)


def test_load_routing_rejects_an_invalid_autonomy_tier(tmp_path):
    path = tmp_path / "routing.yaml"
    path.write_text(
        "routes:\n"
        "  stock_below_rop:\n"
        "    tool: inventory_optimization\n"
        "    param_builder: inventory_from_stock_event\n"
        "    autonomy_tier: T9\n",
        encoding="utf-8",
    )

    with pytest.raises(EventRoutingError, match="autonomy_tier"):
        load_routing(path)


def test_load_routing_accepts_all_three_valid_tiers(tmp_path):
    for tier in ("T1", "T2", "T3"):
        path = tmp_path / f"routing_{tier}.yaml"
        path.write_text(
            "routes:\n"
            "  some_event:\n"
            "    tool: inventory_optimization\n"
            "    param_builder: inventory_from_stock_event\n"
            f"    autonomy_tier: {tier}\n",
            encoding="utf-8",
        )
        assert load_routing(path)["some_event"].autonomy_tier == tier


# -- resolve_route() ----------------------------------------------------------


def _routes() -> dict[str, Route]:
    return {
        "stock_below_rop": Route(
            event_type="stock_below_rop", tool="inventory_optimization",
            param_builder="inventory_from_stock_event", autonomy_tier="T2",
        ),
    }


def test_resolve_route_finds_the_configured_route():
    route = resolve_route(_rop_event(), _routes())
    assert route.tool == "inventory_optimization"


def test_resolve_route_raises_for_an_unrouted_event_type():
    unrouted = Event(type="no_such_type", severity="low", source="monitors", dedup_key="x")

    with pytest.raises(EventRoutingError, match="no_such_type"):
        resolve_route(unrouted, _routes())


# -- inventory_from_stock_event() param builder --------------------------------


def test_inventory_from_stock_event_builds_data_path_and_client():
    params = inventory_from_stock_event(_rop_event())

    assert params["data_path"] == PORTFOLIO
    assert params["client"] == "Tower"
    assert "SKU-A" in params["brief"]


def test_inventory_from_stock_event_uses_payload_client_when_present():
    params = inventory_from_stock_event(_rop_event(extra_payload={"client": "Acme"}))
    assert params["client"] == "Acme"


def test_inventory_from_stock_event_only_forwards_known_override_keys():
    event = _rop_event(extra_payload={"service_level": 0.9, "lead_time_days": 10, "junk_key": "nope"})
    params = inventory_from_stock_event(event)

    # on_hand/reorder_point (the condition data, always present) and junk_key
    # (not a real inventory_optimization param) must NOT leak into overrides.
    assert params["overrides"] == {"service_level": 0.9, "lead_time_days": 10}


def test_inventory_from_stock_event_raises_without_data_path():
    event = Event(
        type="stock_below_rop", severity="high", source="monitors", dedup_key="SKU-A:stock_below_rop",
        sku="SKU-A", payload={"on_hand": 12.0, "reorder_point": 20.0},
    )
    with pytest.raises(EventRoutingError, match="data_path"):
        inventory_from_stock_event(event)


def test_inventory_from_stock_event_labels_the_brief_generically_without_a_sku():
    event = Event(type="stock_below_rop", severity="high", source="monitors",
                  dedup_key="k", payload={"data_path": PORTFOLIO})
    params = inventory_from_stock_event(event)
    assert "flagged SKU" in params["brief"]


# -- inventory_from_state_stock_event() param builder (PR-5) -------------------


def _stock_row_event(*, event_type="rop_breach", sku="SKU-A", extra_payload: dict | None = None) -> Event:
    row = {"product_id": sku, "on_hand": 15.0, "reorder_point": 20.0, "avg_daily_demand": 1.0}
    payload = {"rows": [row]}
    if extra_payload:
        payload.update(extra_payload)
    return Event(type=event_type, severity="medium", source="monitors",
                 dedup_key=f"{sku}:{event_type}", sku=sku, payload=payload)


def test_inventory_from_state_stock_event_writes_a_synthetic_demand_csv():
    params = inventory_from_state_stock_event(_stock_row_event())

    assert params["client"] == "Tower"
    assert "SKU-A" in params["brief"]
    data_path = Path(params["data_path"])
    assert data_path.exists()
    written = pd.read_csv(data_path)
    assert set(written.columns) >= {"date", "product_id", "quantity"}
    assert (written["product_id"] == "SKU-A").all()
    assert (written["quantity"] == 1.0).all()  # avg_daily_demand replayed flat
    assert len(written) == 28  # _SYNTHETIC_HISTORY_DAYS


def test_inventory_from_state_stock_event_only_forwards_known_override_keys():
    event = _stock_row_event(extra_payload={"service_level": 0.9, "junk_key": "nope"})
    params = inventory_from_state_stock_event(event)
    assert params["overrides"] == {"service_level": 0.9}


def test_inventory_from_state_stock_event_raises_without_rows():
    event = Event(type="rop_breach", severity="medium", source="monitors", dedup_key="k", payload={})
    with pytest.raises(EventRoutingError, match="rows"):
        inventory_from_state_stock_event(event)


# -- excess_obsolete_from_state_stock_event() param builder (PR-5) -------------


def test_excess_obsolete_from_state_stock_event_writes_the_stock_rows_directly():
    row = {"product_id": "SKU-A", "on_hand": 260.0, "reorder_point": 10.0, "avg_daily_demand": 2.0}
    event = Event(type="excess_growing", severity="medium", source="monitors",
                  dedup_key="SKU-A:excess_growing", sku="SKU-A", payload={"rows": [row]})

    params = excess_obsolete_from_state_stock_event(event)

    assert params["client"] == "Tower"
    data_path = Path(params["data_path"])
    assert data_path.exists()
    written = pd.read_csv(data_path)
    assert written.loc[0, "product_id"] == "SKU-A"
    assert written.loc[0, "on_hand"] == 260.0
    assert written.loc[0, "daily_demand"] == 2.0  # renamed from avg_daily_demand
    assert "avg_daily_demand" not in written.columns


def test_excess_obsolete_from_state_stock_event_raises_without_rows():
    event = Event(type="excess_growing", severity="medium", source="monitors", dedup_key="k", payload={})
    with pytest.raises(EventRoutingError, match="rows"):
        excess_obsolete_from_state_stock_event(event)


# -- build_params(): unknown param_builder -------------------------------------


def test_build_params_raises_for_an_unknown_param_builder():
    bad_route = Route(event_type="stock_below_rop", tool="inventory_optimization",
                       param_builder="does_not_exist", autonomy_tier="T2")

    with pytest.raises(EventRoutingError, match="does_not_exist"):
        build_params(_rop_event(), bad_route)


# -- handle_event(): the F0 acceptance criterion, end to end ------------------


def test_handle_event_routes_a_synthetic_stock_below_rop_event_end_to_end(tmp_path, monkeypatch):
    """THE F0 ACCEPTANCE CRITERION (plan S4): a synthetic stock_below_rop
    Event -> routed via the real config/event_routing.yaml -> the real
    inventory_optimization tool runs against synthetic demand data through
    the real orchestrator -> a QA-gated JobResult with status=ok -> notify()
    invoked (asserted via monkeypatch, never a real webhook)."""
    captured = {}

    def fake_notify(message, **kwargs):
        captured["message"] = message
        return True

    monkeypatch.setattr(event_intent_module, "notify", fake_notify)
    event = _rop_event()

    routed = handle_event(
        event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path,
    )

    assert routed.route.tool == "inventory_optimization"
    assert routed.route.autonomy_tier == "T2"
    assert routed.result.status == STATUS_OK
    assert routed.result.tool == "inventory_optimization"
    assert routed.result.qa_issues == []  # QA gate passed -- no issues on a real ok result
    assert "excel" in routed.result.deliverables
    assert Path(routed.result.deliverables["excel"]).exists()
    assert routed.notified is True
    assert "SKU-A" in captured["message"]
    assert "T2" in captured["message"]
    assert "stock_below_rop" in captured["message"]


def test_handle_event_does_not_notify_on_a_non_ok_status(tmp_path, monkeypatch):
    """No deliverable => no notification (the plan's QA veto, rule 2, applies
    to event-triggered runs exactly as it does to brief-driven ones). A
    data_path pointing at a nonexistent file makes prepare() short-circuit
    to needs_data before QA is ever reached."""
    called = {"n": 0}

    def fake_notify(message, **kwargs):
        called["n"] += 1
        return True

    monkeypatch.setattr(event_intent_module, "notify", fake_notify)
    event = _rop_event(data_path=str(tmp_path / "does_not_exist.csv"))

    routed = handle_event(
        event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path,
    )

    assert routed.result.status != STATUS_OK
    assert routed.notified is False
    assert called["n"] == 0


def test_handle_event_raises_for_an_unrouted_event_type(tmp_path):
    event = Event(type="no_such_event", severity="low", source="monitors", dedup_key="k")

    with pytest.raises(EventRoutingError, match="no_such_event"):
        handle_event(event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path)


def test_handle_event_raises_for_a_route_naming_an_unregistered_tool(tmp_path):
    bad_routes = {
        "stock_below_rop": Route(
            event_type="stock_below_rop", tool="not_a_real_tool",
            param_builder="inventory_from_stock_event", autonomy_tier="T2",
        ),
    }

    with pytest.raises(EventRoutingError, match="not_a_real_tool"):
        handle_event(_rop_event(), routes=bad_routes, orchestrator=_test_orchestrator(), out_dir=tmp_path)


def test_handle_event_uses_default_routing_path_when_routes_not_supplied(tmp_path, monkeypatch):
    """Exercises the routes=None -> load_routing(DEFAULT_ROUTING_PATH) path --
    the same one a real zero-config caller hits."""
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)

    routed = handle_event(_rop_event(), orchestrator=_test_orchestrator(), out_dir=tmp_path)

    assert routed.result.status == STATUS_OK


# -- handle_event(): PR-5's new state-rows-driven routes, end to end ----------


def test_handle_event_routes_a_synthetic_rop_breach_event_end_to_end(tmp_path, monkeypatch):
    """A rop_breach event carrying event.payload["rows"] (no data_path at all)
    -> routed via inventory_from_state_stock_event's synthesized demand CSV
    -> the real inventory_optimization tool -> a QA-gated JobResult."""
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)
    event = _stock_row_event()

    routed = handle_event(event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path)

    assert routed.route.tool == "inventory_optimization"
    assert routed.result.status == STATUS_OK
    assert routed.result.qa_issues == []


def test_handle_event_routes_a_synthetic_excess_growing_event_end_to_end(tmp_path, monkeypatch):
    """An excess_growing event carrying event.payload["rows"] -> routed via
    excess_obsolete_from_state_stock_event -> the real excess_obsolete tool
    -> a QA-gated JobResult."""
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)
    row = {"product_id": "SKU-A", "on_hand": 260.0, "reorder_point": 10.0, "avg_daily_demand": 2.0}
    event = Event(type="excess_growing", severity="medium", source="monitors",
                  dedup_key="SKU-A:excess_growing", sku="SKU-A", payload={"rows": [row]})

    routed = handle_event(event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path)

    assert routed.route.tool == "excess_obsolete"
    assert routed.result.status == STATUS_OK
    assert routed.result.qa_issues == []
