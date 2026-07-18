"""Capability guardrail -- run the end-to-end smoke harness as part of the suite
so a regression that breaks any tool's prepare->run->QA->deliver pipeline (or
makes it crash on bad input) fails CI, not just a manual run.

Kept deliberately thin: the harness (examples/run_capability_smoke.py) owns the
fixtures and the run logic; this only asserts the invariants.
"""

from __future__ import annotations

import json
from pathlib import Path

from examples.run_capability_smoke import main
from scm_agent.tools import build_default_registry


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
    # Sanity floor: the bulk of tools should actually produce a deliverable, not
    # merely gate. (39 PASS today; the floor guards against a silent collapse.)
    passes = sum(1 for c in report["cases"] if c["bucket"] == "PASS")
    assert passes >= 35, f"only {passes} tools PASSed end-to-end"


def test_no_capability_crashes_on_degenerate_input(tmp_path):
    """--stress: empty / wrong-schema / garbage inputs must degrade, never crash."""
    report = _report(tmp_path, "--stress")
    crashes = [c for c in report["cases"] if c["bucket"] == "CRASH"]
    assert not crashes, "capabilities crashed on bad input: " + ", ".join(
        f"{c['key']}({c['detail']})" for c in crashes
    )
    assert report["_rc"] == 0
