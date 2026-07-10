"""End-to-end tests for POST /api/demo-scan (the /demo funnel endpoint).

Asserts the E2 acceptance criteria: sample dataset -> mini-report with a money
figure; lead artifacts persisted (only when QA passes); a telemetry line in
leads.jsonl always; and the SECURITY.md upload controls (size cap, traversal
containment, isolation) hold on this endpoint exactly like on /api/jobs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

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
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)  # off by default, like the other endpoint tests
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

    lead_dir = reports / safe_lead_dirname("lead@test.com")
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
    _leads, reports = isolated_stores
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

    # SECURITY.md claims the raw upload is never copied into the lead's folder --
    # only the two derived .md artifacts should exist there.
    lead_dir = reports / safe_lead_dirname("up@x.com")
    assert {p.name for p in lead_dir.iterdir()} == {"mini_report.md", "followup_email_draft.md"}


def test_rescanning_the_same_email_overwrites_with_latest_numbers(isolated_stores):
    leads, reports = isolated_stores
    small_csv = "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\nSKU-1,10,5.0,7.0,1\n"
    r1 = client.post(
        "/api/demo-scan",
        data={"email": "repeat@x.com"},
        files={"file": ("first.csv", small_csv.encode(), "text/csv")},
    )
    assert r1.status_code == 200
    r2 = client.post("/api/demo-scan", data={"email": "repeat@x.com", "use_sample": "true"})
    assert r2.status_code == 200
    assert r2.json()["dataset"] == "sample_stock_snapshot.csv"

    lead_dir = reports / safe_lead_dirname("repeat@x.com")
    assert "sample_stock_snapshot.csv" in (lead_dir / "mini_report.md").read_text(encoding="utf-8")
    assert len(leads.read_text(encoding="utf-8").splitlines()) == 2  # one telemetry line per scan


def test_binary_upload_is_a_clean_400_not_a_500(isolated_stores):
    # A real .xlsx (or any non-CSV binary) must never surface as a 500 -- the
    # client-side accept=".csv,.xlsx,.xls" filter is cosmetic, not a server guarantee.
    blob = bytes(range(256)) * 4
    r = client.post(
        "/api/demo-scan",
        data={"email": "binary@x.com"},
        files={"file": ("stock.xlsx", blob, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 400
    assert "CSV" in r.json()["detail"]


def test_duplicate_product_id_rows_sum_like_the_paid_jobs_do(isolated_stores):
    # excess_obsolete_job/abc_xyz_job/financial_kpis_job (reused as-is, same as
    # every paid package) never dedupe by product_id -- pin that here so a
    # future groupby/dedupe change is a visible, deliberate decision, not a
    # silent shift in the headline dollar figure that sells the Diagnostico.
    # daily_demand must stay nonzero -- 0.0 COGS makes DIO non-finite and trips
    # the QA gate before the dedup question is even reachable.
    single = "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\nSKU-1,500,0.1,10.0,300\n"
    doubled = (
        "product_id,on_hand,daily_demand,unit_cost,days_since_last_sale\n"
        "SKU-1,500,0.1,10.0,300\nSKU-1,500,0.1,10.0,300\n"
    )
    r1 = client.post(
        "/api/demo-scan", data={"email": "dup1@x.com"}, files={"file": ("a.csv", single.encode(), "text/csv")}
    )
    r2 = client.post(
        "/api/demo-scan", data={"email": "dup2@x.com"}, files={"file": ("b.csv", doubled.encode(), "text/csv")}
    )
    assert r1.json()["headline"]["n_skus"] == 1
    assert r2.json()["headline"]["n_skus"] == 2
    assert r2.json()["headline"]["eo_value"] == pytest.approx(2 * r1.json()["headline"]["eo_value"])


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

    assert not (reports / safe_lead_dirname("qa@x.com")).exists()  # QA fails => no deliverable
    rec = json.loads(leads.read_text(encoding="utf-8").splitlines()[0])
    assert rec["status"] == "qa_failed"
    assert rec["result"] is None


def test_demo_page_still_serves_and_sells_the_diagnostico():
    r = client.get("/demo")
    assert r.status_code == 200
    assert "Linchpin" in r.text
    assert "diagnostico-arranque" in r.text
    assert "plantilla_stock.csv" in r.text


def test_demo_scan_is_rate_limited(isolated_stores, monkeypatch):
    # Mirrors tests/test_webapp_security.py's pattern for the other public
    # POST endpoints -- a future refactor that drops the rate_limit
    # dependency (e.g. while copying the decorator) must fail this test.
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 1)
    ok = client.post("/api/demo-scan", data={"email": "rl1@x.com", "use_sample": "true"})
    assert ok.status_code == 200
    blocked = client.post("/api/demo-scan", data={"email": "rl2@x.com", "use_sample": "true"})
    assert blocked.status_code == 429


def test_lead_reports_dir_env_var_is_honored():
    # LEAD_REPORTS_DIR is resolved once at import time from
    # LINCHPIN_LEAD_REPORTS_DIR -- the isolated_stores fixture bypasses that by
    # monkeypatching the attribute directly, so exercise the real env plumbing
    # in a fresh subprocess (the operator checklist's Fly-volume persistence
    # depends on exactly this).
    code = "from webapp.app import LEAD_REPORTS_DIR; print(LEAD_REPORTS_DIR)"
    env = {**os.environ, "LINCHPIN_LEAD_REPORTS_DIR": "/tmp/custom-leads-dir", "PYTHONPATH": "."}
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(appmod._REPO_ROOT),
        env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().replace("\\", "/").endswith("/tmp/custom-leads-dir")
