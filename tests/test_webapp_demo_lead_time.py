"""End-to-end tests for POST /api/demo-lead-time (salida #1 del embudo /demo).

Mismo contrato que /api/demo-scan: sample -> titular con dos numeros; artefactos
del lead solo cuando el QA pasa; linea de telemetria siempre; y los controles de
subida de SECURITY.md, que ahora viven en `_stage_csv_upload` compartido.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401  (canonical name, python-multipart >= 0.0.26)
except ImportError:
    pytest.importorskip("multipart")  # legacy name; skips the module if also absent
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402
from webapp.demo_scan import safe_lead_dirname  # noqa: E402

client = TestClient(app)

GOOD_CSV = (
    "supplier,order_date,receipt_date,declared_lead_time_days,product_id\n"
    "ACME,2026-01-01,2026-02-04,21,SKU-1\n"
    "ACME,2026-02-01,2026-03-10,21,SKU-2\n"
    "ACME,2026-03-01,2026-03-29,21,SKU-3\n"
)

# El campo del ERP sin fechas: la promesa, que no puede pasar por medicion.
PROMISE_ONLY_CSV = "supplier,lead_time_days\nACME,21\nACME,21\nACME,21\n"


@pytest.fixture()
def isolated_stores(tmp_path, monkeypatch):
    leads = tmp_path / "leads.jsonl"
    reports = tmp_path / "leads-reports"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    monkeypatch.setattr(appmod, "LEAD_REPORTS_DIR", reports)
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    return leads, reports


def test_sample_devuelve_declarado_y_observado_y_persiste_el_lead(isolated_stores):
    leads, reports = isolated_stores
    r = client.post("/api/demo-lead-time", data={"email": "Lead@Test.com", "use_sample": "true"})
    assert r.status_code == 200
    d = r.json()

    assert d["status"] == "ok"
    h = d["headline"]
    assert h["worst_declared"] > 0 and h["worst_observed"] > 0
    assert h["worst_observed"] != h["worst_declared"]  # el gap es el producto
    assert h["n_skus_to_recalculate"] > 0
    assert d["cta_url"] == "/paquetes/diagnostico-arranque"
    # El sample trae una recepcion anterior a su orden, como cualquier export real.
    assert d["dropped"]["plazo_negativo"] == 1

    lead_dir = reports / safe_lead_dirname("lead@test.com")
    report = (lead_dir / "lead_time_report.md").read_text(encoding="utf-8")
    draft = (lead_dir / "lead_time_followup_draft.md").read_text(encoding="utf-8")
    assert "Plazo por proveedor" in report
    assert "BORRADOR" in draft and "nunca envia correo" in draft

    record = json.loads(leads.read_text(encoding="utf-8").strip())
    assert record["source"] == "demo-lead-time"
    assert record["status"] == "ok"


def test_upload_propio_funciona_igual(isolated_stores):
    r = client.post(
        "/api/demo-lead-time",
        data={"email": "up@test.com"},
        files={"file": ("recepciones.csv", GOOD_CSV, "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["dataset"] == "recepciones.csv"
    assert d["headline"]["worst_declared"] == 21.0


def test_el_campo_del_erp_sin_fechas_es_un_400_que_nombra_lo_que_falta(isolated_stores):
    r = client.post(
        "/api/demo-lead-time",
        data={"email": "up@test.com"},
        files={"file": ("promesa.csv", PROMISE_ONLY_CSV, "text/csv")},
    )
    assert r.status_code == 400
    assert "fecha de orden" in r.json()["detail"]


def test_qa_fallado_no_escribe_artefactos_pero_si_telemetria(isolated_stores):
    leads, reports = isolated_stores
    sin_declarado = "supplier,order_date,receipt_date\n" + "".join(
        f"ACME,2026-0{m}-01,2026-0{m}-20\n" for m in (1, 2, 3)
    )
    r = client.post(
        "/api/demo-lead-time",
        data={"email": "qa@test.com"},
        files={"file": ("sin_declarado.csv", sin_declarado, "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "qa_failed"
    # never-unprotected: el "no" dice que falta y quien lo tiene.
    assert any("maestro de articulos" in i for i in d["qa_issues"])
    assert not (reports / safe_lead_dirname("qa@test.com")).exists()
    assert json.loads(leads.read_text(encoding="utf-8").strip())["status"] == "qa_failed"


def test_sin_archivo_ni_sample_es_400(isolated_stores):
    r = client.post("/api/demo-lead-time", data={"email": "x@test.com"})
    assert r.status_code == 400
    assert "recepciones" in r.json()["detail"]


def test_email_invalido_se_rechaza_antes_de_leer_nada(isolated_stores):
    r = client.post("/api/demo-lead-time", data={"email": "no-es-un-mail", "use_sample": "true"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid email"
