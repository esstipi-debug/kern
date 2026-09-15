# fulfillment_definitions — pack de definiciones firmadas (spec B)

Fecha: 2026-09-10
Estado: spec para aprobación (cero código de B escrito; A ya está en
`feat/fulfillment-milestones`)
Rama sugerida: `feat/fulfillment-definitions` (sobre A, o el mismo PR si
A aún no merged)

Depende de A: `MILESTONE_IDS`, `MilestoneEvent`, `parse_occurred_at`,
`fulfillment_chain_job.prepare`. Sin esos ids, este pack es prosa.

No es C: este spec **no atribuye culpa** (warehouse vs forwarder vs
transporte). Guarda y evalúa predicados booleanos sobre hitos. C consume
el pack firmado y emite cause; B se detiene en `true` / `false` /
`unmeasurable`.

---

## 1. Qué problema resuelve

A mide dwell. El FDE de Samir Saci no se trabó en el dwell: se trabó
cinco workshops en **una frase** (`LOAD_ONTIME = load_end ≤ 19:00`, y
después de quién es el miss). Esa frase no es un timestamp. Es una
regla con dueños.

Kern ya tiene el precedente: `objection_thresholds` en
`ClientProfile`. Un área sin umbral sale `no_evaluable`, nunca
“nadie objeta”. El pack es el mismo gesto para el muelle: sin firma,
no hay “el almacén llegó tarde”. Hay un HANDOFF para sentar a las
partes.

**Tesis en una línea:** una definición operacional es un predicado
cerrado sobre hitos de A, con `means`, y un registro de quién acordó.
No es un `eval()` ni un prompt.

---

## 2. Estado verificado del repo (post-A)

| Hecho | Fuente |
|---|---|
| Seis `MILESTONE_IDS` cerrados | `src/connectors/milestones.py` |
| Dwell no salta hops; inversión falla QA | `src/fulfillment_milestones.py` |
| `uninstrumented_late` es agujero de reloj, no causa | mismo módulo |
| Tool `fulfillment_chain` registrado | `scm_agent/tools.py` |
| Perfil de cliente: `clients/<slug>/profile.json`, atómico | `src/client_profile.py` |
| Labels genéricos (`Client`) **nunca** resuelven perfil | `is_generic_client_label` |
| Umbrales de objeción parciales ⇒ `NO_EVALUABLE` | `src/objection.py` |
| `as_params()` solo exporta floats/dicts planos; un dataclass
  envenena JSON | `ClientProfile.as_params` / `warehouse_capacity` |
| Orchestrator mergea perfil en `params`; webapp usa
  `clients_root=None` por defecto | `scm_agent/orchestrator.py`, CLAUDE.md |
| Un `Tool` toma **un solo** `data_path` | registry |

---

## 3. Frontera

| | ya existe | este spec | C (después) |
|---|---|---|---|
| A / `fulfillment_chain` | dwell por hop | — | — |
| `objection_thresholds` | tolerancia por área, dict en el perfil | no reusa ese campo | — |
| B | — | predicados + firmas + flags por orden | — |
| C | — | — | `CAUSE` a partir de flags firmados |

`fulfillment_chain` **no cambia de significado**. B es otro tool. Un
cliente puede correr A para ver agujeros y B para ver flags; mezclarlos
en un solo informe afirmaría causa con dwell.

El pack **no** entra en `ClientProfile` ni en `as_params()`. Iría a
todos los tools y no es un float. Archivo hermano:
`clients/<slug>/fulfillment_definitions.json`.

---

## 4. Decisiones cerradas

