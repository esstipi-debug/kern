"""Hats agent job (Kern tools #42/#43): N4 hat_tension + N5 hat_settlement.

ONE pandas `prepare()` (D6) reads a weekly demand CSV directly (deliberately
NOT via jobs/intake.py) and builds per-SKU HatInputs; `run_tension()` renders
the disagreement as a protected OPTIONS outcome (the human resolves);
`run_settlement()` reconciles with the operator's weight POLICY (D4) into one
(Q*, SL*) plan + acta de concesiones as a protected HANDOFF. Neither run can
ever emit EXECUTED - the QA gates pin it.

CSV contract: one row = one weekly observation. Required per SKU: product_id,
quantity, unit_cost, lead_time_days (date optional, unused). Transaction-level
files belong to the inventory_optimization intake path, not here.

OPTIONAL per SKU: weight_kg. With it AND a `distance_km` param, the transporte
hat joins the council; without either half it stays off the table and every
surface says so. Nothing here invents a unit weight to fill the gap.

OPTIONAL per SKU: moq / order_multiple (the supplier's terms; run-wide fallbacks
via the `minimum_order_quantity` / `order_multiple` params). With either, the
grid holds only lots the supplier accepts and the baseline is the cheapest
feasible lot around the EOQ (src.constraints.cheapest_feasible_lot) -- so no
ideal, settlement or counterproposal can name a quantity the supplier refuses.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from nosilo_core.guided import (
    EXECUTED,
    HANDOFF,
    OPTIONS,
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_handoff,
    as_options,
    verify_guided,
)
from src.constraints import is_feasible_lot
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.eoq import PriceBreak
from src.export import write_summary_csv
from src.hat_council import (
    UNASSIGNED,
    UNRESOLVED_CONCESSION_THRESHOLD,
    Settlement,
    TensionMap,
    settle,
    tension_map,
)
from src.hats import (
    HAT_CFO,
    HAT_COMERCIAL,
    HAT_COMPRADOR,
    HAT_KEYS,
    HAT_PLANNER,
    HAT_TRANSPORTE,
    HATS,
    MIN_FEASIBLE_Q_POINTS,
    SL_GRID,
    FreightProfile,
    HatConfig,
    HatInputs,
    PerishableProfile,
    baseline_plan,
    build_inputs,
    decision_cost,
    evaluable_hats,
    freight_mode,
    hat_kpis,
    headline_kpi,
    parse_weights,
)
from src.logistics.modes import FreightRates

MIN_OBS = 5   # weekly rows needed for a usable sigma

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "product", "Product")
_QTY_COLS = ("quantity", "qty", "units", "demand", "Quantity")
_COST_COLS = ("unit_cost", "cost", "unit_price", "price", "Unit Cost")
_LEAD_COLS = ("lead_time_days", "lead_time", "leadtime", "lead")
#: Unit weight is a PER-SKU fact, so it is read from the file like unit_cost --
#: not from params, where one number would be imposed on every SKU in the export.
_WEIGHT_COLS = ("weight_kg", "unit_weight_kg", "peso_kg", "weight", "peso")
# Supplier terms. Same vocabulary jobs/intake.py's ALIASES maps for the
# inventory path, minus the spellings `_pick`'s exact match cannot see anyway.
_MOQ_COLS = ("moq", "minimum_order_quantity", "min_order_qty", "min_order_quantity",
             "cantidad_minima", "pedido_minimo", "lote_minimo")
_MULTIPLE_COLS = ("order_multiple", "case_pack", "pack_size", "units_per_case",
                  "multiplo", "multiplo_de_pedido", "unidades_por_caja")
_EXPIRY_COLS = ("days_to_expiry", "days_until_expiry", "shelf_life_days", "shelf_life",
                "vida_util_dias", "dias_para_vencer", "dias_vencimiento")

_POLICY_NOTE = "los pesos son politica del operador, no consenso objetivo"


# -- params -------------------------------------------------------------------


def config_from_params(params: dict | None) -> HatConfig:
    """HatConfig from request params (spec defaults; ValueError on bad values,
    which the tool wrapper maps to needs_clarification)."""
    p = params or {}
    return HatConfig(
        order_cost=float(p.get("order_cost", 75.0)),
        holding_rate=float(p.get("holding_rate", 0.25)),
        wacc=float(p.get("wacc", 0.12)),
        sl_target=float(p.get("sl_target", 0.95)),
        gross_margin_rate=float(p.get("gross_margin_rate", 0.30)),
        periods_per_year=float(p.get("periods_per_year", 52.0)),
    )


def parse_request_weights(params: dict | None) -> dict[str, float]:
    """Weights from request params (D4). ValueError -> needs_clarification."""
    return parse_weights((params or {}).get("weights"))


def _lane_from_params(params: dict | None) -> tuple[float | None, FreightRates | None, float | None]:
    """The RUN-level half of the freight facts: how long the lane is, at what
    tariff, under what delivery window. The per-SKU half (unit weight) comes from
    the CSV.

    `distance_km` absent -> (None, ...) and `transporte` stays off the table for
    every SKU. Deliberately no default distance: a lane length is a fact about a
    client's network, and the whole point of the fifth hat is that it is scored on
    declared facts or not scored at all.
    """
    p = params or {}
    raw_distance = p.get("distance_km")
    distance = None
    if raw_distance is not None:
        distance = float(raw_distance)
        if distance <= 0:
            raise ValueError("distance_km must be > 0")
    raw_rates = p.get("freight_rates")
    rates = None
    if raw_rates is not None:
        if not isinstance(raw_rates, dict):
            raise ValueError("freight_rates must be a mapping of FreightRates fields")
        try:
            rates = FreightRates(**raw_rates)
        except TypeError as exc:
            raise ValueError(f"freight_rates: {exc}") from exc
    raw_transit = p.get("max_transit_days")
    transit = float(raw_transit) if raw_transit is not None else None
    if transit is not None and transit <= 0:
        raise ValueError("max_transit_days must be > 0")
    return distance, rates, transit


def _injected_breaks(params: dict | None) -> tuple[PriceBreak, ...] | None:
    raw = (params or {}).get("price_breaks")
    if raw is None:
        return None
    try:
        breaks = tuple(PriceBreak(min_quantity=float(q), unit_cost=float(p)) for q, p in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "price_breaks must be [[min_qty, unit_price], ...] pairs of numbers") from exc
    if not breaks or any(b.min_quantity < 0 or b.unit_cost <= 0 for b in breaks):
        raise ValueError("price_breaks: min_qty must be >= 0 and unit_price > 0")
    return breaks


# -- prepare ------------------------------------------------------------------


def _pick(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _supplier_term(g: pd.DataFrame, column: str | None, fallback: object, name: str) -> float:
    """One SKU's MOQ or multiple: the strictest positive value in its column,
    else the run-wide param, else 0.0 (no rule). A negative param is a typo,
    not a rule, and is refused rather than silently dropped."""
    if column is not None:
        values = pd.to_numeric(g[column], errors="coerce").dropna()
        values = values[values > 0]
        if len(values):
            return float(values.max())
    if fallback is None or fallback == "":
        return 0.0
    value = float(fallback)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")
    return value


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the weekly demand CSV and build one HatInputs per SKU (sorted)."""
    params = params or {}
    config = config_from_params(params)
    # Validated against the whole vocabulary first so a malformed `weights` fails
    # before the file is read; renormalized at the end over the areas that turned
    # out to be on the table.
    weights = parse_request_weights(params)
    breaks = _injected_breaks(params)
    distance_km, freight_rates, max_transit = _lane_from_params(params)

    df = pd.read_csv(data_path)
    prod = _pick(df, params.get("product_col"), _PRODUCT_COLS)
    qty = _pick(df, params.get("quantity_col"), _QTY_COLS)
    cost = _pick(df, params.get("cost_col"), _COST_COLS)
    lead = _pick(df, params.get("lead_col"), _LEAD_COLS)
    weight = _pick(df, params.get("weight_col"), _WEIGHT_COLS)
    moq_col = _pick(df, params.get("moq_col"), _MOQ_COLS)
    multiple_col = _pick(df, params.get("multiple_col"), _MULTIPLE_COLS)
    expiry_col = _pick(df, params.get("expiry_col"), _EXPIRY_COLS)
    missing = [n for n, c in (("product_id", prod), ("quantity", qty),
                              ("unit_cost", cost), ("lead_time_days", lead)) if c is None]
    if missing:
        raise ValueError(
            f"demand csv: could not find {', '.join(missing)} "
            f"(columns seen: {list(df.columns)[:10]})")

    sku_filter = params.get("sku")
    inputs: list[HatInputs] = []
    skipped: list[str] = []
    for pid, g in df.groupby(prod, sort=True):          # sort=True -> deterministic order
        pid = str(pid)
        if sku_filter and pid != str(sku_filter):
            continue
        q = pd.to_numeric(g[qty], errors="coerce").dropna()
        q = q[q >= 0]
        if len(q) < MIN_OBS or float(q.mean()) <= 0:
            skipped.append(f"{pid}: fewer than {MIN_OBS} usable weekly rows or zero demand")
            continue
        unit_cost = float(pd.to_numeric(g[cost], errors="coerce").dropna().mean())
        lead_days = float(pd.to_numeric(g[lead], errors="coerce").dropna().median())
        if unit_cost <= 0 or lead_days <= 0:
            skipped.append(f"{pid}: unit_cost and lead_time_days must be > 0")
            continue
        mu_w = float(q.mean())
        sigma_w = float(q.std(ddof=1)) if len(q) > 1 else 0.0
        # transporte needs BOTH halves. Either one missing leaves this SKU with
        # four areas -- per SKU, not per run: a file where only some rows carry a
        # weight is a partially measurable portfolio, and saying so beats both
        # dropping the column and inventing the rest of it.
        freight = None
        if distance_km is not None and weight is not None:
            w = pd.to_numeric(g[weight], errors="coerce").dropna()
            w = w[w > 0]
            if len(w):
                freight = FreightProfile(
                    weight_kg_per_unit=float(w.mean()),
                    distance_km=distance_km,
                    rates=freight_rates,
                    max_transit_days=max_transit,
                )
        shelf_days = None
        if expiry_col is not None:
            e = pd.to_numeric(g[expiry_col], errors="coerce").dropna()
            e = e[e > 0]
            if len(e):
                shelf_days = float(e.mean())
        elif params.get("shelf_life_days") is not None:
            shelf_days = float(params["shelf_life_days"])

        perishable = None
        if shelf_days is not None and shelf_days > 0:
            channel_gating = float(params.get("channel_gating_pct", 0.0))
            transit_lead = float(params.get("transit_lead_time_days", 0.0))
            perishable = PerishableProfile(
                shelf_life_days=shelf_days,
                channel_gating_pct=channel_gating,
                transit_lead_time_days=transit_lead,
            )
        inputs.append(build_inputs(
            sku=pid,
            annual_demand=config.periods_per_year * mu_w,
            mean_weekly=mu_w,
            std_weekly=sigma_w,
            lead_time_weeks=lead_days / 7.0,
            unit_cost=unit_cost,
            config=config,
            price_breaks=breaks,
            freight=freight,
            minimum_order_quantity=_supplier_term(
                g, moq_col, params.get("minimum_order_quantity"), "minimum_order_quantity"),
            order_multiple=_supplier_term(
                g, multiple_col, params.get("order_multiple"), "order_multiple"),
            perishable=perishable,
        ))
    if not inputs:
        detail = f" (skipped: {'; '.join(skipped)})" if skipped else ""
        wanted = f"; sku filter: {sku_filter!r}" if sku_filter else ""
        raise ValueError(f"no usable SKUs in the demand csv{detail}{wanted}")
    # Renormalize over the areas at least one SKU can score. Reporting a header of
    # five weights on a run where no SKU has freight would describe a settlement
    # that never happened -- settle() weighs per SKU over that SKU's own areas.
    on_table = tuple(k for k in HAT_KEYS if any(k in evaluable_hats(i) for i in inputs))
    weights = parse_weights(params.get("weights"), on_table)
    return {"inputs": tuple(inputs), "weights": weights, "config": config,
            "skipped": tuple(skipped)}


