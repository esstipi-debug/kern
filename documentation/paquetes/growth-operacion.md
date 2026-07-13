# Growth — Operación Completa de Supply Chain

> **USD 4.000 / mes** · ciclo mensual + revisión ejecutiva trimestral (QBR)
> Para empresas en crecimiento, multi-almacén o multi-canal, operando con un ERP
> (Odoo) o migrando hacia uno.

## Qué recibes

**Cada mes**, un reporte ejecutivo consolidado más el ciclo completo de inventario
(todo lo del plan Starter) ampliado con la operación end-to-end. Hasta **26
análisis** cubiertos por el alcance — se corren los que tu operación activa:

**Núcleo mensual**
- Pronóstico, ABC-XYZ, política de inventario por SKU y análisis de sensibilidad.
- Tu planilla devuelta con el plan de compra staged y reversible — o, si operas
  Odoo, la **reposición conectada directo a tu ERP**: puntos de reorden y borradores
  de orden de compra staged dentro de Odoo, siempre reversibles y con tu aprobación.
- Excedente y obsoletos con efectivo en riesgo; KPIs financieros (rotación, DIO,
  GMROI, cash-to-cash).
- **Optimización de precios** por elasticidad, con las movidas de precio en las que
  el modelo tiene confianza estadística (y las que no, marcadas como tales).
- **Costo de servir** por canal/segmento: qué clientes te dejan margen y cuáles te
  lo comen, con lente de capital de trabajo.
- Exactitud de inventario (conteos vs. sistema), programa de conteo cíclico, FEFO y
  vencimientos si manejas perecederos, devoluciones y logística inversa.

**Si tienes red de distribución**
- Ubicación óptima del stock de seguridad en la cadena (multi-echelon), buffers
  DDMRP, plan de distribución por sucursal (DRP) y políticas afinadas por
  simulación Monte Carlo.

**Cada trimestre (QBR)**
- Scorecard y ranking de proveedores (OTIF, calidad, precio) para la negociación.
- Mapa de riesgos de la cadena con pérdida esperada anual y mitigaciones rankeadas.
- Benchmarking de eficiencia entre bodegas/sucursales (DEA).
- Curva de aprendizaje y costo de importación (landed cost) cuando aplique.

**Garantía de calidad:** cada análisis pasa una compuerta de QA automática. Si uno
solo de los análisis ejecutados falla, el paquete completo no se emite ese ciclo.

## Qué te pedimos

El núcleo son **7 archivos** al inicio de cada ciclo:

| Archivo | Contenido | Columnas mínimas |
|---|---|---|
| `ventas.csv` | Historial de ventas con precio | `date, product_id, quantity, unit_cost, price` |
| `maestro.csv` | Maestro de productos | `sku` (+ nombre, código de barras, costo) |
| `planilla.xlsx` | Tu planilla de reposición (si no operas Odoo) | se detecta automáticamente |
| `stock.csv` | Stock a mano por SKU | `product_id, on_hand, daily_demand` |
| `finanzas.csv` | COGS e inventario promedio por SKU | `product_id, cogs, avg_inventory_value` |
| `pedidos.csv` | Líneas de pedido por canal/segmento | `segment, revenue` (+ costo, flete) |
| `supuestos.csv` | Rangos para el what-if (opcional) | `driver, low, high` |

Los módulos adicionales se activan enviando su archivo cuando corresponda:
conteos físicos, lotes con vencimiento, red de distribución, entregas de
proveedores, importaciones, devoluciones, registro de riesgos. Si operas Odoo,
la conexión reemplaza varios de estos archivos — se configura una sola vez.

## Cómo se ve el ciclo

1. Mandas la carpeta (o la conexión Odoo ya está activa) — día 1.
2. Corremos el ciclo completo con compuerta de QA — días 1–3.
3. Recibes el paquete + 60–90 minutos de revisión ejecutiva mensual.
4. Apruebas: cada cambio a tu sistema (planilla u Odoo) queda staged, reversible
   y auditado. Nada se aplica sin tu visto bueno.
5. Una vez por trimestre, el QBR: proveedores, riesgos, eficiencia y roadmap.

## El camino típico

La mayoría de nuestros clientes arranca con el **Diagnóstico de Arranque**
(USD 1.500–2.500, 2 semanas) para cuantificar el problema, opera 3–6 meses en
**Starter** y sube a Growth cuando se activa el segundo almacén, el ERP o el
canal mayorista. También puedes entrar directo si tu operación ya lo amerita.

---

*Este paquete corre sobre **Kern** (antes Linchpin) - el nucleo de decisiones de la agencia: cada resultado pasa un QA-gate que veta entregables debiles, cita las fuentes del campo en que se apoya (25 obras curadas), y toda escritura a tu sistema es staged, aprobada y reversible. La evolucion completa del nombre: [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
