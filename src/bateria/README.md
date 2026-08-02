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
print(p.puntos, p.porcentaje)  # 5.5 27.5
```

> **Sin ese import el marcador da 0,0% y no falla nada.** `_REGISTRO` se puebla por
> efecto secundario del decorador, y `scorer.py` importa `TareaBateria`/`cargar_bateria`
> pero **no** `comprobaciones.py`. Sin él las 15 tareas salen `PENDIENTE`, que suma
> cero por diseño, y el resultado es un 0,0% perfectamente silencioso. Si obtienes 0,
> es esto — no es que no haya nada implementado.

### El puntaje de hoy

**27,50% · 5,5/20**, medido sobre `73b912d` y reproducible: dos corridas seguidas dan
el mismo vector de estados.

| Estado | N | Tareas |
|---|---|---|
| `CUMPLE` | 2 | T01, T04 |
| `PARCIAL` | 7 | T02, T06, T08, T09, T12, T13, T20 |
| `NO_CUMPLE` | 4 | T05, T07, T10, T14 |
| `SIN_DATO` | 2 | T03, T11 |
| `FUERA_DE_ALCANCE` | 5 | T15–T19 |

Dos cosas que ese número exige aclarar, o la próxima corrida parecerá una regresión:

**Es el puntaje con `Contexto()` vacío.** T03 y T11 piden `sales_history.csv` y
`stock_on_hand.csv` bajo un `data_root`; sin él son `SIN_DATO`, no `NO_CUMPLE`. Con
datasets reales enchufados el número se mueve, y eso es correcto, no un fallo.

**Nada lo congela.** Ningún test fija el agregado y la CI no corre la batería —
`tests/test_bateria_scorer.py` cubre la aritmética, no el resultado. Un cambio en
`jobs/` puede mover el marcador sin que nada se ponga rojo. Re-córrela antes de
citarla.

## Discrepancias abiertas con el doc publicado

`documentation/KERN_BATERIA_20_TAREAS.md` está desalineado con la fuente en dos
puntos. **Ninguno se corrige de costado**: es una pieza de venta con números
publicados, y ajustar al alza el puntaje de una vara que uno mismo propuso es
exactamente el movimiento que la destruye. Las dos son decisiones del dueño.

**Cuántas tareas son humanas.** El doc afirma **3** (T15/T16/T17) y de ahí deriva
el techo humano de 85% · 17/20. La fuente marca `TAREA HUMANA` en **cinco**:
T15–T19. El doc además atribuye a T18 "campos tipados del ledger", cuando T18 es
*"sostener el plan frente a la presión interna"*. Con 5 humanas, las alcanzables
por software son **15, no 17**, y la aritmética del techo cambia — de ahí que
`Puntaje` reporte los dos techos en paralelo. `tests/test_bateria_tareas.py`
congela lo que dice la fuente.

**Qué puntaje se publica.** El doc sigue en **7,5% · 1,5/20**: la corrida manual
del 25-jul-2026, anterior a #193/#194 y a #203. El harness da 27,50%.

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
