"""End-to-end de POST /api/demo-gap-value: los dos archivos van juntos o ninguno."""
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

RECEIPTS_CSV = "supplier,product_id,order_date,receipt_date,declared_lead_time_days\n" + "".join(
    f"Austral,SKU-X,2024-{m:02d}-01,2024-{m:02d}-21,60\n" for m in range(1, 8)
)
DEMAND_CSV = "date,product_id,quantity,unit_cost\n" + "".join(
    f"2024-{(w // 4) + 1:02d}-{(w % 4) * 7 + 1:02d},SKU-X,{40 + (w % 7) * 5},100\n"
    for w in range(40)
)


@pytest.fixture()
def isolated_stores(tmp_path, monkeypatch):
    leads = tmp_path / "leads.jsonl"
    reports = tmp_path / "leads-reports"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    monkeypatch.setattr(appmod, "LEAD_REPORTS_DIR", reports)
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    return leads, reports


def test_sample_devuelve_las_dos_monedas_y_persiste_el_lead(isolated_stores):
    leads, reports = isolated_stores
    r = client.post("/api/demo-gap-value", data={"email": "Gap@Test.com", "use_sample": "true"})
    assert r.status_code == 200
    d = r.json()

    assert d["status"] == "ok"
    h = d["headline"]
    assert h["cash_release"] > 0 and h["exposure"] > 0  # los dos lados se reportan
    assert h["recurring_saving"] == pytest.approx(h["cash_release"] * h["holding_rate"], rel=1e-3)
    assert h["n_over"] + h["n_under"] == h["n_skus"]

    lead_dir = reports / safe_lead_dirname("gap@test.com")
    report = (lead_dir / "gap_value_report.md").read_text(encoding="utf-8")
    draft = (lead_dir / "gap_value_followup_draft.md").read_text(encoding="utf-8")
    assert "Las dos monedas" in report and "SS actual" in report
    assert "BORRADOR" in draft and "nunca envia correo" in draft

    record = json.loads(leads.read_text(encoding="utf-8").strip())
    assert record["source"] == "demo-gap-value"
    assert record["status"] == "ok"


def test_los_dos_uploads_propios_funcionan(isolated_stores):
    r = client.post(
        "/api/demo-gap-value",
        data={"email": "up@test.com"},
        files={
            "receipts": ("recepciones.csv", RECEIPTS_CSV, "text/csv"),
            "demand": ("movimientos.csv", DEMAND_CSV, "text/csv"),
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["dataset"] == "recepciones.csv + movimientos.csv"
    # Declarado 60 contra 20 real: sobra buffer, se libera caja y no hay exposicion.
    assert d["headline"]["cash_release"] > 0
    assert d["headline"]["exposure"] == 0


def test_un_solo_archivo_es_400_que_explica_por_que(isolated_stores):
    r = client.post(
        "/api/demo-gap-value",
        data={"email": "up@test.com"},
        files={"receipts": ("recepciones.csv", RECEIPTS_CSV, "text/csv")},
    )
    assert r.status_code == 400
    assert "LOS DOS archivos" in r.json()["detail"]


def test_demanda_sin_costo_es_qa_fallado_no_una_cifra_inventada(isolated_stores):
    leads, reports = isolated_stores
    sin_costo = "\n".join(
        line.rsplit(",", 1)[0] for line in DEMAND_CSV.strip().split("\n")
    ) + "\n"
    r = client.post(
        "/api/demo-gap-value",
        data={"email": "qa@test.com"},
        files={
            "receipts": ("recepciones.csv", RECEIPTS_CSV, "text/csv"),
            "demand": ("sin_costo.csv", sin_costo, "text/csv"),
        },
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "qa_failed"
    assert any("maestro de articulos" in i for i in d["qa_issues"])
    assert not (reports / safe_lead_dirname("qa@test.com")).exists()
    assert json.loads(leads.read_text(encoding="utf-8").strip())["status"] == "qa_failed"


def test_email_invalido_se_rechaza_antes_de_leer_nada(isolated_stores):
    r = client.post("/api/demo-gap-value", data={"email": "no-es-mail", "use_sample": "true"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid email"
