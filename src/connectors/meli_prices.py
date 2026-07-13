"""MercadoLibre seller price writeback connector (Linchpin 3.0 PR-18, plan
section 7 P3 ``repricing_multichannel``) -- ``[CRED]``, offline-first.

**Distinct from PR-15's finding, not a re-litigation of it.** PR-15 found
that MercadoLibre's PUBLIC, UNAUTHENTICATED search/item API disallows
automated access per ``robots.txt`` (``Disallow: /`` on
``api.mercadolibre.com``) -- that finding is about READING a competitor's
public listings without credentials (``src/pricing_intel/acquire/meli_api.py``,
PR-15's ``L0`` competitor read). THIS module is a completely different,
sanctioned integration surface: a CLIENT authenticating with THEIR OWN
MercadoLibre seller account, via MercadoLibre's official Developers Program
OAuth flow, to update THEIR OWN listing prices. It is ``[CRED]``-gated
(needs the client's real OAuth app credentials, provisioned in their own
MercadoLibre developer account) exactly like Shopify Admin and Amazon
SP-API -- never compliance-blocked, never requested from the operator by
this module.

Mirrors ``src/connectors/odoo.py``'s two-layer split and
``src/connectors/shopify_prices.py``'s shape exactly:

- ``MeliClient`` is the real transport: MercadoLibre's authenticated seller
  Items API -- ``GET /users/{user_id}/items/search?sku=...`` (the
  documented ``seller_sku`` filter on a seller's OWN item search) to resolve
  a SKU to an ``item_id``, ``GET /items/{item_id}`` to read the current
  price, ``PUT /items/{item_id}`` (body ``{"price": ...}``) to write it --
  over any httpx-style object (``.get()``/``.put()``) pre-configured with
  ``base_url="https://api.mercadolibre.com"`` and an
  ``Authorization: Bearer <access_token>`` header from the client's own
  OAuth app. ``MeliClient`` performs no OAuth flow (authorization-code
  exchange, refresh) itself -- acquiring and refreshing that token is a
  deploy-time integration step, out of scope for this module (see repo
  Golden Rule 8/hard rules: no network/writeback I/O beyond this thin
  transport). VERIFY endpoint/field shapes against MercadoLibre's live
  Developers Program docs for the seller's marketplace (``MLA``/``MLM``/...)
  before pointing this at production -- filters and response shapes can
  differ slightly per site.
- ``MeliPriceStore`` holds the entire writeback ``store`` surface
  (read/applied_keys/claim/release/commit/rollback) and speaks only the
  ``MeliRPC`` duck type, so the whole connector runs and is tested offline
  against ``InMemoryMeli`` -- a real seller account (OAuth token) is needed
  only at deploy time.

``httpx`` (the ``repricing`` extra; already pinned by the ``tower`` and
``pricing-intel`` extras) is imported lazily and only required by
``MeliClient`` -- ``InMemoryMeli``/``MeliPriceStore`` have zero extra
dependencies and are exactly what the test suite exercises.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src import writeback

try:  # optional: the 'repricing' extra (httpx already pinned by tower/pricing-intel)
    import httpx  # noqa: F401  (imported to prove availability; the caller builds the client)

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_WB_TARGET = "meli"


class MeliPricesError(RuntimeError):
    """A MercadoLibre price read/write failed (transport, auth, or unknown SKU)."""


@runtime_checkable
class MeliRPC(Protocol):
    """The two operations the connector needs against a seller's account."""

    def find_item_by_sku(self, sku: str) -> dict | None: ...

    def update_item_price(self, item_id: str, price: float) -> None: ...


