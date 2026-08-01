"""Tests for the reverse-logistics / returns-disposition engine (roadmap #6).

For each returned lot the engine ranks the executable dispositions (restock / refurbish /
liquidate / scrap) by net recovery, picks the best, and rolls up recovery rate, value at
risk and the reason Pareto. Pure - no external deps.
"""

import pytest

from src.reverse_logistics import (
    REASON_DISPATCH_ERROR,
    REASON_PRODUCT_FAILURE,
    REASON_UNSPECIFIED,
    DispositionRates,
    ReturnLine,
    best_disposition,
    classify_reason,
    options_for,
    reason_pareto,
    recovered_value,
    recovery_rate,
    returns_value_at_cost,
    supplier_claim_base,
    units_by_reason_kind,
)

_RATES = DispositionRates(
    restock_handling_per_unit=2.0,
    refurbish_cost_per_unit=10.0,
    refurbish_resale_factor=0.6,
    liquidation_recovery_pct=0.2,
    scrap_cost_per_unit=3.0,
)
# unit_cost 50, resale 40 -> restock 38, refurbish 0.6*40-10=14, liquidate 0.2*50=10, scrap -3
_SELLABLE = ReturnLine("SKU-A", returned_units=10.0, reason="wrong_size", unit_cost=50.0, resale_value=40.0)
_DAMAGED = ReturnLine("SKU-B", returned_units=5.0, reason="damaged", unit_cost=50.0, resale_value=40.0, sellable=False)


def test_options_ranked_by_net_recovery_for_a_sellable_return():
    opts = options_for(_SELLABLE, _RATES)

    assert [o.action for o in opts] == ["restock", "refurbish", "liquidate", "scrap"]
    assert opts[0].net_recovery_per_unit == pytest.approx(38.0)
    assert opts[-1].net_recovery_per_unit == pytest.approx(-3.0)


def test_unsellable_return_drops_restock_and_refurbish():
    opts = options_for(_DAMAGED, _RATES)

    assert [o.action for o in opts] == ["liquidate", "scrap"]
    assert opts[0].net_recovery_per_unit == pytest.approx(10.0)


def test_best_disposition_picks_the_top_option_and_scales_by_units():
    d = best_disposition(_SELLABLE, _RATES)

    assert d.best.action == "restock"
    assert d.recovery_value == pytest.approx(38.0 * 10)   # per-unit * returned_units


def test_recovery_rate_is_recovered_over_value_at_risk():
    lines = [_SELLABLE, _DAMAGED]
    dispositions = [best_disposition(line, _RATES) for line in lines]

    # value at risk = 10*50 + 5*50 = 750; recovered = 38*10 + 10*5 = 430
    assert returns_value_at_cost(lines) == pytest.approx(750.0)
    assert recovered_value(dispositions) == pytest.approx(430.0)
    assert recovery_rate(dispositions, lines) == pytest.approx(430.0 / 750.0)


def test_reason_pareto_ranks_reasons_by_units_descending():
    lines = [
        ReturnLine("A", 10.0, "wrong_size", 1.0, 1.0),
        ReturnLine("B", 8.0, "defective", 1.0, 1.0),
        ReturnLine("C", 5.0, "wrong_size", 1.0, 1.0),
    ]

    pareto = reason_pareto(lines)

    assert pareto[0] == ("wrong_size", 15.0)   # 10 + 5, the biggest driver
    assert pareto[1] == ("defective", 8.0)
    assert [r for r, _ in pareto] == ["wrong_size", "defective"]


# --- El motivo cambia la decision, no solo el reporte -------------------------


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("defective unit", REASON_PRODUCT_FAILURE),
        ("Falla de producto", REASON_PRODUCT_FAILURE),
        ("producto vencido", REASON_PRODUCT_FAILURE),
        ("wrong_size", REASON_DISPATCH_ERROR),
        ("error de despacho", REASON_DISPATCH_ERROR),
        ("customer changed mind", REASON_UNSPECIFIED),
        ("", REASON_UNSPECIFIED),
        # Ambiguo: dice las dos cosas. No se elige por nosotros.
        ("wrong item, arrived broken", REASON_UNSPECIFIED),
    ],
)
def test_classify_reason_no_adivina(reason, kind):
    assert classify_reason(reason) == kind


def test_una_falla_de_producto_nunca_vuelve_a_stock():
    """Reponer una unidad fallada a precio pleno no es una ruta mas barata: es la
    accion equivocada. Refurbish sigue disponible, que para eso existe."""
    fallado = ReturnLine("SKU-F", returned_units=10.0, reason="falla de producto",
                         unit_cost=50.0, resale_value=40.0)
    acciones = [o.action for o in options_for(fallado, _RATES)]
    assert "restock" not in acciones
    assert "refurbish" in acciones
    assert best_disposition(fallado, _RATES).best.action == "refurbish"


def test_un_error_de_despacho_conserva_todas_las_rutas():
    """La unidad esta intacta: el problema fue el picking, no el producto."""
    despacho = ReturnLine("SKU-D", returned_units=10.0, reason="error de despacho",
                          unit_cost=50.0, resale_value=40.0)
    assert best_disposition(despacho, _RATES).best.action == "restock"


def test_un_motivo_desconocido_no_bloquea_ninguna_ruta():
    """Sin evidencia de falla, el comportamiento es el de siempre."""
    assert best_disposition(_SELLABLE, _RATES).best.action == "restock"


def test_la_base_reclamable_solo_cuenta_las_fallas():
    fallado = ReturnLine("SKU-F", returned_units=10.0, reason="defectuoso",
                         unit_cost=50.0, resale_value=40.0)
    dispos = [best_disposition(ln, _RATES) for ln in (fallado, _SELLABLE)]
    assert dispos[0].supplier_claim_base == 500.0     # 10 u x 50 de costo
    assert dispos[1].supplier_claim_base == 0.0       # error de despacho: no se le reclama
    assert supplier_claim_base(dispos) == 500.0


def test_las_unidades_se_parten_por_tipo_de_motivo():
    fallado = ReturnLine("SKU-F", returned_units=4.0, reason="roto",
                         unit_cost=50.0, resale_value=40.0)
    assert units_by_reason_kind([fallado, _SELLABLE]) == {
        REASON_PRODUCT_FAILURE: 4.0,
        REASON_DISPATCH_ERROR: 10.0,
    }
