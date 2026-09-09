"""Import-case engine: one shipment graph, many document projections (capability wedge).

Unifies fields extracted from logistics documents (invoice, packing list, B/L, ...)
into a single ``ImportCase``. Conflicts stay first-class — the engine never silently
picks a winner on quantity, weight, value, Incoterm, or origin. Chile import is the
first regime: a DIN field pack is a *projection* of the case, not a filed declaration.

Pure functions. No I/O, no LLM. Reference: ICC Incoterms 2020; Ellram TCO; WCO
idea of one data set, many views. Filing remains a licensed-broker HANDOFF.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class DocumentSlice:
    """One document's extracted fields (already schema-cleaned by the caller)."""

    doc_type: str
    fields: dict
    source: str = ""


@dataclass(frozen=True)
class Conflict:
    """The same canonical fact disagrees across two or more documents."""

    field: str
    values: tuple[tuple[str, str], ...]  # (doc_type_or_source, raw_value)


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


def _norm_token(raw: object) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    compact = text.replace(",", "")
    try:
        number = float(compact)
    except ValueError:
        return " ".join(text.casefold().split())
    if number != number:  # NaN
        return None
    return f"{number:.6f}".rstrip("0").rstrip(".")


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
        seen: dict[str, list[str]] = {}
        raw_by_doc: list[tuple[str, str]] = []
        for sl in docs:
            raw = _pick(sl.fields, aliases)
            if raw is None:
                continue
            token = _norm_token(raw)
            if token is None:
                continue
            seen.setdefault(token, []).append(sl.doc_type)
            raw_by_doc.append((sl.doc_type, raw))
        if not raw_by_doc:
            continue
        tokens = list(seen)
        if len(tokens) == 1:
            unified[key] = raw_by_doc[0][1]
            sources[key] = tuple(d for d, _ in raw_by_doc)
        else:
            conflicts.append(Conflict(key, tuple(raw_by_doc)))
            unified[key] = raw_by_doc[0][1]
            sources[key] = tuple(d for d, _ in raw_by_doc)

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