class MeliClient:
    """Real MercadoLibre seller Items API transport over any httpx-style object.

    ``http`` should already be configured with
    ``base_url="https://api.mercadolibre.com"`` and an
    ``Authorization: Bearer <access_token>`` header carrying the client's own
    OAuth access token (``[CRED]``, deploy-time wiring only -- see module
    docstring). ``user_id`` is the seller's own MercadoLibre user id (the
    item-search endpoint is scoped per seller).
    """

    def __init__(self, http: Any, *, user_id: str) -> None:
        if not _HAS_HTTPX:
            raise MeliPricesError(
                "httpx is required for the live MercadoLibre transport -- install the 'repricing' "
                "extra (`pip install .[repricing]`) or use InMemoryMeli for offline use"
            )
        self._http = http
        self._user_id = user_id

    def find_item_by_sku(self, sku: str) -> dict | None:
        search = self._http.get(f"/users/{self._user_id}/items/search", params={"sku": sku})
        search.raise_for_status()
        results = (search.json() or {}).get("results") or []
        if not results:
            return None
        item_id = results[0]
        item_resp = self._http.get(f"/items/{item_id}")
        item_resp.raise_for_status()
        item = item_resp.json()
        return {"item_id": item["id"], "price": float(item["price"])}

    def update_item_price(self, item_id: str, price: float) -> None:
        resp = self._http.put(f"/items/{item_id}", json={"price": price})
        resp.raise_for_status()
        body = resp.json() or {}
        if "error" in body:
            raise MeliPricesError(f"MercadoLibre rejected the price update: {body}")


class MeliPriceStore:
    """writeback system-of-record surface (read/applied_keys/claim/release/
    commit/rollback) over MercadoLibre item prices, addressed by SKU. Lets
    the connector reuse the ENTIRE safe-staging plane unchanged, exactly as
    ``src.connectors.odoo``'s ``_ReorderRuleStore`` does for Odoo reorder
    rules -- the only thing that differs per channel is this class.

    ``ledger``, when given (a ``src.writeback_store.SqliteAuditLedger``),
    persists the audit/idempotency bookkeeping across a process restart --
    matching every other store in this repo.
    """

    def __init__(self, rpc: MeliRPC, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._audit = writeback.AuditBookkeeping(ledger)

    def read(self, entity_id: str) -> dict:
        item = self._rpc.find_item_by_sku(entity_id)
        return {} if item is None else {"price": float(item["price"])}

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
                item = self._rpc.find_item_by_sku(c.entity_id)
                if item is None:
                    raise MeliPricesError(f"unknown SKU {c.entity_id!r} in MercadoLibre (no item match)")
                restore.append((c.entity_id, c.field, item["price"]))
                self._rpc.update_item_price(item["item_id"], float(c.after))
        except Exception:
            # Compensating undo of whatever landed in THIS call, same pattern as
            # ShopifyPriceStore/OdooConnector's _ReorderRuleStore -- MercadoLibre has
            # no cross-call transaction either.
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
            item = self._rpc.find_item_by_sku(entity_id)
            if item is None:
                raise MeliPricesError(f"cannot restore: SKU {entity_id!r} no longer exists in MercadoLibre")
            self._rpc.update_item_price(item["item_id"], float(original))


# -- offline stand-in (the MercadoLibre analogue of InMemoryOdoo) --------------


class InMemoryMeli:
    """Offline stand-in for a MercadoLibre seller account: the slice of the
    Items API the connector uses. Holds items keyed by SKU (``{sku:
    {"item_id": str, "price": float}}``). Lets the whole MercadoLibre price
    connector run and be tested end-to-end with no network or OAuth token --
    the MercadoLibre analogue of ``src.connectors.odoo.InMemoryOdoo``.
    """

    def __init__(self, items: dict[str, dict] | None = None) -> None:
        self._items: dict[str, dict] = {sku: dict(v) for sku, v in (items or {}).items()}

    def find_item_by_sku(self, sku: str) -> dict | None:
        v = self._items.get(sku)
        return None if v is None else {"item_id": v["item_id"], "price": float(v["price"]), "sku": sku}

    def update_item_price(self, item_id: str, price: float) -> None:
        for v in self._items.values():
            if v["item_id"] == item_id:
                v["price"] = float(price)
                return
        raise MeliPricesError(f"unknown item_id {item_id!r}")


def demo_meli() -> InMemoryMeli:
    """A small, deterministic in-memory MercadoLibre seller account for demos
    and tests (no randomness) -- same SKUs/prices as
    ``src.connectors.odoo.demo_odoo()`` so a multichannel repricing demo can
    reprice the "same" catalog everywhere."""
    return InMemoryMeli(
        {
            "SKU-1": {"item_id": "MLA100000001", "price": 20.0},
            "SKU-2": {"item_id": "MLA100000002", "price": 50.0},
            "SKU-3": {"item_id": "MLA100000003", "price": 8.0},
        }
    )
