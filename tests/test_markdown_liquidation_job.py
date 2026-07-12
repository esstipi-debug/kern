"""Tests for the markdown-liquidation job (jobs/markdown_liquidation_job.py).

Exercises the CSV-prep + deliverable half: reusing the E&O stock prep and the
pricing history prep, running the liquidation plan, QA-gating it, and writing a
formula-injection-safe operational CSV.
"""

from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from jobs import markdown_liquidation_job as job
from src.deliverable import Deliverable
from src.liquidation import DEFAULT_DISCOUNT, ELASTICITY, LiquidationLine, LiquidationReport
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, SkuMap
from src.pricing_intel.models import CompetitorOffer, MatchCandidate


def _write_stock_csv(path) -> str:
    pd.DataFrame(
        [
            {"product_id": "A", "on_hand": 1000, "daily_demand": 1.0, "unit_cost": 5.0},
            {"product_id": "B", "on_hand": 500, "daily_demand": 1.0, "unit_cost": 4.0},
            {"product_id": "C", "on_hand": 10, "daily_demand": 1.0, "unit_cost": 9.0},  # healthy
        ]
    ).to_csv(path, index=False)
    return str(path)


def _write_price_csv(path) -> str:
    rows = [
        # A: real price variation across weeks -> identified elasticity.
        {"date": "2026-01-05", "product_id": "A", "price": 4.0, "quantity": 10},
        {"date": "2026-01-12", "product_id": "A", "price": 4.0, "quantity": 10},
        {"date": "2026-01-19", "product_id": "A", "price": 2.0, "quantity": 40},
        {"date": "2026-01-26", "product_id": "A", "price": 2.0, "quantity": 40},
        # B: flat price across weeks -> no elasticity -> default markdown.
        {"date": "2026-01-05", "product_id": "B", "price": 10.0, "quantity": 5},
        {"date": "2026-01-12", "product_id": "B", "price": 10.0, "quantity": 6},
        {"date": "2026-01-19", "product_id": "B", "price": 10.0, "quantity": 4},
        {"date": "2026-01-26", "product_id": "B", "price": 10.0, "quantity": 5},
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def test_prepare_reads_stock_and_optional_price_history(tmp_path) -> None:
    stock = _write_stock_csv(tmp_path / "stock.csv")
    price = _write_price_csv(tmp_path / "prices.csv")
    payload = job.prepare(stock, {"price_history_path": price})
    assert len(payload["stocks"]) == 3
    assert set(payload["price_history"]) == {"A", "B"}
    assert payload["horizon_weeks"] == 13.0


def test_prepare_works_without_price_history(tmp_path) -> None:
    stock = _write_stock_csv(tmp_path / "stock.csv")
    payload = job.prepare(stock)
    assert payload["price_history"] is None


def test_run_assigns_the_expected_disposition_methods(tmp_path) -> None:
    stock = _write_stock_csv(tmp_path / "stock.csv")
    price = _write_price_csv(tmp_path / "prices.csv")
    report = job.run(job.prepare(stock, {"price_history_path": price}))
    assert isinstance(report, LiquidationReport)
    by_id = {line.product_id: line for line in report.lines}
    assert set(by_id) == {"A", "B"}  # C is healthy, excluded
    assert by_id["A"].method == ELASTICITY
    assert by_id["B"].method == DEFAULT_DISCOUNT


def test_verify_passes_on_a_clean_report(tmp_path) -> None:
    stock = _write_stock_csv(tmp_path / "stock.csv")
    report = job.run(job.prepare(stock))
    assert job.verify(report) == []


def _fabricated_line(**overrides) -> LiquidationLine:
    """A valid line, so each verify() test can override exactly the one bad field it
    means to pin - construction alone would raise (frozen dataclass) if invalid."""
    defaults = dict(
        product_id="X", classification="excess", units_to_clear=10.0, at_risk_value=100.0,
        method="salvage_heuristic", clearance_price=None, weeks_to_clear=math.inf,
        recovered_value=30.0, recovery_pct=0.3,
    )
    return LiquidationLine(**{**defaults, **overrides})


def _fabricated_report(lines: tuple[LiquidationLine, ...], **overrides) -> LiquidationReport:
    defaults = dict(
        lines=lines, n_assessed=len(lines), n_excess=len(lines), n_dead=0,
        n_elasticity=0, n_default_discount=0, n_salvage=len(lines),
        total_at_risk=sum(ln.at_risk_value for ln in lines),
        total_recovered=sum(ln.recovered_value for ln in lines),
        recovery_pct=0.3, horizon_weeks=13.0, default_markdown_pct=0.4,
        salvage_recovery_pct=0.3, summary="fabricated",
    )
    return LiquidationReport(**{**defaults, **overrides})


def test_verify_catches_a_negative_total_recovered() -> None:
    report = _fabricated_report((_fabricated_line(),), total_recovered=-1.0)
    issues = job.verify(report)
    assert any("recovered" in i for i in issues)


def test_verify_catches_a_non_finite_total_at_risk() -> None:
    report = _fabricated_report((_fabricated_line(),), total_at_risk=math.nan)
    issues = job.verify(report)
    assert any("at-risk" in i for i in issues)


def test_verify_catches_a_non_positive_clearance_price() -> None:
    line = _fabricated_line(method=ELASTICITY, clearance_price=0.0)
    issues = job.verify(_fabricated_report((line,)))
    assert any("X" in i and "clearance price" in i for i in issues)


def test_verify_catches_a_non_finite_recovered_value_on_a_line() -> None:
    line = _fabricated_line(recovered_value=math.inf)
    issues = job.verify(_fabricated_report((line,)))
    assert any("X" in i and "recovered value" in i for i in issues)


def test_a_noop_verify_would_be_caught_by_these_tests() -> None:
    """Guards the guard: a verify() that always returns [] must fail at least one of
    the four checks above, so the QA gate itself cannot silently regress to a no-op."""
    bad_reports = [
        _fabricated_report((_fabricated_line(),), total_recovered=-1.0),
        _fabricated_report((_fabricated_line(),), total_at_risk=math.nan),
        _fabricated_report((_fabricated_line(method=ELASTICITY, clearance_price=0.0),)),
        _fabricated_report((_fabricated_line(recovered_value=math.inf),)),
    ]
    assert all(job.verify(r) != [] for r in bad_reports)


def test_write_operational_emits_the_ranked_plan_csv(tmp_path) -> None:
    stock = _write_stock_csv(tmp_path / "stock.csv")
    price = _write_price_csv(tmp_path / "prices.csv")
    report = job.run(job.prepare(stock, {"price_history_path": price}))
    out = job.write_operational(report, tmp_path / "out")
    assert out["csv"].exists()
    with out["csv"].open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["product_id"] for r in rows] == ["A", "B"]  # ranked by at-risk desc
    assert {"clearance_price", "weeks_to_clear", "recovered_value", "method"} <= set(rows[0])


