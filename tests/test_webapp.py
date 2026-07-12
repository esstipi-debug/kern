"""Tests for the Inventory Planner FastAPI backend."""

import asyncio

import pytest

pytest.importorskip("fastapi")
# /api/jobs uses a multipart Form route, so importing webapp.app raises without
# python-multipart — skip the whole module (under either import name) rather than
# error at collection.
try:
    import python_multipart  # noqa: F401  (canonical name, python-multipart >= 0.0.26)
except ImportError:
    pytest.importorskip("multipart")  # legacy name; skips the module if also absent
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as webapp_app_module  # noqa: E402
from webapp.app import JOBS_OUTPUT_DIR, app  # noqa: E402

client = TestClient(app)

REQUIRED_SKU_KEYS = {
    "id", "method", "intermittent", "forecast", "error_std", "bias", "mae",
    "unit_cost", "lead_periods", "policy_kind", "reorder_point", "safety_stock",
    "z_factor", "cycle_investment", "ss_investment", "investment", "status", "history",
}


def test_portfolio_returns_full_portfolio():
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    d = r.json()
    assert len(d["skus"]) == 8
    for s in d["skus"]:
        assert REQUIRED_SKU_KEYS <= set(s)
        assert len(s["history"]) == 52
        # investment is cycle + safety, always
        assert s["investment"] == pytest.approx(s["cycle_investment"] + s["ss_investment"])


def test_real_engine_methods_present():
    d = client.get("/api/portfolio").json()
    methods = {s["method"] for s in d["skus"]}
    allowed = {"auto_ets", "tsb", "auto_modern", "ses", "croston"}
    assert methods <= allowed
    assert d["params"]["forecast_method"] == "auto"


def test_totals_internal_consistency():
    d = client.get("/api/portfolio").json()
    t = d["totals"]
    assert 0.0 <= t["scale"] <= 1.0
    assert t["final"] <= t["requested"] + 1e-6
    # final = cycle floor + scaled safety stock
    assert t["final"] == pytest.approx(t["cycle_floor"] + t["ss_total"] * t["scale"], rel=1e-6)


def test_budget_feasibility_transitions():
    feasible = client.get("/api/portfolio?budget=80000").json()["totals"]
    assert feasible["feasible"] and feasible["scale"] == pytest.approx(1.0)

    infeasible = client.get("/api/portfolio?budget=1000").json()["totals"]
    assert infeasible["feasible"] is False
    assert infeasible["scale"] == pytest.approx(0.0)


def test_service_level_raises_safety_stock():
    low = client.get("/api/portfolio?service_level=0.90").json()["skus"]
    high = client.get("/api/portfolio?service_level=0.99").json()["skus"]
    low_a = next(s for s in low if s["id"] == "SKU-A")
    high_a = next(s for s in high if s["id"] == "SKU-A")
    assert high_a["safety_stock"] > low_a["safety_stock"]


def test_lead_override_changes_policy():
    base = client.get("/api/portfolio").json()["skus"]
    overridden = client.get('/api/portfolio?lead_overrides={"SKU-A": 4}').json()["skus"]
    base_a = next(s for s in base if s["id"] == "SKU-A")
    over_a = next(s for s in overridden if s["id"] == "SKU-A")
    assert over_a["lead_periods"] == 4
    assert over_a["reorder_point"] > base_a["reorder_point"]


def test_input_validation():
    assert client.get("/api/portfolio?service_level=1.5").status_code == 422
    assert client.get("/api/portfolio?service_level=0").status_code == 422
    assert client.get("/api/portfolio?budget=-5").status_code == 422
    assert client.get("/api/portfolio?lead_overrides=not-json").status_code == 400


def test_lead_overrides_rejects_nonfinite_and_out_of_range():
    # Infinity / NaN JSON tokens must be rejected, not flow into the engine
    assert client.get("/api/portfolio", params={"lead_overrides": '{"SKU-A": Infinity}'}).status_code == 400
    assert client.get("/api/portfolio", params={"lead_overrides": '{"SKU-A": NaN}'}).status_code == 400
    # out of range and wrong types
    assert client.get("/api/portfolio", params={"lead_overrides": '{"SKU-A": 999}'}).status_code == 400
    assert client.get("/api/portfolio", params={"lead_overrides": '{"SKU-A": -1}'}).status_code == 400
    assert client.get("/api/portfolio", params={"lead_overrides": '{"SKU-A": true}'}).status_code == 400


