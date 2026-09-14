"""Un tool cita libros y el de al lado no: el cliente no tiene como entender por que.

QUE FIJA ESTE ARCHIVO. ``scm_agent/orchestrator.py`` consulta el indice de lectura SOLO
si el deck del tool sabe recibir pasajes -- se lo pregunta con
``packages.deck_accepts_readings``, introspeccion de firma, hermana de
``deck_accepts_lang``. Mientras ningun deck registrado aceptaba el parametro, el
mecanismo estaba puesto y dormido. Ahora lo aceptan todos, y este archivo es lo unico que
evita que manana nazca el deck 54 mudo: la introspeccion no avisa nada, simplemente deja
de consultar el indice para ese tool y el entregable sale sin lecturas sin un solo error.

POR QUE TODOS Y NO UN SUBCONJUNTO, que es lo contrario del caso ``lang``. Ahi la lista de
decks capaces es un inventario que crece de a uno porque cada ``build_deck`` tiene que
aprender a traducir sus propios rotulos. Una lectura no le pide NADA al ``build_deck``:
el campo ``readings`` ya vive en ``Deliverable`` con su seccion de markdown y su hoja de
xlsx, y el lambda registrado en ``scm_agent/tools.py`` lo enhebra en el ``replace(...)``
que ya hacia para ``options``. No hay nada que un deck tenga que "ganar" para entrar; lo
unico que puede pasar es que alguien escriba el deck nuevo con la firma vieja. Eso es lo
que se falla aca.

DONDE DUELE. El del Orchestrator es el camino SIN GATE -- web, MCP, CLI y eventos:
``_ground`` llama a ``ground_citations`` y nunca pasa por ``filter_citations``. Y el
rotulo ``PDF p.N`` es la pagina FISICA del archivo, que viaja textual hasta el entregable
escrito; un deck mudo no falsea nada, pero un deck que se saltea esta seccion le deja al
cliente la mitad de la procedencia que el resto de los tools si le da.
"""

from __future__ import annotations

import dis
import inspect
from collections.abc import Callable
from pathlib import Path

import pytest

from scm_agent import packages
from scm_agent.package_specs import PACKAGES
from scm_agent.tools import build_default_registry
from src.connectors.lectura import ENV_INTERPRETE
from src.i18n import label

#: Los decks REGISTRADOS que NO pueden recibir lecturas, con el motivo al lado. Vacio, y
#: eso es la afirmacion del archivo, no un detalle: los 53 registrados son lambdas de la
#: misma forma y ninguno tiene una razon de forma para quedarse afuera. Si un deck futuro
#: no pudiera (por ejemplo uno que no componga un ``Deliverable``), su clave va aca CON
#: el motivo escrito -- que es distinto de saltearlo en silencio, que es justo el modo de
#: falla que este archivo existe para impedir.
_DECKS_SIN_LECTURAS: dict[str, str] = {}

#: Piso contra el test vacuo. Si ``build_default_registry()`` devolviera cero tools -- o
#: cero tools con deck -- cada test de inventario de este archivo compararia dos
#: conjuntos vacios y pasaria sin proteger nada. Es un PISO y no una igualdad a
#: proposito: registrar el tool 54 no tiene por que romper este test, escribirlo mudo si.
_DECKS_HOY = 53


def _decks() -> dict[str, Callable]:
    """Los decks registrados, por clave de tool. Los tools sin deck no cuentan."""
    return {t.key: t.deck for t in build_default_registry().list() if t.deck is not None}


@pytest.fixture(autouse=True)
def _sin_indice(monkeypatch):
    """Ningun test de este archivo depende de como esta la maquina que lo corre."""
    monkeypatch.delenv(ENV_INTERPRETE, raising=False)


def test_ningun_deck_registrado_quedo_mudo():
    mudos = {k for k, deck in _decks().items() if not packages.deck_accepts_readings(deck)}

    assert mudos == set(_DECKS_SIN_LECTURAS)


def test_el_inventario_no_puede_pasar_por_vacio():
    """Sin esto, un registry roto que no devuelve nada deja verde al test de arriba."""
    assert len(_decks()) >= _DECKS_HOY


