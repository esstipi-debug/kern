---
pair: stocky-alt
lang: es
slug: alternativa-stocky
title: "Alternativa a Stocky: los tres trabajos a reemplazar antes del 31 de agosto de 2026"
description: "Stocky cierra el 31 de agosto de 2026. Los tres trabajos a reemplazar (pronóstico, punto de reorden, alerta) y con qué regla escrita los cubre Kern."
date: 2026-08-25
updated: 2026-08-25
query: "alternativa a Stocky Shopify"
answer: >
  Stocky salió de la App Store de Shopify el 2 de febrero de 2026 y cierra por
  completo el 31 de agosto de 2026. Hacía tres trabajos: pronóstico de demanda
  con tu historial, punto de reorden con sugerencia de orden de compra, y alerta
  de stock bajo. Una alternativa real cubre los tres. Kern los cubre desde un CSV
  que vos exportás, sin instalar una app ni dar permisos a tu tienda.
cta: both
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/stocky_migration.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/safety_stock.py
  - kind: code
    ref: src/demand_variability.py
  - kind: code
    ref: src/alerting.py
  - kind: code
    ref: jobs/inventory_optimization.py
  - kind: code
    ref: webapp/demo_scan.py
  - kind: book
    ref: "Vandeput, Inventory Optimization: Models and Simulations — Cap. 3 (Reorder Point), Cap. 4 (Safety Stock) y Cap. 9 (demanda gamma, más allá de la normal)"
faq:
  - q: "¿Cuándo cierra Stocky?"
    a: "Shopify lo sacó de la App Store el 2 de febrero de 2026 y lo apaga por completo el 31 de agosto de 2026. Si lo tenés instalado, funciona hasta esa fecha."
  - q: "¿Tengo que instalar una app en Shopify?"
    a: "No. Kern lee un CSV que vos exportás, o tu propia planilla. No es una app de la tienda y no pide permisos permanentes."
  - q: "¿Kern reemplaza pronóstico, punto de reorden y alerta?"
    a: "Los tres, cada uno con una regla que podés leer: σ_e en src/forecasting.py, el colchón bajo demanda normal (z·σ·√τ) en src/safety_stock.py, y los eventos con sus umbrales en src/alerting.py."
  - q: "¿Es otra suscripción mensual?"
    a: "No por defecto. La entrada es un diagnóstico de alcance fijo, una vez; el seguimiento continuo es opcional y posterior, no el único camino."
  - q: "¿Escribe en mi tienda?"
    a: "No. La salida es un plan de reposición en seco, reversible, que una persona revisa y firma. De los cuatro desenlaces posibles, tres se detienen en un humano por diseño."
noindex: false
---

# Alternativa a Stocky: los tres trabajos a reemplazar antes del 31 de agosto de 2026

Stocky salió de la App Store de Shopify el 2 de febrero de 2026 y cierra por completo el 31 de agosto de 2026. Hacía tres trabajos: pronóstico de demanda con tu historial, punto de reorden con sugerencia de orden de compra, y alerta de stock bajo. Una alternativa real cubre los tres. Kern los cubre desde un CSV que vos exportás, sin instalar una app ni dar permisos a tu tienda.

## Qué cierra y cuándo

Son dos fechas, no una: el 2 de febrero de 2026 Shopify retiró Stocky de la App Store —desde ahí, sin instalaciones nuevas— y el 31 de agosto de 2026 lo apaga del todo.

Y si nunca usaste Shopify —si operás sobre Odoo o sobre una planilla— la ola igual te describe: el trabajo es el mismo, pronosticar, reponer y avisar.

## Los tres trabajos a reemplazar

1. Pronóstico de demanda por SKU, desde el historial de ventas.
2. Punto de reorden y sugerencia de orden de compra.
3. Alerta de stock bajo antes de quebrar.

## El pronóstico y su dispersión

