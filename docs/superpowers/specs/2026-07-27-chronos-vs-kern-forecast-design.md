# Diseño: ¿un modelo fundacional de series temporales le gana al pronóstico de Kern?

Fecha: 2026-07-27. Estado: **diseño aprobado para ejecutar, cero código escrito.**
No mergear a `main`. No deployar. Rama de experimento aislada.

---

## 1. La decisión que sirve

Levantar o confirmar el **diferimiento** del veredicto "aliado condicional" sobre los modelos
fundacionales de series temporales (Chronos-2, TimesFM, Granite TTM). Tres lentes adversariales
lo dejaron diferido con una sola condición: *medirlo sobre demanda intermitente antes de escribir
una línea de integración.*

Salida binaria al final: **se cablea** (y qué se cablea) o **no se cablea** (y queda cerrado
con número, no con opinión).

No es objetivo de este experimento mejorar el pronóstico de producción. Es decidir si vale
la pena intentarlo.

---

## 2. El prerrequisito que hace o rompe el experimento

**Hoy Kern no puede identificar demanda intermitente.** `jobs/forecast_job.py` (`prepare_records`)
agrupa por SKU y ordena por fecha pero **nunca resamplea a grilla de calendario**: los períodos
con demanda cero desaparecen. Consecuencia medida sobre `superstore_canonical.csv`:
`ADI = 1.000` en los 1.862 SKU (`adi.nunique() == 1`), las ramas `intermittent`/`lumpy` son
código muerto, y `forecast_demand` devuelve `is_intermittent=False` siempre.

Sin grilla de calendario, el experimento mediría "¿quién pronostica mejor la demanda
intermitente?" sobre un universo donde, por construcción, la intermitencia no existe. La
pregunta se cae sola.

### Decisión de diseño (importante)

El resample se hace **solo dentro del harness del experimento**, en su propio módulo. **No se
toca `jobs/forecast_job.py`.**

Motivo: resamplear en producción cambia el cuadrante de casi todo el catálogo → cambia el método
elegido → cambia MASE/MAPE/FVA → invalida el titular "852/1862 le ganan al naive" de todas las
corridas históricas, y arrastra la marea de 274 archivos de test. Ese costo se paga **solo si el
experimento dice que valió la pena.** Primero se mide, después se paga.

---

## 3. Los contendientes

Cuatro, y el orden importa porque el más barato puede ganar y ahorrar todo el resto.

| # | Contendiente | De dónde sale | Costo de setup |
|---|---|---|---|
| A | **Piso**: `Naive` + `SeasonalNaive` | `statsforecast` 2.0.2 (instalado) | 0 |
| B | **Kern hoy**: el método que `forecast_demand` elige realmente | `src/forecasting.py` | 0 |
| C | **Especialistas de intermitencia**: `CrostonOptimized`, `TSB`, `ADIDA`, `IMAPA` | `statsforecast` 2.0.2 — **ya instalado y sin cablear** | 0 |
| D | **Chronos-2** zero-shot | HF `amazon/chronos-2`, Apache-2.0, 120M, CPU | requiere `torch` (**no instalado**) |

**Moirai queda excluido** (`cc-by-nc-4.0`, no comercial). No entra ni al experimento.

### El rival real de Chronos es C, no B

Si un Croston/TSB bien cableado gana o empata con Chronos-2, **no se cablea Chronos**: se cablea
Croston. Se ahorra `torch`, se ahorra subir la VM de Fly, se ahorra la integración entera, y el
resultado del experimento sigue siendo una mejora real del producto.

Por eso A/B/C se corren **primero y completos** antes de instalar `torch`. Si C gana, D no se
corre nunca.

---

## 4. Métricas

### Punto
`mase` y `rmsse` — ya existen en `src/forecast_metrics.py:64` y `:72`.

**`mape` está prohibido en este experimento.** Es indefinido cuando el real es cero, y la demanda
intermitente es mayormente ceros. Usarlo produciría infinitos o una exclusión silenciosa de
exactamente los SKU que el experimento quiere medir. (`src/forecast_metrics.py:36` existe y no se
usa acá.)

### Dispersión y calibración — esto es lo que decide
Hay que **agregar** (no existen hoy en `forecast_metrics.py`):

- `pinball_loss(actual, quantile_pred, tau)` para τ ∈ {0.1, 0.5, 0.9}
- `interval_coverage(actual, lo, hi)` — de un intervalo P10–P90 se espera 80% de cobertura
  empírica; reportar el desvío contra 80%

**Por qué esto pesa más que el punto:** el entregable de Kern no es el pronóstico, es la política.
`src/policies.py` calcula el safety stock a partir de **sigma**, no del punto. Kern hoy deriva
`error_std` (`src/forecasting.py:43`); Chronos-2 es probabilístico nativo y emite cuantiles. La
pregunta que decide el experimento es **cuál de las dos bandas está mejor calibrada**, no cuál
punto cae más cerca.

### El puente al negocio (obligatorio en el entregable)
Traducir la diferencia de calibración a unidades vía `src/policies.py`: **cuánto safety stock pide
cada método para el mismo nivel de servicio objetivo.** Ese es el único número de todo el
experimento que un cliente entiende, y es el que va en el entregable comercial.

---

## 5. Protocolo

