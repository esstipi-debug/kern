"""Demand-forecasting agent job: a demand-history CSV -> forecastability-aware forecasts.

The data-prep + deck + guided-options half of the forecast tool. Reads a long-format demand
history (sku, period, quantity) with pandas directly (deliberately *not* via jobs/intake.py,
which the parallel loop owns), then for each SKU:

  1. cleans the series (missing -> 0, negatives floored),
  2. segments it by forecastability (Syntetos-Boylan ADI x CV^2 -> smooth / erratic /
     intermittent / lumpy) via ``src.forecastability``,
  3. auto-selects the matching method (AutoETS/TSB via StatsForecast when installed,
     else SES/Croston) and backtests it on a holdout,
  4. quantifies **Forecast Value-Add** against a naive baseline that is *seasonal* when
     the series is (``src.seasonality``) - scoring seasonal demand against a lag-1 naive
     flatters every model (Vandeput 2021, sec 27.2) - and requires the method to win on
     **both** MASE and RMSSE before calling it value-adding (sec 2.6: MASE is MAE-based,
     and optimising an MAE-based metric targets the demand *median*, which is 0 for
     intermittent demand - exactly the SKUs this job routes to TSB/Croston),
  5. scans each series for exceptional periods (``src.outliers``) and *reports* them;
     cleaning is opt-in, never silent,

and emits a protected ``GuidedOutcome`` with **ranked forecasting-policy options**
(auto-per-segment / global SES / review-the-lumpy) so the tool offers *choices to act*.

Mirrors jobs/whatif_job.py (prepare/sniff) and jobs/returns_job.py (guided options) style.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from nosilo_core.guided import GuidedOutcome, Residual, recommend, verify_guided
from src.decision_options import Objective, Scenario, decide
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.forecast_metrics import compute_metrics
from src.forecastability import segment
from src.forecasting import forecast_demand
from src.outliers import scan_outliers
from src.seasonality import detect_season_length

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
    fva: float            # 1 - MASE; > 0 means the method beats the naive baseline on MAE
    #: True only when the method beats naive on BOTH scales - see the module docstring for
    #: why a MASE-only gate blesses a forecast of zero on intermittent demand.
    beats_naive: bool
    n_periods: int
    #: Value-add on the RMSE-based scale. Reported next to ``fva`` because the two answer
    #: different questions: MASE moved = the *typical* error moved, RMSSE moved = the
    #: *biggest* errors moved (Vandeput 2021, sec 27.2).
    rmsse: float = float("inf")
    fva_rmsse: float = 0.0
    beats_naive_mase: bool = False
    beats_naive_rmsse: bool = False
    #: Season length the naive benchmark used (1 = plain lag-1 naive). On the record
    #: because it decides what ``mase``/``fva`` are even measuring.
    season_length: int = 1
    #: ``scaled`` (MASE/RMSSE are real numbers) | ``exact`` (the naive baseline is perfect
    #: here, so not beating it is a verdict, not a gap) | ``unavailable`` (no benchmark
    #: could be measured - no verdict either way). See :func:`_benchmark_state`.
    benchmark_state: str = "unavailable"
    #: Periods flagged as exceptional (promotion, bulk order, clearance, keying error).
    #: Reported, never cleaned behind the caller's back.
    n_outliers: int = 0
    outlier_periods: tuple[int, ...] = ()
    #: Signed forecast error over the fitted history, as ``mean(actual - forecast)``:
    #: positive = the method runs LOW (it under-forecast). MAPE says how wrong, this says
    #: in which direction, which is the half a planner acts on (Vandeput 2021, sec 2.2).
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


@dataclass(frozen=True)
class ForecastJobReport:
    """Portfolio forecast: per-SKU results (hardest-first), the mix and the policy options."""

    skus: tuple[SkuForecast, ...]
    mix: dict
    hardest: tuple[str, float]
    n_skus: int
    n_beating_naive: int
    mean_mase: float
    outcome: GuidedOutcome
    summary: str
    mean_rmsse: float = float("inf")
    #: SKUs the MASE gate would have passed and the RMSSE gate rejects. Not a rounding
    #: difference: it is where a method looks good on typical error while missing the big
    #: periods - the failure a median-seeking metric is blind to.
    n_mase_only: int = 0
    #: SKUs carrying at least one exceptional period, and the total flagged across them.
    n_skus_with_outliers: int = 0
    n_outliers: int = 0


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

    The naive benchmark's season is detected on the **train** slice only, inside this
    function, so it cannot be passed in from a caller that has seen the whole series:
    letting held-out periods shape the benchmark is the data leakage Vandeput warns about
    (sec 3.2) wearing a different hat. The season used comes back on
    ``ForecastMetrics.season_length``.
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
    m = detect_season_length(train)
    return compute_metrics(test, [fc] * h, train=train, season_length=m)


def _policy_outcome(
    *, base_summary: str, beating_share: float, regular_share: float, lumpy_share: float,
    n_unvalidated: int = 0, n_skus: int = 0, n_naive_exact: int = 0,
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
    residuals: list[Residual] = []
    if n_unvalidated > 0:
        unvalidated_share = n_unvalidated / n_skus if n_skus else 0.0
        confidence = round(confidence * (1.0 - 0.5 * unvalidated_share), 2)
        residuals.append(Residual(
            description=f"{n_unvalidated} of {n_skus} SKU(s) had too little demand history to "
                        "backtest - their forecast accuracy (MASE) could not be validated.",
            risk_if_skipped="the chosen method for these SKUs is unverified; a poor fit could go "
                            "unnoticed until a stockout or an excess-stock event exposes it.",
        ))
    if n_naive_exact > 0:
        # Not a gap in the evidence - evidence of a different answer. It gets a residual
        # because it changes what the planner should *do*, not because anything is unknown.
        residuals.append(Residual(
            description=f"{n_naive_exact} of {n_skus} SKU(s) are reproduced exactly by the naive "
                        "baseline (the series simply repeats), so no statistical method can add "
                        "value there.",
            risk_if_skipped="running a model on these spends effort to be worse than repeating "
                            "last cycle; check first that the repetition is real demand and not an "
                            "artefact of how the history was generated or copied.",
        ))
    return decide(
        base_summary, scenarios, objectives,
        confidence=confidence, residuals=residuals or None,
    )


def _benchmark_state(metrics) -> str:
    """``scaled`` | ``exact`` | ``unavailable`` - what the naive baseline could tell us.

    ``unavailable`` is an *absence of a verdict* (no benchmark could be measured: too
    little history, or a season longer than the train slice). ``exact`` is a verdict: the
    naive forecast is right on the nose here, so no method can add value. Both make MASE
    infinite, which is why they have to be told apart before anything is reported - saying
    "we could not validate this SKU" when the truth is "naive already wins" is a different
    claim, and the wrong one.
    """
    if metrics is None or metrics.naive_obs <= 0:
        return "unavailable"
    if metrics.naive_mae == 0.0 or metrics.naive_rmse == 0.0:
        return "exact"
    if not (math.isfinite(metrics.mase) and math.isfinite(metrics.rmsse)):
        return "unavailable"
    return "scaled"


#: z(0.90): half-width of an 80% band (P10-P90) under a normal error.
_Z80 = 1.2815515655446004


def _dispersion(fc) -> float | None:
    """The sigma the band is drawn with, or ``None`` when none was estimable.

    Reuses ``ForecastResult.sigma_source`` instead of re-deciding: the engine and the
    band must never disagree about which dispersion is in play.
    """
    if fc.sigma_source == "forecast_error":
        return float(fc.error_std)
    if fc.sigma_source == "demand_std_fallback":
        return float(fc.demand_std)
    return None


def run(
    series_by_name: dict[str, list[float]],
    *,
    holdout_fraction: float = 0.25,
    min_backtest_periods: int = 4,
) -> ForecastJobReport:
    """Segment, auto-forecast + backtest each SKU, and present ranked forecasting policies."""
    seg = segment(series_by_name)
    skus: list[SkuForecast] = []
    for f in seg.items:
        series = series_by_name[f.name]
        fc = forecast_demand(series, method=f.recommended_method)
        point = float(fc.forecast)
        sigma = _dispersion(fc)
        metrics = _backtest(
            series, f.recommended_method,
            holdout_fraction=holdout_fraction, min_periods=min_backtest_periods,
        )
        state = _benchmark_state(metrics)
        season = metrics.season_length if metrics is not None else 1
        if state == "scaled":
            mase, rmsse, mape, mae = metrics.mase, metrics.rmsse, metrics.mape, metrics.mae
            beats_mase, beats_rmsse = mase < 1.0, rmsse < 1.0
            fva, fva_rmsse = 1.0 - mase, 1.0 - rmsse
        else:
            # "exact": the benchmark is unbeatable, so the verdict is a confident no.
            # "unavailable": there is no benchmark, so there is no verdict at all - and
            # the two must not collapse into the same reported number.
            mase = rmsse = float("inf")   # unscalable, for two different reasons
            # The *unscaled* accuracy is still real when the backtest ran, so keep it:
            # only the comparison against the benchmark is missing, not the measurement.
            measured = metrics is not None and state == "exact"
            mae = metrics.mae if measured else float("nan")
            mape = metrics.mape if measured else float("inf")
            beats_mase = beats_rmsse = False
            fva = fva_rmsse = 0.0
        outliers = scan_outliers(series, season_length=season)
        skus.append(SkuForecast(
            name=f.name, quadrant=f.quadrant, adi=f.adi, cv2=f.cv2, method=f.recommended_method,
            forecast=point, mae=mae, mape=mape, mase=mase, fva=fva,
            rmsse=rmsse, fva_rmsse=fva_rmsse,
            beats_naive=beats_mase and beats_rmsse,
            beats_naive_mase=beats_mase, beats_naive_rmsse=beats_rmsse,
            n_periods=f.n_periods, season_length=season, benchmark_state=state,
            n_outliers=outliers.n_flagged, outlier_periods=outliers.flagged,
            bias=float(fc.bias), error_std=float(fc.error_std), sigma_source=fc.sigma_source,
            p10=(max(0.0, point - _Z80 * sigma) if sigma is not None else None),
            p90=(point + _Z80 * sigma if sigma is not None else None),
        ))

    n = len(skus)
    n_beating = sum(1 for s in skus if s.beats_naive)
    n_mase_only = sum(1 for s in skus if s.beats_naive_mase and not s.beats_naive_rmsse)
    n_unvalidated = sum(1 for s in skus if s.benchmark_state == "unavailable")
    n_naive_exact = sum(1 for s in skus if s.benchmark_state == "exact")
    finite_mases = [s.mase for s in skus if math.isfinite(s.mase)]
    finite_rmsses = [s.rmsse for s in skus if math.isfinite(s.rmsse)]
    mean_mase = sum(finite_mases) / len(finite_mases) if finite_mases else float("inf")
    mean_rmsse = sum(finite_rmsses) / len(finite_rmsses) if finite_rmsses else float("inf")
    n_outliers = sum(s.n_outliers for s in skus)
    n_skus_with_outliers = sum(1 for s in skus if s.n_outliers)
    regular_share = (seg.mix["smooth"] + seg.mix["erratic"]) / n if n else 0.0
    lumpy_share = seg.mix["lumpy"] / n if n else 0.0

    base_summary = (
        f"{n} SKUs segmented (smooth {seg.mix['smooth']}, erratic {seg.mix['erratic']}, "
        f"intermittent {seg.mix['intermittent']}, lumpy {seg.mix['lumpy']}); "
        f"{n_beating}/{n} beat the naive baseline on both MASE and RMSSE."
    )
    outcome = _policy_outcome(
        base_summary=base_summary,
        beating_share=(n_beating / n if n else 0.0),
        regular_share=regular_share,
        lumpy_share=lumpy_share,
        n_unvalidated=n_unvalidated,
        n_skus=n,
        n_naive_exact=n_naive_exact,
    )
    recommended = recommend(outcome.options).label
    summary = f"{base_summary} Recommended policy: {recommended}."

    return ForecastJobReport(
        skus=tuple(skus),
        mix=seg.mix,
        hardest=seg.hardest,
        n_skus=n,
        n_beating_naive=n_beating,
        mean_mase=mean_mase,
        outcome=outcome,
        summary=summary,
        mean_rmsse=mean_rmsse,
        n_mase_only=n_mase_only,
        n_skus_with_outliers=n_skus_with_outliers,
        n_outliers=n_outliers,
    )


def verify(report: ForecastJobReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + finite forecasts."""
    issues = list(verify_guided(report.outcome))
    if report.n_skus <= 0:
        issues.append("no SKUs to forecast")
    if not all(math.isfinite(s.forecast) for s in report.skus):
        issues.append("a forecast is not finite")
    return issues


