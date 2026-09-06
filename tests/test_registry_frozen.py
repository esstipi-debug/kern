"""Freno mecanico #1 del plan del 2026-09-05: el registro de tools queda congelado.

Por que existe. Entre 2026-03 y 2026-09 el registro paso de 0 a 53 tools y nunca
se quito ninguna (0 borrados en 487 commits). La decision escrita del 2026-08-05
de "no mas motor" no freno nada: la semana siguiente entraron 3 tools mas. Un
documento no frena; un test rojo si. Este archivo hace que sumar o quitar una
tool sea un diff explicito sobre ``FROZEN_TOOL_KEYS``, revisado a proposito, y
no un ``register()`` mas que nadie ve.

Que congela y que no. Congela el CONJUNTO de claves (no solo la cuenta: 53
tools distintas de las 53 de hoy tambien fallan) y el contrato de los packs por
rubro (``config/verticals``): sus campos y las cinco llaves de motor que un pack
puede tocar. No congela el codigo de cada tool ni sus parametros.

Como se levanta el freno, cuando corresponda. Se edita la tupla en el mismo PR
que registra o borra la tool, con el motivo en el mensaje de commit y una linea
en el ADR (``documentation/ADR-2026-09-operador.md``). Regla del plan: la tool
#54 no existe hasta que un cliente pago la pida; las columnas del entregable si
pueden crecer sin tocar esto.

Espejo de ``tests/test_how_it_works_data.py`` y ``tests/test_mcp_server.py``,
que ya comparan conjuntos contra el registro; este es el que corre aunque
nadie toque la web ni el MCP.
"""

from __future__ import annotations

import dataclasses

from scm_agent.tools import build_default_registry
from src import vertical_profile

# Las 53 tools registradas en main @ 1aa5098 (2026-09-05), en orden alfabetico.
FROZEN_TOOL_KEYS: frozenset[str] = frozenset(
    {
        "abc_xyz",
        "acceptance_sampling",
        "cost_to_serve",
        "cross_impact",
        "cycle_count",
        "data_quality",
        "ddmrp",
        "dea",
        "digital_twin",
        "disruption_scan",
        "drp",
        "earned_value",
        "excel_replenishment",
        "excess_obsolete",
        "facility_location",
        "fefo",
        "financial_kpis",
        "flow_map",
        "forecast",
        "hat_settlement",
        "hat_tension",
        "intake_scan",
        "inventory_optimization",
        "landed_cost",
        "launch_readiness",
        "leadership_chain",
        "learning_curve",
        "markdown_liquidation",
        "multi_echelon",
        "network_design",
        "newsvendor",
        "odoo_replenishment",
        "org_friction_audit",
        "price_intelligence",
        "price_watch",
        "pricing",
        "procedure_audit",
        "procedure_draft",
        "queuing",
        "reconciliation",
        "returns",
        "risk",
        "scheduling",
        "service_scan",
        "simulation",
        "slotting",
        "sop",
        "sourcing",
        "supplier_management",
        "transportation",
        "vehicle_routing",
        "warehouse_layout",
        "whatif",
    }
)

# Contrato de un pack por rubro (config/verticals/<slug>.yaml). Un campo nuevo
# en el YAML hoy no carga (TypeError en VerticalProfile(**data)); este espejo
# hace que agregarlo sea una decision visible, no un efecto lateral de un PR.
FROZEN_VERTICAL_PROFILE_FIELDS: tuple[str, ...] = (
    "vertical",
    "display_name",
    "demo_question_label",
    "engine_params",
    "demand",
    "classification",
    "diagnostic_outputs",
    "aliases",
    "vocabulary",
    "objections",
    "stockout_cost_usd",
    "stockout_prompt",
)

# Las unicas llaves de motor que un pack puede fijar. lead_time_days figura
# porque el contrato la conoce para PROHIBIRLA (vertical_profile.py: un plazo
# por rubro es una promesa, y dimensionar contra promesas es el defecto que el
# producto denuncia).
FROZEN_ENGINE_PARAM_FIELDS: tuple[str, ...] = (
    "service_level",
    "holding_rate",
    "order_cost",
    "lead_time_days",
    "objection_thresholds",
)


def _describe_drift(actual: set[str], frozen: frozenset[str]) -> str:
    added = sorted(actual - frozen)
    removed = sorted(frozen - actual)
    return (
        f"registro con {len(actual)} tools vs {len(frozen)} congeladas; "
        f"nuevas: {added or '-'}; faltantes: {removed or '-'}. "
        "Si el cambio es deliberado, edita FROZEN_TOOL_KEYS en este mismo PR "
        "y deja el motivo en el ADR."
    )


def test_registry_is_frozen_at_the_53_tools_of_2026_09_05() -> None:
    registered = {t.key for t in build_default_registry().list()}
    assert registered == FROZEN_TOOL_KEYS, _describe_drift(registered, FROZEN_TOOL_KEYS)


def test_vertical_pack_contract_is_frozen() -> None:
    fields = tuple(f.name for f in dataclasses.fields(vertical_profile.VerticalProfile))
    assert fields == FROZEN_VERTICAL_PROFILE_FIELDS, (
        "cambio el contrato de los packs por rubro: "
        f"{sorted(set(fields) ^ set(FROZEN_VERTICAL_PROFILE_FIELDS))}. "
        "Si es deliberado, actualiza FROZEN_VERTICAL_PROFILE_FIELDS aca y el ADR."
    )
    assert tuple(vertical_profile.ENGINE_PARAM_FIELDS) == FROZEN_ENGINE_PARAM_FIELDS, (
        "cambiaron las llaves de motor que un pack puede fijar: "
        f"{sorted(set(vertical_profile.ENGINE_PARAM_FIELDS) ^ set(FROZEN_ENGINE_PARAM_FIELDS))}"
    )
