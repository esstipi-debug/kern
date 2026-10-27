"""Cold Chain and Thermal Compatibility Engine.

Implements thermal cargo classification, refrigerated equipment matching,
and Arrhenius-based shelf-life decay modeling under cold chain temperature abuse.
Complies with:
- FDA FSMA (21 CFR Part 1.908 Sanitary Transportation of Human and Animal Food)
- EU Regulation (EC) No 852/2004 & 853/2004 (Hygiene of Foodstuffs)
- FSANZ Standard 3.2.2 (Food Safety Practices and General Requirements, Australia)
- UK Food Safety and Hygiene (England) Regulations 2013 (Schedule 4 Temperature Control)
- D.S. 977/96 RSA (Chile) & ANVISA RDC 216 / RDC 331 (Brasil)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class ThermalClass(str, Enum):
    """Standardized industrial cold chain thermal regimes."""
    DRY = "DRY"            # Ambient non-refrigerated (15°C to 25°C)
    COOL = "COOL"          # Controlled room temperature / wine / chocolate (8°C to 15°C)
    CHILLED = "CHILLED"    # Refrigerated dairy, meats, produce (0°C to 4°C / 2°C to 8°C)
    FROZEN = "FROZEN"      # Deep frozen foods (-18°C to -25°C)


def normalize_thermal_class(raw_class: str) -> ThermalClass:
    cleaned = raw_class.strip().upper()
    if cleaned in ("DRY", "AMBIENT", "SECO"):
        return ThermalClass.DRY
    if cleaned in ("COOL", "CLIMATIZADO", "TEMPERADO", "CHOCOLATE", "WINE"):
        return ThermalClass.COOL
    if cleaned in ("CHILLED", "REFRIGERATED", "REFRIGERADO", "FRESH"):
        return ThermalClass.CHILLED
    if cleaned in ("FROZEN", "DEEP_FROZEN", "CONGELADO"):
        return ThermalClass.FROZEN
    raise ValueError(f"Unrecognized thermal class: {raw_class!r}. Must be DRY, COOL, CHILLED, or FROZEN.")


@dataclass(frozen=True)
class ThermalCargoItem:
    """Item specification with strict thermal and biological tolerances."""
    sku: str
    thermal_class: ThermalClass
    target_temperature_c: float
    max_temperature_threshold_c: float
    weight_kg: float
    base_shelf_life_days: float
    q10_spoilage_factor: float = 2.0  # Standard Arrhenius rate multiplier per +10°C
    absorbs_odors: bool = False
    emits_strong_odors: bool = False

    def __post_init__(self) -> None:
        if self.target_temperature_c > self.max_temperature_threshold_c:
            raise ValueError(
                f"Target temperature ({self.target_temperature_c}°C) cannot exceed max threshold ({self.max_temperature_threshold_c}°C)"
            )
        if self.base_shelf_life_days <= 0:
            raise ValueError(f"Base shelf life must be positive, got {self.base_shelf_life_days}")


@dataclass(frozen=True)
class ReeferEquipment:
    """Refrigerated truck, container, or cold storage bay profile."""
    equipment_id: str
    supported_thermal_classes: tuple[ThermalClass, ...]
    multi_compartment: bool = False
    max_payload_weight_kg: float = 20000.0
    telemetry_logging_active: bool = True  # FSMA / HACCP required logging


@dataclass(frozen=True)
class ColdChainExposureAudit:
    """Mathematical audit of thermal abuse and accelerated spoilage."""
    sku: str
    target_temp_c: float
    actual_temp_c: float
    breach_delta_c: float
    exposure_hours: float
    acceleration_multiplier: float
    equivalent_shelf_life_days_lost: float
    remaining_effective_shelf_life_days: float
    is_safe_for_consumption: bool
    verdict: str


def calculate_cold_chain_thermal_decay(
    item: ThermalCargoItem,
    actual_ambient_temp_c: float,
    exposure_hours: float,
) -> ColdChainExposureAudit:
    """Calculate thermal degradation using the Arrhenius Q10 kinetic equation.

    Formula:
        Multiplier = Q10 ^ ((T_actual - T_target) / 10.0)
        Days_Lost = (Exposure_Hours / 24.0) * (Multiplier - 1.0)
    """
    if exposure_hours <= 0:
        return ColdChainExposureAudit(
            sku=item.sku,
            target_temp_c=item.target_temperature_c,
            actual_temp_c=actual_ambient_temp_c,
            breach_delta_c=0.0,
            exposure_hours=0.0,
            acceleration_multiplier=1.0,
            equivalent_shelf_life_days_lost=0.0,
            remaining_effective_shelf_life_days=item.base_shelf_life_days,
            is_safe_for_consumption=True,
            verdict="NO_EXPOSURE",
        )

    delta_t = actual_ambient_temp_c - item.target_temperature_c

    # If within target or colder (without freezing chilled goods), decay is normal
    if delta_t <= 0:
        return ColdChainExposureAudit(
            sku=item.sku,
            target_temp_c=item.target_temperature_c,
            actual_temp_c=actual_ambient_temp_c,
            breach_delta_c=0.0,
            exposure_hours=exposure_hours,
            acceleration_multiplier=1.0,
            equivalent_shelf_life_days_lost=exposure_hours / 24.0,
            remaining_effective_shelf_life_days=max(0.0, item.base_shelf_life_days - (exposure_hours / 24.0)),
            is_safe_for_consumption=True,
            verdict="IN_SPECIFICATION",
        )

    # Accelerated kinetic degradation
    exponent = delta_t / 10.0
    multiplier = math.pow(item.q10_spoilage_factor, exponent)

    # Days consumed: real elapsed days * degradation multiplier
    effective_days_consumed = (exposure_hours / 24.0) * multiplier
    shelf_life_lost = effective_days_consumed - (exposure_hours / 24.0)
    remaining_sl = max(0.0, item.base_shelf_life_days - effective_days_consumed)

    # Safety check: if breached max critical threshold for >= 2 hours, fail safety
    threshold_breach = actual_ambient_temp_c > item.max_temperature_threshold_c
    is_safe = not (threshold_breach and exposure_hours >= 2.0) and (remaining_sl > 0.0)

    if not is_safe:
        verdict = "CRITICAL_SPOILAGE_DISPOSAL_REQUIRED"
    elif threshold_breach:
        verdict = "WARNING_TEMPERATURE_BREACH_ACCELERATED_DECAY"
    else:
        verdict = "ACCEPTABLE_CONTROLLED_DECAY"

    return ColdChainExposureAudit(
        sku=item.sku,
        target_temp_c=item.target_temperature_c,
        actual_temp_c=actual_ambient_temp_c,
        breach_delta_c=delta_t,
        exposure_hours=exposure_hours,
        acceleration_multiplier=round(multiplier, 3),
        equivalent_shelf_life_days_lost=round(shelf_life_lost, 2),
        remaining_effective_shelf_life_days=round(remaining_sl, 2),
        is_safe_for_consumption=is_safe,
        verdict=verdict,
    )


@dataclass(frozen=True)
class ThermalLoadingValidation:
    """Validation of cargo manifest against reefer vehicle capabilities."""
    is_compatible: bool
    assigned_thermal_classes: tuple[ThermalClass, ...]
    odor_contamination_violations: list[str] = field(default_factory=list)
    equipment_capacity_exceeded: bool = False
    verdict: str = "COMPLIANT"


def validate_thermal_loading(
    items: list[ThermalCargoItem],
    equipment: ReeferEquipment,
) -> ThermalLoadingValidation:
    """Audit whether a collection of items can be safely loaded into a reefer transport unit."""
    if not items:
        return ThermalLoadingValidation(
            is_compatible=True,
            assigned_thermal_classes=(),
            verdict="EMPTY_CARGO",
        )

    total_weight = sum(it.weight_kg for it in items)
    capacity_exceeded = total_weight > equipment.max_payload_weight_kg

    distinct_classes = tuple(sorted(set(it.thermal_class for it in items), key=lambda c: c.value))

    # Check equipment thermal class support
    unsupported = [c for c in distinct_classes if c not in equipment.supported_thermal_classes]
    if unsupported:
        return ThermalLoadingValidation(
            is_compatible=False,
            assigned_thermal_classes=distinct_classes,
            equipment_capacity_exceeded=capacity_exceeded,
            verdict=f"EQUIPMENT_CANNOT_SUPPORT_THERMAL_CLASSES_{'_'.join(c.value for c in unsupported)}",
        )

    # Check multi-temperature requirement
    if len(distinct_classes) > 1 and not equipment.multi_compartment:
        # A single compartment truck cannot maintain e.g. FROZEN (-18°C) and CHILLED (+4°C) simultaneously
        return ThermalLoadingValidation(
            is_compatible=False,
            assigned_thermal_classes=distinct_classes,
            equipment_capacity_exceeded=capacity_exceeded,
            verdict="SINGLE_COMPARTMENT_CANNOT_MIX_MULTIPLE_THERMAL_REGIMES",
        )

    # Check odor cross-contamination (e.g. seafood + dairy/chocolate)
    odor_violations = []
    has_odor_emitter = any(it.emits_strong_odors for it in items)
    has_odor_absorber = any(it.absorbs_odors for it in items)
    if has_odor_emitter and has_odor_absorber and not equipment.multi_compartment:
        emitters = [it.sku for it in items if it.emits_strong_odors]
        absorbers = [it.sku for it in items if it.absorbs_odors]
        odor_violations.append(
            f"Odor contamination risk: Emitters {emitters} cannot co-load in same compartment with Absorbers {absorbers} (FSMA Sanitary Transportation Rule)."
        )

    is_valid = (not capacity_exceeded) and (len(odor_violations) == 0)

    if not is_valid:
        verdict = "INCOMPATIBLE_CROSS_CONTAMINATION_OR_OVERWEIGHT"
    else:
        verdict = "COMPLIANT"

    return ThermalLoadingValidation(
        is_compatible=is_valid,
        assigned_thermal_classes=distinct_classes,
        odor_contamination_violations=odor_violations,
        equipment_capacity_exceeded=capacity_exceeded,
        verdict=verdict,
    )
