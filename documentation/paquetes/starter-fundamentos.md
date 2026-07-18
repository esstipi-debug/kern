# Starter — Fundamentos de Inventario, todos los meses

> **USD 900 / mes** (piso hasta ~500 SKUs, +USD 40/mes cada bloque de 250
> SKUs, techo USD 1.500 — subir de piso nunca es una sorpresa, siempre se
> aprueba antes) · alcance variable por catálogo · cancelas cuando quieras
> Para e-commerce y distribuidores mono-almacén (USD 1–10M de venta) que hoy
> deciden compras "a ojo" sobre una planilla de Excel.

## Qué recibes cada mes

Un **reporte ejecutivo consolidado** más quince análisis completos, cada uno con su
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
9. **Precio óptimo por SKU** — margen máximo estimado a partir de tu historial de
   precio/cantidad, con el impacto esperado en utilidad.
10. **Excedente y obsolescencia** — qué stock muerto tenés y cuánto vale liberar
    (opcional: corre si mandás `stock.csv`).
11. **KPIs financieros de inventario** — vueltas, días de inventario, margen bruto
    sobre el capital inmovilizado (opcional: corre si mandás `finanzas.csv`).
12. **Conciliación de conteos (IRA)** — exactitud de registros entre sistema y
    conteo físico (opcional: corre si mandás `conteos.csv`).
13. **Costo total en destino (landed cost)** — costo real de tus importaciones más
    allá del precio de lista (opcional: corre si mandás `importaciones.csv`).
14. **Logística inversa de devoluciones** — mejor disposición por SKU devuelto
    (opcional: corre si mandás `devoluciones.csv`).
15. **Registro de riesgos** — mapa de calor EMV/RPN de tu cadena (opcional: corre
    si mandás `riesgos.csv`, cadencia trimestral).

Los tools 9–15 se sumaron el 2026-07-18: son "universales" (aplican a
cualquier negocio sin importar tamaño) y de bajo costo marginal de cómputo,
así que entran al Starter sin subir el precio base. Los 6 marcados
"opcional" corren automáticamente el mes que mandes su archivo — si no lo
mandás, simplemente se omiten y el resto del paquete se entrega igual.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática. Si uno
solo falla, el paquete completo no se emite ese ciclo — no entregamos números a
medias.

## Qué te pedimos

Una carpeta con 3 archivos al inicio de cada ciclo (los mismos cada mes):

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `ventas.csv` | Historial de ventas con precio | `date, product_id, quantity, unit_cost, price` |
| `maestro.csv` | Maestro de productos | `sku` (+ nombre, código de barras, costo) |
| `planilla.xlsx` | Tu planilla de reposición, tal como está | la detectamos automáticamente |

Opcionales cuando apliquen: `supuestos.csv` (rangos para el what-if; si no lo
mandas usamos una plantilla estándar ±20%), `compra_estacional.csv` (compras de
temporada), `stock.csv`, `finanzas.csv`, `conteos.csv`, `importaciones.csv`,
`devoluciones.csv` y `riesgos.csv` (destraban los análisis 10–15 de la lista
de arriba el mes que los mandes). Los parámetros de tu negocio (costo de
mantener, nivel de servicio, plazos) se relevan **una sola vez** y quedan
guardados en tu perfil.

## Cómo se ve el mes

1. Mandas la carpeta (día 1).
2. Corremos, validamos y aplicamos la compuerta de QA (días 1–2).
3. Recibes el paquete completo + 45 minutos de revisión por videollamada.
4. Apruebas el plan de compra; tu planilla vuelve con las cantidades staged y
   reversibles.

## Qué sigue después

Cuando tu operación crezca a multi-almacén, ERP o importaciones, el plan
**Growth** (desde USD 1.500/mes) suma la operación completa: reposición
conectada a Odoo, red de distribución, DDMRP, simulación, costo de servir,
sourcing y más, con una revisión ejecutiva trimestral (QBR).

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
