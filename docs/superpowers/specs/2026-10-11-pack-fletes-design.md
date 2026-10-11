# Pack `fletes`: comparador de transportistas del propio cliente

**Fecha:** 2026-10-11 · **Rama:** `feat/pack-fletes` · **Estado:** spec para revisión

## 1. Para qué

Un cliente que despacha con varios transportistas no sabe, ruta por ruta, cuál le
conviene: el costo está en las facturas y la puntualidad en el tracking, y nadie los
cruza. El pack lee el historial de envíos del propio cliente y devuelve, por ruta, la
matriz de transportistas con un puntaje costo/servicio desglosado y una recomendación
de reasignación con su ahorro.

Es la opción "B" de la conversación del 2026-10-11: **un Kayak de los transportistas
del cliente, no del mercado.** No cotiza, no reserva, no escribe en ningún sistema.
Encaja en la dirección vigente de Kern (capa de decisión sobre el sistema de registro
del cliente).

**Criterio de éxito:** con un CSV de envíos real, el pack entrega por cada ruta con
alternativa una recomendación cuyo ahorro se puede recalcular a mano desde la matriz,
y nunca muestra un número que el dato no respalde.

## 2. Dónde vive y qué puede importar

- `packs/fletes/`, hermano de `packs/source_to_pay/`, sobre `nosilo_core`.
- `tests/test_pack_boundary.py` lo cubre solo: **cero imports de `src`, `scm_agent`,
  `jobs`, `webapp`, `warehouse`; cero dependencias de terceros.** Solo stdlib +
  `nosilo_core`.
- Consecuencias aceptadas:
  - `src/logistics/freight.py::lane_cost_to_serve` **no se reusa** (prohibido por la
    frontera). Se reescribe la agregación (≈25 líneas) dentro del pack. `freight.py`
    no se toca.
  - **Sin Excel en la v1** (requiere `openpyxl`). El cliente exporta a CSV.
- No se expone en MCP ni webapp en esta etapa (igual que `source_to_pay` hoy). Vive en
  su propio `ToolRegistry` y en sus tests.

## 3. Entrada: contrato del CSV

Un archivo, una fila por envío, UTF-8, separador coma, primera fila = encabezados.

| Columna | Obligatoria | Tipo | Regla |
|---|---|---|---|
| `envio_id` | sí | texto | único en el archivo |
| `transportista` | sí | texto | no vacío; se compara sin distinguir mayúsculas ni espacios extremos |
| `origen` | sí | texto | no vacío |
| `destino` | sí | texto | no vacío |
| `fecha_despacho` | sí | `YYYY-MM-DD` | |
| `costo` | sí | decimal | `>= 0` |
| `moneda` | sí | texto ISO (CLP, AUD…) | una sola moneda en todo el archivo |
| `kg` | sí | decimal | `> 0` |
| `fecha_prometida` | no | `YYYY-MM-DD` | |
| `fecha_entrega` | no | `YYYY-MM-DD` | |

- **Ruta** = `origen → destino` (dirigida: ida y vuelta son rutas distintas).
- **Validación estricta:** cualquier fila inválida rechaza el archivo completo con
  número de fila, columna y motivo. Nunca se descarta una fila en silencio.
- Columnas extra se ignoran.

## 4. Motor (`engine.py`)

Puro, sin reloj, `Decimal` en todo el cálculo monetario. Mismos datos ⇒ mismo
resultado.

### 4.1 Métricas por transportista × ruta

| Métrica | Definición |
|---|---|
| `envios` | filas de ese transportista en esa ruta |
| `kg_total`, `costo_total` | sumas |
| `costo_kg` | `costo_total / kg_total` (cociente de sumas, **no** promedio de cocientes) |
| `envios_fechados` | filas con `fecha_prometida` **y** `fecha_entrega` |
| `otd` | `envios a tiempo / envios_fechados`, donde a tiempo = `fecha_entrega <= fecha_prometida`. **Vacío (None) si `envios_fechados = 0`, nunca 0.** |

Se llama **OTD (on-time delivery), no OTIF**: el "in full" no está en un archivo de
fletes. Nombrarlo OTIF sería fabricarlo.

### 4.2 Elegibilidad

Un transportista×ruta es **elegible** para puntaje y recomendación solo si
`envios_fechados >= min_envios` (parámetro, default **10**). Los no elegibles aparecen
en la matriz con su motivo:

- `pocos_envios`: `envios_fechados < min_envios` y hay al menos una fecha.
- `sin_fechas`: ninguna fila con ambas fechas.

### 4.3 Puntaje (solo elegibles, por ruta)

```
indice_costo    = min(costo_kg de los elegibles de la ruta) / costo_kg propio   # 1 = el más barato
indice_servicio = otd                                                           # 0..1
puntaje         = peso_costo * indice_costo + peso_servicio * indice_servicio
```

- Defaults `peso_costo = 0.6`, `peso_servicio = 0.4`. Ambos `>= 0` y deben sumar 1;
  si no, `prepare` devuelve `needs_clarification` con el motivo.
- Se normaliza contra el mejor de la ruta, **no** con min-max: con dos transportistas
  el min-max fuerza 0 y 1 y exagera diferencias mínimas.
- `costo_kg = 0` (envíos sin costo) ⇒ `indice_costo = 1` para ese transportista y el
  caso queda anotado en el informe.