def test_write_operational_defuses_formula_injection(tmp_path) -> None:
    df = pd.DataFrame(
        [{"product_id": "=SUM(A1)", "on_hand": 1000, "daily_demand": 1.0, "unit_cost": 5.0}]
    )
    stock = tmp_path / "evil.csv"
    df.to_csv(stock, index=False)
    report = job.run(job.prepare(str(stock)))
    out = job.write_operational(report, tmp_path / "out")
    with out["csv"].open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    # the dangerous product_id must be neutralized, never a live leading-'=' formula
    assert rows[0]["product_id"] != "=SUM(A1)"
    assert not rows[0]["product_id"].startswith("=")


def test_build_deck_returns_a_deliverable(tmp_path) -> None:
    stock = _write_stock_csv(tmp_path / "stock.csv")
    price = _write_price_csv(tmp_path / "prices.csv")
    report = job.run(job.prepare(stock, {"price_history_path": price}))
    deck = job.build_deck(report, client="Acme", citations=("Gallego & van Ryzin (1994)",))
    assert isinstance(deck, Deliverable)
    assert deck.title == "Markdown Liquidation Plan"
    assert deck.client == "Acme"
    assert deck.kpis and deck.recommendations and deck.residual


def test_build_deck_residual_discloses_the_models_real_limitations(tmp_path) -> None:
    """The design intent (FINANCE_MARKETING_BRIDGE.md section 2) requires this model's
    limits be documented HONESTLY, not just present. A truthy-only check on `residual`
    (e.g. `assert deck.residual`) would still pass if every caveat below were silently
    dropped - pin the substance, not just its presence."""
    stock = _write_stock_csv(tmp_path / "stock.csv")
    report = job.run(job.prepare(stock))
    residual = job.build_deck(report).residual.lower()
    assert "deterministic" in residual  # the fluid-limit framing, not a stochastic optimum
    assert "multi-stage" in residual  # explicitly NOT a multi-stage markdown optimiser
    assert "depletion" in residual  # explicitly NOT Smith & Achabal's depletion effect
    assert "heuristic" in residual and "not optima" in residual  # default/salvage rates are guesses, not optima
    assert "commercial decision" in residual  # the agent recommends, it does not execute


