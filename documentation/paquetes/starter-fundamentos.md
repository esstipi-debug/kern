# Starter — Fundamentos de Inventario, todos los meses

> **USD 2.000 / mes** · alcance fijo · cancelas cuando quieras
> Para e-commerce y distribuidores mono-almacén (USD 1–10M de venta) que hoy
> deciden compras "a ojo" sobre una planilla de Excel.

## Qué recibes cada mes

Un **reporte ejecutivo consolidado** más ocho análisis completos, cada uno con su
reporte y Excel de trabajo:

1. **Pronóstico de demanda por SKU** — con medición honesta de calidad: te decimos
   cuánto valor agrega el pronóstico sobre el método ingenuo (y cuándo no agrega).
2. **Clasificación ABC-XYZ** — tu portafolio segmentado por valor y variabilidad,
   con política por segmento.
3. **Política de inventario por SKU** — punto de reorden, stock de seguridad y
   cantidad de pedido óptima, ajustados a tu nivel de servicio y presupuesto.
4. **Tu planilla, devuelta con el plan de compra adentro** — trabajamos sobre TU
   archivo Excel tal como está: te lo devolvemos con las cantidades a reponer
   staged de forma **reversible** (nada se pisa sin tu aprobación, y todo tiene
   vuelta atrás).
5. **Análisis de sensibilidad (what-if)** — qué supuesto mueve más tu costo anual
   y dónde está tu punto de quiebre de presupuesto.
6. **Programa de conteo cíclico** — calendario balanceado de conteos según la
   clase ABC de cada SKU, listo para operar.
7. **Auditoría de calidad de datos** — duplicados y errores de maestro detectados
   antes de que contaminen las decisiones del mes.
8. **Compra de temporada (cuando aplique)** — cantidad óptima para compras de una
   sola oportunidad (newsvendor): ni te quedas corto ni entierras efectivo.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática. Si uno
solo falla, el paquete completo no se emite ese ciclo — no entregamos números a
medias.

## Qué te pedimos

Una carpeta con 3 archivos al inicio de cada ciclo (los mismos cada mes):

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `ventas.csv` | Historial de ventas | `date, product_id, quantity, unit_cost` |
| `maestro.csv` | Maestro de productos | `sku` (+ nombre, código de barras, costo) |
| `planilla.xlsx` | Tu planilla de reposición, tal como está | la detectamos automáticamente |

Opcionales cuando apliquen: `supuestos.csv` (rangos para el what-if; si no lo
mandas usamos una plantilla estándar ±20%) y `compra_estacional.csv` (compras de
temporada). Los parámetros de tu negocio (costo de mantener, nivel de servicio,
plazos) se relevan **una sola vez** y quedan guardados en tu perfil.

## Cómo se ve el mes

1. Mandas la carpeta (día 1).
2. Corremos, validamos y aplicamos la compuerta de QA (días 1–2).
3. Recibes el paquete completo + 45 minutos de revisión por videollamada.
4. Apruebas el plan de compra; tu planilla vuelve con las cantidades staged y
   reversibles.

## Qué sigue después

Cuando tu operación crezca a multi-almacén, ERP o importaciones, el plan
**Growth** (USD 4.000/mes) suma la operación completa: reposición conectada a
Odoo, red de distribución, pricing, costo de servir, proveedores y riesgo, con
una revisión ejecutiva trimestral (QBR).