- **Auditabilidad:** la salida lleva siempre `indice_costo`, `indice_servicio`, los
  dos pesos y el puntaje. El puntaje nunca viaja solo.
- Empate de puntaje ⇒ menor `costo_kg`; si persiste, orden alfabético del nombre.

### 4.4 Recomendación por ruta

- **Actual** = transportista con más `kg_total` en la ruta (todos, elegibles o no).
- **Mejor** = elegible con mayor puntaje.
- Hay recomendación solo si: existen al menos 2 elegibles, el actual es elegible y
  `mejor != actual`. En los demás casos la ruta se informa con su motivo:
  `sin_alternativa` (un solo transportista), `actual_no_elegible`,
  `menos_de_dos_elegibles`, `actual_ya_es_el_mejor`.
- **Ahorro del período** = `(costo_kg actual − costo_kg mejor) × kg_total del actual`.
  Puede ser **negativo** (el mejor puntaje es más caro pero más puntual): se informa
  con signo, no se oculta.
- **Ahorro anualizado** = ahorro del período × `365 / dias_periodo`, con
  `dias_periodo` = días entre la primera y la última `fecha_despacho` de la ruta + 1.
  Si `dias_periodo < 28` no se anualiza (se muestra solo el del período, con motivo).
- **Δ OTD** = `otd mejor − otd actual`, en puntos porcentuales.

## 5. Herramienta (`tool.py`)

Mismo contrato que `source_to_pay`: `Tool` de `nosilo_core.registry` con
`prepare → run → qa → deliver` y una función `register(registry)`.

- `TOOL_KEY = "carrier_lane_compare"`; `intent_keywords` en castellano e inglés
  (flete, transportista, carrier, freight, lane…).
- `prepare`: lee `request.data_path` con `loader.py`. Sin archivo, archivo ilegible,
  fila inválida o más de una moneda ⇒ `needs_data` con el mensaje exacto. Parámetros
  en `request.params`: `peso_costo`, `peso_servicio`, `min_envios`.
- `run`: llama al motor; `summary` = rutas analizadas, rutas con recomendación, ahorro
  anualizado total (solo de las recomendaciones anualizables).
- `qa` (veto: si devuelve problemas, no hay entregable): ninguna ruta con datos
  comparables (todas `sin_alternativa` o sin elegibles) ⇒ se informa como problema de
  dato, no como "todo bien"; invariantes internas (puntajes en [0,1], sumas de kg por
  ruta = suma de filas).
- `deliver`: `fletes_matriz.csv` (una fila por transportista×ruta, con todas las
  columnas de 4.1–4.3 y el motivo de no elegibilidad) y `fletes_informe.md`
  (recomendaciones primero, ordenadas por ahorro anualizado desc; después rutas sin
  recomendación con su motivo).
- `options`: `GuidedOutcome` como en `source_to_pay` — `as_options` con una opción por
  recomendación; `as_handoff` con el motivo si no hay ninguna. **Sin writeback.**

## 6. Archivos

| Archivo | Responsabilidad |
|---|---|
| `packs/fletes/__init__.py` | docstring del pack + re-exports |
| `packs/fletes/loader.py` | CSV → `tuple[Envio, ...]`, validación estricta |
| `packs/fletes/engine.py` | métricas, elegibilidad, puntaje, recomendación (puro) |
| `packs/fletes/tool.py` | `Tool`, `register()`, `build_fletes_tools()`, deliver, options |
| `tests/test_fletes_loader.py` | contrato del CSV, cada rechazo con su fila |
| `tests/test_fletes_engine.py` | métricas, OTD vacío ≠ 0, elegibilidad, puntaje, empates, ahorro negativo, anualización |
| `tests/test_fletes_pack.py` | ciclo completo por el registry, veto de QA, entregables, options |

Fuera del pack no se toca nada. En particular **no** se registra en
`scm_agent/tools.py`, así que no aplican los ocho lugares fijos de
`.specify/memory/constitution.md` (esos son para capacidades SCM del agente). `test_pack_boundary.py` y `test_core_boundary.py`
quedan como están y deben seguir verdes.

## 7. Pruebas y verificación

- TDD: cada regla de las secciones 3–5 tiene su test antes que su código.
- Un caso armado a mano (3 rutas, 4 transportistas, ~60 envíos) con el ahorro
  esperado calculado en la propia prueba, para que el número sea recalculable.
- Suite completa local (`pytest`), `ruff check .` limpio. La cuota de Actions se
  reponía el 1-oct según memoria: verificar el estado real de la CI en el PR, no
  suponerlo.

## 8. Fuera de alcance (v1)

Excel · conectores (Starshipit/Shippit/Sendle para fechas, ERP para facturas) ·
exposición en MCP/webapp · tipo de cambio · tarifas de mercado · cotización o reserva
· mover o borrar `src/logistics/freight.py` · OTIF real.

## 9. Riesgos

| Riesgo | Mitigación |
|---|---|
| Pesos arbitrarios guían mal la decisión | desglose obligatorio en toda salida; pesos visibles en el informe |
| Recomendar con poca muestra | `min_envios` sobre envíos **fechados** |
| Nombres de transportista inconsistentes ("Chilexpress" vs "CHILEXPRESS ") | normalización de mayúsculas/espacios; el resto (alias) queda para v2 y se nota en el informe |
| Una ruta con períodos muy distintos entre transportistas | el ahorro usa el kg del actual en el período observado; anualización por ruta, no global |
