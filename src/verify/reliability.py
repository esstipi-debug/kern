"""Per-tool reliability report (Control Tower A4 "verify", Linchpin 3.0 PR-8).

Aggregates ``backtest.py``'s per-SKU results into the single headline number the
plan's own worked example uses ("Mes 3: inventory_optimization tiene 94% de
precision en reorden", section 2.3) -- ``ToolReliabilityReport.headline_precision``.
PR-9 (T2->T1 autonomy promotion) compares this against a threshold; nothing here
applies a promotion or writes anything back -- this module only produces evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backtest import MatchedObservation, per_sku_backtest


@dataclass(frozen=True)
class ToolReliabilityReport:
    """A tool's headline reliability over a backtest window."""

    tool: str
    n_decisions: int
    n_skus: int
    n_hits: int
    n_excluded_zero_actual: int
    hit_rate: float | None            # None when no observation was verifiable (see module docstring)
    mean_wape: float
    mean_bias: float
    headline_precision: float | None  # == hit_rate; the single number PR-9 compares to a threshold
    meets_threshold: bool
    threshold: float


def _hit(obs: MatchedObservation, tolerance: float) -> bool | None:
    """Whether ``obs`` is within ``tolerance`` relative error. ``None`` (unverifiable,
    counted neither as a hit nor a miss) when ``actual == 0`` -- a relative
    tolerance is undefined there, matching MAPE's own zero-actual convention
    (see ``src/forecast_metrics.py`` / ``backtest.py``): never fabricate a
    hit/miss verdict out of an undefined ratio."""
    if obs.actual == 0:
        return None
    return abs(obs.predicted - obs.actual) / abs(obs.actual) <= tolerance


def build_reliability_report(
    tool: str,
    observations: list[MatchedObservation],
    *,
    tolerance: float = 0.10,
    threshold: float = 0.85,
) -> ToolReliabilityReport:
    """Aggregate one tool's matched predicted-vs-actual observations into a single
    headline precision number plus the WAPE/bias context behind it.

    ``hit_rate`` = fraction of *verifiable* observations (``actual != 0``) whose
    relative error is within ``tolerance``. Rows with ``actual == 0`` are excluded
    from the ratio (undefined denominator) and reported via
    ``n_excluded_zero_actual`` rather than silently dropped -- if every row for a
    tool has ``actual == 0``, ``hit_rate``/``headline_precision`` come back
    ``None`` (an honest "no reliability signal yet", never a fabricated
    percentage) and ``meets_threshold`` is ``False``.
    """
    if tolerance <= 0:
        raise ValueError("tolerance must be > 0")
    if not 0.0 < threshold <= 1.0:
        raise ValueError("threshold must be in (0, 1]")

    tool_obs = [o for o in observations if o.tool == tool]
    n_decisions = len(tool_obs)
    n_skus = len({o.product_id for o in tool_obs})

    verdicts = [_hit(o, tolerance) for o in tool_obs]
    verifiable = [v for v in verdicts if v is not None]
    n_excluded = len(verdicts) - len(verifiable)
    n_hits = sum(1 for v in verifiable if v)
    hit_rate = (n_hits / len(verifiable)) if verifiable else None

    by_sku = per_sku_backtest(tool_obs)
    mean_wape = (sum(r.wape for r in by_sku) / len(by_sku)) if by_sku else float("nan")
    mean_bias = (sum(r.bias for r in by_sku) / len(by_sku)) if by_sku else float("nan")

    headline = hit_rate
    meets = bool(headline is not None and headline >= threshold)

    return ToolReliabilityReport(
        tool=tool,
        n_decisions=n_decisions,
        n_skus=n_skus,
        n_hits=n_hits,
        n_excluded_zero_actual=n_excluded,
        hit_rate=hit_rate,
        mean_wape=mean_wape,
        mean_bias=mean_bias,
        headline_precision=headline,
        meets_threshold=meets,
        threshold=threshold,
    )


def build_all_reliability_reports(
    observations: list[MatchedObservation],
    *,
    tolerance: float = 0.10,
    threshold: float = 0.85,
) -> list[ToolReliabilityReport]:
    """One report per distinct ``tool`` present in ``observations`` (observations
    with ``tool is None`` are excluded -- reliability is a per-tool concept),
    sorted by tool name for a deterministic report order."""
    tools = sorted({o.tool for o in observations if o.tool is not None})
    return [build_reliability_report(t, observations, tolerance=tolerance, threshold=threshold) for t in tools]
