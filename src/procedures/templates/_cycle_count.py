"""Cycle-count (conteo ciclico) SOP template content -- see
src/procedures/templates/__init__.py for the shared dataclasses, ``render_draft`` and
``draft_to_markdown``."""

from __future__ import annotations

from src.cycle_count import DEFAULT_POLICY
from src.procedures.templates import ControlPlanRow, JobBreakdownStep, ProcedureTemplate, TrainingRow

TEMPLATE = ProcedureTemplate(
    key="cycle_count",
    title={"es": "SOP - Conteo ciclico", "en": "SOP - Cycle Count"},
    purpose={
        "es": (
            "Reemplazar el conteo anual pared a pared por conteos frecuentes de los SKU de "
            "clase A y menos frecuentes de los de clase C, manteniendo la exactitud del "
            "inventario del sistema sin detener la operacion."
        ),
        "en": (
            "Replace the annual wall-to-wall count with frequent counts of class-A SKUs and "
            "less frequent counts of class-C SKUs, keeping system inventory accuracy without "
            "halting operations."
        ),
    },
    scope={
        "es": (
            "Aplica a todos los SKU con clasificacion ABC vigente, contados segun el "
            "cronograma de conteo ciclico distribuido a lo largo del anio de trabajo."
        ),
        "en": (
            "Applies to every SKU with a current ABC classification, counted per the cycle-"
            "count schedule spread across the working year."
        ),
    },
    steps=(
        JobBreakdownStep(
            n=1,
            important_step={
                "es": "Seleccionar los SKU a contar en el dia segun el cronograma por clase ABC",
                "en": "Select the day's SKUs to count per the ABC-class schedule",
            },
            key_points={
                "es": (
                    "Consultar el cronograma de conteo ciclico generado para el dia de trabajo actual",
                    "Confirmar la clase ABC de cada SKU seleccionado, que determina su frecuencia "
                    "de conteo en el anio",
                ),
                "en": (
                    "Pull the cycle-count schedule generated for the current working day",
                    "Confirm the ABC class of each selected SKU, which sets its count frequency "
                    "for the year",
                ),
            },
            reasons={
                "es": (
                    "El cronograma distribuye la carga de conteo de forma pareja a lo largo de "
                    "{working_days} dias habiles",
                    "La clase ABC define cuantas veces al anio se cuenta cada SKU segun la "
                    "politica de conteo ciclico vigente",
                ),
                "en": (
                    "The schedule spreads the counting load evenly across {working_days} working days",
                    "The ABC class sets how many times a year each SKU is counted under the "
                    "current cycle-count policy",
                ),
            },
        ),
        JobBreakdownStep(
            n=2,
            important_step={
                "es": "Congelar los movimientos de inventario de los SKU seleccionados",
                "en": "Freeze inventory movements for the selected SKUs",
            },
            key_points={
                "es": (
                    "Bloquear picking, recepcion y ajustes manuales sobre esos SKU mientras dura el conteo",
                    "Avisar a los turnos afectados antes de iniciar el bloqueo",
                ),
                "en": (
                    "Block picking, receiving and manual adjustments on those SKUs while the count lasts",
                    "Notify the affected shifts before starting the freeze",
                ),
            },
            reasons={
                "es": (
                    "Un movimiento durante el conteo invalida la comparacion contra el stock del sistema",
                    "Avisar con anticipacion evita bloquear una operacion en curso sin aviso",
                ),
                "en": (
                    "A movement during the count invalidates the comparison against system stock",
                    "Advance notice avoids blocking an operation in progress without warning",
                ),
            },
        ),
        JobBreakdownStep(
            n=3,
            important_step={
                "es": "Contar a ciegas y registrar el conteo fisico",
                "en": "Count blind and log the physical count",
            },
            key_points={
                "es": (
                    "Contar sin ver el stock teorico del sistema (conteo a ciegas)",
                    "Registrar el conteo fisico con fecha, hora y responsable",
                ),
                "en": (
                    "Count without seeing the system's theoretical stock (blind count)",
                    "Log the physical count with date, time and the person responsible",
                ),
            },
            reasons={
                "es": (
                    "Contar a ciegas evita el sesgo de ajustar el conteo para que coincida con el sistema",
                    "El registro con responsable permite auditar el conteo si se repite el desvio",
                ),
                "en": (
                    "Blind counting avoids the bias of adjusting the count to match the system",
                    "Logging the responsible person allows the count to be audited if the deviation recurs",
                ),
            },
        ),
        JobBreakdownStep(
            n=4,
            important_step={
                "es": "Comparar el conteo fisico contra el stock del sistema",
                "en": "Compare the physical count against system stock",
            },
            key_points={
                "es": (
                    "Calcular el desvio porcentual entre el conteo fisico y el stock del sistema",
                    "Comparar el desvio contra la tolerancia de recuento ({recount_tolerance_pct}%)",
                ),
                "en": (
                    "Calculate the percentage deviation between the physical count and system stock",
                    "Compare the deviation against the recount tolerance ({recount_tolerance_pct}%)",
                ),
            },
            reasons={
                "es": (
                    "El desvio porcentual normaliza la comparacion entre SKU de distinto volumen",
                    "La tolerancia evita recontar por variaciones normales de conteo",
                ),
                "en": (
                    "The percentage deviation normalizes the comparison across SKUs of different volume",
                    "The tolerance avoids recounting for normal counting variation",
                ),
            },
        ),
        JobBreakdownStep(
            n=5,
            important_step={
                "es": "Recontar si el desvio supera la tolerancia",
                "en": "Recount when the deviation exceeds the tolerance",
            },
            key_points={
                "es": (
                    "Recontar el SKU con una persona distinta a la del primer conteo",
                    "Registrar el segundo conteo junto con el primero",
                ),
                "en": (
                    "Recount the SKU with a different person than the first count",
                    "Log the second count together with the first",
                ),
            },
            reasons={
                "es": (
                    "Un segundo contador independiente reduce el riesgo de repetir el mismo error",
                    "Conservar ambos conteos documenta si el desvio se confirma o se explica por "
                    "un error de conteo",
                ),
                "en": (
                    "An independent second counter reduces the risk of repeating the same error",
                    "Keeping both counts documents whether the deviation is confirmed or explained "
                    "by a counting error",
                ),
            },
        ),
        JobBreakdownStep(
            n=6,
            important_step={
                "es": "Ajustar el inventario con aprobacion y registrar la causa raiz",
                "en": "Adjust inventory with approval and log the root cause",
            },
            key_points={
                "es": (
                    "Enviar el ajuste a aprobacion cuando el desvio confirmado supere la tolerancia",
                    "Registrar la causa raiz del desvio (error de conteo, mal recibo, mermas, etc.)",
                ),
                "en": (
                    "Route the adjustment for approval when the confirmed deviation exceeds the tolerance",
                    "Log the root cause of the deviation (counting error, bad receipt, shrinkage, etc.)",
                ),
            },
            reasons={
                "es": (
                    "El ajuste sin aprobacion abre la puerta a manipular el inventario registrado",
                    "La causa raiz es lo que permite reducir el desvio en el proximo ciclo, no solo "
                    "corregirlo",
                ),
                "en": (
                    "An unapproved adjustment opens the door to manipulating recorded inventory",
                    "The root cause is what reduces the deviation in the next cycle, not just corrects it",
                ),
            },
        ),
    ),
    control_plan=(
        ControlPlanRow(
            process_step=3,
            characteristic={"es": "Exactitud del conteo fisico", "en": "Accuracy of the physical count"},
            spec={
                "es": "Conteo a ciegas con registro de fecha, hora y responsable",
                "en": "Blind count logged with date, time and responsible person",
            },
            measurement_method={
                "es": "Revisar que el registro de conteo tenga responsable y hora",
                "en": "Check that the count log has a responsible person and a timestamp",
            },
            frequency={"es": "Cada conteo ejecutado", "en": "Every count performed"},
            control_method={"es": "Checklist de conteo ciclico", "en": "Cycle-count checklist"},
            reaction_plan={
                "es": "Rehacer el registro si falta el responsable o la hora",
                "en": "Redo the log entry if the responsible person or timestamp is missing",
            },
            owner_role={"es": "Contador designado", "en": "Assigned counter"},
        ),
        ControlPlanRow(
            process_step=4,
            characteristic={
                "es": "Desvio de inventario contra tolerancia",
                "en": "Inventory deviation against tolerance",
            },
            spec={
                "es": "Desvio <= {recount_tolerance_pct}% del stock del sistema",
                "en": "Deviation <= {recount_tolerance_pct}% of system stock",
            },
            measurement_method={
                "es": "Calcular (conteo fisico - stock sistema) / stock sistema",
                "en": "Calculate (physical count - system stock) / system stock",
            },
            frequency={"es": "Cada SKU contado", "en": "Every SKU counted"},
            control_method={
                "es": "Comparacion automatica en la planilla/sistema de conteo",
                "en": "Automatic comparison in the counting spreadsheet/system",
            },
            reaction_plan={
                "es": "Disparar el recuento del paso 5 si el desvio supera la tolerancia",
                "en": "Trigger the step-5 recount when the deviation exceeds the tolerance",
            },
            owner_role={"es": "Supervisor de inventario", "en": "Inventory supervisor"},
        ),
        ControlPlanRow(
            process_step=6,
            characteristic={
                "es": "Aprobacion del ajuste de inventario",
                "en": "Inventory adjustment approval",
            },
            spec={
                "es": "Todo ajuste queda aprobado antes de impactar el stock del sistema",
                "en": "Every adjustment is approved before it hits system stock",
            },
            measurement_method={
                "es": "Revisar la aprobacion registrada en el ERP para cada ajuste",
                "en": "Check the approval logged in the ERP for each adjustment",
            },
            frequency={"es": "Cada ajuste generado", "en": "Every adjustment generated"},
            control_method={"es": "Flujo de aprobacion del ERP", "en": "ERP approval workflow"},
            reaction_plan={
                "es": "Revertir el ajuste no aprobado y escalar al supervisor",
                "en": "Reverse the unapproved adjustment and escalate to the supervisor",
            },
            owner_role={"es": "Supervisor de inventario", "en": "Inventory supervisor"},
        ),
        ControlPlanRow(
            process_step=6,
            characteristic={"es": "Registro de causa raiz", "en": "Root-cause logging"},
            spec={
                "es": "Todo desvio confirmado tiene una causa raiz registrada",
                "en": "Every confirmed deviation has a logged root cause",
            },
            measurement_method={
                "es": "Auditoria mensual del registro de conteo ciclico",
                "en": "Monthly audit of the cycle-count log",
            },
            frequency={"es": "Mensual", "en": "Monthly"},
            control_method={"es": "Revision de cierre de ciclo", "en": "Cycle-close review"},
            reaction_plan={
                "es": "Completar la causa raiz faltante antes de cerrar el mes",
                "en": "Complete the missing root cause before closing the month",
            },
            owner_role={"es": "Jefe de Almacen", "en": "Warehouse Manager"},
        ),
    ),
    training=(
        TrainingRow(
            role={"es": "Contador ciclico", "en": "Cycle counter"},
            job={
                "es": "Ejecucion de conteo a ciegas y registro",
                "en": "Running the blind count and logging it",
            },
            target_offset_days=10,
        ),
        TrainingRow(
            role={"es": "Supervisor de inventario", "en": "Inventory supervisor"},
            job={
                "es": "Aprobacion de ajustes y cierre de causa raiz",
                "en": "Approving adjustments and closing the root cause",
            },
            target_offset_days=30,
        ),
    ),
    controlled_inputs={
        "es": (
            "Clasificacion ABC vigente del catalogo de SKU",
            "Maestro de SKU y ubicaciones de almacen",
        ),
        "en": (
            "Current ABC classification of the SKU catalog",
            "SKU and warehouse location master data",
        ),
    },
    parameters={
        # imported, never copied -- see test_cycle_count_template_imports_default_policy_not_a_copy
        "counts_per_year": DEFAULT_POLICY.counts_per_year,
        "working_days": 250,
        "recount_tolerance_pct": 2,
    },
)
