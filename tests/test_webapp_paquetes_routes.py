"""HTTP-level tests for GET /paquetes and GET /paquetes/{slug}."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402
from webapp.offers import OFFERS  # noqa: E402

client = TestClient(app)


def test_paquetes_index_lists_all_offers() -> None:
    resp = client.get("/paquetes")
    assert resp.status_code == 200
    for offer in OFFERS:
        assert offer.slug in resp.text


def test_paquetes_offer_page_ok_for_known_slug() -> None:
    resp = client.get("/paquetes/starter-fundamentos")
    assert resp.status_code == 200
    assert "/paquetes-docs/starter-fundamentos.md" in resp.text


def test_paquetes_offer_page_404_for_unknown_slug() -> None:
    resp = client.get("/paquetes/no-existe")
    assert resp.status_code == 404


def test_paquetes_offer_page_ok_for_diagnostico_posicion_precios() -> None:
    # regression: this slug 404'd -- never registered in OFFERS despite
    # demo_price_scan.py and pricing_page.py already linking to it.
    resp = client.get("/paquetes/diagnostico-posicion-precios")
    assert resp.status_code == 200
    assert "/paquetes-docs/diagnostico-posicion-precios.md" in resp.text


def test_paquetes_docs_mount_serves_real_one_pager() -> None:
    resp = client.get("/paquetes-docs/growth-operacion.md")
    assert resp.status_code == 200
    assert "Growth" in resp.text


def test_paquetes_index_degrades_cleanly_without_any_sales_env_vars(monkeypatch) -> None:
    for offer in OFFERS:
        monkeypatch.delenv(offer.stripe_env_var, raising=False)
    monkeypatch.delenv("CALENDLY_URL", raising=False)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("OPERATOR_NAME", raising=False)
    monkeypatch.delenv("OPERATOR_BIO", raising=False)
    resp = client.get("/paquetes")
    assert resp.status_code == 200
    assert "mailto:?subject=" in resp.text
    assert "TODO-OPERADOR" in resp.text


def test_paquetes_offer_page_uses_configured_stripe_link(monkeypatch) -> None:
    offer = OFFERS[0]
    monkeypatch.setenv(offer.stripe_env_var, "https://buy.stripe.com/live_test")
    resp = client.get(f"/paquetes/{offer.slug}")
    assert resp.status_code == 200
    assert "https://buy.stripe.com/live_test" in resp.text
