"""HTTP contract for the ISO catalog surface. Isolated FastAPI app — no full boot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from src.iso_catalog import AVISO_DETECCION, HUECO_14083_TKM, SenalesOrden, evidencia_de_orden  # noqa: E402
from webapp.iso import render_iso_html, router  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iso" / "detectar.ejemplo.json"

app = FastAPI()
app.include_router(router)
client = TestClient(app)


@pytest.fixture(scope="module")
def ejemplo():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_get_api_iso_returns_stats_and_normas():
    r = client.get("/api/iso")
    assert r.status_code == 200
    body = r.json()
    assert "stats" in body
    assert "normas" in body
    assert body["stats"]["total"] == 53
    assert len(body["normas"]) == 53
    assert body["estado"] == "integrado"
    assert "conformidad" in body["aviso"].lower()


def test_post_detectar_contrato(ejemplo):
    r = client.post("/api/detectar", json=ejemplo["contratoPost"])
    assert r.status_code == 200
    body = r.json()
    assert body["aviso"] == AVISO_DETECCION
    ids = {row["id"] for row in body["detectadas"]}
    assert {"iatf-16949", "iso-14067", "iso-14083", "iso-9001"} <= ids
    gap = next(row for row in body["detectadas"] if row["id"] == "iso-14083")
    assert gap["huecos"] == [HUECO_14083_TKM]
    blob = json.dumps(body, ensure_ascii=False).lower()
    assert "cumple" not in blob
    assert "certificad" not in blob


def test_post_detectar_empty_profile(ejemplo):
    r = client.post("/api/detectar", json=ejemplo["perfilVacio"])
    assert r.status_code == 200
    assert r.json()["detectadas"] == []


def test_post_detectar_rejects_malformed_body():
    assert client.post("/api/detectar", json=["automotriz"]).status_code == 400
    assert client.post("/api/detectar", json={"clientePide": "iatf"}).status_code == 400
    assert client.post("/api/detectar", json={"kern": ["sap"]}).status_code == 400
    assert client.post("/api/detectar", json={"texto": ["no"]}).status_code == 400


def test_get_iso_orden_monitor_signals():
    r = client.get("/api/iso/orden", params={"ciclo": True, "scrap": True, "kg": True, "tkm": False})
    assert r.status_code == 200
    body = r.json()
    ids = [e["id"] for e in body["evidencias"]]
    assert ids == ["iso-9001", "iso-50001", "iso-14067"]
    assert body["huecos"] == [HUECO_14083_TKM]
    assert "falta t·km" in body["resumen"]
    assert "cumple" not in body["resumen"].lower()


def test_get_iso_orden_rejects_unknown_module():
    r = client.get("/api/iso/orden", params={"modulos": "smartpro,erp-fantasma"})
    assert r.status_code == 400


def test_iso_page_shows_native_evidence_and_14083_gap():
    panel = evidencia_de_orden(
        SenalesOrden(tiene_ciclo=True, tiene_scrap=True, tiene_kg=True, tiene_tkm=False)
    )
    html = render_iso_html(panel)
    assert "Evidencia ISO de esta orden" in html
    assert "ISO 9001" in html
    assert "ISO 50001" in html
    assert "ISO 14067" in html
    assert "t·km" in html
    assert "cumple ISO" not in html
    assert "la planta cumple" not in html.lower()
    page = client.get("/iso")
    assert page.status_code == 200
    assert "Evidencia ISO de esta orden" in page.text
    assert "ISO 9001" in page.text
    assert HUECO_14083_TKM in page.text


def test_iso_page_is_catalog_browser_not_only_order_panel():
    page = client.get("/iso")
    html = page.text
    assert page.status_code == 200
    assert "Catalogo ISO de operaciones y cadena" in html
    assert 'id="iso-q"' in html
    assert 'id="iso-dominio"' in html
    assert "Inventario" in html
    assert "Detectar aplicabilidad para una planta" in html
    assert "POST /api/detectar" in html
    assert "cumple ISO" not in html
    assert "la planta cumple" not in html.lower()
