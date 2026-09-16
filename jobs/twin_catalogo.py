"""Twin sembrado con el catalogo real de un negocio (fidelidad 3).

Lo observable del catalogo publico (SKU, precio, estado de stock, categoria) entra
tal cual al twin de red de ``jobs/digital_twin_job.py``; lo no observable (demanda,
plazos) entra como hipotesis declarada. Una red por grupo (``product_type`` como
proxy del proveedor), dos payloads por grupo (disponibles / quebradas hoy).

Regla de tres estados por campo: declarado / no_declarado / contradicho. Nunca dos.
Spec: docs/superpowers/specs/2026-09-16-twin-sembrado-catalogo-real-design.md
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jobs import digital_twin_job
from jobs.digital_twin_job import _DISRUPTION_KINDS, _MAX_CELLS, TwinReport

ESTADO_DECLARADO = "declarado"
ESTADO_NO_DECLARADO = "no_declarado"
ESTADO_CONTRADICHO = "contradicho"
GRUPO_OTROS = "Otros"
MIN_VARIANTES_POR_GRUPO = 5


@dataclass(frozen=True)
class Variante:
    """Una variante del catalogo, con su clave para el twin y el estado de su SKU."""

    clave: str
    sku: str | None
    variant_id: int
    producto: str
    grupo: str
    precio: float
    disponible: bool
    estado_sku: str


@dataclass(frozen=True)
class Cobertura:
    """La tabla de tres estados que el reporte imprime siempre."""

    sku_declarado: int
    sku_no_declarado: int
    sku_contradicho: int
    precio_declarado: int
    precio_excluido: int
    quiebre_hoy: int
    grupos: tuple[tuple[str, int], ...]


MAX_PRODUCTOS_POR_PAYLOAD = 250
NODO_CD = "CD-MDLC"
NODO_TIENDA = "TIENDA-WEB"


@dataclass(frozen=True)
class Escenario:
    """Un escenario del nucleo; kind=None es la base sin disrupcion."""

    nombre: str
    kind: str | None
    start: int = 90
    duration: int = 42
    magnitude: float = 2.0


ESCENARIOS_ESTRENO: tuple[Escenario, ...] = (
    Escenario("base", None),
    Escenario("corte_proveedor", "supplier_outage", start=90, duration=42),
    Escenario("plazo_estirado", "lead_time_spike", start=90, duration=30, magnitude=5.0),
    Escenario("pico_demanda", "demand_surge", start=90, duration=14, magnitude=2.0),
)


@dataclass(frozen=True)
class Hipotesis:
    """Lo no observable, declarado. Todo esto se imprime en el reporte."""

    base_demanda_por_grupo: float = 8.0   # unidades/dia por variante, antes de la escala ABC
    noise_cv: float = 0.25
    season_amplitude: float = 0.25
    season_period: int = 364
    lead_proveedor: int = 7               # dias proveedor -> CD
    lead_tienda: int = 1                  # dias CD -> tienda
    periods: int = 364                    # periodo = dia
    auto_size_factor: float = 1.0


@dataclass(frozen=True)
class PayloadGrupo:
    grupo: str
    disponibles: bool
    escenario: Escenario
    payload: dict


def _precio(valor: object) -> float | None:
    try:
        p = float(valor)
    except (TypeError, ValueError):
        return None
    return p if math.isfinite(p) and p > 0 else None


def cargar_catalogo(
    path: str | Path, *, min_variantes_por_grupo: int = MIN_VARIANTES_POR_GRUPO,
) -> tuple[tuple[Variante, ...], Cobertura]:
    """Lee un products.json de Shopify y clasifica cada variante en tres estados."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("el catalogo debe ser una lista de productos (products.json)")
    for p in data:
        if "variants" not in p:
            raise ValueError(f"producto {p.get('id')!r} sin campo 'variants'")

    crudas: list[dict] = []
    for p in data:
        for v in p["variants"]:
            crudas.append({
                "sku": (v.get("sku") or "").strip() or None,
                "variant_id": int(v["id"]),
                "producto": str(p.get("title", "")),
                "grupo": str(p.get("product_type") or GRUPO_OTROS).strip() or GRUPO_OTROS,
                "precio": _precio(v.get("price")),
                "disponible": bool(v.get("available")),
            })

    repetidos = {s for s, n in Counter(c["sku"] for c in crudas if c["sku"]).items() if n > 1}
    tam = Counter(c["grupo"] for c in crudas)
    chicos = {g for g, n in tam.items() if n < min_variantes_por_grupo}

    variantes: list[Variante] = []
    excluidas = 0
    for c in crudas:
        if c["precio"] is None:
            excluidas += 1
            continue
        if c["sku"] is None:
            estado, clave = ESTADO_NO_DECLARADO, f"id:{c['variant_id']}"
        elif c["sku"] in repetidos:
            estado, clave = ESTADO_CONTRADICHO, f"{c['sku']}#{c['variant_id']}"
        else:
            estado, clave = ESTADO_DECLARADO, c["sku"]
        grupo = GRUPO_OTROS if c["grupo"] in chicos else c["grupo"]
        variantes.append(Variante(
            clave=clave, sku=c["sku"], variant_id=c["variant_id"], producto=c["producto"],
            grupo=grupo, precio=c["precio"], disponible=c["disponible"], estado_sku=estado,
        ))

    estados = Counter(v.estado_sku for v in variantes)
    cobertura = Cobertura(
        sku_declarado=estados[ESTADO_DECLARADO],
        sku_no_declarado=estados[ESTADO_NO_DECLARADO],
        sku_contradicho=estados[ESTADO_CONTRADICHO],
        precio_declarado=len(variantes),
        precio_excluido=excluidas,
        quiebre_hoy=sum(1 for v in variantes if not v.disponible),
        grupos=tuple(sorted(Counter(v.grupo for v in variantes).items())),
    )
    return tuple(variantes), cobertura


