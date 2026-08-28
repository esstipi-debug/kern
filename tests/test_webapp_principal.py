"""Identity derivation on the live request path.

The tests that matter most here are the negative ones. Reading identity from a
client-settable header is a complete authentication bypass unless a proxy strips
the client's copy first, so the checks that the headers are *ignored* without an
explicit opt-in are load-bearing in a way the happy path is not.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from nosilo_core.access import ANY, PRINCIPAL_HUMAN, PRINCIPAL_SERVICE, PolicySet

from webapp import principal, security
from webapp.app import app


class _FakeRequest:
    """Minimal stand-in - derivation must not need a live ASGI request."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Every test starts from the shipped defaults."""
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", False)
    monkeypatch.setattr(principal, "DEFAULT_TENANT_NAME", "default")
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(security, "POLICY", None)
    security.reset_rate_limit()


# ---- the default: nothing changes -------------------------------------------


def test_a_request_with_no_credentials_is_anonymous() -> None:
    who = principal.principal_from_request(_FakeRequest())
    assert who.id == principal.ANONYMOUS_ID
    assert who.kind == PRINCIPAL_HUMAN
    assert who.roles == frozenset()


def test_identity_headers_are_ignored_without_the_opt_in() -> None:
    """The bypass this whole design is arranged to prevent."""
    forged = _FakeRequest({"x-auth-subject": "ceo@acme.com", "x-auth-roles": "admin,treasury"})
    who = principal.principal_from_request(forged)
    assert who.id == principal.ANONYMOUS_ID
    assert who.roles == frozenset()


def test_derivation_never_raises_on_a_broken_request() -> None:
    class Hostile:
        @property
        def headers(self):
            raise RuntimeError("boom")

    assert principal.principal_from_request(Hostile()).id == principal.ANONYMOUS_ID


# ---- api key = a deployment, not a person -----------------------------------


def test_a_valid_api_key_yields_a_service_principal() -> None:
    who = principal.principal_from_request(_FakeRequest({"x-api-key": "s3cret"}), api_key="s3cret")
    assert who.kind == PRINCIPAL_SERVICE
    assert who.id == principal.API_KEY_SERVICE_ID
    assert who.attributes["source"] == "api_key"


def test_a_shared_secret_is_never_reported_as_a_human() -> None:
    """An audit entry naming a human nobody is accountable for is worse than none."""
    who = principal.principal_from_request(_FakeRequest({"x-api-key": "s3cret"}), api_key="s3cret")
    assert who.kind != PRINCIPAL_HUMAN


def test_a_wrong_api_key_is_anonymous_not_an_error() -> None:
    who = principal.principal_from_request(_FakeRequest({"x-api-key": "wrong"}), api_key="s3cret")
    assert who.id == principal.ANONYMOUS_ID


# ---- trusted headers, once explicitly enabled -------------------------------


def test_trusted_headers_are_read_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    who = principal.principal_from_request(
        _FakeRequest({"x-auth-subject": "u1@acme.com", "x-auth-roles": "buyer, treasury",
                      "x-auth-tenant": "acme"})
    )
    assert who.id == "u1@acme.com"
    assert who.kind == PRINCIPAL_HUMAN
    assert who.tenant == "acme"
    assert who.roles == frozenset({"buyer", "treasury"})


def test_an_empty_subject_header_does_not_mint_a_principal(monkeypatch) -> None:
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    assert principal.principal_from_request(_FakeRequest({"x-auth-subject": "  "})).id == (
        principal.ANONYMOUS_ID
    )


def test_the_tenant_falls_back_when_the_header_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    who = principal.principal_from_request(_FakeRequest({"x-auth-subject": "u1"}))
    assert who.tenant == "default"


# ---- starting authority ------------------------------------------------------


def test_anonymous_starts_with_no_actions() -> None:
    """Empty denies. It only bites once a PolicySet exists - see the next test."""
    assert principal.capability_for(principal.anonymous()).actions == frozenset()


def test_the_api_key_holder_keeps_todays_authority() -> None:
    """Narrowing it here would silently break every existing deployment."""
    who = principal.principal_from_request(_FakeRequest({"x-api-key": "k"}), api_key="k")
    assert principal.capability_for(who).actions == frozenset({ANY})


def test_the_capability_records_who_it_came_from() -> None:
    assert principal.capability_for(principal.anonymous()).chain == (principal.ANONYMOUS_ID,)


# ---- the middleware ----------------------------------------------------------


def _probe_app():
    """A minimal app carrying only the middleware under test.

    Exercised against a purpose-built route rather than a real one so the
    assertion is about what the middleware attached, not about whatever a
    production handler happens to return.

    ``FastAPI``/``Request`` are imported at module scope, not here: this module
    has ``from __future__ import annotations``, so FastAPI resolves the handler's
    ``request: Request`` annotation as a string against the *module* namespace.
    With the import local to this function it resolves to nothing and FastAPI
    treats ``request`` as a missing query parameter, returning 422.
    """
    probe = FastAPI()
    probe.middleware("http")(security.principal_middleware)

    @probe.get("/whoami")
    def whoami(request: Request):
        who = security.request_principal(request)
        return {
            "id": who.id,
            "kind": who.kind,
            "tenant": who.tenant,
            "roles": sorted(who.roles),
            "actions": sorted(security.request_capability(request).actions),
        }

    return TestClient(probe)


