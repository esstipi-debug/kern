"""El Tower deja de disparar quiebres falsos sobre stock que ya esta comprado.

Hasta el 2026-09-18 `rop_breach_monitor` y `stockout_projected_monitor` decidian sobre
`on_hand` -- el ESTANTE -- y no sobre la POSICION (`NFP = on_hand + on_order`). Eso
contradecia la regla central del repo, que los siete emisores de cantidad a pedir ya
cumplen (documentation/KERN_BARRIDO_NETEO.md): una OC que transporte atraso dos semanas
ya esta comprada, y volver a pedirla es comprar dos veces lo mismo.

La asimetria que estos tests fijan, y que es lo unico delicado del arreglo:

- el DISPARADOR se netea (posicion contra punto de reorden),
- la COBERTURA no (dias hasta quedar fisicamente vacio, sobre el estante).

Netear tambien la cobertura cambiaria un falso positivo por un falso NEGATIVO: una OC
que llega en tres semanas no alimenta el mostrador manana.
"""
from __future__ import annotations

import pandas as pd
import pytest

from jobs.state_bridge import StockLine
from scm_agent.monitors import rop_breach_monitor, stockout_projected_monitor
from src.alerting import detect_events
from src.state.system_state import (
    DOMAIN_COLUMNS,
    SchemaValidationError,
    StateSnapshot,
    _validate_fallback,
    _validate_schema,
)


def _fila(**kwargs) -> dict:
    fila = {"product_id": "A", "on_hand": 5.0, "reorder_point": 50.0,
            "avg_daily_demand": 10.0}
    fila.update(kwargs)
    return fila


# -- 1. El disparador netea --------------------------------------------------------


def test_lo_que_ya_viene_en_camino_no_se_vuelve_a_pedir():
    """El caso que motivo todo, medido antes del arreglo: estante 5, punto de reorden
    50, y 100 unidades YA COMPRADAS en camino. Emitia un evento `high` de quiebre
    inminente sobre un SKU que tiene el doble de lo que necesita ya pagado."""
    assert detect_events([_fila(on_order=100.0)]) == []
    # Y sin el dato, el mismo SKU sigue disparando: no se calla por las dudas.
    assert [e.kind for e in detect_events([_fila()])] == ["stockout_risk"]


def test_un_en_transito_insuficiente_sigue_disparando():
    """Netear no es callar. Con 10 en camino la posicion (15) sigue bajo el punto de
    reorden (50), asi que el evento sale igual."""
    (evento,) = detect_events([_fila(on_order=10.0)])
    assert evento.kind == "stockout_risk"
    assert evento.detail["inventory_position"] == pytest.approx(15.0)
    assert evento.detail["netted"] is True


# -- 2. La cobertura NO se netea ---------------------------------------------------


def test_la_cobertura_se_mide_sobre_el_estante_no_sobre_la_posicion():
    """Es la mitad del arreglo que se puede equivocar en silencio. Con estante 5,
    10 en camino y demanda 10/dia: la posicion (15) decide que hay que pedir, pero los
    dias de cobertura son 0,5 (5/10) y NO 1,5 (15/10). Si se netearan, un SKU que
    queda vacio pasado manana se leeria como que aguanta el triple."""
    (evento,) = detect_events([_fila(on_order=10.0)])
    assert evento.detail["days_of_cover"] == pytest.approx(0.5)
    assert evento.severity == "high"      # y por eso sigue siendo urgente


# -- 3. La ausencia se declara, y el NaN no enmudece al monitor ---------------------


def test_sin_dato_de_en_transito_el_evento_lo_dice():
    """Un evento decidido sobre el estante y uno decidido sobre la posicion no son la
    misma afirmacion. El que no pudo netear lo lleva escrito, en el detalle y en el
    mensaje, en vez de parecer que si."""
    (evento,) = detect_events([_fila()])
    assert evento.detail["netted"] is False
    assert evento.detail["on_order"] is None
    assert "sin netear" in evento.message


