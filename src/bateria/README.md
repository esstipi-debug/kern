# Batería de 20 tareas — la fuente

`tareas.json` es la **fuente de verdad** de la vara con la que se puntúa Kern
contra el oficio. `documentation/KERN_BATERIA_20_TAREAS.md` es el resumen
publicable; este directorio es lo que un harness puede correr.

## Procedencia (importa para que la vara valga)

La batería fue escrita el 25-jul-2026 por un agente con **prohibición explícita de
leer este repositorio**. Su nota textual, preservada en el JSON:

> No leí el repositorio de Kern: no abrí, busqué ni ejecuté ningún archivo del
> proyecto, y no consulté su lista de tools, su documentación ni su grafo de
> conocimiento.

El estándar se derivó de arriba hacia abajo desde SCOR Digital Standard v14.0
(ASCM 2025) y CSCP, más descripciones de puesto reales — nunca desde el producto.
Eso es lo único que hace que un puntaje bajo sea creíble y uno alto sea vendible.

**Recuperada el 01-ago-2026.** La enumeración completa no estaba versionada: sólo
existía en el journal de la corrida original (`wf_6a350eb4-65a`, agente
`a37252bae136725b9`). El markdown publicado nombra apenas 6 de las 20 tareas. Si
esa sesión se hubiera purgado, la vara se perdía y no era reconstruible sin violar
la regla de imparcialidad — no se puede re-derivar el estándar habiendo leído ya
el repo.

## Estructura

Cada tarea declara seis campos. El que importa para el harness es
`criterio_de_aprobado`: dice, en prosa y por adelantado, qué cuenta como cumplida
y qué explícitamente no.

```
T01.criterio_de_aprobado:
  "... Sin banda de incertidumbre NO cuenta: el stock de seguridad no se puede
   calcular con un punto solo"
```

## Cómo correrla

Las **15 comprobaciones de software están escritas** (`Puntaje.cobertura == 100%`).
Cada una corre el pipeline real y devuelve cumple/parcial/no **con evidencia**, no
una opinión. El punto de enganche es el decorador `@comprobacion("Txx")`.

```python
import src.bateria.comprobaciones  # NO es opcional -- ver abajo
from src.bateria.scorer import puntuar

p = puntuar()
print(p.puntos, p.porcentaje)  # 13.0 65.0  (o: python scripts/bateria_puntaje.py)
```

> **Sin ese import el marcador da 0,0% y no falla nada.** `_REGISTRO` se puebla por
> efecto secundario del decorador, y `scorer.py` importa `TareaBateria`/`cargar_bateria`
> pero **no** `comprobaciones.py`. Sin él las 15 tareas salen `PENDIENTE`, que suma
> cero por diseño, y el resultado es un 0,0% perfectamente silencioso. Si obtienes 0,
> es esto — no es que no haya nada implementado.

### El puntaje de hoy

**65,00% · 13/20**, medido sobre `1aa5098` (2026-09-05) y reproducible: dos corridas
seguidas dan el mismo vector de estados. El numero y la tabla los emite
`python scripts/bateria_puntaje.py --markdown`; no se tipean.

| Estado | N | Tareas |
|---|---|---|
| `CUMPLE` | 13 | T01, T02, T04, T05, T06, T07, T08, T09, T10, T12, T13, T14, T20 |
| `SIN_DATO` | 2 | T03, T11 |
| `FUERA_DE_ALCANCE` | 5 | T15, T16, T17, T18, T19 |

> El puntaje anterior publicado aqui (37,50% · 7,5/20, sobre `b0b7bb6`) quedo 160
> commits atras del harness: la mitad de las tareas que decia "a medias" o "no
> cumple" ya cumplian. Por eso desde el 2026-09-05
> `tests/test_bateria_puntaje_publicado.py` exige que este documento diga lo que el
> harness da, y congela el vector de estados.

Dos cosas que ese número exige aclarar, o la próxima corrida parecerá una regresión:

**Es el puntaje con `Contexto()` vacío.** T03 y T11 piden `sales_history.csv` y
`stock_on_hand.csv` bajo un `data_root`; sin él son `SIN_DATO`, no `NO_CUMPLE`. Con
datasets reales enchufados el número se mueve, y eso es correcto, no un fallo.

Medido: `scripts/build_bateria_datasets.py` arma `sales_history.csv` desde los
extractos M5 CA_1 (3.049 SKU × 64 meses, precio real) y **T03 pasa a `CUMPLE`** —
`42,50% · 8,5/20`. Es el mismo código: lo único que cambia es que el dato está.

**T11 sigue `SIN_DATO` y no lo destraba ningún dataset del repo.** Pide `on_hand`
por SKU, o sea una FOTO de inventario; M5, Superstore, Olist y DataCo son
transaccionales y registran lo vendido, nunca lo que quedó en góndola.
`daily_demand` sí sale de las ventas, pero derivar `on_hand` de ellas sería
inventarlo. `python scripts/build_bateria_datasets.py --explicar-t11` imprime las
columnas exactas que habría que pedirle a un cliente.

