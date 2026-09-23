# Una fecha de fin por SKU: +16 puntos, y con datos que el cliente ya tiene

> Medido el 2026-09-21 sobre `873c919`. Línea base congelada en
> [`lineas_base/horizonte_por_sku.json`](lineas_base/horizonte_por_sku.json), regrabable
> con `scripts/horizonte_por_sku.py`. `tests/test_horizonte_por_sku_publicado.py` ata el
> documento a su fuente.
>
> **Esto mide una idea; no construye una capacidad.** No se agregó un campo al intake ni
> una regla al motor. Se mide antes de construir, que es la disciplina de toda esta rama:
> si la palanca no paga, no se toca el producto.
>
> ⚠️ **Re-medido sobre 25 mundos el 2026-09-23:
> [`KERN_PALANCAS_SOBRE_25_MUNDOS.md`](KERN_PALANCAS_SOBRE_25_MUNDOS.md).** La línea base de
> acá **no se toca** y sigue siendo la medición correcta de sus cinco mundos, pero dos cosas
> de este documento hay que leerlas allá: la palanca queda en **+0,1309 (23/25, p < 0,001)**,
> o sea que **escala**; y el orden se invierte — sobre 25 mundos ésta es **la más grande de
> la rama**, no la segunda, porque el nivel de servicio (+18,9 acá) cae a +0,0512 y deja de
> distinguirse de cero. H1 también se refuerza allá.

## La pregunta

[`KERN_HORIZONTE_DE_LA_VARA.md`](KERN_HORIZONTE_DE_LA_VARA.md) cerró que el efecto de fin
de horizonte **no** explica el residuo —la vara no se lo debe a Kern— y dejó abierta la
única versión **desplegable** de "horizonte", que es otra cosa: no la fecha en que se
acaba el marcador, sino una **fecha de fin real por SKU** —discontinuación, fin de
temporada, cierre de ventana promocional— que el archivo del cliente tendría que traer.
La cadena de un cliente no se acaba; **un SKU sí**.

## La regla que se midió, en una línea

**No acumules por encima de lo que todavía podés vender.** Con `restante` = la demanda que
queda hasta la fecha de fin del SKU, se topean las **dos** mitades de la política:

```
order_up_to    = min(order_up_to,    restante)
reorder_point  = min(reorder_point,  restante)
```

Las dos, o no sirve: topear solo el objetivo deja el disparador alto y sigue pidiendo;
topear solo el disparador deja el objetivo alto y la orden sale igual de grande. Un SKU
sin `restante` calculado se deja **intacto** — topear con un cero que nadie midió sería
decirle que no compre nunca más, el mismo defecto que `tests/test_negativa_no_es_cero.py`
fija en el productor.

## El resultado

| carril | clase | captura | Δ vs kern | mejoran | fill | unidades | cierre |
|---|---|---:|---:|---:|---:|---:|---:|
| piso | vara | +0,0 % | — | — | 0,817 | 3.064 | 25.017 |
| **kern** | **control** | **−27,6 %** | — | — | 0,750 | 2.987 | 30.440 |
| **horizonte_estimado** | **desplegable** | **−11,5 %** | **+0,1618** | **5/5** | 0,748 | 2.634 | **18.276** |
| horizonte_real | diagnóstico | −14,1 % | +0,1348 | 5/5 | 0,749 | 2.652 | 19.783 |
| techo | vara | +100,0 % | — | — | 1,000 | 3.172 | 0 |

**+16,2 puntos, y mejora en los cinco mundos.** Es la segunda palanca más grande medida
en esta rama, después del nivel de servicio (+18,9) — y a diferencia de aquélla, **ésta sí
se puede accionar**.

## H1 · El hallazgo que no esperaba: saber el futuro no compra nada

`horizonte_real` topea con la demanda futura **verdadera** del mundo, que nadie tiene.
`horizonte_estimado` topea con la **media observada × semanas que faltan**, que es
exactamente lo que un cliente con una fecha de discontinuación calcula con su propio
historial. La brecha entre los dos debería ser el valor de la información que falta.

