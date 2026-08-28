# BRIEF — Kern

**Entrevistado:** el fundador, 2026-08-24.
**Procedencia de las respuestas — leer antes de usar este archivo:**
las respuestas **6, 7 y 8 son textuales del fundador**. Las respuestas
**1 a 5 las propuso el agente a partir de documentos del propio repo**
(`05-GTM-KERN.md` §4 y §5, `marketing/precios-y-canal.md`) y el fundador
las **aprobó sin corrección** el 2026-08-24. No son autoría libre del
agente ni son cita textual del fundador. Esa distinción se mantiene aquí
y no se borra al reescribir.

---

## Las ocho respuestas

**1. Vibe, y referencias.**
Vibe: *instrumento de medición, no folleto.*
Referencias de otros medios: **ninguna entregada.** El fundador no dio
referencias y se siguió solo con la familia estética. Riesgo aceptado y
declarado: mayor probabilidad de salida genérica, porque las referencias
son justamente lo que separa una página de la anterior.

**2. El recorrido, sección por sección.**
Los seis pasos del discurso de `05-GTM-KERN.md` §5 «Estructura del
discurso», como seis escenas separadas:

1. Hecho sobre sus datos
2. El reencuadre
3. Por qué el sistema no lo ve
4. No es un trade-off
5. El número, en dos monedas
6. La prueba

**3. La curva de energía.**
Calma y factual al abrir — un hecho, no un grito. Sube en el reencuadre.
Intensa donde el ERP se rompe. **Cae a silencio en la prueba.** El cierre
pide dos archivos en solo lectura; pedirlo a gritos contradice lo que se
está pidiendo.

**4. Cómo debe sentirse, y el único momento que debe recordar.**
Ver «La curva del sentimiento» y «El pico», abajo.

**5. Una cosa que ningún sitio de la categoría hace.**
El scroll mueve **σLT de 0 a 11** y el buffer se redibuja en vivo. Es la
tabla de §5 convertida en instrumento en vez de en ilustración. Nadie en
la categoría lo hace porque casi ningún sistema calcula ese término.

**6. Familia estética.** — *textual del fundador*
> dense y brutalist

**7. Mundo continuo o escenas distintas.** — *textual del fundador*
> escenas distintas

**8. Assets existentes.** — *textual del fundador*
> no tengo assets

Consecuencia directa: gramática tipográfica, **cero gasto en kie.ai**,
sin `KIE_AI_API_KEY`. Construir sin assets es ruta de primera clase en
este skill, no un plan B.

---

## La curva del sentimiento

Una línea por acto: la emoción primero, la causa en pantalla después.

```
1  Reconocimiento   el campo del ERP contra los ultimos 40 despachos, tipografia plana, sin adorno
2  Duda             pantalla casi vacia, una sola frase          <- SILENCIO AUTORADO
3  Vertigo          sigmaLT entra en la formula y el buffer se redibuja de 30 a 186   <- PICO
4  Alivio           la frontera eficiente: sube en 47 SKU, baja en 12, el neto baja
5  Competencia      el numero en dos monedas, con su rotulo de ejemplo visible
6  Disposicion      un campo con cursor, no un boton
```

Seis emociones distintas, ninguna repetida en actos adyacentes. El alivio
del acto 4 existe porque el acto 3 lo tensó; el vértigo del 3 existe
porque el 2 se calló.

## El pico

Vive en el **acto 3**. La frase que el visitante le diría a alguien:

> *"es el sitio donde ves el número de tu ERP romperse por una variable
> que tu ERP no mide"*

Se lleva, a costa de los demás actos: el mayor `data-sc-span` de la
página, el `data-sc-dwell` que hace que la cámara se asiente exactamente
ahí, y el silencio del acto 2 delante para que el cambio tenga de qué ser
un cambio. **No hay un segundo pico.** Si algún acto empieza a competir,
se degrada: menos span, dispositivo más plano.

## El silencio autorado

**Acto 2.** Pantalla casi vacía sosteniendo una sola frase:

> **"No tienes mucho inventario. Tienes el inventario equivocado."**

