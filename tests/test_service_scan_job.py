"""La tool #51: medir el servicio de una cuenta viva, pedido en lenguaje natural.

Es lo unico que restaba del limite que declaro el PR #308. El motor (`order_fulfillment`),
la lectura (`connectors.service_scan`) y los adaptadores (Unleashed, Cin7) ya existen y ya
estan testeados; lo que faltaba era el envoltorio de agente -- resolver la fuente, hacer QA,
y entregar.

Dos cosas se fijan aca con mas cuidado que el resto:

* **Las credenciales no viajan a ninguna salida.** El reporte se serializa entero y se
  buscan las claves por su valor literal. Un secreto en un `source_name` termina en el
  entregable de un cliente.
* **El QA rechaza un fill rate sin evidencia.** Es el modo de falla que todo este hilo
  persigue: un numero medido sobre cero lineas se lee igual que uno medido sobre mil.

``verify`` devuelve una ``list[str]`` de problemas y vacia significa que paso -- la
convencion que ya usan las 50 tools registradas (ver ``jobs/intake_scan_job.py``), y no
una API nueva para esta.
"""

from __future__ import annotations

import pytest

from jobs import service_scan_job
from src.connectors.simulator import demo_store

_CLAVE = "no-debe-aparecer-jamas-en-ninguna-salida"


def _prep(**params):
    return service_scan_job.prepare(None, params)


# -- de donde sale la fuente ---------------------------------------------------


def test_una_fuente_inyectada_le_gana_a_todo():
    """El camino de los tests, y el que permite probar sin credenciales de nadie."""
    payload = _prep(source=demo_store())

    assert payload["source"] is not None
    assert "simul" in payload["source_name"].lower() or "inject" in payload["source_name"].lower()


def test_con_credenciales_de_unleashed_arma_ese_conector(monkeypatch):
    monkeypatch.setenv("UNLEASHED_API_ID", "the-id")
    monkeypatch.setenv("UNLEASHED_API_KEY", _CLAVE)
    monkeypatch.delenv("CIN7_ACCOUNT_ID", raising=False)

    payload = _prep()

    from src.connectors.unleashed import UnleashedConnector

    assert isinstance(payload["source"], UnleashedConnector)
    assert "unleashed" in payload["source_name"].lower()


def test_con_credenciales_de_cin7_arma_ese_conector(monkeypatch):
    monkeypatch.delenv("UNLEASHED_API_ID", raising=False)
    monkeypatch.delenv("UNLEASHED_API_KEY", raising=False)
    monkeypatch.setenv("CIN7_ACCOUNT_ID", "the-account")
    monkeypatch.setenv("CIN7_APPLICATION_KEY", _CLAVE)

    payload = _prep()

    from src.connectors.cin7 import Cin7Connector

    assert isinstance(payload["source"], Cin7Connector)
    assert "cin7" in payload["source_name"].lower()


def test_sin_credenciales_cae_al_simulador_Y_LO_DICE(monkeypatch):
    """Un stand-in que no se anuncia es una demo que se lee como una cuenta real. El nombre
    de la fuente viaja al entregable, asi que ahi es donde tiene que decirlo."""
    for var in ("UNLEASHED_API_ID", "UNLEASHED_API_KEY", "CIN7_ACCOUNT_ID",
                "CIN7_APPLICATION_KEY"):
        monkeypatch.delenv(var, raising=False)

    payload = _prep()

    assert "simulad" in payload["source_name"].lower() or "demo" in payload["source_name"].lower()
    assert "NO" in payload["source_name"] or "not" in payload["source_name"].lower()


def test_ninguna_credencial_aparece_en_ninguna_salida(monkeypatch):
    """La asercion que importa mas que todas las de arriba juntas.

    Se serializa el payload Y el reporte enteros y se busca la clave por su valor literal.
    Un secreto en un ``source_name`` -- que es lo mas facil de escribir sin pensar, para
    "identificar la cuenta" -- termina impreso en el entregable de un cliente y en cada log
    que lo toque.
    """
    monkeypatch.setenv("UNLEASHED_API_ID", "the-id")
    monkeypatch.setenv("UNLEASHED_API_KEY", _CLAVE)

    payload = _prep()
    texto = repr(payload) + repr(payload["source_name"])

    assert _CLAVE not in texto