def test_intermittent_reorder_distinct_from_order_up_to():
    """(R,S) reorder line uses lead-only risk; must not collapse into order-up-to S."""
    skus = client.get("/api/portfolio").json()["skus"]
    intermittent = [s for s in skus if s["intermittent"]]
    assert intermittent, "expected at least one intermittent SKU"
    for s in intermittent:
        assert s["policy_kind"] == "(R, S)"
        assert s["order_up_to"] is not None
        assert s["reorder_point"] < s["order_up_to"]  # distinct, not byte-identical


def test_static_assets_served():
    assert client.get("/").status_code == 200
    assert "id=\"root\"" in client.get("/").text
    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "/api/portfolio" in js.text


def test_health():
    d = client.get("/api/health").json()
    assert d["ok"] is True and d["skus"] == 8


# ---------------------------------------------------------------------------
# python-multipart guard — do NOT use pytest.importorskip at module level;
# that would skip the existing 13 webapp tests when multipart is absent.
# Try the canonical name first (python-multipart >= 0.0.26) to avoid the
# PendingDeprecationWarning emitted by the legacy `import multipart` alias.
# ---------------------------------------------------------------------------
try:
    import python_multipart  # noqa: F401  (canonical name in python-multipart >= 0.0.26)
    _HAS_MULTIPART = True
except ImportError:
    try:
        import multipart  # noqa: F401  (legacy alias, kept for older releases)
        _HAS_MULTIPART = True
    except ImportError:
        _HAS_MULTIPART = False

requires_multipart = pytest.mark.skipif(not _HAS_MULTIPART, reason="python-multipart not installed")


