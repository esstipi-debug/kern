"""Shopify Admin API version pinning and drift detection.

Shopify releases an API version every quarter and supports each one for at least
twelve months. A request for a version that is no longer accessible does NOT
fail: Shopify *falls forward* and serves the request with the oldest accessible
stable version, returning 200. A stale pin therefore looks healthy while the
semantics underneath it silently move.

Two signals catch it, both from the API itself:

- ``X-Shopify-API-Version`` on every response carries the version actually used.
  Comparing it against the requested one detects drift that already happened.
- ``publicApiVersions`` lists the accessible versions and whether each is
  supported. It anticipates drift that has not happened yet (see
  ``discover_versions``).

This module owns the pin for BOTH Shopify connectors. It used to be duplicated
as ``_DEFAULT_API_VERSION`` in each of them, which is how a pin gets bumped in
one file and forgotten in the other.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_HEADER = "x-shopify-api-version"

#: The Admin API version both Shopify connectors request.
PINNED_VERSION = "2024-10"


class ShopifyVersionDriftError(RuntimeError):
    """Shopify served a different API version than the one requested.

    Raised on writes only. Writing stock or prices under the semantics of a
    version other than the one the code was written against puts bad data in
    the customer's system of record; a drifted read is recoverable.
    """


def served_version(response: Any) -> str | None:
    """Return the API version Shopify actually used, or None if unknowable.

    None means "no information", not "no drift": the offline stand-ins return
    objects without headers, and callers must treat that as a pass.
    """
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == _HEADER:
            return str(value)
    return None


def check_served(response: Any, *, requested: str, is_write: bool) -> None:
    """Raise on a drifted write, warn on a drifted read, pass when unknown."""
    served = served_version(response)
    if served is None or served == requested:
        return
    message = (
        f"Shopify API version drift: requested {requested!r} but the response was "
        f"served by {served!r}. The pinned version is most likely out of support and "
        f"Shopify is falling forward. Bump PINNED_VERSION in "
        f"src/connectors/shopify_api_version.py after verifying the operations in use."
    )
    if is_write:
        raise ShopifyVersionDriftError(message)
    logger.warning(message)


#: The pinned version is accessible and not the next one to be retired.
SUPPORTED = "SOPORTADA"
#: The pinned version is the oldest supported one, so it is next to be retired.
EXPIRING = "POR_VENCER"
#: The pinned version is not in the supported set. Shopify is falling forward.
EXPIRED = "VENCIDA"

VERSIONS_QUERY = """
query PublicApiVersions {
  publicApiVersions {
    handle
    supported
    displayName
  }
}
"""


def parse_versions(data: dict) -> list[dict]:
    """Normalise a ``publicApiVersions`` payload to ``[{handle, supported}]``."""
    try:
        rows = data.get("publicApiVersions")
    except AttributeError:
        raise ValueError(f"unexpected publicApiVersions payload: {data!r}")
    if not isinstance(rows, list):
        raise ValueError(f"unexpected publicApiVersions payload: {data!r}")
    versions = []
    for row in rows:
        try:
            handle = (row or {}).get("handle")
        except AttributeError:
            raise ValueError(f"publicApiVersions entry without a handle: {row!r}")
        if not handle:
            raise ValueError(f"publicApiVersions entry without a handle: {row!r}")
        versions.append({"handle": str(handle), "supported": bool(row.get("supported"))})
    return versions


def check_pin(versions: list[dict], *, pinned: str) -> str:
    """Classify the pin against the versions Shopify currently reports.

    ``publicApiVersions`` carries no dates, only a support flag. Since Shopify
    retires versions in release order, the OLDEST supported handle is by
    construction the next one to go -- that is what EXPIRING means here.
    """
    supported = sorted(v["handle"] for v in versions if v["supported"])
    if pinned not in supported:
        return EXPIRED
    if supported and pinned == supported[0]:
        return EXPIRING
    return SUPPORTED


def discover_versions(graphql) -> list[dict]:
    """Run ``publicApiVersions`` through any ``(query, variables) -> data`` callable.

    Deliberately NOT called at import time or on every request: it needs the
    network, and doing so would break the offline suite. Call it from
    ``scripts/check_shopify_api_version.py`` on a quarterly cadence.
    """
    return parse_versions(graphql(VERSIONS_QUERY, {}))
