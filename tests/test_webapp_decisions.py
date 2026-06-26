"""HTTP tests for the decision-support API (webapp/decisions.py)."""

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

client = TestClient(app)

CARD_KEYS = {"titulo", "veredicto", "guardrails", "opciones", "por_que", "cita", "supuestos"}


def _assert_card(payload: dict) -> None:
    assert CARD_KEYS <= set(payload)
    assert payload["veredicto"]["nivel"] in {"verde", "amarillo", "rojo"}
    assert len(payload["opciones"]) >= 2
    assert sum(1 for o in payload["opciones"] if o["recommended"]) == 1


def test_capacidad_ok():
    r = client.post("/api/decide/capacidad", json={
        "entrada_unidades_dia": 400, "salida_unidades_dia": 400,
        "num_operarios": 5, "unidades_hora_operario": 25, "horas_turno": 8,
    })
    assert r.status_code == 200
    _assert_card(r.json())


def test_capacidad_pydantic_422_on_zero_operarios():
    r = client.post("/api/decide/capacidad", json={
        "entrada_unidades_dia": 400, "salida_unidades_dia": 400,
        "num_operarios": 0, "unidades_hora_operario": 25, "horas_turno": 8,
    })
    assert r.status_code == 422


def test_capacidad_engine_422_spanish_on_zero_load():
    # passes pydantic (>=0) but the engine rejects zero total load with a Spanish message
    r = client.post("/api/decide/capacidad", json={
        "entrada_unidades_dia": 0, "salida_unidades_dia": 0,
        "num_operarios": 1, "unidades_hora_operario": 1, "horas_turno": 1,
    })
    assert r.status_code == 422
    assert "al menos una unidad" in r.json()["detail"]


def test_negociacion_ok():
    r = client.post("/api/decide/negociacion", json={
        "costo_unitario": 60, "precio_venta": 100,
        "margen_minimo_pct": 0.3, "lead_time_actual_dias": 7,
    })
    assert r.status_code == 200
    _assert_card(r.json())


def test_proveedor_ok():
    r = client.post("/api/decide/proveedor", json={
        "actual_precio": 10, "actual_otif": 0.95, "actual_lead": 5, "actual_defectos": 0.01,
        "alt_precio": 12, "alt_otif": 0.85, "alt_lead": 8, "alt_defectos": 0.03,
    })
    assert r.status_code == 200
    _assert_card(r.json())


def test_inventario_ok():
    r = client.post("/api/decide/inventario", json={
        "demanda_media_periodo": 100, "demanda_std_periodo": 20, "lead_time_periodos": 2,
        "nivel_servicio": 0.95, "costo_unitario": 10, "costo_ordenar": 50, "holding_rate_anual": 0.25,
    })
    assert r.status_code == 200
    _assert_card(r.json())


def test_compra_ok():
    r = client.post("/api/decide/compra", json={
        "monto_compra": 5000, "inventario_actual_valor": 50000,
        "cogs_anual": 400000, "ventas_anual": 600000,
    })
    assert r.status_code == 200
    _assert_card(r.json())


def test_liquidar_ok():
    r = client.post("/api/decide/liquidar", json={
        "unidades_excedente": 100, "costo_unitario": 20, "precio_actual": 50,
    })
    assert r.status_code == 200
    _assert_card(r.json())


def test_decisiones_page_served():
    r = client.get("/decisiones")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
