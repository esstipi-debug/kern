"""Launch Readiness agent job (Kern tool #41): campaign launch dates x real lead
time x projected stock coverage -> green/yellow/red verdict per SKU.

Reads two CSVs with pandas directly (deliberately NOT via jobs/intake.py): a
campaign calendar (product_id, launch_date, optional lift/discount) and an
inventory/lead-time file (product_id, on_hand, daily_demand, lead_time_days,
optional demand_std/lead_time_std). Shapes demand by the campaign lift
(src.sop_engine.demand_plan.price_cut_lift_ratio), folds lead-time variability
into demand-during-lead-time (src.risk_period.demand_over_risk_period), projects
coverage vs the launch date (src.safety_stock.safety_stock), and emits a
protected GuidedOutcome per SKU (EXECUTED green / OPTIONS yellow / ESCALATED red).

SCOPE: this tool does NOT integrate with any marketing tool. There is no Slack /
email / marketing-CRM connector anywhere in Kern. The output is a report/handoff
a human forwards; do not describe it as "communicating with marketing".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from nosilo_core.escalation import OPERATIONAL, escalate
from nosilo_core.guided import EXECUTED, ExecutionOption, GuidedOutcome, as_executed, as_options, verify_guided
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.risk_period import demand_over_risk_period
from src.safety_stock import safety_stock
from src.sop_engine.demand_plan import price_cut_lift_ratio

VERDICT_GREEN = "green"
VERDICT_YELLOW = "yellow"
VERDICT_RED = "red"
_VERDICTS = (VERDICT_GREEN, VERDICT_YELLOW, VERDICT_RED)

_LIFT_FLOOR = -1.0
_MAX_SANE_LIFT = 5.0            # expected_lift_pct is a FRACTION; > 5 (=500%) is almost surely a percent typo
_DEFAULT_SERVICE_LEVEL = 0.95
_ROUTE = "marketing campaign owner"
_SLA = "before the campaign go/no-go"

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_LAUNCH_COLS = ("launch_date", "launch", "campaign_date", "go_live", "start_date")
_LIFT_COLS = ("expected_lift_pct", "lift_pct", "expected_lift", "lift")
_CURPRICE_COLS = ("current_price", "price", "list_price", "base_price")
_PROPPRICE_COLS = ("proposed_price", "promo_price", "launch_price", "discount_price")
_ELAST_COLS = ("elasticity", "price_elasticity", "elast")
_ONHAND_COLS = ("on_hand", "quantity", "qty", "stock", "units", "On Hand")
_DEMAND_COLS = ("daily_demand", "demand", "demand_rate", "daily_sales", "run_rate")
_LEAD_COLS = ("lead_time_days", "lead_time", "leadtime", "lead")
_DEMANDSTD_COLS = ("demand_std", "demand_sigma", "std_demand", "sigma_d")
_LEADSTD_COLS = ("lead_time_std", "lead_std", "sigma_lead", "sigma_l")
#: Lo ya comprado que todavia no llego. Mismo vocabulario que `jobs.intake.stock_position`,
#: para que el cliente no tenga que renombrar una columna segun que tool la lea.
_ONORDER_COLS = ("on_order", "en_transito", "en transito", "por_recibir", "oc_pendiente",
                 "open_po", "incoming", "on order", "On Order")
#: CUANDO llega. Sin esto el en-transito no se puede contar contra una FECHA de
#: lanzamiento, que es lo unico que esta pregunta necesita saber.
_ETA_COLS = ("on_order_eta", "eta", "expected_arrival", "arrival_date", "fecha_llegada",
             "fecha_recepcion", "expected_date", "ETA")


@dataclass(frozen=True)
class LaunchInput:
    """One campaign SKU joined to its inventory row (has_coverage=False => no inventory row)."""

    product_id: str
    launch_date: date
    lift_pct: float                 # resolved & floored at -1.0
    has_coverage: bool
    on_hand: float = 0.0
    daily_demand: float = 0.0
    lead_time_days: float = 0.0
    demand_std: float = 0.0
    lead_time_std: float = 0.0
    #: Lo ya comprado que no llego, o ``None`` si el archivo NO LO TRAE. Nunca 0,0 por
    #: omision: un cero diria "medido, y no viene nada".
    on_order: float | None = None
    #: Cuando llega eso. ``None`` = no se sabe, y entonces NO se cuenta -- pero se
    #: nombra. Es la diferencia con el resto de los emisores: contra una FECHA de
    #: lanzamiento, una cantidad sin fecha no puede afirmar que llega a tiempo.
    on_order_eta: date | None = None


@dataclass(frozen=True)
class LaunchLine:
    """One SKU's readiness verdict + the protected outcome behind it."""

    product_id: str
    launch_date: str                # ISO string for CSV/deck
    verdict: str                    # green | yellow | red
    lift_pct: float
    shaped_daily_demand: float
    days_until_launch: float
    lead_time_days: float | None    # None for the no-coverage case
    days_of_cover: float | None
    reorder_point: float | None
    exposure_gap_days: float | None
    on_hand: float | None
    #: Lo que el archivo declaro en camino (``None`` = no lo trae).
    on_order: float | None
    #: De eso, lo que llega A TIEMPO (ETA <= fecha de lanzamiento). ``None`` cuando hay
    #: cantidad pero no fecha: no se puede afirmar ni negar que llegue.
    on_order_before_launch: float | None
    #: ``on_hand`` mas lo que llega a tiempo. Es contra esto que se decide si hace falta
    #: una orden NUEVA.
    position_at_launch: float | None
    outcome: GuidedOutcome
    reason: str


