"""Tests for the Pricing dashboard tab (Linchpin 3.0 PR-13, plan sections
6.11/9): GET /pricing serves the honest empty state today (no persisted
"last run" store exists yet), and render_pricing_html renders real summary
numbers -- position matrix coverage, quarantine rate, freshness, tier mix --
when a PricingSummary is supplied, never a fabricated one.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from jobs import price_intelligence as pi  # noqa: E402
from webapp.app import app  # noqa: E402
from webapp.pricing_page import PricingSummary, render_pricing_html  # noqa: E402

client = TestClient(app)
FIXED_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def test_pricing_route_returns_200_html_with_brand_and_title():
    resp = client.get("/pricing")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Kern" in resp.text
    assert "Pricing" in resp.text


def test_pricing_route_renders_the_honest_empty_state_by_default():
    resp = client.get("/pricing")
    assert "run_price_intel.py" in resp.text
    assert "no fabrica" in resp.text


def test_render_pricing_html_with_summary_shows_real_numbers():
    summary = PricingSummary(
        client="Acme", generated_at="2026-07-12T12:00:00Z", n_products=10,
        n_products_covered=7, coverage_pct=0.70, quarantine_rate=0.10,
        avg_freshness_hours=3.5, sla_hours=48.0, tier_mix={"L1": 12},
        n_quarantined=1, n_discarded=0, n_skipped=2,
    )
    html = render_pricing_html(summary)
    assert "Acme" in html
    assert "70%" in html
    assert "10%" in html
    assert "3.5h" in html
    assert "L1" in html
    assert "12" in html


def test_pricing_summary_from_real_report(tmp_path):
    from src.pricing_intel.ledger import PriceLedger

    fixtures = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "pricing_intel"
    df = pd.DataFrame([{
        "product_id": "SKU-100", "competitor_url": "https://example-retailer.test/p/aw-3000",
        "our_price": 210.00, "html_path": "jsonld_clean.html",
    }])
    payload = pi.prepare_records(df, base_dir=fixtures)
    ledger = PriceLedger(tmp_path / "ledger")
    report = pi.run(payload, ledger=ledger, event_ledger=None, now=FIXED_NOW)
    ledger.close()

    summary = PricingSummary.from_report(report, client="Acme", generated_at=FIXED_NOW.isoformat())
    assert summary.n_products == 1
    assert summary.n_products_covered == 1
    assert summary.coverage_pct == pytest.approx(1.0)
    assert summary.tier_mix == {"L1": 1}

    html = render_pricing_html(summary)
    assert "100%" in html
