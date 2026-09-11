"""Import-case engine: one shipment graph, many document projections (capability wedge).

Unifies fields extracted from logistics documents (invoice, packing list, B/L, ...)
into a single ``ImportCase``. Conflicts stay first-class — the engine never silently
picks a winner on quantity, weight, value, Incoterm, or origin. Chile import is the
first regime: a DIN field pack is a *projection* of the case, not a filed declaration.

Pure functions. No I/O, no LLM. Reference: ICC Incoterms 2020; Ellram TCO; WCO
idea of one data set, many views. Filing remains a licensed-broker HANDOFF.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from src.voice.doc_schemas import DOC_TYPES, get_doc_schema, required_fields

# Canonical keys that should agree across documents. Values are source-field names
# looked up on each extraction (first hit wins as the candidate for that doc).
_CROSSWALK: dict[str, tuple[str, ...]] = {
    "incoterm": ("incoterm",),
    "gross_weight": ("gross_weight",),
    "country_of_origin": ("country_of_origin",),
    "hs_code": ("hs_code", "hts_code"),
    "bl_no": ("bl_no", "bl_or_awb_no"),
    "awb_no": ("awb_no",),
    "container_no": ("container_no",),
    "total_qty": ("total_qty", "qty"),
    "total_value": ("total_value", "entered_value"),
    "currency": ("currency",),
    "shipper": ("shipper", "seller", "exporter"),
    "consignee": ("consignee", "buyer", "importer_of_record"),
    "invoice_no": ("invoice_no",),
    "carton_count": ("carton_count", "packages"),
    "pol": ("pol", "origin_airport", "origin"),
    "pod": ("pod", "destination_airport", "destination", "port_of_entry"),
}

# Chile import pack: canonical key -> official-ish label (not a live SICEX form).
CHILE_DIN_PACK: tuple[tuple[str, str], ...] = (
    ("consignee", "importador"),
    ("shipper", "exportador"),
    ("incoterm", "incoterm"),
    ("country_of_origin", "pais_origen"),
    ("hs_code", "partida_arancelaria_propuesta"),
    ("total_value", "valor"),
    ("currency", "moneda"),
    ("total_qty", "cantidad"),
    ("carton_count", "bultos"),
    ("gross_weight", "peso_bruto"),
    ("bl_no", "conocimiento_embarque"),
    ("awb_no", "guia_aerea"),
    ("container_no", "contenedor"),
    ("invoice_no", "factura_comercial"),
    ("pol", "puerto_embarque"),
    ("pod", "puerto_destino"),
)

_CHILE_CORE_DOCS = frozenset({"commercial_invoice", "packing_list"})
_TRANSPORT_DOCS = frozenset({"bill_of_lading", "air_waybill"})

REGIME_CHILE_IMPORT = "chile_import"

# A number whose decimal convention cannot be recovered from the text alone gets
# this token prefix. The comparison token is the only thing that decides whether
# documents conflict, so an unreadable number must never produce a silent tie.
_AMBIGUOUS = "~ambiguous~"
_NUMERIC = re.compile(r"^([+-]?)(\d[\d.,]*)$")
_NON_FINITE = re.compile(r"^[+-]?(nan|inf(inity)?)$", re.IGNORECASE)

REASON_MISMATCH = "documents state different values for the same fact"
REASON_AMBIGUOUS = (
    "number format is ambiguous: a decimal comma cannot be told from a thousands "
    "separator ('1.234' is 1234 in Chile, 1.234 in the US) - confirm with the issuer"
)


@dataclass(frozen=True)
class DocumentSlice:
    """One document's extracted fields (already schema-cleaned by the caller)."""

    doc_type: str
    fields: dict
    source: str = ""


@dataclass(frozen=True)
class Conflict:
    """The same canonical fact cannot be settled from the documents alone."""

    field: str
    values: tuple[tuple[str, str], ...]  # (doc_type_or_source, raw_value)
    reason: str = ""


