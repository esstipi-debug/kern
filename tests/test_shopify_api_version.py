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


def test_pinned_version_is_2026_07():
    assert PINNED_VERSION == "2026-07"


def test_pin_is_the_version_that_requires_the_idempotent_directive():
    # 2026-04 onwards requires @idempotent on the inventory mutations; the
    # connector sends it, so the pin must not fall below that.
    assert PINNED_VERSION >= "2026-04"


from src.connectors.shopify_api_version import (  # noqa: E402
    EXPIRED,
    EXPIRING,
    SUPPORTED,
    check_pin,
    discover_versions,
    parse_versions,
)

_VERSIONS_PAYLOAD = {
    "publicApiVersions": [
        {"handle": "2025-10", "supported": True, "displayName": "2025-10"},
        {"handle": "2026-01", "supported": True, "displayName": "2026-01"},
        {"handle": "2026-07", "supported": True, "displayName": "2026-07"},
        {"handle": "unstable", "supported": False, "displayName": "unstable"},
    ]
}


def test_parse_versions_keeps_handle_and_support_flag():
    versions = parse_versions(_VERSIONS_PAYLOAD)
    assert {"handle": "2025-10", "supported": True} in versions
    assert len(versions) == 4


def test_parse_versions_rejects_an_unexpected_shape():
    with pytest.raises(ValueError, match="publicApiVersions"):
        parse_versions({"somethingElse": []})


def test_parse_versions_rejects_entries_without_a_handle():
    with pytest.raises(ValueError, match="handle"):
        parse_versions({"publicApiVersions": [{"supported": True}]})


def test_parse_versions_rejects_a_non_dict_data():
    with pytest.raises(ValueError, match="publicApiVersions"):
        parse_versions([{"handle": "2025-10", "supported": True}])


def test_parse_versions_rejects_a_non_dict_entry():
    with pytest.raises(ValueError, match="handle"):
        parse_versions({"publicApiVersions": ["2025-10"]})


def test_check_pin_reports_a_supported_version():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="2026-01") == SUPPORTED


def test_check_pin_reports_the_oldest_supported_version_as_expiring():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="2025-10") == EXPIRING


def test_check_pin_reports_an_absent_version_as_expired():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="2024-10") == EXPIRED


def test_check_pin_reports_an_unsupported_version_as_expired():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="unstable") == EXPIRED


def test_discover_versions_calls_graphql_and_parses():
    calls = []

    def fake_graphql(query, variables):
        calls.append((query, variables))
        return _VERSIONS_PAYLOAD

    versions = discover_versions(fake_graphql)
    assert len(versions) == 4
    assert "publicApiVersions" in calls[0][0]
