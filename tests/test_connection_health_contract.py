"""One contract, every connector that speaks over a network: what each one is
required to say about the *connection*, never about the data.

Why this exists. Three connectors had three different answers to the same
question -- ``odoo.py`` retries reads with backoff, ``sap_b1.py`` re-logs in once
on a 401, ``shopify_api_version.py`` raises on a drifted write -- and nothing
shared a concept of "is this connection usable". Nobody handled a 429 at all,
which is the failure a large Shopify sync meets first, because Shopify GraphQL
bills by query cost and answers with ``Retry-After``.

The line this file draws, and the reason it is a contract rather than a helper:
**the health plane reports, it never repairs and never retries.** ``is_healthy``
may not write. ``on_auth_failure`` re-establishes a *session* -- which has no
business consequence -- and returns whether a retry is warranted; it does not
perform the retry. ``on_throttle`` returns how long to wait; it does not sleep.
The decision to re-issue an operation stays with the caller, because only the
caller knows whether that operation is idempotent: ``writeback.apply`` releases
the idempotency key when ``commit`` raises, so a retried write DOES re-execute,
and ``odoo.py`` creates purchase orders with ``execute_kw(..., "create")``.
A health plane that retried on its own would duplicate them.

Scope, stated rather than implied: this covers the four live transports on
``main`` -- which is all of them, so the drift guard at the end exempts nothing.
``excel.py`` is a local file with no connection plane at all and the
``InMemory*`` stand-ins have no transport to be unhealthy, so neither is
networked in the first place. The final test fails if a new networked client
appears and is silently left out.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.connectors import health  # noqa: E402
from src.connectors.meli_catalog import MeliCatalogClient  # noqa: E402
from src.connectors.meli_prices import MeliClient  # noqa: E402
from src.connectors.odoo import OdooClient  # noqa: E402
from src.connectors.shopify_inventory import ShopifyInventoryClient  # noqa: E402
from src.connectors.shopify_prices import ShopifyClient  # noqa: E402

# -- fakes ---------------------------------------------------------------------


class FakeResponse:
    """An httpx-shaped response whose status and headers the test dictates."""

    def __init__(self, status: int = 200, payload: Any = None, headers: dict | None = None) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload if payload is not None else {"data": {}}

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise FakeHTTPStatusError(f"HTTP {self.status_code}", response=self)


class FakeHTTPStatusError(Exception):
    """Stands in for ``httpx.HTTPStatusError``: carries ``.response``, which is
    the only thing the health helpers are allowed to reach for."""

    def __init__(self, message: str, *, response: FakeResponse) -> None:
        super().__init__(message)
        self.response = response


class FakeHttp:
    """Programmable httpx-style client. ``writes`` records every mutating call so
    a test can assert the health plane never made one."""

    def __init__(self, response: FakeResponse | None = None) -> None:
        self.response = response or FakeResponse(payload={"data": {"shop": {"name": "demo"}}})
        self.writes: list[tuple[str, str]] = []
        self.down = False

    def _answer(self, verb: str, path: str, payload: dict | None) -> FakeResponse:
        if self.down:
            raise OSError("connection refused")
        if self._is_write(verb, payload):
            self.writes.append((verb, path))
        return self.response

    @staticmethod
    def _is_write(verb: str, payload: dict | None) -> bool:
        """GraphQL reads travel by POST, so the verb alone cannot classify a
        Shopify call -- the discriminator is whether the document is a mutation.
        Getting this wrong in the fake would make the 'never writes' invariant
        fire on every read and hide the case it exists to catch."""
        if verb == "get":
            return False
        query = (payload or {}).get("query")
        if query is not None:
            return "mutation" in str(query)
        return True

    def get(self, path: str, **_kw) -> FakeResponse:
        return self._answer("get", path, None)

    def post(self, path: str, json: dict | None = None, **_kw) -> FakeResponse:
        return self._answer("post", path, json)

    def put(self, path: str, json: dict | None = None, **_kw) -> FakeResponse:
        return self._answer("put", path, json)


class FakeOdooProxy:
    """Minimal ``xmlrpc.client.ServerProxy`` stand-in for both Odoo endpoints."""

    def __init__(self) -> None:
        self.authenticate_calls = 0
        self.writes: list[str] = []
        self.down = False
        self.uid: int | None = 7

    def authenticate(self, _db, _user, _key, _ctx) -> Any:
        self.authenticate_calls += 1
        if self.down:
            raise OSError("connection refused")
        return self.uid

    def execute_kw(self, _db, _uid, _key, model, method, *_a, **_kw) -> Any:
        if self.down:
            raise OSError("connection refused")
        if method not in {"search", "search_read", "read", "search_count", "fields_get"}:
            self.writes.append(f"{model}.{method}")
        return 1


# -- cases ---------------------------------------------------------------------


@dataclass(frozen=True)
class HealthCase:
    """A networked client, plus what its protocol makes possible.

    ``can_repair_auth`` is not a quirk of the test: Odoo holds db/user/api-key and
    can re-authenticate itself, while Shopify and MercadoLibre receive a
    pre-built ``Authorization``/``X-Shopify-Access-Token`` header wired at deploy
    time and structurally CANNOT mint a new one. A connector that claimed
    otherwise would be lying to its caller.
    """

    name: str
    build: Callable[[], Any]
    transport: Callable[[Any], Any]
    can_repair_auth: bool
    throttles: bool


def _shopify_prices() -> ShopifyClient:
    return ShopifyClient(FakeHttp())


def _shopify_inventory() -> ShopifyInventoryClient:
    return ShopifyInventoryClient(FakeHttp())


def _meli() -> MeliClient:
    return MeliClient(FakeHttp(), user_id="123")



class _BearerHttp(FakeHttp):
    """``FakeHttp`` con un Authorization visible.

    ``MeliCatalogClient`` se niega a construirse sin Bearer -- el host publico de
    MercadoLibre es robots-prohibited sin token del Developers Program -- asi que
    el doble tiene que mostrar uno para poder ejercitar el contrato de salud.
    """

    headers = {"Authorization": "Bearer test-token"}


def _meli_catalog() -> MeliCatalogClient:
    return MeliCatalogClient(_BearerHttp(), user_id="123")


def _odoo() -> OdooClient:
    proxy = FakeOdooProxy()
    return OdooClient("http://odoo.test", "db", "user", "key", common=proxy, models=proxy)


CASES: tuple[HealthCase, ...] = (
    HealthCase("shopify_prices", _shopify_prices, lambda c: c._http, False, True),
    HealthCase("shopify_inventory", _shopify_inventory, lambda c: c._http, False, True),
    HealthCase("meli_prices", _meli, lambda c: c._http, False, True),
    HealthCase("meli_catalog", _meli_catalog, lambda c: c._http, False, True),
    # throttles=True for Odoo is not about XML-RPC, which has no such signal: a
    # production Odoo usually sits behind nginx or Cloudflare, and those answer a
    # rate limit with a genuine 429 + Retry-After that reaches the client.
    HealthCase("odoo", _odoo, lambda c: c._models, True, True),
)


@pytest.fixture(params=CASES, ids=lambda case: case.name)
def case(request) -> HealthCase:
    return request.param


# -- the contract --------------------------------------------------------------


def test_a_reachable_connection_reports_healthy(case: HealthCase):
    client = case.build()
    assert client.is_healthy() is True


def test_the_health_probe_never_writes(case: HealthCase):
    """The invariant that keeps this the CONNECTION plane. A probe that mutated
    anything would be a data repair wearing a health-check costume."""
    client = case.build()
    client.is_healthy()
    assert case.transport(client).writes == []


def test_an_unreachable_connection_reports_unhealthy_instead_of_raising(case: HealthCase):
    """``is_healthy`` is the question you ask *because* something may be broken;
    raising would force every caller to wrap it in the try/except it exists to
    replace."""
    client = case.build()
    case.transport(client).down = True
    assert client.is_healthy() is False


def test_authenticating_twice_is_safe(case: HealthCase):
    client = case.build()
    client.authenticate()
    client.authenticate()
    assert case.transport(client).writes == []


def test_a_non_auth_error_is_not_reported_as_repairable(case: HealthCase):
    client = case.build()
    assert client.on_auth_failure(ValueError("unrelated")) is False


def test_auth_repair_is_offered_at_most_once_in_a_row(case: HealthCase):
    """A genuinely revoked credential must terminate, not spin. The second
    consecutive failure means re-authenticating did not help."""
    client = case.build()
    exc = _auth_error(case)
    first = client.on_auth_failure(exc)
    assert first is case.can_repair_auth
    assert client.on_auth_failure(exc) is False


def test_a_connector_that_cannot_mint_a_credential_says_so(case: HealthCase):
    """Shopify and MercadoLibre take a pre-built header; there is nothing to
    refresh. Reporting True would send the caller into a retry that re-fails."""
    if case.can_repair_auth:
        pytest.skip("this transport holds its own credentials")
    assert client_on_auth(case) is False


def test_a_non_throttle_error_yields_no_wait(case: HealthCase):
    client = case.build()
    assert client.on_throttle(ValueError("unrelated")) is None


def test_retry_after_is_honoured_when_the_service_sends_one(case: HealthCase):
    if not case.throttles:
        pytest.skip("this protocol has no throttling signal")
    client = case.build()
    exc = FakeHTTPStatusError("429", response=FakeResponse(429, headers={"Retry-After": "7"}))
    assert client.on_throttle(exc) == pytest.approx(7.0)


def test_a_throttle_without_retry_after_still_yields_a_bounded_wait(case: HealthCase):
    """Shopify does not always send Retry-After. Returning None there would read
    as 'not throttled' and the caller would hammer the endpoint."""
    if not case.throttles:
        pytest.skip("this protocol has no throttling signal")
    client = case.build()
    exc = FakeHTTPStatusError("429", response=FakeResponse(429))
    wait = client.on_throttle(exc)
    assert wait is not None
    assert 0 < wait <= health.MAX_BACKOFF_SECONDS


def test_the_whole_health_plane_never_writes(case: HealthCase):
    """The contract in one assertion: exercise every operation, then prove the
    system of record was never touched."""
    client = case.build()
    client.is_healthy()
    client.authenticate()
    client.on_auth_failure(_auth_error(case))
    client.on_throttle(FakeHTTPStatusError("429", response=FakeResponse(429)))
    assert case.transport(client).writes == []


# -- helpers -------------------------------------------------------------------


def _auth_error(case: HealthCase) -> Exception:
    if case.name == "odoo":
        return OSError("connection reset")  # what a dropped Odoo session looks like
    return FakeHTTPStatusError("401", response=FakeResponse(401))


def client_on_auth(case: HealthCase) -> bool:
    return case.build().on_auth_failure(_auth_error(case))


# -- the helpers degrade instead of raising ------------------------------------
#
# The module promises "every helper answers 'no information' rather than
# raising". These are the malformed inputs that would break that promise, and a
# health plane that threw while being asked whether something was broken would
# be self-defeating -- so the promise needs tests, not just a docstring.


class _HeaderlessResponse:
    """A response whose ``headers`` is not a mapping. Real transports differ, and
    the offline stand-ins have no headers at all."""

    status_code = 429
    headers = object()


def test_a_non_numeric_status_reads_as_no_status():
    exc = FakeHTTPStatusError("weird", response=FakeResponse(200))
    exc.response.status_code = "not-a-number"
    assert health.status_of(exc) is None
    assert health.is_auth_failure(exc) is False


def test_an_error_carrying_no_response_reads_as_no_status():
    assert health.status_of(ValueError("plain")) is None


def test_headers_that_are_not_a_mapping_yield_no_retry_after():
    exc = FakeHTTPStatusError("429", response=_HeaderlessResponse())
    assert health.retry_after_seconds(exc) is None
    # Still a throttle, so the caller must still be told to wait.
    assert health.throttle_wait(exc) == pytest.approx(health.BASE_BACKOFF_SECONDS)


def test_a_response_without_headers_at_all_yields_no_retry_after():
    """What the offline stand-ins return: an object with no ``headers`` to read."""
    exc = FakeHTTPStatusError("429", response=FakeResponse(429))
    exc.response.headers = None
    assert health.retry_after_seconds(exc) is None


def test_an_unparseable_retry_after_falls_through_to_backoff():
    exc = FakeHTTPStatusError("429", response=FakeResponse(429, headers={"Retry-After": "Wed, 21 Oct"}))
    assert health.retry_after_seconds(exc) is None
    assert health.throttle_wait(exc) == pytest.approx(health.BASE_BACKOFF_SECONDS)


def test_a_negative_retry_after_is_refused():
    """A negative delay is a malformed header, not an instruction to hurry."""
    exc = FakeHTTPStatusError("429", response=FakeResponse(429, headers={"Retry-After": "-5"}))
    assert health.retry_after_seconds(exc) is None


def test_an_enormous_retry_after_is_capped():
    """Handed to a caller that may sleep on it inside a job with a deadline."""
    exc = FakeHTTPStatusError("429", response=FakeResponse(429, headers={"Retry-After": "99999"}))
    assert health.retry_after_seconds(exc) == pytest.approx(health.MAX_BACKOFF_SECONDS)


def test_backoff_grows_but_stays_bounded():
    assert health.backoff_seconds(0) < health.backoff_seconds(3)
    assert health.backoff_seconds(99) == pytest.approx(health.MAX_BACKOFF_SECONDS)


# -- drift guard ---------------------------------------------------------------


def test_every_networked_client_is_covered_by_this_suite():
    """A new connector that speaks over a network must appear in CASES.
    Otherwise this file's promise -- 'every transport answers the same health
    questions' -- silently becomes 'the ones someone remembered'."""
    import re

    connectors_dir = _REPO_ROOT / "src" / "connectors"
    networked = set()
    for path in sorted(connectors_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        # A live transport is one that hands work to an injected httpx-style
        # object or an xmlrpc proxy, rather than to an in-process dict.
        if re.search(r"raise_for_status\(\)|xmlrpc\.client\.ServerProxy", source):
            networked.add(path.stem)

    # El detector mira el TEXTO del modulo, asi que una mencion dentro de un
    # docstring lo activa igual. Se nombra la excepcion en vez de afinar el regex:
    # un detector mas listo falla en silencio el dia que no reconoce un transporte
    # nuevo, y este, al ser tosco, falla ruidoso -- que es el lado correcto.
    #
    # "shopify_connect" -- NO tiene transporte: ni clase cliente, ni `self._http`,
    # ni credencial propia. Es un modulo de funciones que reciben una respuesta ya
    # hecha. Aparece aca porque su helper `_status()` explica EN SU DOCSTRING que
    # lee `status_code` del atributo "en vez de via raise_for_status()", para que
    # el clasificador separe 401 de 403 de 404 sin depender de la jerarquia de
    # excepciones de httpx. Lo unico que lo delata es la frase que dice que no lo usa.
    networked -= {"shopify_connect"}

    covered = {"shopify_prices", "shopify_inventory", "meli_prices", "meli_catalog", "odoo"}
    # Every module owning a live transport is covered, so there is nothing to
    # exempt. Two near-misses, named so a later reader does not "fix" their
    # absence: odoo_prices.py drives an injected OdooRPC (in production an
    # OdooClient, covered here) and owns no transport; http_client.py maps
    # responses to DTOs and holds no credential and no session. Neither matches
    # the detector above, which is the correct outcome, not an oversight.
    assert networked == covered, (
        f"networked connector(s) absent from this file: {sorted(networked - covered)}"
    )
