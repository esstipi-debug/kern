"""Affinity slotting (plan §2.5) - the co-occurrence dimension of slotting.

COI slotting (``space.py``) places a SKU by its own cube and pick frequency. Affinity
slotting adds: SKUs frequently ordered *together* should sit near each other to cut pick
travel. It measures pairwise **lift** - how much more often two SKUs co-occur than
chance would predict - and groups strongly-linked SKUs into co-location clusters.

    lift(a, b) = P(a and b) / (P(a) * P(b)) = co_count * n_orders / (count_a * count_b)

lift > 1 means positive affinity. Pure python (no deps); complements, not replaces, the
COI module.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable


@dataclass(frozen=True)
class AffinityPair:
    sku_a: str              # canonical: sku_a < sku_b
    sku_b: str
    co_count: int           # orders containing both
    support: float          # co_count / n_orders (joint support)
    lift: float             # support / (support_a * support_b)


def _baskets(orders: Iterable[Iterable[str]]) -> list[frozenset[str]]:
    """Dedupe each order to a set of SKUs (a repeat within one order counts once)."""
    return [frozenset(o) for o in orders]


def affinity_pairs(
    orders: Iterable[Iterable[str]], *, min_co_count: int = 1
) -> list[AffinityPair]:
    """All co-occurring SKU pairs with lift, sorted by lift desc then co_count desc."""
    baskets = _baskets(orders)
    n = len(baskets)
    if n == 0:
        return []

    counts: Counter[str] = Counter()
    co: Counter[tuple[str, str]] = Counter()
    for basket in baskets:
        counts.update(basket)
        for a, b in combinations(sorted(basket), 2):
            co[(a, b)] += 1

    pairs = [
        AffinityPair(
            sku_a=a,
            sku_b=b,
            co_count=c,
            support=c / n,
            lift=c * n / (counts[a] * counts[b]),
        )
        for (a, b), c in co.items()
        if c >= min_co_count
    ]
    pairs.sort(key=lambda p: (p.lift, p.co_count), reverse=True)
    return pairs


def partners(
    orders: Iterable[Iterable[str]], sku: str, *, top_n: int | None = None
) -> list[AffinityPair]:
    """The strongest co-occurrence partners of ``sku``, best-first."""
    related = [p for p in affinity_pairs(orders) if sku in (p.sku_a, p.sku_b)]
    return related[:top_n] if top_n is not None else related


def co_location_groups(
    orders: Iterable[Iterable[str]], *, min_lift: float = 1.0
) -> list[list[str]]:
    """Group SKUs linked by ``lift >= min_lift`` into co-location clusters (size >= 2).

    Clusters are the connected components of the graph whose edges are qualifying pairs.
    """
    edges = [
        (p.sku_a, p.sku_b) for p in affinity_pairs(orders) if p.lift >= min_lift
    ]
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for a, b in edges:
        union(a, b)

    clusters: dict[str, list[str]] = {}
    for node in parent:
        clusters.setdefault(find(node), []).append(node)

    groups = [sorted(members) for members in clusters.values() if len(members) >= 2]
    groups.sort()
    return groups