**No hay brecha.** El estimado sale +0,1618 y el real +0,1348 — el *desplegable* queda
por encima, y eso no se puede publicar como un triunfo sin explicarlo.

Primera hipótesis, testeada y **descartada**: que el estimado fuera un tope más apretado,
porque `generate_world` sortea `trend ~ U(−0,25, +0,60)` —tendencia mayormente positiva—
y entonces la media histórica subestimaría la demanda futura. Medido sobre las cuatro
semanas de replanificación de los cinco mundos (160 casos): el tope estimado queda por
debajo del real en el **59 %** y promedia el **99 %** de él. Real, pero chico. Y **no
correlaciona**: la semilla 1002 no aprieta (ratio 1,007) y el estimado le gana por +0,060;
la 1004 es la que más aprieta (0,903) y le gana por +0,0006.

| semilla | kern | estimado | real | est − real | ratio del tope |
|---|---:|---:|---:|---:|---:|
| 1000 | −0,3306 | −0,0956 | −0,1761 | **+0,0805** | 0,947 |
| 1001 | −0,0193 | +0,2133 | +0,2169 | −0,0037 | 1,145 |
| 1002 | −0,0471 | +0,0795 | +0,0191 | **+0,0604** | 1,007 |
| 1003 | −0,3884 | −0,2210 | −0,2194 | −0,0017 | 0,944 |
| 1004 | −0,5958 | −0,5483 | −0,5477 | −0,0006 | 0,903 |

El estimado gana en **2 de 5** mundos, y la ventaja media sale de esos dos; en los otros
tres la diferencia es **±0,004**, que es ruido. La lectura honesta no es *"el desplegable
gana"* sino **"los dos carriles son equivalentes"**: conocer la demanda futura verdadera
**no compra nada** sobre la media observada.

Y eso es la mejor noticia posible para desplegarlo. La palanca **no necesita** información
que nadie tiene — necesita una **fecha**, que el cliente sí sabe.

## H2 · El mecanismo: compra 12 % menos, sirve lo mismo

| | kern | horizonte_estimado | Δ |
|---|---:|---:|---:|
| unidades pedidas | 2.987 | 2.634 | **−12 %** |
| compra (usd) | 135.359 | 123.048 | **−12.311** |
| quiebre (usd) | 109.567 | 109.935 | **+368** |
| fill | 0,750 | 0,748 | −0,002 |
| capital pagado y sin vender al cierre | 30.440 | **18.276** | **−40 %** |

El ahorro es casi **puro**: gasta 12.311 menos en comprar y paga 368 más de quiebre. No es
un intercambio entre servicio y capital — el servicio no se mueve (dos milésimas de fill)
y el capital inmovilizado cae un 40 %. Es exactamente la forma del residuo que
[`KERN_RESIDUO_DE_LA_BRECHA.md`](KERN_RESIDUO_DE_LA_BRECHA.md) diagnosticó y que ninguna
de sus palancas podía tocar: todas eran *"comprá más"*, y ésta es la primera que es
**"comprá menos de lo que no vas a vender"**.

## Qué NO dice este documento

- **⚠️ En el marcador, TODOS los SKU mueren la misma semana.** El mundo termina en la 24 y
  ese es el único fin que existe, así que este número es el **techo de la idea sobre una
  cartera donde toda la línea se discontinúa a la vez**. Una cartera real tiene fechas
  escalonadas, y la mayoría de sus SKU **no tienen ninguna**. Lo realizable es proporcional
  a la fracción de la cartera que traiga la fecha — este +16,2 no se le promete a nadie.
- **No mide los 25 mundos de la línea base, sino 5** (semillas 1000-1004).
- **No es una implementación.** El tope se aplica como post-proceso sobre la `Policy` que
  devuelve el orquestador: mide el techo de la idea, no una versión del motor. Construirla
  toca el intake (una columna de fecha, con la ausencia declarada), el job, la QA y las
  superficies que rendericen el consejo — las ocho partes de siempre.
- **No mide qué pasa con una fecha EQUIVOCADA.** Un cliente que declare mal una
  discontinuación topearía la compra de un SKU que sigue vendiendo. Esa es la pregunta de
  seguridad de la capacidad y queda abierta.
