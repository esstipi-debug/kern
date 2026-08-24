"""Merge documentation/KERN_BRECHAS_DE_CAPACIDAD.md into the books graph as source #28.

Eight capability gaps Kern does not have yet, each bridged to a theory concept that
already exists in the graph and to the code site where it would land. Same shape as
source #26 (KERN_ATLAS_SOMBREROS_SCM.md): an internal Kern document, ingested at
BUILD time by a reviewed committed script, and permanently excluded from client
citations via ``scm_agent.knowledge._INTERNAL_SOURCES``.

Why a script and not ``scm_agent/graph_memory.py``: that module wrote ``query_outcome``
nodes into the citation graph at QUERY time and was disconnected in PR #186 for exactly
that reason ("the exact perturbation the citation-pool sweep exists to prevent" —
tests/test_knowledge_rag.py:5-8). No committed graph.json ever contained one. A merge
applied in a reviewed PR is the other side of that line, and it is how all 27 prior
sources entered.

The committed graph.json must be reproducible by running exactly this script over
GAPS, so every transformation lives here.

Re-running is refused by default: the merge is additive and a second pass would
duplicate every node and edge. Restore the pre-merge graph
(``git checkout HEAD -- knowledge/scm-books/graph.json``) and re-run, or pass --force.

Usage:
    python scripts/merge_capacity_gaps.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.brechas.bak"
SOURCE_DOC = REPO / "documentation" / "KERN_BRECHAS_DE_CAPACIDAD.md"

SOURCE_FILE = "KERN_BRECHAS_DE_CAPACIDAD.md"
NS = "kernbrechas"
HUB_ID = f"{NS}_brechas_de_capacidad"
REPO_TAG = "knowledge"

# The gaps' own community. A fresh id instead of a full re-cluster: re-running
# scripts/recluster_books_graph.py would churn the community of all 3002 existing
# nodes (and invalidate GRAPH_REPORT.md) to place 17 new ones. Louvain already
# allocates singleton communities for nodes it leaves out, so a dedicated id is a
# valid outcome of the same rule, and it is deterministic where Louvain is not.
COMMUNITY = 202

# relation is the honest fit of the theory anchor: `cites` when the concept genuinely
# grounds the gap, `conceptually_related_to` when the fit is real but moderate. Gap 2
# has no anchor and gets no theory edge — the graph has no concept for retrieval
# reranking and none was fabricated (see the doc's Brecha 2).
GAPS: tuple[dict, ...] = (
    {
        "n": 1,
        "slug": "bucle_react",
        "label": "Brecha 1: bucle ReAct para encadenar las 50 herramientas",
        "rationale": (
            "Kern no encadena herramientas: intent.classify enruta a una sola y los "
            "paquetes corren una tupla congelada de pasos. Falta observar la salida de "
            "una herramienta y reaccionar eligiendo la siguiente. Riesgo de alcance: el "
            "protocolo LLMProvider no expone tool-calling."
        ),
        "theory": "knowledge::iterative_reevaluation_loop",
        "relation": "cites",
        "code_label": "scm_agent/orchestrator.py::Orchestrator.run",
        "code_location": "line 79",
        "doc_location": "Brecha 1",
    },
    {
        "n": 2,
        "slug": "reranker",
        "label": "Brecha 2: reranker en la recuperacion de citas",
        "rationale": (
            "La ruta de citas es IDF pura y no toca los embeddings, que sólo alimentan "
            "bridge(). fastembed es un extra opt-in ausente de produccion y de CI, y los "
            "pesos de cross-encoder no estan descargados. Sin ancla de teoria en el "
            "corpus: ningun libro trata el ordenamiento de texto recuperado."
        ),
        "theory": None,
        "relation": None,
        "code_label": "scm_agent/knowledge.py::KnowledgeBase.ground_citations_detailed",
        "code_location": "line 343",
        "doc_location": "Brecha 2",
    },
    {
        "n": 3,
        "slug": "mcp_escritura_tiers",
        "label": "Brecha 3: MCP de escritura con tiers de autonomia",
        "rationale": (
            "El servidor MCP expone 35 de 50 herramientas y excluye todo writeback a "
            "proposito, mientras autonomy.py ya sabe auto-aplicar un changeset reversible "
            "y degradar el irreversible a HANDOFF. Las dos mitades existen sin conectarse."
        ),
        "theory": "knowledge::price_decision_authority",
        "relation": "conceptually_related_to",
        "code_label": "webapp/mcp_server.py::build_mcp_server",
        "code_location": "line 172",
        "doc_location": "Brecha 3",
    },
    {
        "n": 4,
        "slug": "rag_del_cliente",
        "label": "Brecha 4: corpus por cliente, no solo el corpus del oficio",
        "rationale": (
            "El conjunto de grafos esta fijado a dos, libros y codigo. No hay corpus por "
            "cliente con sus contratos, SLA y manuales, asi que preguntar si una compra "
            "cumple el contrato es incontestable. Frontera dura: el dato de un cliente no "
            "se mezcla con el corpus del oficio ni alimenta el analisis de otro cliente."
        ),
        "theory": "knowledge::supply_contract",
        "relation": "cites",
        "code_label": "scm_agent/knowledge.py::KnowledgeBase.__init__",
        "code_location": "line 164",
        "doc_location": "Brecha 4",
    },
    {
        "n": 5,
        "slug": "fan_out_paralelo",
        "label": "Brecha 5: fan-out paralelo de los pasos de un paquete",
        "rationale": (
            "Cero primitivas de paralelismo en scm_agent, jobs y src; el unico pool del "
            "repo tiene un worker y serializa. Lo que gobierna la paralelizabilidad no es "
            "el diferimiento de escrituras sino el grafo de dependencias entre pasos, que "
            "no esta vacio: cycle_count deriva de abc_xyz."
        ),
        # Not knowledge::business_process_reengineering (Christopher), whose rationale
        # is the closer prose match: Christopher is in merge_l3_rebuild.REPLACE_STEMS,
        # and a real rebuild drops that node and this edge with it. Jacobs & Chase is
        # not re-extracted, and assembly-line balancing is the better anchor anyway —
        # it is precedence-constrained tasks assigned to stations, which is exactly the
        # step-dependency problem that governs this gap.
        "theory": "knowledge::assembly_line_balancing",
        "relation": "cites",
        "code_label": "scm_agent/packages.py::run_package",
        "code_location": "line 271",
        "doc_location": "Brecha 5",
    },
    {
        "n": 6,
        "slug": "mcp_como_cliente",
        "label": "Brecha 6: MCP como cliente, no solo como servidor",
        "rationale": (
            "Kern publica herramientas y no consume ninguna. Los conectores son 21 "
            "archivos mantenidos a mano. La mitad cliente de la dependencia mcp ya esta "
            "instalada y sin usar, asi que cerrar esto no pide dependencia nueva."
        ),
        "theory": "knowledge::data_integration",
        "relation": "cites",
        "code_label": "src/connectors/__init__.py::InventorySource",
        "code_location": "line 60",
        "doc_location": "Brecha 6",
    },
    {
        "n": 7,
        "slug": "onboarding_self_serve",
        "label": "Brecha 7: onboarding self-serve de la credencial MCP",
        "rationale": (
            "El demo publico ya funciona sin operador; lo que no tiene ruta automatica es "
            "la credencial MCP paga. El bloqueo esta aguas arriba: el billing es un stub "
            "sin webhook, y la key se liga a un nombre libre sin email, pago ni expiracion."
        ),
        "theory": "knowledge::digitalization_of_supplier_data_collection",
        "relation": "conceptually_related_to",
        "code_label": "src/mcp_keys.py::McpKeyStore.issue",
        "code_location": "line 67",
        "doc_location": "Brecha 7",
    },
    {
        "n": 8,
        "slug": "critico_del_narrativo",
        "label": "Brecha 8: critico sobre el narrativo del entregable",
        "rationale": (
            "El gate de QA revisa numeros y ninguna de las funciones verify toma prosa. "
            "Despues del gate, narrative_rewrite reemplaza el resumen entero sin "
            "post-chequeo: el prompt pide conservar cada numero y nada verifica que lo "
            "haya hecho. Hay dos sitios de llamada, no uno."
        ),
        "theory": "knowledge::forecast_value_added",
        "relation": "cites",
        "code_label": "scm_agent/orchestrator.py::Orchestrator._narrative",
        "code_location": "line 244",
        "doc_location": "Brecha 8",
    },
)


def gap_id(gap: dict) -> str:
    return f"{NS}_{gap['slug']}"


def code_id(gap: dict) -> str:
    return f"{NS}_{gap['slug']}_codigo"


def _node(node_id: str, label: str, file_type: str, location: str, rationale: str | None) -> dict:
    """A books-graph node in the same shape as source #26's nodes."""
    node = {
        "label": label,
        "file_type": file_type,
        "source_file": SOURCE_FILE,
        "source_location": location,
        "source_url": None,
        "captured_at": None,
        "author": None,
        "contributor": None,
        "id": node_id,
        "community": COMMUNITY,
        "norm_label": label.lower(),
        "repo": REPO_TAG,
    }
    if rationale is not None:
        node["rationale"] = rationale
    return node