def _usa_el_parametro(deck: Callable, nombre: str) -> bool:
    """Si el deck LEE ``nombre``, o si solo lo acepta y lo tira.

    POR QUE NO ALCANZA CON LA FIRMA. ``deck_accepts_readings`` mira la firma, que es lo
    unico que el Orchestrator puede mirar ANTES de decidir si paga la consulta. Un deck
    que acepta el parametro y no lo usa contesta que si, cobra los 16,8 s y ~1,6 GB del
    arranque del indice, y entrega un entregable sin lecturas igual: es la peor de las
    dos fallas y es justo la que la firma no puede ver.

    POR QUE EL BYTECODE. Los 53 decks son lambdas que reciben el ``report`` de su tool, y
    no hay forma de llamar a las 53 sin fabricar 53 reports. Lo que si se puede leer sin
    llamarlas es lo que hacen: un parametro leido aparece como un ``LOAD_FAST`` con su
    nombre, uno ignorado no aparece en ninguna instruccion. No prueba que el valor llegue
    hasta el archivo -- eso lo prueba la corrida real de
    ``tests/test_orchestrator_lecturas.py`` sobre ``service_scan`` --, pero si distingue
    enhebrado de decorativo, que es lo que se escapaba.
    """
    return any(i.opname.startswith("LOAD_FAST") and i.argval == nombre
               for i in dis.get_instructions(deck))


def test_el_medidor_de_uso_distingue_usar_de_aceptar():
    """Control negativo. Sin esto, ``_usa_el_parametro`` podria contestar que si siempre
    y el test de abajo pasaria sobre 53 decks decorativos."""
    def ignora(report, out_dir, client, citations, confidence, options, readings=()):
        return {}

    def usa(report, out_dir, client, citations, confidence, options, readings=()):
        return {"n": len(readings)}

    assert _usa_el_parametro(ignora, "readings") is False
    assert _usa_el_parametro(usa, "readings") is True


def test_ningun_deck_acepta_las_lecturas_para_tirarlas():
    decorativos = {k for k, deck in _decks().items()
                   if not _usa_el_parametro(deck, "readings")}

    assert decorativos == set(_DECKS_SIN_LECTURAS)


def test_todo_deck_defaultea_a_lecturas_vacias():
    """El default es lo que sostiene a los llamadores que no pasan el parametro.

    Hoy el unico que lo pasa es el Orchestrator; el camino de paquete
    (``scm_agent/packages.py``) llama a los mismos 53 decks sin ``readings`` y tiene que
    seguir funcionando igual. Un default distinto de la tupla vacia -- ``None``, por
    ejemplo -- le pondria a ``replace()`` un valor que ``Deliverable`` no sabe renderizar.
    """
    for key, deck in sorted(_decks().items()):
        param = inspect.signature(deck).parameters.get("readings")
        assert param is not None, key
        assert param.default == (), key


# ---- el llamador que NO pasa lecturas, corrido de verdad ----------------------


class _NoKnowledge:
    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


def test_un_paquete_sigue_escribiendo_sus_decks_sin_pasar_lecturas(tmp_path):
    """El camino de paquete no pasa ``readings`` y no tiene por que enterarse del cambio.

    Es el test del default, pero corrido: la firma sola no prueba que el valor por
    defecto atraviese el ``replace(...)`` sin romper el render. Un paquete escribe los
    decks de varios tools de una, asi que si el enhebrado hubiera roto alguno, esto se
    cae en el archivo, no en una firma.
    """
    from examples.run_package import DEMO_PARAMS, build_demo_intake

    spec = PACKAGES["proyecto_sourcing"]
    intake = build_demo_intake(tmp_path / "intake")

    result = packages.run_package(
        spec, intake, out_dir=tmp_path / "out", params=dict(DEMO_PARAMS),
        knowledge=_NoKnowledge(), clients_root=None,
    )

    assert result.status == "ok", result.summary
    decks_md = [Path(p) for k, p in result.deliverables.items()
                if "_deck_" in k and p.endswith(".md")]
    assert decks_md, result.deliverables
    for deck in decks_md:
        texto = deck.read_text(encoding="utf-8")
        assert label("hdr_readings", "es") not in texto, deck.name
        assert label("hdr_readings", "en") not in texto, deck.name
