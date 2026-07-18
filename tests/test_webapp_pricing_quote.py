"""HTTP tests for GET /api/pricing-quote (webapp/pricing_quote.py).

Thin adapter over src/commercial_pricing.py's GMV-band quote engine (Task 2,
capability A2 of the GMV-band GTM plan). Stateless per request - no
client_profile.py persistence (YAGNI, see plan). These tests exercise the
route's own concerns only (param parsing, package-key aliasing, error
mapping); the pricing arithmetic itself is covered by
tests/test_commercial_pricing.py.
"""

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

client = TestClient(app)


def test_starter_quote_with_skus_returns_980():
    r = client.get("/api/pricing-quote", params={"package": "starter", "revenue": 2_000_000, "skus": 1_000})
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["monthly_price"] == 980.0
    assert "980" in body["price_string"]


def test_starter_quote_without_skus_returns_floor_price():
    r = client.get("/api/pricing-quote", params={"package": "starter", "revenue": 2_000_000})
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["monthly_price"] == 900.0
    assert body["quote"]["sku_count"] is None


def test_scale_quote_is_flat():
    r = client.get("/api/pricing-quote", params={"package": "scale", "revenue": 10_000_000, "skus": 50_000})
    assert r.status_code == 200
    assert r.json()["quote"]["monthly_price"] == 3200.0


def test_retainer_ejecutivo_alias_maps_to_commercial_pricing_retainer_key():
    # scm_agent/package_specs.py's PackageSpec.key is "retainer_ejecutivo", but
    # src/commercial_pricing.py's own VALID_PACKAGE_KEYS uses "retainer" - the
    # route must alias the public-facing key, not pass it straight through.
    r = client.get("/api/pricing-quote", params={"package": "retainer_ejecutivo", "revenue": 5_000_000})
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["monthly_price"] == 4500.0
    assert body["quote"]["package_key"] == "retainer"


def test_unknown_package_is_400_not_500():
    r = client.get("/api/pricing-quote", params={"package": "nonexistent", "revenue": 2_000_000})
    assert r.status_code == 400


def test_package_with_no_gmv_band_pricing_is_400():
    # starter_latam is a real PackageSpec.key but has no GMV-band pricing in
    # src/commercial_pricing.py (flat reduced-scope pricing instead) - must not
    # silently fall back to full Starter pricing.
    r = client.get("/api/pricing-quote", params={"package": "starter_latam", "revenue": 2_000_000})
    assert r.status_code == 400


def test_negative_revenue_is_a_client_error_not_500():
    r = client.get("/api/pricing-quote", params={"package": "starter", "revenue": -1})
    assert r.status_code in (400, 422)


def test_revenue_below_lowest_band_surfaces_needs_clarification():
    r = client.get("/api/pricing-quote", params={"package": "starter", "revenue": 500_000})
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["needs_clarification"] is True
    assert "clarific" in body["price_string"].lower() or "confirmar" in body["price_string"].lower()


def test_package_revenue_mismatch_surfaces_suggested_package():
    r = client.get("/api/pricing-quote", params={"package": "starter", "revenue": 10_000_000})
    assert r.status_code == 200
    body = r.json()
    assert body["quote"]["revenue_band_match"] is False
    assert body["quote"]["suggested_package_key"] == "scale"


def test_missing_required_params_is_422():
    r = client.get("/api/pricing-quote")
    assert r.status_code == 422
