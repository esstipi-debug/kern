"""Task 6: JobResult exposes the tool's structured report object to in-process
callers (the src/simworld/ harness) — populated only on the success path, and
never leaked onto a serialized/HTTP surface.
"""

import inspect

import pandas as pd
import pytest

from scm_agent.orchestrator import Orchestrator
from scm_agent.types import STATUS_NEEDS_DATA, STATUS_OK, JobResult

pytest.importorskip("fastapi")
# /api/jobs is a multipart Form route; importing webapp.app raises without
# python-multipart installed. Same guard as tests/test_webapp.py: try the
# canonical name first (python-multipart >= 0.0.26) to avoid the
# PendingDeprecationWarning on the legacy `import multipart` alias.
try:
    import python_multipart  # noqa: F401

    _HAS_MULTIPART = True
except ImportError:
    try:
        import multipart  # noqa: F401

        _HAS_MULTIPART = True
    except ImportError:
        _HAS_MULTIPART = False

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as webapp_app_module  # noqa: E402
import webapp.mcp_server as mcp_server_module  # noqa: E402
from webapp.app import app  # noqa: E402

requires_multipart = pytest.mark.skipif(not _HAS_MULTIPART, reason="python-multipart not installed")

client = TestClient(app)


def _demand_csv(tmp_path):
    rows = []
    for week in range(20):
        for sku in ("SKU-1", "SKU-2"):
            rows.append({
                "date": pd.Timestamp("2025-01-06") + pd.Timedelta(weeks=week),
                "product_id": sku,
                "quantity": 10 + week % 5,
                "unit_cost": 12.0,
                "lead_time_days": 14,
            })
    csv_path = tmp_path / "demand.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


def test_jobresult_report_defaults_to_none():
    r = JobResult(status="ok", tool=None, confidence=1.0, deliverables={}, summary="")
    assert r.report is None


def test_orchestrator_populates_report_on_success(tmp_path):
    csv = _demand_csv(tmp_path)

    orch = Orchestrator(clients_root=None)
    result = orch.run(
        "set reorder points and safety stock for these SKUs",
        data_path=str(csv),
        job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )
    assert result.status == STATUS_OK, result.summary
    assert result.report is not None
    assert hasattr(result.report, "recommendations")

    recs = result.report.recommendations
    assert recs, "expected at least one per-SKU recommendation"
    for rec in recs:
        assert rec.product_id
        assert rec.reorder_point is not None
        # (s,Q) carries order_quantity, (R,S) carries order_up_to — every
        # recommendation must carry at least one of the two policy numbers
        # the harness reads.
        assert rec.order_quantity is not None or rec.order_up_to is not None


def test_orchestrator_leaves_report_none_on_a_failing_path(tmp_path):
    """The success/failure asymmetry is load-bearing: a later task uses
    `result.report is None` to decide whether Kern produced a usable policy at
    all. An implementation that populates `report` on every return path would
    pass the happy-path test above but must fail here — a request with no data
    file fails with needs_data, and JobResult.report must stay None.
    """
    orch = Orchestrator(clients_root=None)
    result = orch.run(
        "set reorder points and safety stock for these SKUs",
        job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )
    assert result.status == STATUS_NEEDS_DATA, result.summary
    assert result.report is None


@requires_multipart
def test_webapp_payload_does_not_leak_the_report(tmp_path):
    """Behavioral proof, not just a source grep: build a real /api/jobs
    response for a request that succeeds (so JobResult.report is populated,
    per the test above) and confirm the field that actually ships over HTTP
    never carries it.
    """
    csv = _demand_csv(tmp_path)
    with open(csv, "rb") as fh:
        r = client.post(
            "/api/jobs",
            data={
                "brief": "set reorder points and safety stock for these SKUs",
                "job_type": "inventory_optimization",
            },
            files={"file": ("demand.csv", fh, "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "report" not in body


def test_webapp_source_never_serializes_jobresult_wholesale():
    """Cheap backstop on top of the behavioral test above (runs even without
    python-multipart installed): neither webapp/app.py nor webapp/mcp_server.py
    ever calls asdict() on the whole JobResult — only on result.guided, which
    is itself always JSON-safe.
    """
    for module in (webapp_app_module, mcp_server_module):
        src = inspect.getsource(module)
        assert "asdict(result)" not in src