Está puesto a propósito. El pase de verificación debe leerlo como
anticipación, no como scroll muerto ni como página que no cargó.

## La frase del test

> Es el sitio donde **ves el número de tu ERP romperse por una variable
> que tu ERP no mide.**

Es una experiencia, no el nombre de un dispositivo.

---

## Reglas duras — la marca las impone, no son preferencia

Heredadas de la doctrina de procedencia del repo (`citation_gate.py`,
agente `auditor-de-procedencia`, el aviso de `marketing/chile.md` de que
ningún número viene de un cliente real). El propio SKILL.md las reconoce:
*"A brand that forbids invented numbers means no stat counters, however
good they look."*

1. **Cero contadores de estadísticas sobre Kern.** Ninguna cifra de
   tracción, de clientes, de ahorro logrado ni de adopción — no existen y
   no se animan. **Precisión añadida el 2026-08-24:** el dispositivo
   `count` del motor **sí** se permite, y en un solo lugar: el ejemplo
   trabajado de §5 del acto 3, y únicamente con su rótulo «Ejemplo
   numérico» visible al mismo tiempo que la cifra. La distinción es entre
   animar *un cálculo que el lector puede rehacer* y animar *una
   afirmación sobre nosotros que nadie puede verificar*. Lo primero es el
   argumento; lo segundo es lo que esta regla prohíbe.
2. **Los números del pico son un ejemplo, y se rotulan como tal.**
   21 → 34 días, σ = 11, 30 → 186 unidades vienen de `05-GTM-KERN.md` §5
   bajo el título **«Ejemplo numérico»**. **No son la medición de ningún
   cliente.** Deben aparecer en pantalla con esa etiqueta visible. Si
   aparecen como resultado de cliente, la página no pasa la compuerta.
3. **Ninguna métrica de cliente**, ni logo, ni testimonio, ni caso: no
   existen todavía.
4. **Nunca decir «reducir inventario».** §5: operaciones escucha
   *"quieres que me quiebre"* y se cierra. Se dice **mover**.
5. **No liderar con la matemática.** GSM, DDMRP y Monte-Carlo no se
   nombran arriba de la página. Van abajo, cuando ya preguntaron cómo se
   hace, y ahí sí con detalle, porque son el foso.
6. **Precio, textual de §4:** 290 USD/mes, un solo precio, sin tramos,
   sin negociación. Puesta en marcha 0. **Diagnóstico gratis — es el
   vendedor.** Ningún otro precio aparece en la página sin volver a §4.
7. **Compuerta final:** `auditor-de-procedencia` antes de publicar. No es
   revisión opcional.

## Llamado a la acción

Una sola acción, una sola etiqueta, la misma en toda la página. De §5
paso 6, en las palabras que ya están escritas:

> **"Dame dos archivos en solo lectura y en 30 minutos te muestro los SKU
> con nombre y monto. Gratis, y no escribo nada."**

El cierre entrega **un campo con cursor, no un botón** — feel.md: un acto
de cierre que se desvanece a un escenario vacío es la página pidiendo
perdón por existir.

## Presupuesto de largo

8 a 14 alturas de viewport, del SKILL.md. El pico se lleva el tramo más
grande. Los actos administrativos (precio, cómo funciona) se comprimen.

---

# Paso 2 — Gramática, compuerta y score

## Gramática: `typographic poster` (uniqueness.md §2.5)

El tipo es la imagen. El contraste de escala hace todo el trabajo que
haría la fotografía. Elegida por tres razones, en orden:

1. **El activo de Kern es una frase.** §2.5: *"a brand whose asset is a
   sentence."* «No tienes mucho inventario, tienes el inventario
   equivocado» es el activo, y no hay imagen que lo mejore.
2. **No hay assets**, y §2.5 dice que es la respuesta correcta cuando
   generarlos produciría *"eight plausible, forgettable frames"*.
3. Casa con **Dense + Brutalist**: el grammar aporta la escala extrema,
   Dense aporta el cuerpo chico y de alto conteo. El ritmo es la
   distancia entre 40vw y 16px.

