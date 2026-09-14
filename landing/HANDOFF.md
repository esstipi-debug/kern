# Handoff — landing de Kern y blog GEO

Escrito el **2026-08-25**. Todo lo de aquí está verificado contra el repo o
contra la URL en vivo el mismo día. Lo que es estimación va marcado.

---

## 1. Lo primero: qué está en línea y qué no

```
https://kern-landing.fly.dev/      200 · 28.149 b   ← EN VIVO
https://linchpin.fly.dev/blog      404              ← espera merge del #283
https://linchpin.fly.dev/llms.txt  404
```

**Los 11 artículos del blog no los ve nadie.** Están escritos, corregidos y
con 211 tests pasando, pero viven como `.md` en una rama. Hasta que el
#283 mergee y linchpin se redespliegue, no existen para Google ni para
Perplexity.

---

## 2. Dónde está cada cosa

| Repo | Ruta local | Rama | Estado |
|---|---|---|---|
| kern (landing) | `C:/Users/Gamer/Music/scm/.wt-landing` | `claude/kern-landing` | empujada · **PR #288** → `main` |
| kern (blog) | `C:/Users/Gamer/Music/scm/.wt-blog-excess-obsolete` | `claude/blog-excess-obsolete` | empujada · **PR #283** → rama del #279 |
| kern (clon principal) | `C:/Users/Gamer/Music/scm/supply-chain-optimization` | compartido | **no cambiarle la rama**, otras sesiones lo usan |
| nosilo | `C:/Users/Gamer/Music/nosilo/.claude/worktrees/kern-motors-integration-6c1c6d` | `claude/scroll-craft-page-d57cb8` | 1 commit **local**, sin push |

`.wt-web-kern` es de **otra sesión**: tenía 14 archivos sin commitear,
entre ellos `webapp/app.py` y `webapp/static/demo/index.html`. **El #283
va a chocar con eso.**

---

## 3. Assets: lo que ya se probo, y lo que costo

**kie.ai quedo descartado en la practica**: nunca se puso la clave. En su
lugar se uso **OpenArt**, que ya esta conectado como MCP a la sesion.

### Estado de la cuenta, medido el 2026-08-26

```
plan: Free · saldo: 10 creditos
```

Se gastaron 30 de los 40 iniciales en dos pruebas con el MISMO prompt:

| Modelo | Creditos | Resultado |
|---|---|---|
| Kling 3 Omni · 16:9 1k | 10 | Bodega de catalogo. Todo alineado, sin profundidad de campo |
| **Nano Banana 2** · 16:9 1K | 20 | **Gana.** Carton reusado, etiquetas descoloridas, poste golpeado, fondo que se disuelve |

**Nano Banana 2 es el modelo a usar.** La primera eleccion fue Kling por
ser el mas barato, y fue optimizar el numero equivocado.

### Precios verificados (OpenArt, 2026-08-26)

| | Creditos |
|---|---|
| Kling 3 Omni imagen | 10 |
| Nano Banana 2 Lite / Seedream 4.5 | 15 |
| **Nano Banana 2** (1K, cualquier aspecto) | **20** |
| Nano Banana Pro / GPT Image 2 | 40 |
| **PixVerse V6 video** 540p 5s | **50** (el mas barato que existe) |
| Wan 2.7 video 720p 5s | 125 |

**Con 10 creditos no alcanza para nada util.** Ni una Nano Banana (20) ni
un video (50). Hay que recargar antes de seguir generando.

Plan sugerido si se recarga: **60 creditos** = 3 stills mas en Nano Banana
2 (recepcion, detalle de bins, cierre). **110** = eso mas un clip en
PixVerse por `image2video`, que cuesta lo mismo que `text2video` y arranca
de una foto ya aprobada.

### Reglas que se aplicaron al prompt, y hay que mantener

