"""cross_impact playbook: one proposed decision -> the measured effect on the
other areas + an objection verdict (spec docs/superpowers/specs/2026-07-31-
cross-impact-design.md).

Scope boundary vs the two hat tools (READ BEFORE TOUCHING EITHER). ``hat_tension``
answers "where is the disagreement?" - it computes each hat's IDEAL from the grid
and never judges whether a magnitude is tolerable. This module answers a different
question: an area has ALREADY proposed a change, and we measure what it does to
the others and whether any of them would object, against a threshold the operator
declared in the client profile. Neither module imports the other beyond reusing
``hats_job.prepare`` for the CSV read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from nosilo_core.guided import GuidedOutcome, Residual, verify_guided
from src.constraints import feasible_neighbours, is_feasible_lot
from src.cross_impact import CrossImpact, measure
from src.decision_options import Objective, Scenario, decide
from src.deliverable import Deliverable, Finding, Kpi
from src.export import write_summary_csv  # NOTE: export.py, not deliverable.py
from src.hats import (
    HAT_KEYS,
    HAT_TRANSPORTE,
    Candidate,
    HatInputs,
    decision_cost,
    evaluable_hats,
    evaluate,
)
from src.objection import NO_EVALUABLE, OBJETA, Verdict, judge, unmeasurable

from . import hats_job

_POINT_KEYS = ("q", "sl")


def _parse_point(raw: object, name: str) -> Candidate:
    """Turn a {'q': ..., 'sl': ...} mapping into a Candidate, or raise."""
    if raw is None:
        raise ValueError(
            f"'{name}' is required: pass {{'q': <order quantity>, 'sl': <service "
            "level>}}. There is no default - assuming one would state a policy "
            "nobody verified.")
    if not isinstance(raw, dict):
        raise ValueError(f"'{name}' must be a mapping with keys {_POINT_KEYS}, got {type(raw).__name__}")
    missing = [k for k in _POINT_KEYS if k not in raw]
    if missing:
        raise ValueError(f"'{name}' is missing {', '.join(missing)} (needs {_POINT_KEYS})")
    try:
        return Candidate(order_quantity=float(raw["q"]), service_level=float(raw["sl"]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' is not a valid decision point: {exc}") from exc


def _parse_thresholds(raw: object) -> dict[str, float]:
    """Validate the per-area thresholds. Absent/empty is LEGAL - those areas
    come out NO_EVALUABLE rather than taking an assumed tolerance."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("'objection_thresholds' must be a mapping of area -> tolerance")
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in HAT_KEYS:
            raise ValueError(
                f"objection_thresholds key {key!r} is not a known area (expected one of {HAT_KEYS})")
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"objection_thresholds[{key!r}] must be a number: {exc}") from exc
        if not num > 0:
            raise ValueError(f"objection_thresholds[{key!r}] must be > 0, got {num}")
        out[key] = num
    return out


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Build the single-SKU payload. Every gap raises ValueError - the tool
    wrapper turns that into needs_clarification, never a default."""
    params = params or {}

    sku = params.get("sku")
    if not sku:
        raise ValueError("'sku' is required: cross_impact evaluates ONE decision at a time")

    proposed_by = params.get("proposed_by")
    if proposed_by not in HAT_KEYS:
        raise ValueError(
            f"'proposed_by' is required and must be one of {HAT_KEYS}, got {proposed_by!r}")

    current = _parse_point(params.get("current"), "current")
    proposed = _parse_point(params.get("proposed"), "proposed")
    if current == proposed:
        raise ValueError(
            "'current' and 'proposed' are the same point - no hay cambio que evaluar")

    thresholds = _parse_thresholds(params.get("objection_thresholds"))

    # Reuse hats_job's CSV read wholesale; params['sku'] narrows it to one SKU.
    payload = hats_job.prepare(data_path, params)
    inputs = payload["inputs"]
    if len(inputs) != 1:
        raise ValueError(
            f"expected exactly one usable SKU for {sku!r}, got {len(inputs)}")

    return {
        "inputs": inputs[0],
        "current": current,
        "proposed": proposed,
        "proposed_by": proposed_by,
        "thresholds": thresholds,
        "config": payload["config"],
    }


EJECUTAR = "ejecutar"
BILATERAL = "bilateral"
REUNION = "reunion"
INDETERMINADO = "indeterminado"
VERDICTS = (EJECUTAR, BILATERAL, REUNION, INDETERMINADO)

# Confidence cost of ONE area we could not judge at all (no declared threshold in
# the client profile). Deliberately local rather than imported: a general
# `confidence_from_coverage` helper exists on the unmerged branch feat/simworld-
# marcador (commit 25cd8ed) but NOT on main, and this capability must not depend
# on another branch's unmerged work. Unify them if that commit ever lands.
UNEVALUABLE_PENALTY = 0.20
CONFIDENCE_FLOOR = 0.10


def _confidence(n_unevaluable: int) -> float:
    """1.0 when every area in HAT_KEYS was judged; one penalty per area we could
    not -- whether the gap was a missing threshold or a missing measurement."""
    return max(CONFIDENCE_FLOOR, 1.0 - UNEVALUABLE_PENALTY * n_unevaluable)


@dataclass(frozen=True)
class CrossImpactReport:
    impact: CrossImpact
    verdicts: tuple[Verdict, ...]
    n_objections: int
    n_unevaluable: int
    verdict: str
    counterproposal: Candidate | None
    outcome: GuidedOutcome
    summary: str
    # Carried so verify() (Task 6) can re-check that the counterproposal really
    # delivers the zero objections it claims. Not rendered anywhere.
    inputs: HatInputs


def _inputs_of(report: CrossImpactReport) -> HatInputs:
    """The HatInputs behind a report - used only by verify()'s self-check."""
    return report.inputs


