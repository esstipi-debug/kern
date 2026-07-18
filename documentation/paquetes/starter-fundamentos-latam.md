# Starter LatAm — Fundamentos de Inventario, alcance reducido

> **USD 250–300 / mes** · alcance fijo (8 tools) · cancelas cuando quieras
> Para e-commerce y distribuidores mono-almacén en LatAm (USD 1–10M de venta)
> que hoy deciden compras "a ojo" sobre una planilla de Excel.

**Nota sobre el precio:** este NO es el Starter Anglosfera (USD 900–1.500/mes)
con un descuento. Un salario real de analista LatAm (~USD 400–650/mes) es
demasiado bajo para cubrir horas-founder a la misma lógica de %-de-salario que
ancla el precio Anglosfera — así que esta es una oferta de **alcance de
entrega reducido**, no el mismo producto más barato. Ver
[MONETIZATION_BRIEF.md](../MONETIZATION_BRIEF.md), sección "LatAm (solo
equivalente a Starter)".

## Qué recibes cada mes

Un **reporte ejecutivo consolidado** más ocho análisis completos, cada uno con su
reporte y Excel de trabajo:

1. **Auditoría de calidad de datos** — duplicados y errores de maestro detectados
   antes de que contaminen las decisiones del mes.
2. **Clasificación ABC-XYZ** — tu portafolio segmentado por valor y variabilidad,
   con política por segmento.
3. **Pronóstico de demanda por SKU** — con medición honesta de calidad: te decimos
   cuánto valor agrega el pronóstico sobre el método ingenuo (y cuándo no agrega).
4. **Análisis de sensibilidad (what-if)** — qué supuesto mueve más tu costo anual
   y dónde está tu punto de quiebre de presupuesto.
5. **Política de inventario por SKU** — punto de reorden, stock de seguridad y
   cantidad de pedido óptima, ajustados a tu nivel de servicio y presupuesto.
6. **Tu planilla, devuelta con el plan de compra adentro** — trabajamos sobre TU
   archivo Excel tal como está: te lo devolvemos con las cantidades a reponer
   staged de forma **reversible** (nada se pisa sin tu aprobación, y todo tiene
   vuelta atrás).
7. **Programa de conteo cíclico** — calendario balanceado de conteos según la
   clase ABC de cada SKU, listo para operar.
8. **Compra de temporada (cuando aplique)** — cantidad óptima para compras de una
   sola oportunidad (newsvendor): ni te quedas corto ni entierras efectivo.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática. Si uno
solo falla, el paquete completo no se emite ese ciclo — no entregamos números a
medias.

## Qué te pedimos

Una carpeta con 3 archivos al inicio de cada ciclo (los mismos cada mes):

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `ventas.csv` | Historial de ventas/demanda | `date, product_id, quantity, unit_cost` |
| `maestro.csv` | Maestro de productos | `sku` (+ nombre, código de barras, costo) |
| `planilla.xlsx` | Tu planilla de reposición, tal como está | la detectamos automáticamente |

Opcionales cuando apliquen: `supuestos.csv` (rangos para el what-if; si no lo
mandas usamos una plantilla estándar ±20%) y `compra_estacional.csv` (compras
de temporada, destraba el análisis 8 el mes que lo mandes). Los parámetros de
tu negocio (costo de mantener, nivel de servicio, plazos) se relevan **una
sola vez** y quedan guardados en tu perfil.

## Cómo se ve el mes

1. Mandas la carpeta (día 1).
2. Corremos, validamos y aplicamos la compuerta de QA (días 1–2).
3. Recibes el paquete completo.
4. Apruebas el plan de compra; tu planilla vuelve con las cantidades staged y
   reversibles.

## Qué sigue después

Los tools 9–15 del Starter completo (`pricing`, `excess_obsolete`,
`financial_kpis`, `reconciliation`, `landed_cost`, `returns`, `risk`) y los
planes Growth/Scale/Retainer no forman parte de esta oferta LatAm por ahora —
si tu operación necesita ese alcance completo, conversamos las opciones.

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