@dataclass(frozen=True)
class LaunchReadinessReport:
    lines: tuple[LaunchLine, ...]
    n_green: int
    n_yellow: int
    n_red: int
    worst_exposure_gap: tuple[str, float]   # (product_id, days); ("n/a", 0.0) if none
    summary: str


# -- Ingestion ----------------------------------------------------------------


def _pick(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _en_camino(fila, col) -> float | None:
    """El en-transito de esta fila, o ``None`` si la columna no esta o esta vacia.

    Un negativo se descarta como ausente: no existe un en-transito negativo, y dejarlo
    pasar bajaria la posicion al lanzamiento y volveria a pintar de rojo lo que ya esta
    comprado -- la direccion contraria a la de este arreglo.
    """
    if not col or not pd.notna(fila[col]):
        return None
    try:
        valor = float(fila[col])
    except (TypeError, ValueError):
        return None
    return valor if valor >= 0 else None


def _eta(fila, col) -> date | None:
    """La fecha de llegada, o ``None`` si no esta o no se puede leer. Una fecha ilegible
    se trata como ausente -- y entonces el en-transito NO se cuenta -- en vez de
    adivinarla."""
    if not col or not pd.notna(fila[col]):
        return None
    try:
        return _parse_date(fila[col])
    except (ValueError, TypeError):
        return None


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def prepare_records(campaigns: pd.DataFrame, inventory: pd.DataFrame,
                    params: dict | None = None) -> dict:
    """Sniff both files, left-join campaigns->inventory on product_id, resolve lift, bake config."""
    params = params or {}
    c_prod = _pick(campaigns, params.get("product_col"), _PRODUCT_COLS)
    c_launch = _pick(campaigns, params.get("launch_col"), _LAUNCH_COLS)
    missing = [n for n, c in (("product_id", c_prod), ("launch_date", c_launch)) if c is None]
    if missing:
        raise ValueError(
            f"campanas.csv: could not find {', '.join(missing)} "
            f"(columns seen: {list(campaigns.columns)[:10]})")
    c_lift = _pick(campaigns, params.get("lift_col"), _LIFT_COLS)
    c_cur = _pick(campaigns, params.get("current_price_col"), _CURPRICE_COLS)
    c_prop = _pick(campaigns, params.get("proposed_price_col"), _PROPPRICE_COLS)
    c_el = _pick(campaigns, params.get("elasticity_col"), _ELAST_COLS)

    i_prod = _pick(inventory, params.get("product_col"), _PRODUCT_COLS)
    i_on = _pick(inventory, params.get("on_hand_col"), _ONHAND_COLS)
    i_dem = _pick(inventory, params.get("demand_col"), _DEMAND_COLS)
    i_lead = _pick(inventory, params.get("lead_col"), _LEAD_COLS)
    inv_missing = [n for n, c in (("product_id", i_prod), ("on_hand", i_on),
                                  ("daily_demand", i_dem), ("lead_time_days", i_lead)) if c is None]
    if inv_missing:
        raise ValueError(
            f"inventory csv: could not find {', '.join(inv_missing)} "
            f"(columns seen: {list(inventory.columns)[:10]})")
    i_dstd = _pick(inventory, params.get("demand_std_col"), _DEMANDSTD_COLS)
    i_lstd = _pick(inventory, params.get("lead_std_col"), _LEADSTD_COLS)
    # Opcionales las dos, y no se rellenan: sin columna el tool decide igual que antes.
    i_oo = _pick(inventory, params.get("on_order_col"), _ONORDER_COLS)
    i_eta = _pick(inventory, params.get("on_order_eta_col"), _ETA_COLS)

    inv_by_id = {str(r[i_prod]): r for _, r in inventory.iterrows()}
    records: list[LaunchInput] = []
    bad_lift: list[str] = []
    for _, row in campaigns.iterrows():
        pid = str(row[c_prod])
        launch = _parse_date(row[c_launch])
        lift = 0.0
        if c_lift and pd.notna(row[c_lift]):
            raw = float(row[c_lift])
            if raw > _MAX_SANE_LIFT:
                bad_lift.append(f"{pid} ({raw})")
                continue
            lift = max(_LIFT_FLOOR, raw)
        elif c_cur and c_prop and c_el and all(pd.notna(row[c]) for c in (c_cur, c_prop, c_el)):
            try:
                lift = max(_LIFT_FLOOR,
                           price_cut_lift_ratio(float(row[c_cur]), float(row[c_prop]), float(row[c_el])))
            except ValueError:
                lift = 0.0
        inv = inv_by_id.get(pid)
        if inv is None:
            records.append(LaunchInput(product_id=pid, launch_date=launch, lift_pct=lift, has_coverage=False))
        else:
            records.append(LaunchInput(
                product_id=pid, launch_date=launch, lift_pct=lift, has_coverage=True,
                on_hand=float(inv[i_on]), daily_demand=float(inv[i_dem]), lead_time_days=float(inv[i_lead]),
                demand_std=float(inv[i_dstd]) if i_dstd and pd.notna(inv[i_dstd]) else 0.0,
                lead_time_std=float(inv[i_lstd]) if i_lstd and pd.notna(inv[i_lstd]) else 0.0,
                # `None`, no 0.0: la ausencia de columna y "no viene nada" son distintas.
                on_order=_en_camino(inv, i_oo),
                on_order_eta=_eta(inv, i_eta)))
    if bad_lift:
        raise ValueError(
            f"expected_lift_pct must be a fraction (0.20 = +20%); suspicious value(s) > "
            f"{_MAX_SANE_LIFT}: {', '.join(bad_lift)}")
    if not records:
        raise ValueError("no campaign rows found")

    as_of_raw = params.get("as_of_date")
    as_of = _parse_date(as_of_raw) if as_of_raw else date.today()
    return {
        "records": records,
        "target_service_level": float(params.get("target_service_level", _DEFAULT_SERVICE_LEVEL)),
        "as_of_date": as_of,
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the campaign CSV (data_path) and the inventory CSV (params['inventory_path'])."""
    params = params or {}
    inv_path = params.get("inventory_path")
    if not inv_path:
        raise ValueError("params['inventory_path'] (the inventory/lead-time CSV) is required")
    return prepare_records(pd.read_csv(data_path), pd.read_csv(inv_path), params)


# -- Per-SKU verdict engine ---------------------------------------------------


def _missing_data_line(inp: LaunchInput, days_until: float) -> LaunchLine:
    reason = "no coverage data for this SKU - cannot assess launch readiness."
    options = [
        ExecutionOption(
            label="Supply coverage data and re-run", score=2.0, recommended=True,
            summary="add the on-hand + real lead-time row for this SKU and re-run launch readiness.",
            action="provide the inventory row (on_hand, daily_demand, lead_time_days) for this SKU",
            tradeoffs="one data step; unblocks a real verdict"),
        ExecutionOption(
            label="Hold the launch decision", score=1.0,
            summary="hold this SKU's go/no-go until coverage data exists.",
            action="defer the launch decision for this SKU",
            tradeoffs="no launch risk taken blind; delays the decision"),
    ]
    outcome = escalate(f"{inp.product_id}: {reason}", OPERATIONAL, reason,
                       route_to=_ROUTE, options=options, sla=_SLA, confidence=0.5)
    return LaunchLine(
        product_id=inp.product_id, launch_date=inp.launch_date.isoformat(), verdict=VERDICT_RED,
        lift_pct=inp.lift_pct, shaped_daily_demand=0.0, days_until_launch=days_until,
        lead_time_days=None, days_of_cover=None, reorder_point=None, exposure_gap_days=None,
        on_hand=None, on_order=None, on_order_before_launch=None, position_at_launch=None,
        outcome=outcome, reason=reason)


def _degenerate_line(inp: LaunchInput, shaped: float, days_until: float) -> LaunchLine:
    reason = (f"campaign lift ({inp.lift_pct:+.0%}) wipes out demand (shaped <= 0) - data error, "
              "cannot assess coverage.")
    options = [
        ExecutionOption(
            label="Fix the campaign lift input and re-run", score=2.0, recommended=True,
            summary="the resolved lift drives projected demand to zero or below; correct it and re-run.",
            action="correct expected_lift_pct / discount inputs for this SKU",
            tradeoffs="one data fix; unblocks a real verdict"),
        ExecutionOption(
            label="Hold the launch decision", score=1.0,
            summary="hold this SKU's go/no-go until the lift input is fixed.",
            action="defer the launch decision for this SKU",
            tradeoffs="no launch risk taken blind; delays the decision"),
    ]
    outcome = escalate(f"{inp.product_id}: {reason}", OPERATIONAL, reason,
                       route_to=_ROUTE, options=options, sla=_SLA, confidence=0.4)
    return LaunchLine(
        product_id=inp.product_id, launch_date=inp.launch_date.isoformat(), verdict=VERDICT_RED,
        lift_pct=inp.lift_pct, shaped_daily_demand=shaped, days_until_launch=days_until,
        lead_time_days=inp.lead_time_days, days_of_cover=None, reorder_point=None,
        exposure_gap_days=None, on_hand=inp.on_hand, on_order=inp.on_order,
        on_order_before_launch=None, position_at_launch=None, outcome=outcome, reason=reason)


def _llega_antes_del_lanzamiento(inp: LaunchInput) -> float | None:
    """Del en-transito, lo que aterriza A TIEMPO. ``None`` = no se puede afirmar.

    Es la diferencia entre este tool y los otros trece emisores del barrido. Los demas
    netean una cantidad contra una posicion y les alcanza con ``on_order``; aca la
    pregunta es contra una FECHA, y una cantidad sin fecha de llegada no puede decir si
    sirve para el lanzamiento. Entonces:

    - con ETA <= fecha de lanzamiento -> cuenta;
    - con ETA posterior -> NO cuenta (llega tarde, y eso es una respuesta, no una duda);
    - con cantidad y SIN ETA -> ``None``: no se cuenta y **se nombra**. Contarla seria
      inventar una fecha; callarla seria el mismo defecto que este arreglo cierra, con
      el signo cambiado.
    """
    if not inp.on_order or inp.on_order <= 0:
        return None
    if inp.on_order_eta is None:
        return None
    return inp.on_order if inp.on_order_eta <= inp.launch_date else 0.0


def _nota_en_camino(inp: LaunchInput, a_tiempo: float | None) -> str:
    """La frase que impide que el veredicto se lea como si el en-transito no existiera."""
    if not inp.on_order or inp.on_order <= 0:
        return ""
    if a_tiempo is None:
        return (f" NOTE: {inp.on_order:.0f} unit(s) already on order with NO arrival date - "
                "not counted; add an ETA column to have them count.")
    if a_tiempo <= 0:
        return (f" NOTE: {inp.on_order:.0f} unit(s) on order arrive "
                f"{inp.on_order_eta.isoformat()}, AFTER the launch - not counted.")
    return ""


def _assess_sku(inp: LaunchInput, *, service_level: float, as_of: date) -> LaunchLine:
    days_until = float((inp.launch_date - as_of).days)
    if not inp.has_coverage:
        return _missing_data_line(inp, days_until)

    shaped = inp.daily_demand * (1.0 + inp.lift_pct)
    if shaped <= 0:
        return _degenerate_line(inp, shaped, days_until)

    risk = demand_over_risk_period(shaped, inp.demand_std, inp.lead_time_days, inp.lead_time_std)
    # risk.demand_std is ALREADY aggregated over the risk period -> risk_periods MUST be 1.0.
    ss = safety_stock(demand_std_per_period=risk.demand_std,
                      cycle_service_level=service_level, risk_periods=1.0).safety_stock
    reorder_point = risk.mean_demand + ss
    # La COBERTURA se mide sobre el estante -- son los dias que faltan para quedar
    # fisicamente vacio, y una OC que todavia no llego no alimenta el mostrador hoy.
    days_of_cover = inp.on_hand / shaped
    exposure_gap = max(0.0, inp.lead_time_days - days_until)

    # El VEREDICTO, en cambio, pregunta otra cosa: "¿llego listo al lanzamiento?". Ahi
    # lo que ya esta comprado y aterriza ANTES de esa fecha si cuenta -- no hace falta
    # una orden nueva para traerlo. Sin ETA no se puede afirmar que llegue a tiempo,
    # asi que no se cuenta; pero tampoco se calla (ver `_nota_en_camino`).
    en_camino_a_tiempo = _llega_antes_del_lanzamiento(inp)
    position = inp.on_hand + (en_camino_a_tiempo or 0.0)
    nota = _nota_en_camino(inp, en_camino_a_tiempo)

    common = dict(
        product_id=inp.product_id, launch_date=inp.launch_date.isoformat(), lift_pct=inp.lift_pct,
        shaped_daily_demand=shaped, days_until_launch=days_until, lead_time_days=inp.lead_time_days,
        days_of_cover=days_of_cover, reorder_point=reorder_point, exposure_gap_days=exposure_gap,
        on_hand=inp.on_hand, on_order=inp.on_order,
        on_order_before_launch=en_camino_a_tiempo, position_at_launch=position)

    cover_at_launch = position / shaped
    if cover_at_launch >= days_until:
        if en_camino_a_tiempo:
            reason = (f"on-hand {inp.on_hand:.0f} plus {en_camino_a_tiempo:.0f} already on order "
                      f"landing before launch cover {cover_at_launch:.0f} day(s) >= "
                      f"{days_until:.0f} to launch; ready without a NEW reorder.{nota}")
        else:
            reason = (f"on-hand covers {days_of_cover:.0f} day(s) >= {days_until:.0f} to launch; "
                      f"ready without a reorder.{nota}")
        return LaunchLine(**common, verdict=VERDICT_GREEN, reason=reason,
                          outcome=as_executed(f"{inp.product_id}: launch-ready. {reason}", confidence=0.9))

    if exposure_gap > 0:
        reason = (f"lead time {inp.lead_time_days:.0f}d exceeds {days_until:.0f}d to launch by "
                  f"{exposure_gap:.0f}d - a reorder cannot land in time.{nota}")
        options = [
            ExecutionOption(
                label=f"Delay the launch by ~{exposure_gap:.0f} day(s)", score=2.0, recommended=True,
                summary="push the launch date so a standard replenishment can arrive.",
                action=f"move the launch out by >= {exposure_gap:.0f} day(s)",
                tradeoffs="protects day-one availability; slips the campaign date"),
            ExecutionOption(
                label="Launch with limited allocation", score=1.0,
                summary=f"launch only where the {inp.on_hand:.0f} on-hand can serve (limited channels/stores).",
                action="restrict the launch to the channels current on-hand covers",
                tradeoffs="keeps the date; narrower launch footprint"),
        ]
        outcome = escalate(f"{inp.product_id}: launch at risk. {reason}", OPERATIONAL, reason,
                           route_to=_ROUTE, options=options, sla=_SLA, confidence=0.7)
        return LaunchLine(**common, verdict=VERDICT_RED, outcome=outcome, reason=reason)

    order_now = ExecutionOption(
        label="Place the replenishment order now", score=2.0,
        summary=f"a reorder ({inp.lead_time_days:.0f}d) lands before launch; order to the "
                f"{reorder_point:.0f} reorder point.",
        action="place the replenishment order now", tradeoffs="covers the launch; commits the spend")
    limited = ExecutionOption(
        label="Launch with limited allocation", score=1.0,
        summary=f"on-hand {inp.on_hand:.0f} is below the {reorder_point:.0f} reorder point - launch "
                "narrow while stock rebuilds.",
        action="restrict the launch to the channels current on-hand covers",
        tradeoffs="lower spend now; narrower launch footprint")
    if inp.on_hand >= reorder_point:
        items, conf = [replace(order_now, recommended=True), limited], 0.8
    else:
        items, conf = [replace(limited, recommended=True), order_now], 0.6
    reason = (f"on-hand covers {days_of_cover:.0f}d < {days_until:.0f}d to launch, but lead time "
              f"{inp.lead_time_days:.0f}d fits - a reorder can arrive in time.{nota}")
    outcome = as_options(f"{inp.product_id}: orderable before launch. {reason}", items, confidence=conf)
    return LaunchLine(**common, verdict=VERDICT_YELLOW, outcome=outcome, reason=reason)


def run(payload: dict) -> LaunchReadinessReport:
    """Assess every SKU in the payload (sorted by product_id for a deterministic report)."""
    service_level = float(payload.get("target_service_level", _DEFAULT_SERVICE_LEVEL))
    as_of = payload["as_of_date"]
    lines = tuple(sorted(
        (_assess_sku(i, service_level=service_level, as_of=as_of) for i in payload["records"]),
        key=lambda line: line.product_id))
    n_green = sum(1 for line in lines if line.verdict == VERDICT_GREEN)
    n_yellow = sum(1 for line in lines if line.verdict == VERDICT_YELLOW)
    n_red = sum(1 for line in lines if line.verdict == VERDICT_RED)
    gaps = [(line.product_id, line.exposure_gap_days) for line in lines if line.exposure_gap_days]
    worst = max(gaps, key=lambda t: t[1], default=("n/a", 0.0))
    summary = (f"Launch readiness over {len(lines)} SKU(s): {n_green} green, {n_yellow} yellow, "
               f"{n_red} red.")
    return LaunchReadinessReport(lines=lines, n_green=n_green, n_yellow=n_yellow, n_red=n_red,
                                 worst_exposure_gap=worst, summary=summary)


def verify(report: LaunchReadinessReport) -> list[str]:
    """QA gate. Empty list = passed. Every line's outcome honours the never-unprotected
    contract; counts sum; verdict is enumerated; reason is present; a red line is never
    EXECUTED and always carries >= 2 escalation options (the builders don't enforce that)."""
    issues: list[str] = []
    if not report.lines:
        issues.append("no SKUs to assess")
    if report.n_green + report.n_yellow + report.n_red != len(report.lines):
        issues.append("verdict counts do not sum to the line count")
    for line in report.lines:
        issues.extend(f"{line.product_id}: {m}" for m in verify_guided(line.outcome))
        if line.verdict not in _VERDICTS:
            issues.append(f"{line.product_id}: invalid verdict {line.verdict!r}")
        if not line.reason.strip():
            issues.append(f"{line.product_id}: line has no reason")
        if not math.isfinite(line.days_until_launch):
            issues.append(f"{line.product_id}: non-finite days_until_launch")
        if line.verdict == VERDICT_RED:
            if line.outcome.status == EXECUTED:
                issues.append(f"{line.product_id}: red line reports EXECUTED")
            n_opts = len(line.outcome.escalation.options) if line.outcome.escalation else 0
            if n_opts < 2:
                issues.append(f"{line.product_id}: red line has {n_opts} option(s), need >= 2")
    return issues


# -- Deliverables -------------------------------------------------------------


def write_operational(report: LaunchReadinessReport, out_dir, client: str = "Client") -> dict[str, Path]:
    """One row per SKU: verdict, timing, coverage, and the recommended action. N/A for missing fields."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    def fmt(v):
        return "N/A" if v is None else round(v, 1)

    rows = [
        {
            "product_id": line.product_id,
            "launch_date": line.launch_date,
            "verdict": line.verdict,
            "days_until_launch": round(line.days_until_launch, 1),
            "lead_time_days": fmt(line.lead_time_days),
            "days_of_cover": fmt(line.days_of_cover),
            "on_order": fmt(line.on_order),
            "on_order_before_launch": fmt(line.on_order_before_launch),
            "position_at_launch": fmt(line.position_at_launch),
            "reorder_point": fmt(line.reorder_point),
            "exposure_gap_days": fmt(line.exposure_gap_days),
            "recommended_action": line.reason,
        }
        for line in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "launch_readiness.csv")}


def build_deck(report: LaunchReadinessReport, *, client: str = "Client", prepared: str = "",
               citations: tuple[str, ...] = (), confidence: float = 0.8) -> Deliverable:
    """Compose the launch-readiness study: which SKUs are ready, orderable, or at risk."""
    worst_id, worst_gap = report.worst_exposure_gap
    summary = (f"Launch readiness over {len(report.lines)} SKU(s): {report.n_green} green, "
               f"{report.n_yellow} yellow, {report.n_red} red.")
    findings = [
        Finding("Red - not ready for launch",
                f"{report.n_red} SKU(s) cannot be available for their launch date as planned.",
                impact="route to the marketing campaign owner before the go/no-go"),
        Finding("Yellow - orderable in time",
                f"{report.n_yellow} SKU(s) need a reorder or a limited launch to make the date.",
                impact="place the replenishment now or launch narrow"),
    ]
    if worst_gap > 0:
        findings.append(Finding(
            f"Worst lead-time exposure: {worst_id}",
            f"a standard reorder lands {worst_gap:.0f} day(s) after launch.",
            impact="the single biggest date risk - address this first"))
    kpis = (
        Kpi("SKUs", f"{len(report.lines)}", rationale="Campaign SKUs assessed"),
        Kpi("Green (ready)", f"{report.n_green}", target="maximize", rationale="On-hand covers to launch"),
        Kpi("Yellow (orderable)", f"{report.n_yellow}", target="minimize",
            rationale="Needs a reorder or a limited launch"),
        Kpi("Red (at risk)", f"{report.n_red}", target="0", rationale="Cannot be ready as planned"),
        Kpi("Worst exposure gap", f"{worst_id}: {worst_gap:.0f}d", target="0",
            rationale="Largest lead-time-vs-launch shortfall"),
    )
    data_sources = (
        DataSource("Campaign launch dates + expected lift", "marketing calendar", "per campaign"),
        DataSource("On-hand, baseline demand, real lead time", "WMS / ERP + supplier records", "weekly"),
    )
    recommendations = (
        "Route every red SKU to the marketing campaign owner before the go/no-go.",
        "Place the recommended reorders for the yellow SKUs now, or launch them with limited allocation.",
        "Re-run once launch dates or lead times change; the verdict can flip.",
    )
    return Deliverable(
        title="Launch Readiness", client=client, summary=summary, findings=tuple(findings),
        kpis=kpis, data_sources=data_sources, recommendations=recommendations,
        citations=tuple(citations), confidence=confidence,
        residual="This is a report a human forwards - Kern does NOT communicate with any marketing "
                 "tool (no Slack / email / CRM connector exists). Confirm launch dates and lead times, "
                 "and route red SKUs to whoever controls the campaign calendar.",
        prepared=prepared)