> **`SIN_DATO` no es el conteo de "bloqueadas por dato".** Sólo lo marcan los dos
> checks que piden un dataset por ruta; los otros 13 corren sobre fixtures donde el
> dato **sí está** y aun así el campo exigido no sale. Por eso las bloqueadas de
> verdad son **tres** (T03, T11 y T10, que necesita líneas de pedido reales) y las
> otras **diez** son hueco de entregable: el dato perfecto no las mueve. Desglose
> con evidencia en la §2 de `documentation/KERN_BATERIA_20_TAREAS.md`.

**Lo congela un test.** `tests/test_bateria_puntaje_publicado.py` corre la batería
entera en la CI, fija el vector de estados (`PUNTAJE_CONGELADO`) y exige que este
README y `documentation/KERN_BATERIA_20_TAREAS.md` publiquen el mismo número que
`scripts/bateria_puntaje.py`. Un cambio en `jobs/` que mueva el marcador pone rojo
ese test; subirlo o bajarlo es un diff explícito, con el motivo en el commit.

## Lo que la vara no puede ver: el barrido de neteo

La batería pregunta **«¿Kern puede?»** y varias tareas se puntúan con el **mejor**
candidato. T05 es el caso claro: sondea tres entregables de reposición y se queda con
el mejor de los tres. Eso es correcto para una vara — la pregunta es si la capacidad
existe — y **ciego para el producto**, cuya pregunta es la contraria: *«¿cuál de los
emisores **no** puede?»*.

El costo de esa ceguera está medido. `inventory_optimization`, el tool central,
dimensionaba `s` y `Q` sin mirar jamás el estante ni lo que venía en camino, y la
batería estuvo en verde todo ese tiempo: nunca fue candidato de T05. Tres tools
hermanos (`shopify_replenishment`, `channel_replenishment`, `drp`) estaban igual.
**Un tool que nadie sondea no se puede poner rojo.**

`src/bateria/barrido_neteo.py` es el diagnóstico que cierra eso, al lado de la vara y
sin tocarla: `tareas.json` es externa y `TOTAL_TAREAS` sigue en 20. Sondea **los
catorce** tools del registro que ponen un número de compra en manos de un operador,
uno por uno y con la misma disciplina (dos corridas, el valor se mueve, la ausencia se
declara). T05 pasó a **consumir** esas sondas, así que «netea» está definido una sola
vez en el repo.

```bash
python scripts/barrido_neteo.py              # tabla + evidencia (unos 3 s)
python scripts/barrido_neteo.py --markdown   # para pegar en el documento
```

Resultado publicado en `documentation/KERN_BARRIDO_NETEO.md`, congelado por
`tests/test_barrido_neteo.py` — que además **obliga a clasificar**: un tool nuevo que
no diga si emite un número de compra pone rojo la CI.

## Discrepancias con el doc publicado — ambas cerradas

`documentation/KERN_BATERIA_20_TAREAS.md` estuvo desalineado con la fuente en dos
puntos, los dos resueltos el 02-ago-2026 como decisión del dueño y no de costado:
es una pieza de venta con números publicados.

**Qué puntaje se publica.** Publicaba 7,5% · 1,5/20 (corrida manual del 25-jul,
anterior a #193/#194 y #203); ahora el puntaje del harness con el commit sellado.
La cadena que justifica la subida —el harness da 10,0% sobre el código de #202, a
media tarea del veredicto manual— está en el propio doc.

**Cuántas tareas son humanas.** Afirmaba **3** (T15/T16/T17), techo 85% · 17/20.
La fuente marca `TAREA HUMANA` en **cinco** (T15–T19), luego 15 alcanzables y
techo **75%**. El doc además contaba a T18 entre las de "código puro" y la
describía como "campos tipados del ledger", cuando T18 es *"sostener el plan
frente a la presión interna"*. Corregido en los dos sitios.

Con eso, `Puntaje` dejó de reportar dos techos: `techo_segun_doc` y
`_HUMANAS_SEGUN_DOC` existían **sólo** para modelar el desacuerdo y se
eliminaron. El techo vivo se deriva de `tareas_humanas()`, así que marcar una
`TAREA HUMANA` de más o de menos en `tareas.json` lo mueve solo en vez de dejarlo
mintiendo — y `tests/test_bateria_scorer.py` comprueba precisamente esa
derivación, no el 75,0 literal.

## Dos reglas de diseño que no se negocian

1. **Se puntúa el entregable (capa `jobs/`), nunca `src/` directo.** La matemática
   suele existir en `src/` y el job no le pasa los datos — puntuar la librería da
   ~90% y vuelve inútil la vara. Es el patrón central de
   [KERN_AUTOPSIA_FALLAS.md](../../documentation/KERN_AUTOPSIA_FALLAS.md).
2. **`SIN_DATO` ≠ `NO_CUMPLE`.** Falta de dato del cliente no es bug. Confundirlas
   manda a escribir código que no mueve el puntaje.

`PENDIENTE` y `SIN_DATO` suman cero: omitir una comprobación nunca puede inflar el
marcador.

## Trampa conocida al escribir un check

`getattr(obj, "campo", default)` devuelve el default cuando el campo se llama
distinto, y el check emite un veredicto silenciosamente equivocado — el mismo
patrón P2 que la batería denuncia. Acceder a los campos **directo**: un rename
tiene que romper fuerte. Si un campo es genuinamente opcional (existe en algunas
versiones del entregable y en otras no), tratar la ausencia como un caso propio
**con evidencia propia**, nunca como el caso negativo.
