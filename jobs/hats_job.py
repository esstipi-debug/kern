"""Hats agent job (Kern tools #42/#43): N4 hat_tension + N5 hat_settlement.

ONE pandas `prepare()` (D6) reads a weekly demand CSV directly (deliberately
NOT via jobs/intake.py) and builds per-SKU HatInputs; `run_tension()` renders
the 4-hat disagreement as a protected OPTIONS outcome (the human resolves);
`run_settlement()` reconciles with the operator's weight POLICY (D4) into one
(Q*, SL*) plan + acta de concesiones as a protected HANDOFF. Neither run can
ever emit EXECUTED - the QA gates pin it.

CSV contract: one row = one weekly observation. Required per SKU: product_id,
quantity, unit_cost, lead_time_days (date optional, unused). Transaction-level
files belong to the inventory_optimization intake path, not here.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.eoq import PriceBreak
from src.guided import (
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
from src.hat_council import Settlement, TensionMap, settle, tension_map
from src.hats import (
    HAT_KEYS,
    HATS,
    HatConfig,
    HatInputs,
    baseline_plan,
    build_inputs,
    decision_cost,
    hat_kpis,
    headline_kpi,
    parse_weights,
)

MIN_OBS = 5   # weekly rows needed for a usable sigma

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "product", "Product")
_QTY_COLS = ("quantity", "qty", "units", "demand", "Quantity")
_COST_COLS = ("unit_cost", "cost", "unit_price", "price", "Unit Cost")
_LEAD_COLS = ("lead_time_days", "lead_time", "leadtime", "lead")

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


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the weekly demand CSV and build one HatInputs per SKU (sorted)."""
    params = params or {}
    config = config_from_params(params)
    weights = parse_request_weights(params)
    breaks = _injected_breaks(params)

    df = pd.read_csv(data_path)
    prod = _pick(df, params.get("product_col"), _PRODUCT_COLS)
    qty = _pick(df, params.get("quantity_col"), _QTY_COLS)
    cost = _pick(df, params.get("cost_col"), _COST_COLS)
    lead = _pick(df, params.get("lead_col"), _LEAD_COLS)
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
        inputs.append(build_inputs(
            sku=pid,
            annual_demand=config.periods_per_year * mu_w,
            mean_weekly=mu_w,
            std_weekly=sigma_w,
            lead_time_weeks=lead_days / 7.0,
            unit_cost=unit_cost,
            config=config,
            price_breaks=breaks,
        ))
    if not inputs:
        detail = f" (skipped: {'; '.join(skipped)})" if skipped else ""
        wanted = f"; sku filter: {sku_filter!r}" if sku_filter else ""
        raise ValueError(f"no usable SKUs in the demand csv{detail}{wanted}")
    return {"inputs": tuple(inputs), "weights": weights, "config": config,
            "skipped": tuple(skipped)}


# -- shared rendering helpers -------------------------------------------------


def _weights_str(weights: dict[str, float]) -> str:
    return ", ".join(f"{k}={weights[k]:.2f}" for k in HAT_KEYS)


def _four_hat_line(inputs: HatInputs, cand) -> str:
    """The 4 hats' headline KPIs evaluated at ONE candidate (spec sec 7)."""
    v = {k: hat_kpis(inputs, k, cand)[headline_kpi(k)] for k in HAT_KEYS}
    return (f"comprador {v['comprador']:.2f} usd/u | planner {v['planner']:,.0f} usd/anio | "
            f"cfo {v['cfo']:,.0f} usd/anio | comercial fill {v['comercial']:.1%}")


# -- N4: tension --------------------------------------------------------------


@dataclass(frozen=True)
class TensionReport:
    maps: tuple[TensionMap, ...]
    outcome: GuidedOutcome
    summary: str
    n_skus: int
    price_breaks_assumed: bool


def _tension_options(inputs: HatInputs, tmap: TensionMap) -> list[ExecutionOption]:
    """5 options: one per hat ideal + the baseline. score = judge cost min-max
    normalized (best cost -> 1.0); the ranking is informative, the CHOICE is
    human (that framing lives in the outcome summary)."""
    labeled = [(HATS[k].label, tmap.ideals[k].candidate, HATS[k].objetivo) for k in HAT_KEYS]
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
            summary=f"{objetivo}. En esta opcion: {_four_hat_line(inputs, cand)}. "
                    f"Costo juez {cost:,.0f} usd/anio.",
            score=round(score, 6),
            action=f"adoptar Q={cand.order_quantity:,.0f} y SL={cand.service_level:.1%} "
                   f"para {tmap.sku}",
            tradeoffs="ver los 4 KPIs de la descripcion - cada sombrero cede algo distinto",
        ))
    return options


