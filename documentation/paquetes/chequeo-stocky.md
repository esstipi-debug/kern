# Chequeo de migracion Stocky

> **USD 350–400 unico** · Operator-run sobre los CSV de Stocky · 1 entregable
> Para la tienda Shopify que usaba Stocky (Shopify lo saco del App Store el 2-feb-2026 y lo apaga el **31-ago-2026**) y necesita saber que cubre Shopify nativo y que no.

## Que recibes

Un veredicto honesto, no un dashboard:

1. Parse de los CSV de Stocky (proveedores, ordenes de compra, puntos de reorden).
2. Auditoria del maestro de SKUs (duplicados, min>max, reorden<=0).
3. Comparacion contra lo que Shopify nativo realmente cubre: nativo **no** cubre OCs / proveedores / reorden / forecasting — esa brecha es Kern.

Read-only sobre CSV. **Sin API Shopify, sin writeback, sin app que instalar.**

## Que te pedimos

Los exports CSV de Stocky (proveedores, OCs, puntos de reorden) y el maestro de SKUs. Los corre el operador con `examples/run_stocky_migration.py`.

## Como se ve

1. Exportas Stocky (dia 1).
2. Corremos el chequeo y te devolvemos el reporte (dias 1-3).
3. Decidis: Alerts USD 49-99/mes, Starter Mass USD 79/mes, o nada.

## Que sigue despues

El chequeo es el peldaño 0 del embudo Shopify. No reemplaza un retainer. Si el veredicto dice que nativo te alcanza, te lo decimos — no hay upsell forzado.

---

*Corre sobre **Kern** — `jobs/stocky_migration_job.py` + `src/stocky_migration.py`. [KERN_IDENTIDAD_Y_FILOSOFIA.md](../KERN_IDENTIDAD_Y_FILOSOFIA.md).*
