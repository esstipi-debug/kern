"""Jurisdictional Regulatory Framework for International Logistics & Supply Chain.

Implements statutory constraints across 8 major economic jurisdictions:
- US: FMCSA 49 CFR Part 395, Federal Bridge Formula (23 CFR 658.17), FDA FSMA, CBP ACE
- CA: NSC Standard 9, TAC Weights & Dimensions MOU, CFIA SFCR, CBSA CARM
- AU: HVNL General Mass Limits, Standard Hours of Option, FSANZ 3.2.2, ABF/DAFF
- UK: Retained Regulation (EC) 561/2006, Construction and Use 1986, FSA, HMRC CDS/GVMS
- MX: NOM-012-SCT-2-2017, NOM-087-SCT-2-2017, SAT Complemento Carta Porte, COFEPRIS
- CL: D.S. 158 MOP Vialidad, Código del Trabajo Art. 25 bis, SII Guía de Despacho, SAG/RSA
- BR: Resolução CONTRAN nº 882/2021, Lei nº 13.103/2015, NF-e/MDF-e, ANVISA/MAPA
- EU: Regulation (EC) No 561/2006, Directive 96/53/EC, Regulation (EC) 852/2004, ICS2
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TransportRegulation:
    """Road vehicle gross weight limits and driver hours of service regulations."""
    jurisdiction_code: str
    statutory_citation: str
    max_standard_gvw_kg: float         # Standard 5/6-axle semi-trailer
    max_payload_kg: float              # Usable cargo capacity
    max_high_capacity_gvw_kg: float    # Full / B-Double / Rodotrem / Combined transport
    max_continuous_driving_hours: float
    mandatory_break_minutes: float
    max_daily_driving_hours: float
    min_daily_rest_hours: float


@dataclass(frozen=True)
class CustomsSanitaryBuffer:
    """Border clearance, inspection lead times, and mandatory electronic transport manifests."""
    statutory_manifest_name: str       # e.g., "Carta Porte (SAT)", "Guía de Despacho (SII)"
    manifest_mandatory_before_dispatch: bool
    standard_customs_clearance_days: float
    sanitary_inspection_buffer_days: float
    authority_name: str                # e.g., "FDA / CBP", "CFIA / CBSA", "DAFF / ABF"


@dataclass(frozen=True)
class FinancialMacroPolicy:
    """Macroeconomic inventory carrying costs and benchmark interest rates."""
    benchmark_interest_rate: float
    default_annual_holding_cost_rate: float
    local_currency: str


@dataclass(frozen=True)
class JurisdictionProfile:
    """Complete statutory operational profile for a country or trading bloc."""
    code: str
    country_name: str
    transport: TransportRegulation
    customs: CustomsSanitaryBuffer
    financial: FinancialMacroPolicy
    cold_chain_standard: str
    hazmat_standard: str


# Built-in statutory profiles for the 8 jurisdictions
_BUILTIN_JURISDICTIONS: dict[str, JurisdictionProfile] = {
    "us": JurisdictionProfile(
        code="us",
        country_name="United States",
        transport=TransportRegulation(
            jurisdiction_code="us",
            statutory_citation="FMCSA 49 CFR Part 395 & Federal Bridge Formula (23 CFR § 658.17)",
            max_standard_gvw_kg=36287.4,       # 80,000 lbs
            max_payload_kg=20412.0,            # ~45,000 lbs
            max_high_capacity_gvw_kg=47627.0,  # ~105,000 lbs LCV permit
            max_continuous_driving_hours=8.0,
            mandatory_break_minutes=30.0,
            max_daily_driving_hours=11.0,
            min_daily_rest_hours=10.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="ACE e-Manifest (CBP)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=1.0,
            sanitary_inspection_buffer_days=3.0,
            authority_name="FDA / USDA / CBP",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.050,
            default_annual_holding_cost_rate=0.20,
            local_currency="USD",
        ),
        cold_chain_standard="FDA FSMA 21 CFR § 1.908 (Sanitary Transportation Rule)",
        hazmat_standard="US DOT 49 CFR § 177.848",
    ),
    "ca": JurisdictionProfile(
        code="ca",
        country_name="Canada",
        transport=TransportRegulation(
            jurisdiction_code="ca",
            statutory_citation="National Safety Code (NSC) Standard 9 & TAC Weights & Dimensions MOU",
            max_standard_gvw_kg=39500.0,       # Standard 5-axle semi
            max_payload_kg=24000.0,
            max_high_capacity_gvw_kg=63500.0,  # B-Train 8-axle
            max_continuous_driving_hours=13.0, # 13h driving in 14h window
            mandatory_break_minutes=30.0,
            max_daily_driving_hours=13.0,
            min_daily_rest_hours=10.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="ACI / eManifest (CBSA)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=1.0,
            sanitary_inspection_buffer_days=2.5,
            authority_name="CFIA / CBSA CARM",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.0425,
            default_annual_holding_cost_rate=0.19,
            local_currency="CAD",
        ),
        cold_chain_standard="CFIA Safe Food for Canadians Regulations (SFCR)",
        hazmat_standard="Transportation of Dangerous Goods (TDG) Regulations",
    ),
    "au": JurisdictionProfile(
        code="au",
        country_name="Australia",
        transport=TransportRegulation(
            jurisdiction_code="au",
            statutory_citation="Heavy Vehicle National Law (HVNL) - General Mass Limits (GML)",
            max_standard_gvw_kg=42500.0,       # 6-axle semi GML
            max_payload_kg=26000.0,
            max_high_capacity_gvw_kg=62500.0,  # B-Double GML
            max_continuous_driving_hours=5.25,
            mandatory_break_minutes=15.0,
            max_daily_driving_hours=12.0,
            min_daily_rest_hours=7.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="Integrated Cargo System (ICS / ABF)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=1.5,
            sanitary_inspection_buffer_days=4.0,
            authority_name="DAFF (Biosecurity) / ABF",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.0435,
            default_annual_holding_cost_rate=0.19,
            local_currency="AUD",
        ),
        cold_chain_standard="FSANZ Standard 3.2.2 (Food Safety Practices)",
        hazmat_standard="Australian Dangerous Goods Code (ADG 7.8) Chapter 9.2",
    ),
    "uk": JurisdictionProfile(
        code="uk",
        country_name="United Kingdom",
        transport=TransportRegulation(
            jurisdiction_code="uk",
            statutory_citation="Road Vehicles (Construction & Use) 1986 & Retained Reg (EC) 561/2006",
            max_standard_gvw_kg=44000.0,       # 6-axle articulated vehicle
            max_payload_kg=28000.0,
            max_high_capacity_gvw_kg=44000.0,
            max_continuous_driving_hours=4.5,
            mandatory_break_minutes=45.0,
            max_daily_driving_hours=9.0,
            min_daily_rest_hours=11.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="GVMS / GMR (Goods Movement Record - HMRC)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=1.5,
            sanitary_inspection_buffer_days=3.0,
            authority_name="FSA / DEFRA / HMRC CDS",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.050,
            default_annual_holding_cost_rate=0.20,
            local_currency="GBP",
        ),
        cold_chain_standard="Food Safety and Hygiene (England) Regulations 2013 Sched 4 (8°C)",
        hazmat_standard="Carriage of Dangerous Goods (CDG) / ADR",
    ),
    "mx": JurisdictionProfile(
        code="mx",
        country_name="México",
        transport=TransportRegulation(
            jurisdiction_code="mx",
            statutory_citation="NOM-012-SCT-2-2017 (Pesos y Dimensiones) & NOM-087-SCT-2-2017 (Horas Conducción)",
            max_standard_gvw_kg=46500.0,       # T3-S2 en camino ET/A
            max_payload_kg=27000.0,
            max_high_capacity_gvw_kg=66500.0,  # Full T3-S2-R4 autorregulado
            max_continuous_driving_hours=5.0,
            mandatory_break_minutes=30.0,
            max_daily_driving_hours=14.0,
            min_daily_rest_hours=8.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="Complemento Carta Porte (SAT / CFDI)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=2.0,
            sanitary_inspection_buffer_days=5.0,
            authority_name="COFEPRIS / SENASICA / SAT Aduanas",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.105,
            default_annual_holding_cost_rate=0.28,
            local_currency="MXN",
        ),
        cold_chain_standard="NOM-251-SSA1-2009 (Prácticas de higiene y refrigeración)",
        hazmat_standard="NOM-010-SCT2/2009 & NOM-002-SCT/2011",
    ),
    "cl": JurisdictionProfile(
        code="cl",
        country_name="Chile",
        transport=TransportRegulation(
            jurisdiction_code="cl",
            statutory_citation="Decreto Supremo MOP Nº 158 / 80 & Código del Trabajo Art. 25 bis",
            max_standard_gvw_kg=45000.0,       # 45 t PBV máximo nacional
            max_payload_kg=26500.0,
            max_high_capacity_gvw_kg=45000.0,
            max_continuous_driving_hours=5.0,
            mandatory_break_minutes=120.0,     # Descanso obligatorio 2h mín tras 5h
            max_daily_driving_hours=10.0,
            min_daily_rest_hours=10.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="Guía de Despacho Electrónica (SII)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=1.5,
            sanitary_inspection_buffer_days=3.5,
            authority_name="SAG / ISP / Servicio Nacional de Aduanas",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.055,
            default_annual_holding_cost_rate=0.22,
            local_currency="CLP",
        ),
        cold_chain_standard="Decreto Supremo Nº 977/96 (Reglamento Sanitario de los Alimentos)",
        hazmat_standard="NCh 382 / NCh 2120 & D.S. 298 MMTT",
    ),
    "br": JurisdictionProfile(
        code="br",
        country_name="Brasil",
        transport=TransportRegulation(
            jurisdiction_code="br",
            statutory_citation="Resolução CONTRAN nº 882/2021 & Lei nº 13.103/2015 (Lei do Motorista)",
            max_standard_gvw_kg=45000.0,       # Cavalo 6x2 + Semirreboque 3 eixos (PBTC)
            max_payload_kg=27000.0,
            max_high_capacity_gvw_kg=57000.0,  # Bitrem 7 eixos (57t) / Rodotrem 74t c/ AET
            max_continuous_driving_hours=5.5,
            mandatory_break_minutes=30.0,
            max_daily_driving_hours=11.0,
            min_daily_rest_hours=11.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="MDF-e / NF-e (Manifesto Eletrônico de Documentos Fiscais)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=3.0,
            sanitary_inspection_buffer_days=6.0,
            authority_name="ANVISA / MAPA / Receita Federal do Brasil",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.1075,
            default_annual_holding_cost_rate=0.30,
            local_currency="BRL",
        ),
        cold_chain_standard="ANVISA RDC nº 216/2004 & RDC nº 331/2019",
        hazmat_standard="ANTT Resolução nº 5.998/2022",
    ),
    "eu": JurisdictionProfile(
        code="eu",
        country_name="European Union",
        transport=TransportRegulation(
            jurisdiction_code="eu",
            statutory_citation="Regulation (EC) No 561/2006 & Council Directive 96/53/EC",
            max_standard_gvw_kg=40000.0,       # Standard 5-axle
            max_payload_kg=25000.0,
            max_high_capacity_gvw_kg=44000.0,  # Combined intermodal transport
            max_continuous_driving_hours=4.5,
            mandatory_break_minutes=45.0,
            max_daily_driving_hours=9.0,
            min_daily_rest_hours=11.0,
        ),
        customs=CustomsSanitaryBuffer(
            statutory_manifest_name="EU Single Window / Entry Summary Declaration (ENS - ICS2)",
            manifest_mandatory_before_dispatch=True,
            standard_customs_clearance_days=2.0,
            sanitary_inspection_buffer_days=4.0,
            authority_name="EFSA / TRACES / National Customs",
        ),
        financial=FinancialMacroPolicy(
            benchmark_interest_rate=0.035,
            default_annual_holding_cost_rate=0.18,
            local_currency="EUR",
        ),
        cold_chain_standard="Regulation (EC) No 852/2004 & 853/2004 (Food Hygiene)",
        hazmat_standard="Agreement concerning the Carriage of Dangerous Goods by Road (ADR)",
    ),
}


def load_jurisdiction_profile(country_code: str, *, config_dir: Path | str | None = None) -> JurisdictionProfile:
    """Load jurisdiction profile from YAML file if available, falling back to built-in constants."""
    norm_code = country_code.strip().lower()

    if config_dir:
        path = Path(config_dir) / f"{norm_code}.yaml"
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and "code" in raw:
                # Parse YAML into typed profile
                t_raw = raw.get("transport", {})
                c_raw = raw.get("customs", {})
                f_raw = raw.get("financial", {})
                return JurisdictionProfile(
                    code=norm_code,
                    country_name=raw.get("country_name", norm_code.upper()),
                    transport=TransportRegulation(
                        jurisdiction_code=norm_code,
                        statutory_citation=t_raw.get("statutory_citation", ""),
                        max_standard_gvw_kg=float(t_raw.get("max_standard_gvw_kg", 40000.0)),
                        max_payload_kg=float(t_raw.get("max_payload_kg", 25000.0)),
                        max_high_capacity_gvw_kg=float(t_raw.get("max_high_capacity_gvw_kg", 44000.0)),
                        max_continuous_driving_hours=float(t_raw.get("max_continuous_driving_hours", 4.5)),
                        mandatory_break_minutes=float(t_raw.get("mandatory_break_minutes", 45.0)),
                        max_daily_driving_hours=float(t_raw.get("max_daily_driving_hours", 9.0)),
                        min_daily_rest_hours=float(t_raw.get("min_daily_rest_hours", 11.0)),
                    ),
                    customs=CustomsSanitaryBuffer(
                        statutory_manifest_name=c_raw.get("statutory_manifest_name", "Electronic Manifest"),
                        manifest_mandatory_before_dispatch=bool(c_raw.get("manifest_mandatory_before_dispatch", True)),
                        standard_customs_clearance_days=float(c_raw.get("standard_customs_clearance_days", 1.5)),
                        sanitary_inspection_buffer_days=float(c_raw.get("sanitary_inspection_buffer_days", 3.0)),
                        authority_name=c_raw.get("authority_name", "Customs Authority"),
                    ),
                    financial=FinancialMacroPolicy(
                        benchmark_interest_rate=float(f_raw.get("benchmark_interest_rate", 0.05)),
                        default_annual_holding_cost_rate=float(f_raw.get("default_annual_holding_cost_rate", 0.20)),
                        local_currency=f_raw.get("local_currency", "USD"),
                    ),
                    cold_chain_standard=raw.get("cold_chain_standard", "Standard Temperature Control"),
                    hazmat_standard=raw.get("hazmat_standard", "UN Recommendations Model Regulations"),
                )

    if norm_code in _BUILTIN_JURISDICTIONS:
        return _BUILTIN_JURISDICTIONS[norm_code]

    raise ValueError(
        f"Jurisdiction '{country_code}' not supported. Must be one of: {list(_BUILTIN_JURISDICTIONS.keys())}"
    )


@dataclass(frozen=True)
class WeightComplianceResult:
    """Audit of total gross vehicle weight against statutory road limits."""
    cargo_weight_kg: float
    tare_weight_kg: float
    total_gvw_kg: float
    statutory_max_gvw_kg: float
    is_compliant: bool
    overweight_kg: float
    minimum_trucks_required: int
    statutory_citation: str
    verdict: str


def validate_transport_weight(
    cargo_weight_kg: float,
    jurisdiction: str,
    *,
    tare_weight_kg: float = 14000.0,  # Standard tractor + dry trailer tare
    is_high_capacity_permit: bool = False,
) -> WeightComplianceResult:
    """Validate cargo against jurisdiction gross weight limits, calculating truck count if exceeded."""
    profile = load_jurisdiction_profile(jurisdiction)
    max_gvw = (
        profile.transport.max_high_capacity_gvw_kg
        if is_high_capacity_permit
        else profile.transport.max_standard_gvw_kg
    )
    max_payload = max_gvw - tare_weight_kg

    total_gvw = cargo_weight_kg + tare_weight_kg
    is_compliant = total_gvw <= max_gvw
    overweight_kg = max(0.0, total_gvw - max_gvw)

    trucks_required = max(1, math.ceil(cargo_weight_kg / max_payload)) if cargo_weight_kg > 0 else 0

    if is_compliant:
        verdict = "WEIGHT_COMPLIANT"
    else:
        verdict = f"OVERWEIGHT_BY_{round(overweight_kg, 1)}KG_REQUIRES_{trucks_required}_TRUCKS"

    return WeightComplianceResult(
        cargo_weight_kg=cargo_weight_kg,
        tare_weight_kg=tare_weight_kg,
        total_gvw_kg=total_gvw,
        statutory_max_gvw_kg=max_gvw,
        is_compliant=is_compliant,
        overweight_kg=round(overweight_kg, 1),
        minimum_trucks_required=trucks_required,
        statutory_citation=profile.transport.statutory_citation,
        verdict=verdict,
    )


@dataclass(frozen=True)
class DriverHoursAudit:
    """Audit of driving hours against statutory Hours of Service (HOS) rules."""
    transit_duration_hours: float
    jurisdiction: str
    is_single_driver_feasible_without_overnight: bool
    mandatory_break_hours: float
    overnight_rest_days: int
    total_elapsed_delivery_hours: float
    statutory_citation: str
    verdict: str


def validate_driver_hours(
    transit_duration_hours: float,
    jurisdiction: str,
) -> DriverHoursAudit:
    """Calculate legally mandated driver breaks, overnights, and total transit elapsed time."""
    profile = load_jurisdiction_profile(jurisdiction)
    t = profile.transport

    if transit_duration_hours <= 0:
        return DriverHoursAudit(
            transit_duration_hours=0.0,
            jurisdiction=jurisdiction,
            is_single_driver_feasible_without_overnight=True,
            mandatory_break_hours=0.0,
            overnight_rest_days=0,
            total_elapsed_delivery_hours=0.0,
            statutory_citation=t.statutory_citation,
            verdict="ZERO_TRANSIT_TIME",
        )

    # 1. Short mandatory breaks (e.g. 45 min after 4.5h)
    breaks_count = math.floor(transit_duration_hours / t.max_continuous_driving_hours)
    # If exactly on boundary, no break needed at the final destination
    if transit_duration_hours % t.max_continuous_driving_hours == 0 and breaks_count > 0:
        breaks_count -= 1
    total_break_hours = (breaks_count * t.mandatory_break_minutes) / 60.0

    # 2. Daily driving limit & overnight rests
    overnight_days = math.floor(transit_duration_hours / t.max_daily_driving_hours)
    if transit_duration_hours % t.max_daily_driving_hours == 0 and overnight_days > 0:
        overnight_days -= 1
    total_overnight_hours = overnight_days * t.min_daily_rest_hours

    total_elapsed = transit_duration_hours + total_break_hours + total_overnight_hours
    single_driver_day = overnight_days == 0

    if single_driver_day:
        verdict = "FEASIBLE_SINGLE_SHIFT"
    else:
        verdict = f"MULTI_DAY_ROUTE_REQUIRES_{overnight_days}_OVERNIGHT_REST_PERIODS"

    return DriverHoursAudit(
        transit_duration_hours=transit_duration_hours,
        jurisdiction=jurisdiction,
        is_single_driver_feasible_without_overnight=single_driver_day,
        mandatory_break_hours=round(total_break_hours, 2),
        overnight_rest_days=overnight_days,
        total_elapsed_delivery_hours=round(total_elapsed, 2),
        statutory_citation=t.statutory_citation,
        verdict=verdict,
    )


@dataclass(frozen=True)
class CrossBorderLeadTimeBreakdown:
    """Breakdown of international lead time including customs clearance and biosecurity buffers."""
    origin_jurisdiction: str
    destination_jurisdiction: str
    base_transit_days: float
    customs_clearance_days: float
    sanitary_inspection_days: float
    total_effective_lead_time_days: float
    electronic_manifest_required: str
    is_cross_border: bool
    verdict: str


def compute_cross_border_lead_time(
    base_transit_days: float,
    origin_country: str,
    destination_country: str,
    *,
    requires_sanitary_inspection: bool = False,
) -> CrossBorderLeadTimeBreakdown:
    """Calculate realistic lead time adding statutory border clearance and sanitary buffers."""
    norm_orig = origin_country.strip().lower()
    norm_dest = destination_country.strip().lower()

    dest_profile = load_jurisdiction_profile(norm_dest)

    # Intra-country or intra-EU trade
    is_intra_eu = (norm_orig == "eu" and norm_dest == "eu")
    is_domestic = (norm_orig == norm_dest)

    if is_domestic or is_intra_eu:
        return CrossBorderLeadTimeBreakdown(
            origin_jurisdiction=norm_orig,
            destination_jurisdiction=norm_dest,
            base_transit_days=base_transit_days,
            customs_clearance_days=0.0,
            sanitary_inspection_days=0.0,
            total_effective_lead_time_days=base_transit_days,
            electronic_manifest_required=dest_profile.customs.statutory_manifest_name,
            is_cross_border=False,
            verdict="DOMESTIC_OR_COMMON_MARKET_TRANSIT",
        )

    # International Cross-Border
    c = dest_profile.customs
    customs_days = c.standard_customs_clearance_days
    sanitary_days = c.sanitary_inspection_buffer_days if requires_sanitary_inspection else 0.0

    total_days = base_transit_days + customs_days + sanitary_days

    verdict = (
        "CROSS_BORDER_WITH_SANITARY_BIOSECURITY_BUFFER"
        if requires_sanitary_inspection
        else "CROSS_BORDER_STANDARD_CUSTOMS"
    )

    return CrossBorderLeadTimeBreakdown(
        origin_jurisdiction=norm_orig,
        destination_jurisdiction=norm_dest,
        base_transit_days=base_transit_days,
        customs_clearance_days=customs_days,
        sanitary_inspection_days=sanitary_days,
        total_effective_lead_time_days=round(total_days, 2),
        electronic_manifest_required=c.statutory_manifest_name,
        is_cross_border=True,
        verdict=verdict,
    )
