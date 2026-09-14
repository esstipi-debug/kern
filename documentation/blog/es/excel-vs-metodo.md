---
pair: excel-vs-method
lang: es
slug: excel-vs-metodo
title: "Inventario en Excel: qué aguanta la planilla y qué se le escapa a la política"
description: "Excel sirve para exportar y revisar, no para guardar la política de inventario. Dónde se rompe, y cómo el método corre sobre tu propio archivo."
date: 2026-08-25
updated: 2026-08-25
query: "inventario en Excel"
answer: >
  Excel es una buena superficie de exportación y revisión, y una mala casa para
  la política de inventario. La planilla no guarda cuánto se equivocó el
  pronóstico del ciclo anterior, no distingue la dispersión de la demanda del
  error del pronóstico, no simula antes de recomendar y depende de una persona.
  El método puede correr sobre tu archivo sin que lo abandones.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/connectors/excel.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: webapp/demo_scan.py
  - kind: code
    ref: jobs/intake.py
  - kind: code
    ref: webapp/app.py
  - kind: code
    ref: documentation/templates/stock_template.csv
  - kind: book
    ref: "Khan et al. (2022), Supply Chain Resilience, Cap. 6 — seguimiento de cuellos de botella en planilla estática como práctica de línea base"
faq:
  - q: "¿Tengo que migrar de Excel para empezar?"
    a: "No. El escaneo lee el archivo que ya exportás y el entregable vuelve como libro de Excel. El conector de writeback escribe en la planilla del cliente; no la reemplaza."
  - q: "¿Kern puede editar mi planilla?"
    a: "Sí, por el conector de src/connectors/excel.py, con una persona que aprueba el antes y el después. Verifica que ninguna celda haya cambiado en disco desde que se preparó el cambio, deja respaldo del original y reemplaza el archivo de forma atómica. La vuelta atrás es celda por celda."
  - q: "¿Alcanza con un CSV para el escaneo gratis?"
    a: "Sí. El demo acepta CSV o Excel y elige el lector según la extensión. Las columnas exigidas son product_id, on_hand y daily_demand; unit_cost y days_since_last_sale son opcionales. Es un camino de solo lectura."
  - q: "¿Mis macros se van a ejecutar?"
    a: "No. Un .xlsm se abre preservando el proyecto VBA, así que las macros siguen ahí después de escribir, pero nunca se corren: ejecutarlas necesita un Excel vivo por COM, fuera del alcance de un conector de archivo."
  - q: "¿Qué se rompe cuando aparece un segundo almacén?"
    a: "La planilla deja de tener una sola respuesta correcta: la misma unidad falta en una bodega y sobra en la otra, y ninguna columna dice cuál mover. Eso es planificación multi-eslabón, trabajo de un paquete posterior: el escaneo público no corre DRP ni redistribución."
noindex: false
---

# Inventario en Excel: qué aguanta la planilla y qué se le escapa a la política

Excel es una buena superficie de exportación y revisión, y una mala casa para la política de inventario. La planilla no guarda cuánto se equivocó el pronóstico del ciclo anterior, no distingue la dispersión de la demanda del error del pronóstico, no simula antes de recomendar y depende de una persona. El método puede correr sobre tu archivo sin que lo abandones.

## Qué aguanta la planilla

Como superficie de revisión no tiene rival: todo el mundo la abre, se filtra sin permiso de nadie y una fila se explica sola. Kern tampoco pelea contra eso: el entregable del escaneo público vuelve como libro de Excel.

La pregunta no es si sirve para *mirar*, sino si sirve para **guardar la política**: el objetivo de cobertura, cuánto se equivocó el pronóstico el ciclo pasado, y quién movió el mínimo de un SKU en marzo.

## Dónde se rompe como sistema de registro

**La celda que alguien puede borrar.** La fórmula vive en una celda, y la celda se pisa, se arrastra mal o cae en un rango que alguien ordenó sin extender la selección. Nada de eso deja rastro: el archivo guarda el resultado, no la decisión ni el autor. Seis meses después nadie contesta por qué ese mínimo es el que es.

Khan y coautores documentan una empresa que seguía sus cuellos de botella en una herramienta estática de Excel, como operación diaria de tiempo completo: el resultado era difícil de interpretar cuando la capacidad del proveedor y los plazos cambiaban a diario o por hora (Cap. 6). No era un problema de aritmética.

**σ de las ventas no es σ del error.** El error de oficio más caro, y el más silencioso. La desviación estándar que casi todo el mundo calcula es la de las ventas históricas; la que la fórmula de stock de seguridad pide es la del **error de pronóstico**: cuánto se equivocó la predicción, no cuánto varió la demanda. `src/forecasting.py` deja registrado cuál de las dos le entregó al motor: error fuera de muestra, error dentro de muestra, dispersión cruda como sustituto, o no disponible. La planilla no tiene ese campo: nunca sabés en cuál de los cuatro estás.

**El dato faltante que la planilla convierte en cero.** Una celda vacía llega al motor como `NaN`, y `NaN < 0` es falso: un chequeo ingenuo de signo la deja pasar y envenena lo que sigue. El módulo la rechaza de frente, con un mensaje que incluye, textual, `a blank period is not a demand of zero - drop it or fill it deliberately`.

