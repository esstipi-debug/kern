"""SAP Business One Service Layer price writeback connector -- ``[CRED]``, offline-first.

The second ERP behind the same writeback plane as Odoo, and the point of it is
that the plane did not change: risk tiers, signed ``Approval``, idempotency
claim, audit and exact rollback all come from ``nosilo_core.writeback``
untouched. Only this file knows what SAP is.

Two layers, mirroring ``src/connectors/odoo.py`` and
``src/connectors/shopify_prices.py``:

- ``SapB1Client`` is the real transport: the Service Layer's OData v4 REST API
  (``POST /Login`` for a session, ``GET /Items('<ItemCode>')`` to read,
  ``PATCH /Items('<ItemCode>')`` to write) over any httpx-style object
  (``.post/.get/.patch(url, json=...) -> Response``), the same convention as
  ``src/connectors/http_client.py::StoreApiClient``. The caller pre-configures
  that transport with the Service Layer base URL (typically
  ``https://<host>:50000/b1s/v1``).

- ``SapB1PriceStore`` holds the entire writeback store surface and speaks only
  the ``SapB1RPC`` duck type, so the connector runs and is tested offline
  against ``InMemorySapB1``. A real B1 instance is needed at deploy time only.

Three things SAP does differently from the connectors already here, each of
which is a behavior in this file rather than a caveat in a doc:

1. **Sessions, not tokens.** Shopify takes a long-lived access-token header;
   B1 requires an explicit ``POST /Login`` that returns a ``B1SESSION`` cookie
   which *expires* (30 minutes by default). ``SapB1Client`` logs in lazily and
   retries exactly once on a 401, because a session timing out mid-run is the
   normal case, not an error worth surfacing to an operator.

2. **Prices are a collection, not a field.** An item carries one price per
   price list (``ItemPrices: [{PriceList, Price}, ...]``). "The price" is
   meaningless without saying which list, so ``price_list`` is a required-by-
   default constructor argument rather than a hidden constant -- writing to the
   wrong list is a silent, plausible-looking corruption.

3. **PATCH returns 204 with no body.** Success carries no echo of the written
   value, so ``commit`` captures the pre-image from the read it already did,
   exactly like the other connectors, instead of trusting a response.

``httpx`` (the ``repricing`` extra) is imported lazily and only needed by
``SapB1Client``; ``InMemorySapB1``/``SapB1PriceStore`` have zero extra
dependencies and are what the suite exercises. ``[CRED]``: Service Layer
credentials (CompanyDB / user / password) are the client's own, provisioned in
their B1 install -- never requested by this module, and wiring them is a
deploy-time step, not a code change here.

The Service Layer entity/field shapes below (``Items``, ``ItemCode``,
``ItemPrices``, ``PriceList``, ``Price``) are those of Service Layer v1 for
B1 10.0 at design time. VERIFY them against the customer's actual B1 version
before pointing this at production -- SAP revises the Service Layer per
release, the same caveat ``shopify_prices.py`` carries for the Admin API.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from nosilo_core import writeback

try:  # optional: the 'repricing' extra
    import httpx  # noqa: F401  (imported to prove availability; the caller builds the client)

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_WB_TARGET = "sap_b1"
_DEFAULT_PRICE_LIST = 1


class SapB1Error(RuntimeError):
    """A SAP B1 Service Layer read/write failed (transport, auth, or unknown item)."""


@runtime_checkable
class SapB1RPC(Protocol):
    """The two operations the connector needs against a B1 company database."""

    def find_item_by_code(self, item_code: str, price_list: int) -> dict | None: ...

    def update_item_price(self, item_code: str, price_list: int, price: float) -> None: ...


class SapB1Client:
    """Real Service Layer transport over any httpx-style object.

    ``http`` should already carry the Service Layer base URL. Unlike the Shopify
    client, this one authenticates itself: B1 has no static token, only a login
    that mints an expiring session cookie.
    """

    def __init__(self, http: Any, *, company_db: str, username: str, password: str) -> None:
        if not _HAS_HTTPX:
            raise SapB1Error(
                "httpx is required for the live SAP B1 transport -- install the 'repricing' extra "
                "(`pip install .[repricing]`) or use InMemorySapB1 for offline use"
            )
        self._http = http
        self._credentials = {"CompanyDB": company_db, "UserName": username, "Password": password}
        self._logged_in = False

    def login(self) -> None:
        """Open a Service Layer session. The session cookie is kept by the
        transport's own cookie jar, which is why nothing is returned here."""
        resp = self._http.post("/Login", json=self._credentials)
        if resp.status_code >= 400:
            # Deliberately does not echo the response body: a failed B1 login
            # can quote the payload back, and that payload holds the password.
            raise SapB1Error(f"SAP B1 login failed with HTTP {resp.status_code}")
        self._logged_in = True

    def _request(self, method: str, path: str, **kwargs) -> Any:
        """Issue a request, logging in first and retrying once on a 401.

        The retry is the whole reason this wrapper exists: B1 sessions expire on
        a timer, so a long-running job WILL meet a 401 that means "log in again",
        not "your credentials are wrong". Retrying exactly once distinguishes
        the two -- a second 401 is a real auth failure and propagates.
        """
        if not self._logged_in:
            self.login()
        resp = getattr(self._http, method)(path, **kwargs)
        if resp.status_code == 401:
            self._logged_in = False
            self.login()
            resp = getattr(self._http, method)(path, **kwargs)
        return resp

    def find_item_by_code(self, item_code: str, price_list: int) -> dict | None:
        resp = self._request("get", f"/Items('{item_code}')", params={"$select": "ItemCode,ItemPrices"})
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise SapB1Error(f"SAP B1 item read failed with HTTP {resp.status_code}")
        payload = resp.json()
        for row in payload.get("ItemPrices") or []:
            if int(row.get("PriceList", -1)) == int(price_list):
                price = row.get("Price")
                if price is None:
                    return None
                return {"item_code": item_code, "price": float(price), "price_list": int(price_list)}
        # The item exists but carries no price on the requested list. That is not
        # the same as "no such item", and it must not be reported as one: the
        # caller would create a price on a list the client never configured.
        raise SapB1Error(
            f"item {item_code!r} has no price on price list {price_list} -- "
            "configure the list in B1 before repricing against it"
        )

    def update_item_price(self, item_code: str, price_list: int, price: float) -> None:
        resp = self._request(
            "patch",
            f"/Items('{item_code}')",
            json={"ItemPrices": [{"PriceList": int(price_list), "Price": float(price)}]},
        )
        # Service Layer answers a successful PATCH with 204 No Content.
        if resp.status_code not in (200, 204):
            raise SapB1Error(f"SAP B1 rejected the price update with HTTP {resp.status_code}")


