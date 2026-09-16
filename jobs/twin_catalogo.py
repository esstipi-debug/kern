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
import pandas as pd

from jobs import digital_twin_job
from jobs.digital_twin_job import _DISRUPTION_KINDS, _MAX_CELLS, _START_DATE, TwinReport
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.sanitize import defuse_formula

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

    return tuple(variantes), cobertura_de(variantes, precio_excluido=excluidas)


def cobertura_de(
    variantes: tuple[Variante, ...] | list[Variante], *, precio_excluido: int = 0,
) -> Cobertura:
    """La tabla de tres estados para cualquier lista de variantes ya
    clasificadas -- todo el catalogo, o un subconjunto filtrado por grupo.

    ``precio_excluido`` no se puede recalcular a partir de ``variantes``
    porque las variantes excluidas por precio ya no estan en la lista; se
    declara aparte (default 0) para que un caller que filtra por grupo pueda
    seguir mostrando la exclusion del catalogo completo (C1) en vez de
    perderla."""
    estados = Counter(v.estado_sku for v in variantes)
    return Cobertura(
        sku_declarado=estados[ESTADO_DECLARADO],
        sku_no_declarado=estados[ESTADO_NO_DECLARADO],
        sku_contradicho=estados[ESTADO_CONTRADICHO],
        precio_declarado=len(variantes),
        precio_excluido=precio_excluido,
        quiebre_hoy=sum(1 for v in variantes if not v.disponible),
        grupos=tuple(sorted(Counter(v.grupo for v in variantes).items())),
    )


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
                    "disruptions": _disrupciones(grupo, escenario),
                    "seed": seed + 1000 * i + int(disponibles),
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
    arranco_vacia: bool = False   # C2: corrio en el payload initial_on_hand=0


