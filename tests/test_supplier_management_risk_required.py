"""Kraljic segmentation must not present a spend Pareto as a two-axis risk decision.

The Kraljic matrix has two axes: profit impact (spend) and supply risk. When a CSV carries
no risk-driver column, ``_resolve_driver_cols`` returned an empty map, every supplier scored
``supply_risk = 0.000`` (src/supplier_management.py:77-79 returns 0.0 on zero total weight),
every supplier landed in the low-risk half, and ``verify()`` passed because suppliers *were*
segmented. The output looked like a Kraljic study and was a spend ranking.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import supplier_management_job as smj


def _suppliers_csv(tmp_path, **overrides):
    frame = {
        "supplier": ["Alpha", "Beta", "Gamma"],
        "annual_spend": [500_000.0, 120_000.0, 40_000.0],
        "lead_time_days": [7.0, 30.0, 14.0],
        "defect_ppm": [500.0, 20_000.0, 3_000.0],
    }
    frame.update(overrides)
    csv = tmp_path / "suppliers.csv"
    pd.DataFrame(frame).to_csv(csv, index=False)
    return csv


def test_no_risk_driver_column_refuses_instead_of_scoring_every_supplier_zero(tmp_path):
    csv = tmp_path / "spend_only.csv"
    pd.DataFrame({
        "supplier": ["Alpha", "Beta", "Gamma"],
        "annual_spend": [500_000.0, 120_000.0, 40_000.0],
    }).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="risk"):
        smj.prepare(str(csv), {})


def test_a_typo_in_a_risk_column_override_is_reported_not_silently_ignored(tmp_path):
    """An unmatched override degraded exactly like an absent column - silently."""
    with pytest.raises(ValueError, match="qualtiy|not a column|risk_cols"):
        smj.prepare(str(_suppliers_csv(tmp_path)), {"risk_cols": {"quality": "qualtiy_ppm"}})


def test_real_risk_columns_still_produce_a_spread_of_supply_risk(tmp_path):
    payload = smj.prepare(str(_suppliers_csv(tmp_path)), {})

    report = smj.run(payload["suppliers"], payload["drivers"])

    risks = {s.supplier: s.supply_risk for s in report.segments}
    assert len(set(risks.values())) > 1, f"every supplier scored the same risk: {risks}"
    assert risks["Beta"] > risks["Alpha"]      # worse lead time and worse quality
    assert smj.verify(report) == []


def test_verify_rejects_a_report_that_placed_suppliers_with_no_risk_driver(tmp_path):
    """Defence in depth: even hand-built payloads cannot ship a one-axis Kraljic."""
    payload = smj.prepare(str(_suppliers_csv(tmp_path)), {})

    report = smj.run(payload["suppliers"], drivers=[])

    assert "risk" in " ".join(smj.verify(report)).lower()
