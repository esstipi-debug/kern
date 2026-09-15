"""Demand-forecasting agent job: a demand-history CSV -> forecastability-aware forecasts.

The data-prep + deck + guided-options half of the forecast tool. Reads a long-format demand
history (sku, period, quantity) with pandas directly (deliberately *not* via jobs/intake.py,
which the parallel loop owns), then for each SKU:

  1. cleans the series (missing -> 0, negatives floored),
  2. segments it by forecastability (Syntetos-Boylan ADI x CV^2 -> smooth / erratic /
     intermittent / lumpy) via ``src.forecastability``,
  3. auto-selects the matching method (AutoETS/TSB via StatsForecast when installed,
     else SES/Croston) and backtests it on a holdout,
  4. quantifies **Forecast Value-Add** vs a naive baseline (MASE < 1 == beats naive),

and emits a protected ``GuidedOutcome`` with **ranked forecasting-policy options**
(auto-per-segment / global SES / review-the-lumpy) so the tool offers *choices to act*.

Mirrors jobs/whatif_job.py (prepare/sniff) and jobs/returns_job.py (guided options) style.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs import forecast_cycle
from jobs.forecast_cycle import CycleMemory, mean_finite
from nosilo_core.guided import GuidedOutcome, Residual, recommend, verify_guided
from src.decision_options import Objective, Scenario, decide
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.forecast_metrics import compute_metrics
from src.forecastability import segment
from src.forecasting import forecast_demand
from src.outliers import OutlierReport, scan_outliers
from src.seasonality import detect_season_length
from src.state.store import StateStore

_PRODUCT_COLS = ("sku", "product_id", "product", "item", "SKU", "Product", "material")
_QTY_COLS = ("demand", "quantity", "qty", "units", "sales", "Demand", "Quantity", "value")
_PERIOD_COLS = ("period", "date", "week", "month", "Period", "Date", "t")


@dataclass(frozen=True)
class SkuForecast:
    """One SKU's forecastability class, chosen method, next-period forecast and value-add."""

    name: str
    quadrant: str
    adi: float
    cv2: float
    method: str
    forecast: float
    mae: float
    mape: float
    mase: float
    fva: float            # 1 - MASE; > 0 means the method beats the naive baseline
    beats_naive: bool
    n_periods: int
    #: Periodos del ciclo detectado en ESTA serie; 1 = sin estacion, naive lag-1.
    #: Viaja al reporte porque un MASE no se puede auditar sin saber contra que
    #: naive se escalo.
    season_length: int = 1
    #: `scaled` (MASE/RMSSE son numeros reales) | `exact` (el naive es perfecto
    #: aca, asi que NO ganarle es un veredicto, no un hueco) | `unavailable` (no
    #: hubo benchmark medible: ningun veredicto). Las tres dan MASE infinito en
    #: dos casos, y decir "no pudimos validar" cuando la verdad es "el naive ya
    #: gana" es otra afirmacion, y la equivocada.
    benchmark_state: str = "unavailable"
    #: Periodos marcados como excepcionales (promocion, pedido grande, liquidacion,
    #: error de tipeo). Se REPORTAN, nunca se limpian a espaldas del llamador: una
    #: serie corregida en silencio es un pronostico que nadie puede auditar.
    n_outliers: int = 0
    outlier_periods: tuple[int, ...] = ()
    #: Signed forecast error over the fitted history: positive = the method runs HIGH.
    #: MAPE says how wrong, this says in which direction, which is the half a planner
    #: acts on (Vandeput 2021, sec 2.2) and the half the job used to discard.
    bias: float = float("nan")
    #: Dispersion of the forecast, and which one it is (see ForecastResult.sigma_source):
    #: forecast_error | demand_std_fallback | unavailable. Safety stock cannot be sized
    #: from a point, so shipping the point alone was never enough.
    error_std: float = float("nan")
    sigma_source: str = "unavailable"
    #: 80% band around the point (P10 floored at zero: negative demand is not a scenario).
    #: ``None`` when no dispersion was estimable - never a fabricated zero-width band.
    p10: float | None = None
    p90: float | None = None
    #: This SKU's MAPE in the previous frozen cycle, and the change since (negative
    #: = the forecast improved). ``None`` when the SKU was not in that cycle - a
    #: new SKU has no track record, and calling that 0.0 would invent one.
    previous_mape: float | None = None
    mape_change_vs_previous: float | None = None


