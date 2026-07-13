"""Predicted vs. real backtesting per SKU (Control Tower A4 "verify", Linchpin 3.0 PR-8).

Reuses ``src/forecast_metrics.py`` (already-tested MAPE/WAPE/bias math -- see plan
section 1's "activos 3.0 que este v2 reutiliza") instead of re-deriving the metrics;
this module's job is joining ``src.state`` history into predicted/actual pairs and
turning per-SKU results into a recalibration suggestion.

Two ways a "predicted" value gets compared against a realized "actual" (plan
section 5, A4 row):

  - the ``forecast`` domain's ``forecast_qty`` (a tool's forecast for a period), or
  - the ``decisions`` domain's recommended quantity (what a tool told the operator
    to do -- carried as an extra column beyond the domain's base ``decision`` text
    field, since ``strict=False`` already allows it, per ``src/state/system_state.py``);

against the ``outcomes`` domain's realized ``value`` for the matching
``product_id``. Both domains need a ``period`` column to match a forecast/decision
row to its realized outcome -- an *extra* column on ``outcomes`` (allowed by
``strict=False``), not a new required column on ``DOMAIN_COLUMNS`` (per this PR's
brief: extend domain *usage*, don't touch the schema contract).

MAPE convention (documented, never silent -- plan rule 14 "ningun cap silencioso"):
rows with ``actual == 0`` are excluded from the MAPE ratio (a percentage error
against zero is undefined) but are counted and reported via
``n_excluded_zero_actual``, and still feed WAPE/bias (both well-defined at zero --
see ``src/forecast_metrics.py``). If *every* matched row for a SKU has
``actual == 0``, MAPE/WAPE come back as ``+inf`` (``src.forecast_metrics``'s own
convention) -- an honest "undefined" signal, never a fabricated 0% or 100%.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.forecast_metrics import bias as signed_bias
from src.forecast_metrics import mape, wape
from src.safety_stock import service_level_factor, tune_service_level
from src.state.store import StateStore
from src.state.system_state import StateSnapshot, history

# The metric name a realized/actual observation is recorded under in the
# "outcomes" state domain (product_id, tool, metric, value [+ period, extra cols]).
ACTUAL_QTY_METRIC = "actual_qty"


@dataclass(frozen=True)
class MatchedObservation:
    """One predicted-vs-actual pair for a SKU at a period, ready for backtesting."""

    product_id: str
    period: str
    predicted: float
    actual: float
    tool: str | None = None


@dataclass(frozen=True)
class SkuBacktestResult:
    """Per-SKU predicted-vs-actual accuracy over a backtest window."""

    product_id: str
    n_matched: int
    n_excluded_zero_actual: int
    mape: float                  # +inf when every matched actual is 0 (see module docstring)
    wape: float
    bias: float                  # mean signed error (predicted - actual); + = over-forecast
    errors: tuple[float, ...]    # raw (predicted - actual), one per matched observation


def per_sku_backtest(observations: list[MatchedObservation]) -> list[SkuBacktestResult]:
    """Group matched observations by ``product_id`` and compute MAPE/WAPE/bias per SKU.

    Returns results sorted by ``product_id`` for a deterministic report order.
    An empty input returns an empty list (nothing to backtest is not an error).
    """
    by_sku: dict[str, list[MatchedObservation]] = {}
    for obs in observations:
        by_sku.setdefault(obs.product_id, []).append(obs)

    results: list[SkuBacktestResult] = []
    for product_id, obs_list in by_sku.items():
        actual = [o.actual for o in obs_list]
        predicted = [o.predicted for o in obs_list]
        errors = tuple(p - a for p, a in zip(predicted, actual))
        n_excluded = sum(1 for a in actual if a == 0)
        results.append(
            SkuBacktestResult(
                product_id=product_id,
                n_matched=len(obs_list),
                n_excluded_zero_actual=n_excluded,
                mape=mape(actual, predicted),
                wape=wape(actual, predicted),
                bias=signed_bias(actual, predicted),
                errors=errors,
            )
        )
    return sorted(results, key=lambda r: r.product_id)


def _concat_payloads(snapshots: list[StateSnapshot]) -> pd.DataFrame:
    """Stack every snapshot's payload into one long frame (each snapshot is one
    cycle's rows, and a SKU can appear in several cycles across the window)."""
    if not snapshots:
        return pd.DataFrame()
    return pd.concat([s.payload for s in snapshots], ignore_index=True)


