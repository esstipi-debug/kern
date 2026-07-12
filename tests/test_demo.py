"""Tests for the lead-gated self-serve demo: /demo, /api/leads, and the
/api/jobs `use_sample` path that runs the bundled demo dataset."""

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


def test_demo_page_is_served():
    r = client.get("/demo")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Kern" in r.text


def test_lead_capture_appends_normalized_record(tmp_path, monkeypatch):
    leads = tmp_path / "leads.jsonl"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)

    r = client.post("/api/leads", data={"email": "  Foo@Bar.com ", "source": "demo"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    lines = leads.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["email"] == "foo@bar.com"  # trimmed + lowercased
    assert rec["source"] == "demo"
    assert rec["ts"].endswith("Z")


def test_lead_capture_is_append_only(tmp_path, monkeypatch):
    leads = tmp_path / "leads.jsonl"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    client.post("/api/leads", data={"email": "a@x.com"})
    client.post("/api/leads", data={"email": "b@x.com"})
    assert len(leads.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize("bad", ["", "notanemail", "a@b", "x@y.", "@no.com", "no@dom .com"])
def test_lead_capture_rejects_invalid_email(bad, tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "LEADS_FILE", tmp_path / "leads.jsonl")
    r = client.post("/api/leads", data={"email": bad})
    assert r.status_code in (400, 422)


def test_jobs_use_sample_runs_without_an_upload():
    r = client.post(
        "/api/jobs",
        data={"brief": "set up reorder points and safety stock", "use_sample": "true"},
    )
    assert r.status_code == 200
    d = r.json()
    # Providing the bundled sample must keep the tool from bailing for missing data.
    assert d["status"] != "needs_data"
    assert d["tool"]


def test_jobs_without_sample_or_file_still_responds():
    # No file, no sample -> the orchestrator may ask for data, but must not 500.
    r = client.post("/api/jobs", data={"brief": "set up reorder points and safety stock"})
    assert r.status_code == 200
    assert "status" in r.json()
