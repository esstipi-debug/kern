"""La brecha de plazo, valorizada: que la resta diga la verdad en los dos sentidos."""
from __future__ import annotations

import pandas as pd
import pytest

from src.supplier_scorecard import declared_lead_times_for_skus, score_supplier
from webapp import demo_gap_value as gv


def _receipts(rows: list[tuple[str, str, int, int]]) -> pd.DataFrame:
    """(proveedor, sku, plazo_declarado, plazo_real) x n, fechas derivadas."""
    out = []
    for i, (supplier, sku, declared, real) in enumerate(rows):
        order = pd.Timestamp("2024-01-08") + pd.Timedelta(days=14 * i)
        out.append(
            {
                "supplier": supplier,
                "product_id": sku,
                "order_date": order.date().isoformat(),
                "receipt_date": (order + pd.Timedelta(days=real)).date().isoformat(),
                "declared_lead_time_days": declared,
            }
        )
    return pd.DataFrame(out)


def _demand(skus: dict[str, float], *, weeks: int = 52, qty: int = 40) -> pd.DataFrame:
    """Demanda semanal con variabilidad, y costo por SKU."""
    rows = []
    for week in range(weeks):
        date = (pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=week)).date().isoformat()
        for sku, cost in skus.items():
            # Variacion deterministica: sin sigma_d el stock de seguridad es 0 y
            # la resta no puede mover nada.
            rows.append(
                {
                    "date": date,
                    "product_id": sku,
                    "quantity": qty + (week % 7) * 5,
                    "unit_cost": cost,
                }
            )
    return pd.DataFrame(rows)


def test_dias_a_periodos_no_infla_el_periodo_de_riesgo_siete_veces():
    """El scorecard mide en dias; el motor espera periodos. Confundirlos infla el SS."""
    assert gv._days_to_periods(14.0, 52.0) == pytest.approx(1.995, abs=0.01)
    assert gv._days_to_periods(365.0, 52.0) == pytest.approx(52.0)
    assert gv._days_to_periods(1.0, 365.0) == pytest.approx(1.0)


def test_plazo_declarado_inflado_libera_caja():
    """Declarado 60, real 20 constante: el buffer sobra y la caja se libera."""
    rows = [("Austral", "SKU-X", 60, 20) for _ in range(6)]
    result = gv.run_gap_value(_receipts(rows), _demand({"SKU-X": 100.0}))

    assert result.ok, result.qa_issues
    assert result.cash_release > 0
    assert result.exposure == 0
    assert result.net < 0
    # El ahorro recurrente se calcula sobre la caja liberable, al 22%.
    assert result.recurring_saving == pytest.approx(result.cash_release * gv.HOLDING_RATE)


def test_plazo_declarado_subestimado_revela_exposicion_no_ahorro():
    """El caso comun, y el que la doc no anticipaba.

    sigma_L solo SUMA varianza, asi que con el plazo declarado por debajo del real
    la medicion revela buffer que falta. Reportarlo como ahorro seria mentir.
    """
    rows = [("Andes", "SKU-Y", 14, real) for real in (24, 31, 19, 28, 35, 22)]
    result = gv.run_gap_value(_receipts(rows), _demand({"SKU-Y": 100.0}))

    assert result.ok, result.qa_issues
    assert result.exposure > 0
    assert result.cash_release == 0
    assert result.net > 0
    # Mantener el inventario correcto cuesta MAS, y el numero lo dice.
    assert result.net_carrying_change > 0
    assert any("riesgo que ya estabas corriendo" in f for f in result.findings)


def test_el_ahorro_recurrente_no_se_ata_al_neto():
    """Dos decisiones separadas: bajar donde sobra no depende de subir donde falta."""
    rows = [("Austral", "SKU-X", 60, 20) for _ in range(6)]
    rows += [("Andes", "SKU-Y", 14, real) for real in (24, 31, 19, 28, 35, 22)]
    result = gv.run_gap_value(_receipts(rows), _demand({"SKU-X": 100.0, "SKU-Y": 100.0}))

    assert result.ok, result.qa_issues
    assert result.cash_release > 0 and result.exposure > 0
    assert result.recurring_saving > 0  # ejecutable aunque el neto suba
    assert result.recurring_saving == pytest.approx(result.cash_release * gv.HOLDING_RATE)


def test_sin_costo_unitario_no_hay_cifra_de_plata():
    """El motor rellena unit_cost con 1,0. Valorizar sobre eso es inventar el numero."""
    rows = [("Austral", "SKU-X", 60, 20) for _ in range(6)]
    demand = _demand({"SKU-X": 100.0}).drop(columns=["unit_cost"])
    result = gv.run_gap_value(_receipts(rows), demand)

    assert not result.ok
    assert not result.gaps
    assert not result.findings
    assert any("maestro de articulos" in i for i in result.qa_issues)


def test_sin_dispersion_medible_el_qa_explica_por_que_la_resta_seria_muda():
    rows = [("Austral", "SKU-X", 60, 20) for _ in range(2)]  # <3 recepciones
    result = gv.run_gap_value(_receipts(rows), _demand({"SKU-X": 100.0}))

    assert not result.ok
    assert any("recepciones" in i for i in result.qa_issues)


def test_sin_identificador_de_producto_no_se_puede_aterrizar_el_plazo():
    rows = [("Austral", "SKU-X", 60, 20) for _ in range(6)]
    receipts = _receipts(rows).drop(columns=["product_id"])
    result = gv.run_gap_value(receipts, _demand({"SKU-X": 100.0}))

    assert not result.ok
    assert any("identificador de producto" in i for i in result.qa_issues)


def test_skus_que_no_estan_en_los_dos_archivos_se_declaran():
    rows = [("Austral", "SKU-COMPRAS", 60, 20) for _ in range(6)]
    result = gv.run_gap_value(_receipts(rows), _demand({"SKU-VENTAS": 100.0}))

    assert not result.ok
    assert any("los dos archivos" in i for i in result.qa_issues)


def test_declared_lead_times_for_skus_omite_al_proveedor_sin_declarado():
    """Misma regla que sus dos hermanas: omitir, nunca reportar 0."""
    con = score_supplier(
        "Con", [{"lead_time_days": 20.0, "declared_lead_time_days": 60.0}] * 3
    )
    sin = score_supplier("Sin", [{"lead_time_days": 20.0}] * 3)
    mapped = declared_lead_times_for_skus([con, sin], {"A": "Con", "B": "Sin"})

    assert mapped == {"A": 60.0}
    assert "B" not in mapped  # no aparece como 0.0


def test_sku_supplier_map_es_determinista_en_empate():
    grouped = {
        "Zeta": [{"product_id": "SKU-X"}, {"product_id": "SKU-X"}],
        "Alfa": [{"product_id": "SKU-X"}, {"product_id": "SKU-X"}],
    }
    assert gv.sku_supplier_map(grouped) == {"SKU-X": "Alfa"}
    # Y el volumen gana sobre el orden alfabetico.
    grouped["Zeta"].append({"product_id": "SKU-X"})
    assert gv.sku_supplier_map(grouped) == {"SKU-X": "Zeta"}
