"""Mide el ruteo de intencion: reglas por palabra clave contra el decisor tipado (Jev).

`scm_agent/intent.py` rutea por palabras clave y solo consulta al decisor cuando las
reglas empatan o no encuentran nada. Este script contesta las tres preguntas que
deciden si eso esta bien:

1. **Cuanto resuelven las reglas, y cuanto aciertan cuando resuelven.** Un ruteo por
   palabra clave equivocado sale con confianza 1.0 y nunca llega al decisor: el
   decisor no lo puede arreglar.
2. **Cuanto acierta el decisor donde actua** (los briefs que las reglas no resuelven),
   y que cobertura deja cada umbral (`MIN_DECISION_CONFIDENCE` es uno de ellos).
3. **Si la confianza del decisor esta calibrada** (Brier y ECE sobre todos los briefs).
   El sistema pide confirmacion humana segun ese numero, asi que una confianza no
   calibrada es una decision de producto equivocada.

    PYTHONPATH=. python scripts/eval_intent_decider.py --briefs examples/intent_briefs_sample.jsonl
    PYTHONPATH=. python scripts/eval_intent_decider.py --briefs mis_briefs.jsonl --json salida.json

Cada linea del JSONL es `{"brief": ..., "expected": <clave de tool>}`. La muestra que
viene con el repo tiene 12 briefs etiquetados a mano, y es para ver que el script
corre, no para concluir nada: la medicion que vale se hace sobre briefs REALES de
clientes. Sin `TYPESAFE_API_KEY` solo se mide la parte de palabras clave. Con la clave,
cada brief se le manda a TypeSafe, o sea a un tercero: no correr sobre briefs con PII.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from nosilo_core import llm
from scm_agent import intent, tools

THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9)
ECE_BINS = 10


def load_briefs(path: Path, keys: set[str]) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("expected") not in keys:
            raise SystemExit(f"{path}:{n}: expected {row.get('expected')!r} is not a registered tool key")
        rows.append(row)
    if not rows:
        raise SystemExit(f"{path}: no briefs")
    return rows


def _ratio(num: int, den: int) -> float | None:
    return num / den if den else None


def keyword_report(rows: list[dict], reg) -> tuple[dict, list[bool]]:
    resolved = correct = 0
    ambiguous = []
    for row in rows:
        res = intent.classify(row["brief"], reg, llm.RulesFallback())
        ambiguous.append(res.job_type is None)
        if res.job_type is not None:
            resolved += 1
            correct += res.job_type == row["expected"]
            row["keyword"] = res.job_type
    return {
        "n": len(rows),
        "resolved": resolved,
        "resolved_rate": _ratio(resolved, len(rows)),
        "accuracy_when_resolved": _ratio(correct, resolved),
        "confidently_wrong": resolved - correct,
    }, ambiguous


def ece(pairs: list[tuple[float, bool]], bins: int = ECE_BINS) -> float:
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [(c, ok) for c, ok in pairs if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if inside:
            gap = abs(statistics.fmean(c for c, _ in inside) - statistics.fmean(ok for _, ok in inside))
            total += len(inside) / len(pairs) * gap
    return total


def decider_report(rows: list[dict], ambiguous: list[bool], reg, decider) -> dict:
    pairs, latencies, errors = [], [], 0
    top3 = 0
    for row in rows:
        t0 = time.perf_counter()
        try:
            decision = intent._decide(decider, row["brief"], reg)
        except Exception as exc:  # noqa: BLE001 -- se cuenta, no se esconde
            errors += 1
            row["decider_error"] = repr(exc)
            continue
        latencies.append(time.perf_counter() - t0)
        ok = decision.choice == row["expected"]
        row["decider"] = {"choice": decision.choice, "confidence": decision.confidence}
        pairs.append((decision.confidence, ok))
        top3 += row["expected"] in decision.ranked()[:3]

    acting = [(row, amb) for row, amb in zip(rows, ambiguous) if amb and "decider" in row]
    by_threshold = {}
    for t in THRESHOLDS:
        routed = [row for row, _ in acting if row["decider"]["confidence"] >= t]
        hits = sum(row["decider"]["choice"] == row["expected"] for row in routed)
        by_threshold[str(t)] = {
            "coverage": _ratio(len(routed), len(acting)),
            "accuracy_when_routed": _ratio(hits, len(routed)),
        }
    return {
        "answered": len(pairs),
        "errors": errors,
        "accuracy_all": _ratio(sum(ok for _, ok in pairs), len(pairs)),
        "top3_recall_all": _ratio(top3, len(pairs)),
        "acting_on": len(acting),
        "accuracy_where_it_acts": _ratio(sum(r["decider"]["choice"] == r["expected"] for r, _ in acting), len(acting)),
        "by_threshold": by_threshold,
        "brier": statistics.fmean((c - ok) ** 2 for c, ok in pairs) if pairs else None,
        "ece": ece(pairs) if pairs else None,
        "latency_p50_s": statistics.median(latencies) if latencies else None,
        "latency_p95_s": sorted(latencies)[int(0.95 * (len(latencies) - 1))] if latencies else None,
    }


def _fmt(value) -> str:
    return "n/a" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--briefs", type=Path, required=True)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)

    reg = tools.build_default_registry()
    rows = load_briefs(args.briefs, {t.key for t in reg.list()})
    kw, ambiguous = keyword_report(rows, reg)
    out = {"briefs": str(args.briefs), "keyword": kw, "decider": None, "rows": rows}

    print(f"keyword router over {kw['n']} briefs")
    print(f"  resolved            {kw['resolved']} ({_fmt(kw['resolved_rate'])})")
    print(f"  accuracy resolved   {_fmt(kw['accuracy_when_resolved'])}")
    print(f"  confidently wrong   {kw['confidently_wrong']}  (never reach the decider)")

    decider = llm.get_decider()
    if decider is None:
        print("decider: TYPESAFE_API_KEY not set -- keyword part only")
    else:
        dr = decider_report(rows, ambiguous, reg, decider)
        out["decider"] = dr
        print(f"decider over {dr['answered']} briefs ({dr['errors']} errors)")
        print(f"  accuracy (all)      {_fmt(dr['accuracy_all'])}   top-3 recall {_fmt(dr['top3_recall_all'])}")
        print(f"  where it acts       {dr['acting_on']} briefs, accuracy {_fmt(dr['accuracy_where_it_acts'])}")
        for t, cell in dr["by_threshold"].items():
            mark = "  <- MIN_DECISION_CONFIDENCE" if float(t) == intent.MIN_DECISION_CONFIDENCE else ""
            print(f"  threshold {t}       coverage {_fmt(cell['coverage'])}  accuracy {_fmt(cell['accuracy_when_routed'])}{mark}")
        print(f"  calibration         brier {_fmt(dr['brier'])}  ece {_fmt(dr['ece'])}")
        print(f"  latency             p50 {_fmt(dr['latency_p50_s'])} s  p95 {_fmt(dr['latency_p95_s'])} s")

    if args.json:
        args.json.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