class SapB1PriceStore:
    """writeback system-of-record surface (read/applied_keys/claim/release/
    commit/rollback) over SAP B1 item prices, addressed by ItemCode.

    ``price_list`` names which of the item's price lists this store owns; it
    defaults to 1 (B1's own default list) but is worth passing explicitly,
    since writing the right number to the wrong list looks exactly like success.

    ``ledger``, when given (a ``nosilo_core.writeback_store.SqliteAuditLedger``),
    persists the audit/idempotency bookkeeping across a process restart, like
    every other store in this repo.
    """

    def __init__(
        self,
        rpc: SapB1RPC,
        *,
        price_list: int = _DEFAULT_PRICE_LIST,
        ledger: object | None = None,
    ) -> None:
        self._rpc = rpc
        self._price_list = int(price_list)
        self._audit = writeback.AuditBookkeeping(ledger)

    @property
    def target(self) -> str:
        """Writeback target label, price list included -- two stores over the
        same company DB on different lists are different targets in the audit."""
        return f"{_WB_TARGET}:list-{self._price_list}"

    def read(self, entity_id: str) -> dict:
        item = self._rpc.find_item_by_code(entity_id, self._price_list)
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
                item = self._rpc.find_item_by_code(c.entity_id, self._price_list)
                if item is None:
                    raise SapB1Error(f"unknown ItemCode {c.entity_id!r} in SAP B1")
                restore.append((c.entity_id, c.field, item["price"]))
                self._rpc.update_item_price(c.entity_id, self._price_list, float(c.after))
        except Exception:
            # Same compensating undo as the Odoo and Shopify stores: the Service
            # Layer gives no cross-request transaction, so a partial batch is
            # rolled back locally before the error propagates -- a compensating
            # write, not a swallow.
            self._apply_restore(restore)
            raise
        entry = writeback.AuditEntry(
            changeset.idempotency_key, changeset.target, approved_by, tuple(restore)
        )
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._apply_restore(entry.restore)
        self._audit.forget(idempotency_key)

    def _apply_restore(
        self, restore: list[tuple[str, str, object]] | tuple[tuple[str, str, object], ...]
    ) -> None:
        for entity_id, _field, original in restore:
            if self._rpc.find_item_by_code(entity_id, self._price_list) is None:
                raise SapB1Error(f"cannot restore: ItemCode {entity_id!r} no longer exists in SAP B1")
            self._rpc.update_item_price(entity_id, self._price_list, float(original))


# -- offline stand-in (the B1 analogue of InMemoryOdoo / InMemoryShopify) ------


class InMemorySapB1:
    """Offline stand-in for a B1 company database: the slice of Service Layer
    behavior the connector uses.

    Items are held as ``{item_code: {price_list: price}}`` -- a mapping per
    item, not a single number, because modelling B1's multi-list pricing as one
    field is exactly the mistake this connector exists to avoid.
    """

    def __init__(self, items: dict[str, dict[int, float]] | None = None) -> None:
        self._items: dict[str, dict[int, float]] = {
            code: {int(pl): float(p) for pl, p in prices.items()}
            for code, prices in (items or {}).items()
        }

    def find_item_by_code(self, item_code: str, price_list: int) -> dict | None:
        prices = self._items.get(item_code)
        if prices is None:
            return None
        if int(price_list) not in prices:
            raise SapB1Error(
                f"item {item_code!r} has no price on price list {price_list} -- "
                "configure the list in B1 before repricing against it"
            )
        return {"item_code": item_code, "price": prices[int(price_list)], "price_list": int(price_list)}

    def update_item_price(self, item_code: str, price_list: int, price: float) -> None:
        prices = self._items.get(item_code)
        if prices is None:
            raise SapB1Error(f"unknown ItemCode {item_code!r}")
        prices[int(price_list)] = float(price)


def demo_sap_b1() -> InMemorySapB1:
    """A small, deterministic in-memory B1 company DB for demos and tests.

    Same SKUs and list-1 prices as ``demo_odoo()`` / ``demo_shopify()`` so a
    multichannel repricing demo reprices the "same" catalog everywhere. List 2
    carries a wholesale price, so a demo can show that targeting the wrong list
    is a real, visible mistake rather than a hypothetical one.
    """
    return InMemorySapB1(
        {
            "SKU-1": {1: 20.0, 2: 16.0},
            "SKU-2": {1: 50.0, 2: 41.0},
            "SKU-3": {1: 8.0, 2: 6.5},
        }
    )
