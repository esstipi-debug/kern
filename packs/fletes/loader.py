"""El CSV de envios, leido con validacion estricta.

Una fila invalida rechaza el archivo entero con fila, columna y motivo. Nunca se
descarta una fila en silencio: un envio que desaparece es un transportista que
sale mejor de lo que es.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    """Colapsa espacios y compone la forma Unicode (NFC); conserva mayusculas y acentos para mostrar."""
    return " ".join(unicodedata.normalize("NFC", value).split())


def name_key(value: str) -> str:
    """Clave de agrupacion: sin mayusculas, espacios de mas ni acentos, en cualquier forma Unicode.

    'CHILEXPRESS ' y 'Chilexpress' son el mismo transportista; 'Concepcion' y 'Concepción'
    (compuesta o descompuesta) son la misma ciudad.
    """
    decomposed = unicodedata.normalize("NFKD", normalize_name(value).casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


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
    records = _records(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
    header_record = next(records, None)
    if header_record is None:
        raise LoadError("el archivo esta vacio")
    header = [cell.strip().lower() for cell in header_record[1]]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise LoadError("faltan columnas obligatorias: " + ", ".join(missing))
    index = {col: header.index(col) for col in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS) if col in header}

    envios: list[Envio] = []
    seen_ids: dict[str, int] = {}
    currencies: dict[str, int] = {}
    for row_no, cells in records:
        if not any(cell.strip() for cell in cells):
            continue
        if any(cell.strip() for cell in cells[len(header):]):
            raise LoadError(
                f"fila {row_no}: tiene {len(cells)} celdas pero el encabezado tiene {len(header)} "
                "(una coma dentro de un numero o de un texto sin comillas corre las columnas)"
            )
        envio = _parse_row(cells, index, row_no, delimiter)
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


def _records(reader: Any) -> Iterator[tuple[int, list[str]]]:
    """Registros del CSV con la linea donde empieza cada uno.

    Un CSV mal formado (comilla sin cerrar, texto despues de una comilla) levanta csv.Error en modo
    estricto; aca se vuelve LoadError con la linea de inicio, nunca una excepcion cruda ni filas
    tragadas dentro de un campo gigante.
    """
    while True:
        start = reader.line_num + 1
        try:
            cells = next(reader)
        except StopIteration:
            return
        except csv.Error as exc:
            raise LoadError(
                f"fila {start}: el CSV esta mal formado ({exc}); revisa comillas sin cerrar "
                "o una coma dentro de un texto sin comillas"
            ) from None
        yield start, cells


def _cell(cells: list[str], index: dict[str, int], col: str) -> str:
    i = index.get(col)
    if i is None or i >= len(cells):
        return ""
    return cells[i].strip()


def _error(row_no: int, col: str, motivo: str, raw: str) -> LoadError:
    return LoadError(f"fila {row_no}, columna {col}: {motivo} ({raw!r})")


# Decimal() acepta "1e999999999", "1_000" y "nan"; un archivo de fletes no. Solo digitos con un punto
# decimal opcional: lo demas es un export mal formateado y se rechaza en vez de adivinarse.
_PLAIN_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)")
# 45.000 es la forma en que un Excel es-CL guarda cuarenta y cinco mil; leerlo como 45 achica el dato
# 1000 veces sin avisar. Un grupo de tres digitos tras el punto, con el primer grupo sin cero a la
# izquierda, es un separador de miles ("0.750" y "12.5" siguen siendo decimales).
_DOT_THOUSANDS = re.compile(r"[+-]?[1-9][0-9]{0,2}(?:\.[0-9]{3})+")


def _decimal(raw: str, col: str, row_no: int, *, dots_group_thousands: bool) -> Decimal:
    if dots_group_thousands and _DOT_THOUSANDS.fullmatch(raw):
        raise _error(
            row_no, col,
            "parece usar el punto como separador de miles; exporta el numero sin formato de miles "
            "(45000, no 45.000)",
            raw,
        )
    if not _PLAIN_NUMBER.fullmatch(raw):
        raise _error(
            row_no, col, "no es un numero (usa punto decimal, sin separador de miles ni notacion cientifica)", raw
        )
    return Decimal(raw)


def _date(raw: str, col: str, row_no: int) -> date:
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        raise _error(row_no, col, "no es una fecha AAAA-MM-DD", raw)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise _error(row_no, col, "no es una fecha AAAA-MM-DD", raw) from None


def _parse_row(cells: list[str], index: dict[str, int], row_no: int, delimiter: str) -> Envio:
    def required(col: str) -> str:
        value = _cell(cells, index, col)
        if not value:
            raise _error(row_no, col, "vacia", value)
        return value

    def optional_date(col: str) -> date | None:
        value = _cell(cells, index, col)
        return _date(value, col, row_no) if value else None

    costo = _decimal(required("costo"), "costo", row_no, dots_group_thousands=True)
    if costo < 0:
        raise _error(row_no, "costo", "no puede ser negativo", str(costo))
    kg = _decimal(required("kg"), "kg", row_no, dots_group_thousands=delimiter == ";")
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
