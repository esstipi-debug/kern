"""Tests for the Sexto Sombrero ('merma' / Spoilage & Quality) in src/hats.py.

Verifies:
1. Non-perishable SKU cleanly excludes merma from evaluable_hats and reports it in excluded_hats.
2. Perishable SKU includes merma in evaluable_hats, tension_map, and settlement acta.
3. Manageable lot sizes within absorption capacity score 1.0 (zero spoilage).
4. Volume discount lots exceeding shelf-life absorption capacity receive near-zero scores
   and trigger hard objections / vetoes.
5. Channel gating and transit lead time properly reduce effective shelf life.
"""

import pytest

from src.hat_council import settle, tension_map
from src.hats import (
    HAT_CFO,
    HAT_COMERCIAL,
    HAT_COMPRADOR,
    HAT_KEYS,
    HAT_MERMA,
    HAT_PLANNER,
    Candidate,
    FreightProfile,
    HatConfig,
    HatInputs,
    PerishableProfile,
    _u_merma,
    build_inputs,
    evaluable_hats,
    hat_kpis,
    headline_kpi,
    parse_weights,
    perishable_limit,
    spoilage_risk_usd_at,
    spoilage_units_at,
)
from src.objection import OBJETA, judge


def _build_perishable(
    shelf_life_days: float = 30.0,
    channel_gating_pct: float = 0.0,
    transit_lead_time_days: float = 0.0,
    spoilage_tolerance_units: float = 0.0,
    annual_demand: float = 3650.0,  # 10 units/day
    mean_weekly: float = 70.0,
    std_weekly: float = 10.0,
    lead_time_weeks: float = 1.0,
    unit_cost: float = 10.0,
    moq: float = 0.0,
    freight: FreightProfile | None = None,
) -> HatInputs:
    p_profile = PerishableProfile(
        shelf_life_days=shelf_life_days,
        channel_gating_pct=channel_gating_pct,
        transit_lead_time_days=transit_lead_time_days,
        spoilage_tolerance_units=spoilage_tolerance_units,
    )
    return build_inputs(
        sku="PERISH-SKU",
        annual_demand=annual_demand,
        mean_weekly=mean_weekly,
        std_weekly=std_weekly,
        lead_time_weeks=lead_time_weeks,
        unit_cost=unit_cost,
        config=HatConfig(order_cost=50.0, holding_rate=0.25, sl_target=0.95),
        perishable=p_profile,
        freight=freight,
        minimum_order_quantity=moq,
    )


def test_non_perishable_sku_excludes_merma():
    """Non-perishable SKU has perishable=None -> merma is excluded from evaluable_hats."""
    inp = build_inputs(
        sku="DRY-SKU",
        annual_demand=5200.0,
        mean_weekly=100.0,
        std_weekly=20.0,
        lead_time_weeks=2.0,
        unit_cost=10.0,
        config=HatConfig(),
        perishable=None,
    )
    evaluables = evaluable_hats(inp)
    assert HAT_MERMA not in evaluables
    assert evaluables == (HAT_COMPRADOR, HAT_PLANNER, HAT_CFO, HAT_COMERCIAL)

    # In tension map, merma is explicitly reported in excluded_hats
    tmap = tension_map(inp)
    assert HAT_MERMA in tmap.excluded_hats
    assert HAT_MERMA not in tmap.ideals

    # In settlement, merma is in excluded_hats
    s = settle(inp, None)
    assert HAT_MERMA in s.excluded_hats
    assert HAT_MERMA not in [e.hat_key for e in s.acta]

    # Querying KPIs for merma raises ValueError rather than fabricating zero spoilage
    with pytest.raises(ValueError, match="no PerishableProfile declared"):
        hat_kpis(inp, HAT_MERMA, Candidate(100.0, 0.95))


def test_perishable_sku_includes_merma():
    """Perishable SKU has PerishableProfile -> merma is evaluable and included in council."""
    inp = _build_perishable(shelf_life_days=30.0)
    evaluables = evaluable_hats(inp)
    assert HAT_MERMA in evaluables
    assert evaluables == (HAT_COMPRADOR, HAT_PLANNER, HAT_CFO, HAT_COMERCIAL, HAT_MERMA)

    # With freight too, all 6 hats are on the table
    inp_with_freight = _build_perishable(
        shelf_life_days=30.0,
        freight=FreightProfile(weight_kg_per_unit=1.0, distance_km=200.0),
    )
    assert evaluable_hats(inp_with_freight) == HAT_KEYS

    tmap = tension_map(inp)
    assert HAT_MERMA not in tmap.excluded_hats
    assert HAT_MERMA in tmap.ideals
    assert tmap.ideals[HAT_MERMA].candidate.order_quantity > 0

    s = settle(inp, None)
    assert HAT_MERMA not in s.excluded_hats
    assert HAT_MERMA in [e.hat_key for e in s.acta]
    assert headline_kpi(HAT_MERMA) == "spoilage_risk_usd"


