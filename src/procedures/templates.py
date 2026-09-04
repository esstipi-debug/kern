"""Bilingual SOP templates and deterministic rendering into a client-ready draft.

Domain: an SOP (Standard Operating Procedure -- NOT S&OP) is a document-control header
-- owner, approver, version, effective date, next review -- wrapped around a body in
Job Breakdown Sheet form (the TWI Job Instruction structure): numbered Important Steps,
each with Key Points and the Reason for each key point. A control plan links a subset
of those steps to a characteristic/KPI, a spec, a measurement method, a frequency, a
control method, a reaction plan and an owner role. A JI training timetable maps a role
to a job with a target date computed from the document's effective date.

ISO 9001:2015 7.5.2(c) requires "review and approval for suitability and adequacy"
before a controlled document is used -- so every draft this module renders is born
unapproved (``DocumentControl.approved_at`` stays ``None``, ``status`` stays "draft")
until a human signs off outside this module.

Mirrors ``src/procedures/register.py``'s shape -- module constants, frozen dataclasses,
small pure functions -- applied to rendering instead of auditing. Pure module: no
pandas, no file I/O, no system-clock lookups, no ``scm_agent`` imports.
``render_draft`` never raises on a bad ``{placeholder}`` -- an unresolved key in
template text is left as ``{key}`` rather than raising ``KeyError`` -- and never
guesses at a missing parameter's value: every parameter named in a template is either
supplied in ``params`` (source "param") or falls back to the template's own default
(source "convention"). This module never sees a "profile" source: the orchestrator
merges a client profile into ``params`` before a render runs, so from here a
profile-sourced value is indistinguishable from one the caller typed in directly.
``ProcedureDraft.outcome`` is always ``None`` here -- Task 4's job builds the
``GuidedOutcome`` once it wraps this render in an actual client interaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from nosilo_core.guided import GuidedOutcome

from src.cycle_count import DEFAULT_POLICY
from src.procedures.models import DocumentControl

TEMPLATE_VERSION = "1"

_VALID_LANGS = ("es", "en")

#: The literal ISO 9001:2015 7.5.2(c) draft-status sentence, exact per language --
#: every rendered draft carries it, since this module never marks anything approved.
ISO_SENTENCE_ES = "BORRADOR - requiere revision y aprobacion para adecuacion y suficiencia (ISO 9001:2015 7.5.2c)"
ISO_SENTENCE_EN = "DRAFT - requires review and approval for suitability and adequacy (ISO 9001:2015 7.5.2c)"
_ISO_SENTENCE = {"es": ISO_SENTENCE_ES, "en": ISO_SENTENCE_EN}

OWNER_EQUALS_APPROVER_WARNING = "owner == approver: la segregacion dueno/aprobador no se cumple"


# ---- dataclasses: template data (per-language dicts) --------------------------------


@dataclass(frozen=True)
class JobBreakdownStep:
    """One numbered Important Step of a Job Breakdown Sheet, in both languages.

    ``key_points`` and ``reasons`` are positional: ``reasons[lang][i]`` is the reason
    for ``key_points[lang][i]`` -- the TWI Job Instruction pairing of "how" and "why".
    """

    n: int
    important_step: dict[str, str]
    key_points: dict[str, tuple[str, ...]]
    reasons: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ControlPlanRow:
    """One control-plan row: links a JBS step to what is controlled and how."""

    process_step: int
    characteristic: dict[str, str]
    spec: dict[str, str]
    measurement_method: dict[str, str]
    frequency: dict[str, str]
    control_method: dict[str, str]
    reaction_plan: dict[str, str]
    owner_role: dict[str, str]


@dataclass(frozen=True)
class TrainingRow:
    """One JI training-timetable row: a role trained on a job, due some days after go-live."""

    role: dict[str, str]
    job: dict[str, str]
    target_offset_days: int


@dataclass(frozen=True)
class ProcedureTemplate:
    """One bilingual SOP template: content plus the labelled defaults it can substitute.

    ``parameters`` holds convention defaults keyed by the ``{placeholder}`` name they
    fill -- e.g. ``{"review_period_days": 7}``. ``render_draft`` labels each one "param"
    when the caller supplied it, "convention" when this default was used instead.
    """

    key: str
    title: dict[str, str]
    purpose: dict[str, str]
    scope: dict[str, str]
    steps: tuple[JobBreakdownStep, ...]
    control_plan: tuple[ControlPlanRow, ...]
    training: tuple[TrainingRow, ...]
    controlled_inputs: dict[str, tuple[str, ...]]
    parameters: dict[str, object]


# ---- dataclasses: rendered data (already resolved to one language) ------------------


@dataclass(frozen=True)
class RenderedStep:
    n: int
    important_step: str
    key_points: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RenderedControlPlanRow:
    process_step: int
    characteristic: str
    spec: str
    measurement_method: str
    frequency: str
    control_method: str
    reaction_plan: str
    owner_role: str


@dataclass(frozen=True)
class RenderedTraining:
    role: str
    job: str
    target_date: date


@dataclass(frozen=True)
class ProcedureDraft:
    """A single-language, unapproved SOP draft ready to render to markdown.

    ``parameters_used`` is ``(name, value_as_str, source)`` with ``source`` in
    ``{"param", "convention"}`` -- see the module docstring for why "profile" never
    appears here. ``outcome`` stays ``None``: Task 4's job fills it once this render is
    wrapped in an actual client interaction.
    """

    process: str
    lang: str
    control: DocumentControl
    title: str
    purpose: str
    scope: str
    steps: tuple[RenderedStep, ...]
    control_plan: tuple[RenderedControlPlanRow, ...]
    training: tuple[RenderedTraining, ...]
    controlled_inputs: tuple[str, ...]
    parameters_used: tuple[tuple[str, str, str], ...]
    warnings: tuple[str, ...]
    outcome: GuidedOutcome | None = None


# ---- template content: replenishment (reposicion) -----------------------------------

_REPLENISHMENT = ProcedureTemplate(
    key="replenishment",
    title={"es": "SOP - Reposicion de inventario", "en": "SOP - Inventory Replenishment"},
    purpose={
        "es": (
            "Mantener el nivel de servicio objetivo revisando la posicion de inventario "
            "cada {review_period_days} dias, con el service level ({service_level}) y el "
            "lead time ({lead_time_days}) declarados por el cliente, sin quiebres de stock "
            "ni exceso de inventario."
        ),
        "en": (
            "Maintain the target service level by reviewing the inventory position every "
            "{review_period_days} days, using the declared service level ({service_level}) "
            "and lead time ({lead_time_days}), without stockouts or excess inventory."
        ),
    },
    scope={
        "es": (
            "Aplica a todos los SKU cubiertos por la hoja de politica de inventario "
            "vigente ({policy_source}), desde la revision de la posicion hasta el registro "
            "de la orden de compra emitida."
        ),
        "en": (
            "Applies to every SKU covered by the current inventory policy sheet "
            "({policy_source}), from reviewing the inventory position through logging the "
            "purchase order that was issued."
        ),
    },
    steps=(
        JobBreakdownStep(
            n=1,
            important_step={
                "es": "Revisar la posicion de inventario on-hand y on-order por SKU",
                "en": "Review the on-hand and on-order inventory position by SKU",
            },
            key_points={
                "es": (
                    "Consultar el sistema (WMS/ERP) para el SKU y la ubicacion evaluada",
                    "Sumar el stock on-hand mas el on-order (en transito) para obtener la posicion neta",
                ),
                "en": (
                    "Query the system (WMS/ERP) for the SKU and the location under review",
                    "Add on-hand stock plus on-order (in-transit) stock to get the net position",
                ),
            },
            reasons={
                "es": (
                    "Una lectura desactualizada del stock genera pedidos duplicados o pedidos tardios",
                    "La posicion neta evita sobre-pedir cantidad que ya viene en camino",
                ),
                "en": (
                    "A stale stock reading causes duplicate orders or orders placed too late",
                    "The net position avoids over-ordering quantity that is already in transit",
                ),
            },
        ),
        JobBreakdownStep(
            n=2,
            important_step={
                "es": "Comparar la posicion neta contra el punto de reorden (ROP) vigente",
                "en": "Compare the net position against the current reorder point (ROP)",
            },
            key_points={
                "es": (
                    "Ubicar el ROP vigente para el SKU en {policy_source}",
                    "Comparar la posicion neta del paso 1 contra ese ROP",
                ),
                "en": (
                    "Locate the current ROP for the SKU in {policy_source}",
                    "Compare the net position from step 1 against that ROP",
                ),
            },
            reasons={
                "es": (
                    "El ROP vigente refleja la demanda y el lead time mas recientes calculados "
                    "por el motor de optimizacion de inventario",
                    "Comparar contra un ROP desactualizado dispara reposiciones incorrectas",
                ),
                "en": (
                    "The current ROP reflects the most recent demand and lead time calculated "
                    "by the inventory optimization engine",
                    "Comparing against a stale ROP triggers incorrect replenishment",
                ),
            },
        ),
        JobBreakdownStep(
            n=3,
            important_step={
                "es": "Calcular la cantidad a reponer cuando la posicion neta esta bajo el ROP",
                "en": "Calculate the quantity to replenish when the net position is below the ROP",
            },
            key_points={
                "es": (
                    "Calcular la cantidad como el target de stock menos la posicion neta "
                    "(o el EOQ vigente si el proceso del cliente lo define asi)",
                    "Registrar el service level ({service_level}) y el lead time ({lead_time_days}) "
                    "usados en el calculo",
                ),
                "en": (
                    "Calculate the quantity as the stock target minus the net position "
                    "(or the current EOQ when the client's process defines it that way)",
                    "Record the service level ({service_level}) and lead time ({lead_time_days}) "
                    "used in the calculation",
                ),
            },
            reasons={
                "es": (
                    "Reponer menos que la brecha reintroduce el riesgo de quiebre antes de la "
                    "proxima revision",
                    "Dejar constancia del service level y el lead time permite auditar la "
                    "cantidad calculada",
                ),
                "en": (
                    "Replenishing less than the gap reintroduces stockout risk before the next "
                    "review",
                    "Recording the service level and lead time lets the calculated quantity be "
                    "audited later",
                ),
            },
        ),
        JobBreakdownStep(
            n=4,
            important_step={
                "es": "Ajustar la cantidad al MOQ y a los multiplos de empaque/pallet del proveedor",
                "en": "Adjust the quantity to the supplier's MOQ and pack/pallet multiples",
            },
            key_points={
                "es": (
                    "Verificar el MOQ (cantidad minima de orden) del proveedor para el SKU",
                    "Redondear hacia arriba al multiplo de empaque o pallet mas cercano",
                ),
                "en": (
                    "Check the supplier's MOQ (minimum order quantity) for the SKU",
                    "Round up to the nearest pack or pallet multiple",
                ),
            },
            reasons={
                "es": (
                    "Una orden por debajo del MOQ puede ser rechazada o recargada por el proveedor",
                    "Ordenar en multiplos evita fracciones de pallet que complican la recepcion",
                ),
                "en": (
                    "An order below the MOQ can be rejected or surcharged by the supplier",
                    "Ordering in multiples avoids partial pallets that complicate receiving",
                ),
            },
        ),
        JobBreakdownStep(
            n=5,
            important_step={
                "es": "Emitir la orden de compra (OC) y obtener su aprobacion",
                "en": "Issue the purchase order (PO) and obtain its approval",
            },
            key_points={
                "es": (
                    "Generar la OC con cantidad, SKU, proveedor y fecha de entrega esperada",
                    "Enviar a aprobacion cuando el monto supere el limite de autorizacion individual",
                ),
                "en": (
                    "Generate the PO with quantity, SKU, supplier and expected delivery date",
                    "Route it for approval when the amount exceeds the individual authorization limit",
                ),
            },
            reasons={
                "es": (
                    "Una OC sin los datos completos retrasa la confirmacion del proveedor",
                    "La aprobacion documentada evita compras no autorizadas",
                ),
                "en": (
                    "A PO with incomplete data delays supplier confirmation",
                    "A documented approval prevents unauthorized purchases",
                ),
            },
        ),
        JobBreakdownStep(
            n=6,
            important_step={
                "es": "Registrar la reposicion y notificar al dueno del SKU",
                "en": "Log the replenishment and notify the SKU owner",
            },
            key_points={
                "es": (
                    "Guardar la OC emitida junto con la posicion de inventario que la origino",
                    "Notificar al dueno del SKU la fecha de entrega esperada",
                ),
                "en": (
                    "Save the issued PO together with the inventory position that triggered it",
                    "Notify the SKU owner of the expected delivery date",
                ),
            },
            reasons={
                "es": (
                    "El registro permite reconstruir por que se emitio cada OC en una auditoria posterior",
                    "El dueno del SKU necesita la fecha esperada para planificar la recepcion",
                ),
                "en": (
                    "The log lets every PO's origin be reconstructed in a later audit",
                    "The SKU owner needs the expected date to plan receiving",
                ),
            },
        ),
    ),
    control_plan=(
        ControlPlanRow(
            process_step=2,
            characteristic={"es": "Exactitud del ROP aplicado", "en": "Accuracy of the applied ROP"},
            spec={
                "es": "ROP = el vigente en la hoja de politica, sin desvios manuales",
                "en": "ROP = the one current in the policy sheet, no manual overrides",
            },
            measurement_method={
                "es": "Comparar el ROP usado contra el ROP publicado en {policy_source}",
                "en": "Compare the ROP used against the ROP published in {policy_source}",
            },
            frequency={
                "es": "Cada revision (cada {review_period_days} dias)",
                "en": "Every review (every {review_period_days} days)",
            },
            control_method={
                "es": "Verificacion cruzada contra {policy_source}",
                "en": "Cross-check against {policy_source}",
            },
            reaction_plan={
                "es": "Si el ROP no coincide, corregirlo y volver a calcular la posicion antes de continuar",
                "en": "If the ROP does not match, correct it and recompute the position before continuing",
            },
            owner_role={"es": "Planificador de reposicion", "en": "Replenishment planner"},
        ),
        ControlPlanRow(
            process_step=4,
            characteristic={
                "es": "Cumplimiento de MOQ y multiplos del proveedor",
                "en": "Compliance with MOQ and supplier multiples",
            },
            spec={
                "es": "Cantidad ordenada >= MOQ y multiplo exacto de empaque/pallet",
                "en": "Ordered quantity >= MOQ and an exact pack/pallet multiple",
            },
            measurement_method={
                "es": "Verificar la cantidad final de la OC contra la ficha del proveedor",
                "en": "Check the PO's final quantity against the supplier record",
            },
            frequency={"es": "Cada OC emitida", "en": "Every PO issued"},
            control_method={"es": "Checklist de emision de OC", "en": "PO-issuance checklist"},
            reaction_plan={
                "es": "Ajustar la cantidad al multiplo correcto antes de enviar la OC",
                "en": "Adjust the quantity to the correct multiple before sending the PO",
            },
            owner_role={"es": "Comprador", "en": "Buyer"},
        ),
        ControlPlanRow(
            process_step=5,
            characteristic={"es": "Autorizacion de la OC", "en": "PO authorization"},
            spec={
                "es": "OC aprobada por el nivel correspondiente al monto",
                "en": "PO approved at the level corresponding to its amount",
            },
            measurement_method={
                "es": "Revisar la aprobacion registrada en el sistema de compras",
                "en": "Check the approval logged in the purchasing system",
            },
            frequency={"es": "Cada OC emitida", "en": "Every PO issued"},
            control_method={"es": "Flujo de aprobacion del ERP", "en": "ERP approval workflow"},
            reaction_plan={
                "es": "Bloquear el envio al proveedor hasta contar con la aprobacion",
                "en": "Block sending to the supplier until the approval is on record",
            },
            owner_role={"es": "Gerente de Compras", "en": "Purchasing Manager"},
        ),
        ControlPlanRow(
            process_step=6,
            characteristic={
                "es": "Trazabilidad del registro de reposicion",
                "en": "Traceability of the replenishment log",
            },
            spec={
                "es": "Toda OC emitida queda registrada con la posicion que la origino",
                "en": "Every issued PO is logged together with the position that triggered it",
            },
            measurement_method={
                "es": "Muestreo mensual del registro de seguimiento",
                "en": "Monthly sampling of the tracking log",
            },
            frequency={"es": "Mensual", "en": "Monthly"},
            control_method={"es": "Auditoria interna de registro", "en": "Internal log audit"},
            reaction_plan={
                "es": "Completar el registro faltante y notificar al dueno del SKU",
                "en": "Complete the missing log entry and notify the SKU owner",
            },
            owner_role={"es": "Dueno del SKU", "en": "SKU owner"},
        ),
    ),
    training=(
        TrainingRow(
            role={"es": "Planificador de reposicion", "en": "Replenishment planner"},
            job={
                "es": "Ejecucion del ciclo de reposicion (pasos 1 a 3)",
                "en": "Running the replenishment cycle (steps 1 to 3)",
            },
            target_offset_days=14,
        ),
        TrainingRow(
            role={"es": "Comprador", "en": "Buyer"},
            job={
                "es": "Emision y aprobacion de la OC (pasos 4 a 5)",
                "en": "Issuing and approving the PO (steps 4 to 5)",
            },
            target_offset_days=21,
        ),
    ),
    controlled_inputs={
        "es": (
            "Hoja de politica de inventario vigente (documento controlado externo, "
            "ISO 9001:2015 7.5.3)",
            "Maestro de proveedores (MOQ, multiplos de empaque, lead time)",
        ),
        "en": (
            "Current inventory policy sheet (externally controlled document, "
            "ISO 9001:2015 7.5.3)",
            "Supplier master data (MOQ, pack multiples, lead time)",
        ),
    },
    parameters={
        "review_period_days": 7,
        "service_level": "no declarado",
        "lead_time_days": "no declarado",
        "policy_source": "hoja de politica vigente (inventory_optimization)",
    },
)


# ---- template content: cycle_count (conteo ciclico) ----------------------------------

_CYCLE_COUNT = ProcedureTemplate(
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


# ---- template content: sop_cycle (ciclo S&OP mensual) --------------------------------

_SOP_CYCLE = ProcedureTemplate(
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


TEMPLATES: dict[str, ProcedureTemplate] = {
    _REPLENISHMENT.key: _REPLENISHMENT,
    _CYCLE_COUNT.key: _CYCLE_COUNT,
    _SOP_CYCLE.key: _SOP_CYCLE,
}
PROCESS_KEYS = tuple(TEMPLATES)


# ---- functions -----------------------------------------------------------------------


def _require_known_process(process: str) -> None:
    if process not in TEMPLATES:
        raise ValueError(f"unknown process '{process}': choose one of {', '.join(PROCESS_KEYS)}")


def get_template(process: str) -> ProcedureTemplate:
    """The named template, or ``ValueError`` listing ``PROCESS_KEYS`` if it does not exist."""
    _require_known_process(process)
    return TEMPLATES[process]


def resolve_next_review(as_of: date, cadence_days: int) -> date:
    """``as_of + cadence_days`` -- the pure date arithmetic behind a fresh ``DocumentControl``."""
    return as_of + timedelta(days=cadence_days)


class _SafeDict(dict):
    """``str.format_map`` mapping that leaves an unresolved ``{placeholder}`` untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render_draft(
    template: ProcedureTemplate, *, control: DocumentControl, params: dict[str, object], lang: str
) -> ProcedureDraft:
    """Resolve one template to a single-language, unapproved ``ProcedureDraft``.

    Every key in ``template.parameters`` is looked up in ``params`` first (source
    "param"); when it is absent the template's own default is used (source
    "convention") -- see the module docstring for why "profile" is never emitted here.
    Text substitution never raises: an unresolved ``{placeholder}`` is left as-is.
    """
    if lang not in _VALID_LANGS:
        raise ValueError(f"unknown lang '{lang}': choose one of {', '.join(_VALID_LANGS)}")

    resolved: list[tuple[str, object, str]] = []
    for name, default in template.parameters.items():
        if name in params:
            resolved.append((name, params[name], "param"))
        else:
            resolved.append((name, default, "convention"))

    sub_map = {name: str(value) for name, value, _source in resolved}
    parameters_used = tuple((name, str(value), source) for name, value, source in resolved)

    def sub(text: str) -> str:
        return text.format_map(_SafeDict(sub_map))

    steps = tuple(
        RenderedStep(
            n=step.n,
            important_step=sub(step.important_step[lang]),
            key_points=tuple(sub(kp) for kp in step.key_points[lang]),
            reasons=tuple(sub(r) for r in step.reasons[lang]),
        )
        for step in template.steps
    )

    control_plan = tuple(
        RenderedControlPlanRow(
            process_step=row.process_step,
            characteristic=sub(row.characteristic[lang]),
            spec=sub(row.spec[lang]),
            measurement_method=sub(row.measurement_method[lang]),
            frequency=sub(row.frequency[lang]),
            control_method=sub(row.control_method[lang]),
            reaction_plan=sub(row.reaction_plan[lang]),
            owner_role=sub(row.owner_role[lang]),
        )
        for row in template.control_plan
    )

    training = tuple(
        RenderedTraining(
            role=sub(row.role[lang]),
            job=sub(row.job[lang]),
            target_date=control.effective_date + timedelta(days=row.target_offset_days),
        )
        for row in template.training
    )

    controlled_inputs = tuple(sub(text) for text in template.controlled_inputs[lang])

    warnings: list[str] = []
    owner = control.owner.strip()
    approver = control.approver.strip()
    if owner and owner.lower() == approver.lower():
        warnings.append(OWNER_EQUALS_APPROVER_WARNING)

    return ProcedureDraft(
        process=template.key,
        lang=lang,
        control=control,
        title=sub(template.title[lang]),
        purpose=sub(template.purpose[lang]),
        scope=sub(template.scope[lang]),
        steps=steps,
        control_plan=control_plan,
        training=training,
        controlled_inputs=controlled_inputs,
        parameters_used=parameters_used,
        warnings=tuple(warnings),
        outcome=None,
    )