def match_forecast_actuals(
    forecast_snapshots: list[StateSnapshot],
    outcome_snapshots: list[StateSnapshot],
    *,
    metric: str = ACTUAL_QTY_METRIC,
    tool: str | None = None,
) -> list[MatchedObservation]:
    """Join the ``forecast`` domain's ``forecast_qty`` against the ``outcomes``
    domain's realized quantity for the same ``product_id`` + ``period``.

    ``outcome_snapshots`` payloads must carry a ``period`` column (an allowed extra
    column under the outcomes domain's ``strict=False`` contract) so a forecast row
    can be matched to its realized outcome; if it is missing, or no row has
    ``metric == metric``, this returns an empty list rather than raising -- a
    caller who hasn't wired the extra column yet gets an empty, honest result, not
    a crash. ``tool``, if given, further restricts outcome rows to that tool.
    """
    forecast_df = _concat_payloads(forecast_snapshots)
    outcomes_df = _concat_payloads(outcome_snapshots)
    if forecast_df.empty or outcomes_df.empty or "period" not in outcomes_df.columns:
        return []

    outcomes_df = outcomes_df[outcomes_df["metric"] == metric]
    if tool is not None:
        outcomes_df = outcomes_df[outcomes_df["tool"] == tool]
    if outcomes_df.empty:
        return []

    keep_cols = ["product_id", "period", "value"] + (["tool"] if "tool" in outcomes_df.columns else [])
    merged = forecast_df.merge(outcomes_df[keep_cols], on=["product_id", "period"], how="inner")

    return [
        MatchedObservation(
            product_id=row.product_id,
            period=row.period,
            predicted=float(row.forecast_qty),
            actual=float(row.value),
            tool=getattr(row, "tool", None),
        )
        for row in merged.itertuples(index=False)
    ]


def match_decision_actuals(
    decision_snapshots: list[StateSnapshot],
    outcome_snapshots: list[StateSnapshot],
    *,
    decision_value_column: str,
    metric: str = ACTUAL_QTY_METRIC,
) -> list[MatchedObservation]:
    """Join the ``decisions`` domain's recommended quantity against the ``outcomes``
    domain's realized quantity for the same ``product_id`` + ``tool`` + ``period``.

    Both domains only require a ``period`` column as an *extra* column beyond
    their base contract (``decisions``: product_id/tool/decision;
    ``outcomes``: product_id/tool/metric/value -- see
    ``src/state/system_state.py``'s ``DOMAIN_COLUMNS``, ``strict=False``).
    ``decision_value_column`` names the extra numeric column on the decisions
    payload that carries the recommended quantity (e.g. a proposed reorder qty);
    the free-text ``decision`` field itself is not assumed to be numeric.
    Returns an empty list (never raises) when a required column is missing so a
    caller who hasn't wired it yet gets an honest empty result.
    """
    decisions_df = _concat_payloads(decision_snapshots)
    outcomes_df = _concat_payloads(outcome_snapshots)
    required_decision_cols = {"product_id", "tool", "period", decision_value_column}
    if (
        decisions_df.empty
        or outcomes_df.empty
        or not required_decision_cols.issubset(decisions_df.columns)
        or "period" not in outcomes_df.columns
    ):
        return []

    outcomes_df = outcomes_df[outcomes_df["metric"] == metric]
    if outcomes_df.empty:
        return []

    merged = decisions_df.merge(
        outcomes_df[["product_id", "tool", "period", "value"]],
        on=["product_id", "tool", "period"],
        how="inner",
    )

    return [
        MatchedObservation(
            product_id=row.product_id,
            period=row.period,
            predicted=float(getattr(row, decision_value_column)),
            actual=float(row.value),
            tool=row.tool,
        )
        for row in merged.itertuples(index=False)
    ]


def run_forecast_backtest(window: int | None = None, *, store: StateStore | None = None) -> list[SkuBacktestResult]:
    """Convenience wrapper: read ``forecast``+``outcomes`` history via
    ``src.state.system_state.history`` and backtest them.  ``window`` and
    ``store`` are forwarded to ``history()`` unchanged (``store=None`` uses the
    process-wide default store; tests should pass an isolated
    ``StateStore(tmp_path / "state")`` instead, per ``src/state``'s own test
    convention)."""
    forecast_snapshots = history("forecast", window, store=store)
    outcome_snapshots = history("outcomes", window, store=store)
    observations = match_forecast_actuals(forecast_snapshots, outcome_snapshots)
    return per_sku_backtest(observations)


