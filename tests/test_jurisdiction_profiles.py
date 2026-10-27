"""Tests for src/regulatory/jurisdictions.py across 8 statutory jurisdictions."""

from pathlib import Path

import pytest

from src.regulatory.jurisdictions import (
    compute_cross_border_lead_time,
    load_jurisdiction_profile,
    validate_driver_hours,
    validate_transport_weight,
)

CONFIG_DIR = Path("config/jurisdictions")
ALL_8_JURISDICTIONS = ["us", "ca", "au", "uk", "mx", "cl", "br", "eu"]


@pytest.mark.parametrize("code", ALL_8_JURISDICTIONS)
def test_all_8_jurisdictions_load_declaratively(code):
    profile = load_jurisdiction_profile(code, config_dir=CONFIG_DIR)
    assert profile.code == code
    assert len(profile.country_name) > 0
    assert profile.transport.max_standard_gvw_kg > 20000.0
    assert profile.transport.max_daily_driving_hours > 0.0
    assert profile.customs.manifest_mandatory_before_dispatch
    assert len(profile.customs.statutory_manifest_name) > 0
    assert profile.financial.benchmark_interest_rate > 0.0
    assert profile.financial.default_annual_holding_cost_rate > 0.10


def test_transport_weight_compliance_us_vs_mexico():
    # Cargo: 25,000 kg, Tare: 14,000 kg -> Total GVW: 39,000 kg
    # US standard limit: 36,287.4 kg (80,000 lbs) -> Overweight in US!
    us_audit = validate_transport_weight(
        cargo_weight_kg=25000.0,
        jurisdiction="us",
        tare_weight_kg=14000.0,
    )
    assert not us_audit.is_compliant
    assert us_audit.overweight_kg == pytest.approx(2712.6, abs=1.0)
    assert us_audit.minimum_trucks_required == 2
    assert "OVERWEIGHT" in us_audit.verdict

    # Mexico NOM-012 T3-S2 limit: 46,500 kg -> Perfectly compliant in Mexico!
    mx_audit = validate_transport_weight(
        cargo_weight_kg=25000.0,
        jurisdiction="mx",
        tare_weight_kg=14000.0,
    )
    assert mx_audit.is_compliant
    assert mx_audit.overweight_kg == 0.0
    assert mx_audit.minimum_trucks_required == 1
    assert mx_audit.verdict == "WEIGHT_COMPLIANT"


def test_high_capacity_permits_canada_and_australia():
    # 35,000 kg cargo + 14,000 kg tare = 49,000 kg
    # In Canada, standard 5-axle is 39,500 kg (fails), but B-Train is 63,500 kg (passes!)
    ca_std = validate_transport_weight(35000.0, "ca", is_high_capacity_permit=False)
    assert not ca_std.is_compliant

    ca_btrain = validate_transport_weight(35000.0, "ca", is_high_capacity_permit=True)
    assert ca_btrain.is_compliant
    assert ca_btrain.statutory_max_gvw_kg == 63500.0

    # In Australia, GML 6-axle is 42,500 kg (fails), but B-Double is 62,500 kg (passes!)
    au_std = validate_transport_weight(35000.0, "au", is_high_capacity_permit=False)
    assert not au_std.is_compliant

    au_bdouble = validate_transport_weight(35000.0, "au", is_high_capacity_permit=True)
    assert au_bdouble.is_compliant
    assert au_bdouble.statutory_max_gvw_kg == 62500.0


def test_driver_hours_single_shift_vs_mandatory_overnight():
    # 6.0 hours transit in the US:
    # Under FMCSA, max continuous driving is 8.0h -> 0 breaks, 0 overnights, total elapsed 6.0h
    us_short = validate_driver_hours(transit_duration_hours=6.0, jurisdiction="us")
    assert us_short.is_single_driver_feasible_without_overnight
    assert us_short.mandatory_break_hours == 0.0
    assert us_short.total_elapsed_delivery_hours == 6.0

    # 10.0 hours transit in the US:
    # 1 break of 30 min (0.5h) required after 8h -> total elapsed 10.5h
    us_long = validate_driver_hours(transit_duration_hours=10.0, jurisdiction="us")
    assert us_long.is_single_driver_feasible_without_overnight
    assert us_long.mandatory_break_hours == 0.5
    assert us_long.total_elapsed_delivery_hours == 10.5

    # In UK / EU (Reg 561/2006): max continuous driving is 4.5h, daily max is 9.0h
    # 10.0 hours transit in UK: exceeds daily 9h limit -> 2 breaks (1.5h) + 1 overnight (11h)
    uk_audit = validate_driver_hours(transit_duration_hours=10.0, jurisdiction="uk")
    assert not uk_audit.is_single_driver_feasible_without_overnight
    assert uk_audit.overnight_rest_days == 1
    assert uk_audit.mandatory_break_hours == 1.5  # 2 breaks of 45m
    assert uk_audit.total_elapsed_delivery_hours == 22.5  # 10h + 1.5h breaks + 11h rest


def test_chile_mandatory_two_hour_break_after_five_hours():
    # Código del Trabajo Art. 25 bis: 5h continuous max, mandatory 2.0h rest
    cl_audit = validate_driver_hours(transit_duration_hours=6.0, jurisdiction="cl")
    assert cl_audit.mandatory_break_hours == 2.0
    assert cl_audit.total_elapsed_delivery_hours == 8.0


def test_cross_border_customs_and_sanitary_inspection_lead_times():
    # 1. Domestic transit in US: 0 customs days added
    domestic = compute_cross_border_lead_time(
        base_transit_days=3.0,
        origin_country="us",
        destination_country="us",
    )
    assert not domestic.is_cross_border
    assert domestic.total_effective_lead_time_days == 3.0

    # 2. US to Mexico with COFEPRIS/SENASICA inspection:
    # Base: 4 days + 2.0 days customs + 5.0 days sanitary inspection = 11.0 days total!
    cross_mx = compute_cross_border_lead_time(
        base_transit_days=4.0,
        origin_country="us",
        destination_country="mx",
        requires_sanitary_inspection=True,
    )
    assert cross_mx.is_cross_border
    assert cross_mx.customs_clearance_days == 2.0
    assert cross_mx.sanitary_inspection_days == 5.0
    assert cross_mx.total_effective_lead_time_days == 11.0
    assert "Carta Porte" in cross_mx.electronic_manifest_required

    # 3. US to Australia with strict island biosecurity:
    # Base: 14 days + 1.5 days customs + 4.0 days DAFF biosecurity = 19.5 days total
    cross_au = compute_cross_border_lead_time(
        base_transit_days=14.0,
        origin_country="us",
        destination_country="au",
        requires_sanitary_inspection=True,
    )
    assert cross_au.is_cross_border
    assert cross_au.total_effective_lead_time_days == 19.5
    assert "DAFF" in load_jurisdiction_profile("au").customs.authority_name