_LABELS: dict[str, dict[str, str]] = {
    "control_header": {"es": "Control documental", "en": "Document control"},
    "owner": {"es": "Dueno", "en": "Owner"},
    "approver": {"es": "Aprobador", "en": "Approver"},
    "version": {"es": "Version", "en": "Version"},
    "effective_date": {"es": "Vigente desde", "en": "Effective date"},
    "approved_at": {"es": "Aprobado el", "en": "Approved at"},
    "next_review": {"es": "Proxima revision", "en": "Next review"},
    "cadence": {"es": "Cadencia de revision (dias, fuente)", "en": "Review cadence (days, source)"},
    "status": {"es": "Estado", "en": "Status"},
    "template_version": {"es": "Version de plantilla", "en": "Template version"},
    "purpose": {"es": "Proposito", "en": "Purpose"},
    "scope": {"es": "Alcance", "en": "Scope"},
    "procedure": {"es": "Procedimiento (Hoja de Desglose de Trabajo)", "en": "Procedure (Job Breakdown Sheet)"},
    "step_n": {"es": "#", "en": "#"},
    "step_important": {"es": "Paso importante", "en": "Important step"},
    "step_keypoints": {"es": "Puntos clave", "en": "Key points"},
    "step_reasons": {"es": "Razones", "en": "Reasons"},
    "control_plan": {"es": "Plan de control", "en": "Control plan"},
    "cp_step": {"es": "Paso", "en": "Step"},
    "cp_characteristic": {"es": "Caracteristica", "en": "Characteristic"},
    "cp_spec": {"es": "Especificacion", "en": "Spec"},
    "cp_method": {"es": "Metodo de medicion", "en": "Measurement method"},
    "cp_frequency": {"es": "Frecuencia", "en": "Frequency"},
    "cp_control_method": {"es": "Metodo de control", "en": "Control method"},
    "cp_reaction": {"es": "Plan de reaccion", "en": "Reaction plan"},
    "cp_owner_role": {"es": "Rol responsable", "en": "Owner role"},
    "training": {"es": "Entrenamiento (JI)", "en": "Training (JI)"},
    "tr_role": {"es": "Rol", "en": "Role"},
    "tr_job": {"es": "Tarea", "en": "Job"},
    "tr_target": {"es": "Fecha objetivo", "en": "Target date"},
    "controlled_inputs": {"es": "Insumos controlados", "en": "Controlled inputs"},
    "parameters_used": {"es": "Parametros usados", "en": "Parameters used"},
    "param_name": {"es": "Parametro", "en": "Parameter"},
    "param_value": {"es": "Valor", "en": "Value"},
    "param_source": {"es": "Fuente", "en": "Source"},
    "warnings": {"es": "Advertencias", "en": "Warnings"},
    "no_params": {"es": "(sin parametros)", "en": "(no parameters)"},
    "no_warnings": {"es": "(sin advertencias)", "en": "(no warnings)"},
}


