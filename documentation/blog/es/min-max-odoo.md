---
pair: minmax-erp
lang: es
slug: min-max-odoo
title: "Min/max en Odoo: por qué la regla de reorden no se corrige sola"
description: "Min/max en el ERP es una regla estática que alguien tipeó una vez. Dónde se rompe y qué agrega una política: sigma del error y punto de reorden."
date: 2026-08-25
updated: 2026-08-25
query: "min max Odoo reorden"
answer: >
  Min y max en Odoo son una regla estática: cuando el stock a mano baja del
  mínimo, el sistema repone hasta el máximo. La regla no pronostica el patrón de
  demanda, no dimensiona el colchón desde el error de pronóstico y no simula
  antes de recomendar. Una política gobernada calcula sigma del error, el
  período de riesgo y el punto de reorden, y deja la escritura en tu ERP en
  manos de una persona.
cta: diagnostico
offer: diagnostico-arranque
sources:
  - kind: code
    ref: jobs/excel_replenishment_job.py
  - kind: code
    ref: src/policies.py
  - kind: code
    ref: src/demand_variability.py
  - kind: code
    ref: src/safety_stock.py
  - kind: code
    ref: src/risk_period.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/connectors/odoo.py
  - kind: book
    ref: "Vandeput, Inventory Optimization (Models and Simulations) — Ch. 4 (Safety Stock), Ch. 5 (Continuous Review Policy), Ch. 6 (Risk Period, Stochastic Lead Time)"
faq:
  - q: "¿Kern reemplaza a Odoo?"
    a: "No. Odoo sigue siendo el sistema de registro; Kern calcula la política —punto de reorden y nivel objetivo— y, si escribe, lo hace por el plano de escritura segura."
  - q: "¿Va a cambiar mis min/max sin preguntar?"
    a: "No. El plan se monta como changeset en seco con celdas guardia sobre el stock y la señal de reorden: si esos insumos cambiaron antes de la aprobación, la escritura se niega."
  - q: "¿Min/max es lo mismo que un punto de reorden s?"
    a: "El disparo se parece; el número sale de otro lado. El min lo tipeó alguien; el s de src/policies.py es la demanda media del período de riesgo más el stock de seguridad."
  - q: "¿Qué archivo necesito para tener una primera política?"
    a: "Una planilla con SKU, stock a mano y punto de reorden o demanda por período. Una política propia pide además historia: src/forecasting.py exige seis períodos observados."
  - q: "¿Trabajan con implementadores de Odoo?"
    a: "Sí, como canal: el implementador mantiene la instancia y Kern aporta la capa de política sobre los datos que ya viven ahí. No publicamos nombres de partners."
noindex: false
---

# Min/max en Odoo: por qué la regla de reorden no se corrige sola

Min y max en Odoo son una regla estática: cuando el stock a mano baja del mínimo, el sistema repone hasta el máximo. La regla no pronostica el patrón de demanda, no dimensiona el colchón desde el error de pronóstico y no simula antes de recomendar. Una política gobernada calcula sigma del error, el período de riesgo y el punto de reorden, y deja la escritura en tu ERP en manos de una persona.

## Qué dice la regla

La regla nativa cabe en una línea, y Kern la reproduce tal cual cuando tu archivo solo trae ese dato. En `jobs/excel_replenishment_job.py`, el modo `reorder-point` pone el objetivo en `punto_de_reorden * order_up_to_factor` —2,0 por defecto— y pide la diferencia **solo** si el stock quedó por debajo del punto de reorden; por encima, la cantidad es cero.

El otro modo, `demand-cover`, gana apenas la planilla trae una columna de demanda, haya o no además un punto de reorden: el objetivo pasa a ser `demanda_por_período * cover_periods`, ocho por defecto, y ahí no hay umbral que cruzar. Ninguno de los dos responde de dónde salió el número.

## Dónde se rompe

**Cuando el lead time se mueve.** El mínimo tipeado tenía adentro un lead time. Si el proveedor pasa de tres a cinco semanas, la regla no se entera y la reposición llega tarde de forma sistemática.

**Cuando el SKU es intermitente.** Un repuesto que se vende en ráfagas separadas por semanas de cero tiene una media que no describe a nadie. `src/forecasting.py` marca esa condición con el umbral de Syntetos-Boylan (`INTERMITTENT_ADI_THRESHOLD = 1.32`) y cambia a Croston o TSB. Un min/max no cambia de método.

**Cuando falta la señal.** Un producto sin regla nunca dispara, y esa ausencia se lee igual que estar bien surtido. El job hace lo contrario: una celda de mínimo o de demanda en blanco **nunca** vale cero; la fila se excluye, se cuenta en `n_unplanned` y el entregable la nombra "filas que el plan NO pudo cubrir".

**Cuando hay una segunda bodega.** En `src/connectors/odoo.py`, `inventory_levels()` suma el stock de todas las ubicaciones internas por producto; las reglas, en cambio, viven como registros sueltos de `stock.warehouse.orderpoint`. Dos reglas disparan dos compras contra un total de red que ninguna mira.

## Qué agrega una política gobernada

**1. Pronóstico, y la dispersión correcta.** `src/forecasting.py` devuelve `error_std`: sigma del error de pronóstico de un paso adelante. Esa, y no la desviación estándar cruda de la demanda, alimenta el stock de seguridad; `sigma_source` deja registrado cuál se usó. Y hay un piso, `MIN_PERIODS_FOR_POLICY = 6`: con menos historia la salida honesta es "no alcanza", no una cantidad.