#: Why an area produced no row at all, per area. Generic text is not acceptable
#: here: the operator has to know WHICH input to go get.
_MISSING_INPUT_REASON: dict[str, str] = {
    HAT_TRANSPORTE: (
        "transporte: sin perfil de flete declarado (peso por unidad y distancia de la "
        "ruta) - el area no es medible en esta corrida, que no es lo mismo que no objetar"
    ),
}


def _judge_all(inputs, impact: CrossImpact, thresholds: dict[str, float],
               proposed: Candidate) -> tuple[Verdict, ...]:
    """One verdict per hat in HAT_KEYS -- INCLUDING the areas `impact` has no row
    for. Iterating `impact.rows` alone would drop an unmeasurable area from the
    verdict list entirely, and a missing row counts as zero objections in every
    caller downstream; that is the "nobody objects" this module exists to refuse.
    The planner also gets the service-constraint flag its headline KPI cannot
    express (spec 9.1)."""
    breach = proposed.service_level < inputs.config.sl_target
    rows = {row.hat_key: row for row in impact.rows}
    out: list[Verdict] = []
    for hat_key in HAT_KEYS:
        row = rows.get(hat_key)
        if row is None:
            out.append(unmeasurable(hat_key, _MISSING_INPUT_REASON.get(
                hat_key, f"{hat_key}: sin insumos para medir el area en esta corrida")))
            continue
        out.append(judge(row.hat_key, row.value_current, row.value_proposed,
                         thresholds.get(row.hat_key), breaches_sl_target=breach))
    return tuple(out)


def _global_verdict(n_objections: int, n_unevaluable: int) -> str:
    """First match wins. `indeterminado` outranks `ejecutar` on purpose: a
    declared gap must never be reported as "nobody objects"."""
    if n_unevaluable > 0:
        return INDETERMINADO
    if n_objections == 0:
        return EJECUTAR
    if n_objections == 1:
        return BILATERAL
    return REUNION


def _find_counterproposal(inputs, thresholds: dict[str, float],
                          current: Candidate, proposed_by: str,
                          n_unevaluable: int) -> Candidate | None:
    """Cheapest grid point (by judge cost) where NO area objects.

    Returns None when no such point exists - the option is simply not offered.
    The threshold is never loosened to manufacture one. Not even searched when
    any area is NO_EVALUABLE: "zero objections" is not determinable then.
    """
    if n_unevaluable > 0:
        return None
    best: Candidate | None = None
    best_cost = float("inf")
    for cand in evaluate(inputs).candidates:
        if cand == current:
            continue
        impact = measure(inputs, current, cand, proposed_by)
        verdicts = _judge_all(inputs, impact, thresholds, cand)
        if any(v.status == OBJETA for v in verdicts):
            continue
        cost = decision_cost(inputs, cand)
        if cost < best_cost:
            best, best_cost = cand, cost
    return best


def _objections_suffix(n_unevaluable: int) -> str:
    """Appended to an 'Objeciones: N' summary so a zero on an indeterminate run
    never reads as a measured, clean zero (spec: a declared gap must never be
    papered over). Says "sin veredicto" rather than "sin umbral": since the fifth
    hat there are two ways to be unevaluable -- no declared threshold, or no
    inputs to measure the area at all -- and each verdict's own `reason` carries
    which one it was."""
    if n_unevaluable == 0:
        return ""
    return f" (y {n_unevaluable} area(s) sin veredicto posible)"


