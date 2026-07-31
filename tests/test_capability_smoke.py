"""Capability guardrail -- run the end-to-end smoke harness as part of the suite
so a regression that breaks any tool's prepare->run->QA->deliver pipeline (or
makes it crash on bad input) fails CI, not just a manual run.

Kept deliberately thin: the harness (examples/run_capability_smoke.py) owns the
fixtures and the run logic; this only asserts the invariants.
"""

from __future__ import annotations

import json
from pathlib import Path

from examples.run_capability_smoke import _NETWORK, main
from scm_agent.tools import build_default_registry

# Expected bucket per tool for the default (offline, non-stress) run. 47 registered tools.
#
# This replaces a `passes >= 35` numeric floor. A floor erodes silently: gating one tool
# just spends slack, and the suite stays green while a capability quietly stops producing
# anything. A per-tool table makes every such move an explicit, reviewable diff.
#
# Adding a tool? Add its row - a missing or extra key fails loudly.
# Flipping a row PASS -> GATED needs a reason in the PR, not a smaller number here.
EXPECTED_OFFLINE_BUCKETS: dict[str, str] = {
    # --- GATED: requires_data=True and the harness has no fixture yet, so these never
    # execute a line of their own job code. Adding a fixture flips the row to PASS.
    "cross_impact":           "GATED",
    "disruption_scan":        "GATED",
    "flow_map":               "GATED",
    "hat_settlement":         "GATED",
    "hat_tension":            "GATED",
    "network_design":         "GATED",
    "supplier_management":    "GATED",
    # --- SKIP: network tools, not exercised without --include-network.
    "price_intelligence":     "SKIP",
    "price_watch":            "SKIP",
    # --- PASS: produces a real deliverable end to end.
    "abc_xyz":                "PASS",
    "acceptance_sampling":    "PASS",
    "cost_to_serve":          "PASS",
    "cycle_count":            "PASS",
    "data_quality":           "PASS",
    "ddmrp":                  "PASS",
    "dea":                    "PASS",
    "digital_twin":           "PASS",
    "drp":                    "PASS",
    "earned_value":           "PASS",
    "excel_replenishment":    "PASS",
    "excess_obsolete":        "PASS",
    "facility_location":      "PASS",
    "fefo":                   "PASS",
    "financial_kpis":         "PASS",
    "forecast":               "PASS",
    "inventory_optimization": "PASS",
    "landed_cost":            "PASS",
    "launch_readiness":       "PASS",
    "leadership_chain":       "PASS",
    "learning_curve":         "PASS",
    "markdown_liquidation":   "PASS",
    "multi_echelon":          "PASS",
    "newsvendor":             "PASS",
    "odoo_replenishment":     "PASS",
    "pricing":                "PASS",
    "queuing":                "PASS",
    "reconciliation":         "PASS",
    "returns":                "PASS",
    "risk":                   "PASS",
    "scheduling":             "PASS",
    "simulation":             "PASS",
    "slotting":               "PASS",
    "sop":                    "PASS",
    "sourcing":               "PASS",
    "transportation":         "PASS",
    "vehicle_routing":        "PASS",
    "warehouse_layout":       "PASS",
    "whatif":                 "PASS",
}


def _report(tmp_path: Path, *args: str) -> dict:
    rc = main([*args, "--out", str(tmp_path)])
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    report["_rc"] = rc
    return report


def test_every_registered_tool_is_exercised(tmp_path):
    """The harness must cover the whole registry -- no tool silently dropping out."""
    report = _report(tmp_path)
    covered = {c["key"] for c in report["cases"]}
    registered = {t.key for t in build_default_registry().list()}
    assert covered == registered, f"harness/registry drift: {registered ^ covered}"


def test_no_capability_fails_end_to_end(tmp_path):
    """Every offline tool runs clean: PASS, or a protective GATED/SKIP -- never FAIL."""
    report = _report(tmp_path)
    fails = [c for c in report["cases"] if c["bucket"] == "FAIL"]
    assert not fails, "capabilities failed E2E: " + ", ".join(
        f"{c['key']}({c['status']}: {c['detail']})" for c in fails
    )
    assert report["_rc"] == 0


def test_each_tool_lands_in_its_expected_bucket(tmp_path):
    """Per-tool expectations, so gating a capability is a visible diff, not spent slack."""
    report = _report(tmp_path)

    registered = {t.key for t in build_default_registry().list()}
    assert set(EXPECTED_OFFLINE_BUCKETS) == registered, (
        f"expectation-table/registry drift: {registered ^ set(EXPECTED_OFFLINE_BUCKETS)} "
        "-- add the new tool's row (and a smoke fixture, if it needs data)"
    )

    drift = [
        f"{c['key']}: expected {EXPECTED_OFFLINE_BUCKETS[c['key']]}, got {c['bucket']} "
        f"({c['status']}: {c['detail'][:120]})"
        for c in report["cases"]
        if c["bucket"] != EXPECTED_OFFLINE_BUCKETS[c["key"]]
    ]
    assert not drift, "capability bucket drift:\n  " + "\n  ".join(drift)


def test_the_skip_rows_match_the_harness_network_set():
    """Keeps the table coherent if a third network tool lands."""
    skipped = {k for k, b in EXPECTED_OFFLINE_BUCKETS.items() if b == "SKIP"}
    assert skipped == _NETWORK


def test_no_capability_crashes_on_degenerate_input(tmp_path):
    """--stress: empty / wrong-schema / garbage inputs must degrade, never crash."""
    report = _report(tmp_path, "--stress")
    crashes = [c for c in report["cases"] if c["bucket"] == "CRASH"]
    assert not crashes, "capabilities crashed on bad input: " + ", ".join(
        f"{c['key']}({c['detail']})" for c in crashes
    )
    assert report["_rc"] == 0
