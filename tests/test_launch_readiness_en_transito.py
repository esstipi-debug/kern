"""`launch_readiness` deja de pintar de rojo el stock que ya esta comprado.

El veredicto preguntaba si el ESTANTE alcanza para llegar al lanzamiento, asi que un SKU
con una OC en camino -- que aterriza antes de la fecha, con la plata ya gastada -- salia
`red` con la razon "una orden no puede llegar a tiempo". Era falso dos veces: no hacia
falta una orden nueva, y la que existia si llegaba.

**Por que este tool netea distinto que los otros ocho del barrido.** Los demas comparan
una cantidad contra una posicion y les alcanza con `on_order`. Aca la pregunta es contra
una FECHA, asi que una cantidad sin fecha de llegada no puede contestarla. La regla que
estos tests fijan:

- ETA <= fecha de lanzamiento -> cuenta;
- ETA posterior -> NO cuenta, y se dice por que;
- cantidad sin ETA -> NO cuenta (contarla seria inventar una fecha) pero se NOMBRA,
  porque callarla es el mismo defecto con el signo cambiado.
"""
from __future__ import annotations

from datetime import date

import pytest

from jobs.launch_readiness_job import (
    VERDICT_GREEN,
    VERDICT_RED,
    LaunchInput,
    _assess_sku,
)

HOY = date(2026, 1, 1)
LANZAMIENTO = date(2026, 1, 31)          # 30 dias por delante

#: Plazo 45d > 30d hasta el lanzamiento: sin en-transito el veredicto es ROJO por
#: construccion ("una orden no puede llegar a tiempo"). Es el caso que motivo el arreglo.
_BASE = dict(product_id="SKU-L", launch_date=LANZAMIENTO, lift_pct=0.0, has_coverage=True,
             on_hand=50.0, daily_demand=10.0, lead_time_days=45.0)


def _linea(**extra):
    return _assess_sku(LaunchInput(**_BASE, **extra), service_level=0.95, as_of=HOY)


def test_sin_en_transito_el_veredicto_no_cambia():
    """El arreglo no puede mover el caso que no lo necesita: un archivo sin las columnas
    nuevas decide exactamente igual que antes."""
    linea = _linea()
    assert linea.verdict == VERDICT_RED
    assert linea.on_order is None
    assert linea.position_at_launch == pytest.approx(50.0)
    assert "NOTE:" not in linea.reason


def test_lo_que_llega_antes_del_lanzamiento_deja_de_pintar_rojo():
    """El caso que motivo todo: 400 unidades ya compradas que aterrizan 11 dias antes."""
    linea = _linea(on_order=400.0, on_order_eta=date(2026, 1, 20))
    assert linea.verdict == VERDICT_GREEN
    assert linea.on_order_before_launch == pytest.approx(400.0)
    assert linea.position_at_launch == pytest.approx(450.0)
    assert "without a NEW reorder" in linea.reason


def test_lo_que_llega_despues_del_lanzamiento_no_cuenta_y_lo_dice():
    """Llegar tarde es una RESPUESTA, no una duda: no acredita, y el veredicto explica
    por que, para que nadie relea la OC pensando que el tool no la vio."""
    linea = _linea(on_order=400.0, on_order_eta=date(2026, 2, 15))
    assert linea.verdict == VERDICT_RED
    assert linea.on_order_before_launch == pytest.approx(0.0)
    assert linea.position_at_launch == pytest.approx(50.0)
    assert "AFTER the launch - not counted" in linea.reason


def test_una_cantidad_sin_fecha_no_se_cuenta_pero_se_nombra():
    """Contarla seria inventar una fecha de llegada. Callarla seria el defecto que este
    arreglo cierra, al reves. Se declara, y se dice que columna falta."""
    linea = _linea(on_order=400.0)
    assert linea.verdict == VERDICT_RED
    assert linea.on_order == pytest.approx(400.0)
    assert linea.on_order_before_launch is None      # no se pudo afirmar
    assert linea.position_at_launch == pytest.approx(50.0)
    assert "NO arrival date" in linea.reason
    assert "add an ETA column" in linea.reason


def test_una_fecha_ilegible_se_trata_como_ausente():
    """Una ETA que no se puede leer no habilita a adivinarla."""
    import pandas as pd

    from jobs.launch_readiness_job import _eta
    fila = pd.Series({"eta": "no se sabe"})
    assert _eta(fila, "eta") is None
    assert _eta(fila, None) is None


def test_un_en_transito_negativo_no_baja_la_posicion():
    """No existe un en-transito negativo; dejarlo pasar volveria a pintar de rojo lo que
    ya esta comprado, que es la direccion contraria."""
    import pandas as pd

    from jobs.launch_readiness_job import _en_camino
    assert _en_camino(pd.Series({"oo": -100.0}), "oo") is None
    assert _en_camino(pd.Series({"oo": 0.0}), "oo") == 0.0
    assert _en_camino(pd.Series({"oo": 7.0}), "oo") == 7.0


def test_la_cobertura_sigue_midiendose_sobre_el_estante():
    """Misma asimetria que en `rop_breach`, por la misma razon: `days_of_cover` son los
    dias hasta quedar FISICAMENTE vacio, y una OC que todavia no llego no alimenta el
    mostrador hoy. Lo que el en-transito mueve es el VEREDICTO, no la cobertura."""
    linea = _linea(on_order=400.0, on_order_eta=date(2026, 1, 20))
    assert linea.days_of_cover == pytest.approx(5.0)        # 50 / 10, el estante
    assert linea.position_at_launch == pytest.approx(450.0)  # y la posicion, aparte


def test_la_sonda_del_barrido_exige_las_dos_mitades():
    """La sonda acredita solo si lo que llega a tiempo mueve el veredicto Y lo que llega
    tarde NO lo mueve. Sin la segunda mitad, un tool que sumara el en-transito sin mirar
    la fecha pasaria la sonda y seguiria mintiendo sobre el dia del lanzamiento."""
    from src.bateria.barrido_neteo import sonda_launch_readiness

    sonda = sonda_launch_readiness()
    assert sonda.estado == "netea"
    assert sonda.campo_en_transito == "columna on_order + ETA"
    assert "->" in sonda.prueba and "ya emitidas" in sonda.prueba
    assert "DESPUES no acredita" in sonda.prueba