- **Grilla**: semanal (`freq='W'`). El patrón de resample ya existe en el repo: `jobs/sop_job.py:52`.
- **Split**: rolling-origin, **4 orígenes**, horizonte **4 semanas**. No holdout único.
- **Escala de MASE/RMSSE**: se calcula **solo con train** de cada origen. Ver §7.
- **Datos**: `data/kaggle/superstore_canonical.csv` (1.862 SKU, 4 años) como primario;
  harness de Olist como segundo dataset si el runtime lo permite.
- **Estratificación**: cuadrante Syntetos-Boylan (`smooth` / `erratic` / `intermittent` / `lumpy`)
  calculado con el ADI **de grilla**, no el transaccional.

**Se reporta por estrato. Nunca el promedio global.** El promedio global es exactamente lo que
esconde la respuesta: si Chronos gana en `smooth` y pierde en `intermittent`, el promedio puede
decir "gana" y la conclusión sería falsa para el ICP, cuyo dolor es el SKU de baja rotación.

### Ponderación
`superstore_canonical.csv` tiene `unit_cost = 1.0` en las 9.826 filas, así que **no se puede
ponderar por valor** en este dataset. Reportar sin ponderar y **declararlo** como limitación.
No imputar un costo para poder ponderar.

---

## 6. Regla de decisión — declarada ANTES de correr

Comparación siempre contra **el mejor de los otros tres**, no contra Kern-hoy.

1. **Chronos-2 gana** si mejora **RMSSE ≥ 10%** *y* **pinball loss ≥ 10%** en los estratos
   `intermittent` + `lumpy` combinados. Solo entonces se discute arquitectura.
2. **Croston/TSB gana o empata con Chronos** → se cablea Croston/TSB, no Chronos.
   `statsforecast` ya está instalado; costo marginal ~0.
3. **Nada le gana al naive en un estrato** → ese estrato se declara **no pronosticable** y se
   resuelve con política (safety stock + revisión), no con pronóstico. Es un resultado válido,
   publicable, y probablemente el más honesto para SKU de baja rotación.
4. **Empate técnico (<10% en todo)** → no se cablea nada. El diferimiento se convierte en
   descarte y se cierra el tema.

Ningún resultado de este experimento autoriza por sí solo un merge a `main`.

---

## 7. Los tres modos de falla que invalidan el resultado

Checklist obligatorio antes de creer cualquier número:

1. **Fuga por escala.** La escala de MASE/RMSSE se calcula con `train` de ese origen únicamente.
   Si se calcula sobre la serie completa, todos los métodos mejoran y el ranking se ensucia.
2. **Fuga por orden de operaciones.** Resamplear **antes** de partir train/test. Si se resamplea
   después, los ceros del test se rellenan con información del train.
3. **Cuadrante calculado dos veces.** El estrato se fija **una sola vez** con la serie completa
   de grilla y se congela. Si se recalcula por origen, un SKU cambia de estrato entre orígenes y
   los agregados por estrato dejan de significar algo.

Además: si `pinball_loss` se implementa mal, el error es silencioso y direccional. Testearla
contra un caso cerrado a mano (predicción constante, actual conocido) en el mismo PR.

---

## 8. Presupuesto y topes

- `torch` **no está instalado**. Instalarlo solo si A/B/C ya corrieron y C no ganó.
  Wheel CPU; el hardware es AMD 780M **sin CUDA**, así que inferencia CPU.
- **Tope de runtime: 2 horas** para el barrido completo. Si se pasa, submuestrear a
  **300 SKU estratificados** (proporcional por cuadrante, semilla fija) y **declarar el
  submuestreo** en el entregable. No recortar en silencio.
- Cachear las predicciones de Chronos en disco por (SKU, origen): no re-inferir al iterar métricas.

---

## 9. Fuera de alcance

- No toca `ground_citations` ni `citation_gate`. Un número de Chronos **no puede** anclarse en
  `crostons_method` ni en ningún nodo del grafo L3: no está ahí.
- No se mergea a `main`. No se deploya a Fly.
- Sin covariables, sin promociones, sin feriados en v1 (Chronos-2 los soporta; no entran acá).
- Sin dimensión ubicación (`jobs/forecast_job.py` no la tiene y el experimento no la agrega).
- No se toca `jobs/forecast_job.py`. Ver §2.

---

## 10. Entregable

1. `experiments/chronos_vs_kern/results.csv` — una fila por (SKU, origen, método) con
   `mase, rmsse, pinball_10, pinball_50, pinball_90, coverage_80`.
2. Tabla resumen **por estrato** × método, con conteo de SKU por estrato.
3. Tabla de safety stock: unidades pedidas por método para el mismo nivel de servicio (§4).
4. **Un párrafo de veredicto** que cite la regla de §6 por número y diga qué se cablea, si algo.
5. Declaración de limitaciones: sin ponderar por valor, submuestreo si hubo, dataset único si Olist no entró.

---

## 11. Orden de ejecución

| Fase | Qué | Gate para pasar a la siguiente |
|---|---|---|
| 0 | Harness: resample a grilla semanal + rolling origin + estratificación congelada + las 2 métricas nuevas con su test | Los 3 modos de falla de §7 verificados |
| 1 | Correr A (piso) y B (Kern hoy) | Piso establecido; si Kern-hoy no le gana al naive, decirlo |
| 2 | Correr C (Croston/TSB/ADIDA/IMAPA) | **Si C cumple §6.2, PARAR ACÁ.** No instalar torch |
| 3 | Instalar `torch`, correr D (Chronos-2) | — |
| 4 | Tablas, safety stock, veredicto contra §6 | — |