**2. El período de riesgo.** En `src/risk_period.py`, tau es el lead time para (s,Q), o lead time más revisión para (R,S). La demanda media es `μ_d * tau` y la dispersión combina las dos fuentes: `σ_x = raíz(tau * σ_d² + σ_L² * μ_d²)`.

**3. El stock de seguridad.** En el repo hay dos fórmulas y solo una gobierna. `src/policies.py` le pide el colchón a `safety_stock_risk_period`, en `src/demand_variability.py`, cuya rama normal es `Ss = z_α * σ_x`: z multiplica la dispersión del período de riesgo entero, con `z_α = Φ⁻¹(α)` desde `service_level_factor`, en `src/safety_stock.py`. La ecuación 4.3 de Vandeput, que ese archivo escribe `Ss = z_α * σ_d * raíz(tau)`, da idéntico solo con σ_L = 0, porque ahí `σ_x` es exactamente `σ_d * raíz(tau)`. Con lead time variable las dos se separan, y la que corre es la primera.

**4. El umbral.** `src/policies.py` cierra: en (s,Q) el punto de reorden es `s = μ_x + Ss`, con Q desde el EOQ; en (R,S) el nivel objetivo es `S = μ_x + Ss`. En Vandeput, *Inventory Optimization (Models and Simulations)*: safety stock, capítulo 4; continuous review, 5; período de riesgo con lead time estocástico, 6.

Un ejemplo ilustrativo (ej.): demanda media de 40 unidades por semana, sigma del error de 12, nivel de servicio de ciclo del 95% (`z ≈ 1,645`).

| Escenario | Lead time | σ del lead time | μ_x | σ_x | Ss | s |
|---|---|---|---|---|---|---|
| Min tipeado en el ERP | supuesto | ignorado | — | — | — | 120 |
| Lead time 3 sem, estable | 3 sem | 0 | 120 | 20,8 | 34 | 154 |
| Lead time 3 sem, σ_L = 1 sem | 3 sem | 1 sem | 120 | 45,1 | 74 | 194 |
| Lead time estirado a 5 sem | 5 sem | 0 | 200 | 26,8 | 44 | 244 |

El mínimo de 120 es exactamente la demanda media del lead time, con cero colchón. Y en la tercera fila, mover σ_L de cero a una semana más que duplica el colchón: ese término no tiene dónde entrar en la fórmula de un solo período.

Cuando ese cálculo existe, el job lo prefiere: con `optimized_targets`, el punto de reorden y el nivel objetivo de Kern reemplazan la cuenta de la planilla para ese SKU, en los dos modos, y cada línea queda etiquetada `kern_optimized` o `client_sheet`.

## Comparado con qué

**Min/max en Excel.** Mismo umbral, menos gobierno: la planilla no distingue un blanco de un cero ni deja constancia de qué supuesto de lead time produjo el número.

**La regla nativa del ERP.** Ejecuta impecable, mejor que cualquier persona. Lo que no hace es revisar su propio umbral: no pronostica, no dimensiona el colchón desde el error, no sabe que el proveedor cambió. Kern no la reemplaza: le entrega un número recalculado.

**Contratar un planificador.** Con oficio corre el método bien. También tiene 4.000 SKU y cierre de mes, y bajo presión el paso que se salta es simular. Cuando se va, en el ERP queda —otra vez— un min/max tipeado.

**Un chatbot sobre tu export de inventario.** Devuelve un párrafo fluido sobre tus reglas de reorden, pero no el mismo umbral dos veces para el mismo archivo.

## Lo que Kern no hace

No sobrescribe tus min/max en silencio. El plan se monta como changeset en seco, con celdas guardia sobre el stock y la señal de reorden: si esos insumos se movieron entre el montaje y la aprobación, la escritura se niega en vez de aplicar un plan viejo. Solo tras la aprobación de una persona hay respaldo, escritura atómica y rollback, sobre `nosilo_core.writeback`, el plano de escritura segura de `nosilo_core`, el control plane agnóstico de dominio debajo del writeback y de los resultados guiados.

Tampoco es "configurar y olvidar": de los cuatro desenlaces posibles, tres se detienen en un humano por diseño. Y el entregable declara sus límites — las cantidades se calculan contra el stock a mano, así que las órdenes en tránsito hay que restarlas antes de aprobar. El demo público no escribe en Odoo: lee un CSV que vos exportás.

## FAQ

### ¿Kern reemplaza a Odoo?

No. Odoo sigue siendo el sistema de registro; Kern calcula la política —punto de reorden y nivel objetivo— y, si escribe, lo hace por el plano de escritura segura.

### ¿Va a cambiar mis min/max sin preguntar?

No. El plan se monta como changeset en seco con celdas guardia sobre el stock y la señal de reorden: si esos insumos cambiaron antes de la aprobación, la escritura se niega.

### ¿Min/max es lo mismo que un punto de reorden s?

El disparo se parece; el número sale de otro lado. El min lo tipeó alguien; el `s` de `src/policies.py` es la demanda media del período de riesgo más el stock de seguridad.

### ¿Qué archivo necesito para tener una primera política?

Una planilla con SKU, stock a mano y punto de reorden o demanda por período. Una política propia pide además historia: `src/forecasting.py` exige seis períodos observados.

### ¿Trabajan con implementadores de Odoo?

Sí, como canal: el implementador mantiene la instancia y Kern aporta la capa de política sobre los datos que ya viven ahí. No publicamos nombres de partners.

## Siguiente paso

Para verlo sobre tus propios SKU, el paso es el [Diagnóstico de Arranque](/paquetes/diagnostico-arranque): sale de tu archivo, devuelve la política con el cálculo a la vista y nombra los SKU sin historia suficiente. Más liviano: [escaneá tu CSV en el demo](/demo), en solo lectura.
