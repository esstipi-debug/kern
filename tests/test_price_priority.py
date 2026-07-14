"""Tests for jobs/price_priority.py (Kern PR-10, R6 value-based prioritization).

Two layers, mirroring tests/test_seo_priority.py's own structure:
  1. Pure `run()`/`verify()` tests on a small HAND-CONSTRUCTED portfolio (ABC-XYZ
     classifications + price-position reads built directly, numbers verified by
     hand below) -- the primary coverage.
  2. A CSV/`PriceIntelReport` round-trip test for `prepare()` wiring, including
     the required price-source validation (mirrors seo_priority's
     `params['stock_path']` requirement test).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from jobs import price_priority as pp
from jobs.price_intelligence import CompetitorOffer, PriceIntelReport, RowOutcome
from src.classification import SkuClassification

FIXED_NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


def _classification(**overrides) -> SkuClassification:
    defaults = dict(
        product_id="X", annual_value=1000.0, cumulative_share=0.5, abc="B",
        mean_demand=10.0, cv=0.4, xyz="X", cell="BX", service_level=0.95,
        policy="(s, Q)", buffer_distribution="normal",
    )
    defaults.update(overrides)
    return SkuClassification(**defaults)


def _position(**overrides) -> pp.SkuPricePosition:
    defaults = dict(product_id="X", position_index=1.0, competitor_read=pp.CONFIRMED)
    defaults.update(overrides)
    return pp.SkuPricePosition(**defaults)


def _offer(**overrides) -> CompetitorOffer:
    defaults = dict(
        observed_at=FIXED_NOW, site="example-retailer.test", competitor_sku_ref="url-1",
        matched_product_id="X", match_confidence=1.0, price=Decimal("100"), currency="USD",
        price_normalized=Decimal("100"), shipping=None, availability="InStock", promo_flag=False,
        list_price=None, acquisition_tier="L1", extractor="jsonld", extractor_version="1",
        extraction_confidence=0.9,
    )
    defaults.update(overrides)
    return CompetitorOffer(**defaults)


def _row(**overrides) -> RowOutcome:
    defaults = dict(
        product_id="X", site="example-retailer.test", competitor_url="url-1",
        status="accepted", reason="ok", offer=None,
    )
    defaults.update(overrides)
    return RowOutcome(**defaults)


# ---- the four-way action rule ---------------------------------------------------


def test_a_class_pricier_gets_igualar_precio():
    payload = {
        "classifications": [_classification(product_id="P1", abc="A")],
        "price_positions": [_position(product_id="P1", position_index=1.10, competitor_read=pp.CONFIRMED)],
    }
    report = pp.run(payload)
    action = report.actions[0]
    assert action.action == pp.IGUALAR_PRECIO
    assert action.competitor_read == pp.CONFIRMED
    assert "1.10" in action.reason or "1.100" in action.reason
    assert "RECOMMENDATION" in action.reason
    assert report.n_igualar_precio == 1


def test_a_class_cheaper_gets_oportunidad_subir():
    payload = {
        "classifications": [_classification(product_id="P1", abc="B")],
        "price_positions": [_position(product_id="P1", position_index=0.90, competitor_read=pp.CONFIRMED)],
    }
    report = pp.run(payload)
    action = report.actions[0]
    assert action.action == pp.OPORTUNIDAD_SUBIR
    assert "0.90" in action.reason or "0.900" in action.reason
    assert report.n_oportunidad_subir == 1


def test_at_market_gets_vigilar():
    payload = {
        "classifications": [_classification(product_id="P1", abc="A")],
        "price_positions": [_position(product_id="P1", position_index=1.02, competitor_read=pp.CONFIRMED)],
    }
    report = pp.run(payload)
    action = report.actions[0]
    assert action.action == pp.VIGILAR
    assert "at market" in action.reason
    assert report.n_vigilar == 1


def test_c_class_gets_ignorar_bajo_valor():
    # position_index=1.50 would trigger igualar_precio if this were A/B-class --
    # proves the C-class check happens FIRST, regardless of price position.
    payload = {
        "classifications": [_classification(product_id="P1", abc="C")],
        "price_positions": [_position(product_id="P1", position_index=1.50, competitor_read=pp.CONFIRMED)],
    }
    report = pp.run(payload)
    action = report.actions[0]
    assert action.action == pp.IGNORAR_BAJO_VALOR
    assert "class=C" in action.reason
    assert report.n_ignorar_bajo_valor == 1


def test_sku_without_confirmed_read_gets_vigilar_insufficient_signal_not_a_guess():
    payload = {
        "classifications": [_classification(product_id="P1", abc="A")],
        "price_positions": [_position(product_id="P1", position_index=None, competitor_read=pp.INSUFFICIENT_SIGNAL)],
    }
    report = pp.run(payload)
    action = report.actions[0]
    assert action.action == pp.VIGILAR
    assert action.competitor_read == pp.INSUFFICIENT_SIGNAL
    assert action.position_index is None
    # Never a fabricated numeric position in the reason -- no guessed ratio.
    assert "position_index=" not in action.reason
    assert action.action not in (pp.IGUALAR_PRECIO, pp.OPORTUNIDAD_SUBIR)


def test_band_is_caller_overridable():
    # position_index=1.03 is inside the default band (1.05) but outside a
    # tighter 0.02 band -- proves band is a genuine, honored override.
    payload = {
        "classifications": [_classification(product_id="P1", abc="A")],
        "price_positions": [_position(product_id="P1", position_index=1.03, competitor_read=pp.CONFIRMED)],
        "band": 0.02,
    }
    report = pp.run(payload)
    assert report.actions[0].action == pp.IGUALAR_PRECIO
    assert report.band == 0.02


# ---- exclusion: present in only one input ----------------------------------------


def test_sku_in_only_one_input_is_excluded_and_reported():
    payload = {
        "classifications": [
            _classification(product_id="P1", abc="A"),
            _classification(product_id="ONLY_ABC", abc="B"),
        ],
        "price_positions": [
            _position(product_id="P1", position_index=1.0, competitor_read=pp.CONFIRMED),
            _position(product_id="ONLY_PRICE", position_index=1.0, competitor_read=pp.CONFIRMED),
        ],
    }
    report = pp.run(payload)
    excluded_by_id = {e.product_id: e for e in report.excluded}

    assert set(excluded_by_id) == {"ONLY_ABC", "ONLY_PRICE"}
    assert "missing from the price-position" in excluded_by_id["ONLY_ABC"].reason
    assert "missing from ABC-XYZ" in excluded_by_id["ONLY_PRICE"].reason
    assert report.n_excluded == 2
    assert "ONLY_ABC" not in {a.product_id for a in report.actions}
    assert "ONLY_PRICE" not in {a.product_id for a in report.actions}
    # P1 is in BOTH inputs -- it gets an action, not an exclusion.
    assert "P1" in {a.product_id for a in report.actions}


# ---- verify() catches a fabricated / malformed report ----------------------------


def test_verify_flags_invalid_action_or_missing_reason():
    bad_action = pp.PricePriorityReport(
        actions=(pp.SkuPriceAction(
            product_id="BAD", action="discount_it_now", abc="A", xyz="X",
            position_index=1.10, competitor_read=pp.CONFIRMED, reason="",
        ),),
        excluded=(), n_igualar_precio=0, n_oportunidad_subir=0, n_vigilar=0,
        n_ignorar_bajo_valor=0, n_excluded=0, band=0.05, summary="",
    )
    issues = pp.verify(bad_action)
    assert any("invalid action" in i for i in issues)
    assert any("no citable reason" in i for i in issues)

    bad_excluded = pp.PricePriorityReport(
        actions=(), excluded=(pp.ExcludedSku(product_id="Q", reason=""),),
        n_igualar_precio=0, n_oportunidad_subir=0, n_vigilar=0,
        n_ignorar_bajo_valor=0, n_excluded=1, band=0.05, summary="",
    )
    issues2 = pp.verify(bad_excluded)
    assert any("excluded without a reason" in i for i in issues2)


def test_verify_flags_a_price_changing_action_without_a_confirmed_read():
    bad = pp.PricePriorityReport(
        actions=(pp.SkuPriceAction(
            product_id="BAD", action=pp.IGUALAR_PRECIO, abc="A", xyz="X",
            position_index=None, competitor_read=pp.INSUFFICIENT_SIGNAL, reason="guessed it",
        ),),
        excluded=(), n_igualar_precio=1, n_oportunidad_subir=0, n_vigilar=0,
        n_ignorar_bajo_valor=0, n_excluded=0, band=0.05, summary="",
    )
    issues = pp.verify(bad)
    assert any("fabricated price position" in i for i in issues)


def test_verify_flags_inconsistent_position_index_and_competitor_read():
    bad = pp.PricePriorityReport(
        actions=(pp.SkuPriceAction(
            product_id="BAD", action=pp.VIGILAR, abc="A", xyz="X",
            position_index=1.02, competitor_read=pp.INSUFFICIENT_SIGNAL, reason="watching",
        ),),
        excluded=(), n_igualar_precio=0, n_oportunidad_subir=0, n_vigilar=1,
        n_ignorar_bajo_valor=0, n_excluded=0, band=0.05, summary="",
    )
    issues = pp.verify(bad)
    assert any("insufficient_signal" in i for i in issues)


def test_verify_passes_on_a_well_formed_report():
    payload = {
        "classifications": [
            _classification(product_id="P1", abc="A"),
            _classification(product_id="P2", abc="C"),
        ],
        "price_positions": [
            _position(product_id="P1", position_index=1.10, competitor_read=pp.CONFIRMED),
            _position(product_id="P2", position_index=None, competitor_read=pp.INSUFFICIENT_SIGNAL),
        ],
    }
    report = pp.run(payload)
    assert pp.verify(report) == []
    assert pp.price_priority_passed(report) is True


# ---- write_operational -------------------------------------------------------------


def test_write_operational_emits_action_and_excluded_csvs(tmp_path):
    payload = {
        "classifications": [
            _classification(product_id="P1", abc="A"),
            _classification(product_id="ONLY_ABC", abc="B"),
        ],
        "price_positions": [
            _position(product_id="P1", position_index=1.10, competitor_read=pp.CONFIRMED),
            _position(product_id="ONLY_PRICE", position_index=1.0, competitor_read=pp.CONFIRMED),
        ],
    }
    report = pp.run(payload)
    out = pp.write_operational(report, tmp_path)
    action_df = pd.read_csv(out["csv"])
    excluded_df = pd.read_csv(out["excluded_csv"])
    assert set(action_df["product_id"]) == {"P1"}
    assert set(excluded_df["product_id"]) == {"ONLY_ABC", "ONLY_PRICE"}
    assert list(action_df.columns) == list(pp._ACTION_CSV_COLUMNS)


def test_write_operational_on_empty_report_writes_header_only(tmp_path):
    empty = pp.PricePriorityReport(
        actions=(), excluded=(), n_igualar_precio=0, n_oportunidad_subir=0, n_vigilar=0,
        n_ignorar_bajo_valor=0, n_excluded=0, band=0.05, summary="",
    )
    out = pp.write_operational(empty, tmp_path)
    action_df = pd.read_csv(out["csv"])
    assert list(action_df.columns) == list(pp._ACTION_CSV_COLUMNS)
    assert len(action_df) == 0


# ---- prepare(): CSV / PriceIntelReport wiring -------------------------------------


def _write_demand_csv(path) -> str:
    rows = []
    for pid, qty, cost in (("A", 20.0, 5.0), ("B", 2.0, 1.0)):
        for period in range(6):
            rows.append({"product_id": pid, "period": period, "quantity": qty, "unit_cost": cost})
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_price_position_csv(path) -> str:
    # SKU "A" ends up ABC class A (high value); SKU "B" ends up class C (see
    # tests/test_seo_priority.py's identical demand shape / hand-verified split).
    pd.DataFrame([
        {"product_id": "A", "our_price": 110.0, "competitor_price": 100.0},
        {"product_id": "B", "our_price": 50.0, "competitor_price": 45.0},
    ]).to_csv(path, index=False)
    return str(path)


def test_prepare_requires_a_price_source(tmp_path):
    demand = _write_demand_csv(tmp_path / "demand.csv")
    with pytest.raises(ValueError, match="price_report"):
        pp.prepare(demand, {})


def test_prepare_and_run_end_to_end_with_price_position_csv(tmp_path):
    demand = _write_demand_csv(tmp_path / "demand.csv")
    price_position = _write_price_position_csv(tmp_path / "price_position.csv")
    payload = pp.prepare(demand, {"price_position_path": price_position})
    report = pp.run(payload)

    by_id = {a.product_id: a for a in report.actions}
    assert set(by_id) == {"A", "B"}
    # A: ABC class A, position_index = 110/100 = 1.10 > 1.05 -> igualar_precio.
    assert by_id["A"].abc == "A"
    assert by_id["A"].action == pp.IGUALAR_PRECIO
    # B: ABC class C -> ignorar_bajo_valor regardless of its own position_index.
    assert by_id["B"].abc == "C"
    assert by_id["B"].action == pp.IGNORAR_BAJO_VALOR
    assert pp.verify(report) == []


def test_prepare_and_run_end_to_end_with_price_report(tmp_path):
    demand = _write_demand_csv(tmp_path / "demand.csv")
    report_our_prices = {"A": Decimal("94"), "B": Decimal("40")}
    offer_a = _offer(matched_product_id="A", competitor_sku_ref="url-a", price=Decimal("100"),
                      price_normalized=Decimal("100"))
    offer_b = _offer(matched_product_id="B", competitor_sku_ref="url-b", price=Decimal("45"),
                      price_normalized=Decimal("45"))
    price_report = PriceIntelReport(
        n_products=2, n_products_covered=2, coverage_pct=1.0, offers=(offer_a, offer_b),
        our_prices=report_our_prices,
        rows=(
            _row(product_id="A", competitor_url="url-a", offer=offer_a),
            _row(product_id="B", competitor_url="url-b", offer=offer_b),
        ),
        quarantine_rate=0.0, avg_freshness_hours=1.0, sla_hours=48.0, tier_mix={"L1": 2},
        stale_events=(), now=FIXED_NOW, summary="x",
    )
    payload = pp.prepare(demand, {"price_report": price_report})
    report = pp.run(payload)

    by_id = {a.product_id: a for a in report.actions}
    assert set(by_id) == {"A", "B"}
    # A: ABC class A, position_index = 94/100 = 0.94 < 0.95 -> oportunidad_subir.
    assert by_id["A"].abc == "A"
    assert by_id["A"].action == pp.OPORTUNIDAD_SUBIR
    # B: ABC class C -> ignorar_bajo_valor regardless of its own (pricier) position_index.
    assert by_id["B"].abc == "C"
    assert by_id["B"].action == pp.IGNORAR_BAJO_VALOR
    assert pp.verify(report) == []
