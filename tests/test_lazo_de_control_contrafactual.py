"""El punto de T02 y T10 viene del PRODUCTO, no de un corrector ablandado.

Los dos arreglos son de arnes: la capacidad ya estaba cableada y lo que fallaba
era la sonda. Arreglar el corrector para subir el propio marcador es la
operacion que mas disciplina necesita: medir el contrafactual. Que da la
bateria si el producto NO tuviera la capacidad, con la sonda nueva puesta.

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


def test_t02_sin_memoria_de_ciclo_en_el_producto_no_acredita():
    """La sonda pasa store y cycle_id. Si el job los ignorara, T02 no cumple."""
    from jobs import forecast_job

    with _producto_sin(forecast_job, store=None, cycle_id=None):
        r = _resultado(puntuar(), "T02")

    assert r.estado is not Estado.CUMPLE, (
        "T02 acredito sin que el producto compare contra el ciclo previo: "
        "el punto lo esta poniendo la sonda"
    )
    assert "comparacion contra el ciclo previo" in r.detalle["faltantes"]
    assert r.detalle["campos_que_reproducen_el_ciclo_previo"] == []
    assert r.detalle["campos_que_reproducen_el_cambio"] == []


def test_t02_los_otros_dos_requisitos_sobreviven_al_contrafactual():
    """El contrafactual apaga UNA cosa, no la sonda entera."""
    from jobs import forecast_job

    with _producto_sin(forecast_job, store=None, cycle_id=None):
        r = _resultado(puntuar(), "T02")

    assert "MAPE por SKU" not in r.detalle["faltantes"]
    assert "sesgo firmado" not in r.detalle["faltantes"]
    assert r.estado is Estado.PARCIAL and r.puntos == 0.5


def test_t10_sin_lineas_de_pedido_en_el_producto_no_acredita():
    """La sonda inyecta lineas de pedido. Si el job las ignorara, T10 no cumple."""
    from jobs import inventory_optimization

    with _producto_sin(inventory_optimization, order_lines=None):
        r = _resultado(puntuar(), "T10")

    assert r.estado is not Estado.CUMPLE, (
        "T10 acredito sin que el producto mida sobre pedidos reales"
    )
    assert r.detalle["campos_que_reproducen_lo_medido"] == []
    assert r.detalle["campos_que_reproducen_el_otif"] == []


def test_t10_reportar_el_objetivo_del_modelo_sigue_sin_alcanzar():
    """service_level puede moverse y sigue sin acreditar: moverse no es reproducir."""
    from jobs import inventory_optimization

    with _producto_sin(inventory_optimization, order_lines=None):
        r = _resultado(puntuar(), "T10")

    unidos = " ".join(r.detalle["campos_que_reproducen_lo_medido"])
    assert "service_level" not in unidos


def test_el_contrafactual_baja_el_marcador_y_el_gate_no_se_autoacredita():
    """Sin memoria de ciclo ni lineas de pedido, T02+T10 no pueden sumar lo mismo."""
    from jobs import forecast_job, inventory_optimization

    con_las_dos = puntuar().puntos
    with _producto_sin(forecast_job, store=None, cycle_id=None), \
         _producto_sin(inventory_optimization, order_lines=None):
        sin_las_dos = puntuar().puntos

    assert con_las_dos > sin_las_dos, (
        f"el contrafactual no bajo el marcador ({con_las_dos} -> {sin_las_dos}): "
        "la sonda dejo de exigir lo que exige"
    )
    assert con_las_dos - sin_las_dos >= 1.0
