"""Season-length detection for a demand series.

One question: *does this series repeat, and every how many periods?*

It exists because of the benchmark. MASE and RMSSE scale a method's error by a
**naive** forecast's error, and on seasonal demand a lag-1 naive is a straw man --
scoring against it flatters every model and inflates the reported forecast
value-add. Vandeput (2021, sec 27.2) is explicit: when the demand is seasonal the
benchmark must be a *seasonal* naive (same period last cycle).

Detection is deliberately conservative. A season is only declared when the
autocorrelation at a candidate lag is (a) above a floor, (b) a **local peak**, and
(c) backed by at least ``min_cycles`` full cycles of history. Returning 1 means
"no season found" -- the caller then keeps the plain lag-1 naive, which is the
honest default, not a failure.
"""

from __future__ import annotations

import numpy as np

#: Season lengths worth testing on the buckets a supply chain actually plans in:
#: quarters-in-a-year, days-in-a-week, months-in-a-year, weeks-in-a-year.
DEFAULT_CANDIDATES: tuple[int, ...] = (4, 7, 12, 52)

#: Below this autocorrelation the repetition is not distinguishable from noise.
MIN_ACF = 0.3

#: Full cycles of history required before a candidate is believable.
MIN_CYCLES = 2


def _as_array(history: object) -> np.ndarray:
    arr = np.asarray(list(history), dtype=float)
    if arr.size == 0:
        raise ValueError("history is empty")
    return arr


def autocorrelation(history: object, lag: int) -> float:
    """Sample autocorrelation at ``lag``; 0.0 when the lag or the series cannot support it.

    A constant series has zero variance and therefore no meaningful autocorrelation:
    it returns 0.0 rather than a divide-by-zero, which is what keeps a flat demand
    from being declared seasonal at every candidate at once.
    """
    arr = _as_array(history)
    if lag <= 0 or lag >= arr.size:
        return 0.0
    dev = arr - arr.mean()
    denom = float(np.sum(dev * dev))
    if denom == 0:
        return 0.0
    return float(np.sum(dev[lag:] * dev[:-lag]) / denom)


def detect_season_length(
    history: object,
    *,
    candidates: tuple[int, ...] = DEFAULT_CANDIDATES,
    min_cycles: int = MIN_CYCLES,
    min_acf: float = MIN_ACF,
) -> int:
    """Return the detected season length, or ``1`` when the series shows no season.

    ``1`` is not a sentinel for failure -- it is the plain lag-1 naive, and every
    consumer of this module treats it as a valid answer.
    """
    arr = _as_array(history)
    best_lag, best_acf = 1, min_acf
    for m in candidates:
        if m < 2 or arr.size < min_cycles * m + 1:
            continue  # not enough history for this candidate to mean anything
        acf = autocorrelation(arr, m)
        if acf < best_acf:
            continue
        # A trend autocorrelates at *every* lag, so height alone proves nothing.
        # A season shows up as a local peak: the cycle length beats its neighbours.
        if acf <= autocorrelation(arr, m - 1) or acf <= autocorrelation(arr, m + 1):
            continue
        best_lag, best_acf = m, acf
    return best_lag
