"""Corre el barrido de neteo y emite la tabla que publica ``documentation/KERN_BARRIDO_NETEO.md``.

El barrido contesta la pregunta que la bateria no hace: T05 puntua el MEJOR de tres
entregables de reposicion ("¿Kern puede?"), y esto sondea los CATORCE emisores del
registro uno por uno ("¿cual NO puede?"). Correr desde la raiz del repo:

    python scripts/barrido_neteo.py              # tabla + evidencia, consola ASCII
    python scripts/barrido_neteo.py --markdown   # los dos bloques listos para pegar

Tarda unos segundos: cada sonda corre su emisor DOS veces (con y sin en-transito) y
compara las cantidades. Ninguna sonda mira nombres de campo -- ese es el punto.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.bateria.barrido_neteo import (  # noqa: E402
    EMISORES,
    NO_EMISORES,
    SondaNeteo,
    barrer,
)

#: El orden en que se lee la tabla: primero la brecha, despues lo que falta probar,
#: despues lo que no tiene por donde, y al final lo que ya esta cerrado.
ORDEN = ("no_netea", "sin_sonda", "sin_canal", "netea")

_VEREDICTO = {
    "netea": "SI descuenta",
    "no_netea": "NO descuenta",
    "sin_sonda": "sin sonda",
    "sin_canal": "sin canal",
}


def resumen(sondas: tuple[SondaNeteo, ...]) -> str:
    """``7/14 emisores descuentan el en-transito`` -- la linea de arriba del documento."""
    netean = sum(1 for s in sondas if s.estado == "netea")
    return (f"{netean}/{len(sondas)} emisores descuentan el en-transito "
            f"({len(NO_EMISORES)} tools no emiten cantidad a pedir)")


def filas(sondas: tuple[SondaNeteo, ...]) -> list[SondaNeteo]:
    return sorted(sondas, key=lambda s: (ORDEN.index(s.estado), s.tool))


def markdown(sondas: tuple[SondaNeteo, ...]) -> str:
    """La tabla de clasificacion. Es la que ``tests/test_barrido_neteo.py`` congela.

    Lleva el veredicto y NO los numeros: la evidencia numerica va en su propio bloque,
    para que un cambio de motor de pronostico mueva la evidencia sin poner rojo el
    vector de estados, que es lo que de verdad hay que revisar a proposito.
    """
    out = ["| Tool | Emite | Canal de en-transito | Veredicto |", "|---|---|---|---|"]
    for s in filas(sondas):
        canal = s.campo_en_transito or "-"
        out.append(f"| `{s.tool}` | {s.emite} | {canal} | {_VEREDICTO[s.estado]} |")
    return "\n".join(out)


def evidencia_markdown(sondas: tuple[SondaNeteo, ...]) -> str:
    """Las dos corridas de cada sonda, o la razon por la que no hay canal."""
    return "\n".join(f"- **`{s.tool}`** ({s.entregable}) -- {s.prueba}" for s in filas(sondas))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Barrido de neteo: que emisor descuenta el en-transito y cual no.")
    parser.add_argument("--markdown", action="store_true", help="imprimir para pegar en el doc")
    parser.add_argument("--tool", action="append", choices=sorted(EMISORES),
                        help="sondear solo estos tools (repetible)")
    args = parser.parse_args()

    sondas = barrer(args.tool)
    if args.markdown:
        # El documento lleva el punto medio; se fuerza utf-8 en vez de dejar que
        # cp1252 lo rompa a mitad.
        sys.stdout.reconfigure(encoding="utf-8")
        print(resumen(sondas))
        print()
        print(markdown(sondas))
        print()
        print(evidencia_markdown(sondas))
        return

    # Consola: ASCII-only (regla del repo, Windows cp1252).
    print(resumen(sondas))
    for s in filas(sondas):
        print(f"  {_VEREDICTO[s.estado]:<13} {s.tool:<24} {s.campo_en_transito or '-'}")
        print(f"                {s.prueba}")


if __name__ == "__main__":
    main()
