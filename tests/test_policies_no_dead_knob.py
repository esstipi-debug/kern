"""Guard against the inert periods_per_year knob (audit fix 4e).

``continuous_review_sq`` accepted ``periods_per_year: float = 52.0`` and never
referenced it in the body: passing 12 instead of 52 changed Q, safety stock and
the reorder point by exactly 0.0. ``periodic_review_rs`` never declared it at all.
An inert knob is worse than a missing one -- it invites a caller to "fix" a wiring
gap by passing a value that does nothing.

Annualisation happens where it belongs: ``ForecastResult.to_engine_inputs``
multiplies the per-period forecast by periods_per_year to build ``annual_demand``.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from src.policies import continuous_review_sq, periodic_review_rs

REPO = Path(__file__).resolve().parents[1]


def test_neither_policy_declares_periods_per_year():
    for fn in (continuous_review_sq, periodic_review_rs):
        assert "periods_per_year" not in inspect.signature(fn).parameters, (
            f"{fn.__name__} still declares the inert knob"
        )


def test_passing_it_is_rejected_rather_than_silently_ignored():
    common = dict(
        annual_demand=5000.0, mean_demand_per_period=96.0, demand_std_per_period=40.0,
        holding_cost_per_unit=0.25, fixed_order_cost=75.0, lead_time_periods=2.0,
        cycle_service_level=0.95,
    )
    continuous_review_sq(**common)  # still works without it
    with pytest.raises(TypeError):
        continuous_review_sq(**common, periods_per_year=12.0)


def test_no_caller_passes_periods_per_year_to_a_policy():
    """A repo-wide check, so the knob cannot creep back in via a call site."""
    offenders = []
    for sub in ("src", "jobs", "scm_agent", "webapp", "examples", "tests"):
        for path in (REPO / sub).rglob("*.py"):
            if "__pycache__" in path.parts or path.name == Path(__file__).name:
                continue  # this module passes it on purpose, to assert the TypeError
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in ("continuous_review_sq", "periodic_review_rs"):
                    if any(kw.arg == "periods_per_year" for kw in node.keywords):
                        offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
    assert not offenders, "callers passing the inert knob: " + ", ".join(offenders)