@dataclass(frozen=True)
class RecalibrationSuggestion:
    """A pure recalibration *suggestion* -- nothing here writes to config or state.

    Promoting this to an actual change is a human-approved changeset through
    ``src/writeback.py``'s safe-staging plane, exactly like any other write to a
    system of record; this module only produces the evidence.
    """

    product_id: str
    n_matched: int
    observed_sigma_e: float | None    # None when n_matched < 2 (can't estimate a spread)
    current_sigma_e: float
    suggested_sigma_e: float          # == current_sigma_e when no material change is warranted
    sigma_relative_change: float | None
    recommend_recalibration: bool
    current_service_level: float | None = None
    suggested_service_level: float | None = None
    current_z: float | None = None
    suggested_z: float | None = None
    rationale: str = ""


def suggest_sigma_recalibration(
    result: SkuBacktestResult,
    current_sigma_e: float,
    *,
    materiality_threshold: float = 0.20,
    current_service_level: float | None = None,
    target_fill_rate: float | None = None,
    service_level_step: float = 0.5,
) -> RecalibrationSuggestion:
    """Suggest a new sigma_e -- and, if a target fill rate is given, a new cycle
    service level / z-score -- from a SKU's observed backtest errors.

    sigma_e convention: the sample standard deviation (``ddof=1``) of the raw
    forecast errors (``predicted - actual``) over the backtest window -- the same
    quantity ``src/safety_stock.py``'s ``z_alpha * sigma_e`` formula expects.
    Needs >= 2 matched observations to estimate a spread; with fewer,
    ``observed_sigma_e`` is ``None`` and no numeric adjustment is suggested
    (explicit "insufficient data", never a fabricated number -- plan rule 14).
    Reuses ``src.safety_stock.tune_service_level``/``service_level_factor``
    (already-tested closed-loop correction) rather than re-deriving the z-score
    math: the implied fill rate feeding it is the fraction of matched
    observations where the prediction covered (>=) the realized demand.
    """
    if current_sigma_e < 0:
        raise ValueError("current_sigma_e must be >= 0")

    n = result.n_matched
    if n < 2:
        return RecalibrationSuggestion(
            product_id=result.product_id,
            n_matched=n,
            observed_sigma_e=None,
            current_sigma_e=current_sigma_e,
            suggested_sigma_e=current_sigma_e,
            sigma_relative_change=None,
            recommend_recalibration=False,
            current_service_level=current_service_level,
            rationale=f"only {n} matched observation(s) -- need >= 2 to estimate error spread",
        )

    observed_sigma = float(np.std(np.asarray(result.errors, dtype=float), ddof=1))
    if current_sigma_e > 0:
        relative_change = abs(observed_sigma - current_sigma_e) / current_sigma_e
    else:
        relative_change = float("inf") if observed_sigma > 0 else 0.0
    recommend = relative_change > materiality_threshold

    suggested_service_level = current_z = suggested_z = None
    if current_service_level is not None:
        current_z = service_level_factor(current_service_level)
        if target_fill_rate is not None:
            implied_fill_rate = sum(1 for e in result.errors if e >= 0) / n
            suggested_service_level = tune_service_level(
                current_service_level, implied_fill_rate, target_fill_rate, step=service_level_step
            )
            suggested_z = service_level_factor(suggested_service_level)

    direction = "widen" if observed_sigma > current_sigma_e else "narrow"
    rationale = (
        f"{result.product_id}: observed forecast-error std {observed_sigma:.4g} vs configured "
        f"sigma_e {current_sigma_e:.4g} ({relative_change:.1%} relative change) -- "
        f"{'recommend' if recommend else 'no need to'} {direction} the safety-stock band"
    )

    return RecalibrationSuggestion(
        product_id=result.product_id,
        n_matched=n,
        observed_sigma_e=observed_sigma,
        current_sigma_e=current_sigma_e,
        suggested_sigma_e=observed_sigma if recommend else current_sigma_e,
        sigma_relative_change=relative_change,
        recommend_recalibration=recommend,
        current_service_level=current_service_level,
        suggested_service_level=suggested_service_level,
        current_z=current_z,
        suggested_z=suggested_z,
        rationale=rationale,
    )