Un pronóstico no es solo el número esperado: es también cuánto se equivoca, y eso dimensiona el inventario. `src/forecasting.py` devuelve los dos, `forecast` y `error_std` —la desviación estándar de los errores a un paso, σ_e—. La dispersión cruda solo sería correcta con demanda estacionaria, y casi nunca lo es.

El patrón dicta el método: `is_intermittent` compara el intervalo medio entre demandas contra `INTERMITTENT_ADI_THRESHOLD = 1.32` (Syntetos-Boylan) con un `>=`, así que 1,32 exacto ya entra por Croston —o TSB, en el camino moderno—; por debajo, suavizado exponencial.

Y hay un piso de historia, `MIN_PERIODS_FOR_POLICY = 6`: con *n* períodos hay *n−1* errores de un paso, y la desviación estándar muestral de *m* errores carga un error relativo cercano a 1/√(2·(m−1)) —con seis períodos son cinco errores, y 1/√8 ≈ 35%—. El archivo lo llama umbral de rechazo, no perilla: por debajo, la salida honesta es "no hay suficiente historia", nunca una cantidad.

## El colchón y el punto de reorden

`src/safety_stock.py` escribe la fórmula del colchón bajo demanda normal y la cita: Ss = z_α · σ_d · √τ (ec. 4.3), con z_α = Φ⁻¹(α) y τ los períodos de riesgo —lead time en revisión continua, lead time más período de revisión en la periódica—. Es el capítulo 4 de *Inventory Optimization: Models and Simulations* (Vandeput); el punto de reorden, el 3.

No es la única regla del módulo, y el código lo dice: `SafetyStockResult` guarda con qué distribución quedó dimensionado el colchón, porque no siempre es la normal. `src/demand_variability.py` con `distribution="auto"` pasa a gamma cuando la asimetría observada supera σ/μ, y ahí el colchón es el cuantil gamma menos la demanda del período de riesgo, algo que z no describe (capítulo 9).

Ej. ilustrativo, no datos de cliente, rama normal: con σ_e de 12 unidades semanales, lead time de 4 semanas y 95% de nivel de servicio (z ≈ 1,64), el colchón es 1,64 × 12 × √4 ≈ 39 unidades, y el punto de reorden le suma la demanda esperada de esas 4 semanas.

`jobs/inventory_optimization.py` encadena las piezas para la cartera —pronóstico con σ_e, política (s,Q) o (R,S) por SKU, asignación bajo presupuesto— y separa en contadores propios tres motivos de no-cálculo: demanda agregada cero, ningún costo unitario usable e historia insuficiente —el último no es dato roto sino conocimiento faltante—.

## La alerta de stock bajo

`src/alerting.py` resuelve cada SKU en como mucho un evento —las señales no se duplican— y son cuatro: `stockout_risk` (alta), `reorder_due` (media), `dead_stock` (media) y `excess` (baja). Lee `product_id`, `on_hand`, `reorder_point` y `avg_daily_demand`, y emite el lote como un traspaso ejecutable, no como una advertencia suelta.

Los umbrales están escritos —`critical_cover_days = 7.0` y `excess_cover_days = 90.0`— sobre una cobertura de `on_hand / avg_daily_demand`. Ej.: 40 unidades en mano, punto de reorden 60 y demanda de 8 por día dan 5 días de cobertura, bajo el piso de 7: riesgo de quiebre. La misma fila con demanda de 4 da 10 días: sigue bajo el punto de reorden, pero sale como reposición debida.

## Qué no cruza la migración

Antes de "qué herramienta compro" viene "qué sobrevive el traslado". `src/stocky_migration.py` lo responde contra una única constante auditable, `SHOPIFY_NATIVE_COVERAGE`: cada veredicto apunta a un hecho declarado, no a prosa inventada.

Lo nativo no está vacío —crea y recibe órdenes de compra básicas y guarda un proveedor por producto— y aun así las cuatro categorías están en `False`, porque la pregunta no es si la función existe sino si tus datos cruzan. El reorden automático no se recrea; el import de órdenes de compra solo crea líneas en un borrador, sin estados pasados, cantidades recibidas ni vínculo a proveedor; los proveedores no se exportan; y fuera de Stocky no hay pronóstico con tu historial.

