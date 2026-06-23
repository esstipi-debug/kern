"""Logistics document reader (capability M16).

Extracts the high-value fields defined in ``doc_schemas`` from a logistics document
using a pluggable, LLMProvider-shaped model: Claude (PDF + citations) when an
``ANTHROPIC_API_KEY`` and the SDK are present, an inert fallback otherwise. The model
is injected, so the whole reader is exercised credential-free with a stub.

Architecture note: this is engine-layer code, so it must not statically depend on the
agent layer. The default model is fetched via a *lazy* import of ``scm_agent.llm`` and
any object with ``available()``/``extract()`` (the ``DocModel`` shape) can be injected.
When no model is available - or required fields are missing - ``extraction_outcome``
maps the result to a never-unprotected manual-entry handoff (Guided Execution Layer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from src.guided import GuidedOutcome, HandoffPacket, as_executed, as_handoff

from .doc_schemas import DocSchema, get_doc_schema, required_fields

# Sidecar key a model may use to return per-field provenance alongside the values.
_CITATIONS_KEY = "_citations"


@runtime_checkable
class DocModel(Protocol):
    """The slice of LLMProvider the reader needs (scm_agent.llm.LLMProvider fits)."""

    def available(self) -> bool: ...
    def extract(self, prompt: str, schema: dict) -> dict: ...


@dataclass(frozen=True)
class DocExtraction:
    """The structured result of reading one document against its schema."""

    doc_type: str
    fields: dict
    missing_required: tuple[str, ...]
    citations: dict = field(default_factory=dict)
    from_model: bool = True  # False when no model was available (pure manual entry needed)

    @property
    def complete(self) -> bool:
        return not self.missing_required


def build_extraction_prompt(schema: DocSchema, document_text: str) -> str:
    """Compose the extraction instruction for one document type."""
    lines = [
        f"You are reading a {schema.title}. Extract the following fields from the document "
        "verbatim; leave a field out if it is not present (do not guess).",
        "Fields:",
    ]
    for f in schema.fields:
        tag = " (required)" if f.required else ""
        desc = f" - {f.description}" if f.description else ""
        lines.append(f"- {f.name}{tag}{desc}")
    if schema.notes:
        lines.append(f"Notes: {schema.notes}")
    lines += ["", "Document:", document_text]
    return "\n".join(lines)


def _schema_dict(schema: DocSchema) -> dict:
    """A minimal JSON-schema view of the doc fields for the model's extract() call."""
    return {
        "type": "object",
        "properties": {
            f.name: {"type": "string", "description": f.description} for f in schema.fields
        },
        "required": list(required_fields(schema)),
    }


def _clean(raw: dict, schema: DocSchema) -> tuple[dict, dict]:
    """Keep only known, non-empty field values; split out (and filter) citations."""
    known = {f.name for f in schema.fields}
    citations = {k: c for k, c in dict(raw.get(_CITATIONS_KEY) or {}).items() if k in known}
    fields: dict = {}
    for k, v in raw.items():
        if k == _CITATIONS_KEY or k not in known:
            continue
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        fields[k] = v
    return fields, citations


def read_document(
    doc_type: str,
    document_text: str,
    *,
    provider: DocModel | None = None,
) -> DocExtraction:
    """Extract ``doc_type``'s schema fields from ``document_text`` using ``provider``.

    With no provider injected, lazily resolves the default (scm_agent.llm.get_provider).
    An unavailable model yields an empty extraction with every required field flagged.
    """
    schema = get_doc_schema(doc_type)  # validates the type up front
    model = provider if provider is not None else _default_model()
    required = tuple(required_fields(schema))

    if not model.available():
        return DocExtraction(doc_type, {}, required, {}, from_model=False)

    raw = model.extract(build_extraction_prompt(schema, document_text), _schema_dict(schema))
    fields, citations = _clean(raw or {}, schema)
    missing = tuple(f for f in required if f not in fields)
    return DocExtraction(doc_type, fields, missing, citations, from_model=True)


def extraction_outcome(extraction: DocExtraction, *, doc_ref: str = "") -> GuidedOutcome:
    """Map an extraction to a protected outcome: executed if complete, else a handoff."""
    ref = doc_ref or extraction.doc_type
    if extraction.complete:
        return as_executed(
            f"Read {ref}: extracted {len(extraction.fields)} field(s); all required present"
        )

    reason = "no document model available" if not extraction.from_model else "required fields missing"
    packet = HandoffPacket(
        title=f"Confirm/enter missing fields for {ref}",
        steps=[
            f"Automated extraction incomplete ({reason}).",
            "Open the source document and provide: " + ", ".join(extraction.missing_required) + ".",
        ],
        data={"extracted": dict(extraction.fields), "missing": list(extraction.missing_required)},
        risk_if_skipped="downstream steps (customs, demurrage, ETA) would run on incomplete document data",
    )
    return as_handoff(
        f"Read {ref}: {len(extraction.missing_required)} required field(s) need manual entry",
        [packet],
    )


def _default_model() -> DocModel:
    """Resolve the default model lazily, keeping the engine free of a static agent import."""
    try:
        from scm_agent.llm import get_provider

        return get_provider()
    except Exception:
        return _UnavailableModel()


class _UnavailableModel:
    def available(self) -> bool:
        return False

    def extract(self, prompt: str, schema: dict) -> dict:
        return {}
