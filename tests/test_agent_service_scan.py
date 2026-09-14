"""Pedirle el servicio al agente, en lenguaje natural. El objetivo entero del PR.

Sin este archivo, "pedible en lenguaje natural" es una afirmacion sin verificar: la tool
puede estar registrada, tener job, deck y QA, y no rutearse nunca porque sus keywords
pierden contra otra. Eso es exactamente lo que paso con ``order_lines`` -- un argumento que
existia y que ningun llamador pasaba -- y es el patron que este hilo viene persiguiendo.

Lo que se fija: que un brief que un cliente escribiria de verdad llegue a ``service_scan``,
y que el ``prepare -> run -> qa`` completo produzca servicio observado.
"""

from __future__ import annotations

import pytest

from scm_agent import intent, tools
from src.connectors.simulator import demo_store


class _SinLlm:
    """Sin backend de LLM: obliga a que el ruteo lo resuelvan las keywords y no un modelo.

    Es la condicion mas exigente y la que corre en CI. Si la tool solo se alcanza cuando
    hay un LLM disponible, en una instalacion sin clave es inalcanzable.
    """

    def available(self) -> bool:
        return False


@pytest.fixture(scope="module")
def registro():
    return tools.build_default_registry()


@pytest.mark.parametrize(
    "brief",
    [
        "medime el servicio realizado de mi cuenta",
        "que fill rate y OTIF tuve el mes pasado",
        "quiero un service scan de mi backend",
        "cuanto de lo que me pidieron llegue a entregar",
    ],
)
def test_un_brief_de_cliente_rutea_a_service_scan(registro, brief: str):
    """Los cuatro briefs son como se pide esto de verdad, no como lo nombraria un ingeniero.
    Si alguno deja de rutear, la capacidad existe y nadie la alcanza."""
    elegida = intent.classify(brief, registro, _SinLlm())

    assert elegida.job_type == "service_scan", (
        f"{brief!r} ruteo a {elegida.job_type!r} (candidatos: {elegida.candidates})"
    )


def test_el_pipeline_completo_produce_servicio_observado(registro):
    """prepare -> run -> qa, por el camino del agente y no llamando al job directo."""
    from nosilo_core.registry import JobRequest

    tool = registro.get("service_scan")
    preparado = tool.prepare(
        JobRequest(brief="medime el servicio realizado", params={"source": demo_store()}),
        None,
    )
    assert preparado.status == "ok", preparado.messages

    producido = tool.run(preparado.payload, {"source": demo_store()})
    problemas = tool.qa(producido.report)

    assert problemas == [], problemas
    assert producido.report.records
    assert all(r.provenance == "observed" for r in producido.report.records)


def test_la_tool_no_pide_archivo(registro):
    """Lee una CUENTA. Marcarla ``requires_data=True`` la haria pedir un CSV que no existe,
    y el orquestador cortaria con ``needs_data`` antes de intentar conectarse."""
    assert registro.get("service_scan").requires_data is False


def test_el_resumen_del_agente_dice_de_que_backend_y_con_cuanta_evidencia(registro):
    """El resumen es lo que el operador lee antes de abrir nada. Un fill rate sin decir de
    que cuenta salio ni sobre cuantos pedidos no se puede reconciliar."""
    from nosilo_core.registry import JobRequest

    tool = registro.get("service_scan")
    preparado = tool.prepare(
        JobRequest(brief="servicio realizado", params={"source": demo_store()}), None
    )
    producido = tool.run(preparado.payload, {"source": demo_store()})

    assert "orders" in producido.summary.lower()
    assert "fill rate" in producido.summary.lower()


def test_sin_credenciales_el_agente_avisa_que_son_datos_de_demostracion(registro, monkeypatch):
    """El peor entregable posible es una demo que se lee como una cuenta real. Si alguien
    corre la tool sin conectar nada, el resumen tiene que decirlo -- no quedar callado y
    entregar numeros del simulador con cara de medicion."""
    from nosilo_core.registry import JobRequest

    for var in ("UNLEASHED_API_ID", "UNLEASHED_API_KEY", "CIN7_ACCOUNT_ID",
                "CIN7_APPLICATION_KEY"):
        monkeypatch.delenv(var, raising=False)

    tool = registro.get("service_scan")
    preparado = tool.prepare(JobRequest(brief="servicio realizado", params={}), None)
    producido = tool.run(preparado.payload, {})

    assert "demostraci" in producido.summary.lower() or "simulad" in producido.summary.lower()
