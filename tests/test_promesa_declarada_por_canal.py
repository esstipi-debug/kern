"""La promesa que el cliente declara hace medible el OTIF, y nunca se lee como medida.

El problema que cierra (plan del 2026-09-05, PR-2c): Unleashed y Cin7 Core dejan la
fecha pactada vacia en casi todo el libro. Sin fecha prometida no hay OTIF, y el OTIF es
el numero que el producto dice medir y que el canal no mide. La salida NO es inventar
una fecha: es preguntarle al cliente su promesa ("despachamos en 2 dias habiles"), una
vez, y medir contra eso diciendo que la vara es dicha y no registrada.

Lo que estos tests fijan, y que es todo lo que separa esto de fabricar un numero:

1. La promesa solo rellena donde el sistema NO dato la linea. Jamas pisa una fecha del ERP.
2. Sin promesa declarada, nada cambia: el OTIF sigue saliendo ``None``.
3. El fill rate no se toca: no depende de ninguna fecha.
4. Cada linea asi puntuada se cuenta (``lines_scored_against_declared_promise``), mueve
   ``otif_basis`` y se nombra en ``method``.
5. Los dias sin servir NO se cuentan sobre una fecha derivada: esa cuenta necesita una
   fecha que alguien registro.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.client_profile import ClientProfile
from src.order_fulfillment import (
    OTIF_BASIS_CLIENT_PROMISE,
    OTIF_BASIS_ERP,
    OTIF_BASIS_MIXED,
    PROVENANCE_OBSERVED,
    add_business_days,
    measure_from_order_lines,
)


def _linea(**kw) -> dict:
    base = {"ordered_qty": 10.0, "delivered_qty": 10.0, "order_date": "2026-09-01"}
    base.update(kw)
    return base


# -- dias habiles ---------------------------------------------------------------


@pytest.mark.parametrize(
    "desde, dias, esperado",
    [
        ("2026-09-11", 1, "2026-09-14"),   # viernes + 1 -> lunes
        ("2026-09-11", 2, "2026-09-15"),   # viernes + 2 -> martes
        ("2026-09-07", 5, "2026-09-14"),   # lunes + 5 -> lunes siguiente
        ("2026-09-12", 1, "2026-09-14"),   # sabado + 1 -> lunes
        ("2026-09-07", 0, "2026-09-07"),   # cero dias: el mismo dia
    ],
)
def test_los_dias_habiles_saltean_el_fin_de_semana(desde, dias, esperado):
    assert add_business_days(date.fromisoformat(desde), dias) == date.fromisoformat(esperado)


def test_medio_dia_habil_se_redondea_hacia_arriba():
    """"Medio dia habil" es una promesa que se cumple al dia siguiente; truncar la
    dejaria en el mismo dia y volveria puntual a un despacho que llego tarde."""
    assert add_business_days(date(2026, 9, 7), 0.5) == date(2026, 9, 8)


# -- la promesa solo rellena, nunca pisa ----------------------------------------


def test_sin_promesa_declarada_el_otif_no_es_medible():
    """El estado de hoy en Unleashed y Cin7: lineas sin fecha pactada, OTIF None."""
    servicio = measure_from_order_lines(
        "SKU-1", [_linea(delivered_date="2026-09-03"), _linea(delivered_date="2026-09-09")]
    )

    assert servicio.otif is None
    assert servicio.otif_basis is None
    assert servicio.lines_scored_against_declared_promise == 0
    assert servicio.fill_rate == 1.0  # el fill rate nunca dependio de una fecha


def test_con_promesa_declarada_el_otif_se_puede_medir_y_dice_contra_que():
    servicio = measure_from_order_lines(
        "SKU-1",
        [_linea(delivered_date="2026-09-03"), _linea(delivered_date="2026-09-09")],
        promise_days=2,
    )

    # 1-sep + 2 habiles = 3-sep. La primera llego el 3 (a tiempo), la segunda el 9.
    assert servicio.otif == 0.5
    assert servicio.otif_basis == OTIF_BASIS_CLIENT_PROMISE
    assert servicio.lines_scored_against_declared_promise == 2
    assert servicio.lines_otif_scored == 2
    assert "promesa declarada por el cliente" in servicio.method
    assert "2 dias habiles" in servicio.method


def test_la_promesa_declarada_nunca_pisa_la_fecha_del_erp():
    """La linea trae fecha pactada del sistema: esa manda, aunque la promesa sea otra."""
    con_fecha = _linea(promised_date="2026-09-10", delivered_date="2026-09-09")

    sin_promesa = measure_from_order_lines("SKU-1", [con_fecha])
    con_promesa = measure_from_order_lines("SKU-1", [con_fecha], promise_days=1)

    assert sin_promesa.otif == con_promesa.otif == 1.0  # entrego el 9, prometio el 10
    assert con_promesa.otif_basis == OTIF_BASIS_ERP
    assert con_promesa.lines_scored_against_declared_promise == 0
    assert "promesa declarada" not in con_promesa.method


def test_un_libro_mezclado_lo_declara_como_mezclado():
    servicio = measure_from_order_lines(
        "SKU-1",
        [
            _linea(promised_date="2026-09-10", delivered_date="2026-09-09"),
            _linea(delivered_date="2026-09-09"),
        ],
        promise_days=2,
    )

    assert servicio.otif_basis == OTIF_BASIS_MIXED
    assert servicio.lines_otif_scored == 2
    assert servicio.lines_scored_against_declared_promise == 1


def test_el_fill_rate_es_el_mismo_con_y_sin_promesa():
    """La promesa es una vara de PUNTUALIDAD. Tocar el fill rate con ella seria mover
    un numero medido con un dato declarado."""
    lineas = [
        _linea(delivered_qty=7.0, delivered_date="2026-09-03"),
        _linea(delivered_qty=10.0, delivered_date="2026-09-20"),
    ]

    sin = measure_from_order_lines("SKU-1", lineas)
    con = measure_from_order_lines("SKU-1", lineas, promise_days=2)

    assert sin.fill_rate == con.fill_rate == 0.85
    assert sin.lost_sales_units == con.lost_sales_units == 3.0
    assert sin.provenance == con.provenance == PROVENANCE_OBSERVED


def test_los_dias_sin_servir_no_se_cuentan_sobre_una_fecha_derivada():
    """Un dia sin servir necesita una fecha que alguien registro. La derivada no lo es:
    contarla convertiria una promesa en evidencia de un quiebre."""
    sin_ninguna_fecha = [_linea(delivered_qty=4.0)]

    servicio = measure_from_order_lines("SKU-1", sin_ninguna_fecha, promise_days=2)

    assert servicio.days_out_of_stock is None
    assert servicio.lost_sales_units == 6.0  # la falta SI se mide: no necesita fecha


# -- promesa por canal ----------------------------------------------------------


def test_cada_canal_se_juzga_contra_su_propia_promesa():
    """2 dias para retail y 5 para mayorista: la misma entrega es puntual en uno y
    tarde en el otro, que es exactamente por que la promesa es por canal."""
    lineas = [
        _linea(channel="retail", delivered_date="2026-09-08"),
        _linea(channel="mayorista", delivered_date="2026-09-08"),
    ]

    servicio = measure_from_order_lines(
        "SKU-1", lineas, promise_days={"retail": 2, "mayorista": 5}
    )

    # 1-sep + 2 habiles = 3-sep (tarde); 1-sep + 5 habiles = 8-sep (a tiempo)
    assert servicio.otif == 0.5
    assert servicio.lines_scored_against_declared_promise == 2


def test_un_canal_sin_promesa_declarada_no_toma_prestada_la_de_otro():
    """Una promesa prestada de otro canal es una vara inventada: la linea queda fuera
    del OTIF, que es lo que corresponde cuando nadie declaro nada para ese canal."""
    servicio = measure_from_order_lines(
        "SKU-1",
        [_linea(channel="exportacion", delivered_date="2026-09-08")],
        promise_days={"retail": 2},
    )

    assert servicio.otif is None
    assert servicio.lines_scored_against_declared_promise == 0


def test_el_canal_comodin_cubre_lo_que_el_cliente_no_separo():
    servicio = measure_from_order_lines(
        "SKU-1",
        [_linea(channel="exportacion", delivered_date="2026-09-08")],
        promise_days={"retail": 2, "*": 10},
    )

    assert servicio.otif == 1.0  # 1-sep + 10 habiles = 15-sep
    assert servicio.otif_basis == OTIF_BASIS_CLIENT_PROMISE


def test_sin_fecha_de_orden_no_hay_de_donde_derivar():
    servicio = measure_from_order_lines(
        "SKU-1", [{"ordered_qty": 10.0, "delivered_qty": 10.0, "delivered_date": "2026-09-08"}],
        promise_days=2,
    )

    assert servicio.otif is None


# -- el perfil del cliente ------------------------------------------------------


def test_la_promesa_viaja_del_perfil_al_motor():
    perfil = ClientProfile(
        client_id="acme", display_name="Acme", delivery_promise_days={"retail": 2.0}
    )

    assert perfil.as_params()["delivery_promise_days"] == {"retail": 2.0}


@pytest.mark.parametrize(
    "promesa",
    [
        {},                      # vacio no es "sin promesa": se usa None
        {"retail": -1},          # dias negativos
        {"retail": float("nan")},
        {"retail": 120},         # un plazo de importacion cargado donde va el despacho
        {"": 2},                 # canal vacio
        {"retail": True},        # un booleano no es una cantidad de dias
    ],
)
def test_el_perfil_rechaza_una_promesa_que_no_se_puede_cumplir(promesa):
    with pytest.raises(ValueError):
        ClientProfile(client_id="acme", display_name="Acme", delivery_promise_days=promesa)


def test_el_perfil_acepta_no_declarar_promesa():
    perfil = ClientProfile(client_id="acme", display_name="Acme")

    assert perfil.delivery_promise_days is None
    assert "delivery_promise_days" not in perfil.as_params()


# -- lo que ve el cliente -------------------------------------------------------


def _reporte_con(lineas_por_sku, **kw):
    import numpy as np
    import pandas as pd

    from jobs import inventory_optimization

    filas = []
    for sku in lineas_por_sku:
        for t in range(40):
            filas.append({
                "product_id": sku,
                "date": pd.Timestamp("2026-01-01") + pd.Timedelta(weeks=t),
                "quantity": float(max(0.0, 100 + 5 * np.sin(t))),
                "unit_cost": 12.0,
                "lead_time_days": 14.0,
            })
    order_lines = pd.DataFrame(
        [{"product_id": sku, **linea} for sku, ls in lineas_por_sku.items() for linea in ls]
    )
    return inventory_optimization.run(pd.DataFrame(filas), order_lines=order_lines, **kw)


def test_el_deck_rotula_el_otif_contra_la_promesa_declarada():
    """El rotulo es lo primero que se lee: un OTIF contra una vara dicha no puede
    llegarle al cliente con el mismo nombre que uno que su sistema registro."""
    from jobs import inventory_deliverable

    lineas = {"SKU-1": [_linea(delivered_date="2026-09-03"), _linea(delivered_date="2026-09-20")]}

    con = _reporte_con(lineas, delivery_promise_days=2)
    deck = inventory_deliverable.build(con)
    kpi = next(k for k in deck.kpis if k.name.startswith("Realized OTIF"))

    assert kpi.name == "Realized OTIF (vs your declared promise)"
    assert "declared" in kpi.rationale and "not against a date your system recorded" in kpi.rationale


def test_sin_promesa_el_deck_no_cambia_de_rotulo():
    from jobs import inventory_deliverable

    lineas = {"SKU-1": [_linea(promised_date="2026-09-05", delivered_date="2026-09-03")]}

    deck = inventory_deliverable.build(inventory_deliverable_report := _reporte_con(lineas))
    kpi = next(k for k in deck.kpis if k.name.startswith("Realized OTIF"))

    assert kpi.name == "Realized OTIF (observed)"
    assert "declared promise" not in kpi.rationale
    assert inventory_deliverable_report.n_skus == 1


def test_el_portafolio_declara_la_vara_cuando_un_solo_sku_la_uso():
    from src.order_fulfillment import aggregate

    con_promesa = measure_from_order_lines("A", [_linea(delivered_date="2026-09-03")], promise_days=2)
    del_erp = measure_from_order_lines(
        "B", [_linea(promised_date="2026-09-05", delivered_date="2026-09-04")]
    )

    total = aggregate([con_promesa, del_erp])

    assert total.otif_basis == OTIF_BASIS_MIXED
    assert total.lines_scored_against_declared_promise == 1
    assert total.lines_otif_scored == 2
