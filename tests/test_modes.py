"""Tests for the Inventory vs SCM operating-mode split."""
from __future__ import annotations

from scm_agent.modes import (
    INVENTORY,
    MODES,
    SCM,
    build_registry,
    get_mode,
    orchestrator_for,
)
from scm_agent.tools import build_default_registry


def test_scm_mode_exposes_every_tool():
    full = build_default_registry()
    scm_reg = build_registry(SCM, full)
    assert {t.key for t in scm_reg.list()} == {t.key for t in full.list()}


def test_inventory_mode_excludes_leadership():
    keys = {t.key for t in build_registry(INVENTORY).list()}
    assert "inventory_optimization" in keys
    assert "leadership_chain" not in keys


def test_inventory_is_strict_subset_of_scm():
    inv = {t.key for t in build_registry(INVENTORY).list()}
    scm = {t.key for t in build_registry(SCM).list()}
    assert inv and inv < scm


def test_get_mode_resolves_and_defaults_to_superset():
    assert get_mode("inventory") is INVENTORY
    assert get_mode("SCM") is SCM           # case-insensitive
    assert get_mode(None) is SCM            # empty -> superset
    assert get_mode("nonsense") is SCM      # unknown -> safe superset


def test_includes_helper_and_future_tools():
    assert INVENTORY.includes("inventory_optimization")
    assert not INVENTORY.includes("leadership_chain")
    assert SCM.includes("leadership_chain")
    assert SCM.includes("some_future_tool")  # superset admits tools added later
    assert not INVENTORY.includes("some_future_tool")  # inventory stays narrow


def test_modes_have_distinct_personas_and_catalogues():
    assert INVENTORY.persona != SCM.persona
    assert INVENTORY.deliverables and INVENTORY.kpis
    assert SCM.deliverables and SCM.kpis
    assert len(SCM.deliverables) > len(INVENTORY.deliverables)
    assert set(MODES) == {"inventory", "scm"}


def test_orchestrator_for_mode_is_scoped():
    orch = orchestrator_for(INVENTORY)
    keys = {t.key for t in orch.registry.list()}
    assert "leadership_chain" not in keys
    assert "inventory_optimization" in keys


def test_inventory_tool_keys_match_advertised_deliverables():
    """INVENTORY.deliverables is a promise about what a brief routed through this
    mode can produce; tool_keys must actually reach every one of them, and expose
    nothing that isn't promised (mirrors test_scope_matches_monetization_brief's
    exact-set pinning in test_packages.py, which anchors the same tools to the
    commercial Starter package)."""
    required_for_deliverables = {
        "inventory_optimization",  # policy doc + reorder-point/safety-stock model
        "abc_xyz",                 # ABC-XYZ classification + per-segment policy
        "cycle_count",              # stock reconciliation / cycle-count plan
        "reconciliation",           # stock reconciliation / cycle-count plan (IRA)
        "excess_obsolete",          # E&O / dead-stock report
        "forecast",                 # demand forecast package
        "financial_kpis",           # inventory KPI dashboard
        "excel_replenishment",      # purchase-order / replenishment plan
    }
    assert set(INVENTORY.tool_keys) == required_for_deliverables

    reg = build_default_registry()
    for key in INVENTORY.tool_keys:
        reg.get(key)  # KeyError => tool_keys drifted from the registry