def _nodos(grupo: str, hip: Hipotesis, *, vacios: bool) -> list[dict]:
    inicial = 0.0 if vacios else None
    return [
        {"name": f"PROVEEDOR-{grupo}", "kind": "supplier", "supplier": None,
         "lead_time": 0, "review_period": 1, "capacity": None, "initial_on_hand": None},
        {"name": NODO_CD, "kind": "dc", "supplier": f"PROVEEDOR-{grupo}",
         "lead_time": hip.lead_proveedor, "review_period": 1, "capacity": None,
         "initial_on_hand": inicial},
        {"name": NODO_TIENDA, "kind": "store", "supplier": NODO_CD,
         "lead_time": hip.lead_tienda, "review_period": 1, "capacity": None,
         "initial_on_hand": inicial},
    ]


def _disrupciones(grupo: str, esc: Escenario) -> list[dict]:
    if esc.kind is None:
        return []
    if esc.kind not in _DISRUPTION_KINDS:
        raise ValueError(f"escenario desconocido: {esc.kind!r}")
    target = {
        "supplier_outage": f"PROVEEDOR-{grupo}",
        "lead_time_spike": NODO_CD,
        "demand_surge": NODO_TIENDA,
    }[esc.kind]
    return [{"kind": esc.kind, "target": target, "start": esc.start,
             "duration": esc.duration, "magnitude": esc.magnitude}]


def _productos(variantes: list[Variante], hip: Hipotesis, seed: int) -> list[dict]:
    # Mismo mecanismo que digital_twin_job.prepare: escala ABC lognormal ordenada,
    # determinista por semilla. Es un prior, no una observacion.
    rng = np.random.default_rng(seed)
    factores = np.sort(rng.lognormal(mean=0.0, sigma=0.7, size=len(variantes)))[::-1]
    return [
        {"product_id": v.clave, "base": round(hip.base_demanda_por_grupo * float(f), 2)}
        for v, f in zip(variantes, factores)
    ]


def armar_payloads(
    variantes: tuple[Variante, ...] | list[Variante], *,
    hipotesis: Hipotesis, escenario: Escenario, seed: int = 42,
) -> tuple[PayloadGrupo, ...]:
    """Una red por grupo; dos payloads por grupo (disponibles / quebradas), sin vacios."""
    por_grupo: dict[str, list[Variante]] = {}
    for v in variantes:
        por_grupo.setdefault(v.grupo, []).append(v)
    demanda = {
        "trend": 0.0, "season_amplitude": hipotesis.season_amplitude,
        "season_period": hipotesis.season_period, "promo_every": 0, "promo_length": 1,
        "promo_uplift": 0.0, "noise_cv": hipotesis.noise_cv, "zero_prob": 0.0,
    }
    salida: list[PayloadGrupo] = []
    for i, grupo in enumerate(sorted(por_grupo)):
        for disponibles in (False, True):
            lote = [v for v in por_grupo[grupo] if v.disponible is disponibles]
            if not lote:
                continue
            if len(lote) > MAX_PRODUCTOS_POR_PAYLOAD:
                raise ValueError(
                    f"grupo {grupo!r}: {len(lote)} productos supera el techo de "
                    f"{MAX_PRODUCTOS_POR_PAYLOAD} por payload"
                )
            if len(lote) * 1 * hipotesis.periods > _MAX_CELLS:
                raise ValueError(f"grupo {grupo!r}: supera _MAX_CELLS ({_MAX_CELLS:,})")
            salida.append(PayloadGrupo(
                grupo=grupo, disponibles=disponibles, escenario=escenario,
                payload={
                    "nodes": _nodos(grupo, hipotesis, vacios=not disponibles),
                    "products": _productos(lote, hipotesis, seed + 1000 * i + int(disponibles)),
                    "periods": hipotesis.periods, "demand": demanda,
                    "disruptions": _disrupciones(grupo, escenario), "seed": seed,
                    "auto_size_factor": hipotesis.auto_size_factor,
                },
            ))
    return tuple(salida)


