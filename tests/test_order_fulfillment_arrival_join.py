"""Lo que el motor tiene que sostener cuando le sacan las fechas de entrega.

Empalmar un transportista pone ``delivered_date=None`` en toda orden que el transportista no
reporto -- incluso en las que el ERP daba por entregadas. Eso no es un caso hipotetico: es la
mitad del comportamiento de ``with_arrivals``, y toca dos supuestos del motor que hasta hoy
nadie habia tensado porque ningun ERP entrega cantidades sin fechas.

Los dos son degradaciones SILENCIOSAS -- numeros mejores, plausibles, sin un solo ``None``:

1. **La ventana derivada se encoge** al perder las fechas, y la guarda de "en curso" empieza
   a excluir lineas cortas que ya estaban resueltas. Medido: fill 0,85 -> 1,0; ventas
   perdidas 3,0 -> 0,0; dias sin stock 1 -> 0.
2. **El roll-up de cartera no propaga la exclusion**, asi que a nivel portfolio la unica
   senal de que se descarto evidencia no existe.

Y una tercera que no es silenciosa sino simplemente mal ponderada: el OTIF de cartera se
pondera por ``lines_scored``, que NO es el denominador del OTIF de cada registro.
"""

from __future__ import annotations

import pytest

from src import order_fulfillment as of
from src.connectors import Order, OrderLine, observation_window_end, realized_service
from src.connectors.arrivals import with_arrivals


class _NingunaLlegada:
    """Un transportista que no conoce ninguna orden. El peor caso del empalme."""

    def arrivals(self, *, since: str | None = None):
        return []


def _linea(want: float, got: float, prometida: str, entregada: str | None) -> OrderLine:
    return OrderLine(
        sku="SKU-1", quantity=want, price=20.0,
        delivered_qty=got, promised_date=prometida, delivered_date=entregada,
    )


#: SO-A quedo corta (7 de 10) con una promesa DENTRO de la ventana. SO-B se sirvio completa
#: y su despacho es lo que estira la ventana hasta el 30.
_CARTERA = [
    Order("SO-A", "2026-01-05", (_linea(10.0, 7.0, "2026-01-20", "2026-01-15"),)),
    Order("SO-B", "2026-01-10", (_linea(10.0, 10.0, "2026-01-20", "2026-01-30"),)),
]


def test_la_ventana_derivada_se_encoge_cuando_el_empalme_borra_las_fechas():
    """La precondicion del problema, fijada como hecho para que nadie la discuta.

    Sin esto, los dos tests que siguen parecen paranoia. La ventana pasa del 30 al 10 de
    enero porque su unico insumo que no es una fecha de creacion son las entregas.
    """
    assert observation_window_end(_CARTERA) == "2026-01-30"

    empalmadas = with_arrivals(_CARTERA, _NingunaLlegada()).orders

    assert observation_window_end(empalmadas) == "2026-01-10"


def test_medir_con_la_ventana_pinneada_da_el_mismo_numero_que_el_erp_solo():
    """LA DEFENSA.

    El fill rate y las ventas perdidas no dependen de fechas -- son cantidades. Que cambien
    al empalmar un transportista es un artefacto, no una medicion. Con la ventana fijada
    ANTES del empalme, los tres numeros vuelven a ser los del ERP.
    """
    ventana = observation_window_end(_CARTERA)
    solo_erp = of.aggregate(list(realized_service(_CARTERA)))

    empalmadas = list(with_arrivals(_CARTERA, _NingunaLlegada()).orders)
    pinneado = of.aggregate(list(realized_service(empalmadas, as_of=ventana)))

    assert pinneado.fill_rate == pytest.approx(solo_erp.fill_rate) == pytest.approx(0.85)
    assert pinneado.lost_sales_units == pytest.approx(solo_erp.lost_sales_units)
    assert pinneado.days_out_of_stock == solo_erp.days_out_of_stock
    assert pinneado.lines_scored == solo_erp.lines_scored == 2