def test_every_request_carries_a_principal() -> None:
    body = _probe_app().get("/whoami").json()
    assert body["id"] == principal.ANONYMOUS_ID
    assert body["actions"] == []  # anonymous starts with nothing


def test_the_middleware_promotes_a_valid_api_key_holder(monkeypatch) -> None:
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    body = _probe_app().get("/whoami", headers={"x-api-key": "s3cret"}).json()
    assert body["kind"] == PRINCIPAL_SERVICE
    assert body["actions"] == [ANY]


def test_the_middleware_ignores_forged_identity_headers(monkeypatch) -> None:
    """End to end, through the real middleware, with the opt-in off."""
    body = _probe_app().get(
        "/whoami", headers={"x-auth-subject": "ceo@acme.com", "x-auth-roles": "admin"}
    ).json()
    assert body["id"] == principal.ANONYMOUS_ID
    assert body["roles"] == []


def test_the_middleware_reads_identity_headers_once_enabled(monkeypatch) -> None:
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    body = _probe_app().get(
        "/whoami", headers={"x-auth-subject": "u1@acme.com", "x-auth-roles": "buyer",
                            "x-auth-tenant": "acme"}
    ).json()
    assert body["id"] == "u1@acme.com"
    assert body["tenant"] == "acme"
    assert body["roles"] == ["buyer"]


def test_the_real_app_still_serves_anonymously_by_default() -> None:
    """The whole layer is inert until an operator configures it."""
    assert TestClient(app).get("/").status_code == 200


def test_a_handler_without_the_middleware_degrades_to_anonymous() -> None:
    class Bare:
        class state:  # noqa: N801 - mimicking Starlette's attribute bag
            pass

    assert security.request_principal(Bare()).id == principal.ANONYMOUS_ID
    assert security.request_capability(Bare()).actions == frozenset()


# ---- the policy seam ---------------------------------------------------------


def test_the_engine_is_ungoverned_by_default(monkeypatch) -> None:
    """The precondition is stated, not inherited.

    This used to rely on `security.POLICY` still being the module default. Since
    `webapp/app.py` assigns it at import, whether that held depended on which
    test imported the app first - a passing test whose reason was the collection
    order. The property under test is the module's default, so the test sets it.
    """
    monkeypatch.setattr(security, "POLICY", None)
    assert not security.policy_engine().is_governed


def test_setting_a_policy_governs_the_engine(monkeypatch) -> None:
    monkeypatch.setattr(security, "POLICY", PolicySet(version="v1"))
    assert security.policy_engine().is_governed
    assert security.policy_engine().policy.version == "v1"


# ---- production warnings -----------------------------------------------------


def test_production_warns_when_no_policy_is_configured(monkeypatch) -> None:
    # POLICY set explicitly to None for the same reason as the test above: the
    # app now assigns one at import, so "no policy" has to be arranged rather
    # than assumed.
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "k")
    monkeypatch.setattr(security, "RATE_LIMIT", 120)
    monkeypatch.setattr(security, "APPROVAL_SECRET", "s")
    monkeypatch.setattr(security, "POLICY", None)
    warnings = security.production_warnings()
    assert any("no access policy is configured" in w for w in warnings)


def test_a_fully_configured_production_is_silent(monkeypatch) -> None:
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "k")
    monkeypatch.setattr(security, "RATE_LIMIT", 120)
    monkeypatch.setattr(security, "APPROVAL_SECRET", "s")
    monkeypatch.setattr(security, "POLICY", PolicySet(version="v1"))
    assert security.production_warnings() == []


def test_trusted_headers_in_production_are_flagged(monkeypatch) -> None:
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "k")
    monkeypatch.setattr(security, "RATE_LIMIT", 120)
    monkeypatch.setattr(security, "APPROVAL_SECRET", "s")
    monkeypatch.setattr(security, "POLICY", PolicySet(version="v1"))
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    warnings = security.production_warnings()
    assert any("strips client-supplied" in w for w in warnings)


def test_trusted_headers_with_no_api_key_is_flagged_twice(monkeypatch) -> None:
    """The genuinely open shape: anyone can claim any identity."""
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    warnings = security.production_warnings()
    assert any("nothing at all stopping" in w for w in warnings)


def test_development_stays_silent_whatever_is_configured(monkeypatch) -> None:
    monkeypatch.setattr(security, "ENV", "development")
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", True)
    monkeypatch.setattr(security, "API_KEY", "")
    assert security.production_warnings() == []