@pytest.mark.parametrize("ausente", [None, float("nan"), "", "n/d"])
def test_un_en_transito_no_medible_no_enmudece_al_monitor(ausente):
    """El NaN es el caso real, no teorico: el dominio `stock` viaja por parquet, donde
    un `None` de Python vuelve como NaN. Sin filtrarlo, `on_hand + nan` es `nan`,
    `nan <= rop` es `False`, y el monitor se queda MUDO sobre un quiebre real -- peor
    que el bug que este arreglo cierra."""
    (evento,) = detect_events([_fila(on_order=ausente)])
    assert evento.kind == "stockout_risk"
    assert evento.detail["netted"] is False


def test_un_en_transito_negativo_no_infla_la_posicion_hacia_abajo():
    """No existe un en-transito negativo. Dejarlo pasar bajaria la posicion y volveria
    a disparar de mas, que es la direccion contraria a la del arreglo."""
    # Estante 60, POR ENCIMA del punto de reorden (50): no hay nada que pedir. Con el
    # -100 aplicado la posicion daria -40 y saldria un quiebre inminente inventado.
    assert detect_events([_fila(on_hand=60.0, on_order=-100.0)]) == []
    # Y se trata como ausente, no como cero medido: el `-100` no acredita nada.
    (evento,) = detect_events([_fila(on_hand=5.0, on_order=-100.0)])
    assert evento.detail["on_order"] is None
    assert evento.detail["netted"] is False


# -- 4. Punta a punta, por los dos monitores del Tower ------------------------------


def _snapshot(filas: list[dict]) -> StateSnapshot:
    return StateSnapshot(domain="stock", payload=pd.DataFrame(filas),
                         cycle_id="2026-09-18T00:00:00+00:00",
                         created_at="2026-09-18T00:00:00+00:00")


def test_los_dos_monitores_del_tower_dejan_de_emitir_el_falso_positivo():
    con_oc = _snapshot([_fila(on_order=100.0)])
    sin_oc = _snapshot([_fila()])
    assert rop_breach_monitor(con_oc) == []
    assert stockout_projected_monitor(con_oc) == []
    # El mismo SKU sin el dato sigue emitiendo, y el payload del evento lo declara.
    (evento,) = stockout_projected_monitor(sin_oc)
    assert evento.payload["netted"] is False
    assert evento.payload["inventory_position"] == pytest.approx(5.0)


# -- 5. El puente no inventa ceros --------------------------------------------------


def test_el_puente_omite_la_columna_cuando_el_job_no_pudo_medirla():
    """`None` y `0.0` no son lo mismo y la fila no puede confundirlos: sin dato la
    clave no viaja, en vez de viajar en cero."""
    assert "on_order" not in StockLine("A", 5.0, 50.0, 10.0).as_row("t")
    assert StockLine("A", 5.0, 50.0, 10.0, 0.0).as_row("t")["on_order"] == 0.0
    assert StockLine("A", 5.0, 50.0, 10.0, 7.0).as_row("t")["on_order"] == 7.0


# -- 6. El esquema: opcional, anulable, y validada cuando viene ---------------------


@pytest.mark.parametrize("extra,valida", [
    ({}, True),                              # la columna puede faltar entera
    ({"on_order": [100.0]}, True),
    ({"on_order": [float("nan")]}, True),    # un SKU sin dato dentro de un frame que si tiene
    ({"on_order": [-5.0]}, False),           # un en-transito negativo no existe
])
def test_el_contrato_del_dominio_stock_por_los_DOS_caminos(extra, valida):
    """Los dos validadores tienen que decir lo mismo. El de reserva (sin pandera) es el
    camino degradado que este modulo promete: si no aprende que la columna es opcional,
    una instalacion sin pandera rechaza TODO snapshot de stock sin en-transito,
    incluido el que escribe el marcador."""
    df = pd.DataFrame({"product_id": ["A"], "on_hand": [5.0], "reorder_point": [50.0],
                       "avg_daily_demand": [10.0], **extra})
    problemas = _validate_fallback(DOMAIN_COLUMNS["stock"], df)
    assert (not problemas) is valida, problemas
    if valida:
        _validate_schema("stock", df)
    else:
        with pytest.raises(SchemaValidationError):
            _validate_schema("stock", df)