def _edge(source: str, target: str, relation: str, location: str) -> dict:
    """An EXTRACTED edge: every one of these is asserted by the source document."""
    return {
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": SOURCE_FILE,
        "source_location": location,
        "weight": 1.0,
        "source": source,
        "target": target,
    }


def build_payload() -> tuple[list[dict], list[dict]]:
    """The 17 nodes and 23 edges this source contributes. Pure — no I/O."""
    nodes: list[dict] = [
        _node(
            HUB_ID,
            "Brechas de capacidad de Kern",
            "document",
            "documento",
            "Ocho capacidades que Kern no tiene hoy, cada una anclada a un concepto de "
            "teoria del grafo de libros y al sitio de codigo donde aterrizaria. "
            "Documento interno: nunca sale como cita a un cliente.",
        )
    ]
    edges: list[dict] = []

    for gap in GAPS:
        gid, cid = gap_id(gap), code_id(gap)
        nodes.append(_node(gid, gap["label"], "concept", gap["doc_location"], gap["rationale"]))
        nodes.append(
            _node(
                cid,
                gap["code_label"],
                "code",
                f"{gap['doc_location']} / {gap['code_location']}",
                f"Sitio de codigo donde aterrizaria la brecha {gap['n']}, "
                f"verificado en {gap['code_location']} del commit 47b7e1a.",
            )
        )
        # hub -> gap: the parent document contains this gap (same direction as the
        # graph's existing part_of edges, e.g. Queuing Analysis -> M/M/1 Queue).
        edges.append(_edge(HUB_ID, gid, "part_of", gap["doc_location"]))
        # gap -> code site.
        edges.append(_edge(gid, cid, "references", gap["doc_location"]))
        # gap -> theory concept, when one genuinely exists.
        if gap["theory"] is not None:
            edges.append(_edge(gid, gap["theory"], gap["relation"], gap["doc_location"]))

    return nodes, edges


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-apply even if already merged")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    if not SOURCE_DOC.is_file():
        print(f"missing source document {SOURCE_DOC}", file=sys.stderr)
        return 1
    if not COMMITTED.is_file():
        print(f"missing {COMMITTED}", file=sys.stderr)
        return 1

    graph = json.loads(COMMITTED.read_text(encoding="utf-8"))
    nodes: list[dict] = graph["nodes"]
    links: list[dict] = graph.setdefault("links", graph.get("edges", []))
    existing_ids = {n["id"] for n in nodes if "id" in n}

    already = [n for n in nodes if n.get("source_file") == SOURCE_FILE]
    if already and not args.force:
        print(
            f"refusing: {len(already)} nodes from {SOURCE_FILE} are already in the graph.\n"
            "  git checkout HEAD -- knowledge/scm-books/graph.json  (then re-run)\n"
            "  or pass --force if you genuinely intend to re-apply.",
            file=sys.stderr,
        )
        return 1

    new_nodes, new_edges = build_payload()

    # Every theory anchor must already exist. A dangling anchor is the failure mode
    # this whole exercise exists to prevent (inventing a concept node), so it is a
    # hard error, never a warning.
    dangling = sorted(
        {
            e["target"]
            for e in new_edges
            if e["target"] not in existing_ids
            and e["target"] not in {n["id"] for n in new_nodes}
        }
    )
    if dangling:
        print(f"refusing: theory anchors not found in the graph: {dangling}", file=sys.stderr)
        return 1

    collisions = sorted(existing_ids & {n["id"] for n in new_nodes})
    if collisions and not args.force:
        print(f"refusing: node id collision with the committed graph: {collisions}", file=sys.stderr)
        return 1

    anchored = sum(1 for g in GAPS if g["theory"] is not None)
    print(
        f"nodes {len(nodes)} -> {len(nodes) + len(new_nodes)}  "
        f"links {len(links)} -> {len(links) + len(new_edges)}  "
        f"(+{len(new_nodes)} nodes, +{len(new_edges)} edges, community {COMMUNITY})"
    )
    print(f"  {anchored}/{len(GAPS)} gaps anchored to an existing theory concept; "
          f"{len(GAPS) - anchored} deliberately unanchored")
    if args.dry_run:
        print("dry run — nothing written")
        return 0

    shutil.copy2(COMMITTED, BACKUP)
    graph["nodes"] = nodes + new_nodes
    graph["links"] = links + new_edges

    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(COMMITTED.parent), prefix=".merge_brechas_")
    import os

    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
    Path(tmp).replace(COMMITTED)
    print(f"wrote {COMMITTED} (backup at {BACKUP.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
