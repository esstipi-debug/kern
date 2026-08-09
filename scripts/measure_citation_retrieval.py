"""Mide la ruta de citas de produccion: recuperacion lexica vs. semantica.

    PYTHONPATH=. python scripts/measure_citation_retrieval.py [reemplazo|union]

Corre ``_step_citations`` sobre TODAS las tools ancladas+registradas, dos veces:

  A) tal como esta hoy -- ``ground_citations_detailed`` -> ``KnowledgeBase.search``
     (solapamiento de tokens ponderado por IDF, puro lexico).
  B) con el pool de candidatos servido tambien por embeddings, en dos variantes:
       ``reemplazo``  el pool de 16 lo arma ``search_hybrid`` (lo semantico
                      compite por los mismos 16 slots y puede desplazar);
       ``union``      lo lexico se conserva entero y lo semantico se AGREGA
                      al final (no puede desplazar a nadie).

El re-ranking posterior y el gate (anclas / MAX_HOPS / MIN_CITATIONS /
exclusiones) quedan intactos en las tres corridas: lo unico que cambia es de
donde salen los candidatos. Las dos variantes existen para separar dos preguntas
que un swap directo confunde: *?ayuda el recall semantico?* y *?conviene
sacrificar slots lexicos por el?*.

Requiere el extra ``rag`` (``pip install .[rag]``), que NO esta en el entorno de
prod ni en requirements-dev.txt -- ver el veredicto en
``documentation/CITAS_LEXICO_VS_HIBRIDO.md``.
"""
from __future__ import annotations

import json
import sys

from scm_agent import citation_gate
from scm_agent.citation_gate import MIN_CITATIONS
from scm_agent.knowledge import KnowledgeBase
from scm_agent.packages import _CANDIDATE_POOL, _MAX_CITATIONS
from scm_agent.tools import build_default_registry

VARIANTE = sys.argv[1] if len(sys.argv) > 1 else "reemplazo"

# Espejo de tests/test_packages_citations.py: los ceros que un test fija.
CERO_INTENCIONAL = {"data_quality", "cycle_count", "whatif", "earned_value", "intake_scan"}
ANCHOR_ISLANDED = {"dea", "learning_curve", "slotting", "vehicle_routing", "org_friction_audit"}


def step_citations(kb, tool, tool_key):
    cands = kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=_CANDIDATE_POOL)
    return citation_gate.filter_citations(kb, tool_key, cands).kept[:_MAX_CITATIONS]


def clase(k: str) -> str:
    if k in ANCHOR_ISLANDED:
        return "islanded"
    if k in CERO_INTENCIONAL:
        return "cero-intencional"
    return "debe-citar"


def main() -> int:
    kb = KnowledgeBase()
    reg = build_default_registry()
    print(f"variante={VARIANTE}  embeddings_ready={kb._embedding_index.ready} "
          f"size={kb._embedding_index.size}")
    if not kb._embedding_index.ready:
        print("ABORT: sin indice de embeddings la comparacion no tiene sentido "
              "(pip install .[rag])")
        return 2

    keys = sorted(k for k in citation_gate.TOOL_CONCEPTS if k in reg._tools)
    print(f"tools ancladas + registradas: {len(keys)}  "
          f"debe-citar={sum(1 for k in keys if clase(k) == 'debe-citar')}  "
          f"cero-intencional={sum(1 for k in keys if clase(k) == 'cero-intencional')}  "
          f"islanded={sum(1 for k in keys if clase(k) == 'islanded')}\n")

    before = {k: step_citations(kb, reg.get(k), k) for k in keys}
    original_search = KnowledgeBase.search

    def pool_con_semantica(self, query, graph="both", limit=8):
        """No se puede parchear ``search`` con ``search_hybrid``: este la llama
        adentro y da recursion infinita. Se replica su merge sobre original_search."""
        if graph != "books":
            return original_search(self, query, graph=graph, limit=limit)
        if VARIANTE == "reemplazo":
            sem = self.search_semantic(query, graph=graph, limit=limit)
            kw = original_search(self, query, graph=graph, limit=limit)
            kw_ids = {c.id for c in kw}
            merged: dict[str, tuple[float, object]] = {}
            for concept, score in sem:
                merged[concept.id] = (score * (1.5 if concept.id in kw_ids else 1.0), concept)
            for concept in kw:
                merged.setdefault(concept.id, (0.3, concept))
            return [c for _, c in sorted(merged.values(), key=lambda x: -x[0])[:limit]]
        kw = original_search(self, query, graph=graph, limit=limit)
        vistos = {c.id for c in kw}
        return kw + [c for c, _ in self.search_semantic(query, graph=graph, limit=8)
                     if c.id not in vistos]

    KnowledgeBase.search = pool_con_semantica
    try:
        after = {k: step_citations(KnowledgeBase(), reg.get(k), k) for k in keys}
    finally:
        KnowledgeBase.search = original_search

    rompe, mejora, empeora, cambia = [], [], [], []
    for k in keys:
        b, a, c = before[k], after[k], clase(k)
        if c != "debe-citar":
            if a:
                rompe.append(f"{k} ({c}) dejo de ser cero: {list(a)}")
        elif len(b) >= MIN_CITATIONS and len(a) < MIN_CITATIONS:
            rompe.append(f"{k} perdio grounding: {len(b)}->{len(a)}")
        elif len(a) > len(b):
            mejora.append(f"{k} {len(b)}->{len(a)}")
        elif len(a) < len(b):
            empeora.append(f"{k} {len(b)}->{len(a)}")
        elif b != a:
            cambia.append(k)

    print("=== VEREDICTO ===")
    print(f"  tests que se romperian : {len(rompe)}")
    for r in rompe:
        print(f"     - {r}")
    print(f"  ganan citas            : {len(mejora)}  {mejora}")
    print(f"  pierden citas          : {len(empeora)}  {empeora}")
    print(f"  mismo n, otro contenido: {len(cambia)}  {cambia}")
    print(f"\n  citas totales          : {sum(map(len, before.values()))} -> "
          f"{sum(map(len, after.values()))}")

    print("\n=== DETALLE de todo lo que cambia ===")
    for k in keys:
        if before[k] == after[k]:
            continue
        print(f"\n--- {k}  [{clase(k)}]")
        for c in before[k] or ["(cero)"]:
            print(f"   ANTES   {c}")
        for c in after[k] or ["(cero)"]:
            print(f"   DESPUES {c}")

    with open(f"citation_measure_{VARIANTE}.json", "w", encoding="utf-8") as fh:
        json.dump({"before": {k: list(v) for k, v in before.items()},
                   "after": {k: list(v) for k, v in after.items()}}, fh,
                  ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
