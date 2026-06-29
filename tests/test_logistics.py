"""Tests for the offline logistics engine: transport-mode selection + lane freight."""

from src.logistics.freight import FreightLine, lane_cost_to_serve
from src.logistics.modes import (
    FreightRates,
    Shipment,
    ltl_ftl_breakeven_kg,
    quote_modes,
    select_mode,
)

R = FreightRates()


def test_small_light_shipment_picks_parcel():
    sel = select_mode(Shipment("s1", weight_kg=10, distance_km=200), R)
    assert sel.recommended_mode == "parcel"
    assert sel.recommended_cost == 25.0          # 2.5 * 10
    assert sel.savings_vs_next == 35.0           # next is LTL at the 60 minimum


def test_ltl_minimum_charge_applies():
    quotes = {q.mode: q for q in quote_modes(Shipment("s", 10, 200), R)}
    assert quotes["ltl"].cost == 60.0            # max(60, 0.0009*10*200=1.8)


def test_heavy_long_haul_picks_ftl_over_ltl_and_intermodal():
    sel = select_mode(Shipment("s2", weight_kg=15000, distance_km=1000), R)
    by = {q.mode: q for q in sel.quotes}
    assert sel.recommended_mode == "ftl"
    assert by["ftl"].cost == 1800.0              # 1.8 * 1000 * 1 truck
    assert by["ltl"].cost == 13500.0             # 0.0009 * 15000 * 1000
    assert by["intermodal"].feasible is True     # 1000 km >= 700 km min


def test_ftl_rounds_up_to_whole_trucks():
    by = {q.mode: q for q in quote_modes(Shipment("s", 25000, 500), R)}
    assert by["ftl"].cost == 1800.0              # 1.8 * 500 * ceil(25000/20000)=2
    assert by["parcel"].feasible is False        # over the 30 kg cap


def test_intermodal_infeasible_below_min_distance():
    by = {q.mode: q for q in quote_modes(Shipment("s", 500, 300), R)}
    assert by["intermodal"].feasible is False
    assert "intermodal minimum" in by["intermodal"].reason


def test_transit_cap_flips_ltl_to_ftl():
    # weight 500 (parcel out), 300 km (intermodal out): LTL is cheapest but slow (4 d)
    sel = select_mode(Shipment("s3", 500, 300), R, max_transit_days=3)
    by = {q.mode: q for q in sel.quotes}
    assert by["ltl"].feasible is False and "transit" in by["ltl"].reason
    assert sel.recommended_mode == "ftl"         # only mode within the 3-day limit


def test_ltl_ftl_breakeven_weight():
    assert ltl_ftl_breakeven_kg(R) == 2000.0     # 1.8 / 0.0009


def test_lane_cost_to_serve_aggregates_and_ranks():
    lines = [
        FreightLine("A->B", 100, units=10, order_value=1000, weight_kg=50),
        FreightLine("A->B", 50, units=5, order_value=500, weight_kg=25),
        FreightLine("A->C", 300, units=2, order_value=400, weight_kg=300),
    ]
    lanes = lane_cost_to_serve(lines)
    assert [lc.lane for lc in lanes] == ["A->C", "A->B"]      # ranked by total freight desc
    by = {lc.lane: lc for lc in lanes}
    assert by["A->B"].shipments == 2
    assert by["A->B"].total_freight == 150.0
    assert by["A->B"].freight_per_unit == 10.0               # 150 / 15
    assert by["A->B"].freight_pct_of_value == 0.1            # 150 / 1500
    assert by["A->C"].freight_pct_of_value == 0.75           # 300 / 400
