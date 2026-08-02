"""Escritores ergonomicos, uno por dominio de src.state.system_state.

Cada ``record_*`` es un wrapper delgado sobre ``snapshot()``: arma un
DataFrame a partir de dicts planos y lo valida/agrega bajo el contrato de
esquema propio del dominio. Sin logica de negocio, sin texto inventado, sin
valores por defecto -- el llamador decide que significa cada fila. Es lo que
permite que un backfill de dataset real o una instrumentacion en vivo,
despues, enchufen la misma API que usa simworld hoy.
"""

from __future__ import annotations

import pandas as pd

from src.state.store import StateStore
from src.state.system_state import StateSnapshot
from src.state.system_state import snapshot as _snapshot


def _record(domain: str, store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    return _snapshot(domain, pd.DataFrame(rows), cycle_id, store=store)


def record_stock(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``on_hand``, ``reorder_point``, ``avg_daily_demand``."""
    return _record("stock", store, cycle_id, rows)


def record_forecast(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``period``, ``forecast_qty``."""
    return _record("forecast", store, cycle_id, rows)


def record_decision(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``tool``, ``decision`` (texto libre), mas las
    columnas extra que un join necesite (``period``, ``order_qty``)."""
    return _record("decisions", store, cycle_id, rows)


def record_outcome(store: StateStore, cycle_id: str, rows: list[dict]) -> StateSnapshot:
    """Cada fila: ``product_id``, ``tool``, ``metric``, ``value``, mas ``period``
    cuando un join posterior necesite calzarla con una fila de forecast o decision."""
    return _record("outcomes", store, cycle_id, rows)
