"""Los tres embudos de /demo aceptan planilla, no solo CSV.

`jobs.intake.load_table` sabia leer .xlsx/.xlsm/.xls desde siempre, pero los tres
endpoints llamaban `pd.read_csv` directo, y la pagina declaraba `accept=".csv"`.
El cliente cuyo inventario vive en una planilla --el visitante arquetipico de
este embudo segun 05-GTM-KERN, no un caso borde-- tenia que abrir el archivo y
"Guardar como CSV" antes de que el diagnostico gratis funcionara: friccion en el
unico paso que el GTM exige que sea de cero contacto.

Lo que estos tests fijan:

* el mismo contenido, subido como .xlsx y como .csv, devuelve el mismo resultado;
* el staging preserva la extension, que es de lo que `load_table` despacha --
  aplanarla mandaria toda planilla por el camino de `read_csv`;
* un archivo ilegible sigue siendo 400 y no una traza.
"""
from __future__ import annotations

import io

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("openpyxl")
try:
    import python_multipart  # noqa: F401  (canonical name, python-multipart >= 0.0.26)
except ImportError:
    pytest.importorskip("multipart")  # legacy name; skips the module if also absent

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

RECEIPTS = pd.DataFrame(
    {
        "supplier": ["ACME"] * 3 + ["GLOBEX"] * 3,
        "order_date": [
            "2026-01-01", "2026-02-01", "2026-03-01",
            "2026-01-05", "2026-02-05", "2026-03-05",
        ],
        "receipt_date": [
            "2026-02-04", "2026-03-10", "2026-03-29",
            "2026-01-20", "2026-02-19", "2026-03-21",
        ],
        "declared_lead_time_days": [21] * 3 + [14] * 3,
        "product_id": [f"SKU-{i}" for i in range(1, 7)],
    }
)


@pytest.fixture()
def isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "LEADS_FILE", tmp_path / "leads.jsonl")
    monkeypatch.setattr(appmod, "LEAD_REPORTS_DIR", tmp_path / "leads-reports")
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def _post_lead_time(filename: str, payload: bytes, mime: str) -> dict:
    r = client.post(
        "/api/demo-lead-time",
        data={"email": "planilla@test.com"},
        files={"file": (filename, payload, mime)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_xlsx_de_recepciones_produce_el_mismo_resultado_que_el_csv(isolated_stores):
    """El formato del archivo no puede cambiar la medicion, solo la friccion."""
    from_xlsx = _post_lead_time("recepciones.xlsx", _xlsx_bytes(RECEIPTS), XLSX_MIME)
    from_csv = _post_lead_time(
        "recepciones.csv", RECEIPTS.to_csv(index=False).encode("utf-8"), "text/csv"
    )

    assert from_xlsx["status"] == "ok"
    assert from_xlsx["headline"] == from_csv["headline"]
    assert from_xlsx["findings"] == from_csv["findings"]


def test_xlsx_mide_la_brecha_y_no_se_come_la_promesa(isolated_stores):
    """La regla del §7.1 vale igual por la via planilla: lo observado sale de las fechas.

    El archivo trae `declared_lead_time_days` (21 y 14, el campo del ERP). Si el
    scan lo tomara como observacion, la brecha daria cero sobre el defecto exacto
    que viene a mostrar.
    """
    d = _post_lead_time("recepciones.xlsx", _xlsx_bytes(RECEIPTS), XLSX_MIME)
    h = d["headline"]
    assert h["worst_declared"] > 0 and h["worst_observed"] > 0
    assert h["worst_observed"] != h["worst_declared"]


def test_el_staging_preserva_la_extension(isolated_stores, monkeypatch):
    """`load_table` despacha por sufijo: aplanarlo manda toda planilla a read_csv."""
    seen: list[str] = []
    real = appmod.load_table

    def spy(path):
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr(appmod, "load_table", spy)
    _post_lead_time("Recepciones Q1.xlsx", _xlsx_bytes(RECEIPTS), XLSX_MIME)

    assert seen and seen[0].endswith(".xlsx")


def test_planilla_ilegible_es_400_y_no_una_traza(isolated_stores):
    r = client.post(
        "/api/demo-lead-time",
        data={"email": "planilla@test.com"},
        files={"file": ("roto.xlsx", b"esto no es un zip de office", XLSX_MIME)},
    )
    assert r.status_code == 400
    assert "no se pudo leer el archivo" in r.json()["detail"]


def test_stock_en_xlsx_tambien_entra(isolated_stores):
    """El embudo #2 (/api/demo-scan) comparte el staging, asi que comparte el arreglo."""
    stock = pd.DataFrame(
        {
            "product_id": [f"SKU-{i}" for i in range(1, 9)],
            "on_hand": [120, 40, 0, 900, 15, 300, 7, 210],
            "daily_demand": [2.0, 1.5, 0.8, 0.4, 3.2, 12.0, 0.1, 5.5],
            "unit_cost": [10.0, 25.0, 4.5, 80.0, 12.0, 3.0, 150.0, 22.0],
        }
    )
    r = client.post(
        "/api/demo-scan",
        data={"email": "planilla@test.com"},
        files={"file": ("stock.xlsx", _xlsx_bytes(stock), XLSX_MIME)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("ok", "qa_failed")  # entro y corrio; el veredicto es del QA
