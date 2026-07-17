"""HTTP-level tests for GET /stocky-alternative (the Shopify-Stocky-shutdown
SEO/conversion landing page, webapp/stocky_alternative_page.py)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402
from webapp.offers import get_offer  # noqa: E402

client = TestClient(app)


def test_stocky_alternative_page_ok() -> None:
    resp = client.get("/stocky-alternative")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_page_names_the_real_shutdown_date_and_offers() -> None:
    resp = client.get("/stocky-alternative")
    body = resp.text
    assert "August 31, 2026" in body
    starter = get_offer("starter-fundamentos")
    diagnostico = get_offer("diagnostico-arranque")
    assert starter is not None and diagnostico is not None
    assert starter.slug in body
    assert diagnostico.slug in body
    assert starter.price in body
    assert diagnostico.price in body


def test_page_contains_valid_faq_jsonld() -> None:
    resp = client.get("/stocky-alternative")
    body = resp.text
    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
    )
    assert match is not None, "FAQPage JSON-LD script tag not found"
    doc = json.loads(match.group(1))
    assert doc["@type"] == "FAQPage"
    assert len(doc["mainEntity"]) >= 3
    for entry in doc["mainEntity"]:
        assert entry["@type"] == "Question"
        assert entry["name"]
        assert entry["acceptedAnswer"]["text"]


def test_page_degrades_cleanly_without_any_sales_env_vars(monkeypatch) -> None:
    for slug in ("starter-fundamentos", "diagnostico-arranque"):
        offer = get_offer(slug)
        assert offer is not None
        monkeypatch.delenv(offer.stripe_env_var, raising=False)
    monkeypatch.delenv("CALENDLY_URL", raising=False)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    resp = client.get("/stocky-alternative")
    assert resp.status_code == 200
    assert "mailto:?subject=" in resp.text


def test_page_uses_configured_stripe_link(monkeypatch) -> None:
    offer = get_offer("diagnostico-arranque")
    assert offer is not None
    monkeypatch.setenv(offer.stripe_env_var, "https://buy.stripe.com/live_test_stocky")
    resp = client.get("/stocky-alternative")
    assert resp.status_code == 200
    assert "https://buy.stripe.com/live_test_stocky" in resp.text


def test_page_links_to_free_demo_and_home() -> None:
    resp = client.get("/stocky-alternative")
    body = resp.text
    assert 'href="/demo"' in body
    assert 'href="/paquetes"' in body
