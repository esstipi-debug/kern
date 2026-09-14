"""La invariante del campo ``clearance_price`` tiene que ser CIERTA, no aspiracional.

El docstring del campo prometia dos cosas: que el precio queda en ``None`` solo
cuando "no hay ancla (ni precio observado NI costo unitario)", y que ese caso
coincide con "no hay nada en riesgo". Las dos eran falsas, por dos motivos
distintos, y la segunda es la que hace dano en el entregable.

1. ``salvage_recovery_pct=0.0`` es un valor legal (la validacion es
   ``0.0 <= pct <= 1.0``). Con costo unitario > 0 el ancla SI existe y la caja en
   riesgo SI existe, pero ``_salvage_unit_price`` devolvia ``None`` porque el
   producto daba cero. ``None`` significa "no hay con que calcularlo"; aca el
   calculo se hizo y dio cero. Confundir los dos casos hace que el deck imprima
   "no price (no observed price and no unit cost to anchor one to)"
   (``jobs/markdown_liquidation_job.py::_price_phrase``) al lado de plata real:
   una frase literalmente falsa sobre un SKU con costo de 7.00 y 700.00 en riesgo.

2. El remate nunca mira el precio observado -- se ancla al COSTO. Un muerto con
   historial de precios y sin costo se queda sin precio propuesto aunque el precio
   observado exista, asi que "ni precio observado NI costo unitario" jamas fue la
   condicion que el codigo evalua.

Estos tests fijan la invariante en su forma verificable: si hay caja en riesgo,
hay precio propuesto; y la unica ausencia legitima es la falta de ancla de COSTO.
"""

import pytest

from src.excess_obsolete import SkuStock
from src.liquidation import SALVAGE, plan_liquidation


def _muerto(product_id: str = "D", *, unit_cost: float) -> SkuStock:
    return SkuStock(product_id=product_id, on_hand=100.0, daily_demand=0.0,
                    unit_cost=unit_cost, days_since_last_sale=400)


def test_con_caja_en_riesgo_siempre_hay_precio_propuesto():
    """La promesa que importa: nunca una columna de precio vacia al lado de plata.

    Con ``salvage_recovery_pct=0.0`` y costo 7.00 hay 700.00 en riesgo. Antes del
    fix el precio venia ``None`` y el deck explicaba esa ausencia con un motivo
    que no era cierto ("no unit cost"), cuando el costo estaba ahi.
    """
    linea = plan_liquidation([_muerto(unit_cost=7.0)], salvage_recovery_pct=0.0).lines[0]

    assert linea.at_risk_value == pytest.approx(700.0)
    assert linea.clearance_price is not None, (
        "hay 700.00 en riesgo y un costo de 7.00 al que anclar: la ausencia de "
        "precio no es reportable como falta de ancla"
    )
    assert linea.clearance_price == pytest.approx(0.0)


def test_recuperacion_cero_cierra_la_identidad_precio_por_unidades():
    """``recovered_value == clearance_price * units_to_clear`` tambien con pct=0.

    Es la identidad que ``_salvage_unit_price`` dice sostener "para los tres
    metodos". Con el precio en ``None`` era irrepresentable justo en el caso donde
    la recuperacion es cero, que es el que mas conviene poder auditar.
    """
    linea = plan_liquidation([_muerto(unit_cost=7.0)], salvage_recovery_pct=0.0).lines[0]

    assert linea.method == SALVAGE
    assert linea.recovered_value == pytest.approx(0.0)
    assert linea.recovered_value == pytest.approx(
        linea.clearance_price * linea.units_to_clear
    )


def test_la_unica_ausencia_de_precio_es_la_falta_de_ancla_de_costo():
    """Sin costo no hay precio, y ahi tampoco hay caja en riesgo: la invariante
    corregida se cumple de forma no vacia en ambos lados."""
    linea = plan_liquidation([_muerto(unit_cost=0.0)], salvage_recovery_pct=0.30).lines[0]

    assert linea.method == SALVAGE
    assert linea.clearance_price is None
    assert linea.at_risk_value == pytest.approx(0.0)


def test_el_precio_observado_no_es_ancla_del_remate():
    """El remate se ancla al COSTO, nunca al precio observado.

    Un muerto con historial (precio observado 50.0) y sin costo sigue sin precio
    propuesto. Por eso la condicion "ni precio observado NI costo unitario" nunca
    describio al codigo: el precio observado no participa de la decision.
    """
    historial = {"E": ([50.0, 50.0, 50.0], [5.0, 5.0, 5.0])}
    linea = plan_liquidation([_muerto("E", unit_cost=0.0)], historial).lines[0]

    assert linea.method == SALVAGE
    assert linea.current_price == pytest.approx(50.0)   # el precio observado existe
    assert linea.clearance_price is None                # y aun asi no ancla el remate


def test_ninguna_linea_con_riesgo_se_queda_sin_precio_en_un_plan_mixto():
    """La invariante recorrida sobre un plan entero, no sobre un caso armado."""
    stocks = [
        _muerto("A", unit_cost=7.0),
        _muerto("B", unit_cost=0.0),
        SkuStock(product_id="C", on_hand=900.0, daily_demand=1.0, unit_cost=3.0),
    ]
    for pct in (0.0, 0.30, 1.0):
        report = plan_liquidation(stocks, salvage_recovery_pct=pct)
        for linea in report.lines:
            if linea.at_risk_value > 0:
                assert linea.clearance_price is not None, (linea.product_id, pct)