def _scenarios(inputs, impact: CrossImpact, thresholds: dict[str, float],
               n_objections: int, counterproposal: Candidate | None,
               n_unevaluable: int) -> list[Scenario]:
    cur, prop = impact.current, impact.proposed
    suffix = _objections_suffix(n_unevaluable)
    out = [
        Scenario(
            label=f"Ejecutar la propuesta de {impact.proposed_by}",
            summary=(f"{impact.sku}: Q={prop.order_quantity:,.0f}, "
                     f"SL={prop.service_level:.1%}. Objeciones: {n_objections}{suffix}."),
            metrics={"n_objections": float(n_objections),
                     "judge_cost": impact.judge_cost_proposed},
            action=(f"adoptar Q={prop.order_quantity:,.0f} y SL={prop.service_level:.1%} "
                    f"para {impact.sku}"),
            tradeoffs="ver el detalle por area en cross_impact.csv",
        ),
        Scenario(
            label="Mantener el estado actual",
            summary=(f"{impact.sku}: Q={cur.order_quantity:,.0f}, "
                     f"SL={cur.service_level:.1%}. Objeciones: 0{suffix} (nada cambia)."),
            metrics={"n_objections": 0.0, "judge_cost": impact.judge_cost_current},
            action=f"no cambiar la politica de {impact.sku}",
            tradeoffs="se conserva el costo actual y se pierde el efecto buscado",
        ),
    ]
    if counterproposal is not None:
        out.append(Scenario(
            label="Contra-propuesta sin objeciones",
            summary=(f"{impact.sku}: Q={counterproposal.order_quantity:,.0f}, "
                     f"SL={counterproposal.service_level:.1%}. Ninguna area objeta."),
            metrics={"n_objections": 0.0,
                     "judge_cost": decision_cost(inputs, counterproposal)},
            action=(f"adoptar Q={counterproposal.order_quantity:,.0f} y "
                    f"SL={counterproposal.service_level:.1%} para {impact.sku}"),
            tradeoffs="el punto mas barato de la grilla que nadie objeta",
        ))
    return out


def _infeasible_lot_residuals(inputs, proposed: Candidate) -> list[Residual]:
    """The proposal is measured as proposed -- but when its Q is not a lot the
    supplier accepts, the buyer will end up ordering something else, and the
    effect measured here is the effect of a quantity nobody can buy. Named, with
    the nearest legal lots on each side, so the proposer can re-propose one.
    The counterproposal never needs this: it comes off a grid of legal lots."""
    if not inputs.lot_rule or is_feasible_lot(proposed.order_quantity, **inputs.lot_terms):
        return []
    up, down = feasible_neighbours(proposed.order_quantity, **inputs.lot_terms)
    nearest = f"{up:,.0f}" if down is None else f"{down:,.0f} o {up:,.0f}"
    return [Residual(
        description=(f"La Q propuesta ({proposed.order_quantity:,.0f}) no cumple los terminos "
                     f"del proveedor ({inputs.lot_rule}); los lotes factibles mas cercanos "
                     f"son {nearest}."),
        risk_if_skipped=("ejecutar una orden que el proveedor rechaza o redondea por su cuenta: "
                         "el impacto medido aca es el de la Q propuesta, no el del lote que "
                         "finalmente se compre - re-proponer con un lote factible"),
    )]


def _excluded_note(inputs) -> str:
    """Names the areas left off the table, inline in the summary. Empty when all
    five were measured -- silence then means the same thing as the sentence."""
    missing = [k for k in HAT_KEYS if k not in evaluable_hats(inputs)]
    if not missing:
        return ""
    return f" (fuera: {', '.join(missing)})"


