# Negative-quantity gate — design

**Fecha:** 2026-08-03
**Estado:** aprobado (decisiones A y B tomadas), sin implementar
**Cierra:** el hallazgo abierto del item 6 de `documentation/ROADMAP.md`

---

## 1. El diagnostico, corregido

El reporte original decia que Kern acepta demanda negativa "sin objetar". Corriendo
el repro y leyendo el objeto `JobResult` (no el stdout), la realidad es otra:

```
guided.status  = escalated
residual       = "1 of 2 source row(s) were dropped during intake (1 negative quantity)."
escalation     = EscalationPacket(route_to='operations owner', sla='same business day')
```

Kern **si** detecta la fila negativa. `jobs/intake.py:271` la descarta y la cuenta
en `IntakeQuality.n_dropped_negative_quantity`; `scm_agent/tool_options.py:88`
construye el residual y la escalacion. El hueco no es de deteccion sino de dos
cosas distintas:

### 1.1. Causa raiz de orden en el pipeline

En `scm_agent/orchestrator.py`:

| linea | paso |
|---|---|
| 156 | `issues = tool.qa(produced.report)` — **el unico gate** ("QA fails => no deliverable") |
| 169 | `guided = tool.options(produced.report)` — **quien fabrica la escalacion** |
| 179 | `JobResult(status=STATUS_OK, confidence=intent.confidence, ...)` |

Una escalacion nacida en `options()` corre **despues** del gate, asi que
estructuralmente **nunca puede bloquear un entregable**. Ademas al deck solo se le
pasa `guided.options` (linea 170): los `residuals` y el `escalation` no llegan ni a
`report.md` ni a `deliverable.md`. `confidence` es `intent.confidence` — confianza de
**ruteo**, no de datos — por eso sale 1.00 con datos rotos.

### 1.2. El umbral del 20% tapa el caso realista

`intake_quality_threshold = 0.20` escala solo si la fraccion descartada lo supera.
Verificado:

| archivo | negativas | fraccion | `guided.status` |
|---|---|---|---|
| repro del reporte | 1 de 2 | 50% | `escalated` |
| caso realista | 3 de 200 | 1,5% | `options` — **ni escalacion** |

Un export real de ERP trae unas pocas devoluciones. Ese es el caso que hoy pasa
entero en silencio.

## 2. Es transversal — y cada tool hace algo distinto

Cada fila corrida contra un CSV con negativos, no inferida del codigo:

| tool | comportamiento | objeta |
|---|---|---|
| `inventory_optimization` | descarta y cuenta (`intake.py`) | parcial, nunca en superficie |
| `abc_xyz` | **suma** la negativa: un SKU 100/100/100/−300 queda `annual_value=0`, clase **C/Z**, `cv=inf` | no — `qa` limpio |
| `forecast` | `clip(lower=0)` en `forecast_job.py:110` — **fabrica un 0** donde habia −400 | no — ni lo cuenta |
| `excess_obsolete` | `on_hand=−500` → clasificado **"healthy"**, 0 de exceso | no — `qa` limpio |
| `ddmrp` | ADU negativo → `invalid buffer zones for P1` | **si** |

El peor de los cinco es `abc_xyz`: convierte un A-item real en un candidato a
discontinuacion (C/Z), que es exactamente la clase de error que un cliente
descubre despues de haber dejado de comprar el SKU.

**`data_quality` no es el lugar.** Audita un *maestro de productos*
(sku / nombre / GTIN / costo): dedup, digito verificador GS1, costos no positivos.
Nunca mira un archivo transaccional ni una columna de cantidad. Mover ahi la
validacion de demanda le cambiaria el contrato de entrada.

## 3. Decisiones tomadas

**A — semantica.** Descartar (no adivinar la causa) + `qa_issue` sobre umbral.
La señal **se mueve** de `guided.residuals` a `qa_issues`, que es el unico canal
que el gate ya respeta. Material => falla QA => sin entregable. Inmaterial =>
se descarta pero queda escrito en el entregable.

