"""El CSV de envios, leido con validacion estricta.

Una fila invalida rechaza el archivo entero con fila, columna y motivo. Nunca se
descarta una fila en silencio: un envio que desaparece es un transportista que
sale mejor de lo que es.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED_COLUMNS: tuple[str, ...] = (
    "envio_id", "transportista", "origen", "destino",
    "fecha_despacho", "costo", "moneda", "kg",
)
OPTIONAL_COLUMNS: tuple[str, ...] = ("fecha_prometida", "fecha_entrega")


class LoadError(ValueError):
    """El archivo no cumple el contrato. El mensaje dice donde y por que."""


@dataclass(frozen=True)
class Envio:
    envio_id: str
    transportista: str
    origen: str
    destino: str
    fecha_despacho: date
    costo: Decimal
    moneda: str
    kg: Decimal
    fecha_prometida: date | None = None
    fecha_entrega: date | None = None


def normalize_name(value: str) -> str:
    """Colapsa espacios; conserva mayusculas para mostrar."""
    return " ".join(value.split())


def name_key(value: str) -> str:
    """Clave de agrupacion: 'CHILEXPRESS ' y 'Chilexpress' son el mismo."""
    return normalize_name(value).casefold()


def load_envios(path: str | Path) -> tuple[Envio, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raise LoadError(
            "el archivo no esta en UTF-8: en Excel, guardalo como 'CSV UTF-8 (delimitado por comas)'"
        ) from None
    except OSError as exc:
        raise LoadError(f"no se pudo leer el archivo: {exc}") from None
    return parse_envios(text)


def parse_envios(text: str) -> tuple[Envio, ...]:
    text = text.lstrip("﻿")
    first_line = text.split("\n", 1)[0]
    if not first_line.strip():
        raise LoadError("el archivo esta vacio")
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = [cell.strip().lower() for cell in next(reader)]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise LoadError("faltan columnas obligatorias: " + ", ".join(missing))
    index = {col: header.index(col) for col in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS) if col in header}

    envios: list[Envio] = []
    seen_ids: dict[str, int] = {}
    currencies: dict[str, int] = {}
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        row_no = reader.line_num
        envio = _parse_row(cells, index, row_no)
        if envio.envio_id in seen_ids:
            raise LoadError(
                f"fila {row_no}: envio_id {envio.envio_id!r} repetido (ya estaba en la fila {seen_ids[envio.envio_id]})"
            )
        seen_ids[envio.envio_id] = row_no
        currencies.setdefault(envio.moneda, row_no)
        envios.append(envio)

    if not envios:
        raise LoadError("el archivo no tiene envios")
    if len(currencies) > 1:
        raise LoadError(
            "hay mas de una moneda (" + ", ".join(sorted(currencies))
            + "): esta version no convierte monedas; separa el archivo por moneda"
        )
    return tuple(envios)


def _cell(cells: list[str], index: dict[str, int], col: str) -> str:
    i = index.get(col)
    if i is None or i >= len(cells):
        return ""
    return cells[i].strip()


def _error(row_no: int, col: str, motivo: str, raw: str) -> LoadError:
    return LoadError(f"fila {row_no}, columna {col}: {motivo} ({raw!r})")


def _decimal(raw: str, col: str, row_no: int) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise _error(row_no, col, "no es un numero (usa punto decimal, sin separador de miles)", raw) from None
    if not value.is_finite():
        raise _error(row_no, col, "no es un numero finito", raw)
    return value


def _date(raw: str, col: str, row_no: int) -> date:
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        raise _error(row_no, col, "no es una fecha AAAA-MM-DD", raw)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _error(row_no, col, "no es una fecha AAAA-MM-DD", raw) from None


def _parse_row(cells: list[str], index: dict[str, int], row_no: int) -> Envio:
    def required(col: str) -> str:
        value = _cell(cells, index, col)
        if not value:
            raise _error(row_no, col, "vacia", value)
        return value

    def optional_date(col: str) -> date | None:
        value = _cell(cells, index, col)
        return _date(value, col, row_no) if value else None

    costo = _decimal(required("costo"), "costo", row_no)
    if costo < 0:
        raise _error(row_no, "costo", "no puede ser negativo", str(costo))
    kg = _decimal(required("kg"), "kg", row_no)
    if kg <= 0:
        raise _error(row_no, "kg", "debe ser mayor que cero", str(kg))
    despacho = _date(required("fecha_despacho"), "fecha_despacho", row_no)
    prometida = optional_date("fecha_prometida")
    entrega = optional_date("fecha_entrega")
    if entrega is not None and entrega < despacho:
        raise _error(row_no, "fecha_entrega", "es anterior a fecha_despacho", entrega.isoformat())

    return Envio(
        envio_id=required("envio_id"),
        transportista=normalize_name(required("transportista")),
        origen=normalize_name(required("origen")),
        destino=normalize_name(required("destino")),
        fecha_despacho=despacho,
        costo=costo,
        moneda=required("moneda").upper(),
        kg=kg,
        fecha_prometida=prometida,
        fecha_entrega=entrega,
    )
