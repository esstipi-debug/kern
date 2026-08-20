"""El +1,0 de T02 y T10 viene del PRODUCTO, no de un corrector ablandado (kern#252).

Los dos arreglos son de ARNES: la capacidad ya estaba cableada en el producto y
lo que fallaba era la sonda. Arreglar el corrector para subir el propio marcador
es la operacion que mas disciplina necesita, y `08-MEDICION.md` seccion 3 fija
cual: **medir el contrafactual**. Que da la bateria si el producto NO tuviera la
capacidad, con la sonda nueva puesta.

Estos tests son ese contrafactual, escrito una vez y no medido a mano.

Si alguno se pone rojo, la lectura es: la sonda dejo de exigir lo que dice
exigir, y el punto pasa a estar acreditado por el instrumento en vez de por el
entregable.
"""

from __future__ import annotations

from contextlib import contextmanager

import src.bateria.comprobaciones  # noqa: F401  -- registra las comprobaciones
from src.bateria.scorer import Estado, puntuar


def _resultado(puntaje, tarea_id: str):
    return next(r for r in puntaje.resultados if r.tarea_id == tarea_id)


@contextmanager
def _producto_sin(modulo, **quitar_kwargs):
    """Un producto que IGNORA los canales nombrados, como antes de existir."""
    real = modulo.run

    def _mutilado(primero, **kwargs):
        return real(primero, **{k: v for k, v in kwargs.items() if k not in quitar_kwargs})

    modulo.run = _mutilado
    try:
        yield
    finally:
        modulo.run = real


# -- T02 -----------------------------------------------------------------------


def test_t02_sin_memoria_de_ciclo_en_el_producto_no_acredita():
    """La sonda pasa `store` y `cycle_id`. Si el job los ignorara -- o sea, si la
    memoria de ciclo no existiera -- T02 NO puede quedar en CUMPLE."""
    from jobs import forecast_job

    with _producto_sin(forecast_job, store=None, cycle_id=None):
        r = _resultado(puntuar(), "T02")

    assert r.estado is not Estado.CUMPLE, (
        "T02 acredito sin que el producto compare contra el ciclo previo: "
        "el punto lo esta poniendo la sonda"
    )
    assert r.detalle["faltantes"] == ["comparacion contra el ciclo previo"]
    assert r.detalle["campos_de_ciclo_previo_con_valor_que_se_mueve"] == []


def test_t02_los_otros_dos_requisitos_sobreviven_al_contrafactual():
    """El contrafactual tiene que apagar UNA cosa, no la sonda entera. Si MAPE y
    sesgo tambien se cayeran, no estariamos midiendo lo que creemos."""
    from jobs import forecast_job

    with _producto_sin(forecast_job, store=None, cycle_id=None):
        r = _resultado(puntuar(), "T02")

    assert "MAPE por SKU" not in r.detalle["faltantes"]
    assert "sesgo firmado" not in r.detalle["faltantes"]
    assert r.estado is Estado.PARCIAL and r.puntos == 0.5


# -- T10 -----------------------------------------------------------------------


def test_t10_sin_lineas_de_pedido_en_el_producto_no_acredita():
    """La sonda inyecta lineas de pedido reales. Si el job las ignorara, ningun
    campo puede reproducir el hecho y T10 no acredita.

    Notar que el contrafactual da NO_CUMPLE, mas duro que el PARCIAL que daba la
    sonda vieja sobre el mismo producto: el gate se ENDURECIO. Un endurecimiento
    que sube el marcador solo puede hacerlo porque el producto de verdad lo hace.
    """
    from jobs import inventory_optimization

    with _producto_sin(inventory_optimization, order_lines=None, stockout_days=None):
        r = _resultado(puntuar(), "T10")

    assert r.estado is not Estado.CUMPLE, (
        "T10 acredito sin que el producto mida sobre pedidos reales"
    )
    assert r.detalle["campos_que_reproducen_lo_medido"] == []


def test_t10_reportar_el_objetivo_del_modelo_sigue_sin_alcanzar():
    """Lo eliminatorio del criterio no se aflojo: `service_level` se mueve entre
    las dos corridas y sigue sin acreditar, porque moverse no es reproducir."""
    from jobs import inventory_optimization

    with _producto_sin(inventory_optimization, order_lines=None, stockout_days=None):
        r = _resultado(puntuar(), "T10")

    assert "service_level" not in " ".join(r.detalle["campos_que_reproducen_lo_medido"])


# -- la aritmetica del marcador, fijada ----------------------------------------


def test_el_delta_publicable_es_de_un_punto_y_el_gate_quedo_mas_duro():
    """Dos numeros distintos que conviene no confundir, y por eso van juntos.

    **El delta publicable es +1,0**: 12,0 con las sondas VIEJAS sobre este mismo
    producto (T02 parcial, T10 parcial) contra 13,0 con las nuevas. Es lo que se
    escribe en el marcador.

    **El contrafactual da 11,5, no 12,0**, y eso NO es un error: la sonda nueva de
    T10 es mas dura que la vieja. Sobre un producto sin la capacidad, la vieja
    daba PARCIAL 0,5 -- acreditaba medio punto porque el fill rate reproducia --
    y la nueva da NO_CUMPLE 0,0, porque ahora exige que los TRES campos
    reproduzcan su propio hecho.

    O sea: el gate subio el marcador Y se puso mas exigente al mismo tiempo. Esa
    combinacion solo es posible si el punto lo pone el producto. Si alguna vez el
    contrafactual sube hacia 12,0 o mas, el gate se afloja y hay que mirar por que.
    """
    from jobs import forecast_job, inventory_optimization

    con_las_dos = puntuar().puntos
    with _producto_sin(forecast_job, store=None, cycle_id=None), \
         _producto_sin(inventory_optimization, order_lines=None, stockout_days=None):
        sin_las_dos = puntuar().puntos

    assert con_las_dos == 13.0, con_las_dos
    assert sin_las_dos == 11.5, (
        f"el contrafactual dio {sin_las_dos}: la sonda dejo de exigir lo que exige"
    )
    # 13,0 medido contra los 12,0 que daban las sondas viejas sobre este producto.
    assert con_las_dos - 12.0 == 1.0


# -- las dos trampas de la sonda de T02, fijadas -------------------------------


def test_la_sonda_de_t02_usa_el_mismo_product_id_en_los_dos_ciclos():
    """Con ids distintos `previous_mape` por SKU no matchea NUNCA, y el campo
    saldria vacio por una razon que no es la que se esta midiendo."""
    from src.bateria import comprobaciones

    assert isinstance(comprobaciones._SKU_SONDA, str) and comprobaciones._SKU_SONDA


def test_la_sonda_de_t02_usa_dos_stores_y_no_uno():
    """Dos razones, y las dos importan:

    1. Con un solo store, `previous_mean_mape` va de None a un valor y `se_mueve`
       lo acreditaria POR APARICION -- acreditar por presencia otra vez.
    2. El CUMPLE viejo de T02 estaba sostenido por dos corridas compartiendo la
       particion sin nombre, o sea cruce de datos entre clientes. Un solo store
       compartido seria aquel bug con otra ropa.
    """
    import inspect

    from src.bateria import comprobaciones

    fuente = inspect.getsource(comprobaciones.t02_sesgo_y_precision)
    particiones = {
        linea.split('"')[1]
        for linea in fuente.splitlines()
        if "_dos_ciclos(carpeta," in linea and '"' in linea
    }
    assert len(particiones) == 2, f"la sonda de T02 usa {len(particiones)} particion(es): {particiones}"
