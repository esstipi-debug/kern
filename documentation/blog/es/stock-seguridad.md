---
pair: safety-stock
lang: es
slug: stock-seguridad
title: "Stock de seguridad: la fórmula, y por qué el sigma no es el de tus ventas"
description: "Ss = z × σ × √τ, con cada símbolo definido, y el error más común del oficio: usar el STDEV de las ventas en lugar del error de pronóstico."
date: 2026-08-25
updated: 2026-08-25
query: "calcular stock de seguridad"
answer: >
  El stock de seguridad se calcula como Ss = z_alpha × sigma_d × raíz de tau,
  donde z_alpha es la inversa de la normal estándar en el nivel de servicio de
  ciclo, sigma_d es la dispersión de la demanda por período y tau es el período
  de riesgo: el plazo de entrega en revisión continua, o plazo más período de
  revisión en revisión periódica. El sigma correcto es el del error de
  pronóstico, no el de tus ventas.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/safety_stock.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/risk_period.py
  - kind: code
    ref: src/fill_rate.py
  - kind: book
    ref: "Vandeput, Inventory Optimization (Models and Simulations) — Safety Stock, Ch. 4"
  - kind: book
    ref: "Boylan & Syntetos, Intermittent Demand Forecasting — Croston's Method, Ch. 6.5"
faq:
  - q: "¿El stock de seguridad es lo mismo que el punto de reorden?"
    a: "No. El punto de reorden es la demanda esperada durante el período de riesgo más el stock de seguridad: en src/policies.py la revisión continua lo arma como s = mu_x + Ss."
  - q: "¿95% de qué: nivel de servicio de ciclo o fill rate?"
    a: "De nivel de servicio de ciclo: la probabilidad de no quebrar durante un ciclo. El fill rate es la fracción de la demanda servida desde stock, y vive en otro módulo: src/safety_stock.py es el capítulo 4; src/fill_rate.py, el 7."
  - q: "¿Por qué no usar la desviación estándar de las ventas semanales?"
    a: "Porque esa es la dispersión de la demanda, no la del error de pronóstico: con tendencia o estacionalidad, el STDEV de las ventas cuenta como riesgo la parte que tu pronóstico sí anticipó. src/forecasting.py entrega sigma_e en su lugar."
  - q: "¿Y si el plazo de entrega también es variable?"
    a: "Es otra fórmula, no esta. src/risk_period.py combina las dos fuentes como sigma_x = raíz(tau × sigma_d² + sigma_L² × mu_d²), ecuaciones 6.4 y 6.5 del mismo libro. Con un proveedor irregular, el término del plazo domina."
  - q: "¿Cuánta historia hace falta para dimensionar un buffer?"
    a: "Seis períodos observados: la constante MIN_PERIODS_FOR_POLICY de src/forecasting.py. n períodos dan n-1 errores a un paso, y con n = 6 la desviación de esos errores ya arrastra un error estándar relativo de ~35%."
noindex: false
---

# Stock de seguridad: la fórmula, y por qué el sigma no es el de tus ventas

El stock de seguridad se calcula como Ss = z_alpha × sigma_d × raíz de tau, donde z_alpha es la inversa de la normal estándar en el nivel de servicio de ciclo, sigma_d es la dispersión de la demanda por período y tau es el período de riesgo: el plazo de entrega en revisión continua, o plazo más período de revisión en revisión periódica. El sigma correcto es el del error de pronóstico, no el de tus ventas.

## Qué se mide

No es "un colchón por las dudas": es la cantidad que compra una probabilidad declarada, la de no quebrar mientras estás expuesto. `src/safety_stock.py` la saca de una línea, la ecuación 4.3 del capítulo 4 de Vandeput, *Inventory Optimization*:

```
Ss = z_alpha * sigma_d * sqrt(tau)
```

- **`z_alpha`** — `service_level_factor()`, o sea `Phi^{-1}(alpha)`: la inversa de la normal estándar en tu nivel de servicio de ciclo. Rechaza cualquier `alpha` fuera del intervalo abierto (0, 1).
- **`sigma_d`** — la dispersión de la demanda por período. El símbolo que casi todos cargan mal; tiene su propia sección abajo.
- **`tau`** (`risk_periods`) — el período de riesgo, en la misma unidad que `sigma_d`.