# -- shared rendering helpers -------------------------------------------------


def _weights_str(weights: dict[str, float]) -> str:
    """HAT_KEYS order over whatever keys the policy actually carries -- the dict
    holds only the areas on the table, which is 4 or 5."""
    return ", ".join(f"{k}={weights[k]:.2f}" for k in HAT_KEYS if k in weights)


def _excluded_str(inputs: HatInputs) -> str:
    """The areas this SKU could not put on the table, or "" when all five were."""
    missing = [k for k in HAT_KEYS if k not in evaluable_hats(inputs)]
    return ", ".join(missing)


def _hat_line(inputs: HatInputs, cand) -> str:
    """Every EVALUABLE hat's headline KPI at ONE candidate (spec sec 7). An area
    off the table contributes no fragment; naming it is the caller's job, once,
    rather than repeated in every option summary."""
    v = {k: hat_kpis(inputs, k, cand)[headline_kpi(k)] for k in evaluable_hats(inputs)}
    parts = [
        f"comprador {v[HAT_COMPRADOR]:.2f} usd/u",
        f"planner {v[HAT_PLANNER]:,.0f} usd/anio",
        f"cfo {v[HAT_CFO]:,.0f} usd/anio",
        f"comercial fill {v[HAT_COMERCIAL]:.1%}",
    ]
    if HAT_TRANSPORTE in v:
        parts.append(f"transporte {v[HAT_TRANSPORTE]:.2f} usd/u de flete "
                     f"({freight_mode(inputs, cand)})")
    return " | ".join(parts)


