"""La descarga del `.xlsx` de /demo: llega, se abre en Excel, y no lee otra cosa.

El archivo se sirve por una ruta explicita y NO por `app.mount(StaticFiles(...))`,
y esa decision tiene una razon concreta: `mimetypes` de Python no conoce `.xlsx` y
la imagen base `python:3.12-slim` no trae `/etc/mime.types`, asi que Starlette
adivinaria `text/plain`. El navegador mostraria sopa XML en vez de abrir Excel --
en el deploy, y no en el laptop de nadie, que es la peor forma de fallar.
"""
from __future__ import annotations

import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("openpyxl")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "LEADS_FILE", tmp_path / "leads.jsonl")
    monkeypatch.setattr(appmod, "LEAD_REPORTS_DIR", tmp_path / "leads-reports")
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)


def _scan() -> dict:
    r = client.post("/api/demo-lead-time", data={"email": "planilla@x.com", "use_sample": "true"})
    assert r.status_code == 200, r.text
    return r.json()


def test_el_scan_devuelve_un_link_al_libro(isolated):
    d = _scan()
    assert d["status"] == "ok"
    assert d["workbook_url"].startswith("/demo-output/")
    assert d["workbook_url"].endswith("/plazo_real.xlsx")


def test_la_descarga_llega_como_excel_de_verdad(isolated):
    r = client.get(_scan()["workbook_url"])
    assert r.status_code == 200
    # El punto entero de la ruta explicita.
    assert r.headers["content-type"] == XLSX
    assert "plazo_real.xlsx" in r.headers.get("content-disposition", "")
    assert r.content[:2] == b"PK", "un .xlsx es un zip; esto no lo es"
    with zipfile.ZipFile(__import__("io").BytesIO(r.content)) as z:
        assert any(n.startswith("xl/charts/chart") for n in z.namelist()), "sin graficos no es el entregable"


def test_el_arbol_servido_no_es_un_lector_de_archivos(isolated):
    url = _scan()["workbook_url"]
    carpeta = url.rsplit("/", 1)[0]
    for ruta in (
        "/demo-output/../../etc/passwd",
        "/demo-output/loquesea/secrets.env",
        f"{carpeta}/otro.xlsx",
        f"{carpeta}/../otra/plazo_real.xlsx",
    ):
        assert client.get(ruta).status_code == 404, ruta


def test_con_qa_fallado_no_se_ofrece_libro(isolated):
    """Sin brecha no hay entregable, y la respuesta no puede insinuar que si."""
    sin_declarado = (
        "supplier,order_date,receipt_date\n"
        "ACME,2026-01-01,2026-02-04\nACME,2026-02-01,2026-03-10\nACME,2026-03-01,2026-03-29\n"
    )
    r = client.post(
        "/api/demo-lead-time",
        data={"email": "planilla@x.com"},
        files={"file": ("r.csv", sin_declarado.encode(), "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "qa_failed"
    assert "workbook_url" not in d


def test_el_operador_recibe_su_copia_junto_al_markdown(isolated, tmp_path):
    """El .md es para el operador; el .xlsx tambien. Ese arbol nunca se sirve."""
    from webapp.demo_scan import safe_lead_dirname

    _scan()
    lead_dir = (tmp_path / "leads-reports") / safe_lead_dirname("planilla@x.com")
    assert (lead_dir / "plazo_real.xlsx").is_file()
    assert (lead_dir / "lead_time_report.md").is_file()
