---
pair: excess-obsolete
lang: es
slug: stock-muerto
title: "Stock muerto: cómo medir el capital inmovilizado en lo que dejó de moverse"
description: "Muerto, excedido y sano son una clasificación del stock con umbrales escritos, no un criterio a ojo. La regla, las columnas y el ranking por plata."
date: 2026-08-25
updated: 2026-08-25
query: "stock muerto inventario"
answer: >
  El stock muerto es la existencia con demanda diaria en cero, o con un tiempo
  de inactividad conocido igual o mayor al umbral de muerte. El excedido todavía
  rota, pero acumula más días de cobertura que el objetivo. El resto es sano.
  Cada SKU en riesgo se valoriza como unidades en riesgo por costo unitario, y la
  lista se ordena por esa plata: el resultado es un ranking, no una opinión.
cta: demo
offer: diagnostico-arranque
sources:
  - kind: code
    ref: src/excess_obsolete.py
  - kind: code
    ref: documentation/templates/stock_template.csv
faq:
  - q: "¿Cuál es el mínimo de columnas?"
    a: "product_id, on_hand y daily_demand son el mínimo. unit_cost es lo que convierte unidades en ranking de plata, y days_since_last_sale es opcional. Esas cinco son las columnas de documentation/templates/stock_template.csv."
  - q: "¿Cuál es la diferencia entre muerto y excedido?"
    a: "Muerto dejó de moverse: demanda diaria en cero o menos, o inactividad conocida igual o mayor a 180 días. Excedido todavía rota, pero carga más que el objetivo de 90 días de cobertura. Un SKU con demanda cero nunca queda como excedido: queda muerto."
  - q: "¿El escaneo escribe algo en mi ERP?"
    a: "No. El escaneo lee un CSV que vos exportás y devuelve la clasificación y el ranking. No hay writeback en el camino de E&O, y no hay integración de inventario con Shopify ni con Mercado Libre."
  - q: "¿Los montos del artículo son de un cliente real?"
    a: "No. Todos los números son ilustrativos y salen de las filas de ejemplo de la plantilla del repositorio. Este artículo no lleva resultados de clientes, montos recuperados ni testimonios."
  - q: "¿Cuál es el paso pago después del escaneo gratis?"
    a: "El Diagnóstico de Arranque. El demo público corre la misma familia de análisis con la que arranca el diagnóstico, así que el escaneo te dice si vale la pena agendarlo antes de agendarlo."
noindex: false
---

# Stock muerto: cómo medir el capital inmovilizado en lo que dejó de moverse

El stock muerto es la existencia con demanda diaria en cero, o con un tiempo de inactividad conocido igual o mayor al umbral de muerte. El excedido todavía rota, pero acumula más días de cobertura que el objetivo. El resto es sano. Cada SKU en riesgo se valoriza como unidades en riesgo por costo unitario, y la lista se ordena por esa plata: el resultado es un ranking, no una opinión.

## Qué se mide

La conversación sobre stock muerto casi siempre se traba en el mismo punto: es una impresión. Alguien camina la bodega, señala un pallet que nadie tocó desde la última temporada y dice que está muerto. El siguiente mira el mismo pallet y dice que es respaldo por si el proveedor falla. Ninguna de las dos lecturas se puede verificar, así que gana el que tiene más antigüedad en la empresa.

La alternativa es convertirlo en una clasificación con umbrales escritos. En `src/excess_obsolete.py` cada SKU cae en exactamente uno de tres grupos, y la regla que lo puso ahí está a la vista:

- **Muerto** — `daily_demand <= 0`, **o** inactividad conocida igual o mayor a `dead_threshold_days`, que por defecto es 180. Todo lo que hay en mano cuenta como en riesgo.
- **Excedido** — todavía rota, pero los días de cobertura (`on_hand / daily_demand`) superan `target_cover_days`, que por defecto es 90. Las unidades en riesgo son las que están por encima de ese objetivo.
- **Sano** — el resto. Cero unidades en riesgo.

Lo importante no son los números en sí. Es que son parámetros con valores por defecto que se pueden leer, discutir y cambiar, en vez de un umbral que vive en la cabeza de una persona.

## Cómo se calcula

Los días de cobertura son `on_hand / daily_demand`. Cuando la demanda es cero o no es finita, la cobertura es infinita, que es la forma honesta de representar un SKU que con el comportamiento actual no se va a agotar nunca.

Las unidades en riesgo cambian según el grupo. En un SKU muerto está en riesgo todo lo que hay, porque no hay demanda que lo consuma. En uno excedido solo lo que está por encima del objetivo de cobertura:

```
cobertura      = on_hand / daily_demand
muerto         : unidades_en_riesgo = on_hand
excedido       : unidades_en_riesgo = on_hand - 90 * daily_demand
plata_en_riesgo = unidades_en_riesgo * unit_cost
```

El orden es `plata_en_riesgo` de mayor a menor. Ese orden **es** el entregable. Una lista de SKU ordenada alfabéticamente, o por unidades, no te dice por dónde empezar; ordenada por la plata que cada uno retiene, las primeras cinco filas son la reunión.

Tres SKU ilustrativos, armados con las filas de ejemplo de `documentation/templates/stock_template.csv` (ej. solamente, no son datos de cliente):

| SKU | En mano | Demanda diaria | Días inactivo | Costo unitario | Cobertura | Clase | Plata en riesgo |
|---|---|---|---|---|---|---|---|
| A | 800 | 0 | 220 | $8,00 | infinita | muerto | $6.400 |
| B | 900 | 5 | 12 | $12,50 | 180 d | excedido | $5.625 |
| C | 150 | 3,5 | 10 | $12,50 | 42,9 d | sano | $0 |