# -- N4: tension --------------------------------------------------------------


@dataclass(frozen=True)
class TensionReport:
    maps: tuple[TensionMap, ...]
    outcome: GuidedOutcome
    summary: str
    n_skus: int
    price_breaks_assumed: bool
    #: The SKU the OPTIONS were built from. Stored rather than re-derived so the
    #: QA gate can check the option count against the RIGHT map's hat count.
    flagship_sku: str = ""
    #: True when some SKU priced its freight off the illustrative rate card.
    freight_rates_assumed: bool = False


def _tension_options(inputs: HatInputs, tmap: TensionMap) -> list[ExecutionOption]:
    """One option per EVALUABLE hat ideal + the baseline (5 or 6). score = judge
    cost min-max normalized (best cost -> 1.0); the ranking is informative, the
    CHOICE is human (that framing lives in the outcome summary)."""
    labeled = [(HATS[k].label, tmap.ideals[k].candidate, HATS[k].objetivo)
               for k in HAT_KEYS if k in tmap.ideals]
    labeled.append(("Baseline (politica actual)", baseline_plan(inputs),
                    "mantener la politica (s,Q) clasica al 95% de servicio"))
    costs = [decision_cost(inputs, cand) for _, cand, _ in labeled]
    lo, hi = min(costs), max(costs)
    options = []
    for (label, cand, objetivo), cost in zip(labeled, costs):
        score = 0.5 if hi == lo else (hi - cost) / (hi - lo)
        options.append(ExecutionOption(
            label=f"{tmap.sku} - {label}: Q={cand.order_quantity:,.0f}, "
                  f"SL={cand.service_level:.1%}",
            summary=f"{objetivo}. En esta opcion: {_hat_line(inputs, cand)}. "
                    f"Costo juez {cost:,.0f} usd/anio.",
            score=round(score, 6),
            action=f"adoptar Q={cand.order_quantity:,.0f} y SL={cand.service_level:.1%} "
                   f"para {tmap.sku}",
            tradeoffs=f"ver los {len(tmap.ideals)} KPIs de la descripcion - cada sombrero "
                      "cede algo distinto",
        ))
    return options


def _lot_rule_note(inputs: HatInputs) -> str:
    """One sentence, or "": the grid of THIS SKU holds only lots its supplier
    accepts. Said in the summary because a reader comparing an ideal against
    the textbook EOQ has to know why the numbers are round."""
    if not inputs.lot_rule:
        return ""
    return (f" Terminos del proveedor de {inputs.sku} ({inputs.lot_rule}): todos los "
            "candidatos son lotes que el proveedor acepta, y el baseline es el lote "
            "factible mas barato alrededor del EOQ.")


def _lot_rules_note(inputs) -> str:
    ruled = [i for i in inputs if i.lot_rule]
    if not ruled:
        return ""
    listed = ", ".join(f"{i.sku} ({i.lot_rule})" for i in ruled[:5])
    more = f" y {len(ruled) - 5} mas" if len(ruled) > 5 else ""
    return (f" Terminos del proveedor aplicados en {len(ruled)} SKU(s): {listed}{more} - "
            "Q* es un lote que el proveedor acepta.")