def run(payload: dict) -> CrossImpactReport:
    """Measure, judge, rank. Nothing is executed and nothing is written back."""
    inputs = payload["inputs"]
    current, proposed = payload["current"], payload["proposed"]
    proposed_by, thresholds = payload["proposed_by"], payload["thresholds"]

    impact = measure(inputs, current, proposed, proposed_by)
    verdicts = _judge_all(inputs, impact, thresholds, proposed)
    n_obj = sum(1 for v in verdicts if v.status == OBJETA)
    n_unk = sum(1 for v in verdicts if v.status == NO_EVALUABLE)
    verdict = _global_verdict(n_obj, n_unk)
    counter = _find_counterproposal(inputs, thresholds, current, proposed_by, n_unk)

    reading = {
        EJECUTAR: "ninguna area objeta - no hace falta convocar a nadie",
        BILATERAL: "una sola area objeta - alcanza una conversacion bilateral",
        REUNION: "dos o mas areas objetan - la reunion si hace falta",
        INDETERMINADO: (f"{n_unk} area(s) sin veredicto posible - no se puede afirmar "
                        "que nadie objeta"),
    }[verdict]
    summary = (
        f"{impact.sku}: {proposed_by} propone Q={proposed.order_quantity:,.0f}, "
        f"SL={proposed.service_level:.1%} (hoy Q={current.order_quantity:,.0f}, "
        f"SL={current.service_level:.1%}). Veredicto: {verdict} - {reading}. "
        "Los umbrales son POLITICA DEL OPERADOR, no un consenso objetivo. "
        f"Alcance: un SKU, el espacio (Q, SL), {len(evaluable_hats(inputs))} area(s) "
        f"medible(s) de {len(HAT_KEYS)}{_excluded_note(inputs)} - almacen no es "
        "ninguna de ellas."
    )

    residuals = [
        Residual(
            description="La eleccion final la hace un humano; esto mide y ordena, no ejecuta.",
            risk_if_skipped="adoptar un cambio que otra area objeta sin que nadie lo haya aceptado",
        ),
        Residual(
            description=("Almacen no es uno de los sombreros de HAT_KEYS: su impacto "
                         "no fue medido."),
            risk_if_skipped="un cambio de Q sin capacidad fisica para absorberlo",
        ),
    ]
    residuals.extend(_infeasible_lot_residuals(inputs, proposed))
    outcome = decide(
        summary,
        _scenarios(inputs, impact, thresholds, n_obj, counter, n_unk),
        [Objective("n_objections", weight=2.0), Objective("judge_cost", weight=1.0)],
        confidence=_confidence(n_unk),
        residuals=residuals,
    )

    return CrossImpactReport(
        impact=impact, verdicts=verdicts, n_objections=n_obj, n_unevaluable=n_unk,
        verdict=verdict, counterproposal=counter, outcome=outcome, summary=summary,
        inputs=inputs,
    )


def _finite_issues(report: CrossImpactReport) -> list[str]:
    issues = []
    numbers = [("judge_cost_current", report.impact.judge_cost_current),
               ("judge_cost_proposed", report.impact.judge_cost_proposed)]
    for row in report.impact.rows:
        numbers += [(f"{row.hat_key}.value_current", row.value_current),
                    (f"{row.hat_key}.value_proposed", row.value_proposed),
                    (f"{row.hat_key}.delta", row.delta)]
    for v in report.verdicts:
        for name in ("kpi_current", "kpi_proposed", "deterioration", "threshold"):
            value = getattr(v, name)
            if value is not None:
                numbers.append((f"{v.hat_key}.{name}", value))
    for name, value in numbers:
        if not math.isfinite(value):
            issues.append(f"non-finite number in {name}: {value}")
    return issues


def verify(report: CrossImpactReport) -> list[str]:
    """QA gate. Empty list = passed. QA fails => the orchestrator ships nothing.

    Vetoes the DISHONEST combination, not the gap: reporting `indeterminado`
    with the evaluable areas measured and the rest empty is a valid delivery.
    """
    issues: list[str] = []

    if report.verdict not in VERDICTS:
        issues.append(f"unknown verdict {report.verdict!r} (expected one of {VERDICTS})")

    if tuple(v.hat_key for v in report.verdicts) != HAT_KEYS:
        issues.append(
            f"expected exactly one verdict per hat in {HAT_KEYS}, got "
            f"{tuple(v.hat_key for v in report.verdicts)}")

    n_unk = sum(1 for v in report.verdicts if v.status == NO_EVALUABLE)
    if n_unk > 0 and report.verdict != INDETERMINADO:
        issues.append(
            f"{n_unk} area(s) are no_evaluable but the verdict is "
            f"{report.verdict!r} - a declared gap can never be reported as a "
            "clean verdict; it must be 'indeterminado'")

    for v in report.verdicts:
        empty = [n for n in ("kpi_current", "kpi_proposed", "deterioration", "threshold")
                 if getattr(v, n) is None]
        if v.status == NO_EVALUABLE:
            if empty != ["kpi_current", "kpi_proposed", "deterioration", "threshold"]:
                issues.append(
                    f"verdict '{v.hat_key}' is no_evaluable but still carries "
                    f"numbers (populated: {[n for n in ('kpi_current', 'kpi_proposed', 'deterioration', 'threshold') if n not in empty]})")
        elif empty:
            issues.append(f"verdict '{v.hat_key}' is judged ({v.status}) but "
                          f"{', '.join(empty)} is empty")

    issues += _finite_issues(report)
    issues += verify_guided(report.outcome)

    if sum(1 for o in report.outcome.options if o.recommended) != 1:
        issues.append("the options must carry exactly one recommended default")

    if report.counterproposal is not None:
        if n_unk > 0:
            issues.append("a counterproposal was offered while areas are no_evaluable - "
                          "'zero objections' is not determinable")
        else:
            # self-check: the counterproposal must deliver what it promises
            thresholds = {v.hat_key: v.threshold for v in report.verdicts
                          if v.threshold is not None}
            recheck = _judge_all(
                _inputs_of(report), measure(_inputs_of(report), report.impact.current,
                                            report.counterproposal, report.impact.proposed_by),
                thresholds, report.counterproposal)
            if any(v.status == OBJETA for v in recheck):
                issues.append("the counterproposal still draws objections on re-check")

    return issues


