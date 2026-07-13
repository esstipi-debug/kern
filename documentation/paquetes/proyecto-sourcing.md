# Proyecto de Sourcing y Costo de Importación

> **USD 5.000 – 10.000** (pago único, recurrible trimestral o anual)
> Para importadores y empresas con manufactura offshore que necesitan saber
> cuánto cuesta REALMENTE cada proveedor y cada contenedor puesto en destino.

## Qué recibes

Un **estudio ejecutivo consolidado** más tres análisis completos:

1. **Scorecard y selección de proveedor** — tus proveedores actuales
   comparados objetivamente en cumplimiento de entrega (OTIF), tiempo de
   entrega, calidad y precio, con una adjudicación recomendada lista para
   negociar (método TOPSIS, no una opinión).
2. **Costo total en destino (landed cost)** — el costo real de cada SKU
   importado una vez sumados flete, seguro, arancel (consciente del Incoterm)
   y gastos de manejo — no solo el precio de factura. Identifica cuál SKU
   tiene el mayor sobre-costo oculto.
3. **Plan de muestreo de recepción (AQL/LTPD)** — el plan de inspección que
   balancea el riesgo de aceptar un lote defectuoso contra el costo de
   inspeccionar de más, por cada componente o categoría que definas.

Cada número del estudio es trazable a su fuente y queda documentado para que
puedas usarlo directamente en la negociación con tus proveedores.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática antes
de emitirse. Si un solo análisis no la pasa, el estudio completo no se entrega.

## Qué te pedimos

Tres archivos:

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `proveedores.csv` | Registros de entrega por proveedor (una fila por entrega) | `supplier` (+ on_time, in_full, lead_time_days, defects, unit_price) |
| `importaciones.csv` | Líneas de importación | `sku, unit_cost, qty` (+ freight, insurance, duty_rate, incoterm) |
| `calidad_aql.csv` | Niveles de calidad aceptables por componente | `part, aql, ltpd` |

## Cómo trabajamos

Un proyecto de 2–3 semanas: recibimos tus datos, corremos los tres análisis,
validamos contigo los supuestos de costo (aranceles, tarifas de flete
vigentes) y cerramos con una sesión donde revisamos la adjudicación
recomendada y el plan de costo antes de que negocies con tus proveedores.

## Por qué "recurrible trimestral o anual"

Los aranceles cambian, los proveedores cambian su desempeño y tus volúmenes de
importación varían — este estudio tiene sentido repetirlo cuando renuevas
contratos o al menos una vez al año para verificar que sigues pagando el
costo real, no el que asumías.

## Qué sigue después

Si además quieres que el costo de importación y la selección de proveedor se
integren al ciclo mensual de tu operación completa de inventario, ese es el
alcance del plan **Growth** (USD 4.000/mes) o **Scale** (USD 7.500/mes), que ya
incluyen sourcing y landed cost como análisis situacionales del ciclo regular.

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