Preambulo de estilo unico reusado textual, fotografia documental (el
diorama de plastilina esta prohibido por defecto), y tres negativos que no
se negocian: **sin personas, sin texto legible, sin logos**. `taste.md`
prohibe el texto horneado dentro de la imagen.

El prompt exacto que funciono esta en el commit de `landing/assets`.

---

## 3b. La landing ahora tiene foto

`landing/assets/bodega.webp` (161 KB) y `bodega-m.webp` (59 KB), detras de
los paneles del acto de apertura, con `<picture>` y corte en 760px.

El movimiento sale de `--sc-p` heredado: escala de 1.06 a 1.15 y sube 3%.
**Cero JavaScript**, y con `prefers-reduced-motion` queda fija.

**Dos rompimientos deliberados** de `live surface`, que prohibe la
fotografia a sangre y los scrims. La foto va SOLO en el acto de apertura;
el resto sigue siendo instrumento oscuro. Y el degradado pesa arriba,
donde estan los paneles, abriendose en el medio para que el pasillo se
vea: la primera version lo tenia al reves y ahogaba la parte de la foto
que vale.

**ESTA COMMITEADO PERO NO DESPLEGADO.** Lo que hay en vivo todavia no
tiene foto. Para publicarlo:

```bash
cd C:/Users/Gamer/Music/scm/.wt-landing/landing
flyctl deploy --now
```

---

## 3c. Si se vuelve a kie.ai

Es la via que trae el skill de scrollcraft. Se descarto en la practica
porque nunca se puso la clave, pero es la unica que produce clips
scrubbeables cuadro a cuadro, que es lo que exigen las gramaticas del
espectaculo. Para retomarla:

```bash
setx KIE_AI_API_KEY "la-clave"      # o un .env en la carpeta del skill
```

Comprobado el 2026-08-25: la variable **no está puesta** y no hay `.env`.
`node scripts/kie.mjs probe` falla con `KIE_AI_API_KEY not set`.

**No le pidas al fundador que pegue la clave en el chat.** Que la ponga él
en el entorno; el script la lee solo.

### Cuando la clave exista

El skill está clonado en el scratchpad de la sesión anterior, que puede
haberse borrado. Si no está:

```bash
git clone https://github.com/nateherkai/scroll-craft.git
# el skill vive en plugins/nateherk-design/skills/scrollcraft
```

Orden obligatorio, del propio `references/assets.md`:

```bash
node scripts/kie.mjs probe                                    # créditos ANTES
node scripts/kie.mjs still "<preámbulo> + <escena>" out/01.png --ar 16:9
node scripts/kie.mjs shot  "<movimiento>" out/01.png out/01.mp4 --dur 5
bash  scripts/encode.sh out/01.mp4 assets/01.mp4              # GOP denso
bash  scripts/encode.sh out/01.mp4 assets/01-m.mp4 mobile
ffmpeg -y -i out/01.mp4 -frames:v 1 -q:v 2 assets/01-poster.png
```

### Trampas ya pagadas, no las repitas

- **`--ar 4:5` lo rechaza seedream** y el error no dice cuál es el problema.
  Verificados: `16:9` (2736x1520), `9:16`, `3:4`.
- **El póster de un clip tiene que ser su propio primer cuadro**, sacado con
  ffmpeg. Generar uno parecido produce un salto visible al pintar el video.
- **Un preámbulo de estilo, reusado textual en cada prompt.** Es lo que hace
  que seis imágenes parezcan una sesión de fotos. Escríbelo una vez y no lo
  parafrasees.
- **Costos publicados:** 28 créditos por still, 160 por clip de 5s. Una
  página de seis actos: 2 clips + 4 a 6 stills ≈ **520 créditos**. Es la
  cifra de la doc, no una medición.
- **Prohibido por `taste.md`:** dioramas de plastilina, dashboards falsos
  hechos con divs, terminales falsas, texto horneado dentro de la imagen.

---

## 4. La landing: tres builds, y por qué