| # | Decisión | Elegida | Descartadas |
|---|---|---|---|
| D1 | Dónde vive | `clients/<slug>/fulfillment_definitions.json`, mismo root que el perfil | Meterlo en `profile.json` (rompe `as_params`, mezcla costos con reglas de muelle). Params de cada request (no reproducible). YAML libre en el brief. |
| D2 | Qué es una regla | Predicado de vocabulario **cerrado** (`before_clock`, `before_milestone`) | `eval` / DSL abierta (el FDE escribió Python a mano; Kern no). Booleanos sueltos en el CSV (vuelven a A). |
| D3 | Causa / culpa | **Fuera.** El pack puede *nombrar* predicados; no emite `CAUSE=` | Incluir el árbol if/CTRL/FWD del artículo (eso es C; sin C, un árbol se leería como veredicto). |
| D4 | Firma | Registro operator-typed: `{party, name, signed_at}`. No es cripto | OAuth de cada jefe de turno (no hay identidad). Firmar con API key. Pack “firmado” porque el JSON existe. |
| D5 | Partes | Set cerrado: `planning`, `warehouse`, `transport`, `forwarder` | Partes libres (un typo “wharehouse” deja el pack “firmado”). |
| D6 | Evaluación sin hora | `before_clock` sobre un hito **date-only** ⇒ `unmeasurable`, no false | Asumir medianoche (inventa el miss de 19:00). Tratar el día calendario como “a tiempo”. |
| D7 | Pack sin firmar + eventos | `HANDOFF` (quién falta). **No** evalúa flags | Preview “draft” (viaja igual que un hecho). `EXECUTED` con caveat (el caveat se pierde en el Excel). |
| D8 | Pack firmado, sin eventos | `needs_data` pidiendo el CSV de hitos | `EXECUTED` “pack on file” (no hay nada que el planner use el lunes). |
| D9 | Cliente genérico | Nunca lee ni escribe pack (misma frontera que el perfil) | Un pack bajo `clients/client/` compartido por todos. |
| D10 | Superficie | Tool nuevo `fulfillment_definitions` | Extender `fulfillment_chain` (cambia OTIF-adjunto de dwell). Chat que “entiende” la regla. |

---

## 5. Contrato de tipos

Archivo nuevo `src/fulfillment_definitions.py`. Puro: sin pandas, sin
I/O. I/O en `src/fulfillment_definitions_store.py` (espejo de
`load_profile` / `save_profile`: atómico, slug canónico, refuse
generic).

```python
SCHEMA_VERSION = 1
PARTIES = ("planning", "warehouse", "transport", "forwarder")
PREDICATE_KINDS = ("before_clock", "before_milestone")

@dataclass(frozen=True)
class Predicate:
    id: str                 # slug [a-z][a-z0-9_]{1,40}, único en el pack
    kind: str               # PREDICATE_KINDS
    left: str               # MILESTONE_IDS
    right: str              # "HH:MM" (24h) o MILESTONE_IDS
    means: str              # oración, required (mismo rigor que FieldCoverage.means)

@dataclass(frozen=True)
class Signature:
    party: str              # PARTIES
    name: str               # quién dijo que sí (texto del operador)
    signed_at: str          # ISO date o datetime

@dataclass(frozen=True)
class DefinitionPack:
    client_id: str          # slug canónico
    schema_version: int
    predicates: tuple[Predicate, ...]
    required_parties: tuple[str, ...]   # no vacío
    signatures: tuple[Signature, ...]
    means: str              # qué acuerda el pack entero, una oración
    updated_at: str | None
```

Validación en `__post_init__` / `validate_pack`:

- `schema_version == 1` (mayor ⇒ refuse, como el perfil).
- `predicates` no vacío; `id` únicos; `means` no blank.
- `kind=before_clock`: `right` match `^([01]\d|2[0-3]):[0-5]\d$`; `left` ∈ hitos.
- `kind=before_milestone`: ambos ids en `MILESTONE_IDS`, distintos.
- `required_parties` ⊂ `PARTIES`, len ≥ 1, sin dupes.
- Cada `Signature.party` ∈ `required_parties` (firmar un extra es error).
- `name` no blank. `signed_at` parseable con `parse_occurred_at`.

```python
def is_signed(pack: DefinitionPack) -> bool:
    """True iff every required party has at least one signature."""
```

Dos firmas de la misma party: vale (la más reciente no se elige para
nada; B no es un ledger). Falta una party ⇒ no firmado.

Plantilla que emite el HANDOFF cuando no hay archivo:

