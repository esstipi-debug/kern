"""Merge documentation/KERN_BRECHAS_DE_LANZAMIENTO.md into the books graph as source #29.

Cuatro brechas de canal y una frontera, cada una puenteada a un concepto de teoria
que YA EXISTE en el grafo y al sitio de codigo donde vive. Misma forma que la
fuente #28 (KERN_BRECHAS_DE_CAPACIDAD.md): un documento interno de Kern, ingerido
en BUILD TIME por un script commiteado y revisado, y excluido para siempre de las
citas a cliente via ``scm_agent.knowledge._INTERNAL_SOURCES``.

Por que un script y no una escritura en tiempo de consulta: es la linea que trazo
el PR #186 al desconectar ``scm_agent/graph_memory.py`` (borrado despues, en esta
misma rama). Escribir nodos en el grafo de citas DURANTE una consulta es la
perturbacion que el barrido de pool de citas existe para prevenir; escribirlos en
un merge revisado no lo es, y es como entraron las 28 fuentes anteriores.

TODA ancla de teoria de este archivo es DURABLE: ninguna pertenece a un libro de
``scripts/merge_l3_rebuild.REPLACE_STEMS``, asi que un rebuild real de las fuentes
no borra el nodo ni la arista. Lo verifica
tests/test_launch_gaps_graph.py::test_los_anclajes_sobreviven_un_rebuild.

El graph.json commiteado tiene que ser reproducible corriendo exactamente este
script sobre GAPS, asi que toda transformacion vive aca.

Re-correr esta prohibido por defecto: el merge es aditivo y una segunda pasada
duplicaria cada nodo y cada arista. Restaurar el grafo previo
(``git checkout HEAD -- knowledge/scm-books/graph.json``) y re-correr, o pasar --force.

Usage:
    python scripts/merge_launch_gaps.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.lanzamiento.bak"
SOURCE_DOC = REPO / "documentation" / "KERN_BRECHAS_DE_LANZAMIENTO.md"

SOURCE_FILE = "KERN_BRECHAS_DE_LANZAMIENTO.md"
NS = "kernlanzamiento"
HUB_ID = f"{NS}_brechas_de_lanzamiento"
REPO_TAG = "knowledge"

#: Comunidad propia, id fresco en vez de un re-cluster completo: re-correr Louvain
#: reasignaria miles de nodos (e invalidaria GRAPH_REPORT.md) para colocar once.
#: 202 la ocupa la fuente #28.
COMMUNITY = 203

#: El commit contra el que se verifico cada sitio de codigo de las fichas.
COMMIT_VERIFICADO = "69b52d1"

GAPS: tuple[dict, ...] = (
    {
        "n": "L1",
        "slug": "ledger_de_leads_efimero",
        "label": "Brecha L1: el ledger de leads se borra en cada deploy",
        "rationale": (
            "CERRADA por la PR #269 (b883f18). LEADS_DIR estaba fijo sobre el filesystem "
            "efimero de la Machine, sin override de entorno, asi que la unica prueba de que "
            "alguien uso el embudo se perdia en cada deploy -- el quinto almacen efimero, y "
            "el unico que no se podia apuntar al volumen. Ahora acepta LINCHPIN_LEADS_DIR y "
            "fly.toml lo manda a /data/leads_ledger. Sin esto no se podia medir ningun embudo."
        ),
        "theory": "knowledge::inventory_record_accuracy",
        "relation": "conceptually_related_to",
        "theory2": None,
        "code_label": "webapp/app.py::LEADS_DIR",
        "code_location": "line 126",
        "doc_location": "Brecha L1",
    },
    {
        "n": "L2",
        "slug": "shopify_sin_app_store",
        "label": "Brecha L2: Shopify es custom app, no app del App Store",
        "rationale": (
            "El comerciante crea la app en su admin y entrega un Admin API token. Una app "
            "del App Store exige OAuth, app embebida, los tres webhooks obligatorios de "
            "GDPR y Billing API, y ninguna ruta de webapp/app.py coincide con nada de eso. "
            "El brief la pone en Fase 2, con decision aparte."
        ),
        "theory": None,
        "relation": None,
        "theory2": None,
        "code_label": "src/connectors/shopify_connect.py::check_connection",
        "code_location": "modulo",
        "doc_location": "Brecha L2",
    },
    {
        "n": "L3",
        "slug": "billing_stub_sin_webhook",
        "label": "Brecha L3: el billing es un stub sin webhook de Stripe",
        "rationale": (
            "create_checkout_session solo resuelve una variable STRIPE_LINK_<SLUG> puesta a "
            "mano y degrada a mailto; el modulo declara que no llama a la API de Stripe y no "
            "hay handler de webhook en todo webapp. Sin ese evento no existe el disparador "
            "que daria de alta una credencial sola. Bloquea aguas arriba a L2 y a la brecha "
            "de capacidad 7."
        ),
        "theory": "knowledge::supplier_onboarding_scf",
        "relation": "conceptually_related_to",
        "theory2": None,
        "code_label": "webapp/billing.py::create_checkout_session",
        "code_location": "line 15",
        "doc_location": "Brecha L3",
    },
    {
        "n": "L4",
        "slug": "addon_odoo_serie_y_online",
        "label": "Brecha L4: el addon de Odoo estaba en 17.0",
        "rationale": (
            "CERRADA por la PR #258 (69b52d1): el addon corre en Odoo 19, verificado "
            "instalandolo en una base limpia en Docker, y las dos series conviven bajo el "
            "mismo nombre tecnico como exige la Apps Store. El segundo techo que esta ficha "
            "mezclaba -- Odoo Online -- no era una brecha sino una restriccion de plataforma, y se separo a F2."
        ),
        "theory": "knowledge::enterprise_resource_planning",
        "relation": "conceptually_related_to",
        "theory2": "knowledge::erp_data_integration",
        "code_label": "odoo_addon/19.0/kern_dry_run/__manifest__.py",
        "code_location": "manifest de la serie 19.0",
        "doc_location": "Brecha L4",
    },
    {
        "n": "F2",
        "slug": "frontera_odoo_online",
        "label": "Frontera F2: Odoo Online no acepta modulos de terceros",
        "rationale": (
            "NO es trabajo pendiente y no se cierra nunca. El SaaS de odoo.com no admite "
            "modulos de terceros con codigo Python, asi que ninguna serie del addon va a "
            "correr ahi. No bloquea el canal: la External API que src/connectors/odoo_connect.py "
            "ya habla cubre Odoo Online, Odoo.sh y on-premise con una sola ruta de solo "
            "lectura -- el manifest de la serie 19.0 lo dice de frente. El addon es una "
            "comodidad para quien puede instalarlo, no el unico camino."
        ),
        "theory": "knowledge::erp_data_integration",
        "relation": "conceptually_related_to",
        "theory2": None,
        "code_label": "src/connectors/odoo_connect.py::check_connection",
        "code_location": "modulo",
        "doc_location": "Frontera F2",
    },
    {
        "n": "F1",
        "slug": "frontera_plazo_shopify",
        "label": "Frontera F1: el plazo de proveedor es imposible desde Shopify",
        "rationale": (
            "NO es trabajo pendiente y no se cierra nunca. Shopify no tiene ordenes de "
            "compra ni recepciones en su Admin API, asi que el plazo real de proveedor -- la "
            "salida #1 del embudo -- no se puede derivar de datos de Shopify. El modulo lo "
            "declara ausente con motivo y se niega a rellenarlo. Define que por el canal "
            "Shopify se vende analisis de venta y precio, no reposicion con plazo real."
        ),
        "theory": "knowledge::information_sharing_difficulty",
        "relation": "conceptually_related_to",
        "theory2": None,
        "code_label": "src/connectors/shopify_connect.py::PLAZO_AUSENTE",
        "code_location": "line 118",
        "doc_location": "Frontera F1",
    },
)


def gap_id(gap: dict) -> str:
    return f"{NS}_{gap['slug']}"


def code_id(gap: dict) -> str:
    return f"{NS}_{gap['slug']}_codigo"


def _node(node_id: str, label: str, file_type: str, location: str, rationale: str | None) -> dict:
    """Un nodo del grafo de libros, con la misma forma que los de la fuente #28."""
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
    """Una arista EXTRACTED: cada una la afirma el documento fuente."""
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
    """Los 13 nodos y 18 aristas que aporta esta fuente. Puro, sin I/O."""
    nodes: list[dict] = [
        _node(
            HUB_ID,
            "Brechas de lanzamiento de Kern",
            "document",
            "documento",
            "Lo que falta para vender Kern por un canal (Shopify, Odoo, Stocky) y no de a "
            "un cliente por vez, mas una frontera que no se cierra. Cada item trae una "
            "sonda que mide si sigue abierto. Documento interno: nunca sale como cita a "
            "un cliente.",
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
                f"Sitio de codigo de {gap['n']}, verificado en {gap['code_location']} "
                f"del commit {COMMIT_VERIFICADO}.",
            )
        )
        edges.append(_edge(HUB_ID, gid, "part_of", gap["doc_location"]))
        edges.append(_edge(gid, cid, "references", gap["doc_location"]))
        if gap["theory"] is not None:
            edges.append(_edge(gid, gap["theory"], gap["relation"], gap["doc_location"]))
        if gap["theory2"] is not None:
            edges.append(_edge(gid, gap["theory2"], gap["relation"], gap["doc_location"]))

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

    # Toda ancla de teoria tiene que existir ya. Una ancla colgando es el modo de
    # falla que este ejercicio entero existe para prevenir (inventar un nodo de
    # teoria), asi que es un error duro, nunca un warning.
    dangling = sorted(
        {
            e["target"]
            for e in new_edges
            if e["target"] not in existing_ids and e["target"] not in {n["id"] for n in new_nodes}
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
    print(
        f"  {anchored}/{len(GAPS)} items anclados a un concepto de teoria existente; "
        f"{len(GAPS) - anchored} deliberadamente sin ancla"
    )
    if args.dry_run:
        print("dry run - nothing written")
        return 0

    shutil.copy2(COMMITTED, BACKUP)
    graph["nodes"] = nodes + new_nodes
    graph["links"] = links + new_edges

    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(COMMITTED.parent), prefix=".merge_lanzamiento_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, COMMITTED)
    print(f"merged into {COMMITTED.relative_to(REPO)} (backup at {BACKUP.relative_to(REPO)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