def _clash_line(c) -> str:
    return (f"{c.hat_a} vs {c.hat_b}: dQ {c.delta_q:+,.0f} u, "
            f"{c.delta_capital_usd:+,.0f} usd inventario prom., fill {c.delta_fill_rate:+.1%}")


def run_tension(payload: dict) -> TensionReport:
    """One tension map per SKU; the guided OPTIONS spotlight the flagship SKU
    (largest top-clash $ gap; ties -> lowest sku)."""
    inputs = payload["inputs"]
    maps = tuple(tension_map(i) for i in inputs)
    assumed = any(i.price_breaks_assumed for i in inputs)
    rates_assumed = any(i.freight is not None and i.freight.rates_assumed for i in inputs)
    off_table = sorted({k for i in inputs for k in HAT_KEYS if k not in evaluable_hats(i)})
    order = sorted(range(len(maps)),
                   key=lambda ix: (-abs(maps[ix].clashes[0].delta_capital_usd), maps[ix].sku))
    flag_inputs, flag_map = inputs[order[0]], maps[order[0]]
    top3 = "; ".join(_clash_line(c) for c in flag_map.clashes[:3])
    summary = (
        f"Mapa de tension sobre {len(maps)} SKU(s). SKU foco {flag_map.sku} - "
        f"top choques: {top3}. Orden informativo por costo total del juez - "
        f"la eleccion es humana."
        + (" Tarifario de descuentos sintetico (assumed)." if assumed else "")
        + (f" Area(s) fuera de la mesa en al menos un SKU: {', '.join(off_table)}."
           if off_table else "")
        + (" Flete valorizado con el tarifario ilustrativo (assumed)." if rates_assumed else "")
        + _lot_rule_note(flag_inputs)
    )
    residuals = [Residual(
        description="La opcion la elige un humano; este mapa solo hace visible el conflicto.",
        risk_if_skipped="decidir sin ver el trade-off deja a un area pagando el costo sin saberlo",
    )]
    if assumed:
        residuals.append(_assumed_residual())
    residuals.extend(_off_table_residuals(off_table, rates_assumed))
    outcome = as_options(summary, _tension_options(flag_inputs, flag_map),
                         confidence=0.8, residuals=residuals)
    short = (f"Decision tension map over {len(maps)} SKU(s); focus {flag_map.sku} "
             f"({abs(flag_map.clashes[0].delta_capital_usd):,.0f} usd avg-inventory gap).")
    return TensionReport(maps=maps, outcome=outcome, summary=short,
                         n_skus=len(maps), price_breaks_assumed=assumed,
                         flagship_sku=flag_map.sku, freight_rates_assumed=rates_assumed)


def _off_table_residuals(off_table: list[str], rates_assumed: bool) -> list[Residual]:
    """A hat that never scored is a residual, not a footnote: the reader has to be
    told which question this council did NOT answer, and what to hand over to make
    it answerable."""
    out: list[Residual] = []
    if off_table:
        out.append(Residual(
            description=f"Area(s) sin medir en esta corrida: {', '.join(off_table)}. "
                        "El consejo se resolvio sin ellas.",
            risk_if_skipped="un acuerdo entre las areas medidas no es un acuerdo de todas; "
                            "para transporte hacen falta peso por unidad (columna weight_kg) "
                            "y distance_km en params",
        ))
    if rates_assumed:
        out.append(Residual(
            description="El flete se valorizo con el tarifario ILUSTRATIVO de "
                        "src.logistics.modes.FreightRates, no con el del transportista.",
            risk_if_skipped="el punto donde el camion se llena depende del tarifario real; "
                            "pasar freight_rates antes de comprometer cantidades",
        ))
    return out


def _assumed_residual() -> Residual:
    return Residual(
        description="El tarifario de descuentos es sintetico (assumed): -2% en 2x EOQ, "
                    "-4% en 4x EOQ (D8).",
        risk_if_skipped="con el tarifario real del proveedor las cantidades con descuento "
                        "pueden cambiar; pedir los price breaks reales",
    )


# -- N5: settlement -----------------------------------------------------------


@dataclass(frozen=True)
class SettlementReport:
    settlements: tuple[Settlement, ...]
    outcome: GuidedOutcome
    summary: str
    n_skus: int
    weights: dict[str, float]
    price_breaks_assumed: bool
    total_value_usd: float
    #: True when some SKU priced its freight off the illustrative rate card.
    freight_rates_assumed: bool = False


