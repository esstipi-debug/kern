# LEVEL escape hatch for `excess_obsolete` — design

**Fecha:** 2026-08-03
**Estado:** aprobado (opcion A), sin implementar
**Depende de:** PR #228 (`feat/negative-quantity-gate`). Esta rama sale de ahi, no de `main`.
**Origen:** hallazgo del review final de #228, listado ahi como follow-up no bloqueante.

---

## 1. El problema

El gate de cantidad negativa trata `on_hand` como un **nivel**, no un flujo: en
`jobs/quantity_audit.py`, `is_material` cortocircuita en `kind == LEVEL` y devuelve
`True` ante **cualquier** negativo. La justificacion es correcta y ya fue adjudicada —
un stock negativo no admite lectura de "devolucion", es un ledger roto.

Lo que no se penso es que eso deja al operador **sin salida**:

- `jobs/excess_obsolete_job.py` llama a `audit_quantities(..., kind=LEVEL)` sin pasar
  `threshold`, y aunque lo pasara, el cortocircuito lo ignora.
- Entonces `params["negative_quantity_threshold"]` **se ignora en silencio** para esta
  tool. El operador setea la perilla, no pasa nada, y nadie le avisa.
- Una sola fila mala mata la corrida entera.

El costo real esta en el embudo publico. `webapp/demo_scan.py:188-192` hace
`qa_issues = excess_obsolete_job.verify(eo) + ...` y luego
`findings = _findings(...) if not qa_issues else ()`. Un prospecto que sube un export de
ERP con un `on_hand` negativo — comun por timing de asignacion o lag de backflush — no
recibe un scan degradado: recibe **cero hallazgos**.

Y hay un segundo defecto latente: hoy las filas negativas **no se excluyen**. Se pasan a
`classify_excess_obsolete`, que clasifica un `-500` como `healthy`. El gate bloquea el
entregable, asi que el bug no se ve — pero la exclusion nunca ocurrio. Si el gate se
relajara sin tocar eso, el bug original volveria intacto.

## 2. Decision

**Excluir y declarar, con techo.** Elegido sobre tres alternativas:

- *Knob explicito de reconocimiento* (`allow_negative_on_hand=True`): default seguro,
  pero el embudo publico sigue roto salvo que el webapp lo setee — y si lo setea, el
  default deja de proteger justo donde hay mas exposicion.
- *Honrar el threshold bajo LEVEL sin excluir*: acepta que un 5% de ledger roto es
  tolerable, que es el argumento semantico al reves, y sigue clasificando las negativas.
- *Solo hacer ruidoso el ignore*: elimina el silencio, que era la queja literal del
  review, pero deja el embudo exactamente igual de roto.

**Invariante que no se negocia:** ninguna fila con `on_hand` negativo vuelve a llegar a
`classify_excess_obsolete`. La relajacion del gate se apoya en la exclusion, no la
reemplaza.

## 3. Diseño

### 3.1. La exclusion, en `prepare_records`

`jobs/excess_obsolete_job.py::prepare_records` audita primero y descarta despues — el
mismo orden que `jobs/forecast_job.py` usa alrededor de su `clip`, y por la misma razon:
una vez descartada, la fila ya no se puede contar.

```
audit = audit_quantities(df, quantity_col=on_hand, product_col=product, kind=LEVEL,
                         threshold=float(params.get("negative_quantity_threshold",
                                                    DEFAULT_NEGATIVE_THRESHOLD)))
# recien aca: construir `stocks` solo con las filas de on_hand >= 0
```

`QuantityAudit` **no necesita campos nuevos**: `n_negative` ya es exactamente la cantidad
de filas excluidas y `negative_total` su magnitud.

### 3.2. La materialidad, en `jobs/quantity_audit.py`

`is_material` deja de cortocircuitar en LEVEL:

```
if self.kind == LEVEL:
    return self.negative_fraction > self.threshold
```

Lee como la alternativa descartada pero no lo es: bajo LEVEL las filas **se excluyeron**.
Un 3% no significa "3% de ledger roto es tolerable"; significa "sacamos el 3% de las
lineas y el 97% restante sigue describiendo el inventario". Sobre el techo bloquea,
porque ahi el archivo ya no representa el deposito.

`issues()` bajo LEVEL cambia su texto: hoy dice "correct the source file before planning
on it" (correcto cuando no se excluia nada); pasa a decir que se excluyo demasiado como
para que el resto sea representativo.

**Umbral: se reusa `DEFAULT_NEGATIVE_THRESHOLD` (0.02)**, no se crea una constante
LEVEL-especifica. #228 acaba de gastar una ola de arreglos colapsando cinco copias de ese
numero en una sola; una segunda constante reabre exactamente eso. Sigue siendo
overrideable por corrida, con lo cual la perilla que el review encontro ignorada pasa a
ser real.