@dataclass(frozen=True)
class MissingItem:
    kind: str  # "document" | "field"
    name: str
    detail: str


@dataclass(frozen=True)
class PackRow:
    canonical: str
    label: str
    value: str
    sources: tuple[str, ...]
    contested: bool


@dataclass(frozen=True)
class ImportCase:
    regime: str
    documents: tuple[DocumentSlice, ...]
    unified: dict[str, str]
    sources: dict[str, tuple[str, ...]]
    conflicts: tuple[Conflict, ...]
    missing: tuple[MissingItem, ...]
    pack: tuple[PackRow, ...]
    n_documents: int
    n_conflicts: int
    n_missing: int
    copy_reduction_pct: float  # 5 of 6 docs projected => ~83 when core set present
    summary: str

    @property
    def complete_enough(self) -> bool:
        return not any(m.kind == "document" for m in self.missing)


def _grouped(parts: list[str]) -> bool:
    """True when parts read as thousands groups: 1-3 digits, then blocks of 3."""
    return (
        len(parts) > 1
        and all(p.isdigit() for p in parts)
        and 1 <= len(parts[0]) <= 3
        and all(len(p) == 3 for p in parts[1:])
    )


def _decimal_token(text: str) -> str | None:
    """Token for a plain ``123.45`` string, or None when it is not usable."""
    number = float(text)
    if not math.isfinite(number):  # NaN/overflow is not a fact
        return None
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _number_token(text: str) -> str | None:
    """Comparison token for a numeric string, or None when it is not a number.

    Both conventions are read, never assumed. With both separators present the
    LAST one is the decimal mark under either convention; a repeated separator
    can only be thousands grouping. What neither convention settles ('1.234' is
    1234 in Chile and 1.234 in the US) returns an ``_AMBIGUOUS`` token rather
    than a number, so it can never tie with a value that IS determined.
    """
    match = _NUMERIC.match(text.replace(" ", ""))
    if match is None:
        return None
    sign, body = match.groups()
    dots, commas = body.count("."), body.count(",")
    if not dots and not commas:
        return _decimal_token(sign + body)
    if dots and commas:
        decimal = "." if body.rfind(".") > body.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        head, _, tail = body.rpartition(decimal)
        if not tail.isdigit() or not _grouped(head.split(thousands)):
            return None  # a grouping neither convention writes: compare as text
        return _decimal_token(sign + head.replace(thousands, "") + "." + tail)
    separator = "." if dots else ","
    parts = body.split(separator)
    if len(parts) > 2:
        return _decimal_token(sign + body.replace(separator, "")) if _grouped(parts) else None
    head, tail = parts
    if len(tail) == 3 and len(head) <= 3 and not head.startswith("0"):
        return _AMBIGUOUS + sign + body  # '1.234' / '12,500': both readings legal
    return _decimal_token(sign + head + "." + tail)


