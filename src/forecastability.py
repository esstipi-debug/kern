"""Forecastability segmentation (Syntetos-Boylan-Croston demand classification).

Classifies a demand series by two shape statistics:
  - ADI  : average demand interval = periods / periods-with-demand (timing irregularity)
  - CV^2 : squared coefficient of variation of the non-zero demands (size irregularity)

and buckets it into the SBC quadrants - smooth / erratic / intermittent / lumpy - each
paired with the forecast method that fits it (SES for regular timing, Croston for
intermittent). Pure (numpy only), mirroring the analytical-core style.

Grounded in L3: Syntetos, Boylan & Croston (2005), "On the categorization of demand
patterns"; Vandeput (2021), Data Science for Supply Chain Forecasting.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.forecasting import average_demand_interval

# SBC cut-offs: ADI 1.32 separates regular from intermittent timing; CV^2 0.49
# separates stable from variable demand size.
ADI_THRESHOLD = 1.32
CV2_THRESHOLD = 0.49

_METHOD_BY_QUADRANT = {
    "smooth": "ses",
    "erratic": "ses",
    "intermittent": "croston",
    "lumpy": "croston",
}
_QUADRANTS = ("smooth", "erratic", "intermittent", "lumpy")


@dataclass(frozen=True)
class Forecastability:
    """One series' demand-shape classification and the method that fits it."""

    name: str
    adi: float
    cv2: float
    quadrant: str
    recommended_method: str
    n_periods: int
    nonzero_periods: int


@dataclass(frozen=True)
class ForecastabilityReport:
    """Portfolio roll-up: items ranked hardest-first, the quadrant mix and the worst SKU."""

    items: tuple[Forecastability, ...]
    mix: dict
    hardest: tuple[str, float]


def squared_cv_nonzero(history) -> float:
    """CV^2 of the non-zero demands; 0 for constant or all-zero demand."""
    arr = np.asarray(list(history), dtype=float)
    nonzero = arr[arr > 0]
    if nonzero.size <= 1:
        return 0.0
    mean = float(nonzero.mean())
    if mean == 0:
        return 0.0
    std = float(np.std(nonzero, ddof=1))
    return (std / mean) ** 2


def _quadrant(adi: float, cv2: float) -> str:
    intermittent_timing = adi >= ADI_THRESHOLD
    variable_size = cv2 >= CV2_THRESHOLD
    if not intermittent_timing:
        return "erratic" if variable_size else "smooth"
    return "lumpy" if variable_size else "intermittent"


def _difficulty(adi: float, cv2: float) -> float:
    """Forecasting difficulty: rises with both timing and size irregularity."""
    capped_adi = adi if np.isfinite(adi) else 1e6
    return capped_adi * (1.0 + cv2)


def classify_series(name: str, history) -> Forecastability:
    """Classify one demand series into its SBC quadrant and recommended method."""
    arr = np.asarray(list(history), dtype=float)
    adi = average_demand_interval(arr) if arr.size else float("inf")
    cv2 = squared_cv_nonzero(arr)
    quadrant = _quadrant(adi, cv2)
    return Forecastability(
        name=name,
        adi=adi,
        cv2=cv2,
        quadrant=quadrant,
        recommended_method=_METHOD_BY_QUADRANT[quadrant],
        n_periods=int(arr.size),
        nonzero_periods=int(np.count_nonzero(arr)),
    )


def segment(series_by_name: dict) -> ForecastabilityReport:
    """Classify every series, rank hardest-first and roll up the quadrant mix."""
    items = [classify_series(name, hist) for name, hist in series_by_name.items()]
    items.sort(key=lambda f: _difficulty(f.adi, f.cv2), reverse=True)
    mix = {q: sum(1 for f in items if f.quadrant == q) for q in _QUADRANTS}
    hardest = (items[0].name, _difficulty(items[0].adi, items[0].cv2)) if items else ("n/a", 0.0)
    return ForecastabilityReport(items=tuple(items), mix=mix, hardest=hardest)