Descartadas y por que:
- *`qa_issue` siempre*: rechaza exports reales con devoluciones normales; obliga a
  limpiar a mano antes de cada corrida.
- *`needs_data`*: no distingue 1 negativa de 500.
- *Netear como devoluciones*: es lo mas correcto en dominio — hoy el descarte
  **infla** la demanda, porque cuenta la venta de 100 y no la devolucion de 40 —
  pero asume una de las tres causas, y una columna mal mapeada pasaria en silencio.
  Queda anotado como deuda consciente, no como no-problema.

**B — alcance.** Semantica en las 5 tools + fix de surfacing.

## 4. Diseño

### 4.1. Modulo nuevo: `jobs/quantity_audit.py`

Cuenta, nunca muta. Vive en `jobs/` porque es pandas y compartido, junto a
`intake.py` (el otro modulo compartido de esa capa).

```python
@dataclass(frozen=True)
class QuantityAudit:
    column: str                 # "quantity" | "on_hand" | "adu"
    kind: str                   # "flow" | "level"  (ver 4.2)
    n_rows: int
    n_negative: int
    negative_total: float       # suma de los valores negativos (magnitud)
    n_entities_net_negative: int
    threshold: float = 0.02

    @property
    def negative_fraction(self) -> float: ...
    @property
    def is_material(self) -> bool: ...
    def issues(self) -> list[str]: ...      # [] cuando no es material
    def disclosure(self) -> str: ...        # texto para el entregable, aun si no es material


def audit_quantities(df, *, quantity_col, product_col=None, kind="flow", threshold=0.02) -> QuantityAudit
```

### 4.2. Regla de materialidad — distinta para flujo y para nivel

- **`kind="flow"`** (demanda, ventas: `inventory`, `abc_xyz`, `forecast`): material
  cuando `negative_fraction > threshold` (default 0,02) **o** cuando algun SKU
  queda con demanda agregada negativa. Una devolucion aislada es normal; una
  columna con signo invertido no.
- **`kind="level"`** (`excess_obsolete.on_hand`): **cualquier** negativa es
  material. Un nivel de stock negativo no admite lectura de "devolucion": es un
  ledger roto, y hoy se reporta como `healthy`.

`threshold` es override por corrida via `params["negative_quantity_threshold"]`,
igual que `intake_quality_threshold` ya lo es.

### 4.3. Como llega al gate

`Tool.qa` recibe **solo el report** (`nosilo_core/registry.py:44`). Entonces el
audit tiene que viajar `prepare -> payload -> run -> report -> qa`:

| tool | donde se cuenta | donde se cuelga | gate |
|---|---|---|---|
| `inventory_optimization` | ya existe: `IntakeQuality.n_dropped_negative_quantity` | ya existe: `JobReport.intake_quality` | check nuevo en `jobs/qa.py:verify()` |
| `abc_xyz` | `aggregate_skus()` | payload pasa a `{"items", "audit"}`; `AbcXyzReport.quantity_audit` | `abc_xyz_job.verify()` |
| `forecast` | `prepare_records()`, **antes** del `clip(lower=0)` | payload pasa a `{"series", "audit"}`; `ForecastReport.quantity_audit` | `forecast_job.verify()` |
| `excess_obsolete` | `prepare_records()` | el payload ya es dict: `payload["audit"]`; `EOReport.quantity_audit` | `excess_obsolete_job.verify()` |
| `ddmrp` | ya lo caza | — | solo mejorar el mensaje: nombrar el ADU negativo en vez de `invalid buffer zones` |

**Limitacion aceptada y documentada:** en `inventory_optimization` el descarte
ocurre *antes* del `groupby`, asi que un SKU no puede quedar net-negative. Ahi el
gate es solo por fraccion. Se anota en el docstring en vez de fingir cobertura.

