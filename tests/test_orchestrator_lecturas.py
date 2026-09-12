"""El camino del Orchestrator consulta el indice de lectura. Es el camino SIN GATE.

QUE SE FIJA ACA, y por que este camino no es el del paquete.

``scm_agent/packages.py`` ya cablea las lecturas al deck consolidado del paquete, y
aquel camino tiene un gate al lado (``citation_gate.filter_citations``) y un operador
que arma el paquete. Este NO: ``Orchestrator._ground`` llama
``knowledge.ground_citations(..., limit=5)`` y nunca pasa por ``filter_citations``, y es
el camino que usan la web, el MCP, la CLI y los eventos. Lo que salga mal aca llega al
cliente y no hay nada rio abajo que lo pare. Ademas ``_ground`` corre DESPUES de
``tool.qa()``, asi que una lectura nunca puede bloquear un entregable: el modo de falla
no es "se cae el job", es "se embarca con algo malo". De ahi que casi todo lo que se
afirma abajo sea una forma de "no cambia nada" y no una funcionalidad nueva.

1. **INERTE sin indice**, que es SIEMPRE en produccion (la VM tiene 512 MB, el indice
   pide ~1,6 GB, y sin ``KERN_LECTURA_PYTHON`` el conector contesta ``available=False``
   sin intentar levantar nada). El JobResult y los archivos escritos salen exactamente
   como hoy, byte a byte.
2. **Inerte HOY tambien con indice**, porque el cableado le PREGUNTA al deck si sabe
   recibir lecturas y ningun deck registrado sabe todavia. La constante de este archivo
   que lo fija es ``_DECKS_CON_LECTURAS``, que hoy es el conjunto vacio; el dia que un
   deck gane el parametro, este test lo dice.
3. **UNA sola consulta por corrida**, con las palabras canonicas del tool, y los dos
   topes (cuantas palabras salen, cuantos pasajes entran) puestos de este lado.
4. **La guarda va ANTES de escribir nada.** ``read_passages`` promete no levantar pero
   su guarda es ``except Exception`` y anyio levanta ``BaseExceptionGroup``, que esa
   clausula no ve. Corriendo antes de ``tool.deliver`` lo que se escape no puede dejar
   una carpeta de resultados a medias.
5. **No toca las citas ni el prompt del modelo.** Un pasaje no tiene ``node_id``; y el
   texto del pasaje no entra a ``narrative_rewrite``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nosilo_core import llm
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from scm_agent import packages
from scm_agent.knowledge import KnowledgeBase
from scm_agent.orchestrator import Orchestrator
from scm_agent.tools import build_default_registry
from src.connectors import lectura
from src.connectors.lectura import ENV_INTERPRETE, Reading, ReadingLookup

#: Los decks REGISTRADOS que hoy saben recibir lecturas. Inventario, no contrato: es el
#: gemelo de ``_EXPECTED_LANG_AWARE`` en
#: ``tests/test_package_threads_lang_to_tool_decks.py``. Vacio a proposito -- este commit
#: deja el cableado puesto y dormido; el dia que un ``build_deck`` gane ``readings``,
#: este test avisa que ya quedo enchufado.
_DECKS_CON_LECTURAS: set[str] = set()

_LECTURAS = (
    Reading(source="vandeput-inventory-optimization",
            page_label="PDF p.83-84",
            excerpt="Safety stock sizing depends on the risk period."),
    Reading(source="liu-supply-chain-analytics",
            page_label="PDF p.12",
            excerpt="Analytics without a decision attached is reporting."),
)

#: Que el deck NO recibio el parametro, distinto de recibirlo vacio.
_NO_PASADO = object()


class _NoKnowledge:
    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


def _disponible(keywords, **kwargs) -> ReadingLookup:
    return ReadingLookup(available=True, readings=_LECTURAS, note="2 pasaje(s)")


def _no_disponible(keywords, **kwargs) -> ReadingLookup:
    return ReadingLookup(available=False, readings=(), note="no esta el indice")


@pytest.fixture(autouse=True)
def _sin_indice_por_defecto(monkeypatch):
    """Ningun test de este archivo depende de como esta la maquina que lo corre."""
    monkeypatch.delenv(ENV_INTERPRETE, raising=False)


def _deck_que_acepta_lecturas(registro: list):
    """Un deck del futuro: el molde de ``scm_agent/tools.py`` mas ``readings``."""
    def deck(report, out_dir, client, citations, confidence, options, readings=_NO_PASADO):
        registro.append({"citations": tuple(citations), "readings": readings})
        return {}
    return deck


def _deck_de_hoy(registro: list):
    """El molde EXACTO de los 53 registros de hoy: sin ``readings``."""
    def deck(report, out_dir, client, citations, confidence, options):
        registro.append({"citations": tuple(citations)})
        return {}
    return deck


def _escribe_un_archivo(report, out_dir, client) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    destino = Path(out_dir) / "informe.md"
    destino.write_text("entregable", encoding="utf-8")
    return {"report": destino}


def _tool(key: str = "uno", *keywords: str, deck=None, deliver=None) -> Tool:
    return Tool(
        key=key, title=key, description="test double",
        intent_keywords=keywords or ("safety stock",),
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report={"key": key}, summary=f"{key} ok, 7 skus"),
        qa=lambda report: [],
        deliver=deliver or (lambda report, out_dir, client: {}),
        deck=deck,
    )


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _orq(reg: ToolRegistry, *, knowledge=None, provider=None) -> Orchestrator:
    return Orchestrator(
        registry=reg, provider=provider or llm.RulesFallback(),
        knowledge=knowledge or _NoKnowledge(), clients_root=None,
    )


def _correr(reg: ToolRegistry, out: Path, *, key: str = "uno", knowledge=None, provider=None):
    return _orq(reg, knowledge=knowledge, provider=provider).run(
        "safety stock para el catalogo", job_type=key, out_dir=out, client="Client",
    )


# --- 1. la introspeccion: se le pregunta al deck, no a un flag ------------------------


def test_le_pregunta_al_deck_y_no_a_un_flag():
    acepta = packages.deck_accepts_readings

    assert acepta(lambda report, out_dir, client, citations, confidence, options: {}) is False
    assert acepta(
        lambda report, out_dir, client, citations, confidence, options, readings=(): {}
    ) is True


def test_un_tool_sin_deck_no_se_pregunta_por_lecturas():
    assert packages.deck_accepts_readings(None) is False


def test_la_generalizacion_no_confunde_un_parametro_con_el_otro():
    """Las dos preguntas comparten mecanismo, no respuesta."""
    def solo_lang(report, out_dir, client, citations, confidence, options, lang="en"):
        return {}

    def solo_readings(report, out_dir, client, citations, confidence, options, readings=()):
        return {}

    assert packages.deck_accepts_lang(solo_lang) is True
    assert packages.deck_accepts_readings(solo_lang) is False
    assert packages.deck_accepts_lang(solo_readings) is False
    assert packages.deck_accepts_readings(solo_readings) is True


def test_los_decks_con_lecturas_son_los_que_creemos_que_son():
    cableados = {
        tool.key for tool in build_default_registry().list()
        if packages.deck_accepts_readings(tool.deck)
    }

    assert cableados == _DECKS_CON_LECTURAS


# --- 2. inerte: en produccion no pasa absolutamente nada ------------------------------


def test_con_el_registry_real_el_indice_ni_se_consulta_y_el_entregable_no_se_mueve(
    tmp_path, monkeypatch,
):
    """El caso de PRODUCCION, comparado contra el comportamiento de HOY.

    Se corre un tool REAL del registry dos veces -- con el indice arriba y con el indice
    abajo -- y se comparan el JobResult y los BYTES de cada archivo escrito. Ningun deck
    registrado acepta lecturas todavia, asi que ni siquiera se consulta: el costo (16,8 s
    y ~1,6 GB por arranque) no se paga por un resultado que nadie iba a renderizar.
    """
    llamadas: list[tuple] = []

    def _espia(keywords, **kwargs):
        llamadas.append((tuple(keywords), kwargs))
        return _disponible(keywords, **kwargs)

    reg = build_default_registry()
    brief = "evaluate our SC leadership"
    params = {"overrides": {"scores": "3 2 3 1 1", "name": "T"}}

    monkeypatch.setattr(lectura, "read_passages", _espia)
    con = _orq(reg).run(brief, out_dir=tmp_path / "con", **params)
    monkeypatch.setattr(lectura, "read_passages", _no_disponible)
    sin = _orq(reg).run(brief, out_dir=tmp_path / "sin", **params)

    assert con.status == sin.status == "ok"
    assert llamadas == []                      # no se consulto NADA
    assert con.summary == sin.summary
    assert con.citations == sin.citations
    assert sorted(con.deliverables) == sorted(sin.deliverables)
    for nombre, ruta in con.deliverables.items():
        otro = Path(sin.deliverables[nombre])
        assert Path(ruta).read_bytes() == otro.read_bytes(), nombre


def test_sin_indice_el_deck_se_llama_exactamente_como_hoy(tmp_path, monkeypatch):
    """Ni un kwarg de mas: el deck recibe la MISMA llamada que recibia antes."""
    monkeypatch.setattr(lectura, "read_passages", _no_disponible)
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro)))

    _correr(reg, tmp_path)

    assert registro and registro[0]["readings"] is _NO_PASADO


def test_un_indice_disponible_pero_vacio_tambien_es_inerte(tmp_path, monkeypatch):
    """``available=True`` con cero pasajes no puede abrir una seccion vacia."""
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: ReadingLookup(True, (), "cero"))
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro)))

    _correr(reg, tmp_path)

    assert registro and registro[0]["readings"] is _NO_PASADO


def test_un_deck_que_no_sabe_de_lecturas_no_hace_consultar_el_indice(tmp_path, monkeypatch):
    """La condicion de costo: sin consumidor no se levanta un servidor de 1,6 GB."""
    llamadas: list[int] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(1) or _disponible(keywords)))
    reg = _registry(_tool("uno", "safety stock", deck=_deck_de_hoy([])))

    _correr(reg, tmp_path)

    assert llamadas == []


def test_sin_palabras_clave_no_se_consulta_nada(tmp_path, monkeypatch):
    llamadas: list[int] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(1) or _disponible(keywords)))
    registro: list = []
    vacio = Tool(
        key="uno", title="uno", description="sin keywords", intent_keywords=(),
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report={}, summary="uno ok"),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
        deck=_deck_que_acepta_lecturas(registro),
    )

    _correr(_registry(vacio), tmp_path)

    assert llamadas == []
    assert registro and registro[0]["readings"] is _NO_PASADO


# --- 3. una sola consulta, y los dos topes de este lado -------------------------------


def test_una_sola_consulta_por_corrida(tmp_path, monkeypatch):
    """Los dos numeros que viajan en la consulta van LITERALES, por el mismo motivo que
    el 3 de ``test_manda_sus_canonicas_y_no_diez_sinonimos`` y que los dos cupos del
    archivo hermano (ver ``scm_agent/packages.py``, "escritos literales en ese test a
    proposito"): escritos contra la constante los asserts son tautologias y la constante
    se puede mover a cualquier valor sin romper nada. En el camino SIN GATE el precio del
    drift es concreto -- ``k`` es lo unico que evita que el deck del cliente salga con
    cincuenta extractos de PDF, y ``timeout_s`` lo unico que acota cuanto se cuelga un
    request de la web esperando un indice que no contesta."""
    llamadas: list[tuple] = []

    def _espia(keywords, **kwargs):
        llamadas.append((tuple(keywords), kwargs))
        return _disponible(keywords, **kwargs)

    monkeypatch.setattr(lectura, "read_passages", _espia)
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro)))

    _correr(reg, tmp_path)

    assert len(llamadas) == 1
    _, kwargs = llamadas[0]
    assert kwargs["k"] == 3                                # el tope, literal
    assert kwargs["timeout_s"] == 20.0                     # el timeout, literal
    assert registro[0]["readings"] == _LECTURAS


def test_manda_sus_canonicas_y_no_diez_sinonimos(tmp_path, monkeypatch):
    """El tope POR consulta, medido contra el indice vivo en ``scm_agent/packages.py``:
    con doce variantes del mismo tema servia 1 de 4 pasajes; con las tres canonicas, 4
    de 4. Un tool solo es exactamente el caso donde se paga entera la diferencia -- y
    ``intent_keywords`` es justo eso, una lista de sinonimos de intencion ordenada de
    mas a menos canonica. El 3 va LITERAL: escrito contra la constante, el test se
    moveria con ella y no podria fallar."""
    llamadas: list[tuple] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(tuple(keywords))
                                                or _disponible(keywords)))
    registro: list = []
    reg = _registry(_tool("uno", *[f"palabra {i}" for i in range(10)],
                          deck=_deck_que_acepta_lecturas(registro)))

    _correr(reg, tmp_path)

    assert llamadas == [("palabra 0", "palabra 1", "palabra 2")]


def test_el_tope_de_cantidad_lo_pone_kern_y_no_el_otro_repo(tmp_path, monkeypatch):
    """``k`` viaja como PEDIDO y nadie garantiza que se respete. Mismo argumento que el
    tope de extracto (320) y el del slug: es texto ajeno y el render no tiene tope
    propio.

    La entrada y la salida van las dos LITERALES. Construida como
    ``_LECTURAS_POR_DECK + 6`` la entrada se mueve con la constante y el test no puede
    fallar: subir el tope a 50 hace que entren 56 y salgan 56, y pasa igual."""
    de_mas = tuple(
        Reading(f"libro-{i}", f"PDF p.{i}", "texto")
        for i in range(9)                                  # nueve fijos, no "el tope + 6"
    )
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: ReadingLookup(True, de_mas, ""))
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro)))

    _correr(reg, tmp_path)

    assert len(registro[0]["readings"]) == 3                # el tope, literal


# --- 4. la guarda, y el lugar donde va -----------------------------------------------


def test_ni_un_grupo_de_excepciones_tira_la_corrida(tmp_path, monkeypatch):
    """``read_passages`` promete no levantar, pero su guarda es ``except Exception`` y
    anyio levanta ``BaseExceptionGroup``, que esa clausula NO ve. El invariante lo
    sostiene este lado."""
    def _grupo(*a, **kw):
        raise BaseExceptionGroup("unhandled errors in a TaskGroup", [OSError("boom")])

    monkeypatch.setattr(lectura, "read_passages", _grupo)
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro),
                          deliver=_escribe_un_archivo))

    res = _correr(reg, tmp_path)

    assert res.status == "ok"
    assert registro and registro[0]["readings"] is _NO_PASADO
    assert res.deliverables                      # el entregable salio igual


def test_un_ctrl_c_corta_la_corrida_y_no_deja_archivos_a_medias(tmp_path, monkeypatch):
    """POR QUE LA CONSULTA VA ANTES DE ``tool.deliver``.

    La guarda no puede tragarse un Ctrl-C, asi que hay una excepcion que SI sale de la
    consulta. Corriendo antes de escribir, lo que sale no deja nada en la carpeta; si la
    consulta viviera despues de ``deliver`` (donde vive en el camino de paquete, que
    escribe todo junto al final), ese mismo Ctrl-C dejaria los archivos puestos y ningun
    JobResult devuelto."""
    def _interrumpido(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(lectura, "read_passages", _interrumpido)
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro),
                          deliver=_escribe_un_archivo))

    with pytest.raises(KeyboardInterrupt):
        _correr(reg, tmp_path)

    assert list(tmp_path.rglob("*.md")) == []
    assert registro == []


# --- 5. no toca las citas, y no entra al prompt del modelo ---------------------------


@pytest.fixture(scope="module")
def _kb() -> KnowledgeBase:
    return KnowledgeBase()


def test_las_citations_son_identicas_con_y_sin_lecturas(tmp_path, monkeypatch, _kb):
    """La garantia de la que cuelga el producto entero: un pasaje NO es una cita.

    Contra el grafo L3 committeado, corrido dos veces. Este camino no pasa por
    ``filter_citations``, asi que si las lecturas se filtraran al pool de citas no habria
    nada rio abajo que las sacara."""
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", "inventory", "abc analysis",
                          deck=_deck_que_acepta_lecturas(registro)))

    monkeypatch.setattr(lectura, "read_passages", _disponible)
    con = _correr(reg, tmp_path / "con", knowledge=_kb)
    monkeypatch.setattr(lectura, "read_passages", _no_disponible)
    sin = _correr(reg, tmp_path / "sin", knowledge=_kb)

    assert con.status == sin.status == "ok"
    assert con.citations          # el test no puede pasar por vacio
    assert con.citations == sin.citations
    assert registro[0]["readings"] == _LECTURAS      # y las lecturas SI llegaron
    assert registro[0]["citations"] == registro[1]["citations"]
    # Y ningun pasaje se colo al pool. Se busca la HUELLA del pasaje -- la pagina fisica
    # y el texto del extracto -- y no el nombre del libro: ese libro es ademas una fuente
    # legitima del grafo L3, asi que su slug aparece en las citas con todo derecho. Lo que
    # no puede aparecer es la pagina del PDF ni el fragmento copiado.
    assert not [c for c in con.citations if "PDF p." in c or "risk period" in c]


class _ProveedorEspia:
    """Un proveedor que anota lo que se le manda al modelo."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def available(self) -> bool:
        return True

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "uno ok, 7 skus"

    def extract(self, prompt: str, schema: dict) -> dict:
        self.prompts.append(prompt)
        return {}


def test_las_lecturas_no_entran_al_prompt_del_modelo(tmp_path, monkeypatch):
    """DECISION, no olvido: el extracto del libro NO se le pasa a ``narrative_rewrite``.

    Tres razones, y las tres apuntan al mismo lado. (a) El resumen es el campo que el
    cliente lee primero y hoy no depende de si el indice esta arriba; metiendo pasajes al
    prompt, la misma corrida diria una cosa en una maquina con indice y otra en
    produccion, que es justo la inercia que el resto de este archivo defiende. (b) Es
    texto ajeno que vuelve de otro proceso: concatenado crudo al prompt (asi trata
    ``narrative_rewrite`` a las citas) es superficie de inyeccion en el camino SIN gate.
    (c) El pasaje vale por su procedencia -- libro y pagina fisica --, y parafraseado
    dentro de una frase de resumen pierde exactamente eso y queda como afirmacion
    nuestra."""
    monkeypatch.setattr(lectura, "read_passages", _disponible)
    espia = _ProveedorEspia()
    registro: list = []
    reg = _registry(_tool("uno", "safety stock", deck=_deck_que_acepta_lecturas(registro)))

    res = _correr(reg, tmp_path, provider=espia)

    assert espia.prompts                                   # el modelo SI se llamo
    assert registro[0]["readings"] == _LECTURAS            # y las lecturas SI existieron
    for prompt in espia.prompts:
        assert "risk period" not in prompt
        assert "vandeput-inventory-optimization" not in prompt
        assert "PDF p.83-84" not in prompt
    assert res.status == "ok"
