# Kern — Autopsia de fallas propias

> **Fuente citable para el carril B del plan de contenido.** 27-jul-2026
>
> **Estado de verificación:** cada línea de este documento fue re-verificada contra `origin/main` en el commit **`1279434`** el 27-jul-2026. No se transcribió ninguna afirmación de una nota previa sin volver a mirar el código.
>
> **Por qué existe.** El diferencial de Kern es *cada número llega diciendo de dónde salió*. Un diferencial así no se puede vender mientras el propio motor cometa la enfermedad contra la que se diferencia. Este documento es el registro de esa enfermedad: qué se encontró, qué se midió, qué se cerró y qué sigue abierto.
>
> **Origen.** Barrido de ~20 repos OSS del oficio (issues abiertos, secciones de limitaciones, avisos de deprecación), 72 fallas con evidencia, 8 patrones cross-repo, y cada patrón contrastado contra el código real de Kern. Corrida `wf_fe25d91e-e90`, 26-jul-2026.

---

## 0. Advertencia de método (leer antes de citar este documento)

La versión anterior de esta autopsia listaba siete bugs vivos. Al re-verificarla contra `main`, **cinco ya estaban cerrados** por #193 (`e1ff368`) y #194 (`1279434`). La nota previa se había escrito mirando una rama que no contenía esos merges.

Esa es, en sí misma, la lección más transferible del ejercicio y la que da el post: **una autopsia sin sello de commit caduca en días.** Toda afirmación de este documento lleva el commit contra el que se verificó. Si vas a citarlo pasada una semana, re-verificá.

---

## 1. Los 8 patrones del oficio

Verificados en 2 o más repos independientes cada uno. Ninguno es exclusivo de Kern: son la patología del software de supply chain como categoría.

| # | Patrón | En una línea |
|---|---|---|
| P1 | **Promesa registrada, hecho no** | el sistema promete un KPI que sus datos no permiten calcular (OTIF es el caso testigo) |
| P2 | **Degradación silenciosa** | ante un input imposible devuelve un número plausible en vez de un error |
| P3 | **Número sin derivación** | la salida no dice de qué fórmula ni de qué supuesto salió |
| P4 | **El SDK es una foto congelada de una API ajena** | versión de API hardcodeada, sin techo de dependencia |
| P5 | **El software del oficio no es negocio de nadie** | mantenido por un académico o un empleado, abandonado al cambiar de trabajo |
| P6 | **El modelo canónico resuelve el mundo tratable** | imperecedero, lead time fijo, backorder permitido — es decir, no el mundo real |
| P7 | **Exigen input idealizado** | panel balanceado, IDs 1..N, sin huecos |
| P8 | **OSS libre para quien se sirve a sí mismo** | las licencias se rompen cuando operás para un tercero |

**El patrón que Kern refuta — y es el que más vale.** Contra P6: `src/risk_period.py` implementa el riesgo con lead time estocástico,

```
sigma_x = sqrt( tau · sigma_d² + sigma_L² · mu_d² )        Vandeput 6.4-6.5
```

leyendo `lead_time_std` del archivo del cliente, mientras la referencia OSS del nicho es 100% lead-time-determinística. El diferencial está construido y verificado en `main`. La falla no es matemática: es de comunicación — durante meses el entregable no lo mostraba.

---

## 2. Cerrado (el arco completo — es el mejor material del carril)

Cinco frentes cerrados en #193 con TDD (test que reproduce primero, después el fix), y sus derivados en #194.

| Falla | Qué pasaba | Medición | Cierre |
|---|---|---|---|
| **NaN silencioso → safety stock mal calculado** | TSB/Croston emiten un NaN de warm-up en sus *fitted values*; AutoETS no, y la asimetría no estaba documentada aguas arriba. `np.mean`/`np.std` sin `dropna` lo propagaban a `error_std`, y como `nan > 0` es `False`, el motor caía al fallback `demand_std` **sin un solo warning** | sigma 2.626 usada contra 2.753 correcta; en el portafolio de ejemplo, 57,17 u de buffer donde iban 62,09 | #193 (`e1ff368`) — `_error_stats_from_fitted` dropea el par y reporta `n_dropped`; `ForecastResult` gana `n_error_obs_dropped` y `sigma_source` |
| **OTIF 0% impreso como hecho** | ausencia de columna → `False`/`0.0`, y el informe imprimía "OTIF 0% contra target 95%". Cero observaciones no es 0%: es no-medido. Además `bool('Cancelled') == True` | — | cerrado: `_TRUE_TOKENS` explícito + mapeo `in_full_status_col` / `in_full_ok_values`, y error legible cuando falta el campo |
| **Cinco `float("inf")` y un `stockout_rate → 0.0`** | la ausencia codificada como el valor **más favorable** | — | cerrado: `stockout_rate` ahora devuelve `float | None` |
| **Metodología declarada que no corrió** | el entregable decía "suavizado exponencial simple" cuando corría AutoETS/TSB, y `SS = z·sigma_e·sqrt(L)` cuando el motor calcula sigma_x de lead time estocástico | — | cerrado: el entregable distingue explícitamente `sigma_e` de `demand_std_fallback` |
| **Ausencias impresas como números en los demos** | los 5 demos imprimían un hueco como si fuera un dato | — | #194 (`1279434`) |