@dataclass(frozen=True)
class CatalogTwinReport:
    """Un escenario, todos los grupos, valorizado a precio de lista."""

    escenario: Escenario
    hipotesis: Hipotesis
    cobertura: Cobertura
    n_variantes: int   # product_id distintos realmente simulados (demand_rows); C1
    fill_por_grupo: tuple[tuple[str, float], ...]
    variante_dias_quiebre_por_grupo: tuple[tuple[str, int], ...]   # I3: suma sobre variantes, no sobre dias
    # C2 / decision 5: fill, variante-dias en quiebre y venta en riesgo por
    # (grupo, arranco_vacia) -- separa "cuanto tardan en recuperarse las
    # quebradas de hoy" de "cuanto se quiebran las sanas".
    desglose_por_estado: tuple[tuple[str, bool, float, int, float], ...]
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
    # C2: el mismo desglose, pero separado por (grupo, arranco_vacia). Cada
    # ResultadoGrupo ya sabe de que payload vino (disponibles=False === el
    # payload de las quebradas de hoy, initial_on_hand=0 en CD y tienda).
    quiebre_estado: dict[tuple[str, bool], int] = {}
    demandado_estado: dict[tuple[str, bool], float] = {}
    servido_estado: dict[tuple[str, bool], float] = {}
    riesgo_estado: dict[tuple[str, bool], float] = {}
    perdidas: dict[str, float] = {}
    for r in resultados:
        demand.extend(r.report.demand_rows)
        served.extend(r.report.served_rows)
        inventory.extend(r.report.inventory_rows)
        orders.extend(r.report.order_rows)
        dias_grupo = sum(k.stockout_periods for k in r.report.node_kpis if k.kind == "store")
        quiebre_grupo[r.grupo] = quiebre_grupo.get(r.grupo, 0) + dias_grupo
        clave_estado = (r.grupo, not r.disponibles)   # arranco_vacia = not disponibles
        quiebre_estado[clave_estado] = quiebre_estado.get(clave_estado, 0) + dias_grupo
    dem_idx = {(t, p, s): u for t, p, s, u in demand}
    for t, p, s, u in served:
        v = por_clave[p]
        falta = max(dem_idx[(t, p, s)] - u, 0.0)
        perdidas[p] = perdidas.get(p, 0.0) + falta
        g = v.grupo
        demandado[g] = demandado.get(g, 0.0) + dem_idx[(t, p, s)]
        servido[g] = servido.get(g, 0.0) + u
        clave_estado = (g, not v.disponible)
        demandado_estado[clave_estado] = demandado_estado.get(clave_estado, 0.0) + dem_idx[(t, p, s)]
        servido_estado[clave_estado] = servido_estado.get(clave_estado, 0.0) + u

    riesgo_var = {p: perdidas[p] * por_clave[p].precio for p in perdidas}
    riesgo_grupo: dict[str, float] = {}
    for p, clp in riesgo_var.items():
        v = por_clave[p]
        riesgo_grupo[v.grupo] = riesgo_grupo.get(v.grupo, 0.0) + clp
        clave_estado = (v.grupo, not v.disponible)
        riesgo_estado[clave_estado] = riesgo_estado.get(clave_estado, 0.0) + clp
    fill = {
        g: (servido[g] / demandado[g] if demandado[g] > 0 else 1.0) for g in demandado
    }
    desglose_por_estado = tuple(
        (g, vacia,
         round(servido_estado.get((g, vacia), 0.0) / demandado_estado[(g, vacia)], 4)
         if demandado_estado.get((g, vacia), 0.0) > 0 else 1.0,
         quiebre_estado[(g, vacia)],
         round(riesgo_estado.get((g, vacia), 0.0), 0))
        for (g, vacia) in sorted(quiebre_estado)
    )
    golpeadas = tuple(
        VarianteGolpeada(clave=p, producto=por_clave[p].producto,
                         estado_sku=por_clave[p].estado_sku,
                         unidades_perdidas=round(perdidas[p], 2),
                         venta_en_riesgo_clp=round(riesgo_var[p], 0),
                         arranco_vacia=not por_clave[p].disponible)
        for p in sorted(riesgo_var, key=lambda k: -riesgo_var[k])[:top]
        if riesgo_var[p] > 0
    )
    variantes_simuladas = {p for _, p, _, _ in demand}
    return CatalogTwinReport(
        escenario=escenario, hipotesis=hipotesis, cobertura=cobertura,
        n_variantes=len(variantes_simuladas),
        fill_por_grupo=tuple(sorted((g, round(f, 4)) for g, f in fill.items())),
        variante_dias_quiebre_por_grupo=tuple(sorted(quiebre_grupo.items())),
        desglose_por_estado=desglose_por_estado,
        venta_en_riesgo_clp=round(sum(riesgo_var.values()), 0),
        venta_en_riesgo_por_grupo=tuple(sorted((g, round(c, 0)) for g, c in riesgo_grupo.items())),
        golpeadas=golpeadas,
        demand_rows=tuple(demand), served_rows=tuple(served),
        inventory_rows=tuple(inventory), order_rows=tuple(orders),
    )


FRASE_APERTURA = "Esto es una hipótesis sobre su catálogo, no una medición de su bodega."


_IMPACTO_COBERTURA = "lo que entró del catálogo tal cual, y lo que es hipótesis"


def _findings_cobertura(c: Cobertura) -> list[Finding]:
    """Una Finding por fila de la tabla de tres estados: legible en el bullet
    list que ``Deliverable.to_markdown`` realmente renderiza (nunca una tabla
    Markdown real, porque cada Finding sale como una unica linea `- **titulo**
    - detalle`)."""
    return [
        Finding("Cobertura · SKU",
                f"declarado={c.sku_declarado} · no_declarado={c.sku_no_declarado} · "
                f"contradicho={c.sku_contradicho}", impact=_IMPACTO_COBERTURA),
        Finding("Cobertura · precio",
                f"declarado={c.precio_declarado} · no_declarado={c.precio_excluido} "
                "(excluidas del catálogo completo) · contradicho=0", impact=_IMPACTO_COBERTURA),
        Finding("Cobertura · en quiebre hoy",
                f"declarado={c.quiebre_hoy} · no_declarado=0 · contradicho=0", impact=_IMPACTO_COBERTURA),
        Finding("Cobertura · proveedor",
                "proveedor: no declarado; agrupado por categoría (declarado=0 · no_declarado=todos · "
                "contradicho=0)", impact=_IMPACTO_COBERTURA),
        Finding("Cobertura · demanda y lead time",
                "declarado=0 · no_declarado=todos (prior) · contradicho=0", impact=_IMPACTO_COBERTURA),
        Finding("Cobertura · peso / unidad",
                "declarado=0 · no_declarado=todos (la unidad es la variante) · contradicho=0",
                impact=_IMPACTO_COBERTURA),
    ]


