"""Tests for webapp/demo_price_scan.py + POST /api/demo-price-scan
(Linchpin 3.0 PR-13's free lead-magnet, plan section 9).

No real network call ever happens here -- the module-level test injects an
``httpx.MockTransport``-backed client via ``run_demo_price_scan``'s
testing-only ``http_client`` parameter; the endpoint-level test relies on
the SAME SSRF-by-construction guarantee production traffic gets (the
submitted URL's domain has no ``config/sites/*.yaml``, so the acquire step
skips it before ever calling out).
"""
from __future__ import annotations

import httpx
import pytest

from webapp import demo_price_scan

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

client = TestClient(app)


def _mock_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="""
        <html><head><script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product","offers":
          {"@type":"Offer","price":"29.99","priceCurrency":"USD","availability":"https://schema.org/InStock"}}
        </script></head><body></body></html>
        """)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_run_demo_price_scan_caps_urls_and_returns_teaser_rows(tmp_path):
    urls = [f"https://example-retailer.test/p/{i}" for i in range(8)]  # more than MAX_TEASER_URLS
    mock = _mock_client()
    try:
        result = demo_price_scan.run_demo_price_scan(
            urls, product_id="P1", ledger_base_path=tmp_path / "ledger", http_client=mock,
        )
    finally:
        mock.close()

    assert result.n_urls_submitted == demo_price_scan.MAX_TEASER_URLS
    assert result.ok is True
    assert len(result.teaser_rows) == demo_price_scan.MAX_TEASER_URLS
    for row in result.teaser_rows:
        assert row["price_normalized"] == pytest.approx(29.99)
        assert row["acquisition_tier"] == "L1"


def test_run_demo_price_scan_raises_on_no_urls(tmp_path):
    with pytest.raises(ValueError, match="at least one"):
        demo_price_scan.run_demo_price_scan([" ", ""], ledger_base_path=tmp_path / "ledger")


def test_render_mini_report_and_followup_never_auto_sent(tmp_path):
    mock = _mock_client()
    try:
        result = demo_price_scan.run_demo_price_scan(
            ["https://example-retailer.test/p/1"], product_id="Widget",
            ledger_base_path=tmp_path / "ledger", http_client=mock,
        )
    finally:
        mock.close()
    mini = demo_price_scan.render_mini_report(result, email="lead@example.com", product_id="Widget", ts="2026-07-12T00:00:00Z")
    assert "Widget" in mini
    assert "example-retailer.test" in mini
    followup = demo_price_scan.render_followup_email(result, email="lead@example.com", product_id="Widget")
    assert "nunca envia correo automaticamente" in followup


def test_endpoint_rejects_missing_email():
    resp = client.post("/api/demo-price-scan", data={"urls": "https://example-retailer.test/p/1"})
    assert resp.status_code == 422  # FastAPI's own required-field validation


def test_endpoint_rejects_invalid_email():
    resp = client.post("/api/demo-price-scan", data={"email": "not-an-email", "urls": "https://x.test/p/1"})
    assert resp.status_code == 400


def test_endpoint_rejects_empty_urls():
    resp = client.post("/api/demo-price-scan", data={"email": "lead@example.com", "urls": "   "})
    assert resp.status_code == 400


def test_endpoint_unapproved_domain_is_ssrf_safe_never_fetched():
    # No config/sites/internal-service.local.yaml exists -- the acquire step
    # must refuse this domain, never actually attempt the network call.
    resp = client.post(
        "/api/demo-price-scan",
        data={"email": "lead@example.com", "urls": "http://internal-service.local/admin", "product_id": "P1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "qa_failed"
    assert body["headline"]["n_confirmed"] == 0
