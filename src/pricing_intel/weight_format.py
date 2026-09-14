"""Peso neto de un SKU a partir de su titulo, para catalogos chilenos de
alimentos congelados.

Modulo puro, sin I/O. Vive aparte de ``normalize.py`` -- el diseno original
lo puso como dos funciones hermanas de ``extract_pack_size``, pero la
especificacion real (cosechada de 1279 SKUs en 10 catalogos, ver
``docs/superpowers/specs/``) resulto ser un pipeline de ocho pasos con sus
propios lexicos, y meterlo en ``normalize.py`` habria enterrado el
normalizador de precios que ese archivo ya sostiene.

LA INVARIANTE
-------------
Si el peso no se deriva con certeza de evidencia literal, se devuelve
cuarentena con un motivo tipado. **Nunca 1 kg por defecto. Nunca promediar
un rango. Nunca convertir volumen a masa.** Es la gemela de la invariante
de ``src/regulatory/veda_calendar.py``: ningun atributo se afirma sin
evidencia literal.

TRES SALIDAS, NO DOS -- el mismo esqueleto de evidencia que ``Attribution``:

``kg``            peso neto exacto del SKU vendible.
``per_kg``        el catalogo publica precio POR KILO; no hay peso de pack
                  que inferir. 81 de los 96 SKUs de rapa-nui.cl caen aca, y
                  leer su "1kg" como envase da hasta 2x de error en ambos
                  sentidos -- es la trampa mas cara del universo cosechado.
``cuarentena``    con motivo tipado. Una tasa de cuarentena alta no es un
                  defecto: es la medicion. Un normalizador que reporte 95%
                  de cobertura sobre estos catalogos esta mintiendo en mas
                  de 300 SKUs.

DESVIACION DELIBERADA DE LA ESPECIFICACION
------------------------------------------
La espec ordena P1 (basis) antes de P2 (mascarado), pero su propio caso 22
--  ``"Camaron 36/40 Crudo Pelado desvenado  Imp.kilo"`` -> ``per_kg`` --
solo funciona al reves: la regla de "unidad desnuda sin numero en el
titulo" no puede dispararse mientras ``36/40`` siga siendo un numero
visible. Aca el basis por unidad desnuda se evalua DESPUES del mascarado.
El basis explicito ("(precio por kg)", nota del vendedor) si va antes,
porque no depende de que haya numeros.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

# --- lexicos. Se comparan contra el run alfabetico COMPLETO que sigue al
# numero, nunca por substring: "oz" esta dentro de "Gyozas", "lb" dentro de
# "Albacora", "cc" dentro de "Carpaccio" y "l" dentro de "LLIFEN". Son 28
# SKUs reales que una busqueda por substring convierte en unidades falsas.
_KG = {"kg", "kgs", "kgr", "kilo", "kilos", "kilogramo", "kilogramos"}
_G = {"g", "gr", "grs", "gramo", "gramos"}
_LB = {"lb", "lbs"}
_VOL = {"l", "lt", "lts", "litro", "litros", "ml", "cc", "oz"}
_CNT = {"u", "un", "unid", "unidad", "unidades", "uds", "hojas", "mangas",
        "bolsas", "barras", "tarros", "bidones", "cajas", "planchas", "rolls",
        "pack", "piezas", "empanaditas"}
_LEN = {"mm", "cm", "m", "mts", "metros"}
_MASA = _KG | _G | _LB

_CONT_OUTER = ("caja", "cajas", "manga", "mangas", "pallet", "master")
_CONT_SKU = ("bolsa", "saco", "balde", "bidon", "frasco", "pieza", "barra",
             "bandeja", "plancha", "sachet")

# Marcadores de peso NO exacto. Cada uno aparece con varias grafias en los
# catalogos reales; se comparan sobre el titulo ya plegado.
_VENENOS = (
    (r"\baprox\b|\baprox\.|\bapp\b|\bapp\.|\bpeso variable\b", "PESO_VARIABLE"),
    (r"\d\s*(?:kg|lb)?\s*-?\s*up\b", "PISO_ABIERTO"),
    (r"\bpallet\b", "MULTIPLE_EMPAQUE"),
)

_MIN_KG, _MAX_KG = Decimal("0.005"), Decimal("25")

_NUM = r"\d{1,4}(?:[.,]\d{1,3})?"
# El guion NO se excluye por la izquierda: el operando derecho de un rango
# ("170-220G") tiene que ser VISIBLE para poder clasificarlo como rango. Si
# el lookbehind lo tapa, el token desaparece y el SKU sale como "sin
# formato" en vez de "rango indeterminado" -- dos cuarentenas con motivos
# distintos, y solo una es la verdad.
_TOKEN_RE = re.compile(
    rf"(?<![\w.,/])(?P<n>{_NUM})[ ]{{0,2}}(?P<u>[A-Za-z]{{1,8}})\.?(?![A-Za-z])"
)
# "A x B<masa>" y "A unid* B<masa>": multiplicador. El operando derecho DEBE
# traer unidad de masa -- con B pelado es una dimension de corte (11x11) y no
# un conteo de nada.
_MULT_RE = re.compile(
    rf"(?<![\w.,])(?P<a>\d{{1,3}})\s*(?:unid(?:ades?)?|un|u)?\s*[x×*]\s*"
    rf"(?P<b>{_NUM})[ ]{{0,2}}(?P<bu>[A-Za-z]{{1,8}})\.?(?![A-Za-z])",
    re.IGNORECASE,
)
_BASIS_EXPLICITO_RE = re.compile(r"\(\s*precio por kg\s*\)|valor publicado por kilo")
_BARE_KG_RE = re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(sorted(_KG, key=len, reverse=True))})(?![A-Za-z])")


@dataclass(frozen=True)
class WeightOutcome:
    """Tres estados excluyentes, igual que ``Attribution``."""

    kg: Decimal | None = None
    per_kg: bool = False
    quarantine: str | None = None
    evidence: str | None = None

    @property
    def state(self) -> str:
        if self.quarantine:
            return "cuarentena"
        return "per_kg" if self.per_kg else "kg"


def _fold(text: str) -> str:
    plain = unicodedata.normalize("NFKC", text)
    plain = "".join(" " if unicodedata.category(ch) == "Zs" else ch for ch in plain)
    plain = plain.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", plain).strip().lower()


def _to_decimal(raw: str) -> Decimal | None:
    """Coma con 1-3 decimales y punto con 1-2 decimales son decimales. Punto
    con EXACTAMENTE 3 es separador de miles ambiguo y se rechaza: no existe
    ningun ``1.130 kg`` legitimo en la cosecha, asi que el rechazo no cuesta
    un solo SKU y bloquea el error de 1000x de ``1.000cc``."""
    if "." in raw and len(raw.split(".")[-1]) == 3:
        return None
    return Decimal(raw.replace(",", "."))


def _unit_kind(unit: str) -> str | None:
    u = unit.lower()
    if u in _KG:
        return "kg"
    if u in _G:
        return "g"
    if u in _LB:
        return "lb"
    if u in _VOL:
        return "vol"
    if u in _CNT:
        return "cnt"
    if u in _LEN:
        return "len"
    return None


_FACTOR = {"kg": Decimal(1), "g": Decimal("0.001"), "lb": Decimal("0.45359237")}


def _mask(text: str) -> str:
    """P2. Cada clase se reemplaza por un placeholder no numerico para que el
    resto del pipeline no dependa del orden de aparicion. El calibre con
    barra va PRIMERO: en ``"Camaron ... 36/40 - 1 Kg"`` consume el 40 y deja
    al ``- 1 Kg`` sin digito a la izquierda, que es lo que impide que el
    rango se lo lleve."""
    # ``(?!\d)`` en cada operando derecho NO es decorativo. Sin el, ``\d{1,3}``
    # retrocede: en "170-220G" prueba "220", la mirada adelante lo rechaza por
    # llevar masa, y entonces backtrackea a "22" -- dejando "0g" suelto, que
    # parsea como cero gramos y saca al SKU por fuera de rango en vez de
    # reportarlo como rango indeterminado. El ancla mata la clase entera.
    masa = "|".join(sorted(_MASA, key=len, reverse=True))
    masked = re.sub(r"\d{1,3}\s*/\s*\d{1,3}(?!\d)", " CAL ", text)               # P2.1 calibre
    masked = re.sub(r"\bu-?\d{1,3}\b|\bl[12]\b|\bcalibre \w+|\bgrado \d+|\d+ meses\b",
                    " CAL ", masked)                                             # P2.2
    masked = re.sub(rf"(?<![\w.,])\d{{1,3}}\s*[x×*]\s*\d{{1,3}}(?!\d)(?!\s*[.,]?\d*\s*(?:{masa})\b)",
                    " DIM ", masked)                                             # P2.3 dimension
    masked = re.sub(rf"(?<![\w.,])\d{{1,3}}\s*-\s*\d{{1,3}}(?!\d)(?!\s*(?:{masa})\b)",
                    " RNG ", masked)                                             # P2.4 rango sin masa
    return masked


def _levels(masked: str) -> tuple[list[tuple[Decimal, str]], list[tuple[Decimal, str]], bool]:
    """P4. Devuelve (T1, T2, hay_T3). T2 = adyacente a un contenedor externo
    (caja, manga): el peso de la caja, no el del SKU. T3 = rango con masa,
    que nunca produce respuesta por si solo."""
    t1: list[tuple[Decimal, str]] = []
    t2: list[tuple[Decimal, str]] = []
    t3 = False
    consumed: list[tuple[int, int]] = []

    for m in _MULT_RE.finditer(masked):
        if _unit_kind(m.group("bu")) not in ("kg", "g", "lb"):
            continue
        value = _to_decimal(m.group("b"))
        if value is None:
            continue
        total = Decimal(m.group("a")) * value * _FACTOR[_unit_kind(m.group("bu"))]
        (t2 if _near_outer(masked, m.start()) else t1).append((total, m.group(0).strip()))
        consumed.append(m.span())

    for m in _TOKEN_RE.finditer(masked):
        if any(a <= m.start() < b for a, b in consumed):
            continue
        kind = _unit_kind(m.group("u"))
        if kind not in ("kg", "g", "lb"):
            continue
        if re.search(r"\d{1,3}\s*-\s*$", masked[: m.start()]):
            t3 = True
            continue
        value = _to_decimal(m.group("n"))
        if value is None:
            continue
        (t2 if _near_outer(masked, m.start()) else t1).append(
            (value * _FACTOR[kind], m.group(0).strip())
        )
    return t1, t2, t3


def _near_outer(text: str, pos: int) -> bool:
    """Un peso pertenece al contenedor externo solo si la palabra que lo
    nombra esta en el MISMO segmento parentetico y a +-2 palabras.

    El parentesis no es cosmetico: en
    ``"Langostino entero u15 2kg. (caja x 12 kilos)"`` una ventana plana de
    dos palabras alcanza el ``(caja`` desde ``2kg.`` y clasifica los DOS
    pesos como peso de caja, dejando el nivel T1 vacio y devolviendo 12 kg
    -- seis veces el peso real. El parentesis es justamente lo que el
    comerciante uso para decir "esto de aca adentro es la caja".
    """
    segments: list[tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in "()":
            segments.append((start, i))
            start = i + 1
    segments.append((start, len(text)))
    lo, hi = next((a, b) for a, b in segments if a <= pos <= b)

    before = text[lo:pos].split()[-2:]
    after = text[pos:hi].split()[1:3]
    return any(w.strip("(),.") in _CONT_OUTER for w in before + after)


def weight_kg(title: str, *, description: str = "", feed_weight_kg: Decimal | None = None) -> WeightOutcome:
    """El pipeline completo. Ver el docstring del modulo para la invariante."""
    if not title:
        return WeightOutcome(quarantine="SIN_TITULO")

    decoded = html.unescape(title)
    if "&#" in decoded:                                                          # P0.1
        return WeightOutcome(quarantine="ENTIDAD_RESIDUAL")
    folded = _fold(decoded)
    folded = re.sub(r"\$\s*[\d.,]+|\d+\s*%", " MON ", folded)                    # P0.4

    if _BASIS_EXPLICITO_RE.search(folded) or _BASIS_EXPLICITO_RE.search(_fold(description)):
        return WeightOutcome(per_kg=True, evidence="precio por kilo declarado")

    masked = _mask(folded)

    for pattern, motivo in _VENENOS:                                             # P3
        if re.search(pattern, masked):
            return WeightOutcome(quarantine=motivo)
    # Dos contenedores externos DISTINTOS en un mismo titulo ("manga de 10 Kg
    # (10 Unidades por Caja)") significan que el titulo declara tres niveles
    # -- unidad, manga, caja -- como si los tres fueran "el formato". Cual de
    # los tres es el SKU vendible no esta en el texto. Un solo contenedor no
    # es ambiguo: "(caja x 12 kilos)" dice claramente que 12 kg es la caja.
    familias = {w.rstrip("s") for w in _CONT_OUTER if re.search(rf"\b{w}\b", masked)}
    if len(familias) > 1:
        return WeightOutcome(quarantine="MULTIPLE_EMPAQUE")

    t1, t2, t3 = _levels(masked)
    if not t1 and not t2:
        if _BARE_KG_RE.search(masked):                                            # basis por unidad desnuda
            return WeightOutcome(per_kg=True, evidence="unidad de kilo sin cantidad")
        return WeightOutcome(quarantine="RANGO_INDETERMINADO" if t3 else "SIN_FORMATO")

    nivel = t1 or t2
    if len(nivel) > 1:
        distintos = {kg for kg, _ in nivel}
        if len(distintos) > 1:
            return WeightOutcome(quarantine="MASAS_EN_CONFLICTO")
    kg, evidence = nivel[0]

    if not (_MIN_KG <= kg <= _MAX_KG):                                            # cota de plausibilidad
        return WeightOutcome(quarantine="FUERA_DE_RANGO")
    if feed_weight_kg is not None and feed_weight_kg > 0:                         # P6
        if abs(kg - feed_weight_kg) / feed_weight_kg > Decimal("0.02"):
            return WeightOutcome(quarantine="CONTRADICCION_FUENTE")
    return WeightOutcome(kg=kg, evidence=evidence)