@requires_multipart
def test_jobs_leadership_via_params_no_file():
    r = client.post("/api/jobs", data={
        "brief": "evaluate our SC leadership",
        "job_type": "leadership_chain",
        "params": '{"scores": "3 2 3 1 1", "name": "Equipo X"}',
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tool"] == "leadership_chain"
    assert "chart" in body["deliverables"]
    assert body["download_urls"]["chart"].startswith("/jobs-output/")


@requires_multipart
def test_jobs_inventory_with_file_upload():
    with open("data/sample_demand_portfolio.csv", "rb") as fh:
        r = client.post(
            "/api/jobs",
            data={"brief": "set up reorder points and safety stock"},
            files={"file": ("demand.csv", fh, "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["tool"] == "inventory_optimization"
    assert "excel" in body["download_urls"]


@requires_multipart
def test_jobs_needs_data_status():
    r = client.post("/api/jobs", data={"brief": "set up reorder points"})
    assert r.status_code == 200
    assert r.json()["status"] == "needs_data"


@requires_multipart
def test_jobs_downloaded_file_is_served():
    r = client.post("/api/jobs", data={
        "brief": "evaluate leadership", "job_type": "leadership_chain",
        "params": '{"scores": "3 3 3 3 3"}',
    }).json()
    url = r["download_urls"]["report"]
    got = client.get(url)
    assert got.status_code == 200
    assert "CHAIN" in got.text


@requires_multipart
def test_jobs_upload_filename_traversal_is_contained():
    # A path-traversal filename must be reduced to a basename and written ONLY
    # inside the per-job mkdtemp subdir — never escaping JOBS_OUTPUT_DIR.
    csv = b"date,sku,qty\n2024-01-01,A,5\n"
    r = client.post(
        "/api/jobs",
        data={"brief": "set up reorder points and safety stock"},
        files={"file": ("../../evil_traversal.csv", csv, "text/csv")},
    )
    assert r.status_code == 200  # handled gracefully, not a 5xx
    assert not (JOBS_OUTPUT_DIR / "evil_traversal.csv").exists()
    assert not (JOBS_OUTPUT_DIR.parent / "evil_traversal.csv").exists()


@requires_multipart
def test_jobs_upload_too_large_rejected(monkeypatch):
    monkeypatch.setattr("webapp.app.MAX_UPLOAD_BYTES", 100)
    big = b"x" * 200
    r = client.post(
        "/api/jobs",
        data={"brief": "set up reorder points"},
        files={"file": ("big.csv", big, "text/csv")},
    )
    assert r.status_code == 413


@requires_multipart
def test_jobs_invalid_filename_takes_precedence_over_oversized_upload(monkeypatch):
    """Filename validity (_safe_upload_basename) runs in the async handler
    BEFORE the size check, matching the precedence the original single-function
    implementation had - when BOTH are wrong, the caller learns about the
    filename (400), not the size (413). The asyncio.to_thread split (which
    moved filename *containment* validation into the thread-offloaded
    _run_job_sync) must not silently reorder this."""
    monkeypatch.setattr("webapp.app.MAX_UPLOAD_BYTES", 5)
    big = b"x" * 50
    r = client.post(
        "/api/jobs",
        data={"brief": "set up reorder points"},
        files={"file": ("..", big, "text/csv")},
    )
    assert r.status_code == 400
    assert "filename" in r.json()["detail"]


def test_prune_old_jobs_removes_stale_dirs():
    import os
    import shutil
    import time

    from webapp.app import JOBS_TTL_SECONDS, _prune_old_jobs

    old = JOBS_OUTPUT_DIR / "old_job_test_dir"
    fresh = JOBS_OUTPUT_DIR / "fresh_job_test_dir"
    old.mkdir(exist_ok=True)
    fresh.mkdir(exist_ok=True)
    (old / "f.txt").write_text("x", encoding="utf-8")
    past = time.time() - JOBS_TTL_SECONDS - 100
    os.utime(old, (past, past))
    try:
        _prune_old_jobs()
        assert not old.exists()   # stale dir swept
        assert fresh.exists()     # fresh dir kept
    finally:
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(fresh, ignore_errors=True)


async def test_http_exception_raised_inside_a_thread_offloaded_call_propagates_correctly():
    """Direct proof of the mechanism /api/jobs now relies on: FastAPI's
    HTTPException, when raised inside a function dispatched via
    asyncio.to_thread (as _run_job_sync is), reaches the awaiting caller
    unchanged - not swallowed, not turned into a generic error, not lost.
    Independent of any specific upload scenario, since _run_job_sync's own
    exception path (the filename-containment check) is largely unreachable
    through the public API now that _safe_upload_basename pre-validates the
    filename in the async handler before dispatch.
    """
    from fastapi import HTTPException as _HTTPException

    def _raises() -> None:
        raise _HTTPException(status_code=400, detail="boom")

    with pytest.raises(_HTTPException) as exc_info:
        await asyncio.to_thread(_raises)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "boom"


def test_console_route_serves_the_prototype():
    r = client.get("/console")
    assert r.status_code == 200
    assert "Linchpin" in r.text  # the live console brand


@requires_multipart
async def test_jobs_does_not_block_a_concurrent_health_check(monkeypatch):
    """The regression this endpoint's asyncio.to_thread offload fixes: /api/jobs
    used to run the orchestrator SYNCHRONOUSLY inside its `async def` handler, so
    with a single event loop (WEB_CONCURRENCY=1 in production) a real analysis
    blocked every other request - including /api/health - until it finished.

    Proves the fix with genuine concurrency (httpx.AsyncClient over an in-process
    ASGITransport, both requests in flight on the SAME event loop via
    asyncio.gather - not TestClient, which serializes requests) rather than a
    wall-clock timing assertion: a (deterministically slow) job request and a
    health check are fired together, and the health check must complete WHILE
    the job is still sleeping. If /api/jobs regressed to blocking the loop
    directly, the health check could not complete until the job did, and the
    order below would flip.
    """
    order: list[str] = []
    real_run_job_sync = webapp_app_module._run_job_sync

    def _slow_run_job_sync(*args, **kwargs):
        import time as _time
        _time.sleep(0.3)  # simulate a real, CPU-bound orchestrator run
        result = real_run_job_sync(*args, **kwargs)
        order.append("job")
        return result

    monkeypatch.setattr(webapp_app_module, "_run_job_sync", _slow_run_job_sync)

    transport = httpx.ASGITransport(app=app)

    async def _slow_job():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.post("/api/jobs", data={
                "brief": "evaluate leadership", "job_type": "leadership_chain",
                "params": '{"scores": "3 3 3 3 3"}',
            })

    async def _health_check_shortly_after():
        await asyncio.sleep(0.05)  # let the job request start first
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/api/health")
        order.append("health")
        return r

    job_resp, health_resp = await asyncio.gather(_slow_job(), _health_check_shortly_after())

    assert job_resp.status_code == 200
    assert health_resp.status_code == 200 and health_resp.json()["ok"] is True
    # The health check (issued 50ms after the job started, itself near-instant)
    # finished FIRST - proving the event loop stayed free during the job's
    # 300ms of "orchestrator work" instead of being blocked by it.
    assert order == ["health", "job"]


@requires_multipart
def test_jobs_response_includes_citations_key():
    r = client.post("/api/jobs", data={
        "brief": "evaluate leadership", "job_type": "leadership_chain",
        "params": '{"scores": "3 3 3 3 3"}',
    })
    assert r.status_code == 200
    assert "citations" in r.json()  # L3 grounding key is always present


# -- guided: the never-unprotected contract, exposed to every /api/jobs caller ---
#
# Previously invisible: JobResult.guided (src/guided.py's GuidedOutcome) was
# computed by every orchestrator run but never left webapp/app.py - an API/UI
# caller had no structured way to know "this needs a ranked choice", "this is
# gated behind a finance approver", or "this needs a data file", short of
# parsing the free-text `summary`/`clarifications` strings. Now serialized via
# dataclasses.asdict (src/guided.py's dataclasses are frozen + JSON-plain, so
# this is a lossless, zero-custom-code mapping) under the "guided" key.


@requires_multipart
def test_jobs_response_guided_options_for_a_ranked_choice():
    r = client.post("/api/jobs", data={
        "brief": "evaluate leadership", "job_type": "leadership_chain",
        "params": '{"scores": "3 3 3 3 3"}',
    })
    assert r.status_code == 200
    guided = r.json()["guided"]
    assert guided is not None
    assert guided["status"] == "options"
    assert len(guided["options"]) >= 2
    assert sum(1 for o in guided["options"] if o["recommended"]) == 1
    assert guided["handoffs"] == [] and guided["escalation"] is None


@requires_multipart
def test_jobs_response_guided_handoff_for_needs_data():
    r = client.post("/api/jobs", data={"brief": "set up reorder points"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_data"
    guided = body["guided"]
    assert guided is not None
    assert guided["status"] == "handoff"
    assert len(guided["handoffs"]) >= 1
    assert guided["handoffs"][0]["risk_if_skipped"]  # never a bare/empty risk statement


@requires_multipart
def test_jobs_response_guided_escalation_for_an_over_threshold_restock():
    """odoo_replenishment's demo stand-in restocks ~$916 by default (see
    tests/test_connector_odoo_tool.py) - a low financial_threshold forces the
    ESCALATED path, and the API caller must see route_to/sla/reason, not just
    a generic 'ok' with no signal that finance sign-off is required."""
    r = client.post("/api/jobs", data={
        "brief": "connect odoo and plan replenishment from odoo",
        "job_type": "odoo_replenishment",
        "params": '{"financial_threshold": 500}',
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"  # STATUS_OK at the JobResult level...
    guided = body["guided"]
    assert guided["status"] == "escalated"  # ...but the guided contract says otherwise
    esc = guided["escalation"]
    assert esc["route_to"] and esc["sla"] and esc["reason"]
    assert len(esc["options"]) >= 2  # the ranked choices travel WITH the escalation
    assert guided["options"] == esc["options"]  # ...and stay visible at the top level too