**Descartada `chaptered editorial` (§2.2):** asume una columna de medios
con pie de foto desde el capítulo uno. Sin assets, esa columna queda
vacía y el grammar se cae. No es *filmic one-shot*, así que no aplica la
carga de justificar las otras siete.

**Prohibiciones del grammar, que se respetan:** sin `scrub`, sin `pan` de
rieles de tarjetas, sin `tilt`, sin `parallax` sobre texto, sin fondo
fotográfico, sin scrims, sin tarjetas, sin movimiento decorativo. Y la
regla que manda sobre todas: *si un dispositivo está haciendo el trabajo
en vez de la tipografía, el grammar ya falló.*

**El piso tipográfico se endurece.** §2.5 levanta el tope de ~6rem para
display porque la página entera es un momento de hero, pero a cambio
aprieta tracking, medida y corrección óptica: **a 40vw el tracking por
defecto es un defecto visible y un solo kern malo es toda la página.**

## El signature move

El scroll mueve **σLT de 0 a 11** y la cifra de stock de seguridad se
redibuja de **30 a 186**. Codificado en la página, no es un parámetro de
un dispositivo del kit; el motor no se toca.

Es compatible con el grammar porque **la cifra es la tipografía**: el
número está a escala de composición y lo que cambia es el tipo, no un
adorno alrededor del tipo.

## Compuerta de fingerprint

El registro está **vacío** — es el primer build, así que no hay fila
contra la cual diferir y la compuerta pasa por construcción. Fila
planificada, para escribirse en `FINGERPRINTS.md` **solo cuando la página
salga**, que es cuando el registro manda que se anote:

| # | Dimensión | Valor |
|---|---|---|
| 1 | Grammar | Typographic poster |
| 2 | Nav | Sin barra persistente; el wordmark va a escala de composición |
| 3 | Hero device | `kinetic`: el número del propio ERP del visitante a escala extrema, sobre un bloque denso de cifras |
| 4 | Forma de la secuencia | 6 actos, 10,5vh, muy despareja — el pico se lleva el 32% |
| 5 | Cierre | La página se invierte al tipo más chico del sitio; CTA como enlace subrayado más un campo real con cursor |
| 6 | Signature move | El scroll mueve σLT y el buffer se redibuja de 30 a 186 |

## El score

| # | Acto | Sentimiento | Dispositivo | Span | Por qué ese |
|---|---|---|---|---|---|
| 1 | Hecho sobre sus datos | Reconocimiento | `kinetic` + bloque denso | 1,6vh | El hero del grammar debe ser una línea a escala extrema. Abre con **su** número, no con el nuestro |
| 2 | El reencuadre | Duda | `pin`, escala desde `--sc-p` | 1,2vh | **Silencio autorado.** El marco se queda quieto y una sola frase crece |
| 3 | Por qué el sistema no lo ve | Vértigo | **signature** + `count` | **3,4vh** | **PICO.** Mayor span y el `dwell` que asienta la cámara ahí |
| 4 | No es un trade-off | Alivio | `reveal` como barrido sobre las letras | 1,5vh | Un barrido es un cambio de estado, que es exactamente este acto |
| 5 | El número en dos monedas | Competencia | `flow` + `in`, denso | 1,3vh | Acto administrativo: se comprime. Cuerpo chico, dos monedas |
| 6 | La prueba | Disposición | dispositivo de puntero | 1,5vh | La superficie entrega **un campo**, no un botón |

**Total: 10,5vh.**

Chequeos del Paso 2, uno por uno:

- Prohibiciones del grammar: **se respetan** — no hay `scrub`, `pan`,
  `tilt` ni `parallax` en la página.
- Familias distintas: **seis** (`kinetic`, `pin`, `count`+bespoke,
  `reveal`, `flow`+`in`, puntero). El mínimo son cuatro.
- Ninguna familia dos veces seguida: **se cumple**.
- Como máximo dos actos `scrub`: **cero**. El grammar lo prohíbe y
  además no hay video.
- Ningún par adyacente con el mismo sentimiento: **se cumple**, las seis
  emociones son distintas.