El veredicto tiene tres valores y el tercero define el criterio: `migrates_clean`, `gap` y `not_in_export`. Una categoría es brecha solo si entregaste registros de ella; si no, el chequeo no la declara sana: informa que no la evaluó. Igual con el maestro de reorden: un mínimo que el export no dejó medir se cuenta aparte de uno defectuoso, porque ausente y cero no son el mismo valor.

## Comparado con qué

**Otro dashboard mensual.** El SaaS de planificación de inventario —Netstock, Inventory Planner, Prediko— te da la herramienta y te deja operándola: el método correcto queda como ajuste opcional, empezando por poner la dispersión cruda donde va el error de pronóstico. Y la mayoría informa, no opera.

**Excel.** La fórmula se escribe una vez; lo que se erosiona es aplicarla igual dos veces seguidas, con el mismo nivel de servicio y el mismo trato del dato faltante.

**Min/max del ERP.** Umbrales estáticos: dicen cuándo reponer, no pronostican el patrón de demanda ni dimensionan el colchón con el error del pronóstico.

**Un consultor.** Entrega el análisis, y bien, una vez; bajo presión el paso que salta es simular antes de recomendar.

**Un chatbot o una "IA de inventario" genérica.** Devuelve una cifra plausible, sin fuente ni reversibilidad. Acá la regla es la opuesta: si el paso de QA falla, no hay entregable.

## Lo que Kern no hace

Kern no es una app de Shopify y no se instala en tu tienda: no hay integración de inventario con Shopify ni con Mercado Libre, no hay conector que sincronice tus datos de Stocky y no hay permisos permanentes que otorgar. El insumo es un CSV que vos exportás.

Tampoco escribe solo: un cambio de reposición o de punto de reorden se prepara en seco y reversible, para que una persona lo revise y lo firme. De los cuatro desenlaces posibles, tres se detienen en un humano por diseño.

Las cifras de arriba son ilustrativas, salidas de las reglas de los módulos citados: no hay resultados de clientes ni testimonios. Kern es independiente, no afiliado a Shopify; "Stocky" es un producto de Shopify.

## FAQ

### ¿Cuándo cierra Stocky?

Shopify lo sacó de la App Store el 2 de febrero de 2026 y lo apaga por completo el 31 de agosto de 2026. Si lo tenés instalado, funciona hasta esa fecha.

### ¿Tengo que instalar una app en Shopify?

No. Kern lee un CSV que vos exportás, o tu propia planilla. No es una app de la tienda y no pide permisos permanentes.

### ¿Kern reemplaza pronóstico, punto de reorden y alerta?

Los tres, cada uno con una regla que podés leer: σ_e en `src/forecasting.py`, el colchón bajo demanda normal (z·σ·√τ) en `src/safety_stock.py`, y los eventos con sus umbrales en `src/alerting.py`.

### ¿Es otra suscripción mensual?

No por defecto. La entrada es un diagnóstico de alcance fijo, una vez; el seguimiento continuo es opcional y posterior, no el único camino.

### ¿Escribe en mi tienda?

No. La salida es un plan de reposición en seco, reversible, que una persona revisa y firma. De los cuatro desenlaces posibles, tres se detienen en un humano por diseño.

## Próximo paso

[Escaneá tu CSV en el demo](/demo) con `product_id, on_hand, daily_demand`: `webapp/demo_scan.py` corre sobre esa foto tres análisis —excedente y obsoleto, ABC-XYZ y KPIs financieros— y si el `verify()` de alguno levanta un problema, no escribe entregable. Para reemplazar los tres trabajos está el [Diagnóstico de Arranque](/paquetes/diagnostico-arranque). La [versión en inglés](/stocky-alternative) habla al lector de Shopify.
