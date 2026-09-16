# Twin sembrado con el catálogo real de un negocio — diseño

- Fecha: 2026-09-16
- Estado: implementado en la rama feat/twin-catalogo-real (plan docs/superpowers/plans/2026-09-16-twin-sembrado-catalogo-real.md).
- Corrección al escribir el plan: jobs/digital_twin_job.py SÍ recibe dos cambios
  aditivos y compatibles (run() reenvía initial_on_hand del nodo; TwinReport
  exporta served_rows), porque sin ellos la decisión 5 y la 7 no se pueden
  construir. src/digital_twin.py sigue intacto. Los CSV con fecha diaria se
  escriben en el módulo nuevo: write_operational fecha por semanas.
- Rama: ninguna todavía.
- Regla del pivot #11 que este diseño respeta: **no agrega una tool al registry**.
  Es un job + un example. Se congela igual que todo lo demás hasta el primer pago;
  se diseña porque el dueño lo reafirmó dos veces (15-sep y 16-sep) después de
  escuchar la objeción. Queda registrado acá una sola vez.

## El problema

Kern tiene un twin de red (`src/digital_twin.py`, PR #140) que simula una cadena
proveedor → CD → tienda con ventas perdidas, colas FIFO y tres disrupciones. Corre,
y corrió el 2026-09-16 bajo cuatro escenarios. Pero su entrada es una red
**inventada**: `jobs/digital_twin_job.py::prepare` fabrica `SKU-001…SKU-050` con una
demanda lognormal desde una semilla. Nada de lo que simula pertenece a un negocio
con nombre.

Al mismo tiempo existe, en disco, el catálogo real de un distribuidor de congelados
chileno leído de su propia tienda el 2026-08-28: `radiografia-mdlc-catalogo-2026-08-28.json`
(170 productos, 216 variantes, precios en CLP, estado de stock por variante, fechas
de alta y modificación). Es lo observable de ese negocio desde afuera, sin que
nadie diera acceso.

Lo que falta es el puente: que lo observable entre al twin tal cual, y que lo no
observable entre como hipótesis declarada. Eso es la "fidelidad 3" de la tabla del
2026-09-16: entre el twin genérico (fidelidad 2) y el twin con los datos internos del
cliente (fidelidad 4, que no se construye: se consigue con una clave API).

## Qué se construye

Un cargador que toma el JSON del catálogo y produce **una lista de payloads** para el
`run()` que ya existe, más un reporte consolidado que suma los resultados por grupo y
los valoriza con los precios reales.

```
radiografia-*.json ──► jobs/twin_catalogo.py::prepare_from_catalog ──► [payload por grupo]
                                                                            │
                                            jobs/digital_twin_job.run (sin cambios) ◄┘
                                                                            │
                       jobs/twin_catalogo.py::consolidar ◄──────────────────┘
                                │
                    CatalogTwinReport + deck + los mismos 3 CSV del twin
```

Archivos nuevos: `jobs/twin_catalogo.py`, `examples/run_twin_catalogo.py`,
`tests/test_twin_catalogo.py`, `tests/fixtures/catalogo_mdlc_6.json` (6 productos
reales extraídos del JSON grande, para no versionar 411 KB).

Archivos que **no** se tocan: `src/digital_twin.py`, `jobs/digital_twin_job.py`,
`scm_agent/tools.py`, `tool_options.py`, `citation_gate.py`, `i18n.py`,
`how_it_works_data.py`. Ninguno de los 8 lugares fijos de una tool nueva.

## Decisiones tomadas

### 1. Un grupo = una red. El grupo es `product_type`, y se declara como proxy.

El núcleo no ata productos a proveedores: `run()` recorre cada producto por la red
completa (`jobs/digital_twin_job.py:175-185`). Para que "se cae el proveedor de
merluza" golpee solo a los pescados, el cargador parte el catálogo por `product_type`
(Mariscos 46, Pescados 45, Apanados 39, Frutas y Verduras 23, Frutos Secos 13,
Cerdo 2, Otros 2) y arma **una red por grupo**:

```
PROVEEDOR-<grupo> ──(lead 7 días)──► CD-MDLC ──(lead 1 día)──► TIENDA-WEB
```

`product_type` es un proxy del proveedor, no el proveedor. El reporte lo dice así:
"proveedor: no declarado; agrupado por categoría del catálogo". Nunca se nombra un
proveedor real.

Grupos con menos de 5 variantes (Cerdo, Otros) se funden en "Otros" para que la
lognormal de demanda no degenere.

### 2. Tres estados por campo, nunca dos.

La disciplina de la reorientación del 2026-07-25: declarado / no declarado /
contradicho. El cargador la aplica campo por campo y el reporte imprime la tabla.

| Campo del twin | Fuente en el catálogo | Estado |
|---|---|---|
| `product_id` | `variants[].sku` | **declarado** (159); **no declarado** (57) → se usa `variant.id` como clave y se cuenta aparte |
| precio (para valorizar) | `variants[].price` | **declarado** (216/216) |
| en quiebre hoy | `variants[].available == false` | **declarado** (63/216) |
| grupo / proveedor | `product_type` | **declarado** como categoría; **no declarado** como proveedor |
| demanda base | — | **no declarado** → prior (decisión 3) |
| lead times | — | **no declarado** → prior (decisión 4) |
| peso / unidad | `grams` (4/216) y el título ("0,5 kg") | **no declarado** en v1; la unidad de demanda es la variante, no el kilo |
| SKU `MAFI` en dos variantes de distinto peso | catálogo | **contradicho** → se simulan como dos productos con clave `MAFI#<variant.id>` y se reporta |

"Contradicho" existe para que el hallazgo #1 de la radiografía del 28-ago no se
pierda dentro del twin: el cargador no lo arregla, lo declara.

### 3. La demanda es una hipótesis, y se ve como tal.

No hay ventas observables. La demanda base por variante sale del mismo mecanismo que
ya usa `prepare` (lognormal σ = 0,7 ordenada, ABC implícito) escalada por una base
por grupo que el operador pasa como parámetro (`base_demand_por_grupo`, default 8
unidades/día para todos). `noise_cv` 0,25, `season_amplitude` 0,25, `season_period`
364, sin promos, sin tendencia. Todo esto entra por parámetros y sale impreso en el
reporte bajo el título "Hipótesis de demanda (no observadas)".

El periodo es el **día**, 364 periodos. Congelados de pesca se compran y se venden
a diario; la semana escondería el corte de un proveedor de 10 días.

### 4. Lead times: prior por grupo, declarado.

Proveedor → CD: 7 días para todos los grupos en v1. CD → tienda: 1 día. Ambos
parámetros, ambos impresos como "no declarado — prior". Sin `lead_time_std`: el
twin no lo modela.

### 5. Lo que hoy está en quiebre arranca vacío.

Las 63 variantes con `available == false` son el único estado interno observable.
Dentro de cada grupo, el cargador arma **dos payloads**: uno con las variantes
disponibles (arranque en el nivel `S` auto-dimensionado) y otro con las quebradas,
donde `CD-MDLC` y `TIENDA-WEB` llevan `initial_on_hand = 0`. `NodeSpec` ya lo
soporta (`src/digital_twin.py:66`); no hay cambio de núcleo. El reporte separa
"cuánto tardan en recuperarse las quebradas de hoy" de "cuántas se quiebran las
sanas".

### 6. Escenarios de estreno: los tres del núcleo, sobre un grupo con nombre.

El example corre, por defecto, sobre el grupo **Pescados**:

| Escenario | Parámetros | Pregunta que contesta |
|---|---|---|
| Base | sin disrupción | ¿cuánto quiebra la red con estos priors, sin que pase nada? |
| Corte del proveedor | `supplier_outage`, día 90, 42 días | "se te cae el proveedor de pescado 6 semanas" |
| Plazo estirado | `lead_time_spike`, +5 días, 30 días | "el proveedor te empieza a entregar tarde un mes" |
| Pico de demanda | `demand_surge`, ×2, 14 días | "dos semanas de demanda doble" |

`auto_size_factor` default **1,0**, no 1,5: con 1,5 la red no se quiebra bajo ningún
escenario (verificado el 2026-09-16) y el resultado no enseña nada.

### 7. Valorizar con precios reales, y decir exactamente qué se valoriza.

Unidades perdidas × `price` de la variante = **venta en riesgo** (CLP). Es el único
número en pesos del reporte. No es margen, no es costo de stock, no es pérdida
contable: el twin no tiene costos y el catálogo no tiene márgenes. El deck lo llama
"venta en riesgo a precio de lista" y no otra cosa.

### 8. Salida: un reporte por corrida, los mismos CSV que el twin.

`CatalogTwinReport` (dataclass congelada, como `TwinReport`):

- por grupo y escenario: fill de la red, días-tienda en quiebre, venta en riesgo
  CLP, las 10 variantes más golpeadas (con su estado declarado/no declarado de SKU);
- la tabla de cobertura de tres estados (decisión 2), siempre, aunque esté todo en
  verde;
- las hipótesis (decisiones 3 y 4) con sus valores;
- los tres CSV que el twin ya exporta (`demand`, `inventory`, `orders`), con
  `product_id` real, para que el resto del motor pueda correr encima igual que
  con un export de cliente.

Deck en markdown, mismo estilo que `digital_twin_job.build_deck`.

### 9. Guardas.

- Techo de celdas: reutiliza `_MAX_CELLS` (2.000.000). 216 × 1 × 364 = 78.624.
- Techo de productos por payload: 250 (el `prepare` original tiene 50; este cargador
  no pasa por `prepare`, define el suyo).
- Toda variante sin `price` finito y positivo se excluye y se cuenta en el reporte.
- Sin `product_type` → grupo "Otros".
- Entrada que no sea una lista de productos Shopify con `variants` → `ValueError`
  con el campo que faltó.

## Lo que NO se promete (regla del proyecto, más la doctrina de las 6 objeciones)

- No es una medición de la bodega de nadie. Es **una hipótesis sobre su catálogo**.
  El deck abre con esa frase. Objeción 1 de la doctrina del 2026-07-24: el quiebre
  publicado es estado de publicación, no inventario.
- No se afirma nada sobre vedas, ni exposición ni infracción. `veda_calendar.py` no
  entra en este diseño.
- No se normalizan pesos (kg vs unidad). Queda declarado como no declarado.
- No se nombra un proveedor real. Ni el grupo se presenta como "su proveedor".
- No se agrega nada al registry, al MCP ni a la webapp.

## Pruebas

`tests/test_twin_catalogo.py`, sobre la fixture de 6 productos reales:

1. La tabla de tres estados cuenta bien: SKU declarado / no declarado / contradicho
   (la fixture incluye `MAFI` con sus dos pesos y una variante sin SKU).
2. La partición por grupo produce una red por `product_type`, con los chicos fundidos
   en "Otros".
3. Las variantes `available == false` arrancan con `initial_on_hand = 0` en CD y
   tienda; las disponibles no.
4. Venta en riesgo = Σ (unidades no servidas × precio), verificado contra un caso a
   mano con 2 variantes y 3 días.
5. Misma semilla → mismo reporte (determinismo); semilla distinta → distinto.
6. Un catálogo sin `variants` levanta `ValueError` que nombra el campo.
7. El reporte imprime la sección "Hipótesis de demanda (no observadas)" aunque el
   operador no pase ningún parámetro.

Mutación mínima antes de dar por bueno: quitar el `initial_on_hand = 0` debe romper
la prueba 3; cambiar `price` por `compare_at_price` debe romper la 4.

## Tamaño

Un job (~250 líneas), un example (~80), una fixture, una suite (~150). Un día de
trabajo. Un PR.

## Qué queda fuera, a propósito

- Atar productos a proveedores dentro del núcleo (`src/digital_twin.py`). Sería la
  forma correcta si algún día un cliente entrega su maestro de proveedores; hoy nadie
  lo entregó.
- Un segundo catálogo. La máquina generalizada es la tool #54 con otro nombre.
- Cualquier lectura nueva del sitio de MDLC. El JSON del 28-ago alcanza para el
  diseño; re-escanear es una decisión comercial (es la opción A de la conversación
  del 15-sep), no técnica.
