"""Tests for the Inventory Planner FastAPI backend."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

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
    # the real forecaster auto-routes intermittent SKUs to Croston, the rest to SES
    assert "croston" in methods
    assert "ses" in methods


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