def _clash_line(c) -> str:
    return (f"{c.hat_a} vs {c.hat_b}: dQ {c.delta_q:+,.0f} u, "
            f"{c.delta_capital_usd:+,.0f} usd inventario prom., fill {c.delta_fill_rate:+.1%}")


def run_tension(payload: dict) -> TensionReport:
    """One tension map per SKU; the guided OPTIONS spotlight the flagship SKU
    (largest top-clash $ gap; ties -> lowest sku)."""
    inputs = payload["inputs"]
    maps = tuple(tension_map(i) for i in inputs)
    assumed = any(i.price_breaks_assumed for i in inputs)
    order = sorted(range(len(maps)),
                   key=lambda ix: (-abs(maps[ix].clashes[0].delta_capital_usd), maps[ix].sku))
    flag_inputs, flag_map = inputs[order[0]], maps[order[0]]
    top3 = "; ".join(_clash_line(c) for c in flag_map.clashes[:3])
    summary = (
        f"Mapa de tension sobre {len(maps)} SKU(s). SKU foco {flag_map.sku} - "
        f"top choques: {top3}. Orden informativo por costo total del juez - "
        f"la eleccion es humana."
        + (" Tarifario de descuentos sintetico (assumed)." if assumed else "")
    )
    residuals = [Residual(
        description="La opcion la elige un humano; este mapa solo hace visible el conflicto.",
        risk_if_skipped="decidir sin ver el trade-off deja a un area pagando el costo sin saberlo",
    )]
    if assumed:
        residuals.append(_assumed_residual())
    outcome = as_options(summary, _tension_options(flag_inputs, flag_map),
                         confidence=0.8, residuals=residuals)
    short = (f"Decision tension map over {len(maps)} SKU(s); focus {flag_map.sku} "
             f"({abs(flag_map.clashes[0].delta_capital_usd):,.0f} usd avg-inventory gap).")
    return TensionReport(maps=maps, outcome=outcome, summary=short,
                         n_skus=len(maps), price_breaks_assumed=assumed)


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
    if assumed:
        lines.append("")
        lines.append("NOTA: tarifario de descuentos sintetico (assumed), no del proveedor.")
    return "\n".join(lines)


def run_settlement(payload: dict) -> SettlementReport:
    inputs = payload["inputs"]
    weights = payload["weights"]
    settlements = tuple(settle(i, weights) for i in inputs)
    assumed = any(i.price_breaks_assumed for i in inputs)
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
    summary = (
        f"Plan reconciliado para {len(settlements)} SKU(s): valor vs baseline "
        f"{total:+,.0f} usd/anio (pesos: {_weights_str(weights)} - {_POLICY_NOTE})."
        + (" Tarifario (assumed)." if assumed else "")
    )
    outcome = as_handoff(summary, [packet], confidence=0.8, residuals=residuals)
    short = (f"Reconciled replenishment plan over {len(settlements)} SKU(s); "
             f"value vs baseline {total:+,.0f} usd/yr.")
    return SettlementReport(settlements=settlements, outcome=outcome, summary=short,
                            n_skus=len(settlements), weights=dict(weights),
                            price_breaks_assumed=assumed, total_value_usd=total)


# -- QA gates (empty list == passed) ------------------------------------------


def verify_tension(report: TensionReport) -> list[str]:
    """Never-unprotected + N4 shape. A tension outcome must be OPTIONS (never
    EXECUTED - spec sec 7), carry exactly 5 options, and every map must be whole."""
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
    if report.outcome.options and len(report.outcome.options) != len(HAT_KEYS) + 1:
        issues.append(f"expected {len(HAT_KEYS) + 1} options, got {len(report.outcome.options)}")
    for m in report.maps:
        if set(m.ideals) != set(HAT_KEYS):
            issues.append(f"{m.sku}: ideals missing hats")
        if m.candidates_evaluated < 125:
            issues.append(f"{m.sku}: only {m.candidates_evaluated} candidates evaluated")
        if len(m.clashes) != 6:
            issues.append(f"{m.sku}: expected 6 clashes, got {len(m.clashes)}")
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
        for e in s.acta:
            if not 0.0 <= e.concesion <= 1.0:
                issues.append(f"{s.sku}: concesion out of [0,1] for {e.hat_key}")
        if not (s.judge_cost_chosen > 0 and s.judge_cost_baseline > 0):
            issues.append(f"{s.sku}: non-positive judge cost")
        if abs(s.value_vs_baseline_usd - (s.judge_cost_baseline - s.judge_cost_chosen)) > 1e-6:
            issues.append(f"{s.sku}: value_vs_baseline_usd inconsistent")
    return issues
