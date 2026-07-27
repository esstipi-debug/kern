"""La verdad del mundo simulado - generada una vez, con semilla, e inmutable.

Nadie fuera del marcador lee esto. Kern ve el mundo unicamente a traves de
``src/simworld/lens.py``. La demanda verdadera y los lead times reales se sortean
en la generacion, no durante la corrida: es lo que garantiza que los cuatro
corredores enfrenten la realizacion identica y que ninguno pueda tener suerte.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

REGIMES = ("seasonal_trend",)

WEEKS_PER_YEAR = 52.0


@dataclass(frozen=True)
class Sku:
    sku: str
    unit_cost: float
    price: float
    supplier_idx: int


@dataclass(frozen=True)
class Supplier:
    name: str
    promised_lead_weeks: int
    otif: float  # fraccion de ordenes que llegan en el lead time prometido


@dataclass(frozen=True)
class World:
    seed: int
    weeks: int
    regime: str
    skus: tuple[Sku, ...]
    suppliers: tuple[Supplier, ...]
    demand: np.ndarray          # (n_skus, weeks) - la demanda que realmente ocurre
    real_lead_weeks: np.ndarray  # (n_skus, weeks) - lead real de una orden puesta en la semana t


@dataclass(frozen=True)
class WorldState:
    week: int
    on_hand: np.ndarray
    in_transit: tuple[tuple[int, int, float], ...]  # (sku_idx, semana_de_llegada, cantidad)


def generate_world(
    *,
    seed: int,
    n_skus: int,
    n_suppliers: int,
    weeks: int,
    regime: str = "seasonal_trend",
) -> World:
    """Sortea un mundo completo. Misma semilla, mismo mundo, siempre."""
    if regime not in REGIMES:
        raise ValueError(f"regime desconocido: {regime!r}; conocidos: {REGIMES}")
    rng = np.random.default_rng(seed)

    suppliers = tuple(
        Supplier(
            name=f"Proveedor-{i + 1}",
            promised_lead_weeks=int(rng.choice([1, 2, 3, 4])),
            otif=float(rng.uniform(0.55, 0.95)),
        )
        for i in range(n_suppliers)
    )

    skus = []
    for i in range(n_skus):
        unit_cost = float(np.round(rng.uniform(2.0, 90.0), 2))
        skus.append(
            Sku(
                sku=f"SKU-{1000 + i}",
                unit_cost=unit_cost,
                price=float(np.round(unit_cost * rng.uniform(1.4, 2.8), 2)),
                supplier_idx=int(rng.integers(0, n_suppliers)),
            )
        )

    demand = _seasonal_trend_demand(rng, n_skus, weeks)
    real_lead = _real_lead_weeks(rng, skus, suppliers, weeks)
    return World(
        seed=seed, weeks=weeks, regime=regime,
        skus=tuple(skus), suppliers=suppliers,
        demand=demand, real_lead_weeks=real_lead,
    )


def _seasonal_trend_demand(rng: np.random.Generator, n_skus: int, weeks: int) -> np.ndarray:
    t = np.arange(weeks, dtype=float)
    base = rng.uniform(3.0, 40.0, size=(n_skus, 1))
    trend = rng.uniform(-0.25, 0.60, size=(n_skus, 1))
    amp = rng.uniform(0.05, 0.45, size=(n_skus, 1))
    phase = rng.uniform(0.0, 2 * np.pi, size=(n_skus, 1))
    seasonal = 1.0 + amp * np.sin(2 * np.pi * t / WEEKS_PER_YEAR + phase)
    level = base * (1.0 + trend * t / max(weeks, 1)) * seasonal
    noise = rng.normal(0.0, 0.18, size=(n_skus, weeks))
    return np.maximum(np.round(level * (1.0 + noise)), 0.0)


def _real_lead_weeks(
    rng: np.random.Generator,
    skus: list[Sku],
    suppliers: tuple[Supplier, ...],
    weeks: int,
) -> np.ndarray:
    out = np.zeros((len(skus), weeks), dtype=int)
    for i, sku in enumerate(skus):
        sup = suppliers[sku.supplier_idx]
        on_time = rng.random(weeks) < sup.otif
        slip = rng.integers(1, 4, size=weeks)
        out[i] = np.where(on_time, sup.promised_lead_weeks, sup.promised_lead_weeks + slip)
    return out


def initial_state(world: World) -> WorldState:
    """Arranca con cuatro semanas de la demanda media de cada SKU en mano."""
    start = np.round(world.demand[:, : min(4, world.weeks)].mean(axis=1) * 4.0)
    return WorldState(week=0, on_hand=start.astype(float), in_transit=())
