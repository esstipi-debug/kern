"""SKU deduplication (capability M11).

Finds likely-duplicate SKUs by (1) exact identity on a shared valid GTIN, then
(2) fuzzy match on the normalized product name. Uses rapidfuzz when the optional
``dataquality`` extra is installed, falling back to the stdlib ``difflib`` so the
base install still works (graceful degradation, like the repo's optional LLM).
"""

from __future__ import annotations

from dataclasses import dataclass

from .data_quality import is_valid_gtin, normalize_sku

try:  # optional fast path
    from rapidfuzz import fuzz

    def _ratio(a: str, b: str) -> float:
        return float(fuzz.token_sort_ratio(a, b))
except ImportError:  # stdlib fallback
    import difflib

    def _ratio(a: str, b: str) -> float:
        a2 = " ".join(sorted(a.split()))
        b2 = " ".join(sorted(b.split()))
        return difflib.SequenceMatcher(None, a2, b2).ratio() * 100.0


@dataclass(frozen=True)
class DuplicateCluster:
    product_ids: tuple[str, ...]
    reason: str    # "gtin" | "name"
    score: float   # 100 for gtin; min pairwise ratio for name


def find_duplicates(items: list[dict], *, name_threshold: float = 90.0) -> list[DuplicateCluster]:
    """Return clusters of likely-duplicate SKUs.

    Each item dict has ``product_id``, ``name`` and optional ``gtin``.
    """
    if not items:
        return []

    clusters: list[DuplicateCluster] = []
    assigned: set[str] = set()

    # 1) exact identity: shared valid GTIN
    by_gtin: dict[str, list[str]] = {}
    for it in items:
        gtin = str(it.get("gtin", "")).strip()
        if gtin and is_valid_gtin(gtin):
            by_gtin.setdefault(gtin, []).append(str(it["product_id"]))
    for gtin, ids in by_gtin.items():
        if len(ids) > 1:
            clusters.append(DuplicateCluster(tuple(sorted(ids)), "gtin", 100.0))
            assigned.update(ids)

    # 2) fuzzy match on normalized name (greedy clustering over the remainder)
    remaining = [it for it in items if str(it["product_id"]) not in assigned]
    used: set[str] = set()
    for i in remaining:
        pid_i = str(i["product_id"])
        if pid_i in used:
            continue
        name_i = normalize_sku(str(i.get("name", "")))
        group = [pid_i]
        scores = []
        used.add(pid_i)
        for j in remaining:
            pid_j = str(j["product_id"])
            if pid_j in used:
                continue
            r = _ratio(name_i, normalize_sku(str(j.get("name", ""))))
            if r >= name_threshold:
                group.append(pid_j)
                scores.append(r)
                used.add(pid_j)
        if len(group) > 1:
            clusters.append(DuplicateCluster(tuple(sorted(group)), "name", min(scores)))

    return clusters