- Un solo pico, con el mayor span por margen visible: **3,4vh contra
  1,6vh** del siguiente. Y el acto que lo precede es el más silencioso de
  la página (1,2vh).
- Largo total entre 8 y 14vh: **10,5vh**.
- **Fuera de la banda quemada.** SKILL.md marca 6–7 actos a 13,6–13,8vh
  como el patrón que repitieron los cuatro primeros builds del autor, y
  hoy es una dimensión de fingerprint. Este build tiene 6 actos pero a
  **10,5vh**, con reparto muy desparejo. No cae en la banda.

---

# Paso 4 y 5 — la página, y lo que la verificación encontró

Escrita y verificada el **2026-08-24**, servida en `localhost:4174`, viewport
1440×900, con Playwright. `index.html` más el motor sin editar.

## Score final, corregido contra lo medido

El score del Paso 2 tenía un defecto latente: el acto 2 y el acto 3 iban
los dos en contenedor `pin`, o sea **la misma familia dos veces seguida**,
que es un chequeo obligatorio del Paso 2. Los contenedores ahora alternan.
Las seis emociones y el pico no se movieron.

| # | Acto | Sentimiento | Contenedor | Dispositivo | Alto medido |
|---|---|---|---|---|---|
| 1 | Hecho sobre sus datos | Reconocimiento | `pin` | `kinetic` + marca en composición | 1,6vh |
| 2 | El reencuadre | Duda | `flow` | `reveal`, silencio sostenido | 1,5vh |
| 3 | Por qué el sistema no lo ve | Vértigo | `pin` | **signature** + `count` | **3,4vh** |
| 4 | No es un trade-off | Alivio | `flow` | `in` escalonado | ~1,0vh |
| 5 | El número en dos monedas | Competencia | `pin` | cues densos | 1,3vh |
| 6 | La prueba | Disposición | `flow` | puntero, campo real | ~1,4vh |

**Total medido: 10,24vh.** Dentro del presupuesto de 8 a 14, y fuera de la
banda quemada de 6–7 actos a 13,6–13,8vh.

## El signature move, medido

La página recalcula la fórmula cuadro a cuadro contra el recorrido del
marco fijado. Muestreo real a lo largo del acto 3:

| Recorrido | σLT | Varianza por LT | SS |
|---|---|---|---|
| 0,0 | 0,0 | 0 | **30** |
| 0,3 | 2,0 | 400 | 46 |
| 0,5 | 8,0 | 6.400 | 138 |
| 0,7 | 11,0 | 12.100 | 185 |
| 0,8 a 1,0 | 11,0 | 12.100 | **186** |

Los extremos reproducen exacto la tabla de `05-GTM-KERN.md` §5: 30
unidades con los supuestos del ERP, 186 con lo medido. La cifra llega a
186 al 80% del recorrido y se sostiene, para que la frase del 96% aterrice
con el número ya quieto.

## Los cinco defectos que encontró la verificación

1. **La sonda estaba rota antes que la página.** `scrollcraft.css` pone
   `scroll-behavior: smooth` en `<html>`, así que las primeras mediciones
   se tomaron en pleno vuelo del scroll y describían una página que no
   existía. Toda medición de esta página se hace con scroll instantáneo.
   El primer diagnóstico que salió de esa sonda, que `--sc-p` se saturaba
   antes de tiempo, **era falso**: medido bien, `--sc-p` recorre 0 a 0,9997
   sin problema.
2. **Las secciones `flow` no llevaban `data-sc-act="flow"`.** Sin ese
   atributo el motor no las registra y **`data-sc-drift` no hace nada**:
   el fondo se quedaba negro durante todo el acto 4, con tinta oscura
   encima. Texto invisible. La plantilla sí trae el atributo; se había
   omitido al escribir.
3. **El término que crece se montaba sobre el paréntesis de cierre.**
   `transform: scale()` no reflúa el layout. Cambiado a `font-size`, que
   empuja lo que sigue.
4. **El wordmark fijo se montaba sobre el texto de cada acto.** Corregido
   por gramática, no por parche: en poster el wordmark va dentro de la
   composición y puede no haber navegación persistente. Vive en el acto 1
   y en el pie.