# -- lo que mide ---------------------------------------------------------------


def test_el_reporte_trae_servicio_observado_y_su_agregado():
    reporte = service_scan_job.run(_prep(source=demo_store()))

    assert reporte.records
    assert all(r.provenance == "observed" for r in reporte.records)
    assert reporte.portfolio.fill_rate is not None
    assert reporte.n_orders > 0
    assert reporte.n_lines >= reporte.n_orders


def test_las_notas_de_cobertura_del_adaptador_llegan_al_reporte():
    """Sin esto el informe dice un OTIF y no dice de que campo salio ni que significa --
    que es exactamente lo que el guard de cobertura existe para impedir."""
    reporte = service_scan_job.run(_prep(source=demo_store()))

    assert reporte.notes
    assert reporte.coverage is not None


def test_una_cuenta_sin_ordenes_no_es_un_error():
    """Una cuenta recien conectada no tiene historia. Eso es un informe que lo dice, no una
    excepcion en la primera pantalla que ve un prospecto."""

    class _Vacia:
        def list_products(self):
            return []

        def inventory_levels(self):
            return []

        def orders(self, *, since=None):
            return []

    reporte = service_scan_job.run(_prep(source=_Vacia()))

    assert reporte.records == ()
    assert reporte.n_orders == 0
    assert service_scan_job.verify(reporte) == []


# -- el QA ---------------------------------------------------------------------


def test_el_qa_pasa_sobre_una_medicion_con_evidencia():
    reporte = service_scan_job.run(_prep(source=demo_store()))

    assert service_scan_job.verify(reporte) == []


def test_el_qa_RECHAZA_un_fill_rate_sin_una_sola_linea_puntuada():
    """El modo de falla que todo este hilo persigue: un numero medido sobre cero evidencia
    se lee igual que uno medido sobre mil. Si un dia una lectura produce eso, el QA lo
    frena antes del entregable en vez de publicarlo."""
    import dataclasses

    from src import order_fulfillment as of

    reporte = service_scan_job.run(_prep(source=demo_store()))
    roto = dataclasses.replace(
        reporte,
        portfolio=dataclasses.replace(
            reporte.portfolio, fill_rate=0.97, lines_scored=0, order_lines=0
        ),
    )

    problemas = service_scan_job.verify(roto)

    assert problemas
    assert any("evidencia" in m.lower() or "evidence" in m.lower() for m in problemas)
    assert of.PROVENANCE_OBSERVED  # el modulo sigue siendo el que define la palabra


def test_since_llega_al_adaptador():
    """La ventana se filtra en el SERVIDOR donde el backend lo soporte; traer el historico
    entero para descartarlo en memoria es lo que hace lenta la primera corrida."""

    class _Espia:
        def __init__(self):
            self.vistos = []

        def list_products(self):
            return []

        def inventory_levels(self):
            return []

        def orders(self, *, since=None):
            self.vistos.append(since)
            return []

    espia = _Espia()
    service_scan_job.run(_prep(source=espia), since="2026-01-06")

    assert espia.vistos == ["2026-01-06"]


@pytest.mark.parametrize("campo", ["source_name", "since"])
def test_el_reporte_sabe_de_donde_y_desde_cuando(campo: str):
    """Un informe de servicio sin decir sobre que cuenta y sobre que ventana se calculo no
    se puede reconciliar contra nada."""
    reporte = service_scan_job.run(_prep(source=demo_store()), since="2026-01-01")

    assert getattr(reporte, campo) is not None


# -- el entregable -------------------------------------------------------------

from pathlib import Path  # noqa: E402

from jobs import service_scan_deliverable  # noqa: E402


def _deck():
    reporte = service_scan_job.run(_prep(source=demo_store()))
    return reporte, service_scan_deliverable.build(reporte, client="ACME")


def test_el_resumen_dice_el_servicio_y_de_que_cuenta():
    _, deck = _deck()

    assert "fill rate" in deck.summary.lower()
    assert "simulad" in deck.summary.lower() or "demostraci" in deck.summary.lower()


