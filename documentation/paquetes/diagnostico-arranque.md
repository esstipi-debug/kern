# Diagnóstico de Arranque — ¿cuánto dinero tienes atrapado en tu inventario?

> Sprint único de **2 semanas** · **USD 1.500 – 2.500** (pago único)
> Para empresas que compran y almacenan inventario y sospechan que algo se les
> escapa — pero no saben cuánto ni dónde.

## Qué recibes

Un **documento ejecutivo consolidado** (PDF/Excel) más cuatro análisis completos,
cada uno con su reporte y su planilla de trabajo:

1. **Auditoría de calidad de datos** — SKUs duplicados, códigos de barras (GTIN)
   inválidos, campos faltantes en tu maestro de productos, con plan de remediación.
2. **Clasificación ABC-XYZ** — qué SKUs concentran tu valor y cuáles son erráticos;
   política de gestión y nivel de servicio recomendado por segmento.
3. **Excedente y obsoletos (E&O)** — cuánto efectivo está atrapado en stock muerto
   o excedido, SKU por SKU, con la acción recomendada para liberarlo.
4. **KPIs financieros del inventario** — rotación, días de inventario (DIO), GMROI,
   sell-through y ciclo cash-to-cash: cómo trabaja cada dólar invertido.

Cada número del reporte es trazable a su fuente y está fundamentado en literatura
de supply chain citada en el propio documento.

## Cómo trabajamos

- **Semana 1:** recibimos tus 4 archivos, corremos los análisis y validamos
  contigo los supuestos (costos, niveles de servicio).
- **Semana 2:** sesión ejecutiva de hallazgos — te mostramos cuánto hay en juego,
  dónde, y qué haríamos primero.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática antes
de emitirse. Si un solo análisis no la pasa, el paquete no se entrega — no hay
números a medias.

## Qué te pedimos para arrancar

Cuatro archivos (CSV o export de tu sistema; te ayudamos a extraerlos):

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `maestro.csv` | Maestro de productos | `sku` (+ nombre, código de barras, costo) |
| `ventas.csv` | Historial de ventas (12+ meses ideal) | `date, product_id, quantity, unit_cost` |
| `stock.csv` | Stock a mano por SKU | `product_id, on_hand, daily_demand` |
| `finanzas.csv` | COGS e inventario promedio por SKU | `product_id, cogs, avg_inventory_value` |

Más 15–30 minutos de contexto: costo de mantener inventario, nivel de servicio
objetivo y plazos de reposición típicos. Nada más.

## Qué sigue después

El diagnóstico cierra con una recomendación priorizada. Si quieres que el plan se
ejecute y se mantenga mes a mes, ese es exactamente el alcance de los planes
**Starter** (fundamentos de inventario, desde USD 900/mes) y **Growth** (operación
completa de supply chain, desde USD 1.500/mes) — y el diagnóstico ya deja tus datos
listos para arrancar cualquiera de los dos.

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