def test_empty_plan_when_all_stock_is_healthy_is_not_a_qa_failure(tmp_path) -> None:
    pd.DataFrame(
        [{"product_id": "H", "on_hand": 5, "daily_demand": 1.0, "unit_cost": 3.0}]
    ).to_csv(tmp_path / "healthy.csv", index=False)
    report = job.run(job.prepare(str(tmp_path / "healthy.csv")))
    assert report.lines == ()
    assert job.verify(report) == []  # nothing to liquidate is a valid outcome


# -- resolve_competitor_contexts (Linchpin 3.0 PR-19, calendar v2's I/O side) ---


def _sku_map(tmp_path) -> SkuMap:
    return SkuMap(tmp_path / "sku_map")


def _ledger(tmp_path) -> PriceLedger:
    return PriceLedger(tmp_path / "ledger")


def _confirm(sku_map: SkuMap, product_id: str, site: str, ref: str) -> None:
    sku_map.record(MatchCandidate(
        our_product_id=product_id, competitor_sku_ref=ref, site=site, method="gtin",
        score=0.99, status="confirmed", reason="gtin_exact_match", confirmed_by=AUTO_CONFIRMED_BY,
        confirmed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    ))


def _offer(product_id: str, site: str, ref: str, price: float) -> CompetitorOffer:
    return CompetitorOffer(
        observed_at=datetime(2026, 7, 12, tzinfo=timezone.utc), site=site, competitor_sku_ref=ref,
        matched_product_id=product_id, match_confidence=0.95, price=Decimal(str(price)),
        currency="USD", price_normalized=Decimal(str(price)), shipping=None, availability="InStock",
        promo_flag=False, list_price=None, acquisition_tier="L1", extractor="jsonld",
        extractor_version="extruct==0.18.0", extraction_confidence=0.98,
    )


def _lines_report(*product_ids: str) -> LiquidationReport:
    lines = tuple(_fabricated_line(product_id=pid) for pid in product_ids)
    return _fabricated_report(lines)


def test_resolve_competitor_contexts_picks_the_cheapest_confirmed_site(tmp_path) -> None:
    sku_map = _sku_map(tmp_path)
    ledger = _ledger(tmp_path)
    _confirm(sku_map, "A", "site-one.test", "ref-1")
    _confirm(sku_map, "A", "site-two.test", "ref-2")
    ledger.append([_offer("A", "site-one.test", "ref-1", 50.0)])
    ledger.append([_offer("A", "site-two.test", "ref-2", 40.0)])

    contexts = job.resolve_competitor_contexts(_lines_report("A"), sku_map, ledger)

    assert set(contexts) == {"A"}
    assert contexts["A"].site == "site-two.test"
    assert contexts["A"].competitor_price == pytest.approx(40.0)


def test_resolve_competitor_contexts_skips_unconfirmed_matches(tmp_path) -> None:
    sku_map = _sku_map(tmp_path)
    ledger = _ledger(tmp_path)
    sku_map.record(MatchCandidate(
        our_product_id="B", competitor_sku_ref="ref-9", site="site.test", method="probabilistic",
        score=0.6, status="suspect", reason="score_inconclusive",
    ))
    ledger.append([_offer("B", "site.test", "ref-9", 10.0)])

    contexts = job.resolve_competitor_contexts(_lines_report("B"), sku_map, ledger)
    assert contexts == {}


def test_resolve_competitor_contexts_skips_confirmed_match_with_no_ledger_data(tmp_path) -> None:
    sku_map = _sku_map(tmp_path)
    ledger = _ledger(tmp_path)
    _confirm(sku_map, "C", "site.test", "ref-1")  # confirmed, but never acquired into the ledger

    contexts = job.resolve_competitor_contexts(_lines_report("C"), sku_map, ledger)
    assert contexts == {}


def test_resolve_competitor_contexts_dedupes_duplicate_product_ids(tmp_path) -> None:
    sku_map = _sku_map(tmp_path)
    ledger = _ledger(tmp_path)
    _confirm(sku_map, "A", "site.test", "ref-1")
    ledger.append([_offer("A", "site.test", "ref-1", 25.0)])

    report = _lines_report("A", "A")  # same product_id on two lines
    contexts = job.resolve_competitor_contexts(report, sku_map, ledger)
    assert set(contexts) == {"A"}
    assert contexts["A"].competitor_price == pytest.approx(25.0)
