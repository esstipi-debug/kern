"""SAP B1-specific behavior. The shared store contract lives in
tests/test_connector_contract.py; this file covers only what is true of B1 and
of no other connector: expiring sessions, multi-list prices, and a login whose
failure response can quote the password back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nosilo_core import writeback  # noqa: E402
from src.connectors import sap_b1 as mod  # noqa: E402
from src.connectors.sap_b1 import (  # noqa: E402
    InMemorySapB1,
    SapB1Client,
    SapB1Error,
    SapB1PriceStore,
    demo_sap_b1,
)

TARGET = "sap_b1:list-1"

# Built at runtime, never written as a literal next to a `password=` argument.
# The first version of this fixture used SAP's own well-known demo user/password
# pair and GitGuardian flagged the commit -- correctly. A scanner cannot tell a
# test fixture from a leak, and a repo that trains its reviewers to wave the
# scanner through has effectively disabled it. The distinctive sentinel below is
# what the "never echo the credentials" test greps for.
_SYNTHETIC_USER = "-".join(["synthetic", "user"])
_SYNTHETIC_SECRET = "-".join(["synthetic", "value", "not", "a", "credential"])


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """httpx-style transport recording calls, scripted per path.

    ``expire_after`` makes the Nth data request answer 401 exactly once, which
    is how a Service Layer session timing out mid-run actually presents.
    """

    def __init__(self, *, item_payload: dict | None = None, login_status: int = 200,
                 expire_after: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.login_bodies: list[dict] = []
        self._item_payload = item_payload
        self._login_status = login_status
        self._expire_after = expire_after
        self._data_calls = 0

    def post(self, path: str, **kwargs) -> _Resp:
        self.calls.append(("post", path))
        if path == "/Login":
            self.login_bodies.append(kwargs.get("json") or {})
            return _Resp(self._login_status, {"SessionId": "abc"})
        return _Resp(204)

    def _maybe_expire(self) -> _Resp | None:
        self._data_calls += 1
        if self._expire_after is not None and self._data_calls == self._expire_after:
            return _Resp(401)
        return None

    def get(self, path: str, **kwargs) -> _Resp:
        self.calls.append(("get", path))
        expired = self._maybe_expire()
        if expired is not None:
            return expired
        if self._item_payload is None:
            return _Resp(404)
        return _Resp(200, self._item_payload)

    def patch(self, path: str, **kwargs) -> _Resp:
        self.calls.append(("patch", path))
        expired = self._maybe_expire()
        return expired if expired is not None else _Resp(204)


@pytest.fixture(autouse=True)
def _pretend_httpx_is_installed(monkeypatch):
    """SapB1Client refuses to build without httpx. These tests exercise the
    request/session logic, not httpx itself, so the availability flag is
    flipped rather than the dependency imported."""
    monkeypatch.setattr(mod, "_HAS_HTTPX", True)


_ITEM = {"ItemCode": "SKU-1", "ItemPrices": [{"PriceList": 1, "Price": 20.0},
                                             {"PriceList": 2, "Price": 16.0}]}


def _client(http: _FakeHttp) -> SapB1Client:
    return SapB1Client(
        http,
        company_db="TESTDB",
        username=_SYNTHETIC_USER,
        password=_SYNTHETIC_SECRET,
    )


# -- sessions ------------------------------------------------------------------


def test_first_request_logs_in_before_reading():
    http = _FakeHttp(item_payload=_ITEM)
    _client(http).find_item_by_code("SKU-1", 1)
    assert http.calls[0] == ("post", "/Login")


def test_session_is_reused_across_requests():
    http = _FakeHttp(item_payload=_ITEM)
    client = _client(http)
    client.find_item_by_code("SKU-1", 1)
    client.find_item_by_code("SKU-1", 1)
    assert [c for c in http.calls if c == ("post", "/Login")] == [("post", "/Login")]


def test_an_expired_session_is_retried_once_and_succeeds():
    """The normal case for a long-running job: B1 sessions expire on a timer, so
    a 401 mid-run means 'log in again', not 'your credentials are wrong'."""
    http = _FakeHttp(item_payload=_ITEM, expire_after=1)
    result = _client(http).find_item_by_code("SKU-1", 1)
    assert result["price"] == 20.0
    assert [c for c in http.calls if c[0] == "post"].count(("post", "/Login")) == 2


def test_a_second_consecutive_401_is_a_real_auth_failure_and_propagates():
    class _AlwaysExpired(_FakeHttp):
        def get(self, path: str, **kwargs) -> _Resp:
            self.calls.append(("get", path))
            return _Resp(401)

    http = _AlwaysExpired(item_payload=_ITEM)
    with pytest.raises(SapB1Error, match="HTTP 401"):
        _client(http).find_item_by_code("SKU-1", 1)


def test_a_failed_login_never_echoes_the_credentials():
    """A B1 login rejection can quote the request payload back, and that payload
    holds the password. The error message must carry the status and nothing
    else."""
    http = _FakeHttp(login_status=401)
    with pytest.raises(SapB1Error) as excinfo:
        _client(http).login()
    assert _SYNTHETIC_SECRET not in str(excinfo.value)
    assert _SYNTHETIC_USER not in str(excinfo.value)
    assert "401" in str(excinfo.value)


# -- price lists ---------------------------------------------------------------


def test_read_returns_the_price_of_the_requested_list_only():
    http = _FakeHttp(item_payload=_ITEM)
    client = _client(http)
    assert client.find_item_by_code("SKU-1", 1)["price"] == 20.0
    assert client.find_item_by_code("SKU-1", 2)["price"] == 16.0


def test_an_item_without_a_price_on_the_requested_list_is_not_reported_as_missing():
    """'No price on list 3' must not collapse into 'no such item': the caller
    would go on to CREATE a price on a list the client never configured."""
    http = _FakeHttp(item_payload=_ITEM)
    with pytest.raises(SapB1Error, match="no price on price list 3"):
        _client(http).find_item_by_code("SKU-1", 3)


def test_unknown_item_reads_as_none():
    assert _client(_FakeHttp(item_payload=None)).find_item_by_code("NOPE", 1) is None


def test_store_writes_only_to_its_own_price_list():
    """The failure this guards is silent: writing the right number to the wrong
    list looks exactly like success."""
    backend = demo_sap_b1()
    store = SapB1PriceStore(backend, price_list=1)
    changeset = writeback.stage(
        store, TARGET, {"SKU-1": {"price": 25.0}},
        risk_tier=writeback.TIER_REVERSIBLE, idempotency_key="cs-list", reason="reprice list 1",
    )
    writeback.apply(store, changeset, approval=writeback.approve(changeset, "operator"))

    assert backend.find_item_by_code("SKU-1", 1)["price"] == 25.0
    assert backend.find_item_by_code("SKU-1", 2)["price"] == 16.0  # untouched


def test_two_stores_on_different_lists_are_different_writeback_targets():
    backend = demo_sap_b1()
    assert SapB1PriceStore(backend, price_list=1).target != SapB1PriceStore(backend, price_list=2).target


# -- failure handling ----------------------------------------------------------


def test_a_partial_batch_is_undone_before_the_error_propagates():
    """No cross-request transaction exists in the Service Layer, so a batch that
    fails halfway must compensate locally -- otherwise the client is left with
    half a repricing and no audit entry naming it."""
    backend = demo_sap_b1()
    store = SapB1PriceStore(backend, price_list=1)
    changeset = writeback.stage(
        store, TARGET, {"SKU-1": {"price": 25.0}, "SKU-404": {"price": 99.0}},
        risk_tier=writeback.TIER_REVERSIBLE, idempotency_key="cs-partial", reason="partial batch",
    )
    with pytest.raises(SapB1Error, match="unknown ItemCode"):
        writeback.apply(store, changeset, approval=writeback.approve(changeset, "operator"))

    assert backend.find_item_by_code("SKU-1", 1)["price"] == 20.0  # rolled back
    assert "cs-partial" not in store.applied_keys()


def test_client_refuses_to_build_without_httpx(monkeypatch):
    monkeypatch.setattr(mod, "_HAS_HTTPX", False)
    with pytest.raises(SapB1Error, match="repricing"):
        SapB1Client(_FakeHttp(), company_db="X", username="u", password="p")


def test_in_memory_backend_needs_no_credentials_and_no_network():
    """The offline stand-in is the whole reason this connector is testable: a
    real B1 is a deploy-time dependency, not a development one."""
    store = SapB1PriceStore(InMemorySapB1({"SKU-9": {1: 5.0}}), price_list=1)
    assert store.read("SKU-9") == {"price": 5.0}
