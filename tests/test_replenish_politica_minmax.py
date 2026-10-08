"""La politica min/max del camino Odoo: plazo, nivel de servicio y semanas con ceros.

La prueba de punta a punta contra el Odoo de laboratorio (2026-09-19) encontro dos
defectos en ``plan_replenishment``:

- F2: la cantidad era ``pronostico * 8 periodos`` sin plazo ni stock de seguridad.
  El zapato (SKU-006, plazo 45 dias) proponia 0 unidades.
- F3: el "periodo" era un dia-con-pedidos. La transpaleta (SKU-015, 6 ventas de 1
  en 26 semanas) pronosticaba 1 por periodo y compraba 7.

Los fixtures reproducen las ventas del laboratorio agregadas por semana calendario
(ventana 2026-03-26..2026-09-16, 25 semanas), con el stock que tenia cada SKU.
SEMANAS suma 161 / 1900 / 6 en 25 semanas completas; diseno-datos.json dice
167 / 1991 / 6 en 26 porque incluye la semana parcial que weekly_demand_by_sku
descarta. Los numeros esperados son los del calculo a mano del plan (seccion
"Riesgos y decisiones"): nivel de servicio 0,95, revision R = 7 dias, sigma_L = 0.

Dos cifras por SKU, y no son la misma:
- ``order_up_to`` (S) = mu*(L+R) + SS redondeado hacia arriba al paso de compra. Es
  el disparador de compra y lo que Odoo recibe como minimo Y maximo.
- ``reorder_point`` = mu*L + SS, sin redondear, la misma definicion que
  ``jobs/inventory_optimization.py``. Es lo que mira el Tower.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jobs import state_bridge
from nosilo_core.guided import HANDOFF, passed_guided
from src.connectors import InventoryLevel, Order, OrderLine, Product
from src.connectors.replenish import (
    DEFAULT_SERVICE_LEVEL,
    ReplenishmentLine,
    plan_replenishment,
    weekly_demand_by_sku,
)

INICIO = date(2026, 3, 26)
AS_OF = date(2026, 9, 16)

#: Ventas por semana calendario del laboratorio (kern_test, leidas por XML-RPC).
SEMANAS = {
    "SKU-006": (7, 10, 2, 8, 7, 9, 7, 6, 5, 3, 6, 6, 10, 5, 7, 3, 5, 7, 8, 9, 6, 4, 7, 7, 7),
    "SKU-015": (0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1),
    "SKU-002": (87, 102, 49, 96, 38, 7, 63, 54, 105, 99, 46, 104, 52, 78, 72, 83, 103,
                98, 90, 98, 93, 62, 60, 67, 94),
}
STOCK = {"SKU-006": 24.0, "SKU-015": 1.0, "SKU-002": 40.0}
PLAZO = {"SKU-006": 45.0, "SKU-015": 45.0, "SKU-002": 7.0}
MOQ = {"SKU-002": 72.0}
UNIDADES = {"SKU-006": 1.0, "SKU-015": 1.0, "SKU-002": 1.0}


class _Fuente:
    """Un InventorySource minimo: catalogo, estante y pedidos, sin red."""

    def __init__(self, semanas: dict, stock: dict, *, dias: dict | None = None):
        self._stock = stock
        self._pedidos = []
        for sku, serie in semanas.items():
            for i, qty in enumerate(serie):
                if qty > 0:
                    fecha = (INICIO + timedelta(days=7 * i)).isoformat()
                    self._pedidos.append(Order(f"{sku}-{i}", fecha, (OrderLine(sku, float(qty), 1.0),)))
        for sku, filas in (dias or {}).items():
            for i, (fecha, qty) in enumerate(filas):
                self._pedidos.append(Order(f"{sku}-d{i}", fecha, (OrderLine(sku, float(qty), 1.0),)))

    def list_products(self):
        return [Product(sku, sku, 1.0) for sku in self._stock]

    def inventory_levels(self):
        return [InventoryLevel(sku, qty) for sku, qty in self._stock.items()]

    def orders(self, *, since=None):
        return list(self._pedidos)


def _plan(**kw):
    kw.setdefault("lead_time_days_by_sku", PLAZO)
    kw.setdefault("min_qty_by_sku", MOQ)
    kw.setdefault("unit_step_by_sku", UNIDADES)
    kw.setdefault("on_order_by_sku", {})
    kw.setdefault("as_of", AS_OF)
    fuente = kw.pop("fuente", None) or _Fuente(SEMANAS, STOCK)
    return plan_replenishment(fuente, **kw)


def _linea(plan, sku) -> ReplenishmentLine:
    return next(ln for ln in plan.lines if ln.sku == sku)


# -- 1. los tres SKU del laboratorio, con los numeros del calculo a mano -----------


def test_zapato_con_plazo_45_compra_37_y_no_cero():
    ln = _linea(_plan(), "SKU-006")
    assert ln.policy == "min_max" and ln.status == "ok"
    assert ln.forecast_method == "ses" and ln.sigma_source == "forecast_error"
    assert ln.forecast_per_period == pytest.approx(6.660, abs=1e-3)
    assert ln.safety_stock == pytest.approx(10.691, abs=1e-3)
    assert ln.lead_time_days == 45.0 and ln.review_days == 7.0
    assert ln.order_up_to == 61.0 and ln.target == 61.0
    assert ln.reorder_point == pytest.approx(53.50, abs=1e-2)       # 6,660 x 45/7 + 10,691
    assert ln.position == 24.0
    assert ln.restock_unrounded == pytest.approx(36.167, abs=1e-3)
    assert ln.restock_qty == 37.0 and ln.unit_step == 1.0
    assert ln.avg_daily_demand == pytest.approx(6.660 / 7, abs=1e-3)


def test_transpaleta_intermitente_usa_poisson_y_compra_3_no_7():
    ln = _linea(_plan(), "SKU-015")
    assert ln.forecast_method == "tasa_media"
    assert ln.buffer_distribution == "poisson"
    assert ln.n_observations == 24                      # desde la semana de su primera venta
    assert ln.forecast_per_period == pytest.approx(0.25)
    assert ln.order_up_to == 4.0
    assert ln.safety_stock == pytest.approx(4.0 - 0.25 * 52 / 7, abs=1e-9)
    assert ln.reorder_point == pytest.approx(0.25 * 45 / 7 + (4.0 - 0.25 * 52 / 7), abs=1e-9)
    assert ln.restock_unrounded == pytest.approx(3.0)
    assert ln.restock_qty == 3.0


def test_cinta_con_moq_72_compra_185_entera():
    ln = _linea(_plan(), "SKU-002")
    assert ln.forecast_method == "ses"
    assert ln.forecast_per_period == pytest.approx(79.551, abs=1e-3)
    assert ln.safety_stock == pytest.approx(65.258, abs=1e-3)
    assert ln.order_up_to == 225.0
    assert ln.reorder_point == pytest.approx(144.81, abs=1e-2)      # 79,551 x 7/7 + 65,258
    assert ln.restock_unrounded == pytest.approx(184.360, abs=1e-3)
    assert ln.restock_qty == 185.0
    assert ln.lot_rule == "moq 72"


# -- 2. el plazo y el nivel de servicio mueven el numero ---------------------------


def test_el_plazo_cambia_la_cantidad():
    corto = _linea(_plan(lead_time_days_by_sku={**PLAZO, "SKU-006": 7.0}), "SKU-006")
    largo = _linea(_plan(), "SKU-006")
    assert corto.order_up_to < largo.order_up_to
    assert corto.restock_qty < largo.restock_qty


def test_el_stock_de_seguridad_sube_con_el_nivel_de_servicio():
    al_90 = _linea(_plan(service_level=0.90), "SKU-006")
    al_95 = _linea(_plan(service_level=0.95), "SKU-006")
    assert al_90.safety_stock == pytest.approx(8.330, abs=1e-3)
    assert al_90.order_up_to == 58.0 and al_95.order_up_to == 61.0
    assert al_90.service_level == 0.90


def test_default_de_nivel_de_servicio_se_declara():
    plan = _plan()
    ln = _linea(plan, "SKU-006")
    assert ln.service_level == DEFAULT_SERVICE_LEVEL
    assert any(d.startswith("service_level=0.95") for d in ln.defaults_declared)
    assert any("raiz(L+R)" in d for d in ln.defaults_declared)
    assert any(a.startswith("service_level=0.95") for a in plan.assumptions)
    assert any("25 semanas calendario" in a for a in plan.assumptions)
    assert any("qty_to_order" in a for a in plan.assumptions)
    explicito = _linea(_plan(service_level=0.95), "SKU-006")
    assert not any(d.startswith("service_level") for d in explicito.defaults_declared)


@pytest.mark.parametrize("nivel", [0.0, 1.0, 1.5, -0.1])
def test_nivel_de_servicio_fuera_de_rango_falla(nivel):
    with pytest.raises(ValueError, match="service_level"):
        _plan(service_level=nivel)


# -- 3. la regla de compra es la de Odoo 17 -------------------------------------------


def test_compra_solo_si_la_posicion_cae_bajo_el_minimo():
    en_el_minimo = _linea(_plan(on_order_by_sku={"SKU-006": 61.0 - 24.0}), "SKU-006")
    assert en_el_minimo.position == 61.0
    assert en_el_minimo.restock_qty == 0.0 and en_el_minimo.restock_unrounded == 0.0
    un_abajo = _linea(_plan(on_order_by_sku={"SKU-006": 60.0 - 24.0}), "SKU-006")
    assert un_abajo.restock_qty == 1.0


def test_moq_lleva_la_compra_al_minimo_del_proveedor():
    ln = _linea(_plan(on_order_by_sku={"SKU-002": 200.0 - 40.0}), "SKU-002")
    assert ln.restock_unrounded == pytest.approx(24.360, abs=1e-3)
    assert ln.restock_qty == 72.0


def test_el_multiplo_del_proveedor_redondea_hacia_arriba():
    ln = _linea(_plan(order_multiple_by_sku={"SKU-006": 12.0}), "SKU-006")
    assert ln.restock_qty == 48.0
    assert ln.lot_rule == "multiple 12"


def test_maximo_y_compra_caen_en_el_paso_de_compra():
    for ln in _plan().lines:
        assert float(ln.order_up_to).is_integer() and float(ln.restock_qty).is_integer()
        assert ln.order_up_to >= ln.reorder_point


def test_un_sku_por_kilo_no_se_redondea_a_kilos_enteros():
    """Paso 0,001 (kg): el maximo sube al gramo, no al kilo (la regla de F4)."""
    ln = _linea(_plan(unit_step_by_sku={**UNIDADES, "SKU-006": 0.001}), "SKU-006")
    assert ln.unit_step == 0.001
    assert ln.order_up_to == 60.167
    assert ln.restock_qty == 36.167
    assert not float(ln.order_up_to).is_integer()


def test_sin_unidad_leida_no_se_inventa_un_entero_y_se_declara():
    ln = _linea(_plan(unit_step_by_sku=None), "SKU-006")
    assert ln.unit_step is None
    assert ln.order_up_to == pytest.approx(60.167, abs=1e-3)
    assert any(d.startswith("unit_step no leido") for d in ln.defaults_declared)
    con_unidad = _linea(_plan(), "SKU-006")
    assert not any(d.startswith("unit_step") for d in con_unidad.defaults_declared)


# -- 4. semanas calendario con ceros, ventana comun ------------------------------------


def test_semana_sin_venta_cuenta_como_cero():
    ventana = weekly_demand_by_sku(_Fuente({"A": (3, 0, 0, 5)}, {"A": 0.0}).orders(),
                                   as_of=INICIO + timedelta(days=27))
    assert ventana.series["A"] == (3.0, 0.0, 0.0, 5.0)


def test_la_ventana_termina_igual_para_todo_el_catalogo():
    """Un SKU que dejo de vender recibe sus semanas en cero al final."""
    fuente = _Fuente({"VIEJO": (4, 4, 0, 0, 0, 0), "NUEVO": (1, 1, 1, 1, 1, 1)},
                     {"VIEJO": 0.0, "NUEVO": 0.0})
    ventana = weekly_demand_by_sku(fuente.orders(), as_of=INICIO + timedelta(days=41))
    assert ventana.n_weeks == 6
    assert ventana.series["VIEJO"] == (4.0, 4.0, 0.0, 0.0, 0.0, 0.0)


def test_la_semana_parcial_del_principio_se_descarta():
    fuente = _Fuente({}, {"A": 0.0}, dias={"A": [("2026-03-24", 99.0), ("2026-03-26", 2.0),
                                                ("2026-04-01", 3.0)]})
    ventana = weekly_demand_by_sku(fuente.orders())
    assert ventana.start == date(2026, 3, 26) and ventana.n_weeks == 1
    assert ventana.series["A"] == (5.0,)


def test_un_sku_que_ya_vendia_antes_de_la_ventana_conserva_sus_ceros_iniciales():
    """Vendio en la semana parcial descartada: existia, sus semanas en cero son reales."""
    fuente = _Fuente({}, {"A": 0.0, "B": 0.0},
                     dias={"A": [("2026-03-24", 1.0), ("2026-04-09", 1.0)],
                           "B": [("2026-04-09", 1.0)]})
    ventana = weekly_demand_by_sku(fuente.orders(), as_of=date(2026, 4, 15))
    assert ventana.start == date(2026, 3, 26) and ventana.n_weeks == 3
    assert ventana.series["A"] == (0.0, 0.0, 1.0)
    assert ventana.series["B"] == (1.0,)


def test_sigma_se_mide_en_semanas_no_en_dias():
    """Tres pedidos fijos por semana: cero variabilidad semanal, cero stock de seguridad."""
    dias = [((INICIO + timedelta(days=7 * s + d)).isoformat(), 2.0)
            for s in range(12) for d in (0, 2, 4)]
    fuente = _Fuente({}, {"A": 0.0}, dias={"A": dias})
    ln = _linea(_plan(fuente=fuente, lead_time_days_by_sku={"A": 7.0}, min_qty_by_sku={},
                      unit_step_by_sku={"A": 1.0}, as_of=INICIO + timedelta(days=83)), "A")
    assert ln.safety_stock == pytest.approx(0.0, abs=1e-9)
    assert ln.order_up_to == 12.0                       # 6 por semana x (7 + 7) / 7
    assert ln.reorder_point == pytest.approx(6.0)       # 6 por semana x 7 / 7


def test_intermitencia_se_decide_por_adi_semanal_no_por_dias_en_cero():
    """La cinta tiene la mayoria de los DIAS en cero y vende todas las SEMANAS."""
    assert _linea(_plan(), "SKU-002").forecast_method == "ses"


def test_intermitente_con_menos_de_3_semanas_con_venta_no_se_dimensiona():
    fuente = _Fuente({"RARO": (1, 0, 0, 0, 0, 0, 0, 1)}, {"RARO": 0.0})
    ln = _linea(_plan(fuente=fuente, lead_time_days_by_sku={"RARO": 30.0},
                      as_of=INICIO + timedelta(days=55)), "RARO")
    assert ln.status == "insufficient_history"
    assert ln.reorder_point is None and ln.order_up_to is None and ln.restock_qty == 0.0


# -- 5. lo ausente viaja como None -------------------------------------------------------


def test_sku_sin_plazo_viaja_none_y_va_al_residual():
    plan = _plan(lead_time_days_by_sku={"SKU-006": 45.0, "SKU-002": 7.0})
    ln = _linea(plan, "SKU-015")
    assert ln.status == "sin_plazo"
    assert ln.lead_time_days is None and ln.reorder_point is None and ln.safety_stock is None
    assert ln.order_up_to is None and ln.restock_unrounded is None
    assert ln.restock_qty == 0.0 and "SKU-015" not in plan.restock
    assert any("no declared supplier lead time" in r.description and "SKU-015" in r.description
               for r in plan.outcome.residuals)


def test_catalogo_entero_sin_plazo_es_handoff_no_ejecutado():
    plan = _plan(lead_time_days_by_sku={})
    assert plan.restock == {}
    assert plan.outcome.status == HANDOFF and passed_guided(plan.outcome)
    assert plan.outcome.handoffs[0].data["sin_plazo"] == ["SKU-002", "SKU-006", "SKU-015"]


def test_sin_mapa_de_plazos_el_camino_de_cobertura_no_cambia():
    ln = _linea(plan_replenishment(_Fuente(SEMANAS, STOCK), on_order_by_sku={}), "SKU-006")
    assert ln.policy == "cover"
    assert ln.target == pytest.approx(ln.forecast_per_period * 8.0)
    for campo in ("reorder_point", "order_up_to", "safety_stock", "lead_time_days",
                  "service_level", "review_days", "forecast_method"):
        assert getattr(ln, campo) is None


# -- 6. escritura y Tower -------------------------------------------------------------


def test_un_store_sin_stage_reorder_policy_no_escribe_el_maximo_como_minimo():
    class _SoloRestock(_Fuente):
        llamado = False

        def stage_restock(self, *a, **kw):
            type(self).llamado = True

    fuente = _SoloRestock(SEMANAS, STOCK)
    plan = _plan(fuente=fuente, store=fuente)
    assert plan.restock and plan.changeset is None and not _SoloRestock.llamado


def test_un_store_con_stage_reorder_policy_recibe_s_como_minimo_y_maximo():
    class _ConPolitica(_Fuente):
        recibido: dict = {}

        def stage_reorder_policy(self, politica, *, idempotency_key, reason=""):
            type(self).recibido = dict(politica)
            return "cambio"

    fuente = _ConPolitica(SEMANAS, STOCK)
    plan = _plan(fuente=fuente, store=fuente)
    assert plan.changeset == "cambio"
    assert _ConPolitica.recibido["SKU-006"] == (61.0, 61.0)
    assert _ConPolitica.recibido["SKU-015"] == (4.0, 4.0)
    assert _ConPolitica.recibido["SKU-002"] == (225.0, 225.0)


def test_el_tower_lee_mu_l_mas_ss_y_no_el_objetivo():
    ln = ReplenishmentLine("X", 5.0, 1.0, 99.0, 0.0, avg_daily_demand=1.0, reorder_point=12.5)
    [fila] = state_bridge.stock_lines_from_replenishment([ln])
    assert fila.reorder_point == 12.5
    cobertura = ReplenishmentLine("X", 5.0, 1.0, 99.0, 0.0, avg_daily_demand=1.0)
    assert state_bridge.stock_lines_from_replenishment([cobertura])[0].reorder_point == 99.0