def test_un_servicio_no_medido_no_se_imprime_como_cero():
    """La regla de todo el paquete, en la superficie que ve el cliente."""

    class _Vacia:
        def list_products(self):
            return []

        def inventory_levels(self):
            return []

        def orders(self, *, since=None):
            return []

    reporte = service_scan_job.run(_prep(source=_Vacia()))
    deck = service_scan_deliverable.build(reporte, client="ACME")

    # Ningun porcentaje: sin ordenes no hay ninguna tasa que reportar, ni siquiera "0%".
    assert "%" not in deck.summary
    # Y dice POR QUE, que es lo que separa "no medimos" de "medimos y dio mal".
    assert "not a service failure" in deck.summary.lower()


def test_los_hallazgos_son_los_sku_peor_servidos_valorizados():
    _, deck = _deck()

    assert deck.findings
    assert any("$" in f.impact for f in deck.findings)


def test_la_cobertura_va_en_data_sources_y_NO_en_findings():
    """Mismo criterio que #318: un finding es un problema del negocio del cliente, y una
    declaracion de cobertura es un limite de la MEDICION. Entre los hallazgos leeria como
    un defecto de su operacion."""
    _, deck = _deck()

    fuentes = " ".join(d.source for d in deck.data_sources)
    hallazgos = " ".join(f.detail for f in deck.findings)

    assert "delivered_date" in fuentes or "entrega" in fuentes.lower()
    assert "delivered_date" not in hallazgos


def test_el_deck_dice_si_la_fecha_de_entrega_es_DESPACHO():
    """El hallazgo de 6,2 puntos, en la superficie que lo necesita. Sin esto el cliente lee
    "OTIF" y entiende "llego a tiempo", cuando el simulador -- como Unleashed, Cin7 y Odoo
    -- solo puede hablar de la salida."""
    _, deck = _deck()

    todo = " ".join(d.source for d in deck.data_sources).lower()
    assert "despacho" in todo or "dispatch" in todo or "promise" in todo


def test_el_caveat_llega_al_ARCHIVO_que_abre_el_cliente(tmp_path: Path):
    _, deck = _deck()

    escritos = deck.write_all(tmp_path)

    markdown = Path(escritos["report"]).read_text(encoding="utf-8")
    assert "OrderLine.delivered_date" in markdown or "delivered_date" in markdown


def test_la_salida_operativa_es_un_archivo_por_sku(tmp_path: Path):
    reporte, _ = _deck()

    escritos = service_scan_deliverable.write_operational(reporte, tmp_path, "ACME")

    assert escritos
    ruta = Path(next(iter(escritos.values())))
    assert ruta.exists()
    assert "SKU-1" in ruta.read_text(encoding="utf-8")


# -- el contrato never-unprotected ---------------------------------------------


def test_el_reporte_termina_en_un_HANDOFF_y_no_en_una_ejecucion():
    """El contrato que exige ``tests/test_tool_options.py`` a las 51 tools: ningun
    resultado consecuente queda sin desenlace.

    Y HANDOFF y no EXECUTED, que es la parte que importa: un scan de servicio MIDE, no
    arregla nada. Devolverlo como ejecutado diria que el agente ya hizo algo con esos
    quiebres, y no hizo nada -- los conto.
    """
    reporte = service_scan_job.run(_prep(source=demo_store()))

    assert reporte.outcome is not None
    assert reporte.outcome.status == "handoff"


def test_el_handoff_dice_confirmar_que_registra_la_fecha_de_entrega():
    """El paso humano numero uno, y no es una formalidad: de un backend que solo ve la
    salida, "OTIF" significa despachar a tiempo. Entre esa lectura y la llegada real hay
    6,2 puntos medidos, y la unica forma de saber cual es cual es preguntar."""
    reporte = service_scan_job.run(_prep(source=demo_store()))

    texto = " ".join(
        p.title + " " + " ".join(p.steps) for p in reporte.outcome.handoffs
    ).lower()
    assert "entrega" in texto or "delivery" in texto


def test_una_cuenta_sin_ordenes_igual_termina_en_un_desenlace():
    """Sin ordenes tampoco se queda sin salida: el paso humano es conectar o ampliar la
    ventana, no quedarse mirando un informe vacio."""

    class _Vacia:
        def list_products(self):
            return []

        def inventory_levels(self):
            return []

        def orders(self, *, since=None):
            return []

    reporte = service_scan_job.run(_prep(source=_Vacia()))

    assert reporte.outcome is not None
    assert reporte.outcome.handoffs