def _acta_artifact(settlements: tuple[Settlement, ...], weights: dict[str, float],
                   assumed: bool) -> str:
    lines = [
        f"PLAN RECONCILIADO (pesos: {_weights_str(weights)} - {_POLICY_NOTE})",
        f"{'SKU':<12}{'Q*':>10}{'SL*':>8}{'valor vs baseline usd/anio':>30}",
    ]
    for s in settlements:
        lines.append(f"{s.sku:<12}{s.chosen.order_quantity:>10,.0f}"
                     f"{s.chosen.service_level:>8.1%}{s.value_vs_baseline_usd:>+30,.0f}")
    lines.append("")
    lines.append("ACTA DE CONCESIONES (concesion = 1 - utilidad normalizada en el plan elegido)")
    for s in settlements:
        parts = ", ".join(
            f"{e.hat_key} cede {e.concesion:.2f} ({e.kpi_ideal:,.2f} -> {e.kpi_chosen:,.2f})"
            for e in s.acta)
        lines.append(f"{s.sku}: {parts}")
    # Lo que convierte el acta en una decision y no en un calculo: sobre que demanda
    # se acordo, que quedo sin resolver, y quien firma y cuando.
    lines.append("")
    lines.append("DEMANDA ACORDADA (unidades/anio, la base sobre la que se reconcilio)")
    for s in settlements:
        lines.append(f"{s.sku}: {s.agreed_demand_plan:,.0f}")
    lines.append("")
    lines.append(
        f"BRECHAS SIN RESOLVER (concesion > {UNRESOLVED_CONCESSION_THRESHOLD:.0%} del ideal)"
    )
    abiertas = [(s, g) for s in settlements for g in s.unresolved_gaps]
    for s, g in abiertas:
        lines.append(
            f"{s.sku}: {g.hat_key} cede {g.concesion:.2f} "
            f"({g.kpi_ideal:,.2f} -> {g.kpi_chosen:,.2f}) - queda abierta"
        )
    if not abiertas:
        lines.append("ninguna: ningun sombrero cede por encima del umbral")
    lines.append("")
    lines.append("AREAS FUERA DE LA MESA (no medidas: el acuerdo no las incluye)")
    fuera = [(s, s.excluded_hats) for s in settlements if s.excluded_hats]
    for s, ks in fuera:
        lines.append(
            f"{s.sku}: {', '.join(ks)} - sin insumos para puntuar; los pesos se "
            f"renormalizaron sobre {', '.join(k for k in s.weights)}"
        )
    if not fuera:
        lines.append("ninguna: las cinco areas estuvieron en la mesa en todos los SKU")
    lines.append("")
    firma = settlements[0]
    lines.append(
        f"DECISION: {firma.decision_owner}, {firma.decision_date:%Y-%m-%d}"
        + (
            "  <- SIN ASIGNAR: el plan no tiene dueno, pasar owner= para cerrarlo"
            if firma.decision_owner == UNASSIGNED else ""
        )
    )
    if assumed:
        lines.append("")
        lines.append("NOTA: tarifario de descuentos sintetico (assumed), no del proveedor.")
    return "\n".join(lines)


def run_settlement(payload: dict) -> SettlementReport:
    inputs = payload["inputs"]
    weights = payload["weights"]
    settlements = tuple(
        settle(i, weights, owner=payload.get("owner", UNASSIGNED),
               decided_on=payload.get("decided_on"))
        for i in inputs
    )
    assumed = any(i.price_breaks_assumed for i in inputs)
    rates_assumed = any(i.freight is not None and i.freight.rates_assumed for i in inputs)
    off_table = sorted({k for s in settlements for k in s.excluded_hats})
    total = sum(s.value_vs_baseline_usd for s in settlements)
    packet = HandoffPacket(
        title="Aplicar plan reconciliado (Q*, SL*)",
        steps=[
            "Revisar el acta de concesiones por SKU (artifact de este packet).",
            f"Confirmar los pesos como politica del operador: {_weights_str(weights)}.",
            "Cargar Q* y SL* por SKU en el proceso de compra / planificacion.",
            "Registrar la decision, los pesos y el acta para auditoria.",
        ],
        artifact=_acta_artifact(settlements, weights, assumed),
        data={
            "plans": [
                {"sku": s.sku,
                 "order_quantity": round(s.chosen.order_quantity, 1),
                 "service_level": s.chosen.service_level,
                 "value_vs_baseline_usd": round(s.value_vs_baseline_usd, 2)}
                for s in settlements
            ],
            "weights": dict(weights),
            "price_breaks_assumed": assumed,
            "freight_rates_assumed": rates_assumed,
            "areas_off_table": off_table,
        },
        risk_if_skipped="sin aplicar el plan, cada area sigue optimizando su propio objetivo "
                        "y la decision queda sin un punto unico auditable",
    )
    residuals = [Residual(
        description=f"Los pesos del settlement ({_weights_str(weights)}) son politica del "
                    "operador, no consenso objetivo (D4).",
        risk_if_skipped="otra politica de pesos produce otro plan; revisarla antes de aplicar",
    )]
    if assumed:
        residuals.append(_assumed_residual())
    residuals.extend(_off_table_residuals(off_table, rates_assumed))
    summary = (
        f"Plan reconciliado para {len(settlements)} SKU(s): valor vs baseline "
        f"{total:+,.0f} usd/anio (pesos: {_weights_str(weights)} - {_POLICY_NOTE})."
        + (" Tarifario (assumed)." if assumed else "")
        + (f" Area(s) fuera de la mesa: {', '.join(off_table)}." if off_table else "")
        + _lot_rules_note(inputs)
    )
    outcome = as_handoff(summary, [packet], confidence=0.8, residuals=residuals)
    short = (f"Reconciled replenishment plan over {len(settlements)} SKU(s); "
             f"value vs baseline {total:+,.0f} usd/yr.")
    return SettlementReport(settlements=settlements, outcome=outcome, summary=short,
                            n_skus=len(settlements), weights=dict(weights),
                            price_breaks_assumed=assumed, total_value_usd=total,
                            freight_rates_assumed=rates_assumed)


# -- QA gates (empty list == passed) ------------------------------------------


