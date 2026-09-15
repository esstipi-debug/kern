"""Outlier detection and correction on a demand history.

One question: *which periods are exceptional, and what would a plausible value have
been there?*

Kern already refuses to ingest a history with NaNs or negatives - that is a data
*integrity* gate. This module handles the other kind: values that are perfectly real
but exceptional (a promotion, a bulk order, a clearance, a keying error). Left alone
they inflate both the forecast and the safety stock built on its error spread
(Vandeput 2021, ch. 10); the book reports error reductions of a few percentage points
from cleaning alone, and calls automating it "serious business".

**Why the threshold is on the forecast error, not on the demand.** Capping the top/
bottom x% of the demand (winsorization) invents outliers on a clean series and misses
real ones on a seasonal series - the season peaks *are* the extremes. Thresholding the
demand's own standard deviation fails the same way as soon as there is a trend or a
season. The method that works is Vandeput's third: an outlier is a value far from what
a model *expected there*, so the threshold goes on the spread of the **forecast error**
(sec 10.3), and is then recomputed excluding the points it just flagged, because the
outlier inflates the very spread that judges it (sec 10.4).

**Why the baseline is a centred rolling median.** The book uses a causal forecast,
which is right when you are forecasting. Here we are auditing history, so both sides of
a period are already known and there is no reason to look only backwards. A centred
median over the same-phase neighbours tracks level drift, respects seasonality, and -
being a median - is not dragged by the very spike it is supposed to expose. A causal
smoother is, and it contaminates the periods that follow an outlier into looking like
outliers themselves.

Nothing here mutates a history in place: :func:`scan_outliers` returns a report and the
caller decides. Flagging is the default; replacing values is the caller's call.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.forecasting import _as_array

#: z(0.99): the two-sided 98% band Vandeput uses to cut the top and bottom 1%.
DEFAULT_Z = 2.33

#: Same-phase neighbours (each side) the centred median is taken over.
DEFAULT_WINDOW = 2

#: Fewer usable error observations than this and a "spread" is a guess, not a measure.
MIN_ERROR_OBS = 5


@dataclass(frozen=True)
class OutlierReport:
    """What was flagged, what it would be replaced with, and the band that decided it."""

    #: The history with flagged periods pulled back to the edge of the acceptable band.
    #: Identical to the input everywhere else - and identical everywhere when nothing
    #: was flagged, so a caller can use it unconditionally.
    cleaned: tuple[float, ...]
    #: Indices (into the original history) judged exceptional, ascending.
    flagged: tuple[int, ...]
    n_flagged: int
    #: The acceptable band on the forecast error (forecast - actual), i.e. the same sign
    #: convention as ``forecast_metrics.bias``. An error *below* ``lower_error`` means the
    #: baseline fell far short of the actual: a demand spike.
    lower_error: float
    upper_error: float
    #: Whether the band was recomputed excluding the first pass's flags (sec 10.4).
    refined: bool
    z: float
    season_length: int
    #: Set when no judgement was possible; empty string when the scan ran normally.
    #: A scan that could not run must never be read as "no outliers found".
    reason: str = ""


def _phase_baseline(arr: np.ndarray, season_length: int, window: int) -> np.ndarray:
    """Leave-one-out centred median over each period's same-phase neighbours.

    ``season_length=1`` makes every period its own phase's neighbour, i.e. a plain
    centred rolling median. NaN marks periods with too few neighbours to judge.
    """
    n = arr.size
    out = np.full(n, np.nan, dtype=float)
    for phase in range(season_length):
        idx = np.arange(phase, n, season_length)
        if idx.size < 3:
            continue  # a phase needs neighbours before "usual for this phase" means anything
        values = arr[idx]
        for k in range(idx.size):
            # Symmetric window, always. A truncated one-sided window is biased on a
            # trending series - the median of "the periods after me" sits above me by
            # construction - which shows up as the first and last periods of every phase
            # being reported as exceptional. They are not; the window was.
            half = min(window, k, idx.size - 1 - k)
            if half < 1:
                continue  # no neighbour on one side: nothing to be exceptional *against*
            neighbours = np.concatenate((values[k - half:k], values[k + 1:k + half + 1]))
            out[idx[k]] = float(np.median(neighbours))
    return out


def _band(errors: np.ndarray, z: float) -> tuple[float, float]:
    """Acceptable-error band, widened by a float-noise tolerance.

    The tolerance matters for a phase whose history is exactly constant: its spread is
    genuinely zero, and without a tolerance the next period's rounding dust would be
    served up as an exceptional event.
    """
    mean = float(np.mean(errors))
    std = float(np.std(errors, ddof=1)) if errors.size > 1 else 0.0
    tol = 1e-9 * max(1.0, abs(mean) + std)
    return mean - z * std - tol, mean + z * std + tol


def _phase_bands(
    errors: np.ndarray, usable: np.ndarray, season_length: int, z: float, exclude: np.ndarray
) -> tuple[np.ndarray, np.ndarray] | None:
    """Per-index (lower, upper) error bands, measured **within each phase**.

    Pooling every phase's errors into one band only works when the phases sit at a
    similar level. They rarely do: a season's peak carries a wider absolute error than
    its trough, so a pooled band is too tight for the peaks (flagging the season itself)
    and too loose for the troughs. Each phase therefore gets its own band, falling back
    to the pooled one when a phase is too short to measure - which is the common case for
    a 12-month season on three years of history.

    ``exclude`` masks the points a previous pass flagged, so the band can be re-measured
    without the outliers that inflated it.
    """
    counted = usable & ~exclude
    pooled_obs = errors[counted]
    if pooled_obs.size < 2:
        return None
    pooled = _band(pooled_obs, z)

    lower = np.full(errors.size, pooled[0], dtype=float)
    upper = np.full(errors.size, pooled[1], dtype=float)
    for phase in range(season_length):
        idx = np.arange(phase, errors.size, season_length)
        own = idx[counted[idx]]
        if own.size >= MIN_ERROR_OBS:
            lower[idx], upper[idx] = _band(errors[own], z)
    return lower, upper


def scan_outliers(
    history: object,
    *,
    z: float = DEFAULT_Z,
    season_length: int = 1,
    window: int = DEFAULT_WINDOW,
    refine: bool = True,
) -> OutlierReport:
    """Flag exceptional periods and give each a plausible replacement value.

    Raises on a history that is empty, non-finite or negative - same contract as the
    forecasting engine, because a blank period is not a demand of zero and no amount of
    outlier cleaning makes an unusable history usable.
    """
    arr = _as_array(history)
    if int(season_length) < 1:
        raise ValueError(f"season_length must be >= 1, got {season_length!r}")
    season_length = int(season_length)

    def _empty(reason: str) -> OutlierReport:
        return OutlierReport(
            cleaned=tuple(float(v) for v in arr), flagged=(), n_flagged=0,
            lower_error=float("nan"), upper_error=float("nan"),
            refined=False, z=z, season_length=season_length, reason=reason,
        )

    baseline = _phase_baseline(arr, season_length, window)
    usable = np.isfinite(baseline)
    if int(np.count_nonzero(usable)) < MIN_ERROR_OBS:
        return _empty("history too short to estimate an error spread")

    errors = np.where(usable, baseline - arr, np.nan)  # forecast - actual
    if float(np.std(errors[usable])) == 0.0:
        return _empty("forecast error has no spread; nothing can be exceptional")

    none_excluded = np.zeros(arr.size, dtype=bool)
    bands = _phase_bands(errors, usable, season_length, z, none_excluded)
    if bands is None:
        return _empty("forecast error has no spread; nothing can be exceptional")
    lower, upper = bands
    flagged = usable & ((errors < lower) | (errors > upper))

    refined_band = False
    if refine and flagged.any():
        retry = _phase_bands(errors, usable, season_length, z, flagged)
        if retry is not None:
            lower, upper = retry
            flagged = usable & ((errors < lower) | (errors > upper))
            refined_band = True

    cleaned = arr.astype(float).copy()
    idx = np.flatnonzero(flagged)
    for t in idx:
        # Pull the period back to the edge of the band it broke, not to the baseline:
        # an exceptional period is still an unusually high (or low) one.
        edge = lower[t] if errors[t] < lower[t] else upper[t]
        cleaned[t] = max(0.0, float(baseline[t] - edge))

    return OutlierReport(
        cleaned=tuple(float(v) for v in cleaned),
        flagged=tuple(int(t) for t in idx),
        n_flagged=int(idx.size),
        # The widest band actually applied. With one phase it *is* the band; with several
        # it is the loosest of them, which is the honest headline for "what got through".
        lower_error=float(lower.min()),
        upper_error=float(upper.max()),
        refined=refined_band,
        z=z,
        season_length=season_length,
    )
