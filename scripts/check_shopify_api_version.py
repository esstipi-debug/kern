"""Report whether the pinned Shopify API version is still supported.

Needs live credentials, so it is NOT part of the test suite. Run it quarterly,
or after any Shopify changelog entry that touches the operations in use.

    SHOPIFY_SHOP_URL=https://acme.myshopify.com \
    SHOPIFY_ACCESS_TOKEN=shpat_... \
    uv run --no-sync python scripts/check_shopify_api_version.py
"""

from __future__ import annotations

import os
import sys

from src.connectors.shopify_api_version import (
    EXPIRED,
    EXPIRING,
    PINNED_VERSION,
    check_pin,
    discover_versions,
)


def main() -> int:
    shop = os.environ.get("SHOPIFY_SHOP_URL")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not shop or not token:
        print("SHOPIFY_SHOP_URL and SHOPIFY_ACCESS_TOKEN are required", file=sys.stderr)
        return 2

    import httpx

    client = httpx.Client(base_url=shop, headers={"X-Shopify-Access-Token": token}, timeout=30.0)
    path = f"/admin/api/{PINNED_VERSION}/graphql.json"

    def graphql(query: str, variables: dict) -> dict:
        resp = client.post(path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
        served = resp.headers.get("X-Shopify-API-Version")
        if served and served != PINNED_VERSION:
            print(f"DRIFT: requested {PINNED_VERSION}, served {served}")
        return body["data"]

    versions = discover_versions(graphql)
    status = check_pin(versions, pinned=PINNED_VERSION)
    supported = sorted(v["handle"] for v in versions if v["supported"])
    print(f"pin={PINNED_VERSION} status={status} supported={supported}")
    return 1 if status in (EXPIRED, EXPIRING) else 0


if __name__ == "__main__":
    raise SystemExit(main())
