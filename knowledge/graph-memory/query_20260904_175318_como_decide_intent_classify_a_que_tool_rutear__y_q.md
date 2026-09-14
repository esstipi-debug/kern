---
type: "query"
date: "2026-09-04T17:53:18.519305+00:00"
question: "Como decide intent.classify a que tool rutear, y que pasa si dos empatan?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["classify()", "Tool", "build_default_registry()"]
---

# Q: Como decide intent.classify a que tool rutear, y que pasa si dos empatan?

## Answer

registry.match(brief) rankea por keywords y classify solo acepta VICTORIA ESTRICTA: top_score >= 1 AND top_score > second_score (scm_agent/intent.py:59). Un EMPATE no rutea: cae al LLM si hay provider disponible, y sin provider devuelve job_type=None con hasta 3 candidates para pedir aclaracion. Consecuencia de diseno, no accidente: dos tools del mismo dominio no pueden compartir ningun keyword suelto o se anulan mutuamente. Medido en el PR #322: el brief 'registro de procedimientos del ciclo s&op: cuales estan vencidos' empata 1-1 entre sop y procedure_audit y NO rutea -- y esa es la conducta correcta, porque el brief nombra las dos disciplinas con igual peso. Por eso procedure_audit se quedo con los sustantivos (registro, vencimiento, control documental) y procedure_draft con los verbos y los artefactos (job breakdown sheet, control plan, work instruction), y ninguno declara 'sop', 'draft', 'procedure' ni 'revision' a secas. Ojo: el keyword del tool de S&OP es 's&op' CON ampersand, asi que la palabra SOP sola no lo activa.

## Outcome

- Signal: useful

## Source Nodes

- classify()
- Tool
- build_default_registry()