@dataclass(frozen=True)
class ResultadoGrupo:
    grupo: str
    disponibles: bool
    escenario: Escenario
    report: TwinReport


@dataclass(frozen=True)
class VarianteGolpeada:
    clave: str
    producto: str
    estado_sku: str
    unidades_perdidas: float
    venta_en_riesgo_clp: float


@dataclass(frozen=True)
class CatalogTwinReport:
    """Un escenario, todos los grupos, valorizado a precio de lista."""

    escenario: Escenario
    hipotesis: Hipotesis
    cobertura: Cobertura
    fill_por_grupo: tuple[tuple[str, float], ...]
    dias_quiebre_por_grupo: tuple[tuple[str, int], ...]
    venta_en_riesgo_clp: float
    venta_en_riesgo_por_grupo: tuple[tuple[str, float], ...]
    golpeadas: tuple[VarianteGolpeada, ...]
    demand_rows: tuple[tuple[int, str, str, float], ...]
    served_rows: tuple[tuple[int, str, str, float], ...]
    inventory_rows: tuple[tuple[int, str, str, float], ...]
    order_rows: tuple[tuple[int, str, str, float], ...]


def correr(payloads: tuple[PayloadGrupo, ...]) -> tuple[ResultadoGrupo, ...]:
    """Cada payload por el run() del twin, sin cambios."""
    return tuple(
        ResultadoGrupo(grupo=p.grupo, disponibles=p.disponibles, escenario=p.escenario,
                       report=digital_twin_job.run(p.payload))
        for p in payloads
    )


def consolidar(
    resultados: tuple[ResultadoGrupo, ...], variantes: tuple[Variante, ...] | list[Variante], *,
    cobertura: Cobertura, hipotesis: Hipotesis, top: int = 10,
) -> CatalogTwinReport:
    if not resultados:
        raise ValueError("sin resultados que consolidar")
    por_clave = {v.clave: v for v in variantes}
    escenario = resultados[0].escenario

    demand: list = []
    served: list = []
    inventory: list = []
    orders: list = []
    demandado: dict[str, float] = {}
    servido: dict[str, float] = {}
    quiebre_grupo: dict[str, int] = {}
    perdidas: dict[str, float] = {}
    for r in resultados:
        demand.extend(r.report.demand_rows)
        served.extend(r.report.served_rows)
        inventory.extend(r.report.inventory_rows)
        orders.extend(r.report.order_rows)
        quiebre_grupo[r.grupo] = quiebre_grupo.get(r.grupo, 0) + sum(
            k.stockout_periods for k in r.report.node_kpis if k.kind == "store"
        )
    dem_idx = {(t, p, s): u for t, p, s, u in demand}
    for t, p, s, u in served:
        falta = max(dem_idx[(t, p, s)] - u, 0.0)
        perdidas[p] = perdidas.get(p, 0.0) + falta
        g = por_clave[p].grupo
        demandado[g] = demandado.get(g, 0.0) + dem_idx[(t, p, s)]
        servido[g] = servido.get(g, 0.0) + u

    riesgo_var = {p: perdidas[p] * por_clave[p].precio for p in perdidas}
    riesgo_grupo: dict[str, float] = {}
    for p, clp in riesgo_var.items():
        g = por_clave[p].grupo
        riesgo_grupo[g] = riesgo_grupo.get(g, 0.0) + clp
    fill = {
        g: (servido[g] / demandado[g] if demandado[g] > 0 else 1.0) for g in demandado
    }
    golpeadas = tuple(
        VarianteGolpeada(clave=p, producto=por_clave[p].producto,
                         estado_sku=por_clave[p].estado_sku,
                         unidades_perdidas=round(perdidas[p], 2),
                         venta_en_riesgo_clp=round(riesgo_var[p], 0))
        for p in sorted(riesgo_var, key=lambda k: -riesgo_var[k])[:top]
        if riesgo_var[p] > 0
    )
    return CatalogTwinReport(
        escenario=escenario, hipotesis=hipotesis, cobertura=cobertura,
        fill_por_grupo=tuple(sorted((g, round(f, 4)) for g, f in fill.items())),
        dias_quiebre_por_grupo=tuple(sorted(quiebre_grupo.items())),
        venta_en_riesgo_clp=round(sum(riesgo_var.values()), 0),
        venta_en_riesgo_por_grupo=tuple(sorted((g, round(c, 0)) for g, c in riesgo_grupo.items())),
        golpeadas=golpeadas,
        demand_rows=tuple(demand), served_rows=tuple(served),
        inventory_rows=tuple(inventory), order_rows=tuple(orders),
    )
