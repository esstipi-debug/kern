"""Puente entre los tipos de los conectores de Kern y el CDM (``src/cdm.py``).

``src/cdm.py`` esta vendorizado y no puede importar nada de Kern, asi que el mapeo
vive aca: de los DTO de ``src.connectors`` a sobres CDM (para mandarle a otro
sistema lo que Kern leyo) y de sobres CDM a lo que un job de Kern consume (para
leer lo que midio el piso, por ejemplo un conteo ciclico de fdekit).

Lo que este modulo NO hace es deducir algo que el conector no declaro. El ejemplo
que importa: si una fecha de entrega es una salida o una llegada esta escrito en
prosa en el ``SERVICE_COVERAGE`` de cada conector, y una frase no se convierte en
un enum con una busqueda de palabras. El llamador lo pasa explicito, y si las
lineas traen fecha y el llamador no lo paso, ``src.cdm.OrderLine`` lo rechaza.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.cdm import (
    CycleCount,
    InventorySnapshot,
    OrderLine,
    Provenance,
    ScheduledReceipt,
    from_record,
    to_record,
)
from src.connectors import InventoryLevel, Order
from src.connectors import ScheduledReceipt as KernReceipt


def orders_to_cdm(
    orders: Iterable[Order],
    *,
    provenance: Provenance,
    delivered_date_means: str | None = None,
) -> list[dict[str, Any]]:
    """Una linea CDM por cada ``OrderLine`` de Kern, con el id y la fecha de su pedido.

    ``delivered_date_means`` es ``"departure"`` o ``"arrival"`` y vale para todo el
    lote, porque un conector llena el campo siempre con el mismo hecho.
    """
    records = []
    for order in orders:
        for line in order.lines:
            item = OrderLine(
                order_id=order.order_id,
                sku=line.sku,
                ordered_on=order.created_at[:10],
                quantity=line.quantity,
                price=line.price,
                delivered_qty=line.delivered_qty,
                promised_date=line.promised_date,
                delivered_date=line.delivered_date,
                delivered_date_means=delivered_date_means if line.delivered_date is not None else None,
            )
            records.append(to_record(item, provenance))
    return records


def inventory_to_cdm(
    levels: Iterable[InventoryLevel],
    *,
    as_of: str,
    provenance: Provenance,
    on_order_by_sku: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Una foto CDM por cada ``InventoryLevel``.

    ``on_order_by_sku`` sigue la regla de ``jobs.odoo_job.read_on_order``: ``None`` es
    "no se pudo leer lo que viene en camino" y deja ``on_order`` en ``None`` en toda
    linea; un mapa es una medicion, asi que un SKU que no aparece en el tiene cero en
    camino.
    """
    records = []
    for level in levels:
        on_order = None if on_order_by_sku is None else float(on_order_by_sku.get(level.sku, 0.0))
        item = InventorySnapshot(
            sku=level.sku,
            location=level.location,
            on_hand=level.available,
            as_of=as_of,
            on_order=on_order,
        )
        records.append(to_record(item, provenance))
    return records


def receipts_to_cdm(receipts: Iterable[KernReceipt], *, provenance: Provenance) -> list[dict[str, Any]]:
    """Una recepcion CDM por cada ``ScheduledReceipt`` de Kern, con sus dos fechas separadas."""
    return [
        to_record(
            ScheduledReceipt(
                sku=r.sku,
                quantity=r.quantity,
                order_id=r.order_id or None,
                promised_date=r.promised_date[:10] if r.promised_date else None,
                expected_date=r.expected_date[:10] if r.expected_date else None,
                supplier=r.supplier,
            ),
            provenance,
        )
        for r in receipts
    ]


def on_hand_from_cdm(records: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    """El estante por SKU segun la observacion mas reciente de cada (SKU, ubicacion).

    Lee ``inventory_snapshot`` (lo que dice el sistema) y ``cycle_count`` (lo que se
    conto en el piso); el resto de los tipos no dice cuanto hay y se ignora. Se
    compara por DIA, porque una foto del sistema tiene fecha y no hora. Si en el mismo
    dia hay foto y conteo, gana el conteo: el sistema puede estar mal y el piso no.
    Despues suma las ubicaciones de cada SKU.

    Un SKU sin ninguna observacion no aparece en el resultado. No sale con cero,
    porque eso diria que se midio y no habia nada.
    """
    latest: dict[tuple[str, str], tuple[str, int, float]] = {}
    for record in records:
        item, _ = from_record(record)
        if isinstance(item, InventorySnapshot):
            key, qty = (item.as_of, 0), item.on_hand
        elif isinstance(item, CycleCount):
            key, qty = (item.counted_at[:10], 1), item.counted_qty
        else:
            continue
        slot = (item.sku, item.location)
        seen = latest.get(slot)
        if seen is None or key > seen[:2]:
            latest[slot] = (*key, qty)
    totals: dict[str, float] = {}
    for (sku, _location), (_day, _kind, units) in latest.items():
        totals[sku] = totals.get(sku, 0.0) + units
    return totals
