"""Multi-criteria ABC classification (plan §2.2, the MCDM upgrade).

Classic ABC ranks SKUs on a single axis (annual usage value). When criticality, lead
time, margin or obsolescence risk also matter, this ranks each SKU on a weighted
*composite* of those criteria and cuts the ranking into A/B/C bands. It reuses the
TOPSIS engine in ``src/mcdm.py`` (so cost-type criteria like lead time are handled
correctly) and pairs with BWM weights from the same module.

Pure (numpy/scipy via mcdm); complements the single-criterion ABC-XYZ in
``classification.py`` rather than replacing it.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.mcdm import Criterion, topsis_rank


@dataclass(frozen=True)
class MultiCriteriaClass:
    sku: str
    score: float            # composite TOPSIS closeness (higher = more important)
    rank: int               # 1 = most important
    abc_class: str          # "A" | "B" | "C"


def classify_multicriteria(
    items: dict[str, dict[str, float]],
    criteria: list[Criterion],
    weights: dict[str, float],
    *,
    a_share: float = 0.2,
    b_share: float = 0.3,
) -> list[MultiCriteriaClass]:
    """Rank SKUs by a weighted composite of criteria and band them A/B/C.

    ``a_share``/``b_share`` are the cumulative *fractions of items* assigned to A and B
    (the rest are C), banded by descending composite score.
    """
    if a_share < 0 or b_share < 0 or a_share + b_share > 1.0 + 1e-9:
        raise ValueError("a_share and b_share must be non-negative and sum to <= 1")
    if not items:
        return []

    ranking = topsis_rank(items, criteria, weights)
    n = len(ranking.ranking)

    result: list[MultiCriteriaClass] = []
    for position, sku in enumerate(ranking.ranking, start=1):
        cum_fraction = position / n
        if cum_fraction <= a_share + 1e-9:
            band = "A"
        elif cum_fraction <= a_share + b_share + 1e-9:
            band = "B"
        else:
            band = "C"
        result.append(MultiCriteriaClass(sku, ranking.scores[sku], position, band))
    return result
