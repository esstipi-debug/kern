"""El barrido de neteo: la lista COMPLETA de quien descuenta lo que ya viene (A).

La bateria puntua el MEJOR de tres entregables de reposicion (T05), y eso es correcto
para una vara y ciego para el producto: por eso H4 -- el tool central dimensionando
``s`` y ``Q`` sin mirar jamas el estante -- vivio meses con la bateria en verde. Un tool
que nadie sondea no se puede poner rojo.

Lo que este archivo cierra es esa ceguera, y lo hace con tres frenos:

1. **Clasificacion obligatoria.** Todo tool del registro esta en ``EMISORES`` o en
   ``NO_EMISORES``, con razon escrita. Un tool nuevo que ponga un numero de compra en
   manos del operador no puede entrar sin decir con que sonda se prueba. Es el patron
   de ``UNDECLARED_TOOLS`` en ``tests/test_tool_data_contract.py``.
2. **Vector congelado.** ``ESTADOS_NETEO`` fija el resultado del barrido: cerrar una
   brecha (o abrir una) es un diff explicito, igual que ``PUNTAJE_CONGELADO``.
3. **La sonda se prueba a si misma.** Una sonda que acredita de mas es peor que no
   tenerla, asi que ``sonda_dos_corridas`` y el detector de canal se testean con casos
   sinteticos donde la respuesta correcta se sabe de antemano.

La disciplina que el barrido aplica es la de ``tests/test_neteo_en_transito.py``
(kern#251): el valor SE MUEVE, y la ausencia se declara.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scm_agent.tools import build_default_registry  # noqa: E402
from scripts.barrido_neteo import evidencia_markdown, markdown, resumen  # noqa: E402
from src.bateria import barrido_neteo as bn  # noqa: E402

DOC = _REPO_ROOT / "documentation" / "KERN_BARRIDO_NETEO.md"

#: El barrido medido en esta rama. Cerrar una brecha mueve este vector A PROPOSITO:
#: se edita en el mismo PR que la cierra, con `python scripts/barrido_neteo.py --markdown`
#: para regenerar el documento.
#:
#: ``no_netea`` y ``sin_sonda`` arrancan VACIOS y esa es la unica parte del vector que
#: vale como meta: un tool que cae ahi es una brecha abierta con canal ya construido.
#: Los ``sin_canal`` son decisiones pendientes, no bugs (ver el documento).
#:
#: ``launch_readiness`` paso de ``sin_canal`` a ``netea`` el 2026-09-18: era el unico
#: de los seis cuya razon escrita nombraba un DANO concreto ("el veredicto puede salir
#: rojo sobre stock que ya esta comprado") y no una decision de producto pendiente.
#: Netea distinto que los otros ocho, y por eso su sonda corre tres veces: contra una
#: FECHA de lanzamiento, una cantidad sin ETA no puede afirmar que llega a tiempo.
ESTADOS_NETEO = {
    "netea": [
        "channel_replenishment",
        "ddmrp",
        "drp",
        "excel_replenishment",
        "inventory_optimization",
        "launch_readiness",
        "odoo_replenishment",
        "shopify_replenishment",
    ],
    "no_netea": [],
    "sin_sonda": [],
    "sin_canal": [
        "hat_settlement",
        "hat_tension",
        "multi_echelon",
        "newsvendor",
        "simulation",
        "sop",
    ],
}


@pytest.fixture(scope="module")
def sondas() -> tuple[bn.SondaNeteo, ...]:
    """El barrido entero, una vez. Cada sonda corre su emisor dos veces."""
    return bn.barrer()


# -- 1. clasificacion obligatoria -------------------------------------------------


def test_todo_tool_del_registro_dice_si_emite_un_numero_de_compra():
    """El freno: un tool nuevo no entra sin clasificarse.

    Si esto se pone rojo, la pregunta no es "¿como lo apago?" sino "¿este tool pone un
    numero de compra en manos del operador?". Si si, va a ``EMISORES`` CON SONDA; si no,
    a ``NO_EMISORES`` con la razon. Las dos respuestas son baratas; no contestar es lo
    que costo H4.
    """
    registrados = {t.key for t in build_default_registry().list()}
    clasificados = set(bn.EMISORES) | set(bn.NO_EMISORES)
    assert registrados - clasificados == set(), (
        "tools sin clasificar en el barrido de neteo: agregalos a EMISORES (con sonda) "
        "o a NO_EMISORES (con razon)"
    )
    assert clasificados - registrados == set(), "el barrido clasifica tools que ya no existen"


def test_ningun_tool_esta_en_las_dos_listas():
    assert set(bn.EMISORES) & set(bn.NO_EMISORES) == set()


def test_cada_no_emisor_trae_una_razon_escrita():
    """Un conjunto pelado invitaria a esconder ahi lo que no se quiso sondear."""
    for tool, razon in bn.NO_EMISORES.items():
        assert len(razon.split()) >= 5, f"{tool}: la razon es demasiado corta para serlo"


# -- 2. el vector congelado -------------------------------------------------------


def test_el_barrido_reproduce_el_vector_congelado(sondas):
    assert bn.clasificacion(sondas) == ESTADOS_NETEO, (
        "el barrido cambio; si es deliberado, actualiza ESTADOS_NETEO y regenera "
        "documentation/KERN_BARRIDO_NETEO.md con `python scripts/barrido_neteo.py --markdown`"
    )


def test_no_hay_brechas_abiertas_con_canal_ya_construido(sondas):
    """La parte del vector que vale como meta, dicha aparte para que se lea.

    ``sin_canal`` es una decision pendiente; ``no_netea`` es un job que TIENE el dato y
    no lo usa, y ``sin_sonda`` es un campo que nadie probo -- las dos formas del defecto
    que kern#251 y H4 documentan.
    """
    abiertas = {s.tool: s.prueba for s in sondas if s.estado in ("no_netea", "sin_sonda")}
    assert abiertas == {}, f"emisores con canal y sin neteo probado: {abiertas}"


def test_cada_veredicto_de_neteo_muestra_las_dos_corridas(sondas):
    """Acreditar el neteo exige evidencia de comportamiento, no un nombre de campo."""
    for s in sondas:
        if s.estado in ("netea", "no_netea"):
            assert "->" in s.prueba, f"{s.tool} acredita sin mostrar dos corridas: {s.prueba}"
            assert "ya emitidas" in s.prueba


def test_los_sin_canal_nombran_el_entregable_y_la_razon(sondas):
    for s in sondas:
        if s.estado == "sin_canal":
            assert "." in s.entregable, f"{s.tool}: el entregable tiene que ser importable"
            assert len(s.prueba.split()) >= 10, f"{s.tool}: la razon no explica nada"


# -- 3. la sonda se prueba a si misma ---------------------------------------------


def test_la_sonda_acredita_solo_si_TODA_cantidad_baja():
    """Netear un SKU y olvidarse del resto sobre-compra igual."""
    baja_todo = bn.sonda_dos_corridas(
        lambda x: [100.0 - x, 50.0 - x], en_camino=10.0)
    baja_uno = bn.sonda_dos_corridas(
        lambda x: [100.0 - x, 50.0], en_camino=10.0)
    assert baja_todo[0] is True and "baja:" in baja_todo[1]
    assert baja_uno[0] is False and "NO baja" in baja_uno[1]


def test_una_cantidad_que_no_se_mueve_no_acredita():
    """El ataque: un campo ``on_order`` agregado al dataclass y nunca cableado."""
    descuenta, prueba = bn.sonda_dos_corridas(lambda x: [100.0, 50.0], en_camino=80.0)
    assert descuenta is False
    assert "[100.0, 50.0] -> [100.0, 50.0]" in prueba


def test_una_sonda_que_no_dimensiono_nada_no_se_acredita_ni_se_niega():
    """Un falso negativo con cara de medicion es peor que una sonda ausente."""
    vacia = bn.sonda_dos_corridas(lambda x: [], en_camino=80.0)
    ceros = bn.sonda_dos_corridas(lambda x: [0.0, 0.0], en_camino=80.0)
    for descuenta, prueba in (vacia, ceros):
        assert descuenta is None
        assert "no se midio nada" in prueba


def test_una_linea_sin_nada_que_pedir_no_arrastra_el_veredicto():
    """El SKU que ya esta cubierto emite 0 en las dos corridas: no puede bajar, y
    exigirselo convertiria en "NO netea" a un job que neteo todo lo que emitio."""
    descuenta, _ = bn.sonda_dos_corridas(lambda x: [100.0 - x, 0.0], en_camino=10.0)
    assert descuenta is True


def test_dos_corridas_con_distinta_cantidad_de_lineas_no_se_comparan():
    """Si el disparador apaga una linea entera, la sonda no puede alinear las listas:
    lo dice en vez de inventar una comparacion."""
    descuenta, prueba = bn.sonda_dos_corridas(
        lambda x: [100.0] if x else [100.0, 50.0], en_camino=80.0)
    assert descuenta is False and "no puede comparar" in prueba


def test_el_detector_de_canal_mira_tokens_y_no_substrings():
    """``echelon_order_up_to`` contiene "on_order" como substring (echel-on_order-_up_to).

    Con el detector viejo ``multi_echelon`` salia acusado de tener un canal que no
    tiene, y el vector congelado quedaba mintiendo. Un falso positivo aca cuesta lo
    mismo que un falso negativo.
    """
    assert not bn.nombra_en_transito("echelon_order_up_to")
    assert not bn.nombra_en_transito("order_up_to")
    assert not bn.nombra_en_transito("reorder_point")
    for campo in ("on_order", "on_order_by_sku", "en_transito", "in_transit", "incoming",
                  "scheduled_receipts", "por_recibir", "oc_pendiente", "open_po_qty"):
        assert bn.nombra_en_transito(campo), campo


def test_un_canal_nuevo_en_un_tool_sin_canal_lo_pasa_a_sin_sonda():
    """El mecanismo que impide que un campo entre callado: la razon escrita a mano deja
    de valer sola en cuanto el contrato del tool nombra un en-transito."""
    sin = bn._sin_canal("x", "jobs.x.Linea", bn.CANTIDAD,
                        campos={"sku", "qty"}, razon="no hay por donde entrarlo")
    con = bn._sin_canal("x", "jobs.x.Linea", bn.CANTIDAD,
                        campos={"sku", "qty", "on_order"}, razon="no hay por donde entrarlo")
    assert sin.estado == "sin_canal" and sin.campo_en_transito is None
    assert con.estado == "sin_sonda" and con.campo_en_transito == "on_order"
    assert "escribir la sonda" in con.prueba


# -- 4. una sola definicion de "netea" --------------------------------------------


def test_t05_consume_las_mismas_sondas_que_el_barrido():
    """El corrector y el barrido no pueden divergir sobre que significa netear.

    T05 sigue puntuando el mejor de estos tres -- su criterio pide ademas una OC por
    proveedor con fechas -- asi que el barrido no movio el puntaje publicado. Lo que
    agrega es lo que T05 nunca iba a ver: los otros once emisores.
    """
    import src.bateria.comprobaciones as c

    t05 = {s.tool for s in bn.sondas_t05()}
    assert t05 == {"excel_replenishment", "odoo_replenishment", "ddmrp"}
    assert t05 <= set(bn.EMISORES)

    candidatos = (c._candidato_excel(), c._candidato_odoo(), c._candidato_ddmrp())
    assert {x.tool for x in candidatos} == t05
    for cand in candidatos:
        assert cand.descuenta_en_transito is True
        assert "->" in cand.prueba_en_transito


# -- 5. el documento dice lo que el barrido mide -----------------------------------


def test_el_documento_publica_la_tabla_que_emite_el_script(sondas):
    texto = DOC.read_text(encoding="utf-8")
    assert markdown(sondas) in texto, (
        "KERN_BARRIDO_NETEO.md quedo desactualizado; regeneralo con "
        "`python scripts/barrido_neteo.py --markdown`"
    )
    assert resumen(sondas) in texto


def test_el_documento_trae_una_linea_de_evidencia_por_emisor(sondas):
    """La evidencia no se congela por valor (un cambio de motor de pronostico moveria
    los numeros sin que haya brecha nueva), pero no puede faltar: cada emisor tiene que
    tener su linea, con su entregable."""
    texto = DOC.read_text(encoding="utf-8")
    for s in sondas:
        assert f"- **`{s.tool}`** ({s.entregable})" in texto, f"falta la evidencia de {s.tool}"
    assert len(evidencia_markdown(sondas).splitlines()) == len(sondas)