La otra mitad es más fina: con un solo período observado la desviación muestral no existe, y devolver cero produce una dispersión *finita y nula* que aguas abajo se lee como historia plana medida, no como dato faltante. La planilla imprime "no hace falta colchón" como un hecho. De ahí el mínimo de períodos antes de que un pronóstico pueda dimensionar una compra: un umbral de negativa, no una perilla. Por debajo, la salida honesta es "no hay historia suficiente", nunca una cantidad.

## Kern puede escribir en tu propia planilla

El punto que suele sorprender: no hay que sacar Excel para empezar. `src/connectors/excel.py` expone tu libro como sistema de registro escribible, con la misma superficie que los conectores de ERP: reutiliza sin cambios el plano de seguridad de `nosilo_core`, el control plane agnóstico de dominio bajo el writeback.

Una hoja es la entidad y una celda es el campo, sin supuestos de esquema; encima, una capa traduce "SKU-002, columna Punto Reorden, 90" a la celda concreta y detecta sola la fila de encabezado dentro de las primeras veinte filas, así que tus filas de título no necesitan configuración. Lo que importa son las negativas:

- **Chequeo de deriva.** Si una celda del cambio preparado se movió en disco antes de aplicarlo, el commit se niega y no escribe nada.
- **Escritura atómica.** Se guarda en un temporal y se reemplaza el archivo de una sola vez. Si lo tenías abierto en Excel, el error lo dice y aclara que no se cambió nada.
- **Respaldo del original** antes de tocar nada, y **vuelta atrás celda por celda** guardada con el cambio: se deshace más tarde, no solo en el momento.
- **Filas duplicadas: falla cerrado.** Si dos filas comparten el mismo SKU en la columna clave, el conector se niega en vez de elegir una.
- **Macros preservadas, nunca ejecutadas.**

Arriba de todo eso, una persona ve el antes y el después y firma. De los cuatro desenlaces posibles, tres se detienen en un humano por diseño.

## Comparado con qué

**La planilla sola.** El status quo real: calcula bien, no recuerda. No guarda el error del ciclo anterior, no distingue las dos sigmas y depende de que quien entiende las fórmulas siga en la empresa. Se rompe en el SKU 4.000 o en el segundo almacén (escala ilustrativa, no un cliente medido).

**Min/max en Odoo.** Reglas estáticas: le dicen al ERP cuándo pedir, sin pronosticar el patrón de demanda ni dejar constancia de quién movió el umbral. Kern no reemplaza tu Odoo; lo deja como brazo de ejecución de una política preparada y reversible.

**Un SaaS de planificación.** Te da la herramienta y te deja operándola: el método correcto queda como ajuste opcional —dispersión cruda en vez del error de pronóstico— y quien marca esa casilla sos vos.

**Un consultor.** Entrega el análisis, y bien. Una vez: el conocimiento se va con la persona, y bajo presión de calendario la simulación es el primer paso que salta.

**Un chatbot sobre tu exportación.** Párrafo plausible: no se compromete con un umbral legible ni cita de dónde salió.

Kern entra por otro lado: el método corriendo sobre el archivo que ya tenés, con la escritura firmada por una persona.

## Lo que Kern no hace

No te pide arrancar Excel de raíz la semana que viene, y sería raro que lo hiciera: los entregables **son** Excel, más el reporte y el gráfico. No ejecuta tus macros. No es tu WMS. No hay integración de inventario con Shopify ni con Mercado Libre.

El escaneo y el conector tampoco son lo mismo: el demo analiza un archivo en solo lectura, y el conector es un camino aparte con firma humana.

## FAQ

### ¿Tengo que migrar de Excel para empezar?

No. El escaneo lee el archivo que ya exportás y el entregable vuelve como libro de Excel. El conector de writeback escribe en la planilla del cliente; no la reemplaza.

### ¿Kern puede editar mi planilla?

Sí, por el conector de `src/connectors/excel.py`, con una persona que aprueba el antes y el después. Verifica que ninguna celda haya cambiado en disco desde que se preparó el cambio, deja respaldo del original y reemplaza el archivo de forma atómica. La vuelta atrás es celda por celda.

### ¿Alcanza con un CSV para el escaneo gratis?

Sí. El demo acepta CSV o Excel y elige el lector según la extensión. Las columnas exigidas son `product_id`, `on_hand` y `daily_demand`; `unit_cost` y `days_since_last_sale` son opcionales. Es un camino de solo lectura.

### ¿Mis macros se van a ejecutar?

No. Un `.xlsm` se abre preservando el proyecto VBA, así que las macros siguen ahí después de escribir, pero nunca se corren: ejecutarlas necesita un Excel vivo por COM, fuera del alcance de un conector de archivo.

### ¿Qué se rompe cuando aparece un segundo almacén?

La planilla deja de tener una sola respuesta correcta: la misma unidad falta en una bodega y sobra en la otra, y ninguna columna dice cuál mover. Eso es planificación multi-eslabón, trabajo de un paquete posterior: el escaneo público no corre DRP ni redistribución.

## Siguiente paso

Mandá la exportación que ya tenés. [Escaneá tu CSV o tu Excel en el demo](/demo) con las columnas de `documentation/templates/stock_template.csv`: vuelve la clasificación de tu stock, en solo lectura, y es la familia de análisis con la que arranca el [Diagnóstico de Arranque](/paquetes/diagnostico-arranque).