5. **El silencio entraba y salía cortado por arriba.** Ahora se sostiene
   centrado en el viewport, y el acto pasó de 1,2 a 1,5vh para darle el
   recorrido que necesita.

## Lo que queda pendiente

- **El formulario no tiene destino.** No envía a ninguna parte y lo dice
  en pantalla al enviarlo, en vez de simular un envío que no ocurre.
- **`playwright-core` no se instaló** en la carpeta de build. La
  verificación se hizo con el Playwright del harness, no con
  `scripts/shoot.mjs` del skill.
- **`FINGERPRINTS.md` sigue vacío.** La fila se anota cuando la página
  salga, que es lo que manda el registro.
- **`auditor-de-procedencia` todavía no ha corrido.** Es la compuerta y no
  es opcional.

---

# Compuerta de procedencia — 2026-08-24

`auditor-de-procedencia` corrió sobre `index.html` completo. Veredicto:
**BLOQUEADA**. Cinco bloqueantes, dos de ellos por contradecir correcciones
fechadas del propio `05-GTM-KERN.md`.

## El error de fondo, y por qué se cometió

La página decía **«El neto baja y el servicio sube»**. Es cita textual de
`05-GTM-KERN.md:342`, de la tabla de objeciones. **Pero esa línea está
superada por una corrección fechada el 2026-08-07 en el mismo archivo**
(líneas 237-272), que se corrió de verdad contra
`webapp/demo_gap_value.py`:

> σLT solo suma varianza. Así que `SS_real > SS_erp` siempre, salvo que el
> plazo declarado esté materialmente inflado. Sobre el portafolio de
> ejemplo: 6 de 8 SKU piden más buffer, 2 liberan caja. **Neto: +7.882. El
> inventario sube.**

Y `05-GTM-KERN.md:601`: *«El "neto baja" viene de las fuentes #2 y #5, que
no salen de los dos CSV. El discurso correcto es riesgo cuantificado, no
ahorro.»*

Lo que lo agravaba: **toda la página es la fuente #1**, y el cierre pide
**exactamente los dos CSV**. Prometía un resultado desde el entregable que
la fuente dice que no puede producirlo.

**La lección, que vale más que el arreglo: citar textual de un documento no
es verificar.** Hay que comprobar si algo posterior en el mismo archivo lo
corrige. Este repo corrige por recuadro fechado, no borrando la línea
vieja, así que la línea vieja sigue ahí lista para ser citada. Ver
[[verificar-antes-de-afirmar]].

## Los cinco bloqueantes y qué se hizo

| # | Hallazgo | Severidad | Resolución |
|---|---|---|---|
| 1 | «El neto baja y el servicio sube» | CRÍTICO | Acto 4 reescrito a riesgo cuantificado. Decisión del fundador el 2026-08-24: alinear a la corrección |
| 2 | «el término que nadie mide» | CRÍTICO | Superlativo de industria. `:603` lo matiza: Netstock lo tiene en su marketing hace años. Reescrito a «un término que tu ERP no tiene» |
| 3 | «Sube en 47 SKU. Baja en 12» sin rótulo | ALTO | Era el único acto con cifras sin `k-src`. Cifras reemplazadas por las medidas (6 de 8, 2) y rótulo agregado |
| 4 | «te muestro los SKU con nombre y monto» | ALTO | `§7:538`: sin `unit_cost` no hay cifra de plata. Condicionado en pantalla |
| 5 | El formulario no tiene destino | CRÍTICO | **Sigue abierto.** Bloquea publicar, no bloquea seguir trabajando |

## Correcciones al margen, aplicadas

- **El «96%» estaba mal encuadrado.** Es literal en `§5:204`, pero la
  aritmética dice que es 96% **de la varianza** (12.100/12.644 = 95,7%), no
  del buffer: como fracción del buffer son ~79%. Y la página muestra
  `12.100` y la fórmula **en la misma pantalla**, así que era el único
  número refutable sin salir de la página. Ahora dice «el 96% de la
  varianza».