La raíz de `tau` es lo que la intuición no acierta: duplicar el plazo no duplica el buffer, lo multiplica por 1,41.

## Qué es tau: revisión continua o periódica

El docstring lo fija sin ambigüedad: `tau` es el plazo L en una política de revisión continua (s, Q), y R + L en una periódica (R, S). `src/risk_period.py` lo calcula como `tau = mean_lead_time + review_period`.

Con revisión continua la única ventana en la que podés quedar corto va entre disparar el pedido y recibirlo. Con revisión periódica mirás una vez cada R: si el consumo se dispara justo después de una revisión, nadie lo ve hasta la siguiente, y recién ahí arranca el plazo.

## Cómo se calcula, con números ilustrativos

Un importador con demanda semanal y tránsito de seis semanas. Cifras ilustrativas (ej.):

| Insumo | Valor (ej.) |
|---|---|
| `demand_std_per_period` (sigma_e) | 40 unidades/semana |
| Plazo de entrega L | 6 semanas |
| `cycle_service_level` (alpha) | 0,95 |
| `z_alpha = Phi^{-1}(0,95)` | 1,6449 |
| `risk_periods` (tau), política (s, Q) | 6 |

`Ss = 1,6449 × 40 × √6 = 161 unidades`.

Pasá a revisión periódica con R de una semana: `tau = 7` y `Ss = 174 unidades`, trece unidades más de capital por el calendario. Volvé a la continua y subí el servicio de 0,95 a 0,98: `z_alpha` pasa a 2,0537 y el buffer, a 201 unidades — un cuarto más de inventario por tres puntos, y el precio por punto sigue subiendo.

## El sigma correcto: error de pronóstico, no dispersión de ventas

Acá está la razón de ser de este artículo. El docstring de `src/forecasting.py` lo dice con todas las letras: **históricamente esa dispersión era la desviación estándar cruda de la demanda, que solo es correcta si la demanda es estacionaria**. El módulo devuelve `error_std`, que es sigma_e: la desviación de los errores a un paso.

Lo que te rompe no es cuánto varía la demanda alrededor de su promedio, sino cuánto se equivoca tu pronóstico. Con tendencia o estacionalidad, un pronóstico decente las sigue; el `=STDEV(ventas)` de la planilla no sabe que el pronóstico existe y carga esa tendencia entera al riesgo. Con los mismos 40 de sigma_e: si el STDEV crudo diera 62, el cálculo devolvería 250 unidades en vez de 161. Un 55% más de buffer (ej.), sin un punto más de servicio.

El módulo deja registro de cuál usó. `ForecastResult.sigma_source` devuelve `forecast_error_oos` cuando sigma_e se midió contra errores que el modelo nunca vio, `forecast_error` para residuos dentro de muestra, `demand_std_fallback` cuando entró la desviación cruda de suplente, y `unavailable` cuando ninguna de las dos es finita y positiva. Esa última se abre en dos dentro de `to_engine_inputs`: si una dispersión no es un número real —NaN o infinito— levanta un error en vez de sustituirla por cero, porque un cero ahí imprimiría "no hace falta buffer" como si fuera un hecho; si las dos son finitas y valen cero, eso es una historia plana medida, y pasa `sigma = 0.0` a propósito. `jobs/qa.py` vigila la diferencia: `unavailable` con buffer positivo es un número fabricado y no sale.

## Cuando la demanda es intermitente

La fórmula asume demanda normal. Para un SKU que vende de a saltos ese es el modelo equivocado por defecto, y `src/forecasting.py` lo detecta antes de elegir método: un ADI (períodos totales sobre períodos con demanda) igual o mayor a `INTERMITTENT_ADI_THRESHOLD = 1.32`, el corte de Syntetos-Boylan, saca al SKU del suavizado por defecto — Croston en el camino propio (Boylan & Syntetos, *Intermittent Demand Forecasting*, cap. 6.5), TSB con el pronosticador moderno de `src/forecasting_auto.py` instalado. Y si la demanda del período de riesgo es asimétrica, `src/demand_variability.py` ajusta una gamma: `Ss = iota_gamma − mu_x`, que z no describe.