```json
{
  "client_id": "<slug>",
  "schema_version": 1,
  "means": "when a load counts as on time, in one sentence",
  "required_parties": ["warehouse", "transport"],
  "predicates": [
    {
      "id": "load_ontime",
      "kind": "before_clock",
      "left": "load_end",
      "right": "19:00",
      "means": "loading finished at or before the 19:00 dock cutoff"
    }
  ],
  "signatures": []
}
```

Ese es el `LOAD_ONTIME` del artículo, sin el árbol de culpa.

---

## 6. Evaluación de un predicado (aún no es causa)

```python
@dataclass(frozen=True)
class Flag:
    order_id: str
    predicate_id: str
    value: bool | None     # None = unmeasurable
    reason: str
```

`evaluate(events, pack) -> tuple[Flag, ...]`

Por orden (collapse igual que A: último evento por hito) y por
predicado:

| kind | left ausente | left date-only y before_clock | ok |
|---|---|---|---|
| `before_clock` | `None` | `None` (D6) | `time(left) <= HH:MM` **el mismo día civil del left** |
| `before_milestone` | `None` si falta left o right | n/a | `left.instant <= right.instant` |

`before_clock` **no** usa dwell ni el día de `arrived`. El cutoff es
del día del hito izquierdo (el camión de hoy, no el SLA de la tienda).

Un pack firmado cuyas predicates piden `load_end` y el CSV no lo trae:
flags `None`, no `false`. El informe lo dice. QA no falla: es
cobertura, como A.

`evaluate` sobre pack **no** firmado: no se llama. El job ni entra.

---

## 7. Persistencia

`src/fulfillment_definitions_store.py`:

- `pack_path(slug, root) -> root / slug / "fulfillment_definitions.json"`
- `load_pack(client_id, *, root)` → `DefinitionPack | None`. Generic
  label ⇒ `None` (aunque el archivo exista).
- `save_pack(pack, *, root)` → atomic temp+replace, mismo retry
  Windows que el perfil. `client_id` debe ser slug. Refuse generic.
- Corrupt JSON ⇒ `ValueError` ruidoso, no pack vacío.

El orchestrator **no** mergea el pack a `params`. El job lo carga por
slug + `clients_root`.

`clients_root` llega así (un renglón, params ya puesto gana):

```python
# scm_agent/orchestrator.py, tras merge_params, si self.clients_root:
if "clients_root" not in params:
    params["clients_root"] = str(self.clients_root)
```

Webapp con `clients_root=None`: el tool no encuentra pack ⇒ HANDOFF
plantilla. Correcto: el `client` HTTP no es identidad.

Tests pasan `params={"clients_root": str(tmp_path)}`.

---

## 8. Tool `fulfillment_definitions`

- `requires_data=True`: el CSV de hitos (reusa
  `fulfillment_chain_job.prepare`). El pack no es el `data_path`.
- Keywords (multi-palabra, no robar `fulfillment_chain`): `pack de
  definiciones`, `definition pack`, `load_ontime`, `corte de muelle`,
  `quienes firmaron el hito`, `dock cutoff definition`. Evitar
  `dwell`, `otif`, `cadena de hitos` sueltos.
- `run`:
  1. Generic client ⇒ `HANDOFF` “nombra un cliente real; el pack no
     vive bajo Client”.
  2. Sin archivo ⇒ `HANDOFF` con la plantilla (§5) como `artifact`,
     steps = workshop (almacén + transporte, una oración `means`,
     firmas). `risk_if_skipped` = “cualquier RCA de atraso será
     opinión”.
  3. Archivo presente, `not is_signed` ⇒ `HANDOFF` listando parties
     que faltan. No evalúa.
  4. Firmado + eventos ⇒ `evaluate`, `EXECUTED`, tabla de flags.
     Residual fijo: “estos flags no son una atribución de causa”.
