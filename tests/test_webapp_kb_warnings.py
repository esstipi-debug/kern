"""The /api/jobs response exposes kb_warnings so clients see graph degradation."""

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

client = TestClient(app)


def test_jobs_response_exposes_kb_warnings_key():
    r = client.post(
        "/api/jobs",
        data={"brief": "evaluate our SC leadership", "params": '{"scores": "3 2 3 1 1", "name": "T"}'},
    )
    assert r.status_code == 200
    assert "kb_warnings" in r.json()
