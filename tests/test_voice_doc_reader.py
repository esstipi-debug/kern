"""Tests for the logistics document reader (capability M16, credential-free).

Extracts doc_schemas fields from a document via an injected LLMProvider-shaped model.
With no model available the result maps to a never-unprotected manual-entry handoff.
Fully exercised with a stub - no credentials, no network.
"""

import sys

import pytest

from src.guided import EXECUTED, HANDOFF, passed_guided
from src.voice.doc_reader import (
    DocExtraction,
    DocModel,
    _default_model,
    _UnavailableModel,
    extraction_outcome,
    read_document,
)
from src.voice.doc_schemas import get_doc_schema, required_fields


class _StubModel:
    """An injectable LLMProvider-shaped stub."""

    def __init__(self, payload: dict, *, available: bool = True) -> None:
        self._payload = payload
        self._available = available
        self.calls: list[tuple[str, dict]] = []

    def available(self) -> bool:
        return self._available

    def extract(self, prompt: str, schema: dict) -> dict:
        self.calls.append((prompt, schema))
        return dict(self._payload)


_BL_OK = {
    "bl_no": "MBL123",
    "container_no": "ABCU1234567",
    "vessel_voyage": "EVER GIVEN 0501E",
    "pol": "Shanghai",
    "pod": "Los Angeles",
}


def test_extracts_known_fields_and_drops_unknown_keys():
    stub = _StubModel({**_BL_OK, "unknown_x": "junk"})

    ext = read_document("bill_of_lading", "<doc text>", provider=stub)

    assert isinstance(ext, DocExtraction)
    assert ext.fields["bl_no"] == "MBL123"
    assert "unknown_x" not in ext.fields  # not in the schema -> dropped
    assert ext.from_model is True


def test_flags_missing_required_fields():
    stub = _StubModel({"bl_no": "MBL123"})  # required container_no/vessel_voyage/pol/pod absent

    ext = read_document("bill_of_lading", "<doc>", provider=stub)

    assert "bl_no" not in ext.missing_required
    assert set(ext.missing_required) == {"container_no", "vessel_voyage", "pol", "pod"}
    assert ext.complete is False


def test_empty_or_none_values_count_as_missing():
    stub = _StubModel({**_BL_OK, "bl_no": "", "container_no": None})

    ext = read_document("bill_of_lading", "<doc>", provider=stub)

    assert "bl_no" not in ext.fields and "container_no" not in ext.fields
    assert "bl_no" in ext.missing_required and "container_no" in ext.missing_required


def test_complete_extraction_reports_complete():
    ext = read_document("bill_of_lading", "<doc>", provider=_StubModel(_BL_OK))

    assert ext.missing_required == ()
    assert ext.complete is True


def test_unavailable_provider_yields_manual_entry_extraction():
    stub = _StubModel({}, available=False)

    ext = read_document("purchase_order", "<doc>", provider=stub)

    assert ext.from_model is False
    assert ext.fields == {}
    assert set(ext.missing_required) == set(required_fields(get_doc_schema("purchase_order")))
    assert stub.calls == []  # never asked the model when it is unavailable


def test_citations_sidecar_is_separated_from_fields():
    stub = _StubModel({**_BL_OK, "_citations": {"bl_no": "page 1, top-right", "zzz": "ignored"}})

    ext = read_document("bill_of_lading", "<doc>", provider=stub)

    assert "_citations" not in ext.fields
    assert ext.citations == {"bl_no": "page 1, top-right"}  # filtered to known fields


def test_prompt_carries_schema_title_and_required_fields():
    stub = _StubModel(_BL_OK)

    read_document("bill_of_lading", "BL BODY TEXT", provider=stub)

    prompt = stub.calls[0][0]
    assert "Bill of Lading" in prompt
    assert "bl_no" in prompt
    assert "BL BODY TEXT" in prompt


def test_unknown_doc_type_raises():
    with pytest.raises(KeyError):
        read_document("not_a_doc", "<doc>", provider=_StubModel({}))


def test_extraction_outcome_executed_when_complete():
    ext = read_document("bill_of_lading", "<doc>", provider=_StubModel(_BL_OK))

    outcome = extraction_outcome(ext, doc_ref="BL MBL123")

    assert outcome.status == EXECUTED
    assert passed_guided(outcome)


def test_extraction_outcome_hands_off_missing_fields():
    ext = read_document("bill_of_lading", "<doc>", provider=_StubModel({"bl_no": "MBL123"}))

    outcome = extraction_outcome(ext, doc_ref="BL MBL123")

    assert outcome.status == HANDOFF
    assert passed_guided(outcome)
    packet = outcome.handoffs[0]
    assert "container_no" in " ".join(packet.steps)
    assert packet.risk_if_skipped  # never-unprotected: risk stated


def test_extraction_outcome_hands_off_when_no_model():
    ext = read_document("purchase_order", "<doc>", provider=_StubModel({}, available=False))

    outcome = extraction_outcome(ext, doc_ref="PO 9001")

    assert outcome.status == HANDOFF
    assert passed_guided(outcome)


def test_default_model_resolves_to_a_docmodel():
    model = _default_model()  # constructs only; never calls the network

    assert isinstance(model, DocModel)


def test_unavailable_model_extracts_nothing():
    model = _UnavailableModel()

    assert model.available() is False
    assert model.extract("prompt", {}) == {}


def test_default_model_falls_back_gracefully_when_agent_layer_unimportable(monkeypatch):
    # Force the lazy `from scm_agent.llm import get_provider` to fail.
    monkeypatch.setitem(sys.modules, "scm_agent.llm", None)

    ext = read_document("purchase_order", "<doc>")  # no provider -> default path

    assert ext.from_model is False
    assert ext.fields == {}
