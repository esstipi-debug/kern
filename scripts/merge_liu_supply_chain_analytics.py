"""Merge Liu, *Supply Chain Analytics* (2022, Palgrave Macmillan) into the books graph.

**Ingesta PARCIAL, a proposito.** El libro se leyo completo el 2026-08-05/06 (388 pag.:
caps. 1, 7 y 8 de primera mano, el resto por lectura delegada con informes verificados,
mas un barrido regex de 35 terminos). Nada de eso entra al grafo por el hecho de haberlo
leido: el grafo L3 no es el archivo de lo que leimos, es el respaldo de lo que el motor
afirma (``docs/superpowers/specs/2026-08-06-l3-liu-ingesta-parcial.md``). Bajo esa vara
quedan afuera:

  - ~40% del volumen: tutorial de pandas/matplotlib/sklearn sin contenido de dominio;
  - los cuatro metodos de inventario que ensena (EOQ, safety stock, ROP, revision
    periodica): Kern ya los tiene, implementados y con mejores fuentes en el grafo.
    Duplicar el concepto sin agregar rigor diluye el pool de citas;
  - el LP de almacen (p.252): antiejemplo que ignora la demanda por decreto
    ("for simplicity reason, ignore the demand pattern");
  - el clasificador de stockout (7.4) y el RFR/XGBoost sobre Rossmann (8.4): exigen
    features que ningun ERP PyME tiene.

Entra lo que el motor afirma o podria afirmar y hoy no puede citar: cinco piezas, mas
la definicion de la que cuelgan.

**Los tres errata NO entran como conceptos.** Van como ``known_errata`` del nodo de la
fuente -- un grafo de citas no puede tener un nodo citable que diga algo falso, aunque
lo diga el libro. El #1 tiene ademas un uso positivo: es evidencia documentada de que
la ensenanza estandar confunde cycle service level con fill rate, distincion que
``src/safety_stock.py`` ya hace bien.

**Por que este script lleva su extraccion inline.** Misma razon que
``merge_vandeput_forecasting.py``: la extraccion la hizo quien leyo el libro, asi que
es un artefacto fijo y vive en el repo. ``graph.json`` se reproduce desde un clon
limpio, sin clave de LLM y sin el corpus crudo (que esta gitignoreado). Solo se guardan
etiquetas y descripciones de una linea; no se reproduce texto del libro.

**La comunidad es NUEVA (206) y no se re-clusteriza.** Re-correr Louvain reasigna miles
de nodos e invalida toda medicion de citas registrada antes; el precedente es la fuente
#27 (Vandeput forecasting), que tomo la 205 por la misma razon. Los seis conceptos van a
una comunidad propia sin hub: un nodo que toque a los seis pondria cualquier par a
``MAX_HOPS``, que es el atajo de auto-validacion que ``EXCLUDED_CONCEPTS`` existe para
cerrar.

Run order (see knowledge/scm-books/README.md):
  1. this script
  2. ``scripts/regen_books_report.py`` (needs ``graphify``: ``pip install "graphifyy[mcp]"``).
     It re-renders the report from the committed communities and **never re-clusters**, so it is
     safe after a merge: the alternative -- ``graphify cluster-only`` -- re-runs Louvain, reassigns
     thousands of nodes and invalidates every citation measurement taken before it. The report
     committed with this source was regenerated on 2026-10-26 (3110 nodes / 6421 edges / 207
     communities; the artifact itself says 3111 / 6449 / 207 -- one malformed node and the 28
     parallel links are what graphify's simple graph drops).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.json.bak"

NS = "knowledge"
BOOK_SLUG = "liu-supply-chain-analytics.pdf"
BOOK_ID = "book_liu_supply_chain_analytics"
MIN_INFERRED = 0.75
RUBRIC = (0.55, 0.65, 0.75, 0.85, 0.95)

#: Comunidad fresca para esta fuente. ``main`` aborta si ya esta ocupada: escribirla
#: encima de una existente fundiria dos obras en una seccion del reporte sin que
#: ningun conteo se mueva.
COMMUNITY = 206

# --- extraction ---------------------------------------------------------------
# (local_id, label, source_location, rationale)
# Ids que ya existen en el grafo se enganchan al nodo canonico (la arista es el
# puente cross-book); los que no existen se agregan con la cita de este libro.
CONCEPTS: tuple[tuple[str, str, str, str], ...] = (
    ("supply_chain_analytics", "Supply Chain Analytics", "p.27",
     "Application of machine learning and data analytics techniques to supply chain "
     "data; the book's own definition, and the umbrella the other four concepts hang from."),
    ("analytics_maturity_levels",
     "Descriptive, Diagnostic, Predictive and Prescriptive Analytics", "p.25-26 (Fig. 1.6)",
     "Four levels: what happened, why it happened, what will happen, what to do about it. "
     "The diagnostic level - the one that answers WHY - is the one this graph lacked; the "
     "book's definition of prescriptive names safety-stock and inventory-level "
     "recommendations, i.e. the engine's own output."),
    ("operations_research_vs_supply_chain_analytics",
     "Operations Research vs Supply Chain Analytics", "p.30 (preface p.6)",
     "Boundary drawn on purpose: OR emphasises mathematical modelling and optimisation, "
     "SCA explores and learns from data, and the book states it includes no further OR "
     "topics. Measured with that ruler EOQ, safety stock, (s,Q)/(R,S), p-median, VRP and "
     "DRP are OR, not analytics."),
    ("smart_analytics_goals", "SMART Goals for Supply Chain Analytics", "p.28-30 (Fig. 1.7)",
     "Simple, Meaningful, Actionable, Reliable, Timely. What is citable: the adoption rule "
     "(the simpler and more understandable a solution, the likelier it is to be adopted - "
     "the Coca-Cola quote this book reports is 'models must be simple and transparent, yet "
     "effective'), the failure mode (without deep process knowledge the output is 'nothing "
     "more than fun and interesting facts'), and timely meaning PROACTIVE, not on demand."),
    ("small_firm_analytics_conditions", "When a Small Firm Does Need Analytics Software",
     "p.240-284",
     "The book says four times that a small company can manage inventory by hand - and "
     "all four carry a condition: demand stable and predictable, SKUs not complex, "
     "suppliers able to replenish ad hoc and fast. So it does not say the small firm "
     "never needs this; it says under which conditions it does not, which turns an "
     "objection into a qualification criterion."),
    ("tracking_signal_band", "Tracking Signal Band (+-6)", "p.312 (ec. 8.16)",
     "Operational threshold for TS = cumulative forecast error / MAD: outside +-6 the "
     "forecast is biased, and the reading is binary and actionable ('either the model is "
     "flawed, or the underlying demand pattern has shifted'). The threshold is NOT Liu's: "
     "the book attributes it to Chopra & Meindl (2016), which is where the authority edge "
     "goes. This repo implements it in src/forecast_metrics.py."),
)

# (source, relation, target, confidence, score)
EDGES: tuple[tuple[str, str, str, str, float], ...] = (
    # -- la definicion y sus niveles ------------------------------------------
    ("supply_chain_analytics", "conceptually_related_to", "demand_forecasting", "EXTRACTED", 1.0),
    ("supply_chain_analytics", "conceptually_related_to", "predictive_analytics", "EXTRACTED", 1.0),
    ("supply_chain_analytics", "conceptually_related_to", "machine_learning_demand", "EXTRACTED", 1.0),
    ("analytics_maturity_levels", "part_of", "supply_chain_analytics", "EXTRACTED", 1.0),
    ("analytics_maturity_levels", "conceptually_related_to", "maturity_model_descriptive_purpose", "EXTRACTED", 1.0),
    ("analytics_maturity_levels", "conceptually_related_to", "maturity_model_prescriptive_purpose", "EXTRACTED", 1.0),
    ("analytics_maturity_levels", "conceptually_related_to", "predictive_analytics", "EXTRACTED", 1.0),
    # -- la frontera con OR ---------------------------------------------------
    ("operations_research_vs_supply_chain_analytics", "conceptually_related_to", "supply_chain_analytics", "EXTRACTED", 1.0),
    ("operations_research_vs_supply_chain_analytics", "conceptually_related_to", "service_level_optimization", "INFERRED", 0.85),
    ("operations_research_vs_supply_chain_analytics", "conceptually_related_to", "ai_ml_forecasting", "INFERRED", 0.75),
    # -- SMART y adopcion organizacional --------------------------------------
    ("smart_analytics_goals", "part_of", "supply_chain_analytics", "EXTRACTED", 1.0),
    ("smart_analytics_goals", "conceptually_related_to", "key_performance_indicators", "EXTRACTED", 1.0),
    ("smart_analytics_goals", "conceptually_related_to", "forecast_value_added", "INFERRED", 0.85),
    # -- cuando una PyME si necesita el software ------------------------------
    ("small_firm_analytics_conditions", "part_of", "supply_chain_analytics", "EXTRACTED", 1.0),
    ("small_firm_analytics_conditions", "conceptually_related_to", "demand_classification", "EXTRACTED", 1.0),
    ("small_firm_analytics_conditions", "conceptually_related_to", "intermittent_demand", "EXTRACTED", 1.0),
    ("small_firm_analytics_conditions", "conceptually_related_to", "safety_stock", "INFERRED", 0.85),
    # -- el umbral del tracking signal ---------------------------------------
    ("tracking_signal_band", "conceptually_related_to", "tracking_signal", "EXTRACTED", 1.0),
    ("tracking_signal_band", "conceptually_related_to", "forecast_error", "EXTRACTED", 1.0),
    ("tracking_signal_band", "conceptually_related_to", "mean_error_bias", "EXTRACTED", 1.0),
    ("tracking_signal_band", "conceptually_related_to", "mean_absolute_error", "EXTRACTED", 1.0),
    ("tracking_signal_band", "cites", "book_chopra_meindl", "EXTRACTED", 1.0),
)

#: Canonical ids this book's edges attach to but does not define. Listed so a typo
#: (``tracking_singal``) fails loudly instead of silently dropping the edge - which is
#: how a bridge quietly turns into an isolated island. ``main`` asserts every one.
BRIDGES: tuple[str, ...] = (
    "ai_ml_forecasting", "book_chopra_meindl", "demand_classification", "demand_forecasting",
    "forecast_error", "forecast_value_added", "intermittent_demand", "key_performance_indicators",
    "machine_learning_demand", "maturity_model_descriptive_purpose",
    "maturity_model_prescriptive_purpose", "mean_absolute_error", "mean_error_bias",
    "predictive_analytics", "safety_stock", "service_level_optimization", "tracking_signal",
)

#: Los tres errores verificados de primera mano. Se guardan como propiedad de la
#: FUENTE, nunca como nodo de concepto ni como arista hacia uno: el cliente ve
#: ``label - source_file source_location`` y no hay lugar donde poner "pero este
#: numero esta mal".
KNOWN_ERRATA: tuple[dict[str, str], ...] = (
    {
        "location": "p.246",
        "text": "'to potentially satisfy 90% of the demand, you will need a Z-score about "
                "1.28' - 1.28 is the 90% CYCLE SERVICE LEVEL, not a 90% fill rate.",
        "repo_note": "src/safety_stock.py keeps both quantities apart; the engine is more "
                     "correct than the book here.",
    },
    {
        "location": "p.311 (ec. 8.13)",
        "text": "RMSE printed as sqrt(1/n * sum|E_t/A_t|), which is the body of MAPE under "
                "a root; the correct formula squares E_t.",
        "repo_note": "src/forecast_metrics.py::rmse uses the squared errors.",
    },
    {
        "location": "p.265",
        "text": "'solely relying on accuracy score is sufficient, especially when the "
                "dataset is extremely imbalanced' - says the opposite of its own section "
                "(a missing 'not').",
        "repo_note": "not ingested; no concept depends on it.",
    },
)

BOOK_NODE = {
    "label": "Liu: Supply Chain Analytics (2022)",
    "file_type": "paper",
    "source_location": None,
    "source_url": None,
    "captured_at": None,
    "author": "Kurt Y. Liu",
    "contributor": None,
    "rationale": (
        "2022 Palgrave Macmillan text on supply chain analytics: the analytics maturity "
        "levels, the boundary between operations research and analytics, SMART goals for "
        "an analytics programme, forecasting and demand management, and inventory methods "
        "the repo already implements from better sources. Read in full on 2026-08-05/06; "
        "ingested PARTIALLY - only what the engine asserts or could assert and cannot cite."
    ),
    "norm_label": "liu: supply chain analytics (2022)",
    "repo": NS,
    "local_id": BOOK_ID,
    "known_errata": [dict(e) for e in KNOWN_ERRATA],
}


def snap(score: float | None) -> float | None:
    """Snap an off-rubric INFERRED score to the nearest allowed value (ties go LOWER)."""
    if score is None or score in RUBRIC:
        return score
    return min(RUBRIC, key=lambda r: (round(abs(r - score), 6), r))


def edge_key(source: str, target: str, relation: str | None) -> tuple:
    """Identity of an undirected edge; the graph declares multigraph=false."""
    return (frozenset((source, target)), relation)


def _rank(edge: dict) -> tuple:
    return (1 if edge.get("confidence") == "EXTRACTED" else 0, edge.get("confidence_score") or 0)


def build_extraction() -> tuple[dict[str, dict], list[dict]]:
    nodes: dict[str, dict] = {
        BOOK_ID: {**BOOK_NODE, "id": BOOK_ID, "source_file": BOOK_SLUG, "community": COMMUNITY},
    }
    for local_id, label, loc, rationale in CONCEPTS:
        if local_id in nodes:
            raise SystemExit(f"duplicate concept id in extraction: {local_id}")
        nodes[local_id] = {
            "id": local_id,
            "label": label,
            "file_type": "concept",
            "source_file": BOOK_SLUG,
            "source_location": loc,
            "source_url": None,
            "captured_at": None,
            "author": None,
            "contributor": None,
            "rationale": rationale,
            "norm_label": label.lower(),
            "repo": NS,
            "local_id": local_id,
            "community": COMMUNITY,
        }

    edges: list[dict] = []
    # El nodo libro referencia cada concepto que introduce: eso es lo que hace a cada
    # concepto citable contra esta fuente.
    for local_id, _label, loc, _r in CONCEPTS:
        edges.append({
            "relation": "references", "confidence": "EXTRACTED", "confidence_score": 1.0,
            "source_file": BOOK_SLUG, "source_location": loc, "weight": 1.0,
            "source": BOOK_ID, "target": local_id,
        })
    declared = set(nodes) | set(BRIDGES)
    for s, rel, t, conf, score in EDGES:
        for end in (s, t):
            if end not in declared:
                raise SystemExit(f"edge endpoint declared in neither CONCEPTS nor BRIDGES: {end}")
        edges.append({
            "relation": rel, "confidence": conf,
            "confidence_score": snap(score) if conf == "INFERRED" else score,
            "source_file": BOOK_SLUG, "source_location": None, "weight": 1.0,
            "source": s, "target": t,
        })
    return nodes, edges


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-apply even if already merged")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args()

    nodes, edges = build_extraction()
    print(f"extraction: {len(nodes)} nodes, {len(edges)} edges")

    base = json.loads(COMMITTED.read_text(encoding="utf-8"))
    base_links = base.get("links", base.get("edges", []))
    committed_ids = {n["id"] for n in base["nodes"] if "id" in n}

    def qualify(bare: str) -> str:
        return f"{NS}::{bare}"

    missing_bridges = [b for b in BRIDGES if qualify(b) not in committed_ids]
    if missing_bridges:
        print(
            "ABORT: BRIDGES ids absent from the committed graph (typo, or the concept was "
            f"renamed upstream): {', '.join(sorted(missing_bridges))}\n"
            "Left alone these would be added as new nodes carrying only this book's citation, "
            "quietly forking a concept that already exists under another id.",
            file=sys.stderr,
        )
        return 1

    taken = {n.get("community") for n in base["nodes"]}
    if COMMUNITY in taken and not args.dry_run:
        print(
            f"ABORT: community {COMMUNITY} is already in use. Pick the next free id: writing "
            "over an existing community merges two sources in the report with no count moving.",
            file=sys.stderr,
        )
        return 1

    if qualify(BOOK_ID) in committed_ids and not args.force:
        print(
            f"ABORT: {qualify(BOOK_ID)} is already in {COMMITTED.name} -- this book looks merged.\n"
            "Re-running would double every edge while the id-set guard still reported 'purely "
            "additive'. Restore the pre-merge graph (git checkout HEAD -- "
            "knowledge/scm-books/graph.json) and re-run, or pass --force.",
            file=sys.stderr,
        )
        return 1

    bridged: list[str] = []
    added: list[dict] = []
    for bare, n in nodes.items():
        qid = qualify(bare)
        if qid in committed_ids:
            bridged.append(bare)  # shared concept: keep the canonical node, edges form the bridge
            continue
        added.append({**n, "id": qid})

    merged_nodes = base["nodes"] + added
    merged_ids = {n["id"] for n in merged_nodes if "id" in n}

    seen: dict[tuple, dict] = {}
    for e in base_links:
        s, t = e.get("source"), e.get("target")
        if s is not None and t is not None:
            seen.setdefault(edge_key(s, t, e.get("relation")), e)

    new_links: list[dict] = []
    dropped_low_conf = dropped_dangling = collapsed = 0
    for e in edges:
        ne = {**e, "source": qualify(e["source"]), "target": qualify(e["target"])}
        if ne.get("confidence") == "INFERRED" and (ne.get("confidence_score") or 0) < MIN_INFERRED:
            dropped_low_conf += 1
            continue
        if ne["source"] not in merged_ids or ne["target"] not in merged_ids or ne["source"] == ne["target"]:
            dropped_dangling += 1
            continue
        key = edge_key(ne["source"], ne["target"], ne.get("relation"))
        prior = seen.get(key)
        if prior is not None:
            collapsed += 1
            if _rank(ne) > _rank(prior) and prior in new_links:
                new_links[new_links.index(prior)] = ne
                seen[key] = ne
            continue
        seen[key] = ne
        new_links.append(ne)

    merged_links = base_links + new_links
    out = {
        **{k: v for k, v in base.items() if k not in ("nodes", "links", "edges")},
        "nodes": merged_nodes,
        "links": merged_links,
    }

    new_ids = {n["id"] for n in added}
    touched_existing = sorted({
        end for e in new_links for end in (e["source"], e["target"]) if end not in new_ids
    })
    print()
    print(f"nodes  {len(base['nodes'])} -> {len(merged_nodes)}  (+{len(added)} new)")
    print(f"bridges  {len(touched_existing)} pre-existing canonical nodes gained an edge from this book "
          f"({len(bridged)} of them are concepts this book also defines)")
    print(f"edges  {len(base_links)} -> {len(merged_links)}  (+{len(new_links)}; dropped "
          f"{dropped_low_conf} low-confidence INFERRED, {dropped_dangling} dangling/self-loop, "
          f"collapsed {collapsed} duplicate parallel)")
    print(f"community  {COMMUNITY} (fresh; no re-cluster, so earlier citation measurements stay valid)")

    lost = committed_ids - merged_ids
    print()
    print(f"ID-SET GUARD: pre-existing ids lost in merge: {len(lost)}")
    if lost:
        print("  !! " + ", ".join(sorted(lost)[:20]))
        return 1
    print("  OK - every pre-existing node id survived (purely additive)")

    keys = [edge_key(e["source"], e["target"], e.get("relation")) for e in merged_links]
    dupes = len(keys) - len(set(keys))
    print(f"PARALLEL-EDGE GUARD: duplicate (endpoints, relation) links: {dupes}")
    if dupes:
        print("  !! graph declares multigraph=false; these will silently collapse on load")
        return 1
    print("  OK - no parallel links")

    orphans = [n["id"] for n in added
               if not any(n["id"] in (e["source"], e["target"]) for e in new_links)]
    print(f"ORPHAN GUARD: new nodes with no edge: {len(orphans)}")
    if orphans:
        print("  !! " + ", ".join(sorted(orphans)[:20]))
        return 1
    print("  OK - every new node is connected")

    uncited = [n["id"] for n in added
               if n["id"] != qualify(BOOK_ID)
               and not any(e["source"] == qualify(BOOK_ID) and e["target"] == n["id"]
                           for e in new_links)]
    print(f"CITATION GUARD: new concepts not referenced by the book node: {len(uncited)}")
    if uncited:
        print("  !! " + ", ".join(sorted(uncited)[:20]))
        return 1
    print("  OK - every new concept is citable back to this source")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    if BACKUP.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rotated = BACKUP.with_suffix(f".{stamp}.bak")
        shutil.move(str(BACKUP), str(rotated))
        print(f"rotated previous backup -> {rotated.name}")
    shutil.copy2(COMMITTED, BACKUP)
    COMMITTED.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"backup -> {BACKUP}")
    print(f"sample bridges: {[b.split('::')[-1] for b in touched_existing[:15]]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
