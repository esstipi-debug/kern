"""T2 - provenance of synthesized cells.

IntakeQuality counted dropped rows and nothing else, so a file could have two of its
canonical columns fabricated wholesale and still report dropped_fraction=0.0000 --
a pristine quality signal. _apply_intake_quality even returned early on
``n_dropped == 0``, so the escalation path was unreachable for this failure class.

Witness 3, re-measured at HEAD: data/kaggle/superstore.csv has no cost and no
lead-time columns. unit_cost is now *derived* from revenue (Sales/Quantity) by an
earlier fix, so the columns fabricated 100% are lead_time_days (14.0) and
lead_time_std (0.0).

Escalation is deliberately narrower than disclosure. lead_time_std defaulting to 0.0
is an explicit modelling assumption -- deterministic lead time, which the engine
handles correctly -- and every shipped sample file lacks it, so escalating there
would gate healthy runs. A fabricated unit cost or mean lead time silently moves a
money number and a ranking, so those escalate.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import intake, inventory_optimization
from scm_agent import tool_options

REPO = Path(__file__).resolve().parents[1]
CRITICAL = ("unit_cost", "lead_time_days")


def _superstore_slice(n_skus=25):
    """Witness 3's column structure at test speed: the real file, fewer SKUs.

    What T2 detects is which columns were fabricated, which is structural -- the row
    count does not change it.
    """
    raw = pd.read_csv(REPO / "data" / "kaggle" / "superstore.csv", encoding="latin-1")
    keep = raw["Product ID"].drop_duplicates().head(n_skus)
    return raw[raw["Product ID"].isin(keep)].copy()


def test_intake_counts_synthesized_cells_per_column():
    frame, quality = intake.normalize_tracked(
        _superstore_slice(), intake.detect_columns(_superstore_slice())
    )
    synth = dict(quality.synthesized)
    n = len(frame)
    assert synth.get("lead_time_days") == n, "mean lead time was fabricated for every row"
    assert synth.get("lead_time_std") == n, "sigma_LT was fabricated for every row"
    assert synth.get("unit_cost", 0) == 0, "unit cost is derived from revenue, not defaulted"
    assert set(quality.fully_synthesized_columns) == {"lead_time_days", "lead_time_std"}


def test_the_old_signal_still_reports_clean():
    """The point of the witness: dropped_fraction alone says the data is perfect."""
    _frame, quality = intake.normalize_tracked(
        _superstore_slice(), intake.detect_columns(_superstore_slice())
    )
    assert quality.n_dropped == 0
    assert quality.dropped_fraction == pytest.approx(0.0)
    # ...while two columns were fabricated end to end
    assert quality.fully_synthesized_columns


def test_healthy_sample_file_only_lacks_sigma_lt():
    raw = pd.read_csv(REPO / "data" / "sample_demand_portfolio.csv")
    _frame, quality = intake.normalize_tracked(raw, intake.detect_columns(raw))
    assert set(quality.fully_synthesized_columns) == {"lead_time_std"}
    assert not [c for c in quality.fully_synthesized_columns if c in CRITICAL]


def _options_for(raw: pd.DataFrame):
    frame, quality = intake.normalize_tracked(raw, intake.detect_columns(raw))
    report = inventory_optimization.run(frame, intake_quality=quality)
    return tool_options.inventory_options(report)


def test_fabricated_lead_time_escalates_the_outcome():
    outcome = _options_for(_superstore_slice())
    assert outcome.status == "escalated", (
        "a fabricated mean lead time must reach the escalation path"
    )
    blob = " ".join(
        [outcome.summary]
        + [r.description for r in outcome.residuals]
        + ([outcome.escalation.reason] if outcome.escalation else [])
    ).lower()
    assert "lead_time_days" in blob
    assert "synthes" in blob or "default" in blob or "fabricat" in blob


def test_healthy_file_is_not_escalated_but_still_discloses():
    outcome = _options_for(pd.read_csv(REPO / "data" / "sample_demand_portfolio.csv"))
    assert outcome.status == "options", "a healthy file must not be gated"
    # lead_time_std is still disclosed as a residual, just not escalated
    blob = " ".join(r.description for r in outcome.residuals).lower()
    assert "lead_time_std" in blob