def verify_tension(report: TensionReport) -> list[str]:
    """Never-unprotected + N4 shape. A tension outcome must be OPTIONS (never
    EXECUTED - spec sec 7), carry one option per evaluable hat plus the baseline,
    and every map must be whole -- where "whole" now means every hat in HAT_KEYS
    is EITHER scored OR named in `excluded_hats`. A map that simply lacks a hat,
    with no declaration, is the failure this gate is here to catch."""
    issues: list[str] = []
    if not report.maps:
        issues.append("no tension maps")
    if report.n_skus != len(report.maps):
        issues.append("n_skus does not match the map count")
    if report.outcome.status != OPTIONS:
        issues.append(f"tension outcome must be OPTIONS, got '{report.outcome.status}'")
    if report.outcome.status == EXECUTED:
        issues.append("tension outcome reports EXECUTED - forbidden")
    issues.extend(verify_guided(report.outcome))
    flagship = next((m for m in report.maps if m.sku == report.flagship_sku), None)
    if report.outcome.options:
        if flagship is None:
            issues.append(f"flagship_sku {report.flagship_sku!r} is not among the maps")
        elif len(report.outcome.options) != len(flagship.ideals) + 1:
            issues.append(f"expected {len(flagship.ideals) + 1} options, got "
                          f"{len(report.outcome.options)}")
    for m in report.maps:
        if set(m.ideals) | set(m.excluded_hats) != set(HAT_KEYS):
            issues.append(
                f"{m.sku}: every hat must be scored or declared excluded; scored="
                f"{sorted(m.ideals)}, excluded={list(m.excluded_hats)}")
        if set(m.ideals) & set(m.excluded_hats):
            issues.append(f"{m.sku}: a hat is both scored and excluded")
        if not m.ideals:
            issues.append(f"{m.sku}: no hat could be scored at all")
        if m.lot_rule:
            # Under supplier terms the Q axis is the legal lots in the span, which
            # can be fewer than 25 -- but never fewer than MIN_FEASIBLE_Q_POINTS,
            # and every ideal has to be one of them.
            floor = len(SL_GRID) * MIN_FEASIBLE_Q_POINTS
            if m.candidates_evaluated < floor:
                issues.append(f"{m.sku}: only {m.candidates_evaluated} candidates evaluated "
                              f"under supplier terms ({m.lot_rule}); floor is {floor}")
            for e in m.ideals.values():
                if not is_feasible_lot(e.candidate.order_quantity,
                                       minimum_order_quantity=m.minimum_order_quantity,
                                       order_multiple=m.order_multiple):
                    issues.append(f"{m.sku}: {e.hat_key}'s ideal Q="
                                  f"{e.candidate.order_quantity:g} violates the supplier "
                                  f"terms ({m.lot_rule})")
        elif m.candidates_evaluated < 125:
            issues.append(f"{m.sku}: only {m.candidates_evaluated} candidates evaluated")
        expected_clashes = len(m.ideals) * (len(m.ideals) - 1) // 2
        if len(m.clashes) != expected_clashes:
            issues.append(f"{m.sku}: expected {expected_clashes} clashes for "
                          f"{len(m.ideals)} hats, got {len(m.clashes)}")
        for e in m.ideals.values():
            if not 0.0 <= e.utility_norm <= 1.0:
                issues.append(f"{m.sku}: utility_norm out of [0,1] for {e.hat_key}")
    return issues


def verify_settlement(report: SettlementReport) -> list[str]:
    """Never-unprotected + N5 shape. A settlement outcome must be HANDOFF (never
    EXECUTED), weights must be a normalized policy, concessions in [0,1], and
    the weights-are-policy residual must be present (spec crit #8)."""
    issues: list[str] = []
    if not report.settlements:
        issues.append("no settlements")
    if report.n_skus != len(report.settlements):
        issues.append("n_skus does not match the settlement count")
    if report.outcome.status != HANDOFF:
        issues.append(f"settlement outcome must be HANDOFF, got '{report.outcome.status}'")
    if report.outcome.status == EXECUTED:
        issues.append("settlement outcome reports EXECUTED - forbidden")
    issues.extend(verify_guided(report.outcome))
    if abs(sum(report.weights.values()) - 1.0) > 1e-9:
        issues.append("weights are not normalized to 1")
    if not any("politica" in r.description for r in report.outcome.residuals):
        issues.append("missing the weights-are-policy residual")
    for s in report.settlements:
        # Same invariant the tension gate enforces: every area is either in the
        # acta or declared excluded. An acta that is simply short one area reads
        # as unanimity among five when it was unanimity among four.
        signed = {e.hat_key for e in s.acta}
        if signed | set(s.excluded_hats) != set(HAT_KEYS):
            issues.append(
                f"{s.sku}: every hat must sign the acta or be declared excluded; "
                f"signed={sorted(signed)}, excluded={list(s.excluded_hats)}")
        if signed & set(s.excluded_hats):
            issues.append(f"{s.sku}: a hat both signed the acta and is excluded")
        if set(s.weights) != signed:
            issues.append(
                f"{s.sku}: the weight policy covers {sorted(s.weights)} but the acta "
                f"covers {sorted(signed)} - they must be the same areas")
        for e in s.acta:
            if not 0.0 <= e.concesion <= 1.0:
                issues.append(f"{s.sku}: concesion out of [0,1] for {e.hat_key}")
        if not (s.judge_cost_chosen > 0 and s.judge_cost_baseline > 0):
            issues.append(f"{s.sku}: non-positive judge cost")
        if abs(s.value_vs_baseline_usd - (s.judge_cost_baseline - s.judge_cost_chosen)) > 1e-6:
            issues.append(f"{s.sku}: value_vs_baseline_usd inconsistent")
        if s.lot_rule and not is_feasible_lot(
            s.chosen.order_quantity, minimum_order_quantity=s.minimum_order_quantity,
            order_multiple=s.order_multiple,
        ):
            issues.append(f"{s.sku}: settled Q={s.chosen.order_quantity:g} violates the "
                          f"supplier terms ({s.lot_rule})")
    return issues


