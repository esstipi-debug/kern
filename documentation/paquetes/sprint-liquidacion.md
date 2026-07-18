# Sprint de Liquidación

> **10–20% del cash efectivamente recuperado** (piso USD 1.500) — no es un
> precio fijo. Cobramos solo un porcentaje de lo que realmente recuperás:
> si no se recupera nada, no se cobra nada.

## Qué recibes

Un **plan de liquidación priorizado, con precio y fecha**, no solo un
diagnóstico:

1. **Auditoría de calidad de datos** — antes de planificar nada, verificamos
   que tu maestro de productos esté limpio (SKUs duplicados, códigos
   inválidos) para que el plan se apoye en datos confiables.
2. **Clasificación de excedente y stock muerto** — cuánto capital está
   atrapado, en qué SKUs y por qué (sin venta hace más de 180 días o muy por
   encima de tu cobertura objetivo).
3. **Plan de liquidación por SKU** — para cada producto en riesgo: precio de
   liquidación recomendado, semanas estimadas para agotar el stock, y cuánto
   cash se recupera comparado con darlo de baja a cero. Cuando tenés
   historial de precios, el precio surge de una curva de elasticidad real;
   si no, aplicamos un descuento estándar documentado o una recuperación de
   salvamento — nunca un número inventado.
4. **Análisis de precios (opcional)** — si nos mandás tu historial de ventas
   con precio, sumamos una lectura de elasticidad de precio por SKU que
   afina el plan de liquidación.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática
antes de emitirse. Si un solo análisis no la pasa, el plan completo no se
entrega.

## Cómo funciona el precio

Al arrancar el sprint recibís una **estimación** del cash recuperable y del
honorario correspondiente (10–20%, con un piso de USD 1.500 — nunca más de
lo que se recupera). Es una proyección, no una factura.

Al cerrar el sprint (2–3 semanas después, con tus ventas reales de ese
período) medimos el **recupero real** SKU por SKU contra la estimación y
emitimos un anexo de cierre. **El honorario final se factura sobre el
recupero real, nunca sobre la proyección inicial.**

## Qué te pedimos

Los mismos archivos que el Diagnóstico de Arranque — si ya lo corriste, no
hace falta mandar nada nuevo:

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `maestro.csv` | Maestro de productos/SKUs | `sku` (+ name, gtin, unit_cost opcionales) |
| `stock.csv` | Stock a mano por SKU con su demanda diaria | `product_id, on_hand, daily_demand` (+ unit_cost, days_since_last_sale opcionales) |
| `ventas.csv` (opcional) | Historial de ventas con precio, si querés el análisis de precios | `date, product_id, quantity, unit_cost, price` |

## Cómo trabajamos

Un sprint de **2–3 semanas**: corremos los análisis sobre tus datos,
validamos contigo el horizonte de liquidación y el margen mínimo aceptable, y
te entregamos el plan priorizado listo para ejecutar (vos decidís y ejecutás
la liquidación — el plan no cambia precios ni dispone stock por su cuenta).
Al cierre, con tus ventas reales del período, medimos el recupero efectivo y
cerramos con el anexo de honorarios real.

## Qué sigue después

El Sprint de Liquidación resuelve el stock muerto que ya tenés — no evita que
se vuelva a acumular. Si querés que la clasificación de excedente, el
pronóstico y la política de reposición corran todos los meses para que este
problema no vuelva a aparecer, ese es el alcance de **Starter — Fundamentos
de Inventario** (desde USD 900/mes).

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
