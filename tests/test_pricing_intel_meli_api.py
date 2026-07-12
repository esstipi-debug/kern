"""Tests for src/pricing_intel/acquire/meli_api.py (Linchpin 3.0 PR-15).

No real network call ever happens here -- every httpx.Client is built on
httpx.MockTransport (same convention as tests/test_pricing_intel_pdp_fetcher.py).
Guarantees under test:
- MeliApiFetcher.fetch() round-trips a 200 JSON response into a
  RawObservation carrying the raw body text and status code;
- the identifiable User-Agent header is actually sent;
- a transport-level failure returns status_code=None/html=None, never raises;
- parse_meli_item_json() parses a frozen/realistic MELI item response into a
  correct MeliItemObservation (hand-verified price/currency/availability);
- an availability status other than "active" (or available_quantity == 0)
  resolves to OutOfStock, never fabricated InStock;
- a malformed body / API error envelope / missing required field raises
  MeliParseError, never a fabricated observation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from src.pricing_intel.acquire.base import RawObservation
from src.pricing_intel.acquire.meli_api import (
    MELI_API_CONFIDENCE,
    MELI_API_VERSION,
    MELI_DOMAIN,
    USER_AGENT,
    MeliApiFetcher,
    MeliItemObservation,
    MeliParseError,
    parse_meli_item_json,
)

FIXED_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)

_ACTIVE_ITEM = {
    "id": "MLA1234567890",
    "site_id": "MLA",
    "title": "Notebook Ejemplo 15 pulgadas",
    "price": 899999.99,
    "currency_id": "ARS",
    "available_quantity": 5,
    "sold_quantity": 120,
    "status": "active",
    "condition": "new",
    "permalink": "https://articulo.mercadolibre.com.ar/MLA-1234567890-notebook-ejemplo",
}

_PAUSED_ITEM = {
    "id": "MLA9999999999",
    "site_id": "MLA",
    "title": "Producto pausado",
    "price": 129990,
    "currency_id": "ARS",
    "available_quantity": 0,
    "status": "paused",
    "permalink": "https://articulo.mercadolibre.com.ar/MLA-9999999999-producto",
}

_ERROR_ENVELOPE = {"message": "Resource not found", "error": "not_found", "status": 404, "cause": []}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- MeliApiFetcher.fetch() -----------------------------------------------------


def test_fetch_returns_raw_observation_with_status_and_body_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/items/MLA1234567890"
        return httpx.Response(200, json=_ACTIVE_ITEM)

    fetcher = MeliApiFetcher(client=_client(handler), domain="meli-api.test")
    result = fetcher.fetch("MLA1234567890", now=FIXED_NOW)

    assert isinstance(result, RawObservation)
    assert result.status_code == 200
    assert result.sku_ref == "MLA1234567890"
    assert result.fetched_at == FIXED_NOW
    assert json.loads(result.html)["id"] == "MLA1234567890"


def test_fetch_hits_the_meli_domain_items_endpoint() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        return httpx.Response(200, json=_ACTIVE_ITEM)

    fetcher = MeliApiFetcher(client=_client(handler))
    fetcher.fetch("MLA1234567890", now=FIXED_NOW)
    assert seen["host"] == MELI_DOMAIN
    assert fetcher.domain == MELI_DOMAIN
    assert fetcher.tier == "L0"


def test_user_agent_is_sent_and_identifiable_not_spoofed() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=_ACTIVE_ITEM)

    MeliApiFetcher(client=_client(handler)).fetch("MLA1234567890", now=FIXED_NOW)
    assert seen["ua"] == USER_AGENT
    assert "Chrome" not in seen["ua"]
    assert "Mozilla" not in seen["ua"]


@pytest.mark.parametrize("status", [403, 429])
def test_blocking_status_codes_come_back_as_a_plain_observation_not_an_exception(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="blocked")

    result = MeliApiFetcher(client=_client(handler)).fetch("MLA1234567890", now=FIXED_NOW)
    assert isinstance(result, RawObservation)
    assert result.status_code == status


def test_transport_failure_returns_status_code_none_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = MeliApiFetcher(client=_client(handler)).fetch("MLA1234567890", now=FIXED_NOW)
    assert isinstance(result, RawObservation)
    assert result.status_code is None
    assert result.html is None


def test_fetched_at_defaults_to_now_when_not_supplied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ACTIVE_ITEM)

    before = datetime.now(timezone.utc)
    result = MeliApiFetcher(client=_client(handler)).fetch("MLA1234567890")
    after = datetime.now(timezone.utc)
    assert before <= result.fetched_at <= after


# -- parse_meli_item_json() ------------------------------------------------------


def test_parse_active_item_hand_verified() -> None:
    obs = parse_meli_item_json(json.dumps(_ACTIVE_ITEM), fetched_at=FIXED_NOW)
    assert isinstance(obs, MeliItemObservation)
    assert obs.item_id == "MLA1234567890"
    assert obs.site_id == "MLA"
    assert obs.price == Decimal("899999.99")
    assert obs.currency == "ARS"
    assert obs.availability == "InStock"
    assert obs.permalink == _ACTIVE_ITEM["permalink"]
    assert obs.fetched_at == FIXED_NOW


def test_parse_integer_price() -> None:
    obs = parse_meli_item_json(json.dumps(_PAUSED_ITEM), fetched_at=FIXED_NOW)
    assert obs.price == Decimal("129990")
    assert obs.currency == "ARS"


def test_paused_status_or_zero_quantity_resolves_out_of_stock() -> None:
    obs = parse_meli_item_json(json.dumps(_PAUSED_ITEM), fetched_at=FIXED_NOW)
    assert obs.availability == "OutOfStock"


def test_active_status_with_zero_available_quantity_resolves_out_of_stock() -> None:
    item = {**_ACTIVE_ITEM, "available_quantity": 0}
    obs = parse_meli_item_json(json.dumps(item), fetched_at=FIXED_NOW)
    assert obs.availability == "OutOfStock"


def test_unrecognized_status_conservatively_resolves_out_of_stock_never_raises() -> None:
    item = {**_ACTIVE_ITEM, "status": "under_review"}
    obs = parse_meli_item_json(json.dumps(item), fetched_at=FIXED_NOW)
    assert obs.availability == "OutOfStock"


def test_malformed_json_raises_meli_parse_error() -> None:
    with pytest.raises(MeliParseError):
        parse_meli_item_json("{not valid json", fetched_at=FIXED_NOW)


def test_non_object_json_raises_meli_parse_error() -> None:
    with pytest.raises(MeliParseError):
        parse_meli_item_json("[1, 2, 3]", fetched_at=FIXED_NOW)


def test_api_error_envelope_raises_meli_parse_error() -> None:
    with pytest.raises(MeliParseError, match="not_found|Resource not found"):
        parse_meli_item_json(json.dumps(_ERROR_ENVELOPE), fetched_at=FIXED_NOW)


@pytest.mark.parametrize("missing_field", ["id", "site_id", "price", "currency_id"])
def test_missing_required_field_raises_meli_parse_error(missing_field: str) -> None:
    item = dict(_ACTIVE_ITEM)
    del item[missing_field]
    with pytest.raises(MeliParseError):
        parse_meli_item_json(json.dumps(item), fetched_at=FIXED_NOW)


def test_unparseable_price_raises_meli_parse_error() -> None:
    item = {**_ACTIVE_ITEM, "price": "not-a-number", "currency_id": "ARS"}
    with pytest.raises(MeliParseError):
        parse_meli_item_json(json.dumps(item), fetched_at=FIXED_NOW)


def test_module_constants_are_documented_provenance_values() -> None:
    # Provenance (plan rule 7): extractor + version + confidence travel with
    # every observation this module's caller builds from a MeliItemObservation.
    assert MELI_API_VERSION == "1"
    assert MELI_API_CONFIDENCE == 1.0
