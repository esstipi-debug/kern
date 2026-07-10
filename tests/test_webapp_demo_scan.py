"""End-to-end tests for POST /api/demo-scan (the /demo funnel endpoint).

Asserts the E2 acceptance criteria: sample dataset -> mini-report with a money
figure; lead artifacts persisted (only when QA passes); a telemetry line in
leads.jsonl always; and the SECURITY.md upload controls (size cap, traversal
containment, isolation) hold on this endpoint exactly like on /api/jobs.
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
from webapp.app import app  # noqa: E402

client = TestClient(app)

GOOD_CSV = (
    "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\n"
    "SKU-1,320,6.0,7.0,3\n"
    "SKU-2,900,1.5,12.0,210\n"
    "SKU-3,500,0.0,9.5,260\n"
)


@pytest.fixture()
def isolated_stores(tmp_path, monkeypatch):
    leads = tmp_path / "leads.jsonl"
    reports = tmp_path / "leads-reports"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    monkeypatch.setattr(appmod, "LEAD_REPORTS_DIR", reports)
    return leads, reports


def test_sample_scan_returns_money_headline_and_persists_lead(isolated_stores):
    leads, reports = isolated_stores
    r = client.post("/api/demo-scan", data={"email": "Lead@Test.com", "use_sample": "true"})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["headline"]["eo_value"] > 0
    assert len(d["findings"]) == 3
    assert d["cta_url"] == "/paquetes/diagnostico-arranque"

    lead_dir = reports / "lead_at_test.com"
    mini = lead_dir / "mini_report.md"
    draft = lead_dir / "followup_email_draft.md"
    assert mini.exists() and draft.exists()
    assert "$" in mini.read_text(encoding="utf-8")
    assert "BORRADOR" in draft.read_text(encoding="utf-8")

    lines = leads.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["email"] == "lead@test.com"
    assert rec["source"] == "demo-scan"
    assert rec["dataset"] == "sample_stock_snapshot.csv"
    assert rec["status"] == "ok"
    assert rec["result"]["eo_value"] > 0


def test_upload_scan_end_to_end(isolated_stores):
    r = client.post(
        "/api/demo-scan",
        data={"email": "up@x.com"},
        files={"file": ("mi_stock.csv", GOOD_CSV.encode(), "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["dataset"] == "mi_stock.csv"
    assert d["headline"]["eo_value"] > 0


def test_upload_too_large_rejected(isolated_stores):
    blob = b"x" * (appmod.MAX_UPLOAD_BYTES + 1)
    r = client.post(
        "/api/demo-scan",
        data={"email": "big@x.com"},
        files={"file": ("big.csv", blob, "text/csv")},
    )
    assert r.status_code == 413


def test_upload_traversal_filename_is_contained(isolated_stores, tmp_path):
    r = client.post(
        "/api/demo-scan",
        data={"email": "trav@x.com"},
        files={"file": ("../../evil.csv", GOOD_CSV.encode(), "text/csv")},
    )
    # basename()d into the isolated scan dir -> processes normally, never escapes.
    assert r.status_code == 200
    assert not (appmod.JOBS_OUTPUT_DIR.parent / "evil.csv").exists()
    assert not (appmod.JOBS_OUTPUT_DIR.parent.parent / "evil.csv").exists()


@pytest.mark.parametrize("bad", ["", "notanemail", "a@b", "@no.com"])
def test_invalid_email_rejected(bad, isolated_stores):
    r = client.post("/api/demo-scan", data={"email": bad, "use_sample": "true"})
    assert r.status_code in (400, 422)


def test_no_file_and_no_sample_is_actionable_400(isolated_stores):
    r = client.post("/api/demo-scan", data={"email": "a@b.com"})
    assert r.status_code == 400
    assert "use_sample" in r.json()["detail"] or "CSV" in r.json()["detail"]


def test_missing_columns_yield_actionable_400(isolated_stores):
    csv = "foo,bar\n1,2\n"
    r = client.post(
        "/api/demo-scan",
        data={"email": "cols@x.com"},
        files={"file": ("weird.csv", csv.encode(), "text/csv")},
    )
    assert r.status_code == 400
    assert "columnas requeridas" in r.json()["detail"]
    assert "product_id" in r.json()["detail"]


def test_unreadable_csv_yields_400(isolated_stores):
    r = client.post(
        "/api/demo-scan",
        data={"email": "empty@x.com"},
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert r.status_code == 400


def test_qa_failure_writes_no_artifacts_but_logs_telemetry(isolated_stores):
    leads, reports = isolated_stores
    # No unit_cost -> zero inventory value -> the QA gate blocks the deliverable.
    csv = "product_id,on_hand,daily_demand\nSKU-1,10,1.0\n"
    r = client.post(
        "/api/demo-scan",
        data={"email": "qa@x.com"},
        files={"file": ("nocost.csv", csv.encode(), "text/csv")},
    )
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "qa_failed"
    assert d["qa_issues"]
    assert "headline" not in d

    assert not (reports / "qa_at_x.com").exists()  # QA fails => no deliverable
    rec = json.loads(leads.read_text(encoding="utf-8").splitlines()[0])
    assert rec["status"] == "qa_failed"
    assert rec["result"] is None


def test_demo_page_still_serves_and_sells_the_diagnostico():
    r = client.get("/demo")
    assert r.status_code == 200
    assert "Linchpin" in r.text
    assert "diagnostico-arranque" in r.text
    assert "plantilla_stock.csv" in r.text