El SKU A dispara la regla de muerte por partida doble: demanda en cero e inactividad por encima de 180 días. Las 800 unidades quedan en riesgo. El B sí rota, y aun así carga el doble del objetivo de cobertura, así que las 450 unidades por encima quedan en riesgo. El C está dentro del objetivo y no aporta nada.

Fijate en el orden. El B tiene más unidades en mano que el A y queda igual en segundo lugar, porque el ranking es por plata y no por volumen. Es la diferencia entre vaciar el rincón más aparatoso de la bodega y vaciar el más caro.

## La columna opcional que cambia la respuesta

`days_since_last_sale` es opcional, y por defecto vale `None`, no `0`. Esa distinción hace un trabajo real, y el comentario del módulo dice por qué:

> Antes venía por defecto en 0.0, es decir "se vendió hoy": la lectura que más favorece al SKU, y la que deja fuera del conteo a uno genuinamente muerto.

Si una inactividad desconocida se leyera como cero, todo SKU sin ese dato parecería vendido hoy, y la regla de inactividad nunca se dispararía justo en las filas donde tenés menos información. Hoy, desconocido simplemente no activa la regla. Un SKU sin ese dato igual puede quedar muerto, pero por la prueba de demanda, no por accidente.

La consecuencia práctica para tu exportación: si podés entregar la inactividad, entregala. Si no podés, dejá la celda vacía en vez de rellenarla con ceros. Un cero es una afirmación; una celda vacía es la verdad.

## Comparado con qué

**Excel sin una regla de cobertura.** La planilla calcula días de cobertura en una columna, y eso es lo fácil. Lo que no suele sostener es un objetivo escrito, un umbral de muerte escrito y un tratamiento consistente del dato faltante en todas las filas y todos los meses. La fórmula no es el problema: la disciplina de aplicar la misma dos veces seguidas es lo que se erosiona.

**"Lo veo a ojo".** Funciona con veinte SKU y con una sola persona mirando. Con dos mil SKU y dos personas, produce dos listas distintas, y la discusión pasa a ser sobre quién tiene razón en vez de sobre qué liquidar primero.

**Min/max en el ERP.** Los umbrales de mínimo y máximo le dicen al sistema cuándo reponer. No dimensionan la plata que ya está por encima del objetivo, y no clasifican de ninguna manera a un SKU con demanda cero, porque uno que no tiene demanda nunca cruza un punto de reposición. Se queda callado en la bodega.

**Un consultor.** Entrega el análisis, y lo entrega bien. Lo entrega una vez. La clasificación de arriba es la misma cada vez que corrés el archivo, con los mismos umbrales, y podés compararla contra la del mes pasado.

## Lo que Kern no hace

El escaneo lee el CSV que vos exportás. No escribe en tu ERP, no toca tus publicaciones y no es una integración de inventario con Shopify ni con Mercado Libre. El camino de E&O toma un archivo, lo clasifica y devuelve un entregable con el ranking y una acción recomendada por clase.

Las acciones recomendadas salen del propio módulo y son deliberadamente secas: liquidar, devolver al proveedor o dar de baja para lo muerto; dejar de comprar, redistribuir o promocionar para bajar stock en lo excedido; monitorear y no hacer nada en lo sano. Son disparadores de una decisión, no la decisión.

De forma más general, Kern prepara el trabajo y una persona firma lo irreversible. De los cuatro desenlaces posibles de un resultado guiado, tres se detienen en un humano por diseño.

Este artículo tampoco lleva resultados de clientes. Todas las cifras de arriba son ilustrativas y salen de la plantilla de ejemplo del propio repositorio.

## FAQ

### ¿Cuál es el mínimo de columnas?

`product_id`, `on_hand` y `daily_demand` son el mínimo. `unit_cost` es lo que convierte unidades en ranking de plata, y `days_since_last_sale` es opcional. Esas cinco son las columnas de `documentation/templates/stock_template.csv`.

### ¿Cuál es la diferencia entre muerto y excedido?

Muerto dejó de moverse: demanda diaria en cero o menos, o inactividad conocida igual o mayor a 180 días. Excedido todavía rota, pero carga más que el objetivo de 90 días de cobertura. Un SKU con demanda cero nunca queda como excedido: queda muerto.

### ¿El escaneo escribe algo en mi ERP?

No. El escaneo lee un CSV que vos exportás y devuelve la clasificación y el ranking. No hay writeback en el camino de E&O, y no hay integración de inventario con Shopify ni con Mercado Libre.

### ¿Los montos del artículo son de un cliente real?

No. Todos los números son ilustrativos y salen de las filas de ejemplo de la plantilla del repositorio. Este artículo no lleva resultados de clientes, montos recuperados ni testimonios.

### ¿Cuál es el paso pago después del escaneo gratis?

El Diagnóstico de Arranque. El demo público corre la misma familia de análisis con la que arranca el diagnóstico, así que el escaneo te dice si vale la pena agendarlo antes de agendarlo.

## Próximo paso

Exportá las cinco columnas de arriba y [escaneá tu CSV en el demo](/demo). Devuelve la clasificación y el ranking por plata de tus propios SKU, en solo lectura, y es la misma familia de análisis con la que arranca el [Diagnóstico de Arranque](/paquetes/diagnostico-arranque).