- **Se quitaron las citas `05-GTM-KERN.md §4` y `§5` de la página.** Una
  cita que apunta a un documento que el visitante no puede abrir no es una
  cita, y expone nomenclatura interna.
- **«no escribo nada» junto a un campo de correo** se leía como «no guardo
  nada». Ahora dice «sin escribir nada en tu ERP».
- El diferenciador real, de `:603`, entró al `meta description` y al
  cierre: no es medir la varianza, es **mostrarla gratis sobre los datos
  del visitante en 30 minutos sin hablar con un vendedor**.

## Lo que pasó limpio

Cero fuga C2 — el auditor corrió el detector de riesgo de identificación
sobre el HTML y devolvió `[]`. Precio exacto contra `§4:110-114`. Cero
contadores, logos o testimonios. GSM, DDMRP y Monte-Carlo ausentes.
«Reducir» ausente del copy. Voz correcta contra `voice-card.md`. Toda la
aritmética de §5 verifica exacto, y el JS reproduce los dos extremos.

## Lo que NO se pudo verificar, y sigue sin verificarse

**Ninguna afirmación de capacidad.** El auditor buscó el clon de kern en
`C:/Users/Gamer/Music/scm/kern-main` y **no existe**; `scm/` está vacío.
No se pudo contrastar contra código: «en 30 minutos», «sin escribir nada
en tu ERP», «los SKU con nombre», ni la lectura en solo lectura. Se aplicó
abstención en vez de asumir, citando que la capacidad sale del código y
que `§9:598-600` registra dos veces que esta doc ya se equivocó sobre lo
construido.

**Mientras el clon no aparezca, la página no puede acreditar lo que
promete que Kern hace.**

---

# Publicada — 2026-08-24

**https://kern-landing.fly.dev/**

App **separada** de `linchpin` a propósito: ahí viven el motor de Kern y la
webapp, y ese día había trabajo sin commitear de otra sesión en
`.wt-web-kern`. Una landing no comparte máquina con el servicio que le
responde cálculos a los clientes.

| | |
|---|---|
| App | `kern-landing`, org `personal` |
| Región | `gru` (São Paulo) |
| Imagen | `nginx:1.27-alpine`, 18 MB, tres archivos estáticos |
| Máquinas | 2, `shared-cpu-1x` 256mb, `auto_stop_machines = "stop"` |

**Santiago (`scl`) no se pudo usar:** Fly la deprecó y rechaza recursos
nuevos. Es la región natural para un mercado chileno y no está disponible.

## Precios retirados por decisión del fundador

El 2026-08-24, con la página ya desplegada, el fundador ordenó anular los
precios: están en revisión, y la superficie de kern
(`webapp/paquetes_page.py:240`) publica **$79** contra los **290 USD/mes**
de `05-GTM-KERN.md` §4. Publicar un precio en disputa es peor que no
publicar ninguno.

Se retiraron 290 USD/mes y la puesta en marcha. **Se mantuvo «el
diagnóstico es gratis»**, que no es un precio en revisión sino la oferta
sobre la que está construida la página entera: sin eso no hay llamado a la
acción. La fila dice ahora «Kern operando · Por publicar».

Verificado contra la URL pública: `290`, `USD`, `Puesta en marcha` y `sin
tramos` no aparecen en el HTML servido.

## Un defecto que solo apareció al publicar

`nadie mide` seguía en el HTML servido después del primer despliegue,
dentro de un **comentario CSS** del primer borrador. No se veía en
pantalla — el `innerText` estaba limpio, por eso el chequeo en el
navegador no lo detectó — pero viajaba en el fuente de una página pública
repitiendo el superlativo que el auditor había bloqueado. Corregido y
redesplegado.

**La lección: grepear el DOM renderizado no alcanza. Lo que se publica es
el archivo, comentarios incluidos.**

## Lo que sigue abierto, con la página ya en línea

1. **El formulario no tiene destino.** El único llamado a la acción no
   envía nada. Lo dice en pantalla al enviarlo, pero es una promesa que la
   página no puede cumplir.