### 3.3. La declaracion

`excess_obsolete` **no escribe `report.md`** — su unico entregable operacional es un CSV
(`jobs/excess_obsolete_job.py:190`, `write_summary_csv`). Asi que la declaracion va por
dos canales que ya existen:

1. **`guided.residuals`**, via el `_apply_quantity_audit` que ya esta cableado en
   `scm_agent/tool_options.py::excess_obsolete_options`. Efecto lateral bueno: ese helper
   **deja de ser inerte** para esta tool. Hoy, bajo LEVEL, `is_material` y `disclosure()`
   disparan con la misma condicion (`n_negative > 0`), asi que `verify()` siempre bloquea
   primero y el helper nunca corre — un minor diferido de #228 que este cambio retira,
   porque ahora si existe un caso sub-umbral genuino.
2. **Un `Finding` en el deck** (`build_deck`), nombrando cuantas lineas se excluyeron y su
   magnitud, junto al KPI `SKUs` que hoy dice "Stock lines assessed" y que a partir de
   ahora cuenta las sobrevivientes.

### 3.4. Consecuencias declaradas

- **Los numeros de plata se mueven.** `total_on_hand_value` y `eo_pct_of_value` se
  calculan sobre las filas sobrevivientes. Es una **correccion**, no una regresion: un
  `on_hand` negativo venia *restando* del denominador del titular "cash at risk".
- **Un archivo 100% negativo sigue refusando.** Si se descartan todas las filas, `stocks`
  queda vacio y `scm_agent/tools.py:2164` ya devuelve `needs_data`. No hace falta camino
  nuevo.
- **El embudo publico** (`webapp/demo_scan.py`) pasa a entregar un scan real con nota
  honesta en vez de cero hallazgos, sin cambios en `demo_scan.py` mismo.
- **`src/bateria/comprobaciones.py:1953`** (T11) llama `job.verify(reporte)` sobre este
  mismo camino; su veredicto se relaja igual. Es coherente y deseado.

## 4. Tests que fallan hoy

| test | hoy | despues |
|---|---|---|
| 1 fila negativa de 200 (0.5%) | `verify()` devuelve issue, entregable bloqueado | `verify() == []`, la fila **no** esta en `report.lines`, residual declarado |
| la fila excluida no se clasifica | `-500` llega a `classify_excess_obsolete` como `healthy` | ningun `line.on_hand < 0` en el reporte |
| 20 negativas de 200 (10%, sobre el techo) | bloquea (por la razon equivocada) | bloquea, con el texto nuevo |
| `negative_quantity_threshold=0.5` con 10% negativo | se ignora, bloquea | no bloquea |
| `total_on_hand_value` con una negativa | incluye el valor negativo | lo excluye |
| todas las filas negativas | bloquea via `verify()` | `needs_data` en `prepare` |
| `webapp/demo_scan.py` con 1 negativa de 200 | `findings == ()` | `findings` no vacio |

Mas un test de no-regresion: los 12 tests de `tests/test_negative_quantity_gate.py` que
ya existen — **dos de ellos cambian de expectativa a proposito** y hay que actualizarlos
con justificacion, no borrarlos:
`test_excess_obsolete_blocks_on_any_negative_on_hand` (1 de 2 filas = 50%, sigue
bloqueando, ahora por share) y
`test_excess_obsolete_level_rule_fires_where_the_flow_rule_would_not` (1 de 200 = 0.5%,
**deja de bloquear** — que es justamente el cambio que este spec introduce; pasa a
verificar que la fila se excluyo y se declaro).

## 5. Riesgos

- **El test discriminador LEVEL/FLOW pierde su premisa.** Ese test existia para probar que
  LEVEL no es FLOW. Tras este cambio la diferencia ya no es "any vs share" sino "excluye
  vs suma", asi que el test tiene que re-escribirse alrededor de la exclusion. Si se
  borra en vez de re-escribirse, se pierde el unico guard de esa distincion.
- **Relajar un gate recien puesto** puede leerse como retroceso. Mitigado por la
  invariante de exclusion: el resultado es mas seguro que antes de #228, no menos, porque
  antes las negativas se clasificaban.
- **`negative_fraction` se mide sobre filas, no sobre valor.** En E&O cada fila es un SKU,
  asi que filas ~ SKUs. Medir sobre valor seria mejor conceptualmente pero el valor de un
  `on_hand` negativo no tiene signo util. Limitacion aceptada, documentada en el docstring.