@dataclass(frozen=True)
class ForecastJobReport:
    """Portfolio forecast: per-SKU results (hardest-first), the mix and the policy options."""

    skus: tuple[SkuForecast, ...]
    mix: dict
    hardest: tuple[str, float]
    n_skus: int
    n_beating_naive: int
    mean_mase: float
    #: Portfolio-level accuracy: mean MAPE and mean SIGNED bias over the SKUs that
    #: could be backtested. MASE alone answers "better than naive?"; a planner is
    #: also asked "how far off, and in which direction" - that is these two.
    mean_mape: float
    mean_bias: float
    outcome: GuidedOutcome
    summary: str
    #: Cycle memory (see jobs/forecast_cycle.py). Flat scalars, and ``None`` on the
    #: first run rather than zeros - "no previous cycle" and "no change since the
    #: previous cycle" are different statements and must not print the same.
    cycle_id: str | None = None
    previous_cycle_id: str | None = None
    previous_mean_mape: float | None = None
    previous_mean_bias: float | None = None
    mean_mape_vs_previous: float | None = None
    mean_bias_vs_previous: float | None = None
    #: WHY there is no previous cycle, when there is none, in four words: "first
    #: cycle", "previous cycle unreadable", "no store: cycle memory off". Empty
    #: when one was read. A bare ``None`` on the five fields above prints all
    #: three the same, and only one of them is the client's fault to fix.
    no_baseline_reason: str = ""
    cycle_comparison: str = ""


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict[str, list[float]]:
    """Sniff the demand columns and build one cleaned, period-ordered series per SKU.

    Required columns: product and quantity (raises ValueError listing any missing). A period
    column, when present, orders each SKU's series. Missing demand -> 0; negatives floored.
    """
    params = params or {}
    product_col = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    qty_col = _pick_column(df, params.get("qty_col"), _QTY_COLS)
    missing = [n for n, c in (("product", product_col), ("quantity", qty_col)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})"
        )
    period_col = _pick_column(df, params.get("period_col"), _PERIOD_COLS)

    cols = [product_col, qty_col] + ([period_col] if period_col else [])
    work = df[cols].copy()
    work[qty_col] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0.0).clip(lower=0.0)

    series_by_name: dict[str, list[float]] = {}
    for product, group in work.groupby(product_col, sort=False):
        if period_col:
            group = group.sort_values(period_col)
        series_by_name[str(product)] = [float(v) for v in group[qty_col].tolist()]
    if not series_by_name:
        raise ValueError("no demand series found in the data")
    return series_by_name


def prepare(data_path: str, params: dict | None = None) -> dict[str, list[float]]:
    """Read a demand-history CSV and build the cleaned per-SKU series."""
    return prepare_records(pd.read_csv(data_path), params)


def _backtest(series: list[float], method: str, *, holdout_fraction: float, min_periods: int):
    """Hold out the tail, fit on the head, and score the flat forecast (None if too short).

    El naive de referencia sale de la serie, no de una constante: si repite cada m
    periodos, MASE y RMSSE se escalan contra el naive ESTACIONAL. Contra un lag-1,
    un pronostico plano sobre una serie que alterna alto/bajo parece excelente --
    el benchmark esta oscilando de punta a punta todos los periodos, y el FVA que
    sale de ahi es un elogio prestado.
    """
    n = len(series)
    if n < min_periods:
        return None
    h = min(max(1, round(n * holdout_fraction)), n - 2)
    if h < 1:
        return None
    train = series[:-h]
    test = series[-h:]
    fc = forecast_demand(train, method=method).forecast
    return compute_metrics(
        test, [fc] * h, train=train, season_length=detect_season_length(train)
    )


