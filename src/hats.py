"""Five-hat decision substrate over one replenishment decision (Q, SL).

Spec: docs/superpowers/specs/2026-07-20-hats-n4-n5-design.md (D1-D8).
Role "hats" (comprador / planner / cfo / comercial / transporte) score the SAME
candidate (order_quantity, service_level) with their own pure utility; a
neutral judge cost values any candidate in $ without deciding. `Hat` lives
BESIDE scm_agent.modes.Mode (D7): src/ never imports scm_agent; `mode_key`
is a soft string reference, never resolved here.

THE FIFTH HAT IS CONDITIONAL, AND THAT IS THE POINT. `transporte` scores the
freight a policy forces -- and freight needs two physical facts no demand
series carries: the weight of a unit and the length of the lane. There is no
D8-style synthetic default for those (the price-break default at least derives
from EOQ and the real unit cost; a kilogram derives from nothing). So a SKU
without a declared `FreightProfile` has FOUR areas on the table, not five with
one guessed, and `evaluable_hats` is the single place that says which. Every
surface that renders a council must repeat the exclusion: a quietly four-hat
settlement reads as "transporte did not object", which is the exact shape
`src.objection.NO_EVALUABLE` exists to prevent.

Weights are an explicit OPERATOR POLICY (D4), not an objective consensus:
`parse_weights` validates and renormalizes them, and every surface that
prints a settlement repeats that framing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.constraints import cheapest_feasible_lot, feasible_lots, feasible_neighbours, lot_rule
from src.eoq import PriceBreak, compute_eoq, compute_eoq_volume_discount
from src.fill_rate import fill_rate_from_safety_stock, normal_loss_standard
from src.financial_kpis import days_inventory_outstanding
from src.logistics.modes import FreightRates, ModeSelection, Shipment, select_mode
from src.safety_stock import safety_stock, service_level_factor

HAT_COMPRADOR = "comprador"
HAT_PLANNER = "planner"
HAT_CFO = "cfo"
HAT_COMERCIAL = "comercial"
HAT_TRANSPORTE = "transporte"
#: Appended, never inserted: `src.cross_impact.CrossImpact.rows` and
#: `src.hat_council.tension_map`'s pair order are both defined as "HAT_KEYS
#: order", so putting `transporte` anywhere but last would silently reorder
#: every existing deliverable.
HAT_KEYS: tuple[str, ...] = (
    HAT_COMPRADOR, HAT_PLANNER, HAT_CFO, HAT_COMERCIAL, HAT_TRANSPORTE,
)

# D1: the settlement domain is a 2D grid, anchored on closed-form quantities.
SL_GRID: tuple[float, ...] = (0.90, 0.925, 0.95, 0.975, 0.99)
N_Q_POINTS = 25
Q_SPAN_LO = 0.5     # x min(Q_eoq, Q_disc)
Q_SPAN_HI = 1.25    # x max(Q_eoq, Q_disc)
#: Under supplier terms (MOQ / case-pack multiple) the Q axis is laid over lots
#: the supplier ACCEPTS instead of 25 linear points -- see `candidate_grid`. A
#: coarse multiple can leave the span with two or three legal lots, so the grid
#: reaches upward until it holds at least this many...
MIN_FEASIBLE_Q_POINTS = 5
#: ...and a fine multiple (sell by 1) can put thousands in it, so it is thinned
#: evenly to at most this many, ends kept.
MAX_FEASIBLE_Q_POINTS = 4 * N_Q_POINTS

# D8: synthetic default price breaks for the testbed (labeled "(assumed)").
ASSUMED_BREAK_TIERS: tuple[tuple[float, float], ...] = ((2.0, 0.98), (4.0, 0.96))

#: Uniform over the WHOLE vocabulary. Derived from the length, not typed: the
#: literal 0.25 this used to carry stopped summing to 1 the moment a fifth hat
#: existed, and nothing downstream re-normalized it. `parse_weights(None, hats)`
#: computes the same uniform over the areas actually on the table, which is what
#: every caller with an `inputs` in hand should use instead of this constant.
DEFAULT_WEIGHTS: dict[str, float] = {k: 1.0 / len(HAT_KEYS) for k in HAT_KEYS}


@dataclass(frozen=True)
class Hat:
    """A role profile over the replenishment decision. Descriptive only: the
    utility is a pure function elsewhere in this module, never a field, and
    `tool_keys`/`mode_key` are soft references (strings), never resolved."""

    key: str
    label: str
    objetivo: str
    kpis: tuple[str, ...]
    tool_keys: frozenset[str]
    mode_key: str | None


HATS: dict[str, Hat] = {
    HAT_COMPRADOR: Hat(
        key=HAT_COMPRADOR, label="Comprador",
        objetivo="minimizar el costo unitario efectivo de compra (capturar descuentos por volumen)",
        kpis=("costo unitario efectivo", "ordenes por anio"),
        tool_keys=frozenset({"inventory_optimization", "sourcing"}), mode_key="scm"),
    HAT_PLANNER: Hat(
        key=HAT_PLANNER, label="Planner",
        objetivo="minimizar costo de ordenar + mantener cumpliendo el nivel de servicio objetivo",
        kpis=("costo de politica", "nivel de servicio", "safety stock"),
        tool_keys=frozenset({"inventory_optimization", "forecast"}), mode_key="inventory"),
    HAT_CFO: Hat(
        key=HAT_CFO, label="CFO",
        objetivo="minimizar el cargo de capital sobre el inventario promedio (WACC)",
        kpis=("cargo de capital", "inventario promedio", "DIO"),
        tool_keys=frozenset({"financial_kpis", "cost_to_serve"}), mode_key="scm"),
    HAT_COMERCIAL: Hat(
        key=HAT_COMERCIAL, label="Comercial",
        objetivo="maximizar el fill rate (disponibilidad para vender)",
        kpis=("fill rate", "unidades cortas esperadas por anio"),
        tool_keys=frozenset({"inventory_optimization", "pricing"}), mode_key="scm"),
    HAT_TRANSPORTE: Hat(
        key=HAT_TRANSPORTE, label="Transporte",
        objetivo="minimizar el flete por unidad movida (llenar el camion sin derramarlo)",
        kpis=("flete por unidad", "flete anual", "envios por anio"),
        tool_keys=frozenset({"transportation", "cost_to_serve"}), mode_key="scm"),
}


@dataclass(frozen=True)
class HatConfig:
    """Effective parameters (the spec's "params efectivos"), all injectable.

    D5 - no double counting of capital: `holding_rate` is the repo's h_total
    (0.25/yr, INCLUDING capital). It decomposes into `wacc` (capital slice,
    what the CFO hat charges) and `h_oop = holding_rate - wacc` (warehouse,
    insurance, shrink). The judge uses h_total; the CFO hat uses ONLY wacc.
    """

    order_cost: float = 75.0
    holding_rate: float = 0.25
    wacc: float = 0.12
    sl_target: float = 0.95
    gross_margin_rate: float = 0.30
    periods_per_year: float = 52.0

    def __post_init__(self) -> None:
        if self.order_cost <= 0:
            raise ValueError("order_cost must be > 0")
        if self.holding_rate <= 0:
            raise ValueError("holding_rate must be > 0")
        if not 0 < self.wacc < self.holding_rate:
            raise ValueError(
                f"wacc must satisfy 0 < wacc < holding_rate (got wacc={self.wacc}, "
                f"holding_rate={self.holding_rate}) - capital is a SLICE of h_total (D5)")
        if not 0 < self.sl_target < 1:
            raise ValueError("sl_target must be in (0, 1)")
        if not 0 <= self.gross_margin_rate < 1:
            raise ValueError("gross_margin_rate must be in [0, 1)")
        if self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be > 0")

    @property
    def h_oop(self) -> float:
        """Out-of-pocket holding slice (h_total - WACC)."""
        return self.holding_rate - self.wacc


@dataclass(frozen=True)
class FreightProfile:
    """The two physical facts that make `transporte` evaluable, plus the card
    that prices them. Declared per SKU/lane by the operator -- never derived.

    `rates=None` means the illustrative card `src.logistics.modes.FreightRates()`
    ships with; `rates_assumed` is then True and every surface must label the
    freight "(tarifa supuesta)", the same contract `price_breaks_assumed` carries
    for D8. The weight and the distance have no such fallback: they are required.
    """

    weight_kg_per_unit: float
    distance_km: float
    rates: FreightRates | None = None
    #: Passed through to `select_mode`; a lane with a delivery window cannot be
    #: quoted on a mode that misses it.
    max_transit_days: float | None = None

    def __post_init__(self) -> None:
        if self.weight_kg_per_unit <= 0:
            raise ValueError("weight_kg_per_unit must be > 0")
        if self.distance_km <= 0:
            raise ValueError("distance_km must be > 0")
        if self.max_transit_days is not None and self.max_transit_days <= 0:
            raise ValueError("max_transit_days must be > 0 when given")

    @property
    def rates_assumed(self) -> bool:
        """True when nobody declared a tariff and the illustrative card is in use."""
        return self.rates is None

    @property
    def effective_rates(self) -> FreightRates:
        return self.rates if self.rates is not None else FreightRates()


@dataclass(frozen=True)
class HatInputs:
    """One SKU's decision inputs. `price_breaks_assumed` marks the D8
    synthetic default so every output can carry the "(assumed)" label.
    `freight` is None on a SKU nobody declared transport facts for -- see
    `evaluable_hats`, which is what turns that None into four areas."""

    sku: str
    annual_demand: float
    mean_weekly: float
    std_weekly: float
    lead_time_weeks: float
    unit_cost: float
    price_breaks: tuple[PriceBreak, ...]
    price_breaks_assumed: bool
    config: HatConfig
    freight: FreightProfile | None = None
    #: Supplier terms, in units; 0.0 = none declared. When either binds, the
    #: grid holds only lots the supplier accepts and the baseline snaps to the
    #: cheapest feasible lot (src.constraints.cheapest_feasible_lot) -- exactly
    #: what jobs/inventory_optimization.py ships, so the equivalence holds.
    minimum_order_quantity: float = 0.0
    order_multiple: float = 0.0

    def __post_init__(self) -> None:
        if self.annual_demand <= 0:
            raise ValueError(f"{self.sku}: annual_demand must be > 0")
        if self.minimum_order_quantity < 0 or self.order_multiple < 0:
            raise ValueError(f"{self.sku}: supplier terms must be >= 0")
        if self.mean_weekly <= 0:
            raise ValueError(f"{self.sku}: mean_weekly must be > 0")
        if self.std_weekly < 0:
            raise ValueError(f"{self.sku}: std_weekly must be >= 0")
        if self.lead_time_weeks <= 0:
            raise ValueError(f"{self.sku}: lead_time_weeks must be > 0")
        if self.unit_cost <= 0:
            raise ValueError(f"{self.sku}: unit_cost must be > 0")

    @property
    def lot_rule(self) -> str:
        """"" when no supplier term binds, else e.g. "moq 60, multiple 30"."""
        return lot_rule(self.minimum_order_quantity, self.order_multiple)

    @property
    def lot_terms(self) -> dict[str, float]:
        """The two terms as the keyword arguments src.constraints takes."""
        return {"minimum_order_quantity": self.minimum_order_quantity,
                "order_multiple": self.order_multiple}


@dataclass(frozen=True)
class Candidate:
    """One point of the decision grid."""

    order_quantity: float
    service_level: float

    def __post_init__(self) -> None:
        if self.order_quantity <= 0:
            raise ValueError("order_quantity must be > 0")
        if not 0 < self.service_level < 1:
            raise ValueError("service_level must be in (0, 1)")


@dataclass(frozen=True)
class HatEvaluation:
    """One hat's score of one candidate."""

    hat_key: str
    candidate: Candidate
    utility_raw: float
    utility_norm: float
    kpis: dict = field(default_factory=dict)


def evaluable_hats(inputs: HatInputs) -> tuple[str, ...]:
    """The areas that can be scored for THIS SKU, in HAT_KEYS order.

    The single source of truth for "who is on the table". `transporte` drops out
    when no `FreightProfile` was declared, because the alternative is inventing a
    unit weight -- and a fabricated physical fact is worse than a declared hole
    (the same call `src.flow_map` makes when it reports an edge UNMEASURABLE
    rather than assuming a transit time).

    Callers must not just iterate this and move on: whatever they render has to
    NAME the excluded area. Four hats agreeing quietly is indistinguishable from
    five hats agreeing, and only one of those is true.
    """
    if inputs.freight is None:
        return tuple(k for k in HAT_KEYS if k != HAT_TRANSPORTE)
    return HAT_KEYS


def parse_weights(
    raw: str | dict | None, hats: tuple[str, ...] = HAT_KEYS,
) -> dict[str, float]:
    """Parse and validate the settlement weights (D4: an explicit operator
    POLICY, not an objective consensus). Missing hats weigh 0. Renormalized
    to sum 1. Raises ValueError on unknown keys, negatives, or sum <= 0.

    `hats` is the set actually on the table -- pass `evaluable_hats(inputs)`.
    Weight declared for an area that is NOT on the table is dropped and the rest
    renormalized, because the alternative -- raising -- makes a mixed portfolio
    unusable: one policy is written per run, while evaluability is per SKU, so a
    five-area policy over a file where only some SKUs carry a weight would fail
    the whole run. The drop is never silent: `Settlement.excluded_hats` carries
    the areas, and the acta prints which weights were renormalized over what.

    The degenerate case still raises, through the existing sum-0 check: a policy
    whose weight lands ENTIRELY on off-table areas leaves nothing to settle on,
    and quietly falling back to uniform would invent a policy nobody wrote.
    """
    if raw is None:
        return {k: 1.0 / len(hats) for k in hats}
    if isinstance(raw, str):
        pairs: dict[str, float] = {}
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            key, sep, value = chunk.partition("=")
            if not sep:
                raise ValueError(f"weights: expected 'hat=value', got {chunk!r}")
            try:
                pairs[key.strip().lower()] = float(value)
            except ValueError as exc:
                raise ValueError(f"weights: {value!r} is not a number for {key.strip()!r}") from exc
        raw = pairs
    unknown = set(raw) - set(HAT_KEYS)
    if unknown:
        raise ValueError(f"weights: unknown hat key(s) {sorted(unknown)}; valid: {list(HAT_KEYS)}")
    declared = {k: float(raw.get(k, 0.0)) for k in HAT_KEYS}
    if any(w < 0 for w in declared.values()):
        raise ValueError("weights: negative weights are not allowed")
    weights = {k: declared[k] for k in hats}
    total = sum(weights.values())
    if total <= 0:
        dropped = sorted(k for k in HAT_KEYS if k not in hats and declared[k] > 0)
        if dropped:
            raise ValueError(
                f"weights: the whole policy sits on {dropped}, which is not evaluable "
                f"here; the areas on the table are {list(hats)}. Declare the missing "
                "inputs (transporte needs a unit weight and a lane distance) or weigh "
                "an area that can be scored")
        raise ValueError("weights: sum must be > 0")
    return {k: w / total for k, w in weights.items()}


# -- D8: price breaks ----------------------------------------------------------


def default_price_breaks(
    annual_demand: float, unit_cost: float, config: HatConfig,
) -> tuple[PriceBreak, ...]:
    """Deterministic synthetic tariff for the testbed (D8): -2% at 2x EOQ and
    -4% at 4x EOQ over the base unit cost. Every output that uses this default
    must label it "(assumed)" -- on a real client the breaks come from the
    supplier tariff via params["price_breaks"]."""
    q_eoq = compute_eoq(annual_demand, config.holding_rate * unit_cost, config.order_cost).order_quantity
    return (
        PriceBreak(min_quantity=0.0, unit_cost=unit_cost),
        PriceBreak(min_quantity=2.0 * q_eoq, unit_cost=round(unit_cost * (1 - 0.02), 10)),
        PriceBreak(min_quantity=4.0 * q_eoq, unit_cost=round(unit_cost * (1 - 0.04), 10)),
    )


def build_inputs(
    *,
    sku: str,
    annual_demand: float,
    mean_weekly: float,
    std_weekly: float,
    lead_time_weeks: float,
    unit_cost: float,
    config: HatConfig,
    price_breaks: tuple[PriceBreak, ...] | None = None,
    freight: FreightProfile | None = None,
    minimum_order_quantity: float = 0.0,
    order_multiple: float = 0.0,
) -> HatInputs:
    """Assemble one SKU's inputs; `price_breaks=None` -> D8 synthetic default.
    `freight=None` leaves `transporte` off the table (no synthetic default).
    `minimum_order_quantity` / `order_multiple` are the supplier's terms; 0 = none."""
    assumed = price_breaks is None
    breaks = (
        default_price_breaks(annual_demand, unit_cost, config)
        if assumed else tuple(price_breaks)
    )
    return HatInputs(
        sku=sku, annual_demand=annual_demand, mean_weekly=mean_weekly,
        std_weekly=std_weekly, lead_time_weeks=lead_time_weeks, unit_cost=unit_cost,
        price_breaks=breaks, price_breaks_assumed=assumed, config=config,
        freight=freight,
        minimum_order_quantity=minimum_order_quantity, order_multiple=order_multiple,
    )


def unit_cost_at(inputs: HatInputs, order_quantity: float) -> float:
    """Piecewise c(Q): the unit cost of the highest tier whose min_quantity <= Q;
    base unit_cost when no tier covers Q (injected tariffs need no base tier)."""
    best = inputs.unit_cost
    for tier in sorted(inputs.price_breaks, key=lambda b: b.min_quantity):
        if order_quantity >= tier.min_quantity:
            best = tier.unit_cost
    return best


# -- Risk-period helpers ---------------------------------------------------------


def sigma_over_lead(inputs: HatInputs) -> float:
    """sigma_L = sigma_w * sqrt(L_w)."""
    return inputs.std_weekly * (inputs.lead_time_weeks ** 0.5)


def ss_units(inputs: HatInputs, service_level: float) -> float:
    """SS(SL) = z(SL) * sigma_w * sqrt(L_w) -- the engine's own safety_stock."""
    return safety_stock(
        inputs.std_weekly, service_level, risk_periods=inputs.lead_time_weeks,
    ).safety_stock


# -- D1: the deterministic 2D grid ----------------------------------------------


def anchor_quantities(inputs: HatInputs) -> tuple[float, float]:
    """(q_eoq, q_disc): the two closed-form anchors, computed BEFORE the grid.

    q_eoq  -- classic EOQ at the base unit cost.
    q_disc -- best Q across the price-break tariff (all-units discounts).
    """
    cfg = inputs.config
    q_eoq = compute_eoq(
        inputs.annual_demand, cfg.holding_rate * inputs.unit_cost, cfg.order_cost,
    ).order_quantity
    q_disc = compute_eoq_volume_discount(
        inputs.annual_demand, cfg.holding_rate, cfg.order_cost, list(inputs.price_breaks),
    ).order_quantity
    return q_eoq, q_disc


def candidate_grid(inputs: HatInputs) -> tuple[Candidate, ...]:
    """SL_GRID x 25 linear Q points on [0.5*min(anchors), 1.25*max(anchors)],
    plus the mandatory candidates q_eoq, q_disc and the baseline Q (== q_eoq,
    see baseline_plan) deduped in. Fixed order: SL asc outer, Q asc inner.

    Under supplier terms the Q axis is `_feasible_q_points` instead: every
    candidate is a lot the supplier accepts, so no hat's ideal and no settlement
    can land on a quantity that cannot be ordered -- a council that agrees on 40
    when the supplier sells by 30 has agreed on nothing.
    """
    q_eoq, q_disc = anchor_quantities(inputs)
    lo = Q_SPAN_LO * min(q_eoq, q_disc)
    hi = Q_SPAN_HI * max(q_eoq, q_disc)
    if inputs.lot_rule:
        qs = _feasible_q_points(inputs, lo, hi, (q_eoq, q_disc))
    else:
        qs = [lo + i * (hi - lo) / (N_Q_POINTS - 1) for i in range(N_Q_POINTS)]
        for mandatory in (q_eoq, q_disc):        # baseline Q == q_eoq (dedupe handles it)
            if not any(abs(q - mandatory) < 1e-12 for q in qs):
                qs.append(mandatory)
        qs.sort()
    return tuple(
        Candidate(order_quantity=q, service_level=sl) for sl in SL_GRID for q in qs
    )


def _feasible_q_points(
    inputs: HatInputs, lo: float, hi: float, anchors: tuple[float, ...],
) -> list[float]:
    """The Q axis over lots the supplier accepts, ascending and deduped.

    Nothing below the MOQ is a candidate, so the span starts there when the MOQ
    swallows its lower end, and keeps the unconstrained span's shape (hi/lo =
    Q_SPAN_HI/Q_SPAN_LO) above it. With a case-pack multiple the axis is every
    legal lot in the span (`feasible_lots`), widened upward to at least
    MIN_FEASIBLE_Q_POINTS lots and thinned to at most MAX_FEASIBLE_Q_POINTS;
    with only an MOQ the legal set is continuous above it and the axis stays
    the usual 25 linear points. Each anchor joins as its feasible neighbours --
    the lot above it and, when one exists, the lot below -- which is where
    `baseline_plan` lands, so the grid always contains the baseline.
    """
    terms = inputs.lot_terms
    lo = max(lo, inputs.minimum_order_quantity)
    hi = max(hi, lo * Q_SPAN_HI / Q_SPAN_LO)
    if inputs.order_multiple > 0:
        qs = feasible_lots(lo, hi, **terms)
        while len(qs) < MIN_FEASIBLE_Q_POINTS:
            hi += inputs.order_multiple * (MIN_FEASIBLE_Q_POINTS - len(qs))
            qs = feasible_lots(lo, hi, **terms)
        if len(qs) > MAX_FEASIBLE_Q_POINTS:
            n = len(qs)
            qs = [qs[round(i * (n - 1) / (MAX_FEASIBLE_Q_POINTS - 1))]
                  for i in range(MAX_FEASIBLE_Q_POINTS)]
    else:
        qs = [lo + i * (hi - lo) / (N_Q_POINTS - 1) for i in range(N_Q_POINTS)]
    for anchor in anchors:
        for q in feasible_neighbours(anchor, **terms):
            if q is not None and not any(abs(x - q) <= 1e-9 * max(1.0, q) for x in qs):
                qs.append(q)
    qs.sort()
    return qs


# -- D3: shortage economics ------------------------------------------------------


def shortage_cost_per_unit(inputs: HatInputs) -> float:
    """p_short = precio - c_base with precio = c_base / (1 - gross_margin_rate):
    a stockout is a lost sale at unit margin (D3). Selling price derives from
    the BASE unit cost -- purchase discounts do not change the shelf price."""
    m = inputs.config.gross_margin_rate
    return inputs.unit_cost * m / (1.0 - m)


def _expected_units_short_per_year(inputs: HatInputs, cand: Candidate) -> float:
    """(D/Q) * sigma_L * L_N(z(SL)); zero when demand has no variability."""
    sigma_l = sigma_over_lead(inputs)
    if sigma_l <= 0:
        return 0.0
    z = service_level_factor(cand.service_level)
    cycles = inputs.annual_demand / cand.order_quantity
    return cycles * sigma_l * float(normal_loss_standard(z))


# -- Freight economics (the transporte hat) ----------------------------------------


def freight_quote(inputs: HatInputs, order_quantity: float) -> ModeSelection:
    """Cheapest feasible mode for ONE shipment of `order_quantity` units.

    Q is the shipment: a replenishment policy that orders Q at a time moves Q at
    a time. That is what makes freight a function of the SAME decision variable
    the other four hats argue over, and it is why the curve is a SAWTOOTH rather
    than a slope -- `src.logistics.modes` charges FTL by `ceil(weight/capacity)`,
    so the unit that spills into a second half-empty truck costs what a whole
    truck costs. A Q the comprador loves for hitting a price break can land on
    the wrong side of that step.
    """
    f = inputs.freight
    if f is None:
        raise ValueError(
            f"{inputs.sku}: transporte is not evaluable -- no FreightProfile declared. "
            "It needs weight_kg_per_unit and distance_km, and there is no default for "
            "either: a kilogram cannot be derived from a demand series. Use "
            "evaluable_hats(inputs) to find out before asking for this KPI.")
    return select_mode(
        Shipment(
            shipment_id=inputs.sku,
            weight_kg=order_quantity * f.weight_kg_per_unit,
            distance_km=f.distance_km,
            units=order_quantity,
        ),
        f.effective_rates,
        max_transit_days=f.max_transit_days,
    )


def freight_per_unit(inputs: HatInputs, cand: Candidate) -> float:
    """cost_of_one_shipment(Q) / Q -- the operating ratio src.logistics.freight
    ranks lanes by, here as a function of the policy instead of of history."""
    return freight_quote(inputs, cand.order_quantity).recommended_cost / cand.order_quantity


def annual_freight(inputs: HatInputs, cand: Candidate) -> float:
    """(D/Q) * cost_of_one_shipment(Q).

    Exactly `annual_demand * freight_per_unit`, so the two are ONE signal in two
    units, not two pieces of evidence -- D is fixed across the grid. Flat in SL:
    the same annual volume moves either way and this module models no expedite,
    which is a declared limit of the utility, not an accident of it.
    """
    q = cand.order_quantity
    return (inputs.annual_demand / q) * freight_quote(inputs, q).recommended_cost


def freight_mode(inputs: HatInputs, cand: Candidate) -> str:
    """The mode behind the number. Kept OUT of `hat_kpis` on purpose: that dict
    is `dict[str, float]` and every consumer (acta rows, cross-impact deltas)
    does arithmetic on its values."""
    return freight_quote(inputs, cand.order_quantity).recommended_mode


# -- The neutral judge (spec sec 5) -- values, never decides ------------------------


def decision_cost(inputs: HatInputs, cand: Candidate) -> float:
    """C(Q, SL) = D*c(Q) + K*D/Q + h_total*c(Q)*(Q/2 + SS(SL)) + p_short*E[short]
    + annual freight, the last term ONLY when a FreightProfile was declared.

    Honesty note (spec sec 5): minimizing C directly would be one more policy with
    implicit weights baked into its coefficients. The hat model exists to make
    that trade-off explicit and auditable -- the judge only VALUES candidates so
    tension and settlements can be compared in $; it never picks the plan.

    Why freight is conditional rather than always-on: with no profile there is no
    freight number to add, and adding zero is not neutral -- it would tell the
    judge this policy ships for free. Omitting the term instead keeps C exactly
    what it has always been on a SKU with no transport facts, which is also why
    the baseline equivalence against jobs/inventory_optimization.py (pinned by
    tests/test_hats_valuation.py) still holds unchanged.
    """
    cfg = inputs.config
    c_q = unit_cost_at(inputs, cand.order_quantity)
    purchase = inputs.annual_demand * c_q
    ordering = cfg.order_cost * inputs.annual_demand / cand.order_quantity
    holding = cfg.holding_rate * c_q * (cand.order_quantity / 2.0 + ss_units(inputs, cand.service_level))
    shortage = shortage_cost_per_unit(inputs) * _expected_units_short_per_year(inputs, cand)
    freight = annual_freight(inputs, cand) if inputs.freight is not None else 0.0
    return purchase + ordering + holding + shortage + freight


def baseline_plan(inputs: HatInputs) -> Candidate:
    """What Kern ships today (jobs/inventory_optimization.py): (s,Q) via classic
    EOQ at holding_rate*unit_cost with cycle service level 0.95 (= sl_target)
    and a CONSTANT unit cost (no discounts). Pure mirror -- the equivalence
    against the real job is pinned by tests/test_hats_valuation.py."""
    cfg = inputs.config
    q = compute_eoq(
        inputs.annual_demand, cfg.holding_rate * inputs.unit_cost, cfg.order_cost,
    ).order_quantity
    if inputs.lot_rule:
        # Same call, same cost curve, same tie-break as the inventory job: the
        # cheapest lot the supplier accepts around the EOQ.
        q = cheapest_feasible_lot(
            q, annual_demand=inputs.annual_demand,
            holding_cost_per_unit=cfg.holding_rate * inputs.unit_cost,
            fixed_order_cost=cfg.order_cost, **inputs.lot_terms,
        ).chosen.quantity
    return Candidate(order_quantity=q, service_level=cfg.sl_target)


# -- The utilities (spec sec 4) -- higher is better ---------------------------------


def _u_comprador(inputs: HatInputs, cand: Candidate) -> float:
    """-(c(Q) + K/Q): effective unit purchase cost. Flat in SL."""
    return -(unit_cost_at(inputs, cand.order_quantity)
             + inputs.config.order_cost / cand.order_quantity)


def _u_planner_valid(inputs: HatInputs, cand: Candidate) -> float:
    """-(K*D/Q + h_total*c(Q)*(Q/2 + SS)) -- only meaningful when SL >= sl_target."""
    cfg = inputs.config
    c_q = unit_cost_at(inputs, cand.order_quantity)
    return -(cfg.order_cost * inputs.annual_demand / cand.order_quantity
             + cfg.holding_rate * c_q * (cand.order_quantity / 2.0
                                         + ss_units(inputs, cand.service_level)))


def _u_cfo(inputs: HatInputs, cand: Candidate) -> float:
    """-(WACC * c(Q) * (Q/2 + SS)): the capital slice only (D5, no double count)."""
    c_q = unit_cost_at(inputs, cand.order_quantity)
    return -(inputs.config.wacc * c_q * (cand.order_quantity / 2.0
                                         + ss_units(inputs, cand.service_level)))


def _u_comercial(inputs: HatInputs, cand: Candidate) -> float:
    """Fill rate beta at (Q, SL); 1.0 when demand has no variability."""
    sigma_l = sigma_over_lead(inputs)
    if sigma_l <= 0:
        return 1.0
    return fill_rate_from_safety_stock(
        ss_units(inputs, cand.service_level), cand.order_quantity, sigma_l)


def _u_transporte(inputs: HatInputs, cand: Candidate) -> float:
    """-(annual freight at Q). Flat in SL, sawtooth in Q (see `freight_quote`)."""
    return -annual_freight(inputs, cand)


def utilities_raw(
    inputs: HatInputs, candidates: tuple[Candidate, ...],
) -> dict[str, tuple[float, ...]]:
    """Raw utilities per hat, aligned with `candidates`. The returned dict has a
    `transporte` key only when one is evaluable -- its keys are `evaluable_hats`.

    Planner (spec sec 4): candidates below sl_target get a FINITE penalty strictly
    below every valid candidate, ordered by deficit:
        u = u_min_valid - (sl_target - SL) * range_valid,
        range_valid = max(u_max_valid - u_min_valid, 1.0).
    If NO candidate meets sl_target (injectable sl_target above the grid), the
    empty-valid-set convention u_min_valid=0.0, range_valid=1.0 keeps the
    deficit ordering and stays deterministic.
    """
    sl_target = inputs.config.sl_target
    planner_valid = [
        _u_planner_valid(inputs, c) for c in candidates if c.service_level >= sl_target
    ]
    if planner_valid:
        u_min_valid = min(planner_valid)
        range_valid = max(max(planner_valid) - u_min_valid, 1.0)
    else:
        u_min_valid, range_valid = 0.0, 1.0

    def planner(cand: Candidate) -> float:
        if cand.service_level >= sl_target:
            return _u_planner_valid(inputs, cand)
        return u_min_valid - (sl_target - cand.service_level) * range_valid

    out = {
        HAT_COMPRADOR: tuple(_u_comprador(inputs, c) for c in candidates),
        HAT_PLANNER: tuple(planner(c) for c in candidates),
        HAT_CFO: tuple(_u_cfo(inputs, c) for c in candidates),
        HAT_COMERCIAL: tuple(_u_comercial(inputs, c) for c in candidates),
    }
    if inputs.freight is not None:
        out[HAT_TRANSPORTE] = tuple(_u_transporte(inputs, c) for c in candidates)
    return out


# -- D2: min-max normalization ----------------------------------------------------


def normalize(values: tuple[float, ...]) -> tuple[float, ...]:
    """u_norm = (u - min) / (max - min) over the WHOLE candidate set; a flat
    hat (max == min) normalizes to 0.5 everywhere (D2 border)."""
    lo, hi = min(values), max(values)
    if hi == lo:
        return tuple(0.5 for _ in values)
    return tuple((v - lo) / (hi - lo) for v in values)


# -- Deterministic selection (spec sec 6) --------------------------------------------


def select_best_index(
    scores: tuple[float, ...],
    judge_costs: tuple[float, ...],
    candidates: tuple[Candidate, ...],
) -> int:
    """argmax(scores) with the shared tie-break: (1) lower judge C, (2) lower Q,
    (3) lower SL. Used by every ideal AND by the settlement -- same rule, so
    w_x=1 collapses exactly onto hat x's ideal."""
    best = 0
    for i in range(1, len(scores)):
        a, b = scores[i], scores[best]
        if a > b:
            best = i
        elif a == b:
            ka = (judge_costs[i], candidates[i].order_quantity, candidates[i].service_level)
            kb = (judge_costs[best], candidates[best].order_quantity, candidates[best].service_level)
            if ka < kb:
                best = i
    return best


# -- KPIs per hat at a candidate (frozen keys; council/job/tests rely on them) ---


def hat_kpis(inputs: HatInputs, hat_key: str, cand: Candidate) -> dict[str, float]:
    c_q = unit_cost_at(inputs, cand.order_quantity)
    ss = ss_units(inputs, cand.service_level)
    avg_inv_usd = c_q * (cand.order_quantity / 2.0 + ss)
    if hat_key == HAT_COMPRADOR:
        return {
            "effective_unit_cost": c_q + inputs.config.order_cost / cand.order_quantity,
            "unit_price": c_q,
            "orders_per_year": inputs.annual_demand / cand.order_quantity,
        }
    if hat_key == HAT_PLANNER:
        return {
            "policy_cost": -_u_planner_valid(inputs, cand),
            "service_level": cand.service_level,
            "safety_stock_units": ss,
        }
    if hat_key == HAT_CFO:
        return {
            "capital_charge_usd": inputs.config.wacc * avg_inv_usd,
            "avg_inventory_usd": avg_inv_usd,
            "dio_days": days_inventory_outstanding(avg_inv_usd, inputs.annual_demand * c_q),
        }
    if hat_key == HAT_COMERCIAL:
        return {
            "fill_rate": _u_comercial(inputs, cand),
            "expected_units_short_per_year": _expected_units_short_per_year(inputs, cand),
        }
    if hat_key == HAT_TRANSPORTE:
        # Raises when no profile was declared -- never a zero freight, which would
        # read as "this policy ships for free" instead of "nobody measured it".
        sel = freight_quote(inputs, cand.order_quantity)
        return {
            "freight_per_unit_usd": sel.recommended_cost / cand.order_quantity,
            "annual_freight_usd": (inputs.annual_demand / cand.order_quantity) * sel.recommended_cost,
            "shipments_per_year": inputs.annual_demand / cand.order_quantity,
            "transit_days": sel.transit_days,
        }
    raise ValueError(f"unknown hat: {hat_key}")


_HEADLINE = {
    HAT_COMPRADOR: "effective_unit_cost",
    HAT_PLANNER: "policy_cost",
    HAT_CFO: "capital_charge_usd",
    HAT_COMERCIAL: "fill_rate",
    HAT_TRANSPORTE: "freight_per_unit_usd",
}


def headline_kpi(hat_key: str) -> str:
    """The one KPI each hat's acta row is written in (its own units)."""
    return _HEADLINE[hat_key]


# -- One evaluation of the whole grid (shared by N4 and N5) -----------------------


@dataclass(frozen=True)
class GridEvaluation:
    """Everything both levels need, computed once per SKU. The utility dicts are
    keyed by `evaluable_hats(inputs)`, so iterating `HAT_KEYS` over them KeyErrors
    on a SKU with no freight profile -- deliberately, rather than skipping the
    area in silence."""

    candidates: tuple[Candidate, ...]
    judge_costs: tuple[float, ...]
    utilities_raw: dict[str, tuple[float, ...]]
    utilities_norm: dict[str, tuple[float, ...]]


def evaluate(inputs: HatInputs) -> GridEvaluation:
    cands = candidate_grid(inputs)
    raw = utilities_raw(inputs, cands)
    return GridEvaluation(
        candidates=cands,
        judge_costs=tuple(decision_cost(inputs, c) for c in cands),
        utilities_raw=raw,
        utilities_norm={k: normalize(v) for k, v in raw.items()},
    )
