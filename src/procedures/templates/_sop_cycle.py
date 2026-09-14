"""Monthly S&OP cycle SOP template content -- see src/procedures/templates/__init__.py
for the shared dataclasses, ``render_draft`` and ``draft_to_markdown``."""

from __future__ import annotations

from src.procedures.templates import ControlPlanRow, JobBreakdownStep, ProcedureTemplate, TrainingRow

TEMPLATE = ProcedureTemplate(
    key="sop_cycle",
    title={"es": "SOP - Ciclo mensual de S&OP", "en": "SOP - Monthly S&OP Cycle"},
    purpose={
        "es": (
            "Asegurar que la demanda y la oferta se concilien mensualmente en un ciclo de 5 "
            "pasos, con una decision ejecutiva registrada para cada brecha que no se pudo "
            "resolver a nivel funcional."
        ),
        "en": (
            "Ensure demand and supply are reconciled monthly through a 5-step cycle, with a "
            "recorded executive decision for every gap that could not be resolved at the "
            "functional level."
        ),
    },
    scope={
        "es": (
            "Aplica al ciclo mensual de Planificacion de Ventas y Operaciones (S&OP) de la "
            "organizacion, desde la recoleccion de datos hasta la decision ejecutiva. Ver "
            "RB-7 para el detalle de gobernanza del ciclo."
        ),
        "en": (
            "Applies to the organization's monthly Sales and Operations Planning (S&OP) "
            "cycle, from data collection through the executive decision. See RB-7 for the "
            "cycle's governance detail."
        ),
    },
    steps=(
        JobBreakdownStep(
            n=1,
            important_step={
                "es": "Recoleccion de datos (dia 1 del ciclo)",
                "en": "Data gathering (day 1 of the cycle)",
            },
            key_points={
                "es": (
                    "Cerrar los datos de ventas, inventario y ordenes del mes anterior",
                    "Publicar el paquete de datos base para los equipos de demanda y oferta",
                ),
                "en": (
                    "Close out last month's sales, inventory and order data",
                    "Publish the base data package for the demand and supply teams",
                ),
            },
            reasons={
                "es": (
                    "Sin datos cerrados del mes anterior, la revision de demanda parte de una "
                    "base incompleta",
                    "Publicar el paquete a tiempo permite que ambos equipos trabajen con el "
                    "mismo corte",
                ),
                "en": (
                    "Without last month's data closed out, the demand review starts from an "
                    "incomplete base",
                    "Publishing the package on time lets both teams work from the same cutoff",
                ),
            },
        ),
        JobBreakdownStep(
            n=2,
            important_step={
                "es": "Revision de demanda (dia 5 del ciclo)",
                "en": "Demand review (day 5 of the cycle)",
            },
            key_points={
                "es": (
                    "Consolidar el forecast estadistico con el ajuste comercial (demanda sin restringir)",
                    "Documentar los supuestos y eventos que expliquen los cambios frente al mes anterior",
                ),
                "en": (
                    "Consolidate the statistical forecast with the commercial adjustment "
                    "(unconstrained demand)",
                    "Document the assumptions and events that explain the changes from last month",
                ),
            },
            reasons={
                "es": (
                    "Un forecast sin ajuste comercial ignora informacion que el sistema no puede ver",
                    "Documentar los supuestos permite auditar por que cambio el forecast de un "
                    "mes a otro",
                ),
                "en": (
                    "A forecast without a commercial adjustment ignores information the system "
                    "cannot see",
                    "Documenting the assumptions lets the forecast change be audited month to month",
                ),
            },
        ),
        JobBreakdownStep(
            n=3,
            important_step={
                "es": "Revision de oferta (dia 10 del ciclo)",
                "en": "Supply review (day 10 of the cycle)",
            },
            key_points={
                "es": (
                    "Confrontar el forecast consolidado contra la capacidad y el plan de abastecimiento",
                    "Identificar brechas de capacidad o de suministro y las opciones para cerrarlas",
                ),
                "en": (
                    "Confront the consolidated forecast against capacity and the supply plan",
                    "Identify capacity or supply gaps and the options to close them",
                ),
            },
            reasons={
                "es": (
                    "Un plan de demanda que la operacion no puede cumplir solo traslada el "
                    "problema al mes siguiente",
                    "Nombrar las opciones (horas extra, subcontratar, priorizar) habilita la "
                    "decision en pre-S&OP",
                ),
                "en": (
                    "A demand plan operations cannot meet only pushes the problem into next month",
                    "Naming the options (overtime, subcontracting, prioritizing) is what enables "
                    "the pre-S&OP decision",
                ),
            },
        ),
        JobBreakdownStep(
            n=4,
            important_step={
                "es": "Pre-S&OP (dia 15 del ciclo)",
                "en": "Pre-S&OP (day 15 of the cycle)",
            },
            key_points={
                "es": (
                    "Resolver a nivel funcional las brechas identificadas en la revision de oferta",
                    "Preparar los escenarios y recomendaciones que subiran al S&OP ejecutivo",
                ),
                "en": (
                    "Resolve at the functional level the gaps identified in the supply review",
                    "Prepare the scenarios and recommendations that will go up to the executive S&OP",
                ),
            },
            reasons={
                "es": (
                    "Resolver lo que se puede resolver a nivel funcional evita escalar decisiones "
                    "operativas al comite ejecutivo",
                    "Un escenario preparado con anticipacion permite una decision ejecutiva mas rapida",
                ),
                "en": (
                    "Resolving what can be resolved functionally avoids escalating operational "
                    "decisions to the executive committee",
                    "A scenario prepared in advance makes for a faster executive decision",
                ),
            },
        ),
        JobBreakdownStep(
            n=5,
            important_step={
                "es": "S&OP ejecutivo (dia 20 del ciclo)",
                "en": "Executive S&OP (day 20 of the cycle)",
            },
            key_points={
                "es": (
                    "Presentar el plan consolidado de demanda y oferta con las brechas no resueltas",
                    "Decidir y dejar registrada la resolucion de cada brecha escalada (ver RB-7)",
                ),
                "en": (
                    "Present the consolidated demand and supply plan with the unresolved gaps",
                    "Decide and record the resolution of every escalated gap (see RB-7)",
                ),
            },
            reasons={
                "es": (
                    "El comite ejecutivo es el unico nivel con autoridad para resolver un "
                    "trade-off entre areas",
                    "Registrar la decision evita que la misma brecha se vuelva a discutir sin "
                    "avanzar el mes siguiente",
                ),
                "en": (
                    "The executive committee is the only level with the authority to resolve a "
                    "cross-area trade-off",
                    "Recording the decision keeps the same gap from being re-discussed without "
                    "progress next month",
                ),
            },
        ),
    ),
    control_plan=(
        ControlPlanRow(
            process_step=2,
            characteristic={
                "es": "Exactitud del forecast consolidado (MAPE)",
                "en": "Consolidated forecast accuracy (MAPE)",
            },
            spec={"es": "MAPE <= 20% contra el mes anterior", "en": "MAPE <= 20% against the prior month"},
            measurement_method={
                "es": "Comparar el forecast consolidado contra la demanda real del mes previo",
                "en": "Compare the consolidated forecast against actual demand for the prior month",
            },
            frequency={"es": "Mensual", "en": "Monthly"},
            control_method={"es": "Revision en el paso de demanda", "en": "Review in the demand step"},
            reaction_plan={
                "es": "Escalar la causa raiz si el MAPE supera 20% dos meses seguidos",
                "en": "Escalate the root cause if MAPE exceeds 20% for two months running",
            },
            owner_role={"es": "Planificador de demanda", "en": "Demand planner"},
        ),
        ControlPlanRow(
            process_step=3,
            characteristic={
                "es": "Cobertura de capacidad/oferta",
                "en": "Capacity/supply coverage",
            },
            spec={
                "es": "Brecha de capacidad identificada y cuantificada antes de pre-S&OP",
                "en": "Capacity gap identified and quantified before pre-S&OP",
            },
            measurement_method={
                "es": "Confrontar el forecast contra el plan de capacidad vigente",
                "en": "Confront the forecast against the current capacity plan",
            },
            frequency={"es": "Mensual", "en": "Monthly"},
            control_method={"es": "Revision en el paso de oferta", "en": "Review in the supply step"},
            reaction_plan={
                "es": "Levantar opciones de cierre de brecha antes de pre-S&OP",
                "en": "Raise gap-closing options before pre-S&OP",
            },
            owner_role={"es": "Planificador de oferta", "en": "Supply planner"},
        ),
        ControlPlanRow(
            process_step=5,
            characteristic={
                "es": "Resolucion de brechas escaladas",
                "en": "Resolution of escalated gaps",
            },
            spec={
                "es": "Toda brecha escalada al comite queda con una decision registrada (ver RB-7)",
                "en": "Every gap escalated to the committee ends with a recorded decision (see RB-7)",
            },
            measurement_method={
                "es": "Revisar el acta del S&OP ejecutivo",
                "en": "Review the executive S&OP minutes",
            },
            frequency={"es": "Mensual", "en": "Monthly"},
            control_method={
                "es": "Acta y seguimiento del comite de S&OP",
                "en": "S&OP committee minutes and follow-up",
            },
            reaction_plan={
                "es": "Reabrir la brecha en el siguiente ciclo si no quedo una decision registrada",
                "en": "Reopen the gap next cycle if no decision was recorded",
            },
            owner_role={"es": "Gerente de Operaciones / Lider de S&OP", "en": "Operations Manager / S&OP lead"},
        ),
    ),
    training=(
        TrainingRow(
            role={"es": "Planificador de demanda", "en": "Demand planner"},
            job={"es": "Revision de demanda mensual", "en": "Monthly demand review"},
            target_offset_days=5,
        ),
        TrainingRow(
            role={"es": "Gerente de Operaciones", "en": "Operations Manager"},
            job={"es": "S&OP ejecutivo mensual", "en": "Monthly executive S&OP"},
            target_offset_days=20,
        ),
    ),
    controlled_inputs={
        "es": (
            "Plan de demanda y datos de ventas del mes anterior",
            "Plan financiero/presupuesto vigente",
        ),
        "en": (
            "Prior month demand plan and sales data",
            "Current financial plan/budget",
        ),
    },
    parameters={},
)