def _policy_outcome(
    *, base_summary: str, beating_share: float, regular_share: float, lumpy_share: float,
    n_unvalidated: int = 0, n_skus: int = 0,
) -> GuidedOutcome:
    """Rank three forecasting policies (accuracy vs ops simplicity vs manual effort).

    ``n_unvalidated`` is the count of SKUs too short to backtest (MASE=inf) - too
    little history to know whether the chosen method actually beats naive. Instead
    of silently shipping the same confident label as a well-backed forecast, this
    states the gap as a residual (never a bare/empty risk) and dampens confidence
    proportionally to how much of the portfolio could not be validated.
    """
    scenarios = [
        Scenario(
            "auto_per_segment",
            "Adopt the SBC-recommended method per SKU (AutoETS for regular, TSB for intermittent when available)",
            {"accuracy": beating_share, "ops_simplicity": 0.5, "manual_effort": 0.15},
            action="apply per-segment auto method selection",
            tradeoffs="best accuracy; one-time setup of per-segment automation",
        ),
        Scenario(
            "global_ses",
            "Run a single smoothing model across every SKU (SES / AutoETS)",
            {"accuracy": regular_share, "ops_simplicity": 1.0, "manual_effort": 0.0},
            action="apply one global smoothing model",
            tradeoffs="simplest to operate; over-forecasts intermittent/lumpy demand",
        ),
        Scenario(
            "review_lumpy",
            "Auto-forecast the forecastable SKUs; route lumpy demand to planner review",
            {"accuracy": min(1.0, beating_share + 0.1), "ops_simplicity": 0.5, "manual_effort": lumpy_share},
            action="auto for smooth/erratic/intermittent; manual review for lumpy",
            tradeoffs="captures the hardest SKUs with human judgement; adds review effort",
        ),
    ]
    objectives = [
        Objective("accuracy", weight=3.0, maximize=True),
        Objective("ops_simplicity", weight=1.0, maximize=True),
        Objective("manual_effort", weight=0.5),  # cost-like -> minimized
    ]
    confidence = 0.8
    residuals: list[Residual] | None = None
    if n_unvalidated > 0:
        unvalidated_share = n_unvalidated / n_skus if n_skus else 0.0
        confidence = round(confidence * (1.0 - 0.5 * unvalidated_share), 2)
        residuals = [Residual(
            description=f"{n_unvalidated} of {n_skus} SKU(s) had too little demand history to "
                        "backtest - their forecast accuracy (MASE) could not be validated.",
            risk_if_skipped="the chosen method for these SKUs is unverified; a poor fit could go "
                            "unnoticed until a stockout or an excess-stock event exposes it.",
        )]
    return decide(base_summary, scenarios, objectives, confidence=confidence, residuals=residuals)


#: z(0.90): half-width of an 80% band (P10-P90) under a normal error.
_Z80 = 1.2815515655446004


def _dispersion(fc) -> float | None:
    """The sigma the band is drawn with, or ``None`` when none was estimable.

    Reuses ``ForecastResult.sigma_source`` instead of re-deciding: the engine and the
    band must never disagree about which dispersion is in play.
    """
    if fc.sigma_source == "forecast_error_oos":
        return float(fc.error_std_oos)
    if fc.sigma_source == "forecast_error":
        return float(fc.error_std)
    if fc.sigma_source == "demand_std_fallback":
        return float(fc.demand_std)
    return None


