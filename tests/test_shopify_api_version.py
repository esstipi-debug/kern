"""Tests for src/connectors/shopify_api_version.py."""

from __future__ import annotations

import logging

import pytest

from src.connectors.shopify_api_version import (
    PINNED_VERSION,
    ShopifyVersionDriftError,
    check_served,
    served_version,
)


class _Resp:
    """Minimal stand-in for an httpx response: only what the checks read."""

    def __init__(self, headers: dict | None = None) -> None:
        if headers is not None:
            self.headers = headers


def test_served_version_reads_the_header():
    assert served_version(_Resp({"X-Shopify-API-Version": "2025-10"})) == "2025-10"


def test_served_version_is_case_insensitive():
    # httpx headers are case-insensitive; a plain dict is not, so we normalise.
    assert served_version(_Resp({"x-shopify-api-version": "2025-10"})) == "2025-10"


def test_served_version_is_none_without_the_header():
    assert served_version(_Resp({})) is None


def test_served_version_is_none_without_headers_at_all():
    # The offline stand-ins return plain objects with no .headers attribute.
    assert served_version(_Resp()) is None


def test_check_served_passes_when_versions_match():
    check_served(_Resp({"X-Shopify-API-Version": "2024-10"}), requested="2024-10", is_write=False)


def test_check_served_raises_on_a_drifted_write():
    resp = _Resp({"X-Shopify-API-Version": "2025-10"})
    with pytest.raises(ShopifyVersionDriftError, match="2024-10.*2025-10"):
        check_served(resp, requested="2024-10", is_write=True)


def test_check_served_only_warns_on_a_drifted_read(caplog):
    resp = _Resp({"X-Shopify-API-Version": "2025-10"})
    with caplog.at_level(logging.WARNING):
        check_served(resp, requested="2024-10", is_write=False)
    assert "2025-10" in caplog.text


def test_check_served_is_silent_without_a_header_even_on_writes():
    # Offline stand-ins must not be treated as drift, or the whole suite breaks.
    check_served(_Resp(), requested="2024-10", is_write=True)


def test_pinned_version_is_2024_10():
    assert PINNED_VERSION == "2024-10"