def test_manageable_lot_size_scores_one_zero_spoilage():
    """Manageable lot sizes within physical absorption capacity score 1.0 (zero spoilage)."""
    # 10 units/day, 30 days life = 300 units absorption capacity
    # Lead time 1 wk (70 units) + SS (~16 units) = ROP ~86
    # Max absorbable = 300 - 86 = 214 units
    inp = _build_perishable(shelf_life_days=30.0)
    cand = Candidate(order_quantity=100.0, service_level=0.95)

    assert spoilage_units_at(inp, cand) == 0.0
    assert spoilage_risk_usd_at(inp, cand) == 0.0

    score = _u_merma(inp, cand)
    assert score == pytest.approx(1.0)

    kpis = hat_kpis(inp, HAT_MERMA, cand)
    assert kpis["spoilage_risk_usd"] == 0.0
    assert kpis["spoilage_units"] == 0.0
    assert kpis["max_absorbable_units"] > 100.0
    assert kpis["effective_shelf_life_days"] == 30.0


def test_large_volume_discount_lot_exceeding_shelf_life_near_zero_score_and_veto():
    """Volume discount buy (e.g. 5x EOQ) exceeding shelf-life absorption capacity
    receives a near-zero utility score and triggers a hard objection / veto."""
    # Daily demand = 10 units/day.
    # Shelf life = 20 days -> Absorption capacity = 200 units.
    # Reorder point: 70 (lead time demand) + ss (~16.4) = ~86.4 units.
    # Max absorbable units = 200 - 86.4 = ~113.6 units.
    # With order_cost = 15.0, EOQ is ~209, grid span starts around ~104.
    p_profile = PerishableProfile(shelf_life_days=20.0)
    inp = build_inputs(
        sku="PERISH-VETO",
        annual_demand=3650.0,
        mean_weekly=70.0,
        std_weekly=10.0,
        lead_time_weeks=1.0,
        unit_cost=10.0,
        config=HatConfig(order_cost=15.0, holding_rate=0.25, sl_target=0.95),
        perishable=p_profile,
    )
    p_lim = perishable_limit(inp, Candidate(100.0, 0.95))
    max_absorbable = p_lim.max_absorbable_units
    assert 100.0 < max_absorbable < 125.0

    # Compras attempts to order a large batch of 500 units (more than 4x absorption capacity):
    cand_large = Candidate(order_quantity=500.0, service_level=0.95)
    spoilage = spoilage_units_at(inp, cand_large)
    assert spoilage > 350.0  # ~386 units will spoil!
    spoilage_usd = spoilage_risk_usd_at(inp, cand_large)
    assert spoilage_usd > 3500.0

    # Utility score drops sharply towards 0.0
    score_large = _u_merma(inp, cand_large)
    assert score_large < 0.01  # near-zero score!

    # Objection verdict: proposed large lot deteriorates spoilage exposure from $0 to >$3500
    # With a tolerance of $50, merma issues a hard veto (OBJETA)
    verdict = judge(
        HAT_MERMA,
        kpi_current=0.0,
        kpi_proposed=spoilage_usd,
        threshold=50.0,
    )
    assert verdict.status == OBJETA
    assert verdict.deterioration == pytest.approx(spoilage_usd)
    assert verdict.kpi_name == "spoilage_risk_usd"

    # In settlement with merma having appropriate weight (e.g. 0.30):
    weights = parse_weights(
        {"comprador": 0.15, "planner": 0.20, "cfo": 0.15, "comercial": 0.20, "merma": 0.30},
        evaluable_hats(inp),
    )
    s = settle(inp, weights)
    # Chosen lot quantity MUST respect shelf life and avoid massive spoilage
    assert s.chosen.order_quantity <= max_absorbable + 5.0
    assert s.chosen.order_quantity < 120.0  # far below the 500 speculative buy!


def test_channel_gating_and_transit_lead_time_reduce_usable_shelf_life():
    """Retailer channel gating (e.g. 50% life required) and transit lead time
    discount the effective shelf life horizon."""
    # Total shelf life = 60 days
    # Supermarket gating = 50% (channel accepts with >=50% remaining life)
    # Transit lead time = 6 days
    # Usable commercial life = 60 * (1 - 0.50) - 6 = 24 days
    profile = PerishableProfile(
        shelf_life_days=60.0,
        channel_gating_pct=0.50,
        transit_lead_time_days=6.0,
    )
    assert profile.effective_shelf_life_days == 24.0

    inp = _build_perishable(
        shelf_life_days=60.0,
        channel_gating_pct=0.50,
        transit_lead_time_days=6.0,
    )
    kpis = hat_kpis(inp, HAT_MERMA, Candidate(100.0, 0.95))
    assert kpis["effective_shelf_life_days"] == 24.0


def test_perishable_profile_validation():
    """PerishableProfile validates bounds on shelf life, gating %, and transit days."""
    with pytest.raises(ValueError, match="shelf_life_days must be > 0"):
        PerishableProfile(shelf_life_days=0.0)

    with pytest.raises(ValueError, match="channel_gating_pct must be in"):
        PerishableProfile(shelf_life_days=30.0, channel_gating_pct=1.0)

    with pytest.raises(ValueError, match="transit_lead_time_days must be >= 0"):
        PerishableProfile(shelf_life_days=30.0, transit_lead_time_days=-1.0)

    with pytest.raises(ValueError, match="spoilage_tolerance_units must be >= 0"):
        PerishableProfile(shelf_life_days=30.0, spoilage_tolerance_units=-5.0)
