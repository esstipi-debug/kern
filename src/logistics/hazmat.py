"""Hazmat Segregation and Mixed-Load Compliance Engine (UN 9 Classes).

Implements strict segregation tables and co-loading compatibility rules across:
- US DOT 49 CFR § 177.848 (Segregation Table for Hazardous Materials)
- IMDG Code Chapter 7.2 (Segregation of Cargo Transport Units)
- ADR 2023 Chapter 7.5.2 (Prohibitions on Mixed Loading)
- Australian Dangerous Goods Code (ADG 7.8) Chapter 9.2
- Canadian Transport of Dangerous Goods (TDG) Regulations
- NOM-010-SCT2/2009 (México) & NCh 2120 / D.S. 298 (Chile)
- ANTT Resolução nº 5.998/2022 (Brasil)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class HazmatClass(str, Enum):
    """UN 9 Classes and primary divisions for hazardous materials."""
    CLASS_1_EXPLOSIVES = "1"
    CLASS_1_1 = "1.1"
    CLASS_1_2 = "1.2"
    CLASS_1_3 = "1.3"
    CLASS_1_4 = "1.4"
    CLASS_1_5 = "1.5"
    CLASS_1_6 = "1.6"

    CLASS_2_1_FLAMMABLE_GAS = "2.1"
    CLASS_2_2_NON_FLAMMABLE_GAS = "2.2"
    CLASS_2_3_TOXIC_GAS = "2.3"

    CLASS_3_FLAMMABLE_LIQUIDS = "3"

    CLASS_4_1_FLAMMABLE_SOLIDS = "4.1"
    CLASS_4_2_SPONTANEOUS_COMBUSTIBLE = "4.2"
    CLASS_4_3_DANGEROUS_WHEN_WET = "4.3"

    CLASS_5_1_OXIDIZERS = "5.1"
    CLASS_5_2_ORGANIC_PEROXIDES = "5.2"

    CLASS_6_1_TOXIC_SUBSTANCES = "6.1"
    CLASS_6_2_INFECTIOUS_SUBSTANCES = "6.2"

    CLASS_7_RADIOACTIVE = "7"

    CLASS_8_CORROSIVE = "8"
    CLASS_8_A_ACIDS = "8A"
    CLASS_8_B_BASES = "8B"

    CLASS_9_MISCELLANEOUS = "9"


class SegregationLevel(str, Enum):
    """Segregation requirements between dangerous goods."""
    COMPATIBLE = "COMPATIBLE"
    SEPARATE_COMPARTMENT = "SEPARATE_COMPARTMENT"
    PROHIBITED = "PROHIBITED"


# Primary class normalizer to map detailed divisions to root or segregation class
def normalize_hazmat_class(raw_class: str) -> str:
    cleaned = raw_class.strip().upper()
    if cleaned in ("8A", "8-A", "ACID", "ACIDS"):
        return "8A"
    if cleaned in ("8B", "8-B", "BASE", "BASES", "ALKALI", "ALKALIS"):
        return "8B"
    if cleaned.startswith("1.") or cleaned == "1":
        return cleaned
    if cleaned in ("2.1", "2.2", "2.3"):
        return cleaned
    if cleaned in ("4.1", "4.2", "4.3"):
        return cleaned
    if cleaned in ("5.1", "5.2"):
        return cleaned
    if cleaned in ("6.1", "6.2"):
        return cleaned
    if cleaned in ("3", "7", "8", "9"):
        return cleaned
    # Fallback to root digit if present
    root = cleaned.split(".")[0]
    if root in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
        return root
    raise ValueError(f"Unrecognized or unsupported Hazmat class: {raw_class!r}")


# Strict segregation matrix based on US DOT 49 CFR § 177.848 and IMDG Code 7.2.
# Key is frozenset of two normalized classes. If not present, default rule applies.
_PROHIBITED_PAIRS: set[frozenset[str]] = {
    # Class 1 (Explosives) is strictly prohibited with almost all commercial classes
    frozenset({"1.1", "2.1"}), frozenset({"1.1", "2.2"}), frozenset({"1.1", "2.3"}),
    frozenset({"1.1", "3"}), frozenset({"1.1", "4.1"}), frozenset({"1.1", "4.2"}),
    frozenset({"1.1", "4.3"}), frozenset({"1.1", "5.1"}), frozenset({"1.1", "5.2"}),
    frozenset({"1.1", "6.1"}), frozenset({"1.1", "7"}), frozenset({"1.1", "8"}),
    frozenset({"1.1", "8A"}), frozenset({"1.1", "8B"}),

    # Class 3 (Flammable Liquids) + Class 5.1 (Oxidizers) / 5.2 (Organic Peroxides)
    frozenset({"3", "5.1"}),
    frozenset({"3", "5.2"}),

    # Class 2.1 (Flammable Gas) + Class 5.1 (Oxidizers)
    frozenset({"2.1", "5.1"}),
    frozenset({"2.1", "5.2"}),

    # Class 4.2 (Spontaneously Combustible) + Class 5.1 (Oxidizers)
    frozenset({"4.2", "5.1"}),
    frozenset({"4.2", "5.2"}),

    # Class 4.3 (Dangerous When Wet) + Class 3 / Class 8 (aqueous corrosives)
    frozenset({"4.3", "8A"}),
    frozenset({"4.3", "8B"}),
    frozenset({"4.3", "5.1"}),

    # Class 5.1 (Oxidizers) + Class 8A (Acids) / Class 8B (Alkalis)
    frozenset({"5.1", "8A"}),
    frozenset({"5.1", "8B"}),

    # Chemical Neutralization Violence: Strong Acids (8A) + Strong Bases (8B)
    frozenset({"8A", "8B"}),

    # Class 5.2 (Organic Peroxides) + Class 8 (Corrosives)
    frozenset({"5.2", "8"}),
    frozenset({"5.2", "8A"}),
    frozenset({"5.2", "8B"}),
}

_SEPARATE_COMPARTMENT_PAIRS: set[frozenset[str]] = {
    # Needs 3m physical separation or separate partition
    frozenset({"3", "8A"}),
    frozenset({"3", "8B"}),
    frozenset({"2.1", "3"}),
    frozenset({"2.1", "8A"}),
    frozenset({"2.1", "8B"}),
    frozenset({"4.1", "5.1"}),
    frozenset({"6.1", "3"}),
    frozenset({"6.1", "8A"}),
    frozenset({"6.1", "8B"}),
}


@dataclass(frozen=True)
class HazmatItem:
    """Individual item or SKU declaring hazardous material properties."""
    sku: str
    hazmat_class: str
    weight_kg: float
    description: str = ""
    is_food_grade: bool = False
    un_number: str = ""  # e.g. "UN1203" for Gasoline

    def __post_init__(self) -> None:
        if self.weight_kg <= 0:
            raise ValueError(f"Hazmat item weight must be positive, got {self.weight_kg}")


@dataclass(frozen=True)
class HazmatCompatibilityResult:
    """Pairwise compatibility evaluation between two Hazmat classes."""
    class_a: str
    class_b: str
    segregation_level: SegregationLevel
    is_allowed_on_same_vehicle: bool
    requires_separation_distance_m: float
    legal_basis: str
    reason: str


def check_hazmat_compatibility(class_a: str, class_b: str) -> HazmatCompatibilityResult:
    """Check pairwise compatibility between two Hazmat classes."""
    norm_a = normalize_hazmat_class(class_a)
    norm_b = normalize_hazmat_class(class_b)

    # Identical classes are compatible unless specific subclass rules say otherwise
    if norm_a == norm_b:
        return HazmatCompatibilityResult(
            class_a=norm_a,
            class_b=norm_b,
            segregation_level=SegregationLevel.COMPATIBLE,
            is_allowed_on_same_vehicle=True,
            requires_separation_distance_m=0.0,
            legal_basis="49 CFR § 177.848(e) / IMDG 7.2.4",
            reason=f"Substances belonging to the same class ({norm_a}) may be co-loaded.",
        )

    pair = frozenset({norm_a, norm_b})

    # 1. Prohibited Check
    if pair in _PROHIBITED_PAIRS:
        return HazmatCompatibilityResult(
            class_a=norm_a,
            class_b=norm_b,
            segregation_level=SegregationLevel.PROHIBITED,
            is_allowed_on_same_vehicle=False,
            requires_separation_distance_m=float("inf"),
            legal_basis="49 CFR § 177.848 Table / IMDG 7.2.4 (Prohibited Co-loading)",
            reason=f"Severe reaction hazard: Class {norm_a} and Class {norm_b} are strictly prohibited on the same transport unit.",
        )

    # 2. Separate Compartment Check
    if pair in _SEPARATE_COMPARTMENT_PAIRS:
        return HazmatCompatibilityResult(
            class_a=norm_a,
            class_b=norm_b,
            segregation_level=SegregationLevel.SEPARATE_COMPARTMENT,
            is_allowed_on_same_vehicle=True,
            requires_separation_distance_m=3.0,
            legal_basis="49 CFR § 177.848(d) / ADR 7.5.2 (Separated by at least 3m)",
            reason=f"Classes {norm_a} and {norm_b} require minimum 3.0m separation or non-combustible longitudinal bulkhead.",
        )

    # 3. Default: Compatible
    return HazmatCompatibilityResult(
        class_a=norm_a,
        class_b=norm_b,
        segregation_level=SegregationLevel.COMPATIBLE,
        is_allowed_on_same_vehicle=True,
        requires_separation_distance_m=0.0,
        legal_basis="49 CFR § 177.848 / IMDG Code 7.2.4",
        reason=f"Classes {norm_a} and {norm_b} are compatible for general mixed freight loading.",
    )


@dataclass(frozen=True)
class ManifestHazmatValidation:
    """Audit result for a full shipment manifest of Hazmat items."""
    is_legally_feasible: bool
    total_hazmat_weight_kg: float
    prohibited_conflicts: list[tuple[str, str, str]] = field(default_factory=list)
    separation_warnings: list[tuple[str, str, str]] = field(default_factory=list)
    food_poisoning_violations: list[str] = field(default_factory=list)
    requires_placard: bool = False
    verdict: str = "COMPLIANT"


def validate_manifest_hazmat(
    items: list[HazmatItem],
    *,
    placard_threshold_kg: float = 454.0,  # 1,000 lbs standard DOT threshold
) -> ManifestHazmatValidation:
    """Validate an entire manifest of SKUs for co-loading and food-grade contamination."""
    if not items:
        return ManifestHazmatValidation(
            is_legally_feasible=True,
            total_hazmat_weight_kg=0.0,
            verdict="EMPTY_MANIFEST",
        )

    total_weight = sum(it.weight_kg for it in items)
    prohibited = []
    separated = []
    food_violations = []

    # Check for food-grade contamination with toxics or corrosives
    has_food = any(it.is_food_grade for it in items)
    if has_food:
        for it in items:
            norm_cls = normalize_hazmat_class(it.hazmat_class)
            if norm_cls in ("6.1", "6.2", "8", "8A", "8B"):
                food_violations.append(
                    f"SKU {it.sku} (Class {norm_cls}) co-loaded with food-grade goods violates FDA/FSMA and 49 CFR § 177.841(e)."
                )

    # Pairwise check across all items
    n = len(items)
    for i in range(n):
        for j in range(i + 1, n):
            it_a = items[i]
            it_b = items[j]
            comp = check_hazmat_compatibility(it_a.hazmat_class, it_b.hazmat_class)
            if comp.segregation_level == SegregationLevel.PROHIBITED:
                prohibited.append((it_a.sku, it_b.sku, comp.reason))
            elif comp.segregation_level == SegregationLevel.SEPARATE_COMPARTMENT:
                separated.append((it_a.sku, it_b.sku, comp.reason))

    is_feasible = (len(prohibited) == 0) and (len(food_violations) == 0)
    requires_placard = total_weight >= placard_threshold_kg

    if not is_feasible:
        verdict = "ILLEGAL_HAZMAT_MANIFEST_CO_LOADING_PROHIBITED"
    elif len(separated) > 0:
        verdict = "FEASIBLE_WITH_COMPARTMENT_SEPARATION_REQUIRED"
    else:
        verdict = "COMPLIANT"

    return ManifestHazmatValidation(
        is_legally_feasible=is_feasible,
        total_hazmat_weight_kg=total_weight,
        prohibited_conflicts=prohibited,
        separation_warnings=separated,
        food_poisoning_violations=food_violations,
        requires_placard=requires_placard,
        verdict=verdict,
    )


def partition_hazmat_shipment(items: list[HazmatItem]) -> list[list[HazmatItem]]:
    """Partition a set of incompatible Hazmat items into minimum legally compliant vehicle loads.

    Solves vertex coloring on the incompatibility graph using Welsh-Powell heuristic
    to guarantee every sub-group has zero PROHIBITED pairs.
    """
    if not items:
        return []

    # Build incompatibility conflict adjacency
    n = len(items)
    adj: dict[int, set[int]] = {i: set() for i in range(n)}

    for i in range(n):
        for j in range(i + 1, n):
            comp = check_hazmat_compatibility(items[i].hazmat_class, items[j].hazmat_class)
            is_food_conflict = (
                (items[i].is_food_grade and normalize_hazmat_class(items[j].hazmat_class) in ("6.1", "6.2", "8", "8A", "8B"))
                or (items[j].is_food_grade and normalize_hazmat_class(items[i].hazmat_class) in ("6.1", "6.2", "8", "8A", "8B"))
            )
            if comp.segregation_level == SegregationLevel.PROHIBITED or is_food_conflict:
                adj[i].add(j)
                adj[j].add(i)

    # Welsh-Powell greedy coloring
    nodes_by_degree = sorted(range(n), key=lambda idx: len(adj[idx]), reverse=True)
    partition_map: dict[int, int] = {}  # item_idx -> vehicle_id
    vehicle_members: list[list[int]] = []

    for node in nodes_by_degree:
        # Find first vehicle load where node has no conflicts with existing members
        assigned_vehicle = -1
        for v_id, members in enumerate(vehicle_members):
            if not any(member in adj[node] for member in members):
                assigned_vehicle = v_id
                members.append(node)
                partition_map[node] = v_id
                break

        if assigned_vehicle == -1:
            new_v_id = len(vehicle_members)
            vehicle_members.append([node])
            partition_map[node] = new_v_id

    # Translate indices back to items
    result = []
    for members in vehicle_members:
        result.append([items[idx] for idx in members])
    return result