| Build | Gramática | Dónde está |
|---|---|---|
| 1 | `typographic poster` | **Solo en Fly, release v4.** Nunca se versionó |
| 2 | `live surface` | `landing/index.html`, en vivo, releases v5 y v6 |
| 2b | `live surface` + suelo fotografico | `landing/index.html`, **commiteado sin desplegar** |
| 3 | por hacer | necesita clips de video, o sea recargar creditos |

**Build 1 no está en git.** Se desplegó sin versionarse. Si lo quieres de
vuelta: `flyctl releases -a kern-landing` y rollback a **v4**.

**Por qué cambió a build 2:** el fundador pidió movimiento, y
`typographic poster` prohíbe el movimiento decorativo. Cambiar de registro
obliga a cambiar de gramática.

**Por qué build 2 no lo dejó conforme:** dijo *"mataste la página, hiciste
algo ultra básico"*. `live surface` es la gramática más sobria del
catálogo — prohíbe cromo de marketing y titulares grandes. Fue mal criterio
elegirla cuando lo que pedía era espectáculo. **Las gramáticas del
espectáculo (`continuous world`, `filmic one-shot`) no funcionan sin
assets.** Ya hay UNA imagen buena (§3b), que alcanzo para darle suelo
fotografico al acto de apertura pero no para una de esas gramaticas:
esas piden clips de video, y el saldo no da.

`landing/BRIEF.md` (638 líneas) tiene la entrevista completa, el score, las
dos filas de fingerprint y la auditoría. **Léelo antes de tocar la página.**

---

## 5. Reglas duras que NO se negocian

Salen de `documentation/KERN_BRAND_PRODUCT_BIBLE.md` §10 y de la auditoría
de procedencia. Un agente `auditor-de-procedencia` las hace cumplir.

1. **Cero contadores sobre Kern**: tracción, clientes, ahorro logrado,
   adopción. No existen.
2. **Toda cifra ilustrativa lleva su rótulo en pantalla**, al mismo tiempo
   que la cifra. No en el pie.
3. **Cero logos, testimonios y casos de éxito.**
4. **Nunca "reducir inventario"**: operaciones lo escucha como *"querés que
   me quiebre"*. Se dice **mover**.
5. **Precios RETIRADOS** hasta nuevo aviso. `webapp/paquetes_page.py:240`
   publica **$79** con una calculadora de ROI encima; `05-GTM-KERN.md` §4
   dice **290 USD/mes**. **Dos superficies del mismo negocio dicen números
   distintos y nadie lo ha resuelto.**
6. **La única redacción permitida sobre desenlaces**: "de los cuatro
   desenlaces posibles, tres se detienen en un humano por diseño". Nunca
   una frecuencia medida.
7. **No liderar con la matemática.** GSM, DDMRP, Monte-Carlo van abajo.

### La trampa que ya costó cara

`05-GTM-KERN.md` **corrige por recuadro fechado sin borrar la línea vieja**.
La línea vieja se queda ahí, lista para ser citada. Pasó: se citó
*"el neto baja y el servicio sube"* de la tabla de objeciones, y una
corrección del 2026-08-07 en el mismo archivo dice lo contrario —
**σLT solo suma varianza, así que el inventario SUBE** (+7.882 sobre el
portafolio de ejemplo). El discurso correcto es **riesgo cuantificado, no
ahorro**.

**Citar textual no es verificar. Hay que buscar si algo posterior lo corrige.**

---

## 6. El blog (#283 y #279, YA MERGEADOS)

11 markdown en `documentation/blog/{es,en}/` + renderer SSR + figuras SVG.
**211 tests pasan, sin regresiones.**

Lo que hay que saber para revisarlo:

- **La primera pasada de verificación no aprobó ningún par.** 11
  bloqueantes. Cuatro eran afirmaciones que contradecían el código citado,
  incluida una donde el artículo **invertía** el comportamiento del branch
  que describía.