def _benchmark_state(metrics) -> str:
    """`scaled` | `exact` | `unavailable` -- que pudo decir el naive de referencia."""
    if metrics is None or metrics.naive_obs <= 0:
        return "unavailable"
    if metrics.naive_mae == 0.0 or metrics.naive_rmse == 0.0:
        return "exact"
    return "scaled" if math.isfinite(metrics.mase) else "unavailable"


def _sku_forecast(
    f, series: list[float], memory: CycleMemory,
    *, holdout_fraction: float, min_periods: int,
) -> SkuForecast:
    """One SKU's row: point, band, backtest, and last cycle's error beside today's."""
    _m = detect_season_length(series)
    # Se escanea con el MISMO m que puntua el benchmark: un pico de temporada no es
    # un outlier, es la temporada. Contra una linea base sin fase, diciembre entero
    # saldria marcado todos los anios.
    try:
        _outliers = scan_outliers(series, season_length=_m)
    except ValueError:
        # Historia inutilizable para el escaner (vacia, no finita, negativa). El
        # pronostico ya la rechaza por su cuenta; aca no se inventa un cero.
        _outliers = OutlierReport(cleaned=tuple(series), flagged=(), n_flagged=0)
    fc = forecast_demand(series, method=f.recommended_method)
    point = float(fc.forecast)
    sigma = _dispersion(fc)
    metrics = _backtest(
        series, f.recommended_method,
        holdout_fraction=holdout_fraction, min_periods=min_periods,
    )
    if metrics is not None and math.isfinite(metrics.mase):
        mase, mape, mae = metrics.mase, metrics.mape, metrics.mae
        fva, beats = 1.0 - mase, mase < 1.0
    else:
        mase, mape, mae = float("inf"), float("inf"), float("nan")
        fva, beats = 0.0, False
    was = None if memory.previous is None else memory.previous.mape_by_sku.get(f.name)
    return SkuForecast(
        name=f.name, quadrant=f.quadrant, adi=f.adi, cv2=f.cv2, method=f.recommended_method,
        season_length=_m,
        benchmark_state=_benchmark_state(metrics),
        n_outliers=_outliers.n_flagged,
        outlier_periods=_outliers.flagged,
        forecast=point, mae=mae, mape=mape, mase=mase, fva=fva, beats_naive=beats,
        n_periods=f.n_periods,
        bias=float(fc.bias), error_std=float(fc.error_std), sigma_source=fc.sigma_source,
        p10=(max(0.0, point - _Z80 * sigma) if sigma is not None else None),
        p90=(point + _Z80 * sigma if sigma is not None else None),
        previous_mape=was,
        mape_change_vs_previous=(
            None if was is None or not math.isfinite(mape) else mape - was
        ),
    )


def _policy_and_summary(seg, skus: list[SkuForecast]) -> tuple[GuidedOutcome, str]:
    """The ranked-policy outcome plus the one-line summary that names the winner."""
    n = len(skus)
    n_beating = sum(1 for s in skus if s.beats_naive)
    # Un SKU cuyo naive es EXACTO no es un SKU sin validar: es un veredicto ("nada
    # puede ganarle a repetir el ciclo anterior"). Contarlo como hueco de datos le
    # bajaba la confianza al entregable por una medicion que SI se hizo.
    n_validated = sum(1 for s in skus if s.benchmark_state in ("scaled", "exact"))
    base_summary = (
        f"{n} SKUs segmented (smooth {seg.mix['smooth']}, erratic {seg.mix['erratic']}, "
        f"intermittent {seg.mix['intermittent']}, lumpy {seg.mix['lumpy']}); "
        f"{n_beating}/{n} beat the naive baseline."
    )
    outcome = _policy_outcome(
        base_summary=base_summary,
        beating_share=(n_beating / n if n else 0.0),
        regular_share=((seg.mix["smooth"] + seg.mix["erratic"]) / n if n else 0.0),
        lumpy_share=(seg.mix["lumpy"] / n if n else 0.0),
        n_unvalidated=n - n_validated,
        n_skus=n,
    )
    return outcome, f"{base_summary} Recommended policy: {recommend(outcome.options).label}."


