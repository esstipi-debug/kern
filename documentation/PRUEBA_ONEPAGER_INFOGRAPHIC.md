# Prueba de 1 pagina: AntV Infographic como piel del entregable

**Fecha:** 2026-08-01 · **Version probada:** `@antv/infographic@0.2.19` (MIT)
**Reproducir:**

```bash
PYTHONPATH=. python examples/run_free_scan_onepager.py \
  --data data/sample_stock_snapshot.csv --client "Distribuidora Demo"
# abre webapp/static/onepager/index.html
```

## Que se probo

El escaneo gratis (`jobs/excess_obsolete_job.py`, el mismo job que corre
`examples/run_free_scan.py`) renderizado como one-pager visual, para decidir si
la libreria sirve como capa de presentacion del entregable de venta.

Numeros de la corrida real: USD 27.900 atrapados, 77% del valor de inventario,
3 SKUs muertos + 1 excedido de 8 analizados.

## Lo que funciona

- **La costura es tan barata como se esperaba.** Python solo emite texto (el DSL);
  el SVG lo arma la libreria en el browser. Cero dependencias Python nuevas, cero
  pipeline npm, el motor no se toca.
- **Bundle UMD por CDN**: `dist/infographic.min.js` (854 KB sin comprimir), global
  `AntVInfographic`. Ojo: el campo `unpkg` del `package.json` apunta a
  `dist/infographic.umd.min.js`, que **no existe** en el paquete publicado.
- **Los iconos resuelven** sin configuracion extra.
- El bloque `chart-bar-plain-text` sale limpio y es honesto: se puede usar.

## Los tres defectos encontrados

### 1. El color no puede significar nada (bloqueante)

Los templates asignan color **por indice de item**, no por dato. En la corrida,
SKU-004 (`dead`) sale verde azulado y SKU-006 (`excess`) sale ambar — el color
contradice la clasificacion.

Se probo pasar `color <hex>` por item: **el DSL lo ignora** y ademas rompe el
render del `icon` (el icono se reemplaza por un circulo solido). No hay override
por item desde la sintaxis.

Para un entregable que se vende con "cada numero trazable", un color que
contradice la clasificacion no es un detalle estetico: es un bug de credibilidad.

### 2. El titulo se superpone con la descripcion

Cuando el `title` envuelve a dos lineas, choca con el `desc`. El layout no es
robusto a largos de texto arbitrarios — que es exactamente lo que tiene un
documento generado.

### 3. Los hit-targets del editor se dibujan igual

Con `editable: false` el editor no se engancha, pero los rectangulos
`btn-add` / `btn-remove` (verde y rojo, `fill-opacity 0.3`) se siguen pintando
sobre la pagina. Hay que taparlos por CSS — ver el bloque en el generador.

## Decision tomada: adopcion parcial

Solo el grafico de barras sale de la libreria, y **solo las barras**. Titulo,
descripcion, tabla de acciones y footer son HTML propio. Eso neutraliza los tres
defectos de una:

| Defecto | Como queda neutralizado |
|---|---|
| Color por indice | Todas las barras del mismo color (`#b45309`). El largo codifica magnitud; la clasificacion viaja en la tabla HTML, donde el color si es fiel al dato |
| Titulo que se superpone | El `title`/`desc` del DSL no se usan: son `<h2>`/`<p>` propios |
| Hit-targets del editor | El template `chart-*` no los dibuja |

El `list-column-vertical-icon-arrow` en zigzag queda descartado: era mas dificil
de leer que una tabla de 4 filas y arrastraba el problema de color.

## Pendiente conocido

Las acciones salen en ingles (`liquidate / return to vendor / write off`) porque
asi las emite `jobs/excess_obsolete_job.py`, sobre una pagina en castellano. Se
puede mapear en la capa de presentacion (no toca numeros ni motor) o traducirlas
en el job. Sin decidir.

## Notas operativas

- Render 100% en el browser. Para adjuntar PNG/PDF en un mail hace falta el
  export del cliente (la libreria expone `exportToSVG` y opciones de PNG) o un
  headless. Hoy el activo es un link, no un adjunto.
- `v0.2.x`: la API todavia se mueve; conviene pinear la version exacta como en
  el generador.
