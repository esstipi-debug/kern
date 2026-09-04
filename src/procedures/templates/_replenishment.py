"""Replenishment (reposicion) SOP template content -- see src/procedures/templates/__init__.py
for the shared dataclasses, ``render_draft`` and ``draft_to_markdown``."""

from __future__ import annotations

from src.procedures.templates import ControlPlanRow, JobBreakdownStep, ProcedureTemplate, TrainingRow

#: service_level/lead_time_days have no numeric convention default -- both languages
#: fall back to this placeholder text until the caller (or the client profile, merged
#: upstream into ``params``) supplies a real value.
_NOT_DECLARED = {"es": "no declarado", "en": "not declared"}

TEMPLATE = ProcedureTemplate(
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
        "service_level": _NOT_DECLARED,
        "lead_time_days": _NOT_DECLARED,
        "policy_source": {
            "es": "hoja de politica vigente (inventory_optimization)",
            "en": "current inventory policy sheet (inventory_optimization)",
        },
    },
)