def test_sin_pinnear_el_fill_rate_sube_solo_y_esa_es_la_falla_que_se_previene():
    """El contrafactico. Documenta la magnitud para que quede claro que no es cosmetico:
    una cartera medida en 85% con 3 unidades perdidas pasa a servicio perfecto."""
    empalmadas = list(with_arrivals(_CARTERA, _NingunaLlegada()).orders)

    derivado = of.aggregate(list(realized_service(empalmadas)))

    assert derivado.fill_rate == pytest.approx(1.0)
    assert derivado.lost_sales_units == pytest.approx(0.0)
    assert derivado.lines_scored == 1, "una linea se cayo del denominador"


def test_el_rollup_publica_cuantas_lineas_quedaron_en_curso():
    """La segunda degradacion silenciosa: a nivel SKU la exclusion se ve en
    ``lines_in_flight`` y en el ``method``; a nivel cartera -- que es lo que consume el
    entregable -- no se veia. Un lector no podia dimensionar lo que se descarto."""
    empalmadas = list(with_arrivals(_CARTERA, _NingunaLlegada()).orders)
    registros = list(realized_service(empalmadas))

    por_sku = sum(r.lines_in_flight for r in registros)
    assert por_sku == 1, "precondicion: a nivel SKU la exclusion si se ve"

    assert of.aggregate(registros).lines_in_flight == por_sku


def test_el_otif_de_cartera_se_pondera_por_las_lineas_que_puntuaron_otif():
    """La tercera: el peso equivocado.

    Cada registro calcula su OTIF sobre las lineas que tenian promesa Y fecha; el roll-up
    lo ponderaba por ``lines_scored``, que es el denominador del FILL RATE. Mientras el ERP
    fecha todo lo que entrega los dos numeros coinciden y nadie lo nota. Un transportista
    con cobertura parcial los divorcia: un SKU con 10 lineas servidas y solo 2 con fecha de
    llegada pesaba cinco veces mas de lo que su evidencia de OTIF justifica.
    """
    #: A: 4 lineas servidas, solo 1 con fecha -> OTIF 0.0 sobre 1 linea de evidencia.
    a = of.measure_from_order_lines("A", [
        {"ordered_qty": 10, "delivered_qty": 10, "promised_date": "2026-01-10",
         "delivered_date": "2026-01-20"},
        *[{"ordered_qty": 10, "delivered_qty": 10, "promised_date": "2026-01-10"}] * 3,
    ], as_of="2026-01-31")
    #: B: 1 linea servida, 1 con fecha -> OTIF 1.0 sobre 1 linea de evidencia.
    b = of.measure_from_order_lines("B", [
        {"ordered_qty": 10, "delivered_qty": 10, "promised_date": "2026-01-10",
         "delivered_date": "2026-01-05"},
    ], as_of="2026-01-31")

    assert (a.otif, a.lines_scored) == (0.0, 4)
    assert (b.otif, b.lines_scored) == (1.0, 1)
    assert a.lines_otif_scored == 1, "A tiene UNA linea con las dos fechas, no cuatro"
    assert b.lines_otif_scored == 1

    # Ponderado por evidencia de OTIF: 0.0 y 1.0 pesan igual -> 0.5.
    # Ponderado por lines_scored (el bug): 0.0*4 + 1.0*1 sobre 5 -> 0.2.
    assert of.aggregate([a, b]).otif == pytest.approx(0.5)


def test_el_rollup_suma_las_lineas_que_puntuaron_otif():
    """El campo tambien se agrega: sin esto, un lector del portfolio no puede saber sobre
    cuanta evidencia descansa el OTIF que esta leyendo."""
    a = of.measure_from_order_lines("A", [
        {"ordered_qty": 10, "delivered_qty": 10, "promised_date": "2026-01-10",
         "delivered_date": "2026-01-05"},
    ], as_of="2026-01-31")
    b = of.measure_from_order_lines("B", [
        {"ordered_qty": 10, "delivered_qty": 10, "promised_date": "2026-01-10",
         "delivered_date": "2026-01-05"},
    ], as_of="2026-01-31")

    assert of.aggregate([a, b]).lines_otif_scored == 2