# -- Deliverables -------------------------------------------------------------


def write_tension(report: TensionReport, out_dir, client: str = "Client") -> dict[str, Path]:
    """One row per (SKU, hat): the ideal + that hat's headline KPI, plus the
    SKU's top clash repeated per row for filterability."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for m in report.maps:
        top = m.clashes[0]
        for k in (k for k in HAT_KEYS if k in m.ideals):
            e = m.ideals[k]
            rows.append({
                "sku": m.sku,
                "hat": k,
                "ideal_q": round(e.candidate.order_quantity, 1),
                "ideal_service_level": e.candidate.service_level,
                "kpi": headline_kpi(k),
                "kpi_value": round(e.kpis[headline_kpi(k)], 4),
                "top_clash": f"{top.hat_a} vs {top.hat_b}",
                "top_clash_capital_usd": round(top.delta_capital_usd, 2),
                "price_breaks": "assumed" if report.price_breaks_assumed else "real",
                # Repeated per row so a filtered view can never lose the caveat.
                "areas_fuera_de_la_mesa": ", ".join(m.excluded_hats),
            })
    return {"csv": write_summary_csv(rows, d / "hat_tension.csv")}


def write_settlement(report: SettlementReport, out_dir, client: str = "Client") -> dict[str, Path]:
    """One row per SKU: the chosen plan, both judge costs, the signed value and
    every hat's concession."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for s in report.settlements:
        row = {
            "sku": s.sku,
            "chosen_q": round(s.chosen.order_quantity, 1),
            "chosen_service_level": s.chosen.service_level,
            "judge_cost_chosen": round(s.judge_cost_chosen, 2),
            "judge_cost_baseline": round(s.judge_cost_baseline, 2),
            "value_vs_baseline_usd": round(s.value_vs_baseline_usd, 2),
        }
        for e in s.acta:
            row[f"concesion_{e.hat_key}"] = round(e.concesion, 4)
        row["areas_fuera_de_la_mesa"] = ", ".join(s.excluded_hats)
        rows.append(row)
    return {"csv": write_summary_csv(rows, d / "hat_settlement.csv")}


def _assumed_finding() -> Finding:
    return Finding(
        "Tarifario (assumed)",
        "los descuentos por volumen son sinteticos: -2% en 2x EOQ, -4% en 4x EOQ (D8).",
        impact="pedir el tarifario real del proveedor antes de negociar cantidades")


def _lot_rule_findings(maps_or_settlements) -> list[Finding]:
    """One finding when any SKU's Q axis was laid over supplier lots: the reader
    has to know the ideals are round because the supplier sells that way, not
    because the model is coarse."""
    ruled = [m for m in maps_or_settlements if m.lot_rule]
    if not ruled:
        return []
    listed = ", ".join(f"{m.sku} ({m.lot_rule})" for m in ruled[:5])
    more = f" y {len(ruled) - 5} mas" if len(ruled) > 5 else ""
    return [Finding(
        "Terminos del proveedor",
        f"{len(ruled)} SKU(s) con MOQ o multiplo de caja: {listed}{more}. Cada candidato "
        "evaluado es un lote que el proveedor acepta; el baseline es el lote factible mas "
        "barato alrededor del EOQ.",
        impact="ninguna opcion ni acuerdo nombra una cantidad que el proveedor rechace")]


def _off_table_findings(maps_or_settlements, rates_assumed: bool) -> list[Finding]:
    """Findings, not footnotes, for the areas nobody could score. A deck that
    presents a four-way agreement without naming the fifth area lets the reader
    conclude the fifth had no objection -- which nobody checked."""
    off = sorted({k for m in maps_or_settlements for k in m.excluded_hats})
    out: list[Finding] = []
    if off:
        out.append(Finding(
            "Areas fuera de la mesa",
            f"{', '.join(off)} no se pudo puntuar en al menos un SKU: faltan sus insumos "
            "(transporte necesita peso por unidad y distancia de la ruta).",
            impact="el acuerdo cubre solo a las areas medidas; declarar los insumos "
                   "faltantes convierte una omision en un veredicto"))
    if rates_assumed:
        out.append(Finding(
            "Tarifario de flete (assumed)",
            "el flete se valorizo con el tarifario ilustrativo de src.logistics.modes, "
            "no con el del transportista.",
            impact="el punto donde el camion se llena depende del tarifario real - "
                   "pedirlo antes de comprometer cantidades"))
    return out


