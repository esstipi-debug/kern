"""La fuente #29 (brechas de lanzamiento) entra al grafo y NO SALE como cita.

`documentation/KERN_BRECHAS_DE_LANZAMIENTO.md` es un documento propio de Kern.
Un documento propio no puede aparecer como cita al lado de Chopra & Meindl: eso
vaciaria en silencio la promesa de que las citas salen de fuentes publicas. El
unico chokepoint que lo impide es
`scm_agent/knowledge.py::_INTERNAL_SOURCES` + `ground_citations_detailed`, y toda
fuente interna nueva pasa por esa lista Y por un test adversarial. Este es el
test adversarial, modelado sobre el de la fuente #28.

Lo que se fija, ademas del filtro:

* los anclajes de teoria EXISTEN y son anteriores a esta fuente -- no se invento
  ningun nodo;
* los anclajes SOBREVIVEN un rebuild de libros: ninguno pertenece a
  `merge_l3_rebuild.REPLACE_STEMS`, o el rebuild borraria el nodo y con el la
  arista, dejando un puente que se corta solo;
* el item sin ancla (L2) no tiene arista de teoria fabricada;
* cada sitio de codigo apunta a un archivo que existe.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent.knowledge import _INTERNAL_SOURCES, KnowledgeBase
from scm_agent.tools import build_default_registry
from scripts.merge_l3_rebuild import REPLACE_STEMS
from scripts.merge_launch_gaps import GAPS, HUB_ID, SOURCE_FILE, code_id, gap_id

RAIZ = Path(__file__).resolve().parents[1]
GRAFO = RAIZ / "knowledge" / "scm-books" / "graph.json"


@pytest.fixture(scope="module")
def grafo() -> dict:
    return json.loads(GRAFO.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase()


@pytest.fixture(scope="module")
def ids_de_la_fuente(grafo: dict) -> set[str]:
    ids = {n["id"] for n in grafo["nodes"] if n.get("source_file") == SOURCE_FILE}
    assert ids, f"{SOURCE_FILE} no aporto ningun nodo; corriste scripts/merge_launch_gaps.py?"
    return ids


# --- 1. nunca una cita a cliente ---------------------------------------------


def test_la_fuente_esta_registrada_como_interna():
    assert SOURCE_FILE in _INTERNAL_SOURCES


def test_ningun_nodo_llega_a_la_ruta_de_citas_de_ninguna_herramienta(kb, ids_de_la_fuente):
    """La ruta real del cliente: las 50 herramientas, con pool generoso."""
    fugas: list[str] = []
    tools = build_default_registry().list()
    assert len(tools) >= 50, f"el registry bajo a {len(tools)}; ampliar este barrido"
    for tool in tools:
        for cita in kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=16):
            if cita.node_id in ids_de_la_fuente or SOURCE_FILE in cita.text:
                fugas.append(f"{tool.key}: {cita.node_id} - {cita.text}")
    assert not fugas, "nodos de lanzamiento llegaron a la ruta de citas:\n" + "\n".join(fugas)


def test_ningun_nodo_llega_consultado_con_sus_propias_palabras(kb, grafo, ids_de_la_fuente):
    """EL TEST QUE IMPORTA: distingue "filtrado" de "quedo mal rankeado".

    Estas consultas son la mejor coincidencia posible para estos nodos. Si el
    filtro no existiera, serian el primer hit de cada una.
    """
    por_id = {n["id"]: n for n in grafo["nodes"]}
    fugas: list[str] = []
    for node_id in sorted(ids_de_la_fuente):
        nodo = por_id[node_id]
        consulta = f"{nodo['label']} {nodo.get('rationale', '')}"
        for cita in kb.ground_citations_detailed(consulta.split()[:12], consulta, limit=16):
            if cita.node_id in ids_de_la_fuente or SOURCE_FILE in cita.text:
                fugas.append(f"la consulta de {node_id} devolvio {cita.node_id}")
    assert not fugas, "el filtro no los frena:\n" + "\n".join(fugas)


def test_son_descubribles_por_search_o_sea_que_el_filtro_es_lo_que_los_frena(kb):
    """Contraprueba de los dos de arriba.

    Si estos nodos fueran invisibles para el buscador, los tests anteriores
    pasarian por obscuridad y no por el filtro. `search` es la superficie de
    descubrimiento del operador y SI tiene que encontrarlos.
    """
    hits = kb.search("brechas de lanzamiento de kern shopify odoo", graph="books", limit=10)
    ids = {h.qualified_id.split("::")[-1] for h in hits}
    assert HUB_ID in ids or any(gap_id(g) in ids for g in GAPS), (
        f"ningun nodo de la fuente aparece en search: {sorted(ids)}"
    )


# --- 2. no se invento teoria, y los puentes no se cortan solos ---------------


def test_los_anclajes_existen_y_son_anteriores_a_esta_fuente(grafo):
    por_id = {n["id"]: n for n in grafo["nodes"]}
    for gap in GAPS:
        for ancla in (gap["theory"], gap["theory2"]):
            if ancla is None:
                continue
            assert ancla in por_id, f"{gap['n']} ancla en {ancla}, que no existe en el grafo"
            assert por_id[ancla].get("source_file") != SOURCE_FILE, (
                f"{gap['n']} ancla en un nodo que trajo esta misma fuente: no es teoria previa"
            )


def test_los_anclajes_sobreviven_un_rebuild(grafo):
    """Un ancla a un libro que merge_l3_rebuild re-extrae es un puente que se
    corta solo: el rebuild borra el nodo y con el la arista."""
    por_id = {n["id"]: n for n in grafo["nodes"]}
    stems = {s.lower() for s in REPLACE_STEMS}
    frágiles = []
    for gap in GAPS:
        for ancla in (gap["theory"], gap["theory2"]):
            if ancla is None:
                continue
            fuente = por_id[ancla].get("source_file") or ""
            if Path(fuente).stem.lower() in stems:
                frágiles.append(f"{gap['n']} -> {ancla} ({fuente})")
    assert not frágiles, "anclas que un rebuild borraria:\n" + "\n".join(frágiles)


def test_el_item_sin_ancla_no_tiene_arista_de_teoria_fabricada(grafo):
    """L2 no tiene ancla a proposito: no existe en el grafo ningun concepto sobre
    distribuir software por el marketplace de una plataforma. Un test que lo
    permita 'arreglar' con un nodo cercano derrota el punto del ejercicio."""
    sin_ancla = [g for g in GAPS if g["theory"] is None]
    assert sin_ancla, "si ya todos tienen ancla, revisar este test antes de borrarlo"
    for gap in sin_ancla:
        gid = gap_id(gap)
        teoricas = [
            e for e in grafo["links"]
            if e.get("source") == gid and e.get("relation") in ("cites", "conceptually_related_to")
        ]
        assert not teoricas, f"{gap['n']} gano una arista de teoria: {teoricas}"


# --- 3. las fichas apuntan a algo real ---------------------------------------


def test_cada_item_tiene_un_sitio_de_codigo_que_existe(grafo):
    por_id = {n["id"]: n for n in grafo["nodes"]}
    for gap in GAPS:
        cid = code_id(gap)
        assert cid in por_id, f"{gap['n']} no aporto su nodo de codigo"
        ruta = RAIZ / por_id[cid]["label"].split("::")[0]
        assert ruta.exists(), f"{gap['n']} apunta a {ruta}, que no existe"


def test_cada_item_cuelga_del_hub(grafo):
    aristas = {(e["source"], e["target"]) for e in grafo["links"] if e.get("relation") == "part_of"}
    for gap in GAPS:
        assert (HUB_ID, gap_id(gap)) in aristas, f"{gap['n']} no cuelga del hub"


def test_la_frontera_esta_marcada_como_tal(grafo):
    """F1 no es trabajo pendiente. Su nodo tiene que decirlo, o alguien lo va a
    tomar como backlog e intentar 'cerrarlo' inventando un plazo."""
    frontera = next(g for g in GAPS if g["n"] == "F1")
    por_id = {n["id"]: n for n in grafo["nodes"]}
    rationale = por_id[gap_id(frontera)].get("rationale", "")
    assert "no se cierra nunca" in rationale.lower()
