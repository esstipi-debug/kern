"""Tests for jobs/reprice_recommend.py — the decision bridge that joins the
price-watch competitor position with the elasticity optimizer into one NUMERIC
proposed price per SKU, and glues the result into jobs/repricing.py's staged,
gated changeset path.

Design invariants under test (mirroring the repo's pricing QA rules):
- "needs_data, never a fabricated number": no elasticity signal AND no
  confirmed competitor read => no price, ever.
- A competitor-rule proposal (match-to-market) is EXPLICITLY labeled
  basis="competitor_rule", never presented as elasticity-optimal, and is
  excluded from staging unless the caller opts in.
- A confirmed competitor read caps an elasticity-optimal price at
  competitor_avg * (1 + premium_cap) — the "igualar/subir bounded at market"
  semantics — and a cap BELOW the margin floor is an honest conflict result,
  never a below-margin price.
- Every SKU seen in either input is accounted for in the report (golden
  rule 14), never silently dropped.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs.reprice_recommend import (
    BASIS_COMPETITOR_RULE,
    BASIS_ELASTICITY,
    STATUS_CONFLICT,
    STATUS_NEEDS_DATA,
    STATUS_PROPOSED,
    RepriceProposal,
    RepriceRecommendReport,
    prepare,
    prices_for_staging,
    reprice_recommend_passed,
    run,
    stage_from_recommendations,
    verify,
    write_operational,
)
from src import writeback

# ---- fixtures -------------------------------------------------------------------

_DATES = pd.date_range("2025-01-06", periods=10, freq="W-MON")
_PRICES_E = (10.0, 10.5, 9.5, 11.0, 9.0, 10.8, 9.3, 11.5, 8.8, 10.2)


def _demand_frame(include_cost: bool = True) -> pd.DataFrame:
    """SKU-E follows a clean q = A * p^-2.5 power law (elastic, identified);
    SKU-N sits at one constant price (unidentified — no price variation)."""
    rows = []
    for i, (d, p) in enumerate(zip(_DATES, _PRICES_E)):
        q = 8000.0 * p**-2.5 * (1.0 + (0.02 if i % 2 == 0 else -0.02))
        row = {"date": d, "product_id": "SKU-E", "quantity": round(q, 3), "unit_price": p}
        if include_cost:
            row["unit_cost"] = 4.0
        rows.append(row)
    for d in _DATES:
        row = {"date": d, "product_id": "SKU-N", "quantity": 25.0, "unit_price": 10.0}
        if include_cost:
            row["unit_cost"] = 5.0
        rows.append(row)
    return pd.DataFrame(rows)


def _demand_csv(tmp_path, include_cost: bool = True) -> str:
    path = tmp_path / "demand.csv"
    _demand_frame(include_cost).to_csv(path, index=False)
    return str(path)


def _position_csv(tmp_path, competitor_e: float, competitor_n: float | None = 9.0) -> str:
    rows = [
        {"product_id": "SKU-E", "our_price": 10.0, "competitor_price": competitor_e},
    ]
    if competitor_n is not None:
        rows.append({"product_id": "SKU-N", "our_price": 10.0, "competitor_price": competitor_n})
    path = tmp_path / "positions.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _run_bridge(tmp_path, competitor_e: float, competitor_n: float | None = 9.0, **params):
    payload = prepare(
        _demand_csv(tmp_path),
        {"price_position_path": _position_csv(tmp_path, competitor_e, competitor_n), **params},
    )
    return run(payload)


def _by_pid(report: RepriceRecommendReport) -> dict[str, RepriceProposal]:
    return {p.product_id: p for p in report.proposals}


# ---- prepare --------------------------------------------------------------------


def test_prepare_reads_positions_from_csv(tmp_path):
    payload = prepare(
        _demand_csv(tmp_path), {"price_position_path": _position_csv(tmp_path, 8.0)},
    )
    reads = payload["reads"]
    assert set(reads) == {"SKU-E", "SKU-N"}
    assert reads["SKU-E"].competitor_avg == pytest.approx(8.0)
    assert reads["SKU-E"].our_price == pytest.approx(10.0)
    assert reads["SKU-E"].n_obs == 1


def test_prepare_averages_multiple_competitor_rows(tmp_path):
    rows = [
        {"product_id": "SKU-E", "our_price": 10.0, "competitor_price": 7.0},
        {"product_id": "SKU-E", "our_price": 10.0, "competitor_price": 9.0},
    ]
    pos = tmp_path / "pos.csv"
    pd.DataFrame(rows).to_csv(pos, index=False)
    payload = prepare(_demand_csv(tmp_path), {"price_position_path": str(pos)})
    read = payload["reads"]["SKU-E"]
    assert read.competitor_avg == pytest.approx(8.0)
    assert read.competitor_min == pytest.approx(7.0)
    assert read.n_obs == 2


def test_prepare_requires_a_position_source(tmp_path):
    with pytest.raises(ValueError, match="price_report.*price_position_path"):
        prepare(_demand_csv(tmp_path), {})


# ---- run: the decision matrix -----------------------------------------------------


def test_elastic_sku_uncapped_when_market_is_above_pstar(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0)  # cap 8.4 > p* ~6.67
    p = _by_pid(report)["SKU-E"]
    assert p.status == STATUS_PROPOSED
    assert p.basis == BASIS_ELASTICITY
    assert p.stageable is True
    assert p.price_capped is False
    e = p.elasticity_used
    assert e is not None and e < -1
    assert p.proposed_price == pytest.approx(4.0 * e / (e + 1.0), rel=1e-6)
    assert p.competitor_cap == pytest.approx(8.0 * 1.05)


def test_elastic_sku_capped_at_competitor_premium(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=6.0)  # cap 6.3 < p* ~6.67
    p = _by_pid(report)["SKU-E"]
    assert p.status == STATUS_PROPOSED
    assert p.basis == BASIS_ELASTICITY
    assert p.price_capped is True
    assert p.proposed_price == pytest.approx(6.0 * 1.05, rel=1e-6)


def test_conflict_when_market_sits_below_the_margin_floor(tmp_path):
    # competitor 3.0 -> cap 3.15; floor = landed 4.0 * (1 + 0) = 4.0 > cap.
    report = _run_bridge(tmp_path, competitor_e=3.0)
    p = _by_pid(report)["SKU-E"]
    assert p.status == STATUS_CONFLICT
    assert p.proposed_price is None
    assert p.stageable is False
    assert "floor" in p.reason.lower() or "margin" in p.reason.lower()


def test_needs_data_sku_with_confirmed_read_gets_labeled_rule_based_match(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=9.0)
    p = _by_pid(report)["SKU-N"]
    assert p.status == STATUS_PROPOSED
    assert p.basis == BASIS_COMPETITOR_RULE
    assert p.stageable is False  # never staged by default
    assert p.elasticity_used is None  # never dressed up as elasticity math
    assert p.proposed_price == pytest.approx(9.0)  # match-to-market, floor 5.0 respected


def test_needs_data_sku_without_read_stays_needs_data(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=None)
    p = _by_pid(report)["SKU-N"]
    assert p.status == STATUS_NEEDS_DATA
    assert p.proposed_price is None
    assert p.basis is None


def test_sku_without_cost_column_is_needs_data(tmp_path):
    path = tmp_path / "nocost.csv"
    _demand_frame(include_cost=False).to_csv(path, index=False)
    payload = prepare(str(path), {"price_position_path": _position_csv(tmp_path, 8.0)})
    report = run(payload)
    for pid in ("SKU-E", "SKU-N"):
        p = _by_pid(report)[pid]
        assert p.status == STATUS_NEEDS_DATA
        assert "cost" in p.reason.lower()


def test_sku_only_in_the_position_input_is_reported_not_dropped(tmp_path):
    rows = [
        {"product_id": "SKU-E", "our_price": 10.0, "competitor_price": 8.0},
        {"product_id": "SKU-GHOST", "our_price": 15.0, "competitor_price": 14.0},
    ]
    pos = tmp_path / "pos.csv"
    pd.DataFrame(rows).to_csv(pos, index=False)
    payload = prepare(_demand_csv(tmp_path), {"price_position_path": str(pos)})
    report = run(payload)
    p = _by_pid(report)["SKU-GHOST"]
    assert p.status == STATUS_NEEDS_DATA
    assert "history" in p.reason.lower()


def test_current_price_is_the_latest_bucket_not_the_median(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0)
    p = _by_pid(report)["SKU-E"]
    assert p.current_price == pytest.approx(_PRICES_E[-1])


def test_report_counts_are_consistent(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=9.0)
    assert report.n_proposed == sum(1 for p in report.proposals if p.status == STATUS_PROPOSED)
    assert report.n_rule_based == sum(1 for p in report.proposals if p.basis == BASIS_COMPETITOR_RULE)
    assert report.n_needs_data == sum(1 for p in report.proposals if p.status == STATUS_NEEDS_DATA)
    assert report.n_conflict == sum(1 for p in report.proposals if p.status == STATUS_CONFLICT)
    assert report.summary


# ---- staging selection ------------------------------------------------------------


def test_prices_for_staging_defaults_to_elasticity_only(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=9.0)
    staged = prices_for_staging(report)
    assert set(staged) == {"SKU-E"}
    both = prices_for_staging(report, include_rule_based=True)
    assert set(both) == {"SKU-E", "SKU-N"}
    assert both["SKU-N"] == pytest.approx(9.0)


# ---- QA gate ----------------------------------------------------------------------


def _proposal(**overrides) -> RepriceProposal:
    base = dict(
        product_id="SKU-X", status=STATUS_PROPOSED, basis=BASIS_ELASTICITY,
        current_price=10.0, proposed_price=8.0, landed_cost=4.0,
        elasticity_used=-2.5, position_index=1.25, competitor_avg=8.0,
        competitor_cap=8.4, floor_price=4.0, floor_applied=False,
        price_capped=False, stageable=True, reason="test proposal",
    )
    base.update(overrides)
    return RepriceProposal(**base)


def _report_of(*proposals: RepriceProposal) -> RepriceRecommendReport:
    return RepriceRecommendReport(
        proposals=tuple(proposals),
        n_proposed=sum(1 for p in proposals if p.status == STATUS_PROPOSED),
        n_rule_based=sum(1 for p in proposals if p.basis == BASIS_COMPETITOR_RULE),
        n_needs_data=sum(1 for p in proposals if p.status == STATUS_NEEDS_DATA),
        n_conflict=sum(1 for p in proposals if p.status == STATUS_CONFLICT),
        premium_cap=0.05,
        summary="hand-built",
    )


def test_verify_passes_on_real_run_output(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=9.0)
    assert verify(report) == []
    assert reprice_recommend_passed(report)


def test_verify_flags_rule_based_marked_stageable():
    bad = _proposal(basis=BASIS_COMPETITOR_RULE, elasticity_used=None, stageable=True)
    issues = verify(_report_of(bad))
    assert any("stageable" in i for i in issues)


def test_verify_flags_a_price_below_the_margin_floor():
    bad = _proposal(proposed_price=3.5, floor_price=4.0)
    issues = verify(_report_of(bad))
    assert any("floor" in i for i in issues)


def test_verify_flags_a_price_above_the_competitor_cap():
    bad = _proposal(proposed_price=9.0, competitor_cap=8.4)
    issues = verify(_report_of(bad))
    assert any("cap" in i for i in issues)


def test_verify_flags_a_conflict_that_still_carries_a_price():
    bad = _proposal(status=STATUS_CONFLICT, proposed_price=5.0, stageable=False)
    issues = verify(_report_of(bad))
    assert any("conflict" in i for i in issues)


def test_verify_flags_rule_based_without_a_confirmed_read():
    bad = _proposal(
        basis=BASIS_COMPETITOR_RULE, elasticity_used=None, stageable=False,
        competitor_avg=None, competitor_cap=None, position_index=None,
    )
    issues = verify(_report_of(bad))
    assert any("read" in i or "competitor" in i for i in issues)


# ---- the glue into the staged, gated repricing path --------------------------------


def test_stage_from_recommendations_returns_a_gated_changeset(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=9.0)
    staged_prices = prices_for_staging(report)
    store = writeback.InMemoryStore({pid: {"price": 10.0} for pid in staged_prices})
    changeset = stage_from_recommendations(
        store, "shopify", report,
        idempotency_key="test-reprice-1",
        reason="Close the margin gap on SKU-E per elasticity + competitor position.",
    )
    assert isinstance(changeset, writeback.Changeset)
    staged_skus = {c.entity_id for c in changeset.changes if not c.is_noop}
    assert staged_skus == {"SKU-E"}


def test_stage_from_recommendations_with_nothing_stageable_raises(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=3.0, competitor_n=None)  # conflict + needs_data
    store = writeback.InMemoryStore({"SKU-E": {"price": 10.0}})
    with pytest.raises(ValueError, match="[Nn]o stageable"):
        stage_from_recommendations(
            store, "shopify", report, idempotency_key="test-reprice-2", reason="nothing to stage",
        )


# ---- deliverable --------------------------------------------------------------------


def test_write_operational_writes_every_sku_row(tmp_path):
    report = _run_bridge(tmp_path, competitor_e=8.0, competitor_n=9.0)
    out = write_operational(report, tmp_path / "out")
    df = pd.read_csv(out["csv"])
    assert set(df["product_id"]) == {p.product_id for p in report.proposals}
    assert "basis" in df.columns and "reason" in df.columns