def _seccion_hipotesis(h: Hipotesis) -> str:
    """Una sola linea (sin `##` ni saltos) con las cuatro hipotesis, porque
    ``Finding.detail`` sale como una unica linea de bullet, nunca una
    seccion propia con su encabezado."""
    bullets = (
        f"demanda base por grupo: {h.base_demanda_por_grupo} unidades/día, escala ABC lognormal (σ 0,7)",
        f"ruido (CV): {h.noise_cv} · estacionalidad: ±{h.season_amplitude:.0%} cada {h.season_period} días",
        f"lead time proveedor → CD: {h.lead_proveedor} días (no declarado — prior) · "
        f"CD → tienda: {h.lead_tienda} día(s)",
        f"horizonte: {h.periods} días · dimensionado automático: {h.auto_size_factor}×",
    )
    return " · ".join(bullets)


def _linea_desglose(desglose: tuple[tuple[str, bool, float, int, float], ...]) -> str:
    """Una sola linea (mismo estilo que ``_seccion_hipotesis``): por grupo,
    fill y venta en riesgo de "arranco vacia" (quiebre publicado hoy) junto
    al de "sana", para que se puedan comparar de un vistazo (C2 / decision 5)."""
    por_grupo: dict[str, dict[bool, tuple[float, int, float]]] = {}
    for g, vacia, fill, dias, venta in desglose:
        por_grupo.setdefault(g, {})[vacia] = (fill, dias, venta)
    partes = []
    for g in sorted(por_grupo):
        estados = por_grupo[g]
        segmentos = []
        for vacia, etiqueta in ((True, "arrancó vacía"), (False, "sana")):
            if vacia in estados:
                fill, dias, venta = estados[vacia]
                segmentos.append(
                    f"{etiqueta}: fill {fill:.1%}, {dias} variante-días en quiebre, "
                    f"venta en riesgo CLP {venta:,.0f}"
                )
        partes.append(f"{g} — " + " · ".join(segmentos))
    return " || ".join(partes)


def deck(report: CatalogTwinReport, *, client: str = "Client", lectura: str) -> Deliverable:
    esc = report.escenario
    fills = dict(report.fill_por_grupo)
    peor = min(fills, key=fills.get)
    summary = (
        f"{FRASE_APERTURA} Escenario '{esc.nombre}' sobre {report.n_variantes} "
        f"variantes en {len(fills)} grupo(s), {report.hipotesis.periods} días: venta en riesgo a "
        f"precio de lista CLP {report.venta_en_riesgo_clp:,.0f}; el grupo más golpeado es "
        f"{peor} con fill {fills[peor]:.1%}."
    )
    findings = [
        *_findings_cobertura(report.cobertura),
        Finding("Hipótesis de demanda (no observadas)", _seccion_hipotesis(report.hipotesis),
                impact="cambiar cualquiera cambia el número; por eso están impresas"),
        Finding("Quebradas de hoy vs sanas", _linea_desglose(report.desglose_por_estado),
                impact="cuanto tardan en recuperarse las quebradas de hoy vs. cuanto se quiebran las sanas"),
    ]
    for g in report.golpeadas[:5]:
        detalle = f"{g.unidades_perdidas:,.0f} unidades no servidas · CLP {g.venta_en_riesgo_clp:,.0f}"
        if g.arranco_vacia:
            detalle += " · arrancó vacía (quiebre publicado hoy)"
        findings.append(Finding(
            f"{g.producto} ({g.clave}, SKU {g.estado_sku})", detalle,
            impact="venta en riesgo a precio de lista; no es margen ni pérdida contable",
        ))
    kpis = [
        Kpi("Venta en riesgo a precio de lista (CLP)", f"{report.venta_en_riesgo_clp:,.0f}",
            rationale="unidades no servidas × precio de lista de la variante"),
        *[Kpi(f"Fill {g}", f"{f:.1%}", target="≥ 95%") for g, f in report.fill_por_grupo],
        *[Kpi(f"Variante-días en quiebre {g}", str(d),
              rationale="suma sobre variantes: una variante vacía un día cuenta 1")
          for g, d in report.variante_dias_quiebre_por_grupo],
        *[Kpi(f"Fill {g} · {'arrancaron vacías' if vacia else 'sanas'}", f"{fill:.1%}")
          for g, vacia, fill, _, _ in report.desglose_por_estado],
    ]
    fuentes = [
        DataSource("SKU, precio, estado de stock, categoría", "products.json público del catálogo", lectura),
        DataSource("demanda, lead times", "prior declarado (no observado)", "por corrida"),
    ]
    return Deliverable(
        title=f"Twin del catálogo — escenario {esc.nombre}",
        client=client, summary=summary, findings=tuple(findings), kpis=tuple(kpis),
        data_sources=tuple(fuentes),
        recommendations=(
            "Validar la demanda base con un mes de ventas reales antes de usar cualquier número de este deck.",
            "Si el corte de proveedor domina, la decisión es cobertura de stock del grupo, no de una variante.",
        ),
        confidence=0.3,
        residual="Proveedor real, demanda y plazos no observados; los quiebres publicados son estado de publicación, no inventario.",
    )