def _read_memory(store: StateStore | None) -> CycleMemory:
    """The previous frozen cycle, or the written-down reason there is none."""
    return forecast_cycle.NO_STORE_MEMORY if store is None else forecast_cycle.read_previous(store)


def _freeze(
    store: StateStore | None, cycle_id: str | None, skus: list[SkuForecast]
) -> forecast_cycle.FrozenCycle:
    """Write this cycle down, or say -- in the deliverable -- that nobody asked to."""
    if store is None:
        return forecast_cycle.NO_STORE_FREEZE
    return forecast_cycle.freeze(store, cycle_id or forecast_cycle.now_cycle_id(), skus)


def run(
    series_by_name: dict[str, list[float]],
    *,
    holdout_fraction: float = 0.25,
    min_backtest_periods: int = 4,
    store: StateStore | None = None,
    cycle_id: str | None = None,
) -> ForecastJobReport:
    """Segment, auto-forecast + backtest each SKU, and present ranked forecasting policies.

    Carries the cycle memory too, and it is OPT-IN. With a ``store``, the
    previous frozen cycle is read BEFORE anything is written (read after the
    write and "previous" would be today) and this cycle is frozen on the way out
    so the next run has a baseline. WITHOUT one, this run neither reads nor
    freezes anything and the report says so in words (``no_baseline_reason``,
    ``cycle_comparison``) -- never a zero and never a mute ``None``.

    ``store=None`` used to resolve to ``default_store()``, the shared unnamed
    partition. That made every caller who never named a client share one cycle
    memory, so two datasets in one process compared against each other; see
    ``forecast_cycle.NO_STORE_MEMORY``. Pass ``store=store_for(client_id)`` for
    one client's memory, ``store=default_store()`` to opt into the shared
    partition on purpose, and ``cycle_id`` to keep a run deterministic.
    """
    memory = _read_memory(store)
    seg = segment(series_by_name)
    skus = [
        _sku_forecast(f, series_by_name[f.name], memory,
                      holdout_fraction=holdout_fraction, min_periods=min_backtest_periods)
        for f in seg.items
    ]

    finite_mases = [s.mase for s in skus if math.isfinite(s.mase)]
    mean_mape = mean_finite(s.mape for s in skus)
    mean_bias = mean_finite(s.bias for s in skus)
    outcome, summary = _policy_and_summary(seg, skus)
    frozen = _freeze(store, cycle_id, skus)

    return ForecastJobReport(
        skus=tuple(skus),
        mix=seg.mix,
        hardest=seg.hardest,
        n_skus=len(skus),
        n_beating_naive=sum(1 for s in skus if s.beats_naive),
        mean_mase=(sum(finite_mases) / len(finite_mases) if finite_mases else float("inf")),
        mean_mape=mean_mape,
        mean_bias=mean_bias,
        outcome=outcome,
        summary=summary,
        **forecast_cycle.compare(memory, mean_mape, mean_bias, frozen),
    )


