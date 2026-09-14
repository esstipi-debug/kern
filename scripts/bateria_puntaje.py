"""Emite el puntaje de la bateria de 20 tareas desde ``puntuar()``, nunca a mano.

El numero publicado en ``src/bateria/README.md`` y en
``documentation/KERN_BATERIA_20_TAREAS.md`` envejecio dos veces (7,5% -> 37,5% ->
65%) porque se tipeaba. Este script lo imprime en el formato exacto que esos
documentos usan, y ``tests/test_bateria_puntaje_publicado.py`` exige que el
documento diga lo mismo que el script. Correr desde la raiz del repo:

    python scripts/bateria_puntaje.py            # resumen + tabla por estado
    python scripts/bateria_puntaje.py --markdown # la tabla lista para pegar

Sin el import de ``src.bateria.comprobaciones`` el marcador da 0,0% en silencio
(ver el README de la bateria); por eso se importa aca, no en el llamador.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import src.bateria.comprobaciones  # noqa: E402,F401  (registra las comprobaciones)
from src.bateria.scorer import Puntaje, puntuar  # noqa: E402

ORDEN = ("cumple", "parcial", "no_cumple", "sin_dato", "fuera_de_alcance", "pendiente")


def resumen(p: Puntaje) -> str:
    """``65,00% · 13/20`` con coma decimal, como lo publican los documentos."""
    pct = f"{p.porcentaje:.2f}".replace(".", ",")
    puntos = f"{p.puntos:g}".replace(".", ",")
    return f"{pct}% · {puntos}/20"


def tabla(p: Puntaje) -> list[tuple[str, int, list[str]]]:
    por: dict[str, list[str]] = defaultdict(list)
    for r in p.resultados:
        por[r.estado.value].append(r.tarea_id)
    return [(estado, len(por[estado]), sorted(por[estado])) for estado in ORDEN if por.get(estado)]


def markdown(p: Puntaje) -> str:
    filas = ["| Estado | N | Tareas |", "|---|---|---|"]
    for estado, n, tareas in tabla(p):
        filas.append(f"| `{estado.upper()}` | {n} | {', '.join(tareas)} |")
    return "\n".join(filas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Puntaje de la bateria de 20 tareas, desde el harness.")
    parser.add_argument("--markdown", action="store_true", help="imprimir la tabla en markdown")
    args = parser.parse_args()
    p = puntuar()
    if args.markdown:
        # Texto para pegar en los documentos: lleva el punto medio que ellos usan,
        # asi que se fuerza utf-8 en vez de dejar que cp1252 lo rompa a mitad.
        sys.stdout.reconfigure(encoding="utf-8")
        print(resumen(p))
        print(markdown(p))
    else:
        # Consola: ASCII-only (regla del repo, Windows cp1252).
        print(resumen(p).replace("·", "-"))
        for estado, n, tareas in tabla(p):
            print(f"  {estado:<17} {n:>2}  {', '.join(tareas)}")
    print(f"  cobertura de comprobaciones: {p.cobertura * 100:.0f}%  techo segun la fuente: {p.techo_segun_fuente:g}%")


if __name__ == "__main__":
    main()