def write(report: CrossImpactReport, out_dir, client: str = "Client") -> dict[str, Path]:
    """Two CSVs: the per-area detail, and the ONE countable verdict row.

    An unevaluable area's cells are written EMPTY (''), never 0 - the gap has to
    look like a gap in the spreadsheet too.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    def cell(value: float | None, digits: int) -> object:
        return "" if value is None else round(value, digits)

    rows = []
    for row, v in zip(report.impact.rows, report.verdicts):
        rows.append({
            "sku": report.impact.sku,
            "hat": row.hat_key,
            "kpi": row.kpi_name,
            "value_current": cell(v.kpi_current, 4),
            "value_proposed": cell(v.kpi_proposed, 4),
            "delta": cell(None if v.kpi_current is None else row.delta, 4),
            "deterioration": cell(v.deterioration, 4),
            "threshold": cell(v.threshold, 4),
            "status": v.status,
            "reason": v.reason,
        })

    verdict_row = {
        "sku": report.impact.sku,
        "proposed_by": report.impact.proposed_by,
        "q_current": round(report.impact.current.order_quantity, 1),
        "sl_current": report.impact.current.service_level,
        "q_proposed": round(report.impact.proposed.order_quantity, 1),
        "sl_proposed": report.impact.proposed.service_level,
        "n_objections": report.n_objections,
        "n_unevaluable": report.n_unevaluable,
        "verdict": report.verdict,
        "judge_cost_current": round(report.impact.judge_cost_current, 2),
        "judge_cost_proposed": round(report.impact.judge_cost_proposed, 2),
    }

    return {
        "csv": write_summary_csv(rows, d / "cross_impact.csv"),
        "verdict_csv": write_summary_csv([verdict_row], d / "cross_impact_verdict.csv"),
    }


def build_deck(report: CrossImpactReport, *, client: str = "Client",
               citations: tuple[str, ...] = (), confidence: float | None = None,
               lang: str = "es") -> Deliverable:
    """The premium deck. ``lang`` is passed explicitly - the PR #194 lesson."""
    findings = [
        Finding(f"{v.hat_key}: {v.status}", v.reason,
                impact=("sin medir" if v.deterioration is None
                        else f"{v.deterioration:+,.4f} {v.kpi_name}"))
        for v in report.verdicts
    ]
    findings.append(Finding(
        "Alcance declarado",
        f"Un SKU, el espacio (Q, SL) y {len(HAT_KEYS)} areas, de las cuales "
        f"{len(report.verdicts) - report.n_unevaluable} se pudieron juzgar. Almacen NO "
        "es uno de los sombreros: su impacto no fue medido.",
        impact="pedir la capacidad fisica antes de mover cantidades"))

    kpis = [
        Kpi("Veredicto de coordinacion", report.verdict,
            rationale="cero objeciones = la reunion no se hace"),
        Kpi("Areas que objetan", str(report.n_objections), target="0"),
        Kpi("Areas sin veredicto", str(report.n_unevaluable), target="0",
            rationale="sin umbral declarado o sin insumos para medirla; cada una "
                      "impide afirmar que nadie objeta"),
    ]

    return Deliverable(
        title=f"Impacto cruzado - {report.impact.sku}",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        recommendations=(
            "Los umbrales son POLITICA DEL OPERADOR, no un consenso objetivo: revisalos "
            "con cada area antes de tratarlos como un veto.",
            "Declarar el umbral de las areas faltantes convierte un 'indeterminado' en un "
            "veredicto accionable.",
        ),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        lang=lang,
    )