## Comparado con qué

**Excel con `STDEV`.** La fórmula entra en una celda; eso nunca fue el problema. Lo que la planilla no carga es la distinción entre el sigma de las ventas y el del error.

**Min/max en Odoo.** Un mínimo y un máximo son la *salida* de una política, no la política: no declaran alpha, ni tau, ni sigma, así que cuando el servicio cae no hay nada que mover.

**Un consultor.** Hace el cálculo bien, y una vez. La diferencia no es la fórmula sino la frecuencia: el mismo método cada ciclo, sobre cada SKU.

**Un chatbot sobre tu export.** Un número redondo con explicación fluida, sin la definición del sigma que usó y sin el mismo resultado dos veces.

## Lo que Kern no hace

**No adivina la demanda.** El pronóstico lleva su propio sesgo y error, y el buffer se dimensiona contra ese error, no contra el pronóstico.

**No dimensiona una política sobre una historia que no la sostiene.** `MIN_PERIODS_FOR_POLICY = 6` es, en palabras del módulo, un umbral de negativa y no una perilla: por debajo, `jobs/inventory_optimization.py` deja el SKU como `insufficient_history` y no devuelve cantidad.

**No afirma un nivel de servicio que no calculó.** `achieved_service_level()` es la inversa analítica de `safety_stock()`: dado un buffer, devuelve el alpha que ese buffer compra.

**No escribe el stock de seguridad en tu ERP desde este artículo.** Kern prepara el trabajo y una persona firma lo irreversible; de los cuatro desenlaces posibles, tres se detienen en un humano por diseño.

El 0,95 de los ejemplos es el valor por defecto del motor (`service_level: float = 0.95` en `jobs/inventory_optimization.py`), no una recomendación universal.

## FAQ

### ¿El stock de seguridad es lo mismo que el punto de reorden?

No. El punto de reorden es la demanda esperada durante el período de riesgo más el stock de seguridad: en `src/policies.py` la revisión continua lo arma como `s = mu_x + Ss`.

### ¿95% de qué: nivel de servicio de ciclo o fill rate?

De nivel de servicio de ciclo: la probabilidad de no quebrar durante un ciclo. El fill rate es la fracción de la demanda servida desde stock, y vive en otro módulo: `src/safety_stock.py` es el capítulo 4; `src/fill_rate.py`, el 7.

### ¿Por qué no usar la desviación estándar de las ventas semanales?

Porque esa es la dispersión de la demanda, no la del error de pronóstico: con tendencia o estacionalidad, el `STDEV` de las ventas cuenta como riesgo la parte que tu pronóstico sí anticipó. `src/forecasting.py` entrega sigma_e en su lugar.

### ¿Y si el plazo de entrega también es variable?

Es otra fórmula, no esta. `src/risk_period.py` combina las dos fuentes como `sigma_x = raíz(tau × sigma_d² + sigma_L² × mu_d²)`, ecuaciones 6.4 y 6.5 del mismo libro. Con un proveedor irregular, el término del plazo domina.

### ¿Cuánta historia hace falta para dimensionar un buffer?

Seis períodos observados: la constante `MIN_PERIODS_FOR_POLICY` de `src/forecasting.py`. n períodos dan n-1 errores a un paso, y con n = 6 la desviación de esos errores ya arrastra un error estándar relativo de ~35%.

## Siguiente paso

Dimensionar el stock de seguridad es trabajo del [Diagnóstico de Arranque](/paquetes/diagnostico-arranque), no del escaneo público. Lo que sí podés medir hoy es la otra mitad de la fórmula: [escaneá tu CSV de recepciones](/demo) y te devuelve el plazo real de cada proveedor contra el declarado, con desviación y p90. Ese promedio medido es la L de tu `tau` —todo tu `tau` en revisión continua, y L + R en cuanto sumás el período de revisión— y esa desviación es el término `sigma_L`.
