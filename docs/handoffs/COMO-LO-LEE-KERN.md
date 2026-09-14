# Como lo lee Kern

Fecha: 2026-09-06 | Rama: `cursor/como-lo-lee-kern-37e0`

Documento de operador, anclado al codigo. No es un mapa de alias para memorizar:
es lo que hay que poder **mostrar** cuando un cliente abre su propia planilla
al lado del entregable y pregunta "de donde salio este numero".

## La regla

Cada decision que Kern toma al leer un archivo se cuenta y se declara. Un
limpiador silencioso convierte "tu planilla traia doce filas de basura" en
"tu demanda es doce filas mas chica", y eso no se ve en la forma de la
salida. Mismo motivo por el que el OTIF no medido se imprime `not_measured`
en vez de un cero, y por el que `excel_hygiene` existe.

Hasta este cambio el informe de higiene viajaba en `DataFrame.attrs` y el
mapeo moria dentro de `prepare_tracked`. El cliente no veia ninguno de los dos.

## El camino, en orden

1. **Hoja y fila de encabezado** (`jobs/intake.py::detect_sheet_and_header`).
   No asume primera hoja ni fila 0. Gana la combinacion que reconoce mas
   alias. Empate: hoja mas a la izquierda, fila mas arriba. Cero alias:
   `(0, 0)` y el mapeo falla despues con las columnas a la vista, no un
   mapeo inventado.
2. **Higiene de Excel** (`src/excel_hygiene.py`), solo si hay una columna de
   producto reconocible. En este orden, a proposito:
   - completar celdas combinadas **desde el libro** (`merged_cells`), nunca
     un `ffill` a ciegas;
   - tirar filas en blanco y la fila `TOTAL` (por etiqueta o porque la
     ultima fila cuadra con la suma);
   - tirar columnas `Unnamed` vacias.
   Cada descarte se cuenta. Sin columna de producto se devuelve el crudo.
3. **Mapeo de encabezados** (`detect_columns`). Alias en en/es/pt, acentos
   plegados. Un `overrides` gana; despues el pack por vertical; despues la
   tabla generica. `sku` no es un canonico: es alias de `product_id`.
4. **Normalizacion** (`normalize_tracked`). Fechas con `format="mixed"`;
   cantidades con separador LATAM (`1.234,56`) y anglosajon (`1,234.56`).
   Tira fecha ilegible, cantidad ilegible, cantidad negativa -- y lo cuenta
   en `IntakeQuality`. Agrega a periodo. Rellena `unit_cost` / `lead_time_days`
   / `lead_time_std` donde faltan y anota cuantas celdas invento.

CSV no pasa por (1)-(2): un titulo arriba ya rompe con un error de columnas
desparejas. Excel es el formato que esconde el titulo y la fila TOTAL.

## Donde lo ve el cliente

| Superficie | Que muestra |
|---|---|
| Deck del orquestador (`jobs/inventory_deliverable.py`) | filas en el mapa de fuentes: ubicacion, mapeo, higiene, descartes, defaults |
| `inventory_plan.xlsx` / `report.md` (`jobs/deliverables.py`) | seccion / hoja **How Kern read this** |
| Embudo `/demo` (`brecha_en_plata.xlsx`, hoja Calidad de los datos) | bloque **Como lo leyo Kern** en castellano |
| CLI `examples/run_inventory_job.py` | ahora usa `prepare_tracked` y imprime la lectura antes de correr |

No va entre los hallazgos. Un finding es un problema de la operacion del
cliente; esto es un limite de la medicion.

## Lo que NO hace

- No adivina un encabezado ambiguo (`entrega` pelado no es ni plazo ni
  fecha: ver el comentario en `ALIASES`).
- No completa un hueco real de SKU: solo donde el `.xlsx` declara una
  combinacion.
- No traduce el deck de inventario al castellano. El i18n de `src/i18n.py`
  produce un entregable entero en un idioma; mezclar los dos dentro del
  mismo archivo no es eso. El embudo `/demo` si es castellano, y ahi las
  etiquetas estan en castellano.
- No cubre pricing ni el resto de jobs. El flagship (inventario) y el
  primer contacto (demo de brecha) eran los dos sitios donde la planilla
  real entra sin que nadie la toque.

## Como verificarlo

```
PYTHONPATH=. pytest tests/test_intake_reading.py tests/test_excel_hygiene.py tests/test_demo_workbook_stock_gap.py tests/test_deliverable_service_note.py -q
```

Un Excel de prueba con portada, SKU combinado y fila TOTAL tiene que
nombrar la hoja, la fila 2 de encabezado, `product_id <- 'sku'`, y las
filas de TOTAL / combinadas en el markdown y en la hoja del xlsx.