**Por qué esto es contenido y no una nota interna:** tiene arco completo — se encontró corriendo código, se midió con dos cifras, se arregló con test primero, se mergeó y se deployó. Es la demostración de *autonomía ganada* sin afirmar un solo porcentaje.

---

## 3. Abierto (verificado en `main` @ `1279434`)

| # | Falla | Evidencia | Por qué duele |
|---|---|---|---|
| A1 | **`src/audit_evidence.py` — 615 líneas, cero llamadores de producción** | referencias solo dentro del propio archivo y sus tests | Es literalmente el moat de auditabilidad: `InputArtifact` + sha256, `params_used`, `formula_versions`, `output_sha256`, `QaCheck`. Escrito entero y sin cablear |
| A2 | **`stockout_rate` — computable, nunca entregado** | `src/financial_kpis.py:97`, cero llamadores fuera de tests | El nivel de servicio **realizado** es el KPI que primero pide cualquier planificador. El motor puede calcularlo y ningún entregable lo muestra |
| A3 | **`shelf_life_max_quantity` — exportado, sin consumidor** | `src/constraints.py:41`, solo re-exportado en `src/__init__.py` | Consumidores naturales sin cablear: `src/sop_engine/purchase_plan.py`, `jobs/inventory_optimization.py` |
| A4 | **El diferencial invisible** | `src/risk_period.py` calcula sigma_x estocástico | Construido, correcto, y comunicado por debajo de su valor |

**La patología común a A1-A3 no es "código muerto".** Es *código vivo que el producto reclama como cableado*. Es la misma familia que P1: promesa registrada, hecho no. Con A1 y A2 cerrados, la tesis de auditabilidad se cumple de verdad.

---

## 4. Condición de honestidad

La tesis *"cada número llega diciendo de dónde salió y con qué supuesto"* **ya se puede sostener** para el cálculo: el NaN, el OTIF y los `inf` están cerrados. **Todavía no se puede sostener para la evidencia**, porque el módulo que la produce (A1) no está conectado a ninguna corrida real.

Redacción permitida hoy: *"el motor se niega a devolver un número plausible cuando el input es imposible"* — verificable en #193.
Redacción **no** permitida hasta cerrar A1: cualquier cosa que implique que cada entregable llega con su paquete de evidencia re-ejecutable.

---

## 5. Cuatro tesis de diferencial refutadas (no repetir en outreach)

El espejo contra los repos del oficio refutó cuatro argumentos que sonaban buenos:

1. **Vender el diagnóstico de "qué evento empezar a registrar"** — retrasa el valor para un owner-operator que hoy está en Excel.
2. **"El conector se reemplaza, la trazabilidad no"** — lo dice cualquier iPaaS, y Kern no tiene un conector corriendo en un cliente real.
3. **"El trabajo real es acondicionar el dato"** — no se gana el derecho a decirlo antes de cerrar A1.
4. **"Te decimos qué pieza OSS está cobrada, abandonada o prohibida"** — interesante para un ingeniero, irrelevante para quien compra.

---

## 6. Regla de citación para el carril B

```
- Falla → <archivo:línea | commit>
- Verificada contra → origin/main @ <sha>, fecha
- Estado → cerrado (con PR) | abierto (con fecha de cierre comprometida)
- Prohibido → datos de cliente · vector de ataque · % de autonomía · falla abierta sin fecha
```

Ninguna falla se publica sin las cuatro líneas completas.

---

*Kern — el núcleo que tu cadena no tiene.*