def _label(key: str, lang: str) -> str:
    return _LABELS[key][lang]


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def draft_to_markdown(draft: ProcedureDraft) -> str:
    """Render a ``ProcedureDraft`` to a deterministic, single-language markdown document.

    ``ValueError`` (listing ``PROCESS_KEYS``) if ``draft.process`` is not a known
    template key. Never touches a clock and iterates only over the draft's own tuples,
    so the same draft always produces byte-identical markdown.
    """
    _require_known_process(draft.process)
    lang = draft.lang

    def L(key: str) -> str:
        return _label(key, lang)

    lines: list[str] = [f"# {draft.title}", "", f"**{_ISO_SENTENCE[lang]}**", "", f"## {L('control_header')}", ""]

    approved_at = draft.control.approved_at.isoformat() if draft.control.approved_at is not None else "-"
    control_rows: list[tuple[object, ...]] = [
        (L("owner"), draft.control.owner),
        (L("approver"), draft.control.approver),
        (L("version"), draft.control.version),
        (L("effective_date"), draft.control.effective_date.isoformat()),
        (L("approved_at"), approved_at),
        (L("next_review"), draft.control.next_review.isoformat()),
        (L("cadence"), f"{draft.control.review_cadence_days} ({draft.control.cadence_source})"),
        (L("status"), draft.control.status),
        (L("template_version"), draft.control.template_version),
    ]
    lines.extend(_markdown_table((L("param_name"), L("param_value")), control_rows))
    lines.append("")

    lines.append(f"## {L('purpose')}")
    lines.append("")
    lines.append(draft.purpose)
    lines.append("")

    lines.append(f"## {L('scope')}")
    lines.append("")
    lines.append(draft.scope)
    lines.append("")

    lines.append(f"## {L('procedure')}")
    lines.append("")
    step_rows: list[tuple[object, ...]] = [
        (step.n, step.important_step, "; ".join(step.key_points), "; ".join(step.reasons)) for step in draft.steps
    ]
    lines.extend(
        _markdown_table((L("step_n"), L("step_important"), L("step_keypoints"), L("step_reasons")), step_rows)
    )
    lines.append("")

    lines.append(f"## {L('control_plan')}")
    lines.append("")
    cp_rows: list[tuple[object, ...]] = [
        (
            row.process_step,
            row.characteristic,
            row.spec,
            row.measurement_method,
            row.frequency,
            row.control_method,
            row.reaction_plan,
            row.owner_role,
        )
        for row in draft.control_plan
    ]
    lines.extend(
        _markdown_table(
            (
                L("cp_step"),
                L("cp_characteristic"),
                L("cp_spec"),
                L("cp_method"),
                L("cp_frequency"),
                L("cp_control_method"),
                L("cp_reaction"),
                L("cp_owner_role"),
            ),
            cp_rows,
        )
    )
    lines.append("")

    lines.append(f"## {L('training')}")
    lines.append("")
    training_rows: list[tuple[object, ...]] = [
        (row.role, row.job, row.target_date.isoformat()) for row in draft.training
    ]
    lines.extend(_markdown_table((L("tr_role"), L("tr_job"), L("tr_target")), training_rows))
    lines.append("")

    lines.append(f"## {L('controlled_inputs')}")
    lines.append("")
    lines.extend(f"- {item}" for item in draft.controlled_inputs)
    lines.append("")

    lines.append(f"## {L('parameters_used')}")
    lines.append("")
    if draft.parameters_used:
        lines.extend(
            _markdown_table(
                (L("param_name"), L("param_value"), L("param_source")),
                list(draft.parameters_used),
            )
        )
    else:
        lines.append(L("no_params"))
    lines.append("")

    lines.append(f"## {L('warnings')}")
    lines.append("")
    if draft.warnings:
        lines.extend(f"- {w}" for w in draft.warnings)
    else:
        lines.append(L("no_warnings"))

    return "\n".join(lines)