def _con_fecha(rows, value_col: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["period", "product_id", "location", value_col])
    inicio = pd.Timestamp(_START_DATE)
    df.insert(0, "date", df.pop("period").map(lambda t: (inicio + pd.Timedelta(days=int(t))).date().isoformat()))
    # I4: product_id/location vienen del catalogo real (SKU, titulo) y salian
    # crudos via to_csv, saltandose la defensa contra CSV-injection que el
    # resto del repo aplica (src/export.write_summary_csv ya la usa).
    df["product_id"] = df["product_id"].map(defuse_formula)
    df["location"] = df["location"].map(defuse_formula)
    return df


def escribir(
    report: CatalogTwinReport, out_dir: str | Path, *, client: str = "Client", lectura: str,
) -> dict[str, Path]:
    """Escribe el deck y los mismos CSV que ``jobs/digital_twin_job.py`` (demanda,
    servido, inventario, ordenes) mas la cobertura y las mas golpeadas, todo con
    fecha diaria (``_START_DATE``) y clave real de variante. Los archivos llevan
    el prefijo ``twin_catalogo_`` (M9) para no colisionar con los ``twin_*.csv``
    del twin generico en el mismo ``deliverables/``; las claves del dict devuelto
    (``paths``) no cambian. ``lectura`` no tiene default: la procedencia del
    catalogo (I5) se declara en cada corrida, nunca se inventa."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for key, col, rows in (
        ("demand_history", "units", report.demand_rows),
        ("served", "units_served", report.served_rows),
        ("inventory", "on_hand", report.inventory_rows),
        ("orders", "order_qty", report.order_rows),
    ):
        p = d / f"twin_catalogo_{key}.csv"
        _con_fecha(rows, col).to_csv(p, index=False)
        paths[key] = p
    c = report.cobertura
    paths["cobertura"] = write_summary_csv([
        {"campo": "sku", "declarado": c.sku_declarado, "no_declarado": c.sku_no_declarado, "contradicho": c.sku_contradicho},
        {"campo": "precio", "declarado": c.precio_declarado, "no_declarado": c.precio_excluido, "contradicho": 0},
        {"campo": "quiebre_hoy", "declarado": c.quiebre_hoy, "no_declarado": 0, "contradicho": 0},
    ], d / "twin_catalogo_cobertura.csv")
    paths["golpeadas"] = write_summary_csv([
        {"clave": g.clave, "producto": g.producto, "estado_sku": g.estado_sku,
         "unidades_perdidas": g.unidades_perdidas, "venta_en_riesgo_clp": g.venta_en_riesgo_clp,
         "arranco_vacia": g.arranco_vacia}
        for g in report.golpeadas
    ], d / "twin_catalogo_golpeadas.csv")
    deck_path = d / "twin_catalogo_deck.md"
    deck_path.write_text(deck(report, client=client, lectura=lectura).to_markdown(), encoding="utf-8")
    paths["deck"] = deck_path
    return paths
