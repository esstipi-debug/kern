"""El inventario del grafo L3, atado a los numeros que la documentacion declara.

Por que existe. El 2026-09-15 entro la obra curada #27 (Vandeput, *Data Science for
Supply Chain Forecasting*, +60 nodos) y el conteo se movio en la pagina, el
`README.md` de la raiz, `pyproject.toml` y `CLAUDE.md` -- pero `CLAUDE.md` quedo
diciendo "41 external source files" cuando el artefacto ya tenia 42, y
`knowledge/scm-books/README.md` quedo dos merges atras: "24 supply-chain books",
3044 nodos y 6198 aristas. Nada lo cazaba, porque
`tests/test_how_it_works_page.py::test_source_count_claim_still_matches_the_books_graph`
fija la PAGINA (y el conteo de archivos), no los documentos.

Este test deriva los numeros del artefacto y exige que los documentos los digan. Un
merge futuro lo pone rojo hasta que la prosa se mueva con el grafo -- que es la regla
que `CLAUDE.md` ya declara ("when something here is stale, fix it in the same PR").

Tres definiciones, que NO son intercambiables (el README del grafo las explica):
  - ARCHIVOS: valores distintos de `source_file`, sin los `KERN_*` internos. 42 hoy.
  - OBRAS CURADAS: obras enteras que una persona citaria. Los 9 `ch*.pdf` son UN
    volumen editado (Cohen & Dai) y `asq-cssgb-body-of-knowledge-2022-map.pdf` es un
    mapa de body-of-knowledge colgado de la fuente #30, no un libro: 42 archivos ->
    27 obras. Es una decision humana por archivo, no aritmetica -- por eso los dos
    conjuntos son constantes nombradas aca y no un cociente.
  - NODOS: lo que el artefacto declara, sin recortes.

Stdlib pura a proposito: este test tiene que poder correr en un clon sin el control
plane `nosilo_core`, que es lo que la capa de agente necesita para importar.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_GRAPH = _REPO / "knowledge" / "scm-books" / "graph.json"

_INTERNAL_PREFIX = "KERN_"
_CHAPTER = re.compile(r"^ch\d")

#: Unico archivo externo que NO es una obra curada ni un capitulo: el mapa de
#: body-of-knowledge que la fuente #30 (control documental) uso como evidencia.
#: "Leer el mapa del CSSGB no es haber leido un libro" -- misma regla que dejo
#: afuera las 6 paginas web de esa fuente.
_NOT_A_WORK = {"asq-cssgb-body-of-knowledge-2022-map.pdf"}

#: Los 9 capitulos son de UN volumen editado, asi que suman UNA obra.
_CHAPTERS_ARE_ONE_WORK = True

#: Numeros declarados hoy. Cambian solo con un merge, y con el merge cambian los
#: documentos: eso es exactamente lo que este archivo vigila.
_EXPECTED_EXTERNAL_FILES = 42
_EXPECTED_WORKS = 27
_EXPECTED_NODES = 3104
_EXPECTED_LINKS = 6421
_EXPECTED_COMMUNITIES = 206
_EXPECTED_NODES_FROM_EXTERNAL = 2973


def _flat(path: Path) -> str:
    """Texto del archivo con el whitespace colapsado (la prosa viene envuelta)."""
    return " ".join(path.read_text(encoding="utf-8").split())


def _inventory() -> dict:
    data = json.loads(_GRAPH.read_text(encoding="utf-8"))
    nodes = data["nodes"]
    links = data.get("links", data.get("edges", []))

    def source(node: dict) -> str | None:
        raw = node.get("source_file")
        return Path(str(raw)).name if raw else None

    files = {source(n) for n in nodes if source(n)}
    internal = {f for f in files if f.startswith(_INTERNAL_PREFIX)}
    external = files - internal
    chapters = {f for f in external if _CHAPTER.match(f)}
    secondary = {f for f in external if f.endswith(".html")} | (_NOT_A_WORK & external)
    whole = external - chapters - secondary

    works = len(whole) + (1 if chapters and _CHAPTERS_ARE_ONE_WORK else len(chapters))
    return {
        "nodes": len(nodes),
        "links": len(links),
        "communities": len({n.get("community") for n in nodes}),
        "files": files,
        "internal": internal,
        "external": external,
        "chapters": chapters,
        "secondary": secondary,
        "whole": whole,
        "works": works,
        "nodes_from_external": sum(
            1 for n in nodes if source(n) and source(n) not in internal
        ),
    }


def test_cada_archivo_externo_esta_clasificado_en_obra_capitulo_o_artefacto() -> None:
    """La particion tiene que ser total: un archivo nuevo obliga a decidir que es.

    Si alguien agrega un PDF de una fuente nueva, cae en `whole` y mueve el conteo de
    obras (el test de abajo se pone rojo y obliga a actualizar la prosa). Si agrega
    algo que no es ninguna de las tres cosas -- un `.csv`, un `.docx` -- tambien cae
    en `whole`, y ese es el caso que este test prefiere gritar: la obra se declara a
    mano, no por descarte.
    """
    inv = _inventory()
    external = inv["external"]
    parts = inv["whole"] | inv["chapters"] | inv["secondary"]

    assert parts == external, sorted(external - parts)  # sin resto: particion total
    assert not (inv["whole"] & inv["chapters"])
    assert not (inv["whole"] & inv["secondary"])
    assert not (inv["chapters"] & inv["secondary"])

    assert len(inv["chapters"]) == 9, sorted(inv["chapters"])
    assert len(inv["secondary"]) == 7, sorted(inv["secondary"])  # 6 web + el BoK
    assert _NOT_A_WORK <= inv["files"], "el BoK de la fuente #30 desaparecio del grafo"


def test_el_artefacto_tiene_27_obras_sobre_42_archivos() -> None:
    inv = _inventory()
    assert len(inv["external"]) == _EXPECTED_EXTERNAL_FILES, sorted(inv["external"])
    assert inv["works"] == _EXPECTED_WORKS
    assert len(inv["internal"]) == 3, sorted(inv["internal"])


def test_los_documentos_declaran_el_mismo_inventario_que_el_artefacto() -> None:
    """El punto del archivo: la prosa y el artefacto no pueden separarse.

    Cada afirmacion vive en su documento; si un merge mueve los numeros y la prosa no
    lo sigue, esto se pone rojo nombrando el documento.
    """
    inv = _inventory()
    works, files = inv["works"], len(inv["external"])

    claude = _flat(_REPO / "CLAUDE.md")
    assert f"{works} curated external works** over {files} external source files" in claude
    assert "tests/test_l3_source_inventory.py" in claude

    root_readme = _flat(_REPO / "README.md")
    assert f"{works} curated external sources" in root_readme
    assert f"over {files} external source files" in root_readme
    assert f"{inv['nodes']:,} nodes" in root_readme
    assert f"{inv['nodes_from_external']:,}" in root_readme

    assert f"{works} curated external sources" in _flat(_REPO / "pyproject.toml")

    graph_readme = _flat(_REPO / "knowledge" / "scm-books" / "README.md")
    assert f"{works} curated external works over {files} external source files" in graph_readme
    assert (
        f"{inv['nodes']} nodes · {inv['links']} links · {inv['communities']} communities"
        in graph_readme
    )


def test_el_grafo_no_perdio_nodos_ni_aristas() -> None:
    """El artefacto contra si mismo: un merge que encoge se nota aca, no en la prosa."""
    inv = _inventory()
    assert inv["nodes"] == _EXPECTED_NODES
    assert inv["links"] == _EXPECTED_LINKS
    assert inv["communities"] == _EXPECTED_COMMUNITIES
    assert inv["nodes_from_external"] == _EXPECTED_NODES_FROM_EXTERNAL
