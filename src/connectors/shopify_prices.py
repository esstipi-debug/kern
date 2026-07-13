"""Shopify Admin API price writeback connector (Linchpin 3.0 PR-18, plan
section 7 P3 ``repricing_multichannel``) -- ``[CRED]``, offline-first.

Mirrors ``src/connectors/odoo.py``'s two-layer split exactly:

- ``ShopifyClient`` is the real transport: Shopify's GraphQL Admin API
  (``productVariants(query: "sku:...")`` to resolve a SKU to its variant +
  parent product, ``productVariantsBulkUpdate`` to write the price) over any
  httpx-style object (``.post(url, json=...) -> Response``) -- the same
  "hand it any httpx-style transport" convention as
  ``src/connectors/http_client.py::StoreApiClient``. The caller
  pre-configures that transport with the shop's base URL
  (``https://<shop>.myshopify.com``) and an ``X-Shopify-Access-Token``
  header carrying the client's own custom/private-app access token --
  ``ShopifyClient`` performs no auth or token exchange itself. ``[CRED]``:
  Shopify's Admin API is per-store, app-scoped credentials the client
  provisions in their own admin -- never requested from the operator by this
  module; wiring a real token is a deploy-time step, not a code change here.
  The GraphQL field/mutation names are current as of API version 2024-10 at
  design time -- Shopify revises its Admin API on a quarterly release
  schedule, so VERIFY the field/mutation shapes against the live API docs
  for the shop's actual API version before pointing this at production.
- ``ShopifyPriceStore`` holds the entire writeback ``store`` surface
  (read/applied_keys/claim/release/commit/rollback) and speaks only the
  ``ShopifyRPC`` duck type, so the whole connector runs and is tested
  offline against ``InMemoryShopify`` -- a real shop (URL + access token) is
  needed only at deploy time. This is the SAME connector-independence
  pattern ``OdooConnector``/``_ReorderRuleStore`` established: the safety
  plane (risk tiers, signed Approval, idempotency, audit, rollback) is 100%
  reused from ``src.writeback``, unchanged.

``httpx`` (the ``repricing`` extra; already pinned by the ``tower`` and
``pricing-intel`` extras) is imported lazily and only required by
``ShopifyClient`` -- ``InMemoryShopify``/``ShopifyPriceStore`` have zero
extra dependencies and are exactly what the test suite exercises.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src import writeback

try:  # optional: the 'repricing' extra (httpx already pinned by tower/pricing-intel)
    import httpx  # noqa: F401  (imported to prove availability; the caller builds the client)

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_WB_TARGET = "shopify"
_DEFAULT_API_VERSION = "2024-10"


class ShopifyPricesError(RuntimeError):
    """A Shopify price read/write failed (transport, auth, or unknown SKU)."""


@runtime_checkable
class ShopifyRPC(Protocol):
    """The two operations the connector needs against a Shopify shop."""

    def find_variant_by_sku(self, sku: str) -> dict | None: ...

    def update_variant_price(self, variant_id: str, price: float) -> None: ...


class ShopifyClient:
    """Real Shopify Admin GraphQL transport over any httpx-style object.

    ``http`` should already be configured with the shop's base URL and
    ``X-Shopify-Access-Token`` header (``[CRED]``, deploy-time wiring only --
    see module docstring). Caches variant_id -> product_id internally
    (``productVariantsBulkUpdate`` needs the owning product) so
    ``update_variant_price`` never has to re-query it, mirroring
    ``OdooConnector``'s own ``_sku_by_id``/``_id_by_sku`` caches.
    """

    def __init__(self, http: Any, *, api_version: str = _DEFAULT_API_VERSION) -> None:
        if not _HAS_HTTPX:
            raise ShopifyPricesError(
                "httpx is required for the live Shopify transport -- install the 'repricing' extra "
                "(`pip install .[repricing]`) or use InMemoryShopify for offline use"
            )
        self._http = http
        self._graphql_path = f"/admin/api/{api_version}/graphql.json"
        self._product_id_by_variant: dict[str, str] = {}

    def _graphql(self, query: str, variables: dict) -> dict:
        resp = self._http.post(self._graphql_path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise ShopifyPricesError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]

    def find_variant_by_sku(self, sku: str) -> dict | None:
        query = """
        query FindVariantBySku($q: String!) {
          productVariants(first: 1, query: $q) {
            edges { node { id price sku product { id } } }
          }
        }
        """
        data = self._graphql(query, {"q": f"sku:{sku}"})
        edges = data.get("productVariants", {}).get("edges", [])
        if not edges:
            return None
        node = edges[0]["node"]
        variant_id = node["id"]
        self._product_id_by_variant[variant_id] = node["product"]["id"]
        return {"variant_id": variant_id, "price": float(node["price"]), "sku": node["sku"]}

    def update_variant_price(self, variant_id: str, price: float) -> None:
        product_id = self._product_id_by_variant.get(variant_id)
        if product_id is None:
            raise ShopifyPricesError(
                f"unknown product for variant {variant_id!r} -- call find_variant_by_sku first "
                "(ShopifyPriceStore always does; a variant_id used outside that flow hits this)"
            )
        mutation = """
        mutation UpdateVariantPrice($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            userErrors { field message }
          }
        }
        """
        data = self._graphql(
            mutation,
            {"productId": product_id, "variants": [{"id": variant_id, "price": str(price)}]},
        )
        errors = (data.get("productVariantsBulkUpdate") or {}).get("userErrors") or []
        if errors:
            raise ShopifyPricesError(f"Shopify rejected the price update: {errors}")


class ShopifyPriceStore:
    """writeback system-of-record surface (read/applied_keys/claim/release/
    commit/rollback) over Shopify variant prices, addressed by SKU. Lets the
    connector reuse the ENTIRE safe-staging plane unchanged, exactly as
    ``src.connectors.odoo``'s ``_ReorderRuleStore`` does for Odoo reorder
    rules -- the only thing that differs per channel is this class.

    ``ledger``, when given (a ``src.writeback_store.SqliteAuditLedger``),
    persists the audit/idempotency bookkeeping across a process restart --
    matching every other store in this repo (``InMemoryStore``,
    ``ExcelWorkbookStore``, ``_ReorderRuleStore``).
    """

    def __init__(self, rpc: ShopifyRPC, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._audit = writeback.AuditBookkeeping(ledger)

    def read(self, entity_id: str) -> dict:
        variant = self._rpc.find_variant_by_sku(entity_id)
        return {} if variant is None else {"price": float(variant["price"])}

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        try:
            for c in changeset.changes:
                if c.is_noop:
                    continue
                variant = self._rpc.find_variant_by_sku(c.entity_id)
                if variant is None:
                    raise ShopifyPricesError(f"unknown SKU {c.entity_id!r} in Shopify (no variant match)")
                restore.append((c.entity_id, c.field, variant["price"]))
                self._rpc.update_variant_price(variant["variant_id"], float(c.after))
        except Exception:
            # Mirrors OdooConnector's _ReorderRuleStore: undo whatever landed in THIS
            # call before propagating, since Shopify has no cross-call transaction to
            # lean on -- a local compensating write, not a swallow (see odoo.py's
            # identical note for the residual-failure case).
            self._apply_restore(restore)
            raise
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._apply_restore(entry.restore)
        self._audit.forget(idempotency_key)

    def _apply_restore(self, restore: list[tuple[str, str, object]] | tuple[tuple[str, str, object], ...]) -> None:
        for entity_id, _field, original in restore:
            variant = self._rpc.find_variant_by_sku(entity_id)
            if variant is None:
                raise ShopifyPricesError(f"cannot restore: SKU {entity_id!r} no longer exists in Shopify")
            self._rpc.update_variant_price(variant["variant_id"], float(original))


# -- offline stand-in (the Shopify analogue of InMemoryOdoo) -------------------


class InMemoryShopify:
    """Offline stand-in for a Shopify shop: the slice of Admin API behavior
    the connector uses. Holds variants keyed by SKU (``{sku: {"variant_id":
    str, "price": float}}``). Lets the whole Shopify price connector run and
    be tested end-to-end with no network or access token -- the Shopify
    analogue of ``src.connectors.odoo.InMemoryOdoo``.
    """

    def __init__(self, variants: dict[str, dict] | None = None) -> None:
        self._variants: dict[str, dict] = {sku: dict(v) for sku, v in (variants or {}).items()}

    def find_variant_by_sku(self, sku: str) -> dict | None:
        v = self._variants.get(sku)
        return None if v is None else {"variant_id": v["variant_id"], "price": float(v["price"]), "sku": sku}

    def update_variant_price(self, variant_id: str, price: float) -> None:
        for v in self._variants.values():
            if v["variant_id"] == variant_id:
                v["price"] = float(price)
                return
        raise ShopifyPricesError(f"unknown variant_id {variant_id!r}")


def demo_shopify() -> InMemoryShopify:
    """A small, deterministic in-memory Shopify shop for demos and tests (no
    randomness) -- same SKUs/prices as ``src.connectors.odoo.demo_odoo()`` so
    a multichannel repricing demo can reprice the "same" catalog everywhere."""
    return InMemoryShopify(
        {
            "SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0},
            "SKU-2": {"variant_id": "gid://shopify/ProductVariant/2", "price": 50.0},
            "SKU-3": {"variant_id": "gid://shopify/ProductVariant/3", "price": 8.0},
        }
    )