El `clip(lower=0)` de `forecast_job.py:110` **se mantiene** (pisar en 0 es correcto
una vez declarado); lo que cambia es que primero cuenta.

Cero cambios en `orchestrator.py` para la parte semantica: un `qa_issue` ya
produce `STATUS_QA_FAILED` sin entregables. El contrato existente hace el trabajo.

### 4.4. Surfacing

1. **`report.md` operacional** de las 5 tools: seccion "Data quality" con
   `audit.disclosure()` **siempre que haya al menos una negativa**, sea material o
   no. Es el caso inmaterial el que hoy desaparece entero.
2. **`examples/run_agent.py`**: imprimir `result.guided.residuals` y, cuando exista,
   el `EscalationPacket` (razon, `route_to`, `sla`). Hoy el CLI imprime `status`,
   `summary`, entregables, `qa_issues` y `clarifications` — y nada de `guided`.
3. **Respuesta de `POST /api/jobs`**: incluir residuals y escalacion.

**Fuera de alcance, declarado, no silenciado:**

- *El deck premium (`deliverable.md` / `.xlsx`)*. La firma de `Tool.deck` es
  `(report, out_dir, client, citations, confidence, options)` y el orquestador le
  pasa solo `guided.options`. Meter los residuals exige cambiar esa firma en las
  49 tools registradas — desproporcionado para este PR y con superficie de rotura
  grande. Queda como issue aparte. Nota concreta: hoy `deliverable.md` imprime
  *"No residual actions: the analysis above is ready to use."* (`src/i18n.py:142`)
  mientras `guided` lleva dos residuals — o sea que el deck no solo omite, afirma
  lo contrario.
- *`JobResult.status` / `confidence` coherentes con `guided.status`*. Que
  `status=ok` conviva con `guided.status=escalated` sigue siendo posible por otras
  vias (ej. >20% de fechas malas). Cambiar `status` ahi toca el contrato que
  consumen webapp, MCP y ~1100 tests. Con la decision A el caso *material* ya cae
  en `qa_failed`, que es el que importaba. El resto es un PR propio.

### 4.5. Tests de regresion (fallan hoy)

`tests/test_negative_quantity_gate.py`:

| test | hoy | despues |
|---|---|---|
| inventory, 3 negativas de 200 (1,5%) | `ok`, 5 entregables | `qa_failed`, 0 entregables |
| inventory, 1 negativa de 500 (0,2%, inmaterial) | sin rastro en `report.md` | `ok` + disclosure en `report.md` |
| `abc_xyz`, SKU 100/100/100/−300 | `verify()` == `[]`, clase C/Z | `qa_issue` |
| `forecast`, serie con −400 | `[100,100,100,0,...]`, sin conteo | `audit.n_negative == 1` + `qa_issue` |
| `excess_obsolete`, `on_hand=−500` | `"healthy"`, `verify()` == `[]` | `qa_issue` (regla `kind="level"`) |
| `ddmrp`, ADU negativo | `"invalid buffer zones for P1"` | mensaje que nombra el ADU negativo |
| `run_agent.py` con archivo escalado | no imprime `guided` | imprime residuals + escalacion |

Mas un test de no-regresion: los samples limpios commiteados siguen dando
`status=ok`, `qa_issues=[]` y **sin** seccion "Data quality".

## 5. Riesgos

- **Falsos positivos en clientes con muchas devoluciones.** Mitigado por el
  threshold configurable por corrida y por perfil de cliente.
- **Los datasets de test existentes pueden traer negativas** y volver rojos tests
  que hoy pasan. Es señal, no ruido: hay que mirar cada caso antes de bajar el
  umbral.
- **Doble conteo en `inventory_optimization`**: la misma fila podria aparecer en el
  residual de intake y en el `qa_issue` nuevo. El check nuevo lee `IntakeQuality`,
  no vuelve a contar, y cuando es material el `qa_issue` gana (no hay entregable,
  asi que el residual no llega a renderizarse).