def _norm_token(raw: object) -> str | None:
    """Token that decides whether two documents state the same fact."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if _NON_FINITE.match(text.replace(" ", "")):
        return None
    token = _number_token(text)
    if token is not None:
        return token
    return " ".join(text.casefold().split())


def _pick(fields: dict, aliases: tuple[str, ...]) -> str | None:
    for name in aliases:
        if name in fields and fields[name] not in (None, ""):
            token = _norm_token(fields[name])
            if token is not None:
                return str(fields[name]).strip()
    return None


def _required_gaps(slice_: DocumentSlice) -> list[MissingItem]:
    if slice_.doc_type not in DOC_TYPES:
        return [MissingItem("document", slice_.doc_type, "unknown document type")]
    schema = get_doc_schema(slice_.doc_type)
    missing = []
    for name in required_fields(schema):
        value = slice_.fields.get(name)
        if value is None or (isinstance(value, str) and not str(value).strip()):
            missing.append(MissingItem(
                "field", name, f"{slice_.doc_type} required field empty",
            ))
    return missing


def _chile_document_gaps(types_present: set[str]) -> list[MissingItem]:
    gaps = []
    for name in sorted(_CHILE_CORE_DOCS):
        if name not in types_present:
            gaps.append(MissingItem("document", name, "required for chile_import pack"))
    if not (types_present & _TRANSPORT_DOCS):
        gaps.append(MissingItem(
            "document", "bill_of_lading|air_waybill",
            "need an ocean B/L or air waybill for chile_import",
        ))
    return gaps


def _copy_reduction(types_present: set[str]) -> float:
    """Share of the six-doc set that can be projected once a master exists.

    The six: invoice, packing, transport (B/L or AWB), origin cert, customs pack,
    insurance/SI. Transport counts as present if either B/L or AWB is in. This is
    a product metric (copies avoided), not a clearance-automation claim.
    """
    six = (
        "commercial_invoice" in types_present,
        "packing_list" in types_present,
        bool(types_present & _TRANSPORT_DOCS),
        "certificate_of_origin" in types_present,
        True,  # customs pack is always projected from the case
        "booking_confirmation" in types_present,
    )
    filled = sum(1 for x in six if x)
    return round(100.0 * filled / 6.0, 1)


def merge_case(
    slices: list[DocumentSlice] | tuple[DocumentSlice, ...],
    *,
    regime: str = REGIME_CHILE_IMPORT,
) -> ImportCase:
    """Merge document slices into one case. Conflicts are listed, never auto-resolved."""
    docs = tuple(slices)
    types_present = {d.doc_type for d in docs}

    missing: list[MissingItem] = []
    for sl in docs:
        missing.extend(_required_gaps(sl))
    if regime == REGIME_CHILE_IMPORT:
        missing.extend(_chile_document_gaps(types_present))

    unified: dict[str, str] = {}
    sources: dict[str, tuple[str, ...]] = {}
    conflicts: list[Conflict] = []

    for key, aliases in _CROSSWALK.items():
        tokens: set[str] = set()
        raw_by_doc: list[tuple[str, str]] = []
        for sl in docs:
            raw = _pick(sl.fields, aliases)
            if raw is None:
                continue
            token = _norm_token(raw)
            if token is None:
                continue
            tokens.add(token)
            raw_by_doc.append((sl.doc_type, raw))
        if not raw_by_doc:
            continue
        unified[key] = raw_by_doc[0][1]
        sources[key] = tuple(d for d, _ in raw_by_doc)
        if len(tokens) > 1:
            conflicts.append(Conflict(key, tuple(raw_by_doc), REASON_MISMATCH))
        elif any(t.startswith(_AMBIGUOUS) for t in tokens):
            # One text, two legal readings: agreeing here would be an accident.
            conflicts.append(Conflict(key, tuple(raw_by_doc), REASON_AMBIGUOUS))

    pack = tuple(
        PackRow(
            canonical=canon,
            label=label,
            value=unified.get(canon, ""),
            sources=sources.get(canon, ()),
            contested=any(c.field == canon for c in conflicts),
        )
        for canon, label in CHILE_DIN_PACK
    )

    n_conf = len(conflicts)
    n_miss = len(missing)
    copy_pct = _copy_reduction(types_present)
    summary = (
        f"Import case ({regime}): {len(docs)} document(s), {n_conf} conflict(s), "
        f"{n_miss} missing item(s); estimated copy-reduction {copy_pct:.0f}% "
        f"(projections, not a filed declaration)."
    )
    return ImportCase(
        regime=regime,
        documents=docs,
        unified=unified,
        sources=sources,
        conflicts=tuple(conflicts),
        missing=tuple(missing),
        pack=pack,
        n_documents=len(docs),
        n_conflicts=n_conf,
        n_missing=n_miss,
        copy_reduction_pct=copy_pct,
        summary=summary,
    )