2. **Ninguna afirmación de capacidad está verificada contra código.** El
   clon de kern sí existe, en
   `C:/Users/Gamer/Music/scm/supply-chain-optimization`; el auditor lo
   buscó en `scm/kern-main` y concluyó mal que no estaba. La verificación
   se puede hacer y no se ha hecho.
3. **Kern tiene dos landings.** Ésta y `webapp/static/index.html`. No se
   decidió cuál manda.
4. **`FINGERPRINTS.md` sigue vacío.** La fila se anota ahora que la página
   salió.
5. Nada está commiteado: `scrollcraft/` sigue sin seguimiento.

---

# El formulario, conectado — 2026-08-25

Deja de estar muerto. Va por **POST de formulario clásico** a
`https://linchpin.fly.dev/api/leads`, con `email` y un `source` oculto en
`kern-landing`. Los dos campos son los que declara `api_leads` en
`webapp/app.py:697` como `Form(...)`.

## Por qué form POST y no fetch

Medido contra el servicio en vivo, no supuesto:

- El secreto `LINCHPIN_CORS_ORIGINS` **no existe** en linchpin, así que
  `security.CORS_ORIGINS` queda vacía.
- El preflight `OPTIONS` desde `https://kern-landing.fly.dev` devuelve
  **405 sin `Access-Control-Allow-Origin`**.
- La CSP de linchpin declara `connect-src 'self'`.

Un `fetch` entre orígenes falla. Una navegación de formulario no pasa por
CORS, así que funciona sin tocar el backend ni redesplegar el motor.

**Descartado el iframe oculto**, que habría evitado que el visitante
aterrice en el JSON: linchpin manda `x-frame-options: DENY`, así que la
respuesta no se renderiza en el marco y el evento de carga no es señal
fiable de que el lead entró. Mostrar «listo» sin saberlo es mentir.

## Cómo se verificó sin ensuciar el ledger

El ledger de leads es PII y, según el propio `fly.toml`, «la única prueba
de que alguien usó el embudo». Meterle una fila de prueba lo contamina.

Se probó mandando un **correo inválido**: `api_leads` valida contra
`EMAIL_RE` y levanta 400 **antes** del `LEADS_FILE.open("a")`.

```
POST /api/leads  email=no-es-un-correo&source=kern-landing
→ 400 {"detail":"invalid email"}
```

Eso acredita que el endpoint acepta `x-www-form-urlencoded`, que ligó los
**dos** campos (un `source` no reconocido daría 422, no 400), y que llegó a
la validación. Sin escribir nada.

## Lo que queda feo, y su arreglo

Al enviar, el navegador **aterriza en linchpin mostrando `{"ok":true}`
crudo**. Es el precio de no tocar el backend. Se arregla de dos formas, las
dos fuera del alcance de hoy:

1. Que `api_leads` devuelva un redirect en vez de JSON cuando el `Accept`
   sea HTML. Es un cambio en `webapp/app.py`, que la otra sesión tiene
   modificado.
2. La migración a kern-landing que el fundador reafirmó: con todo en el
   mismo origen, el problema desaparece junto con el CORS.

---

# Cambio de gramática — 2026-08-25

**Decisión del fundador:** la página pasa a ser ilustrada y con movimiento.
Eso **no se puede hacer dentro de `typographic poster`**: esa gramática
prohíbe el movimiento decorativo y manda que la tipografía haga todo el
trabajo. Cambiar de registro obliga a cambiar de gramática, así que este
es un build nuevo, no un retoque.

## Nueva gramática: `live surface` (uniqueness.md §2.3)

> «La página se comporta como el producto. No una captura de él, ni un
> falso hecho con divs: la superficie real, corriendo, con el scroll
> manejando su estado.»

Elegida porque Kern es una herramienta y el demo es el argumento. Y
porque su regla de honestidad se puede cumplir de verdad acá:

> «La superficie tiene que ser markup real corriendo lógica real sobre
> datos reales o claramente rotulados como muestra… Si los paneles no
> pueden calcular, la gramática no está disponible. Elegí otra.»

Los paneles calculan en el navegador: la clasificación de E&O
(`src/excess_obsolete.py`) y la fórmula de stock de seguridad son
aritmética. **Nada está pintado.** El escenario se rotula como muestra en
la propia página.