def build_tension_deck(report: TensionReport, *, client: str = "Client", prepared: str = "",
                       citations: tuple[str, ...] = (), confidence: float = 0.8) -> Deliverable:
    """The N4 study: what each hat wants, what the disagreement costs in $."""
    order = sorted(report.maps, key=lambda m: (-abs(m.clashes[0].delta_capital_usd), m.sku))
    flag = order[0]
    top = flag.clashes[0]
    summary = (f"Tension de la decision de reabastecimiento sobre {report.n_skus} SKU(s): "
               "ideales por sombrero y choques valuados en usd. La eleccion es humana.")
    findings = [
        Finding("Top clash",
                f"{flag.sku}: {top.hat_a} vs {top.hat_b} difieren en "
                f"{abs(top.delta_capital_usd):,.0f} usd de inventario promedio.",
                impact="la brecha mas cara entre areas - resolverla primero"),
        Finding("Cobertura",
                f"{report.n_skus} SKU(s), {flag.candidates_evaluated} candidatos evaluados "
                "por SKU sobre la misma grilla determinista (Q x SL).",
                impact=f"los {len(flag.ideals)} sombreros puntuan candidatos identicos "
                       "- comparables"),
    ]
    if report.price_breaks_assumed:
        findings.append(_assumed_finding())
    findings.extend(_off_table_findings(report.maps, report.freight_rates_assumed))
    findings.extend(_lot_rule_findings(report.maps))
    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="SKUs con tension mapeada"),
        Kpi("Top clash", f"{flag.sku}: {abs(top.delta_capital_usd):,.0f} usd",
            target="resolver", rationale="mayor brecha de inventario promedio entre ideales"),
        Kpi("Opciones por decision", f"{len(flag.ideals) + 1}",
            rationale=f"{len(flag.ideals)} ideales + baseline"),
        Kpi("Areas en la mesa", f"{len(flag.ideals)} de {len(HAT_KEYS)}",
            target=f"{len(HAT_KEYS)}",
            rationale="un choque entre menos areas no descarta a las que faltan"),
        Kpi("Tarifario", "assumed" if report.price_breaks_assumed else "real",
            rationale="origen de los price breaks (D8)"),
    )
    data_sources = (
        DataSource("Demanda semanal + costo unitario + lead time por SKU",
                   "export WMS/ERP (CSV)", "semanal"),
        DataSource("Price breaks del proveedor",
                   "tarifario del proveedor (sintetico '(assumed)' si falta)", "por negociacion"),
        DataSource("Peso por unidad (columna weight_kg) + distancia de la ruta (params)",
                   "maestro de productos + red del cliente", "por ruta"),
    )
    recommendations = (
        "Usar el mapa en la mesa compras-finanzas-comercial: cada area ve que cede.",
        f"Elegir una de las {len(flag.ideals) + 1} opciones, o pedir el settlement N5 "
        "con pesos explicitos.",
        "Si el tarifario es (assumed), pedir los price breaks reales y re-correr.",
    )
    return Deliverable(
        title="Decision Tension Map (Replenishment)", client=client, summary=summary,
        findings=tuple(findings), kpis=kpis, data_sources=data_sources,
        recommendations=recommendations, citations=tuple(citations), confidence=confidence,
        residual="La eleccion entre opciones es humana: este mapa hace visible el conflicto, "
                 "no lo resuelve. El ranking por costo juez es solo orden informativo.",
        prepared=prepared)


def build_settlement_deck(report: SettlementReport, *, client: str = "Client", prepared: str = "",
                          citations: tuple[str, ...] = (), confidence: float = 0.8) -> Deliverable:
    """The N5 study: the reconciled plan, its signed $ value, and the acta."""
    worst = max(report.settlements, key=lambda s: max(e.concesion for e in s.acta))
    summary = (f"Plan reconciliado (Q*, SL*) para {report.n_skus} SKU(s): valor vs baseline "
               f"{report.total_value_usd:+,.0f} usd/anio. Pesos = politica del operador.")
    findings = [
        Finding("Valor vs baseline",
                f"{report.total_value_usd:+,.0f} usd/anio agregado (con signo: un valor "
                "negativo ES informacion - lo que cuesta esa politica de pesos).",
                impact="el numero que justifica (o cuestiona) la politica de pesos"),
        Finding("Concesion maxima",
                f"{worst.sku}: " + "; ".join(f"{e.hat_key} cede {e.concesion:.2f}"
                                             for e in worst.acta),
                impact="que area paga mas por el consenso - revisarla con esa area"),
    ]
    if report.price_breaks_assumed:
        findings.append(_assumed_finding())
    findings.extend(_off_table_findings(report.settlements, report.freight_rates_assumed))
    findings.extend(_lot_rule_findings(report.settlements))
    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="SKUs con plan reconciliado"),
        Kpi("Valor vs baseline", f"{report.total_value_usd:+,.0f} usd/anio", target="> 0",
            rationale="costo juez del baseline menos costo juez del plan (signed)"),
        Kpi("Pesos", _weights_str(report.weights), rationale="politica del operador (D4)"),
        Kpi("Areas en la mesa", f"{len(report.weights)} de {len(HAT_KEYS)}",
            target=f"{len(HAT_KEYS)}",
            rationale="un acuerdo entre menos areas no es un acuerdo de todas"),
        Kpi("Tarifario", "assumed" if report.price_breaks_assumed else "real",
            rationale="origen de los price breaks (D8)"),
    )
    data_sources = (
        DataSource("Demanda semanal + costo unitario + lead time por SKU",
                   "export WMS/ERP (CSV)", "semanal"),
        DataSource("Price breaks del proveedor",
                   "tarifario del proveedor (sintetico '(assumed)' si falta)", "por negociacion"),
        DataSource("Peso por unidad (columna weight_kg) + distancia de la ruta (params)",
                   "maestro de productos + red del cliente", "por ruta"),
    )
    recommendations = (
        "Aplicar Q*/SL* por SKU via el proceso de compra (packet HANDOFF adjunto).",
        "Revisar el acta: si una concesion duele, cambiar los pesos ES la conversacion.",
        "Re-correr con el tarifario real del proveedor si los breaks son (assumed).",
    )
    return Deliverable(
        title="Reconciled Replenishment Plan", client=client, summary=summary,
        findings=tuple(findings), kpis=kpis, data_sources=data_sources,
        recommendations=recommendations, citations=tuple(citations), confidence=confidence,
        residual="Los pesos son politica del operador, no consenso objetivo (D4). Aplicar el "
                 "plan es un paso humano (HANDOFF); cualquier escritura a un sistema de "
                 "registro pasa por nosilo_core/writeback.py - fuera de alcance aqui.",
        prepared=prepared)
