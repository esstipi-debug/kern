"""La fuente #30 (control documental y trabajo estandar) SI sale como cita.

Es la inversa de tests/test_launch_gaps_graph.py. Las fuentes #26, #28 y #29 son
documentos internos de Kern y su test adversarial verifica que NUNCA lleguen a una
cita. Esta fuente existe para lo contrario: `procedure_draft` (tool #53) mostraba
cero citas porque el corpus no tenia una sola linea de teoria de control documental,
y `scm_agent/packages.py:100-104` dice que eso se arregla ingiriendo fuentes, no
aflojando un ancla. Si estos nodos no se citan, la fuente no sirvio para nada.

Lo que se fija, y por que cada cosa:

* los anclajes EXISTEN y son anteriores a esta fuente -- no se invento ningun nodo;
* los anclajes SOBREVIVEN un rebuild de libros: ninguno esta en
  `merge_l3_rebuild.REPLACE_STEMS`, o el rebuild borraria el nodo ancla y con el la
  arista, dejando un puente que se corta solo;
* NINGUN nodo nuevo supera `MAX_DEGREE`. Un nodo de alto grado es un iman en el
  cierre de 2 saltos: es el mecanismo exacto por el que las fuentes #26 y #27
  dejaron en cero las citas de tools ajenas, una detectada solo por revision
  adversarial;
* NO hay hub. Un hub que toque los doce conceptos deja cualquier par a 2 saltos
  (`MAX_HOPS`), que es la self-validation loophole que `EXCLUDED_CONCEPTS` existe
  para tapar. Es la unica diferencia estructural deliberada contra
  `merge_capacity_gaps.py`, y sin este test se pierde en la proxima copia;
* cada nodo declara su PROPIA procedencia, y ningun `source_file` nombra una obra
  que nadie leyo. El cliente ve `label - source_file source_location` y nada mas,
  asi que la honestidad tiene que vivir ahi adentro: la pagina de producto del
  CQI-8 no es el CQI-8;
* las contradicciones se preservan como nodos, no se aplanan a un numero inventado.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent import citation_gate
from scm_agent.knowledge import _INTERNAL_SOURCES, KnowledgeBase
from scm_agent.tools import build_default_registry
from scripts.merge_l3_rebuild import REPLACE_STEMS
from scripts.merge_sop_document_control import (
    COMMUNITY,
    FORBIDDEN_SOURCE_FILES,
    MAX_DEGREE,
    SOURCE_FILES,
    build_payload,
)

RAIZ = Path(__file__).resolve().parents[1]
GRAFO = RAIZ / "knowledge" / "scm-books" / "graph.json"
PAPEL_DE_TRABAJO = RAIZ / "documentation" / "SOP_DOCUMENT_CONTROL_SOURCES.md"


@pytest.fixture(scope="module")
def grafo() -> dict:
    return json.loads(GRAFO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase()


@pytest.fixture(scope="module")
def nodos(grafo: dict) -> list[dict]:
    salida = [n for n in grafo["nodes"] if n.get("source_file") in SOURCE_FILES]
    assert salida, "la fuente #30 no aporto nodos; corriste scripts/merge_sop_document_control.py?"
    return salida


@pytest.fixture(scope="module")
def ids(nodos: list[dict]) -> set[str]:
    return {n["id"] for n in nodos}


# --- 1. la fuente cumple su unico proposito ----------------------------------


def test_procedure_draft_cita_la_teoria_ingerida(kb: KnowledgeBase, ids: set[str]) -> None:
    """EL test de esta tanda. Sin esto, la fuente es peso muerto en el grafo.

    `procedure_draft` mostraba cero citas -- no una degradada por el piso
    MIN_CITATIONS, sino cero candidatos resueltos en radio. Se fija que al menos
    una cita salga de ESTA fuente, por node_id, no por texto: un assert de
    substring pasaria con un nodo viejo que casualmente comparta palabras.
    """
    reg = build_default_registry()
    tool = reg.get("procedure_draft")
    crudas = kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=16)
    resultado = citation_gate.filter_citations(kb, "procedure_draft", crudas)

    assert len(resultado.kept) >= citation_gate.MIN_CITATIONS
    de_esta_fuente = [c for c in crudas if c.node_id in ids and c.text in resultado.kept]
    assert de_esta_fuente, (
        "procedure_draft cita, pero nada de la fuente #30: la ingesta no fue la causa. "
        f"cita {resultado.kept}"
    )


def test_procedure_audit_no_cayo_bajo_el_piso(kb: KnowledgeBase) -> None:
    """La tool que estaba en el filo.

    Antes del merge tenia exactamente MIN_CITATIONS (2): la posicion desde la cual
    UN candidato desplazado significa cero, porque el piso vacia el lote entero.
    El merge le desplazo las dos y le puso tres -- pero eso hay que verificarlo,
    no suponerlo, cada vez que el grafo cambie.
    """
    reg = build_default_registry()
    tool = reg.get("procedure_audit")
    crudas = kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=16)
    resultado = citation_gate.filter_citations(kb, "procedure_audit", crudas)
    assert len(resultado.kept) >= citation_gate.MIN_CITATIONS, resultado.omitted


def test_la_fuente_no_esta_marcada_como_interna() -> None:
    """Lo contrario de las fuentes #26/#28/#29: entrar a `_INTERNAL_SOURCES` la
    excluiria de toda cita -- pero sus nodos SEGUIRIAN consumiendo slots del pool,
    porque el filtro corre despues de que search() rankee. Todo el riesgo y cero
    beneficio; este test lo hace imposible por accidente."""
    for archivo in SOURCE_FILES:
        assert archivo not in _INTERNAL_SOURCES, archivo


# --- 2. procedencia: lo que el cliente ve ------------------------------------


def test_ningun_source_file_nombra_una_obra_que_nadie_leyo(nodos: list[dict]) -> None:
    """El cliente ve `label - source_file source_location` y nada mas. Un nodo con
    `source_file: iso-9001-2015.pdf` afirmaria que se leyo la norma; se confirmo por
    fuentes secundarias y ningun ente certificador resulto alcanzable."""
    for n in nodos:
        assert n["source_file"] not in FORBIDDEN_SOURCE_FILES, n["id"]


def test_cada_nodo_declara_su_propia_procedencia(nodos: list[dict]) -> None:
    """No hay un `source_file` comun para toda la fuente: siete artefactos distintos,
    de distinto tipo de evidencia. Un solo nombre para los doce borraria que el ASQ
    BoK se leyo entero y la ficha del CQI-8 es una pagina de producto."""
    for n in nodos:
        assert n.get("source_file"), n["id"]
        assert n.get("source_location"), n["id"]
    assert {n["source_file"] for n in nodos} == SOURCE_FILES


def test_el_papel_de_trabajo_no_es_source_file_de_nadie(nodos: list[dict]) -> None:
    """`SOP_DOCUMENT_CONTROL_SOURCES.md` es el input del script y el registro
    auditable, no una fuente. Citarlo seria citar nuestro propio resumen."""
    assert PAPEL_DE_TRABAJO.exists()
    for n in nodos:
        assert PAPEL_DE_TRABAJO.name not in str(n["source_file"]), n["id"]


def test_el_papel_de_trabajo_cubre_los_siete_artefactos() -> None:
    """Si un artefacto entra al grafo sin ficha en el papel de trabajo, su
    procedencia deja de ser auditable y la regla del documento se vuelve decorativa."""
    texto = PAPEL_DE_TRABAJO.read_text(encoding="utf-8")
    faltan = [a for a in sorted(SOURCE_FILES) if a not in texto]
    assert not faltan, f"artefactos sin ficha: {faltan}"


# --- 3. estructura: por que este merge no envenena a las otras tools ---------


def test_los_anclajes_existen_y_son_anteriores(grafo: dict, ids: set[str]) -> None:
    indice = {n["id"] for n in grafo["nodes"]}
    _, aristas = build_payload()
    for e in aristas:
        for punta in (e["source"], e["target"]):
            assert punta in indice, f"ancla colgante: {punta}"
        externa = e["source"] if e["source"] not in ids else e["target"]
        if externa not in ids:
            assert externa in indice, externa


def test_los_anclajes_sobreviven_un_rebuild(grafo: dict, ids: set[str]) -> None:
    """Un ancla en REPLACE_STEMS se borra en el proximo rebuild de libros y se lleva
    la arista: un puente que se corta solo, sin que nada falle. Por eso las diez
    cuelgan de heizer-render, que esta fuera de esa lista."""
    por_id = {n["id"]: n for n in grafo["nodes"]}
    _, aristas = build_payload()
    for e in aristas:
        for punta in (e["source"], e["target"]):
            if punta in ids:
                continue
            archivo = str(por_id[punta].get("source_file", ""))
            stem = Path(archivo).stem
            assert stem not in REPLACE_STEMS, f"{punta} vive en {archivo}, que un rebuild reemplaza"


def test_ningun_nodo_nuevo_es_un_iman(grafo: dict, ids: set[str]) -> None:
    """La guardia que las fuentes #26 y #27 no tenian. Un nodo de grado alto entra al
    cierre de 2 saltos de muchas anclas ajenas y les desplaza candidatos on-topic."""
    grados: dict[str, int] = dict.fromkeys(ids, 0)
    for e in grafo["links"]:
        for punta in (e.get("source"), e.get("target")):
            if punta in grados:
                grados[punta] += 1
    excedidos = {k: v for k, v in grados.items() if v > MAX_DEGREE}
    assert not excedidos, f"iman(es): {excedidos} (tope {MAX_DEGREE})"


def test_no_hay_hub(grafo: dict, ids: set[str]) -> None:
    """La diferencia estructural deliberada contra merge_capacity_gaps.py.

    Ese script cuelga todo de un HUB_ID. Con MAX_HOPS=2, un hub que toque los doce
    conceptos deja CUALQUIER par a 2 saltos: la self-validation loophole que
    EXCLUDED_CONCEPTS existe para tapar. Sin hub, cada concepto es citable solo por
    su propia vecindad. Se verifica por la propiedad, no por la ausencia de una
    constante: ningun nodo de la fuente toca a la mayoria de los demas.
    """
    vecinos: dict[str, set[str]] = {i: set() for i in ids}
    for e in grafo["links"]:
        s, t = e.get("source"), e.get("target")
        if s in vecinos and t in ids:
            vecinos[s].add(t)
        if t in vecinos and s in ids:
            vecinos[t].add(s)
    techo = len(ids) // 2
    hubs = {k: len(v) for k, v in vecinos.items() if len(v) > techo}
    assert not hubs, f"hub interno: {hubs} (un nodo toca mas de {techo} de los {len(ids)})"


def test_la_comunidad_es_propia(nodos: list[dict], grafo: dict) -> None:
    """Comunidad fresca en vez de re-correr Louvain: un re-cluster reasignaria miles
    de nodos e invalidaria toda medicion de citas previa a este merge."""
    assert {n.get("community") for n in nodos} == {COMMUNITY}
    ajenos = [
        n["id"]
        for n in grafo["nodes"]
        if n.get("community") == COMMUNITY and n.get("source_file") not in SOURCE_FILES
    ]
    assert not ajenos, f"la comunidad {COMMUNITY} no es exclusiva: {ajenos}"


# --- 4. las contradicciones se preservan -------------------------------------


def test_las_contradicciones_entran_como_nodos_citables(nodos: list[dict]) -> None:
    """Una cita que dice "las fuentes no coinciden" vale mas que una que finge un
    numero. La cadencia del nivel superior de un LPA tiene cuatro respuestas en
    cuatro fuentes reales, y el reparto de tiempo del leader standard work tiene dos
    en el mismo libro. Aplanar cualquiera de las dos a un valor seria inventar."""
    contradicciones = [n for n in nodos if n.get("file_type") == "contradiction"]
    assert len(contradicciones) == 2, [n["id"] for n in contradicciones]
    for n in contradicciones:
        assert n.get("rationale"), n["id"]


def test_el_merge_es_idempotente(grafo: dict, ids: set[str]) -> None:
    """Correr el script dos veces no puede duplicar nodos ni aristas. El grafo se
    declara `multigraph: false`, asi que una arista paralela es un dato invalido que
    ninguna lectura posterior detecta."""
    nodos_esperados, aristas_esperadas = build_payload()
    assert len(ids) == len(nodos_esperados)

    vistas = set()
    for e in grafo["links"]:
        clave = (e.get("source"), e.get("target"), e.get("relation"))
        if clave[0] in ids or clave[1] in ids:
            assert clave not in vistas, f"arista duplicada: {clave}"
            vistas.add(clave)
    assert len(vistas) == len(aristas_esperadas)