**Prohibiciones que se respetan:** sin cromo de marketing, sin barra de
wordmark más CTA, sin scrims, sin fotografía a sangre, sin pilas de
titulares `kinetic`, sin `scrub`, sin `spotlight`, sin `drift` más allá de
dos paradas. Un encabezado en 6rem rompe esta gramática al instante.

**Lo que manda en cambio:** el cromo de la app **es** la navegación. El
hero es la superficie ya en un estado, no un título. El cierre es **un
input de verdad**, no un botón magnético — que ya era el cierre del build
anterior y sobrevive intacto.

## Qué sobrevive del build 1 y qué muere

| | |
|---|---|
| **Sobrevive** | El signature move: el scroll mueve σLT y la fórmula se recalcula. Ahora es un panel del producto en vez de un momento tipográfico |
| **Sobrevive** | La curva de seis emociones y el pico en el acto 3 |
| **Sobrevive** | Las siete reglas duras de procedencia, sin excepción |
| **Muere** | La escala tipográfica extrema. `live surface` la prohíbe |
| **Muere** | El wordmark en composición. Lo reemplaza el cromo de app |
| **Muere** | El silencio tipográfico del acto 2. Pasa a ser una línea de estado, que es el idioma de la superficie |

## Fila de fingerprint, build 2

El build 1 salió publicado en `kern-landing.fly.dev` y queda **superado**
por este. Se anotan los dos, porque el registro es la memoria de lo que ya
se hizo y borrar el primero sería mentirle al gate.

| # | Dimensión | Build 1 (superado) | Build 2 |
|---|---|---|---|
| 1 | Grammar | Typographic poster | **Live surface** |
| 2 | Nav | Wordmark en composición | **Cromo de app: barra de estado y pestañas reales** |
| 3 | Hero device | `kinetic`, el número del ERP a escala extrema | **La superficie ya cargada, poblándose fila por fila** |
| 4 | Forma | 6 actos, 10,2vh, pico al 32% | **6 actos, el estado avanza dentro de un marco fijo** |
| 5 | Cierre | Tipo más chico del sitio, campo con cursor | **Primer paso de uso: el mismo campo, ahora dentro de la superficie** |
| 6 | Signature | σLT mueve el buffer de 30 a 186 | **Igual, pero como panel que recalcula, no como tipografía** |

Difieren en 5 de 6 dimensiones. El gate pide 4. Pasa.

## El sistema visual es compartido con el blog

Los iconos (caja, pallet, estantería, camión, gráfico, reloj) y el
vocabulario de figuras salen de `webapp/blog_figures.py` del repo de kern.
Se generan desde ahí y se pegan como SVG en el estático, para que **una
sola fuente autore la identidad** de las dos superficies y no se
bifurquen.

## Build 2 publicado — 2026-08-25

`https://kern-landing.fly.dev/` · 28,1 KB · misma app de Fly, `gru`.

Seis paneles, y **todos calculan**: la clasificación corre las reglas de
`src/excess_obsolete.py` sobre cinco SKU de muestra, la ruta ubica el
plazo declarado contra la banda observada, y el panel de política
recalcula la fórmula cuadro a cuadro. Nada está pintado. El escenario se
declara como muestra en el cromo (`muestra.csv · solo lectura`) y en el
pie.

**El camión, que era el pedido, quedó dentro del idioma del producto:**
viaja de los 21 días que declara el ERP a los 34 observados. Es el
hallazgo, moviéndose, no un adorno de logística.

**Un defecto que la verificación encontró y se corrigió:** el acto 4 decía
«2 SKU piden más, 1 libera caja», y el panel calculaba 1 muerto, 2
excedidos y 2 sanos. La copia describía datos que no existían. Ahora dice
lo que el panel muestra: el excedido pesa más que el muerto, y el excedido
todavía rota.

El aterrizaje también arrancaba a medias, con el panel del plazo detrás de
un cue al 20%. En `live surface` el hero es la superficie ya en un estado,
así que los dos paneles saludan y sostienen.
