"""Tests for GET /api/metrics (E8): aggregate, PII-free funnel counts from
leads.jsonl, gated behind LINCHPIN_API_KEY like POST /api/jobs.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401  (canonical name)
except ImportError:
    pytest.importorskip("multipart")  # legacy name

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _open_defaults(monkeypatch):
    """Every test starts from the shipped default: no throttle, no API key."""
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    monkeypatch.setattr(security, "API_KEY", "")
    yield


@pytest.fixture()
def isolated_leads(tmp_path, monkeypatch):
    leads = tmp_path / "leads.jsonl"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    return leads


def _write(leads_path, *records):
    with leads_path.open("a", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")


def test_empty_or_missing_leads_file_returns_all_zero_counts(isolated_leads):
    # File doesn't even exist yet (no lead captured since deploy) - must not crash.
    body = client.get("/api/metrics").json()
    assert body == {
        "leads": {"total_captures": 0, "unique_emails": 0, "by_source": {}},
        "demo_scan": {"total_runs": 0, "by_status": {}, "by_dataset": {}},
    }


def test_aggregates_leads_and_demo_scan_records_separately(isolated_leads):
    _write(
        isolated_leads,
        {"email": "a@example.com", "source": "demo", "ts": "2026-07-01T00:00:00Z"},
        {"email": "b@example.com", "source": "demo-scan", "ts": "2026-07-01T00:00:00Z",
         "dataset": "sample_stock_snapshot.csv", "status": "ok", "result": {"eo_value": 1000}},
        {"email": "c@example.com", "source": "demo-scan", "ts": "2026-07-01T00:00:00Z",
         "dataset": "upload.csv", "status": "qa_failed", "result": None},
    )
    body = client.get("/api/metrics").json()
    assert body["leads"]["total_captures"] == 3
    assert body["leads"]["unique_emails"] == 3
    assert body["leads"]["by_source"] == {"demo": 1, "demo-scan": 2}
    assert body["demo_scan"]["total_runs"] == 2
    assert body["demo_scan"]["by_status"] == {"ok": 1, "qa_failed": 1}
    assert body["demo_scan"]["by_dataset"] == {"sample_stock_snapshot.csv": 1, "upload.csv": 1}


def test_repeat_email_counts_once_toward_unique_but_every_capture_toward_total(isolated_leads):
    _write(
        isolated_leads,
        {"email": "Same@Example.com", "source": "demo", "ts": "t1"},
        {"email": "same@example.com", "source": "demo-scan", "ts": "t2",
         "dataset": "d.csv", "status": "ok", "result": {}},
    )
    body = client.get("/api/metrics").json()
    assert body["leads"]["total_captures"] == 2
    assert body["leads"]["unique_emails"] == 1  # case-insensitive dedupe


def test_response_never_contains_a_raw_email(isolated_leads):
    _write(isolated_leads, {"email": "secret-lead@example.com", "source": "demo", "ts": "t1"})
    assert "secret-lead@example.com" not in client.get("/api/metrics").text


def test_an_email_shaped_dataset_filename_is_sanitized_not_leaked_verbatim(isolated_leads):
    # The "dataset" field is a caller-controlled upload filename (POST
    # /api/demo-scan lets the lead name their own file) - regression for a
    # real leak the adversarial review found: an upload literally named
    # "victim@example.com.csv" landed verbatim as a by_dataset response key.
    _write(isolated_leads, {"email": "a@example.com", "source": "demo-scan", "ts": "t1",
                            "dataset": "victim@example.com.csv", "status": "ok"})
    resp = client.get("/api/metrics")
    assert "victim@example.com.csv" not in resp.text
    assert "@" not in resp.text
    assert resp.json()["demo_scan"]["by_dataset"] == {"victimexample.com.csv": 1}


def test_a_malformed_line_is_skipped_not_a_crash(isolated_leads):
    isolated_leads.parent.mkdir(parents=True, exist_ok=True)
    with isolated_leads.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
        handle.write(json.dumps({"email": "ok@example.com", "source": "demo", "ts": "t1"}) + "\n")
        handle.write("\n")  # blank line, also tolerated
    body = client.get("/api/metrics").json()
    assert body["leads"]["total_captures"] == 1  # the malformed + blank lines don't count


@pytest.mark.parametrize("bad_line", ['"just a string"', "42", '["a", "b"]', "null"])
def test_a_syntactically_valid_but_non_object_line_is_skipped_not_a_crash(isolated_leads, bad_line):
    # json.loads succeeds on these (so the old `except json.JSONDecodeError`
    # alone let them through to `rec.get(...)`, which crashes on anything
    # that isn't a dict) - regression for a real, reproduced 500.
    isolated_leads.parent.mkdir(parents=True, exist_ok=True)
    with isolated_leads.open("a", encoding="utf-8") as handle:
        handle.write(bad_line + "\n")
        handle.write(json.dumps({"email": "ok@example.com", "source": "demo", "ts": "t1"}) + "\n")
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    assert resp.json()["leads"]["total_captures"] == 1


def test_an_unhashable_source_or_status_value_is_skipped_not_a_crash(isolated_leads):
    # A dict/list-valued "source" or "status" would crash a plain
    # `by_source[source] = ...` dict-keying line (TypeError: unhashable
    # type) - _metrics_label must reduce it to a safe string first.
    _write(
        isolated_leads,
        {"email": "a@example.com", "source": ["nested", "list"], "ts": "t1"},
        {"email": "b@example.com", "source": "demo-scan", "ts": "t2",
         "dataset": "d.csv", "status": {"weird": "dict"}},
    )
    resp = client.get("/api/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["leads"]["total_captures"] == 2
    assert body["leads"]["by_source"]["unknown"] == 1  # non-string source -> "unknown"


def test_bucket_count_is_capped_and_overflow_folds_into_other(isolated_leads):
    # POST /api/leads' "source" field is caller-controlled with no fixed
    # enum - an attacker scripting distinct source values per request must
    # not be able to grow the response's by_source dict without bound.
    _write(isolated_leads, *[
        {"email": f"lead{i}@example.com", "source": f"src-{i}", "ts": "t"}
        for i in range(40)
    ])
    by_source = client.get("/api/metrics").json()["leads"]["by_source"]
    assert len(by_source) == 26  # _METRICS_MAX_BUCKETS (25) distinct labels + "other"
    assert by_source["other"] == 15  # the 15 that didn't fit


def test_whitespace_variant_emails_dedupe_to_one_unique_count(isolated_leads):
    _write(
        isolated_leads,
        {"email": "foo@example.com", "source": "demo", "ts": "t1"},
        {"email": " foo@example.com", "source": "demo", "ts": "t2"},
        {"email": "foo@example.com ", "source": "demo", "ts": "t3"},
    )
    body = client.get("/api/metrics").json()
    assert body["leads"]["total_captures"] == 3
    assert body["leads"]["unique_emails"] == 1


def test_missing_source_or_status_falls_back_to_unknown(isolated_leads):
    _write(
        isolated_leads,
        {"email": "a@example.com", "ts": "t1"},  # no "source" at all
        {"email": "b@example.com", "source": "demo-scan", "ts": "t2", "dataset": "d.csv"},  # no "status"
    )
    body = client.get("/api/metrics").json()
    assert body["leads"]["by_source"]["unknown"] == 1
    assert body["demo_scan"]["by_status"]["unknown"] == 1


def test_api_key_enforced_when_configured(monkeypatch, isolated_leads):
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    assert client.get("/api/metrics").status_code == 401
    assert client.get("/api/metrics", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/metrics", headers={"X-API-Key": "s3cret"}).status_code == 200


def test_api_open_when_no_key_set(isolated_leads):
    assert client.get("/api/metrics").status_code == 200


def test_rate_limit_trips_after_threshold(monkeypatch, isolated_leads):
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 2)
    monkeypatch.setattr(security, "RATE_WINDOW", 60)
    assert client.get("/api/metrics").status_code == 200
    assert client.get("/api/metrics").status_code == 200
    blocked = client.get("/api/metrics")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
