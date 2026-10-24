"""Modelo canonico de datos (CDM) v1: el formato en que Kern y fdekit se pasan hechos.

ESTE ARCHIVO ESTA VENDORIZADO. Vive en ``kern/src/cdm.py`` y se copia BYTE A BYTE
a ``mytra-fde/src/fdekit/cdm.py``. Cada repo fija el sha256 del archivo en un test
(``tests/test_cdm.py``), asi que editar una sola copia pone ese repo en rojo. Para
cambiarlo: editar aca, subir ``CDM_VERSION``, copiar el archivo entero al otro repo
y actualizar el hash en los dos tests.

Por que una copia y no una dependencia: ``nosilo_core`` fue un paquete aparte entre
agosto y septiembre de 2026 y el empaquetado no rindio (ver ``pyproject.toml``), y
fdekit tiene que correr en un notebook prestado, con Python 3.10 y sin permisos de
instalacion. Por eso el modulo usa SOLO la biblioteca estandar y no importa nada de
ninguno de los dos repos.

Que resuelve: los ~30 conectores de Kern y los contratos de eventos de fdekit
describian los mismos hechos (stock en estante, una linea de pedido, una recepcion
en camino, un conteo ciclico) con nombres y reglas distintas en cada lado. Un
registro CDM es un sobre ``{"cdm", "kind", "data", "provenance"}`` cuyo ``data`` es
exactamente los campos de uno de los tipos de abajo.

Tres reglas que el tipo hace cumplir en vez de dejarlas a cada productor:

* **La ausencia se declara.** Un campo opcional en ``None`` significa "nadie lo
  midio" y NUNCA es cero. En el sobre la clave tiene que estar presente igual: una
  clave que falta es un bug del productor, un ``None`` es un hecho.
* **Una fecha de entrega sin su significado se rechaza.** Odoo, Unleashed y Cin7
  llenan ``delivered_date`` con una SALIDA; Olist y Shopify con una LLEGADA. Mismo
  campo, sentido opuesto, 6,2 puntos de OTIF de diferencia. ``OrderLine`` exige
  ``delivered_date_means`` siempre que traiga ``delivered_date``.
* **Un instante sin zona horaria se rechaza.** Los eventos fisicos llevan
  ``datetime`` con offset; uno ingenuo no dice si es UTC o America/Santiago.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any, Mapping

#: Version semantica del contrato. Un MAJOR distinto no se lee: cambia lo que un
#: campo significa. Un MINOR distinto si, porque solo agrega.
CDM_VERSION = "1.0.0"

# ---- vocabularios -----------------------------------------------------------

#: Niveles de riesgo de una escritura, los mismos strings que ``nosilo_core.writeback``.
TIER_READ = "read"
TIER_REVERSIBLE = "reversible"
TIER_IRREVERSIBLE = "irreversible"
RISK_TIERS = (TIER_READ, TIER_REVERSIBLE, TIER_IRREVERSIBLE)

#: Que hecho registra una fecha de entrega.
DATE_MEANS_DEPARTURE = "departure"
DATE_MEANS_ARRIVAL = "arrival"
DATE_MEANINGS = (DATE_MEANS_DEPARTURE, DATE_MEANS_ARRIVAL)

#: Por que se dio de baja una unidad. Los cuatro motivos de ``ScrapDispositionReason``.
SCRAP_REASONS = ("damaged_in_transit", "dropped_in_warehouse", "expired_lot", "contamination_qa")


class CdmError(ValueError):
    """Un registro que no cumple el contrato. Siempre nombra el campo."""


# ---- validadores -------------------------------------------------------------

def _text(name: str, value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or not value.strip():
        raise CdmError(f"{name}: tiene que ser un texto no vacio, vino {value!r}")


def _qty(name: str, value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    # bool es int en Python: True como cantidad es un bug del productor, no un 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CdmError(f"{name}: tiene que ser un numero, vino {value!r}")
    if not math.isfinite(value) or value < 0:
        raise CdmError(f"{name}: tiene que ser finito y >= 0, vino {value!r}")


def _day(name: str, value: Any, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str) or len(value) != 10:
        raise CdmError(f"{name}: tiene que ser una fecha YYYY-MM-DD, vino {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise CdmError(f"{name}: tiene que ser una fecha YYYY-MM-DD, vino {value!r}") from None


def _instant(name: str, value: Any) -> None:
    if not isinstance(value, str):
        raise CdmError(f"{name}: tiene que ser un instante ISO 8601 con offset, vino {value!r}")
    try:
        # Python 3.10 no acepta el sufijo Z en fromisoformat.
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise CdmError(f"{name}: tiene que ser un instante ISO 8601 con offset, vino {value!r}") from None
    if parsed.tzinfo is None:
        raise CdmError(f"{name}: {value!r} no tiene zona horaria; un instante ingenuo no se puede ubicar")


def _choice(name: str, value: Any, options: tuple[str, ...], *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if value not in options:
        raise CdmError(f"{name}: tiene que ser uno de {options}, vino {value!r}")


# ---- tipos ---------------------------------------------------------------------

@dataclass(frozen=True)
class Provenance:
    """De donde salio un registro: sistema, id en ese sistema y cuando se observo."""

    source_system: str
    source_id: str | None = None
    observed_at: str | None = None

    def __post_init__(self) -> None:
        _text("source_system", self.source_system)
        _text("source_id", self.source_id, optional=True)
        if self.observed_at is not None:
            _instant("observed_at", self.observed_at)


@dataclass(frozen=True)
class InventorySnapshot:
    """Stock de un SKU en una ubicacion, como lo reporta el sistema a una fecha.

    ``on_order`` en ``None`` es "no se sabe que viene en camino" y no "no viene nada":
    netear contra un cero inventado manda al comprador a pedir dos veces.
    """

    KIND = "inventory_snapshot"

    sku: str
    location: str
    on_hand: float
    as_of: str
    on_order: float | None = None

    def __post_init__(self) -> None:
        _text("sku", self.sku)
        _text("location", self.location)
        _qty("on_hand", self.on_hand)
        _day("as_of", self.as_of)
        _qty("on_order", self.on_order, optional=True)


@dataclass(frozen=True)
class OrderLine:
    """Una linea de pedido de venta: lo que se pidio y, si se sabe, lo que se entrego."""

    KIND = "order_line"

    order_id: str
    sku: str
    ordered_on: str
    quantity: float
    price: float | None = None
    delivered_qty: float | None = None
    promised_date: str | None = None
    delivered_date: str | None = None
    delivered_date_means: str | None = None

    def __post_init__(self) -> None:
        _text("order_id", self.order_id)
        _text("sku", self.sku)
        _day("ordered_on", self.ordered_on)
        _qty("quantity", self.quantity)
        _qty("price", self.price, optional=True)
        _qty("delivered_qty", self.delivered_qty, optional=True)
        _day("promised_date", self.promised_date, optional=True)
        _day("delivered_date", self.delivered_date, optional=True)
        _choice("delivered_date_means", self.delivered_date_means, DATE_MEANINGS, optional=True)
        if self.delivered_date is not None and self.delivered_date_means is None:
            raise CdmError(
                "delivered_date_means: una delivered_date sin decir si es salida o llegada no se "
                "puede usar para OTIF; los backends llenan el mismo campo con hechos opuestos"
            )


@dataclass(frozen=True)
class ScheduledReceipt:
    """Unidades compradas y todavia no recibidas.

    ``promised_date`` es lo que dice el ERP (la ficha del proveedor). ``expected_date``
    es la fecha con el plazo MEDIDO de ese proveedor, y es ``None`` cuando no hay
    medicion: nunca la prometida con otra etiqueta.
    """

    KIND = "scheduled_receipt"

    sku: str
    quantity: float
    order_id: str | None = None
    promised_date: str | None = None
    expected_date: str | None = None
    supplier: str | None = None

    def __post_init__(self) -> None:
        _text("sku", self.sku)
        _qty("quantity", self.quantity)
        _text("order_id", self.order_id, optional=True)
        _day("promised_date", self.promised_date, optional=True)
        _day("expected_date", self.expected_date, optional=True)
        _text("supplier", self.supplier, optional=True)


@dataclass(frozen=True)
class CycleCount:
    """Un conteo fisico de un SKU en una ubicacion.

    ``expected_qty`` es lo que el sistema esperaba encontrar. En un conteo ciego el
    operador no lo ve, pero el registro lo lleva para medir la diferencia; ``None``
    si el sistema no tenia expectativa.
    """

    KIND = "cycle_count"

    sku: str
    location: str
    counted_qty: float
    counted_at: str
    expected_qty: float | None = None

    def __post_init__(self) -> None:
        _text("sku", self.sku)
        _text("location", self.location)
        _qty("counted_qty", self.counted_qty)
        _instant("counted_at", self.counted_at)
        _qty("expected_qty", self.expected_qty, optional=True)

    @property
    def variance(self) -> float | None:
        """Contado menos esperado. ``None`` sin expectativa: no es una diferencia de cero."""
        return None if self.expected_qty is None else self.counted_qty - self.expected_qty


@dataclass(frozen=True)
class ScrapDisposal:
    """Unidades dadas de baja: salen del stock sin venderse."""

    KIND = "scrap_disposal"

    sku: str
    location: str
    units: float
    reason: str
    disposed_at: str
    measured_weight_kg: float | None = None

    def __post_init__(self) -> None:
        _text("sku", self.sku)
        _text("location", self.location)
        _qty("units", self.units)
        _choice("reason", self.reason, SCRAP_REASONS)
        _instant("disposed_at", self.disposed_at)
        _qty("measured_weight_kg", self.measured_weight_kg, optional=True)


@dataclass(frozen=True)
class GateArrival:
    """Un camion registrado en la porteria: el instante fisico en que una entrega llego."""

    KIND = "gate_arrival"

    delivery_id: str
    carrier_id: str
    location: str
    arrived_at: str

    def __post_init__(self) -> None:
        _text("delivery_id", self.delivery_id)
        _text("carrier_id", self.carrier_id)
        _text("location", self.location)
        _instant("arrived_at", self.arrived_at)


#: Todo tipo que puede viajar en un sobre, por su ``KIND``.
KINDS: dict[str, type] = {
    t.KIND: t
    for t in (InventorySnapshot, OrderLine, ScheduledReceipt, CycleCount, ScrapDisposal, GateArrival)
}


# ---- sobre ---------------------------------------------------------------------

def _major(version: str) -> str:
    return str(version).split(".", 1)[0]


def to_record(item: Any, provenance: Provenance) -> dict[str, Any]:
    """Envuelve un registro tipado en el sobre CDM. Los ``None`` viajan como ``None``."""
    kind = getattr(type(item), "KIND", None)
    if kind not in KINDS or not isinstance(item, KINDS[kind]):
        raise CdmError(f"kind: {type(item).__name__} no es un tipo CDM")
    if not isinstance(provenance, Provenance):
        raise CdmError("provenance: todo registro CDM tiene que decir de donde salio")
    return {
        "cdm": CDM_VERSION,
        "kind": kind,
        "data": {f.name: getattr(item, f.name) for f in fields(item)},
        "provenance": {f.name: getattr(provenance, f.name) for f in fields(provenance)},
    }


def _exact_keys(name: str, payload: Any, cls: type) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise CdmError(f"{name}: tiene que ser un objeto, vino {type(payload).__name__}")
    expected = {f.name for f in fields(cls)}
    got = set(payload)
    if got - expected:
        raise CdmError(f"{name}: campos desconocidos {sorted(got - expected)}")
    if expected - got:
        raise CdmError(
            f"{name}: faltan {sorted(expected - got)}; un dato ausente viaja como null, no se omite"
        )
    return dict(payload)


def from_record(record: Mapping[str, Any]) -> tuple[Any, Provenance]:
    """Lee un sobre CDM y devuelve ``(registro, procedencia)``, validado."""
    if not isinstance(record, Mapping):
        raise CdmError("record: tiene que ser un objeto")
    version = record.get("cdm")
    if version is None or _major(version) != _major(CDM_VERSION):
        raise CdmError(f"cdm: version {version!r} no es compatible con {CDM_VERSION}")
    kind = record.get("kind")
    if kind not in KINDS:
        raise CdmError(f"kind: {kind!r} no es uno de {sorted(KINDS)}")
    cls = KINDS[kind]
    item = cls(**_exact_keys("data", record.get("data"), cls))
    provenance = Provenance(**_exact_keys("provenance", record.get("provenance"), Provenance))
    return item, provenance