def test_una_llegada_tardia_revela_que_el_tiempo_avanzo_para_toda_la_cartera():
    """LA MINA QUE EL PIN SOLO NO TAPA.

    Cobertura del transportista PARCIAL, no cero. O1 se sirvio completa y el ERP la
    despacho a tiempo (04, contra promesa 05); el transportista dice que en realidad
    LLEGO el 10 -- tarde, y esa fecha prueba que el tiempo del mundo real ya paso el 10.
    O2 es un stockout TOTAL con promesa el 07, y el transportista nunca la reporta.

    Fijar la ventana SOLO con hechos pre-empalme (lo que este archivo defendia hasta este
    test) la deja en el 04 -- la fecha de despacho de O1, la unica que el ERP conocia. Con
    esa ventana, O2 todavia no vencio segun el reloj pinneado y cae en la guarda "en
    curso": desaparece del fill rate, del OTIF y de las ventas perdidas A LA VEZ, no solo
    del OTIF. Un stockout total se leeria como servicio perfecto.

    La correccion no es dejar de pinnear -- eso reabre el bug original, ver el test de
    arriba -- es no ignorar la evidencia que el EMPALME aporta cuando confirma tiempo mas
    reciente del que el ERP solo por si sabia. La ventana correcta es el maximo entre la
    pre-empalme y la post-empalme: nunca se encoge (protege el caso de cobertura cero) y
    crece cuando el transportista prueba que el reloj avanzo (protege este caso).
    """
    o1 = Order("O1", "2026-01-01",
              (_linea(10.0, 10.0, "2026-01-05", "2026-01-04"),))
    o2 = Order("O2", "2026-01-01",
              (_linea(10.0, 0.0, "2026-01-07", None),))

    from src.connectors.arrivals import Arrival

    class _SoloO1:
        def arrivals(self, *, since=None):
            return [Arrival("O1", "2026-01-10")]

    ventana_pre = observation_window_end([o1, o2])
    empalmadas = list(with_arrivals([o1, o2], _SoloO1()).orders)
    ventana_correcta = max(ventana_pre, observation_window_end(empalmadas))

    assert ventana_correcta == "2026-01-10", "la llegada tardia de O1 tiene que ganar"

    registros = list(realized_service(empalmadas, as_of=ventana_correcta))
    portfolio = of.aggregate(registros)

    assert portfolio.fill_rate == pytest.approx(0.5), "el stockout de O2 tiene que contar"
    assert portfolio.lost_sales_value == pytest.approx(200.0)
    assert portfolio.lines_in_flight == 0, "O2 ya vencio, no esta en curso"


def test_sin_la_correccion_el_stockout_se_leeria_como_servicio_perfecto():
    """El contrafactico, para que la magnitud del bug quede en el registro: pinnear SOLO
    con hechos pre-empalme sobre la misma cartera da fill 100% y $0 perdidos."""
    o1 = Order("O1", "2026-01-01",
              (_linea(10.0, 10.0, "2026-01-05", "2026-01-04"),))
    o2 = Order("O2", "2026-01-01",
              (_linea(10.0, 0.0, "2026-01-07", None),))

    class _SoloO1:
        def arrivals(self, *, since=None):
            from src.connectors.arrivals import Arrival
            return [Arrival("O1", "2026-01-10")]

    ventana_pre = observation_window_end([o1, o2])
    empalmadas = list(with_arrivals([o1, o2], _SoloO1()).orders)

    solo_pre = of.aggregate(list(realized_service(empalmadas, as_of=ventana_pre)))

    assert solo_pre.fill_rate == pytest.approx(1.0)
    assert solo_pre.lost_sales_value == pytest.approx(0.0)
    assert solo_pre.lines_in_flight == 1, "la evidencia se descarto, y aca es donde se ve"


def test_un_sku_sin_ninguna_fecha_de_llegada_no_aporta_al_otif_de_cartera():
    """Y el caso limite del empalme: cobertura cero. El SKU cuenta para el fill rate y no
    aporta ni un voto al OTIF, porque no tiene evidencia de puntualidad -- ni buena ni
    mala. Contarlo como cumplimiento o como falla seria inventar la mitad del numero."""
    sin_fechas = of.measure_from_order_lines("A", [
        {"ordered_qty": 10, "delivered_qty": 10, "promised_date": "2026-01-10"},
    ], as_of="2026-01-31")

    assert sin_fechas.otif is None
    assert sin_fechas.lines_otif_scored == 0
    assert sin_fechas.lines_scored == 1