- El más profundo: **hay dos fórmulas de stock de seguridad en el repo y
  solo una gobierna.** `Ss = z·σ_x` desde `safety_stock_risk_period` en
  `src/demand_variability.py`. La 4.3 de Vandeput solo coincide mientras
  `σ_L` sea cero.
- Los cuatro archivos en inglés eran **calcos frase por frase** y se
  reescribieron desde cero.
- **Ningún artículo cita libro**: `query_knowledge.py --search` sobre E&O
  devolvió solo nodos de código. `WRITER.md` manda soltar la cita antes que
  inventar el libro.

### Figuras

Se autoran en el markdown como una valla ` ```kern-figure ` con JSON.
**El campo `label` es obligatorio y se valida en código**: un gráfico es el
lugar más fácil de colar una cifra sin rótulo.

Faltan 5 sesiones de escritura más si se quiere ampliar el catálogo, pero
el v1 de 6 pares está cerrado. **No agregar un 7º slug** sin nombrar una
query distinta.

---

## 7. Decisiones abiertas, del fundador

1. **El precio.** $79 contra 290 USD/mes. Bloquea publicar cualquier precio.
2. **Dominio propio.** No hay ninguno; `flyctl certs list` vacío en las dos
   apps. **No bloquea nada** — el blog no lleva `canonical` en el
   frontmatter, el renderer lo arma. Solo importa para que la autoridad SEO
   se acumule en un ápex propio en vez de en `fly.dev`.
3. **Mover linchpin a kern-landing.** El fundador lo reafirmó. Implica
   migrar un volumen cifrado de 1 GB de `iad` a `gru` con **PII y la traza
   de autonomía T1/T2/T3 adentro**. No es mover archivos.
4. **`landing/shots/` y las capturas.** Ya ignoradas en los dos repos.

---

## 8. Cosas que rompen si no las sabes

- **`scroll-behavior: smooth`** viene en `scrollcraft.css`. Cualquier
  medición con `window.scrollTo` sin `behavior:'instant'` mide la página en
  pleno vuelo y **miente**. Ya produjo un diagnóstico falso completo.
- **`data-sc-drift` no hace nada sin `data-sc-act`.** Una sección `flow`
  sin ese atributo no se registra y el fondo se queda en el color anterior.
- **`--sc-p` se abre cuando el acto entra por abajo**, no cuando se fija.
  Para animar contra el marco fijado, medí `rect.top` contra
  `offsetHeight - innerHeight`.
- **`region scl` (Santiago) está deprecada en Fly.** `kern-landing` corre en
  `gru`.
- **CORS de linchpin vacío**: no existe `LINCHPIN_CORS_ORIGINS` y la CSP
  declara `connect-src 'self'`. Un `fetch` entre orígenes falla; por eso el
  formulario de la landing es un POST clásico.
- **Grepear el DOM renderizado no alcanza.** Una frase prohibida sobrevivió
  en un comentario CSS y viajó a producción. Lo que se publica es el
  archivo, comentarios incluidos.
- **El clon de kern se llama `supply-chain-optimization`**, no `kern-main`.
  Un auditor lo buscó por el nombre equivocado y se abstuvo de verificar
  todas las capacidades.

---

## 9. Lo que yo haría primero

1. **Pedir la clave de kie.ai** y correr `probe`. Sin eso el build 3 no
   arranca.
2. **Resolver el precio.** Hoy tus dos superficies públicas dirían cifras
   distintas.
3. **Desplegar linchpin.** Los #283 y #279 ya estan en `main`, pero este
   repo NO tiene workflow de despliegue: `linchpin.fly.dev/blog` sigue
   devolviendo 404 hasta que alguien corra `flyctl deploy -a linchpin`
   desde la raiz. Ese deploy toca el motor en produccion, que monta un
   volumen con PII y la traza de autonomia: se hace a ojos abiertos.
4. **Desplegar la landing con foto** (§3b), que esta commiteada sin salir.