- `qa`: `verify_guided`; `EXECUTED` con pack no firmado falla;
  `EXECUTED` sin ningún flag (cero órdenes) falla; inversión de
  timestamps **no** la re-chequea este tool (ya la chequea A; B no
  sustituye ese gate). Si el caller no corrió A, B igual evalúa:
  un hop invertido puede hacer `before_milestone` True en el sentido
  contrario al proceso — residual, no QA fail (C se pondrá estricto).
- `deliver`: Excel de flags (`order_id`, `predicate_id`, `value` vacío
  si None, `reason`) + deck. `data_sources` incluye `means` de cada
  predicado y las firmas (party, name, signed_at).

Citas L3 tentativas (verificar al implementar):
`cross_functional_integration` está cerca de un hub — no usarla sola.
Preferir nodos de process mapping / dock-to-stock si existen; si el
grafo no tiene nada de “operational definition”, islar ancla como
`org_friction_audit` o dejar `_NO_CONCEPT_MAP_BY_DESIGN` con la
misma justificación medida. No fabricar un ancla.

---

## 9. Tests

`tests/test_fulfillment_definitions.py` (puro):

1. Pack sin `means` no instancia.
2. `before_clock` / `right="19:60"` no instancia.
3. `is_signed` False si falta `transport`.
4. `is_signed` True con warehouse+transport.
5. Generic slug: `save_pack` raises.
6. `load_end` 18:59 vs 19:00 ⇒ `True`; 19:01 ⇒ `False`.
7. `load_end` date-only vs 19:00 ⇒ `None`, reason nombra D6.
8. Falta `load_end` ⇒ `None`, no False.
9. `before_milestone(pack_end, load_end)` con ambos ⇒ compara instants.
10. `evaluate` no se exporta como API que acepte pack unsigned (función
    sí puede ser pura; el job es quien se niega). El test del job
    cubre que unsigned no llama evaluate / no escribe flags.

`tests/test_fulfillment_definitions_job.py`:

1. Sin archivo en `clients_root` ⇒ verify ok, outcome HANDOFF, artifact
   contiene `"load_ontime"`.
2. Pack unsigned + CSV ⇒ HANDOFF, CSV de flags **no** se escribe (o
   se escribe vacío — elegir **no escribir flags**; un Excel vacío se
   lee como “nadie falló”).
3. Pack signed + CSV del artículo (load 18:00 y 20:00) ⇒ dos filas,
   true y false.
4. `JobRequest.client="Client"` ⇒ no lee un pack plantado en
   `clients/client/`.
5. Orchestrator inject: con `clients_root=tmp` el tool encuentra el
   pack sin `params` manuales.
6. Brief “pack de definiciones / corte de muelle” rutea aquí, no a
   `fulfillment_chain`.

Guard de conteo de tools: 54 → 55 (o 58→59 si se rebasea a un main
más nuevo). Mismos espejos que A (`how_it_works_data`, README,
`TOOL_TITLES`, `UNDECLARED_TOOLS`, smoke fixture).

---

## 10. Fuera de alcance

| Qué | Dónde |
|---|---|
| Árbol `CTRL==SHP/FWD` ⇒ CAUSE | spec C |
| UAT “lunes del planner” | golden files contra flags de B, después |
| Formula `order_created = HEUR if CRIND=A` | alias de A / intake, no un predicado |
| WMS live | MilestoneSource, otro PR |
| Firmar desde la UI web | el JSON lo escribe el operador; B no es un workflow de docusign |

---

## 11. Definition of done

- Tipos + validate + evaluate + store atómico.
- Tool `fulfillment_definitions` + tests §9.
- Un renglón de orchestrator para `clients_root` en params, con test
  de que un params explícito gana.
- `fulfillment_chain` sin diff funcional.
- Residual en todo `EXECUTED`: flags ≠ causa.
- README / i18n / how-it-works / citation gate al día.

---

## 12. Lo que este spec no afirma

No afirma que cinco workshops quepan en un JSON. Afirma que, cuando
esas cinco reuniones terminan, Kern tiene **dónde guardar la frase**
y **se niega a calcular flags** hasta que las partes nombradas están
en el archivo. Eso es lo que A no podía hacer, y lo que C no debe
hacer sin esto.