def _fmt(x: float) -> object:
    """Round for the operational CSV, leaving non-finite values blank-friendly."""
    return round(x, 4) if math.isfinite(x) else ""


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
            "mase": _fmt(s.mase),
            "rmsse": _fmt(s.rmsse),
            "fva": round(s.fva, 4),
            "fva_rmsse": round(s.fva_rmsse, 4),
            "beats_naive": s.beats_naive,
            "beats_naive_mase": s.beats_naive_mase,
            "beats_naive_rmsse": s.beats_naive_rmsse,
            "benchmark": "seasonal_naive" if s.season_length > 1 else "naive",
            "season_length": s.season_length,
            "n_outliers": s.n_outliers,
            "outlier_periods": " ".join(str(i) for i in s.outlier_periods),
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
    mean_rmsse_txt = f"{report.mean_rmsse:.2f}" if math.isfinite(report.mean_rmsse) else "n/a"
    n_seasonal = sum(1 for s in report.skus if s.season_length > 1)
    seasonal_txt = (
        f"{n_seasonal} of {n} SKU(s) are scored against a *seasonal* naive."
        if n_seasonal else "No SKU showed a season, so the benchmark is the lag-1 naive throughout."
    )

    summary = (
        f"Forecast study across {n} SKU(s): {mix['smooth']} smooth / {mix['erratic']} erratic / "
        f"{mix['intermittent']} intermittent / {mix['lumpy']} lumpy. {report.n_beating_naive}/{n} "
        f"({beat_pct:.0f}%) beat the naive baseline on both scales "
        f"(mean MASE {mean_mase_txt}, mean RMSSE {mean_rmsse_txt})."
    )

    findings = [
        Finding(
            "Forecastability mix (Syntetos-Boylan)",
            f"smooth {mix['smooth']}, erratic {mix['erratic']}, intermittent {mix['intermittent']}, "
            f"lumpy {mix['lumpy']} - timing (ADI) x size variability (CV^2) sets the method per SKU.",
            impact="regular SKUs take SES; intermittent/lumpy take Croston - one global model misfits both",
        ),
        Finding(
            "Forecast value-add vs naive (both scales)",
            f"{report.n_beating_naive}/{n} SKU(s) beat the naive baseline on a holdout backtest "
            f"on BOTH scales (mean MASE {mean_mase_txt}, mean RMSSE {mean_rmsse_txt}). "
            f"{seasonal_txt} MASE alone is not enough: it is MAE-based, and an MAE-optimal "
            "forecast is the demand median - which is zero for intermittent demand.",
            impact="adopt the method only where it beats naive; elsewhere naive is the honest default",
        ),
    ]
    if report.n_mase_only:
        findings.append(Finding(
            "SKUs that pass on typical error but fail on the big periods",
            f"{report.n_mase_only} SKU(s) beat naive on MASE and lose on RMSSE: the method is fine "
            "on ordinary weeks and misses the peaks, which is where the stockouts come from.",
            impact="do not adopt on these; the safety stock, not the point forecast, is what has to absorb them",
        ))
    if report.n_skus_with_outliers:
        findings.append(Finding(
            "Exceptional periods detected (not cleaned)",
            f"{report.n_outliers} period(s) across {report.n_skus_with_outliers} SKU(s) sit outside "
            "the acceptable forecast-error band - typically promotions, bulk orders, clearances or "
            "keying errors. They inflate both the forecast and the error spread the safety stock "
            "is sized from. They are flagged in forecast_register.csv, and left untouched.",
            impact="confirm each with the planner, then re-run with cleaning on; do not let them size the buffer",
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
            rationale="SKUs beating the (seasonal where applicable) naive on BOTH MASE and RMSSE"),
        Kpi("Mean MASE", mean_mase_txt, target="minimize",
            rationale="Mean scaled error across backtested SKUs (< 1 is better than naive)"),
        Kpi("Mean RMSSE", mean_rmsse_txt, target="minimize",
            rationale="Same, on the RMSE scale: this is the one that sees the big misses"),
        Kpi("Exceptional periods", f"{report.n_outliers}", target="minimize",
            rationale="Periods outside the acceptable forecast-error band; flagged, not cleaned"),
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