def verify(report: ForecastJobReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + finite forecasts."""
    issues = list(verify_guided(report.outcome))
    if report.n_skus <= 0:
        issues.append("no SKUs to forecast")
    if not all(math.isfinite(s.forecast) for s in report.skus):
        issues.append("a forecast is not finite")
    # A read previous cycle that produced no delta would print a baseline the
    # client cannot act on; an empty comparison line would silently drop the
    # "better or worse" half of the deliverable.
    if report.previous_cycle_id is not None and report.mean_mape_vs_previous is None:
        issues.append("a previous cycle was read but no comparison was computed")
    if not report.cycle_comparison.strip():
        issues.append("the cycle comparison is empty")
    # No previous cycle is three different statements (first one on record, one
    # that would not load, no store given at all) and only one of them is the
    # client's to act on. A mute None for all three is the failure this field exists
    # to prevent, so the gate is on the reason being said, not just on the None.
    if report.previous_cycle_id is None and not report.no_baseline_reason.strip():
        issues.append("there is no previous cycle and the report does not say why")
    return issues


def _fmt(x: float) -> object:
    """Round for the operational CSV, leaving non-finite values blank-friendly."""
    return round(x, 4) if math.isfinite(x) else ""


def _pct(x: float | None) -> str:
    """A rate as a percentage for the deck, or "n/a" when there is nothing to show."""
    return f"{x * 100:.1f}%" if x is not None and math.isfinite(x) else "n/a"


def _delta_pp(x: float | None, no_baseline_reason: str = "first cycle") -> str:
    """A change in a rate, in percentage points, signed. Never a bare 0.0 stand-in
    for "unknown": a missing delta says WHICH kind of missing it is, in words.

    ``no_baseline_reason`` comes from the report rather than being assumed here:
    "first cycle" is a claim about the store's contents, and a run given no store
    never looked at one."""
    if x is None:
        return f"n/a ({no_baseline_reason or 'no previous cycle'})"
    return f"{x * 100:+.1f} pp" if math.isfinite(x) else "n/a"


def write_operational(report: ForecastJobReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per SKU with its class, method and value-add."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "name": s.name,
            "quadrant": s.quadrant,
            "adi": _fmt(s.adi),
            "cv2": round(s.cv2, 4),
            "method": s.method,
            "forecast": round(s.forecast, 2),
            "p10": "" if s.p10 is None else round(s.p10, 2),
            "p90": "" if s.p90 is None else round(s.p90, 2),
            "sigma": _fmt(s.error_std),
            "sigma_source": s.sigma_source,
            "bias": _fmt(s.bias),
            "mape": _fmt(s.mape),
            "previous_mape": "" if s.previous_mape is None else round(s.previous_mape, 4),
            "mape_change_vs_previous": (
                "" if s.mape_change_vs_previous is None else round(s.mape_change_vs_previous, 4)
            ),
            "mase": _fmt(s.mase),
            "fva": round(s.fva, 4),
            "beats_naive": s.beats_naive,
            "n_periods": s.n_periods,
        }
        for s in report.skus
    ]
    return {"csv": write_summary_csv(rows, d / "forecast_register.csv")}


def build_deck(
    report: ForecastJobReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the forecast study: which SKUs are forecastable, by what method, and the value-add."""
    mix = report.mix
    n = report.n_skus
    beat_pct = (report.n_beating_naive / n * 100) if n else 0.0
    hard_name, _ = report.hardest
    mean_mase_txt = f"{report.mean_mase:.2f}" if math.isfinite(report.mean_mase) else "n/a"

    summary = (
        f"Forecast study across {n} SKU(s): {mix['smooth']} smooth / {mix['erratic']} erratic / "
        f"{mix['intermittent']} intermittent / {mix['lumpy']} lumpy. {report.n_beating_naive}/{n} "
        f"({beat_pct:.0f}%) beat the naive baseline (mean MASE {mean_mase_txt})."
    )

    findings = [
        Finding(
            "Forecastability mix (Syntetos-Boylan)",
            f"smooth {mix['smooth']}, erratic {mix['erratic']}, intermittent {mix['intermittent']}, "
            f"lumpy {mix['lumpy']} - timing (ADI) x size variability (CV^2) sets the method per SKU.",
            impact="regular SKUs take SES; intermittent/lumpy take Croston - one global model misfits both",
        ),
        Finding(
            "Forecast value-add vs naive",
            f"{report.n_beating_naive}/{n} SKU(s) beat the naive baseline on a holdout backtest "
            f"(mean MASE {mean_mase_txt}); MASE < 1 means the chosen method adds value.",
            impact="adopt the method only where it beats naive; elsewhere naive is the honest default",
        ),
    ]
    worse = sorted(
        (s for s in report.skus if (s.mape_change_vs_previous or 0.0) > 0.0),
        key=lambda s: s.mape_change_vs_previous, reverse=True,
    )[:3]
    degraded = (
        " SKUs that got worse: "
        + ", ".join(f"{s.name} {_pct(s.previous_mape)} -> {_pct(s.mape)}" for s in worse)
        + "."
    ) if worse else ""
    findings.append(Finding(
        "Forecast accuracy vs the previous cycle",
        f"{report.cycle_comparison}{degraded}",
        impact="a MAPE with no frozen baseline cannot be managed - the delta is what says "
               "whether the plan is getting better or worse",
    ))

    hardest_skus = [s for s in report.skus if s.quadrant == "lumpy"][:3]
    if hardest_skus:
        names = ", ".join(s.name for s in hardest_skus)
        findings.append(Finding(
            "Hardest SKUs (lumpy) need a human in the loop",
            f"{names} are lumpy (irregular timing AND variable size) - statistical methods are weakest here.",
            impact="route lumpy SKUs to planner review or a service-level buffer, not a point forecast",
        ))
    else:
        findings.append(Finding(
            f"Hardest SKU to forecast: {hard_name}",
            "no lumpy SKUs; the hardest series still carries the most timing/size irregularity.",
            impact="watch this SKU's accuracy; it sets the floor on planning confidence",
        ))

    opts = report.outcome.options
    findings.append(Finding(
        "Recommended forecasting policy (choose one)",
        "; ".join(
            f"{i + 1}. {o.label}{' [recommended]' if o.recommended else ''} - {o.tradeoffs}"
            for i, o in enumerate(opts)
        ),
        impact="pick a policy; the recommended one is the best accuracy/effort balance",
    ))

    kpis = (
        Kpi("SKUs forecast", f"{n}", rationale="Series segmented and backtested"),
        Kpi("Beat naive", f"{report.n_beating_naive}/{n} ({beat_pct:.0f}%)", target="maximize",
            rationale="SKUs where the chosen method beats a naive baseline (MASE < 1)"),
        Kpi("Mean MASE", mean_mase_txt, target="minimize",
            rationale="Mean scaled error across backtested SKUs (< 1 is better than naive)"),
        Kpi("Mean MAPE", _pct(report.mean_mape), target="minimize",
            rationale="Mean absolute percentage error across backtested SKUs"),
        Kpi("MAPE vs previous cycle",
            _delta_pp(report.mean_mape_vs_previous, report.no_baseline_reason), target="minimize",
            rationale="Change in mean MAPE since the previous frozen cycle (negative = improving)"),
        Kpi("Intermittent + lumpy", f"{mix['intermittent'] + mix['lumpy']}", target="minimize",
            rationale="SKUs needing Croston / human review rather than smoothing"),
        Kpi("Hardest SKU", hard_name, rationale="Most irregular timing x size (lowest forecastability)"),
    )

    data_sources = (
        DataSource("Demand history (sku / period / quantity)", "ERP / sales history", "weekly"),
        DataSource("Forecastability cut-offs (ADI 1.32 / CV^2 0.49)", "Syntetos-Boylan (2005)", "fixed"),
    )

    recommendations = [
        "Adopt the recommended forecasting policy as the default; re-segment quarterly as demand shifts.",
        "Use sigma_e (forecast-error std), not raw demand std, to size safety stock downstream.",
        "Hand lumpy SKUs to planner review or a service-level policy instead of a point forecast.",
    ]

    return Deliverable(
        title="Demand Forecasting & Forecastability",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),   # ranked policies -> the deck's "Options to act"
        citations=tuple(citations),
        confidence=confidence,
        residual="forecasts assume the history is representative; confirm promotions, new-product "
                 "introductions and seasonality with the planner before locking the plan.",
        prepared=prepared,
    )
