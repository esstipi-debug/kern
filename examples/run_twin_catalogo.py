"""Twin sembrado con el catalogo real de un negocio: un grupo, cuatro escenarios.

    python examples/run_twin_catalogo.py --catalogo radiografia-mdlc-catalogo-2026-08-28.json
    python examples/run_twin_catalogo.py --catalogo cat.json --grupo Pescados --escenario corte_proveedor

Lo observable (SKU, precio, quiebre, categoria) entra tal cual; demanda y plazos son
priors declarados y se imprimen. Nada de esto mide la bodega de nadie.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jobs import twin_catalogo as tc  # noqa: E402


def _ascii(texto: object) -> str:
    """La consola de Windows (cp1252) revienta con acentos; los archivos siguen en UTF-8."""
    return str(texto).encode("ascii", errors="replace").decode("ascii")


def _escenarios(nombre: str) -> tuple[tc.Escenario, ...]:
    if nombre == "todos":
        return tc.ESCENARIOS_ESTRENO
    por_nombre = {e.nombre: e for e in tc.ESCENARIOS_ESTRENO}
    if nombre not in por_nombre:
        raise SystemExit(f"escenario desconocido: {nombre!r}; opciones: {', '.join(por_nombre)}, todos")
    return (por_nombre[nombre],)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogo", required=True, help="products.json de Shopify")
    ap.add_argument("--grupo", default="Pescados", help="product_type a simular (o 'todos')")
    ap.add_argument("--escenario", default="todos")
    ap.add_argument("--out", default="deliverables/twin_catalogo")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--base-demanda", type=float, default=8.0, dest="base_demanda")
    ap.add_argument("--periodos", type=int, default=364)
    ap.add_argument("--cliente", default="Mariscos De La Concha")
    args = ap.parse_args(argv)

    variantes, cobertura = tc.cargar_catalogo(args.catalogo)
    if args.grupo != "todos":
        variantes = tuple(v for v in variantes if v.grupo == args.grupo)
        if not variantes:
            raise SystemExit(f"el grupo {args.grupo!r} no existe; hay: {', '.join(g for g, _ in cobertura.grupos)}")
    hip = tc.Hipotesis(base_demanda_por_grupo=args.base_demanda, periods=args.periodos)

    print(f"{'escenario':18} {'fill':>7} {'dias quiebre':>13} {'venta en riesgo CLP':>20}")
    for esc in _escenarios(args.escenario):
        pls = tc.armar_payloads(variantes, hipotesis=hip, escenario=esc, seed=args.seed)
        rep = tc.consolidar(tc.correr(pls), variantes, cobertura=cobertura, hipotesis=hip)
        tc.escribir(rep, Path(args.out) / esc.nombre, client=args.cliente)
        fill = dict(rep.fill_por_grupo)
        dias = dict(rep.dias_quiebre_por_grupo)
        riesgo = dict(rep.venta_en_riesgo_por_grupo)
        for g in sorted(fill):
            print(f"{_ascii(esc.nombre):18} {fill[g]:7.1%} {dias[g]:13d} {riesgo[g]:20,.0f}  [{_ascii(g)}]")
    print(f"\ncobertura: sku declarado={cobertura.sku_declarado} no_declarado={cobertura.sku_no_declarado} "
          f"contradicho={cobertura.sku_contradicho} | en quiebre hoy={cobertura.quiebre_hoy}")
    print(f"salida en {_ascii(args.out)}/<escenario>/ (twin_deck.md, twin_*.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
