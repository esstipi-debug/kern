"""Auditoría de parámetros de cliente -- si el CONTEXTO alcanza, y qué preguntar.

El espejo de ``src.intake_scan`` sobre la otra mitad de la entrada: aquel audita
si el DATO alcanza, este audita si los PARAMETROS de negocio alcanzan. Puro y sin
dependencias: nada toca disco, nada llama a un LLM, y dos corridas sobre la misma
entrada devuelven exactamente el mismo resultado en el mismo orden.

El catálogo está indexado POR PARAMETRO y no por herramienta: el parámetro es del
dominio, y duplicarlo por herramienta es la repetición que produjo cinco
instancias del mismo defecto en intake_scan.
"""

from __future__ import annotations

from dataclasses import dataclass

# Las cuatro areas de src.hats.HAT_KEYS, copiadas a proposito en vez de
# importadas: src.hats arrastra numpy y scipy por su cadena de imports, y este
# modulo es puro y liviano por contrato. La copia NO puede derivar --
# tests/test_discovery_brief.py asierta igualdad contra src.hats.HAT_KEYS, donde
# importar scipy no cuesta nada.
_HAT_KEYS = ("comprador", "planner", "cfo", "comercial")


@dataclass(frozen=True)
class ParamSpec:
    """Un parámetro del dominio: qué preguntar, qué es sano, y qué se rompe sin él."""

    name: str
    question: str
    unit: str
    low: float
    high: float
    moves: str
    # (tool_key, valor, sitio en el código). TUPLA a propósito: el mismo parámetro
    # tiene distinto default según quién lo lea (order_cost vale 100.0 en
    # simulation_job y 75 según el docstring de client_profile). Un default único
    # mentiría en `moves`.
    defaults: tuple[tuple[str, float, str], ...] = ()
    # Sub-claves de un parámetro granular (hoy solo objection_thresholds). Vacío
    # => el parámetro es escalar y "presente" sí es un booleano.
    subkeys: tuple[str, ...] = ()


# Parámetros que al menos una herramienta declara en `required_client_params`.
# Estos producen veredictos que BLOQUEAN.
CATALOG: tuple[ParamSpec, ...] = (
    ParamSpec(
        name="holding_rate",
        question="¿Qué porcentaje anual del valor del inventario les cuesta mantenerlo "
                 "(capital, depósito, seguro, obsolescencia)?",
        unit="fracción anual",
        low=0.05, high=0.60,
        moves="El costo de mantener y, con él, el ahorro anual de la portada.",
        defaults=(
            ("inventory_optimization", 0.25, "jobs/inventory_optimization.py:126"),
            ("inventory_optimization", 0.25, "src/client_profile.py (docstring)"),
            ("webapp", 0.22, "webapp/app.py:510"),
        ),
    ),
    ParamSpec(
        name="service_level",
        question="¿Qué probabilidad de NO quebrar stock durante el lead time quieren "
                 "sostener por ciclo?",
        unit="fracción",
        low=0.50, high=0.999,
        moves="El z del stock de seguridad: mueve el inventario objetivo de toda la red.",
        defaults=(
            ("flow_map", 0.95, "jobs/flow_map_job.py:163"),
            ("inventory_optimization", 0.95, "jobs/inventory_optimization.py:125"),
            ("inventory_optimization", 0.95, "src/client_profile.py (docstring)"),
            ("multi_echelon", 0.95, "jobs/multi_echelon_job.py:31"),
        ),
    ),
    ParamSpec(
        name="order_cost",
        question="¿Cuánto les cuesta emitir y recibir UNA orden de compra, todo incluido?",
        unit="usd/pedido",
        low=1.0, high=5000.0,
        moves="El lote económico y la frecuencia de reposición de toda la red.",
        defaults=(
            ("batch", 1000.0, "src/batch.py:28"),
            ("hats", 75.0, "src/hats.py:90"),
            ("inventory_optimization", 75.0, "jobs/inventory_optimization.py:127"),
            ("powerbi_export", 1000.0, "src/powerbi_export.py:56"),
            ("powerbi_export", 1000.0, "src/powerbi_export.py:249"),
            ("simulation", 100.0, "jobs/simulation_job.py:31"),
            ("webapp", 80.0, "webapp/app.py:509"),
        ),
    ),
    ParamSpec(
        name="goodwill",
        question="Además del margen perdido, ¿cuánto les cuesta en reputación cada "
                 "unidad que no pudieron entregar?",
        unit="usd/unidad faltante",
        low=0.0, high=10000.0,
        moves="El ratio crítico del newsvendor: un cero asumido baja la cantidad "
              "óptima de TODOS los SKUs a la vez.",
        defaults=(("newsvendor", 0.0, "jobs/newsvendor_job.py:93"),),
    ),
    ParamSpec(
        name="objection_thresholds",
        question="Por cada área (comprador, planner, CFO, comercial): ¿cuánto puede "
                 "empeorar su indicador propio antes de que la decisión se caiga?",
        unit="unidades del KPI de cada área",
        low=0.0, high=1_000_000.0,
        moves="Qué áreas pueden evaluarse en el cruce de impacto; sin umbral, el "
              "área sale NO_EVALUABLE.",
        defaults=(),
        subkeys=_HAT_KEYS,
    ),
)

# Parámetros que el motor usa con default silencioso y NINGUNA herramienta declara.
# No bloquean nada: son el backlog de la tanda 2, y existen para que el reporte
# tenga un denominador honesto en vez de una lista de deseos.
UNDECLARED_USE: tuple[ParamSpec, ...] = (
    ParamSpec(
        name="review_period",
        question="¿Cada cuántos días revisan el stock para decidir una reposición?",
        unit="días",
        low=1.0, high=90.0,
        moves="El horizonte de riesgo del (R,S): un período asumido de 1 día "
              "subestima el stock de seguridad.",
        defaults=(
            ("multi_echelon", 1.0, "jobs/multi_echelon_job.py:32"),
            ("simulation", 1.0, "jobs/simulation_job.py:33"),
        ),
    ),
    ParamSpec(
        name="backorder_cost",
        question="¿Cuánto les cuesta dejar una unidad pendiente hasta poder entregarla?",
        unit="usd/unidad/período",
        low=0.0, high=10000.0,
        moves="El balance faltante-vs-exceso: impacto de costo de escasez vs exceso.",
        defaults=(
            ("batch", 50.0, "src/batch.py:29"),
            ("powerbi_export", 50.0, "src/powerbi_export.py:57"),
            ("powerbi_export", 50.0, "src/powerbi_export.py:250"),
            ("simulation", 5.0, "jobs/simulation_job.py:32"),
        ),
    ),
    ParamSpec(
        name="lead_time_days",
        question="Cuando el CSV no lo trae por SKU, ¿cuántos días tarda el "
                 "reabastecimiento típico?",
        unit="días",
        low=1.0, high=365.0,
        moves="El plazo sobre el que se dimensiona el stock de seguridad.",
        defaults=(),
    ),
)

_BY_NAME: dict[str, ParamSpec] = {s.name: s for s in CATALOG + UNDECLARED_USE}


def spec_for(name: str) -> ParamSpec | None:
    """El ParamSpec de un parámetro, o None si el catálogo no lo conoce."""
    return _BY_NAME.get(name)
