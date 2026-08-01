# KERN — Kit de Producción · "El planificador de las 3am"

> **Film de marca (Familia A #3)** · del caos del Excel a las 3am → a la calma del ciclo gobernado con firma humana.
> Fuente creativa: `KERN_VIDEO_PLAYBOOK.md` (H3 "3. El planificador de las 3am"). Fuente de marca/producto y compliance: `KERN_BRAND_PRODUCT_BIBLE.md` (§10 claims prohibidos manda).
> Versión 1.0 · 24-jul-2026 · Español LATAM (voseo neutral) · Kern 2.9.0
>
> **Regla dura (inviolable):** cada claim es auditable contra código o fuente citada, o no se dice. Nada de "IA mágica/última generación", "% de autonomía", "autonomía total/sin humano", "ahorra X horas" como dato duro, casos de éxito de clientes reales, integración Shopify/Amazon o inventario de Mercado Libre, ni Control Tower autónomo como producto de hoy. **El Acto 2 muestra siempre firma humana + HANDOFF: Kern prepara, el humano firma; nunca ejecuta el ciclo solo.** Toda cifra de maqueta va rotulada `ej.` en pantalla.

---

## 1. Ficha técnica

| Campo | Especificación |
|---|---|
| **Título** | El planificador de las 3am |
| **Duración master** | **100 s exactos** (1:40:00), 16 planos, dos actos (0–24 s caos · 24–100 s calma) |
| **Formato** | Film de marca cinematográfico, "un día en la operación", híbrido live-action + inserts de interfaz |
| **Aspecto master** | **16:9** (1920×1080 entrega HD / 3840×2160 master 4K), gráficas y encuadre nativos horizontales |
| **Derivados** | **9:16** (1080×1920) reel de 30 s · **1:1** (1080×1080) ad de 15 s — ver §7. Reencuadre protegido: sujeto y cifras dentro del centro seguro |
| **fps** | **24 fps** master (look cinematográfico, obturador 180°/1° = 1/48). Derivados sociales entregar a **30 fps** (conform desde 24) |
| **Gamma / color** | Rodaje Log, grade a Rec.709 (entrega social) + P3 (master). Acto 1 desaturado y frío; Acto 2 cálido y limpio |
| **Grano** | Grano tenue **solo en Acto 1** (35 mm fino, ~ISO 800 look). Acto 2 limpio, sin grano |

### 1.1 Paleta (color = estado, no decoración — §11)

Autoritativo el **oklch** de la biblia; el hex es aproximación para herramientas que no toman oklch (derivar siempre del oklch).

| Rol | oklch (light) | oklch (dark) | hex aprox. |
|---|---|---|---|
| Superficie | `98% 0 0` | `20% 0.01 260` | `#FAFAFA` / `#14161E` |
| Texto | `20% 0 0` | `94% 0 0` | `#2B2B2B` / `#EDEDED` |
| **Acento azul** | `62% 0.17 250` | `70% 0.16 250` | `#3B72D4` / `#5B8CE6` |
| **OK / verde** | `64% 0.16 150` | `72% 0.16 150` | `#22A56C` / `#45C089` |
| **Warn / ámbar** | `72% 0.16 80` | `80% 0.15 85` | `#D2962D` / `#E5B04B` |
| **Riesgo / rojo** | `60% 0.20 25` | `68% 0.19 25` | `#CE3B34` / `#E15A4E` |
| **Teal de marca** (badge / latido del núcleo) | — | — | **`#5EEAD4`** (exacto) |

Regla: si un color no significa un estado, no va. **Cero "glow de IA", cero gradientes mágicos.** El teal aparece por primera vez en el corte a negro (0:18) y es el único color "emocional".

### 1.2 Tipografía

- **Títulos e interfaz:** `Inter` (400/600/700). Kinetic principal en Inter SemiBold.
- **Cifras, tablas, políticas, badges:** `IBM Plex Mono` con `tabular-nums` — números siempre alineados. La cifra monoespaciada es tan marca como el logo.
- Nunca cifra de producto en Inter; nunca copy emocional en Mono.

### 1.3 Firma sonora

- **Motivo "latido teal":** sub-bass heartbeat suave a **~60 BPM**, sincronizado al pulso teal. Es la firma sonora del film — abre el Acto 2 y cierra el film con un único latido.
- **Tick de cursor mono:** un clic seco y breve cada vez que una cifra `IBM Plex Mono` se "asienta".
- **Compuerta mecánica:** "thunk" grave con peso (QA-gate y writeback). Nunca whoosh épico.
- Acto 1: tic-tac de reloj, teclado, respiración corta. Acto 2: pad cálido minimal. **Nunca triunfalista** — la resolución es calma, no euforia.

---

## 2. Guion locutado final (VO)

Ritmo objetivo **~2.5 palabras/seg**. Toda locución en voseo neutral, registro bajo y preciso, **sin locución "vendedora"**. Los silencios respiran: la VO ocupa ~40 s de los 100 s; el resto es imagen, cifra y firma sonora.

> **Dirección global de voz:** una sola voz, íntima, cerca del micrófono. Acto 1 = cansada, resignada, casi para adentro. Acto 2 = despierta, serena, confiada sin subir el volumen. Cero énfasis publicitario. Dejá caer los finales de frase.

| # | Timecode | Copy VO (definitivo) | Palabras | Objetivo (s) | Tono / intención de lectura |
|---|---|---|---|---|---|
| VO1 | 0:00–0:09 | "3:07 de la mañana. La planilla otra vez no cierra." | 9 | 3.6 s de habla en 9 s | Cansada, resignada. Un suspiro antes de "otra vez". Volumen bajo. |
| VO2 | 0:09–0:18 | "Sin método real. Sin memoria de cuánto te equivocaste el mes pasado. Y si esta persona se va, se va también el criterio." | 23 | 9.2 s en 9 s | Tensión creciente, enumerativa. Cada "sin" pesa. Cierre seco en "criterio". |
| — | 0:18–0:24 | *(sin VO — solo texto en pantalla y latido teal)* | — | — | Silencio deliberado. El latido calma el pulso del montaje. |
| VO3 | 0:24–0:38 | "Mientras dormís, corre el método correcto. Simula antes de recomendar. Y se cita: dos fuentes, o no entrega." | 18 | 7.2 s en 14 s | Calma, clara. Punto y respiración entre cada afirmación. Sin triunfo. |
| — | 0:38–0:50 | *(sin VO — texto + SFX de compuerta)* | — | — | La imagen y el "thunk" hablan. VO en pausa. |
| VO4 | 0:50–1:04 | "A la mañana no encontrás un caos. Encontrás una decisión preparada, con su evidencia. Y vos firmás." | 17 | 6.8 s en 14 s | Cálida, aliviada. "Y vos firmás" dicho como un hecho tranquilo, no como venta. |
| VO5 | 1:04–1:18 | "Se aplica sobre tu Odoo, en staging, reversible. Nunca a ciegas." | 11 | 4.4 s en 14 s | Precisa, firme, técnica. "Nunca a ciegas" con punto final rotundo. |
| VO6 | 1:18–1:33 | "El humano vende y decide. Kern produce el análisis —el trabajo mecánico, no la decisión— y siempre muestra su fuente." | 20 | 8.0 s en 15 s | Reposada, de tesis. El inciso "—el trabajo mecánico, no la decisión—" leído más bajo, como aclaración honesta. |
| — | 1:33–1:40 | *(sin VO — tagline en pantalla; opcional un único susurro del tagline)* | — | — | Un beat. Cierre. |

**Total VO:** ~98 palabras habladas · ~39 s de locución en 100 s de film (densidad calma, deliberada).

**Opción de línea de tagline (si se decide locutar el cierre):** "Kern. El núcleo que tu cadena no tiene." — 8 palabras, susurrada, una sola vez, sobre el último latido.

---

## 3. Plan de texto en pantalla (kinetic)

Reglas: copy emocional en **Inter SemiBold**; toda cifra/badge en **IBM Plex Mono + tabular-nums**. Entradas y salidas en **opacity + transform** (nunca layout). Sin easing "de casino": `cubic-bezier(0.16, 1, 0.3, 1)` (ease-out-expo) para entradas, `ease-in` corto para salidas. Tamaño relativo expresado sobre alto de cuadro (vh) para portar a 9:16/1:1.

| # | Timecode | Frase / dato | Fuente · peso | Tamaño (rel.) | Color | Posición | Entrada → Salida | Timing |
|---|---|---|---|---|---|---|---|---|
| T1 | 0:02–0:07 | `03:07 AM` | IBM Plex Mono 600 | 9 vh | Texto dark `#EDEDED` sobre negro | Centro, sobre el celular | Fade-in 300 ms → corte | 5 s en pantalla |
| T2 | 0:10–0:16 | `#REF!` (x3, cascada) | IBM Plex Mono 700 | 5 vh | Riesgo rojo `#CE3B34` | Sobre celdas, escalonadas | Pop por tecla (scale 0.9→1, 120 ms c/u) → congela | Aparecen en cadena |
| T3 | 0:14–0:18 | `σ cruda — dispersión, no error  (ej.)` | IBM Plex Mono 500 | 3.2 vh | Ámbar warn `#D2962D` | Inferior, bajo la columna σ | Slide-up 8 px + fade 250 ms → fade-out | 4 s |
| T4 | 0:19–0:24 | "¿Y si el método corriera solo — pero nada se moviera sin vos?" | Inter 600 | 5.5 vh | Texto `#EDEDED`; "sin vos" en teal `#5EEAD4` | Centro, sobre negro | Type-on por cláusula, caret teal parpadeante → hold | 5 s |
| T5 | 0:25–0:31 | `03:07 AM` (repetido, ahora en calma) | IBM Plex Mono 600 | 6 vh | Texto `#EDEDED` tenue | Superior-izq, discreto | Fade-in 400 ms → persiste tenue | Ancla el "mismo reloj" |
| T6 | 0:31–0:38 | `SKU-0417 · reorden`  ·  `clasifica → corre → valida` | IBM Plex Mono 500 | 3 vh | Acento azul `#5B8CE6`; estados en verde al validar | Panel derecho, línea de estado | Cada etapa entra con tick de cursor (fade 150 ms) | Secuencial |
| T7 | 0:33–0:38 | `MAPE 18% → 11%  (ej.)` · `s=180 u · Q=420 u  (ej.)` | IBM Plex Mono 600 | 3.4 vh | Verde ok `#45C089`; badge `ej.` en ámbar | Panel, bloque de cifras | Conteo tabular al valor + tick → hold | Números "cuentan" |
| T8 | 0:40–0:50 | "QA veta. Un paso que falla ⇒ no se escribe archivo." | Inter 600 | 5.5 vh | Texto `#EDEDED`; "QA veta" en ámbar→verde | Centro-inferior | Entra con el "thunk" de compuerta (fade + 6 px) → fade-out | 10 s (dos beats) |
| T9 | 0:45–0:50 | `QA: ok · cita 2/25 fuentes` | IBM Plex Mono 500 | 2.8 vh | Verde ok `#45C089` | Junto al archivo escrito | Check secuencial, tick por ítem | Al pasar en verde |
| T10 | 0:58–1:04 | `HANDOFF · orden preparada · firma pendiente` | IBM Plex Mono 600 | 3.4 vh | Ámbar warn `#E5B04B` (espera) | Sobre la OC en el teléfono | Slide-up 8 px + fade 300 ms → hold | 6 s |
| T11 | 1:00–1:04 | "Una decisión preparada, con su evidencia. Vos firmás." | Inter 600 | 5 vh | Texto `#2B2B2B` (superficie clara) | Inferior | Fade 300 ms → hold | 4 s |
| T12 | 1:05–1:11 | `[ Aprobar ]` (botón) | Inter 600 | 3.6 vh | Acento azul `#3B72D4`, texto blanco | Centro, bajo la OC | Estado hover→press→ok; ripple sutil al tocar | Micro-interacción |
| T13 | 1:12–1:18 | `Changeset · dry-run · tier: reversible · aprobación 900 s`  →  `rollback ✓ · firma ✓` | IBM Plex Mono 500 | 3 vh | Azul→verde ok al confirmar | Inferior, banda de estado | Sella con "thunk"; ✓ en verde secuencial | 6 s |
| T14 | 1:26–1:33 | "El método no duerme. La decisión, la firmás vos." | Inter 600 | 5.5 vh | Texto `#2B2B2B`; "la firmás vos" en teal | Centro | Fade 400 ms → hold | 7 s |
| T15 | 1:34–1:40 | **Kern** · *el núcleo que tu cadena no tiene.* | Inter 700 (logo) + Inter 400 italic (tagline) | Logo 7 vh · tagline 3 vh | Texto `#2B2B2B`; núcleo teal `#5EEAD4` | Centro | Núcleo late una vez → logo fade-in 500 ms | 6 s, corte final |

**Frases kinéticas ancla (del guion fuente, textuales):**
- "3:07 AM. La planilla otra vez no cierra."
- "El método corriendo mientras dormís — sin que nadie decida saltárselo."
- "QA veta. Un paso que falla ⇒ no se escribe archivo."
- "Una decisión preparada, con su evidencia. Vos firmás."

---

## 4. Shot list detallada

> **Blindaje §10:** toda cifra de interfaz es maqueta del motor rotulada `ej.` — ejemplo ilustrativo, **no** resultado de un cliente real. Los nombres de campo (`excess_value`, `σ_e`, `(s,Q)`, `Changeset`, `rollback`) son reales del código; los valores, ejemplos.

**Leyenda:** Enc. = encuadre · Mov. = movimiento de cámara.

| # | TC | Dur | Enc. | Mov. cámara | Iluminación | Atrezzo / props | Qué se ve exactamente |
|---|---|---|---|---|---|---|---|
| **ACTO 1 — el caos del Excel a las 3am** |
| S01 | 0:00–0:04 | 4 s | Macro (celular en la mano) | Estática, respiración leve hand-held | Única fuente: pantalla del celular, frío ~5600 K; resto negro | Celular, funda gastada | Pantalla del celular en la oscuridad marcando `03:07 AM` (T1). Grano tenue. Nada más iluminado. |
| S02 | 0:04–0:09 | 5 s | PP rostro / detalle anteojos | Push-in muy lento | Luz de monitor sobre rostro agotado, azulada | Anteojos | Reflejado en el cristal de los anteojos: Excel con `#REF!` y **filas rojas**, min/max a ojo. Ojos cansados. |
| S03 | 0:09–0:14 | 5 s | Plano medio manos + teclado | Hand-held nervioso, cortes secos | Monitor frío, sombras duras | Teclado, mate frío, **post-its** "¿pedimos 200 o 500?" | Manos rehaciendo fórmulas, borrando y retipeando. Post-its pegados al borde del monitor. Tensión. |
| S04 | 0:14–0:18 | 4 s | Insert macro de pantalla | Estática con micro-jitter | Solo la pantalla | — | Columna `σ` mal calculada, rotulada `σ cruda — dispersión, no error (ej.)` (T3); una **cascada de `#REF!`** (T2) baja por la hoja. |
| S05 | 0:18–0:24 | 6 s | PG cuarto → corte a negro | La laptop se cierra en cuadro; corte duro a negro | Se apaga la única fuente; negro total | Laptop | La persona **cierra la laptop**. Corte a negro. En el centro aparece un **latido teal `#5EEAD4`** (motivo sonoro entra). Texto T4: "¿Y si el método corriera solo — pero nada se moviera sin vos?" |
| **ACTO 2 — la calma del ciclo gobernado** |
| S06 | 0:24–0:31 | 7 s | PG del dormitorio | Dolly lateral lentísimo | Azul nocturno suave, práctica tenue; **calma**, sin dureza | Cama, mesa de luz, reloj | **Mismo reloj: `03:07 AM`** (T5), pero la casa está a oscuras y **la persona duerme**. Nada titila. Aire quieto. |
| S07 | 0:31–0:38 | 7 s | Insert full-screen del panel Kern | Estática; las cifras entran solas | Interfaz superficie dark `#14161E`, data-dense | — | Panel calmo, `IBM Plex Mono` alineado: `SKU-0417 · reorden` corre `clasifica → corre → valida` (T6). Bloque de cifras `MAPE 18%→11% (ej.)`, `s=180 · Q=420 (ej.)`, `σ_e` (T7). Cifras alineadas, nada parpadea. |
| S08 | 0:38–0:44 | 6 s | Insert: la QA-gate | Push-in corto a la compuerta | Ámbar warn tiñe el paso que falla | — | Una **compuerta mecánica** baja sobre un paso que falla; el paso se marca **ámbar** `#D2962D`. Texto T8 (primer beat): "QA veta." "Thunk" grave. |
| S09 | 0:44–0:50 | 6 s | Insert: reintento → verde | Estática; el estado cambia | Ámbar → verde ok | — | El paso se **descarta y se reintenta** hasta pasar en **verde ok** `#45C089`; recién ahí **se escribe el archivo**. Badges `QA: ok · cita 2/25 fuentes` (T9). T8 (segundo beat): "Un paso que falla ⇒ no se escribe archivo." |
| S10 | 0:50–0:57 | 7 s | PP rostro que despierta | Push-in suave, se estabiliza | **Amanece**: luz cálida ~3200 K entra por la ventana, dorada | Sábanas, ventana | La luz de la mañana entra al cuarto. La persona **abre los ojos**, descansada. Cambio térmico total vs. Acto 1. |
| S11 | 0:57–1:04 | 7 s | Close del teléfono en la mano | Estática, apoyo cálido | Luz de mañana + pantalla cálida | Teléfono | En la pantalla: **entregable listo** (miniaturas `Excel · reporte · gráfico`) y **una orden de compra preparada** con badge `HANDOFF · orden preparada · firma pendiente` en **ámbar** (T10). Texto T11: "Una decisión preparada, con su evidencia. Vos firmás." |
| S12 | 1:04–1:11 | 7 s | Macro del dedo sobre el botón | Estática; micro-empuje al tocar | Cálida, foco en el botón | Teléfono | El **dedo toca `[ Aprobar ]`** (T12) — gesto humano, deliberado, sin apuro. Ripple azul sutil. La firma es un acto humano. |
| S13 | 1:11–1:18 | 7 s | Insert: writeback a Odoo | Estática; la banda de estado sella | Superficie dark→neutra; verde ok al final | — | `Changeset · dry-run · tier: reversible · aprobación 900 s` se aplica sobre **Odoo en staging**; sella con "thunk"; badge final `rollback ✓ · firma ✓` en **verde** (T13). Micro-interacción precisa. |
| S14 | 1:18–1:26 | 8 s | PG → zoom-out de la operación | Zoom-out suave y continuo | Neutra cálida, respiración visual | Depósito / oficina en calma | **Zoom-out:** la operación entera corriendo en calma — un organismo sano latiendo a ritmo constante. Sin incendios. |
| S15 | 1:26–1:33 | 7 s | PG conceptual: ritmo mensual | Continúa el zoom-out / transición | Empieza a aclarar hacia superficie clara | Calendario/ciclo sutil | El caos se lee ahora como **ciclo mensual gobernado**. Texto T14: "El método no duerme. La decisión, la firmás vos." |
| S16 | 1:33–1:40 | 7 s | Plano gráfico limpio | Estática | **Superficie clara** `#FAFAFA`, limpia, sin grano | — | Núcleo **teal** pequeño y firme late **una vez**; aparece **Kern · *el núcleo que tu cadena no tiene.*** (T15). Un beat. Corte final. |

**Total: 16 planos · 100 s.**

---

## 5. Prompts de generación IA por shot

> **Estética Kern (aplica a todos):** calmo, data-dense, cinematográfico, realista. Acto 1 = frío, desaturado, grano 35 mm tenue, luz de monitor. Acto 2 = cálido, limpio, luz natural de amanecer. **Sin "AI glow", sin gradientes neón, sin hologramas, sin robots.** Cifras en interfaz siempre en tipografía monoespaciada, alineadas.

### 5.0 Negative prompt maestro (concatenar a TODOS los shots)

```
neon glow, holographic UI, sci-fi HUD, glowing blue AI aura, futuristic robot,
android, cyborg, magic particles, lens flares, rainbow gradients, oversaturated,
3d render look, cartoon, plastic skin, distorted hands, extra fingers, warped text,
gibberish text, watermark, logo soup, busy motion graphics, epic lighting,
volumetric god rays, purple-teal cyberpunk palette, dashboard clutter
```

Para inserts de interfaz agregá además: `handwritten font, decorative typography, colored background gradients, drop shadows on text, skeuomorphism`.

---

### ACTO 1

**S01 — Celular 3:07 AM**
- **Video (Runway/Kling/Sora):** `Extreme close-up of a worn smartphone held in the dark, screen the only light source, cold 5600K glow on fingertips, display shows a clock reading 3:07 AM in a clean monospaced font, pitch black surroundings, subtle handheld breathing, faint fine film grain, cinematic, shallow depth of field, 24fps, moody and quiet.`
- **Imagen clave (Midjourney):** `macro photo of a smartphone screen showing 03:07 AM in monospaced type, held in the dark, cold screen glow on a fingertip, black background, faint film grain, cinematic, muted cool palette --ar 16:9 --style raw`
- **Negativos extra:** `bright room, colorful app icons, warm light`

**S02 — Reflejo de Excel en los anteojos**
- **Video:** `Slow push-in on a tired person's face lit only by a cold monitor, eyeglasses reflecting a spreadsheet full of red error cells and red rows, blue-cold light, exhausted eyes, faint grain, cinematic realism, minimal movement.`
- **Imagen clave (MJ):** `close-up of eyeglasses reflecting a spreadsheet with red error cells, tired face in cold monitor light, night, desaturated, cinematic, film grain --ar 16:9 --style raw`
- **Negativos extra:** `readable brand names, glowing screen halo`

**S03 — Manos rehaciendo fórmulas**
- **Video:** `Handheld medium shot of hands typing and deleting on a keyboard at night, sticky notes on the monitor edge, cold desk light, nervous quick energy, shallow focus, faint film grain, documentary realism, tense.`
- **Imagen clave (MJ):** `hands on a keyboard at 3am, sticky notes reading short handwritten questions on the monitor bezel, cold light, desaturated, tense workspace, film grain, cinematic --ar 16:9 --style raw`
- **Negativos extra:** `tidy desk, warm lamp, smiling person`

**S04 — Insert: σ mal calculada + cascada #REF!**
- **Video (motion graphics / screen-capture look):** `Screen-only insert: a dark spreadsheet where a red "#REF!" error cascades down a column, one cell after another, a monospaced amber caption fades in below a column labeled sigma, clean flat UI, no glow, calm precise motion, tabular numbers aligned.`
- **Imagen clave (MJ):** `flat minimal spreadsheet UI on dark background, a column of red #REF! errors cascading down, small amber monospaced caption, aligned tabular numbers, no gradients, clean data-dense design --ar 16:9`
- **Negativos extra:** `neon, 3d, glossy, decorative fonts`

**S05 — Cierra la laptop → negro → latido teal**
- **Video:** `A person closes a laptop in a dark room, the only light snuffs out, hard cut to pure black, then a single soft teal pulse appears at center like a heartbeat, extremely minimal, calm, quiet, cinematic negative space.`
- **Imagen clave (MJ):** `pure black frame with a single small soft teal dot glowing gently at center, minimalist, calm, high contrast, negative space --ar 16:9`
- **Negativos extra:** `bright glow bloom, particles, rays`

---

### ACTO 2

**S06 — Mismo reloj, la persona duerme**
- **Video:** `Slow lateral dolly across a dark bedroom at night, a small clock reads 3:07 AM, a person sleeping peacefully, soft blue nocturnal light, calm and still, no flicker, cinematic, warm undertone beneath the blue, quiet.`
- **Imagen clave (MJ):** `dark peaceful bedroom at night, small clock showing 03:07, person sleeping, soft blue light with warm undertone, calm, cinematic, still --ar 16:9 --style raw`
- **Negativos extra:** `messy room, harsh shadows, glowing devices`

**S07 — Panel Kern: clasifica → corre → valida**
- **Video (motion graphics):** `Full-screen calm dark data dashboard, monospaced aligned numbers, a status line moving through classify then run then validate for an inventory SKU, numeric blocks settle into place with subtle ticks, flat design, semantic colors (blue accent, green ok), no glow, data-dense and calm.`
- **Imagen clave (MJ):** `minimal dark supply-chain dashboard, monospaced tabular numbers aligned, status pipeline classify-run-validate, blue and green semantic accents, flat clean UI, calm, data-dense, no gradients --ar 16:9`
- **Negativos extra:** `neon HUD, 3d charts, glow, clutter`

**S08 — QA-gate que veta (compuerta ámbar)**
- **Video:** `A mechanical gate lowers over a failing step in a clean dashboard, the step turns amber, heavy deliberate motion with weight, flat UI, one clear amber warning state, calm and precise, no glow.`
- **Imagen clave (MJ):** `clean flat dashboard with a mechanical gate lowering over a step marked amber warning, deliberate industrial feel, minimal, no gradients --ar 16:9`
- **Negativos extra:** `sparks, neon, explosion`

**S09 — Reintento → verde ok → se escribe archivo**
- **Video:** `A dashboard step retries and turns from amber to green ok, then a file is written, small green checkmarks and monospaced badges appear in sequence with subtle ticks, calm, precise, flat design, semantic green.`
- **Imagen clave (MJ):** `flat dashboard step turning green ok, green checkmarks, monospaced badges reading QA ok, calm minimal UI, no gradients --ar 16:9`
- **Negativos extra:** `celebration, confetti, glow`

**S10 — Amanece, la persona despierta**
- **Video:** `Soft push-in on a person waking rested as golden dawn light streams through a window, warm 3200K light, calm and hopeful without drama, cinematic, clean, no grain, gentle.`
- **Imagen clave (MJ):** `person waking up rested at dawn, warm golden light through window, calm, cinematic, clean, soft --ar 16:9 --style raw`
- **Negativos extra:** `harsh sun, cold light, film grain`

**S11 — Teléfono: entregable + OC preparada (HANDOFF)**
- **Video:** `Close-up of a phone in warm morning light showing a finished deliverable (Excel, report, chart thumbnails) and a prepared purchase order with an amber "awaiting signature" badge, clean flat UI, monospaced numbers, calm, warm.`
- **Imagen clave (MJ):** `phone screen in warm light showing document thumbnails and a prepared purchase order with an amber pending-signature badge, clean flat UI, monospaced numbers, calm --ar 16:9`
- **Negativos extra:** `notifications spam, neon, glow`

**S12 — El dedo toca "Aprobar"**
- **Video:** `Macro of a fingertip pressing an "Approve" button on a phone in warm light, a subtle blue ripple on press, deliberate unhurried human gesture, shallow focus, calm, cinematic realism.`
- **Imagen clave (MJ):** `macro of a fingertip pressing a blue Approve button on a phone screen, warm light, subtle ripple, calm, deliberate, cinematic --ar 16:9 --style raw`
- **Negativos extra:** `multiple fingers, distorted hand, glow burst`

**S13 — Writeback a Odoo, staged y reversible**
- **Video (motion graphics):** `Clean status band shows a changeset applying in staging with tier reversible, then seals with a heavy thunk into green "rollback ok, signed ok" badges, monospaced, flat, precise micro-interaction, no glow.`
- **Imagen clave (MJ):** `flat status band, monospaced text changeset dry-run tier reversible, green rollback-ok signed-ok badges, minimal clean UI, no gradients --ar 16:9`
- **Negativos extra:** `neon, 3d, glow`

**S14 — Zoom-out: la operación en calma**
- **Video:** `Smooth continuous zoom-out revealing a calm well-run warehouse or operations floor breathing at a steady rhythm, neutral warm light, orderly, cinematic, no chaos, quiet confidence.`
- **Imagen clave (MJ):** `wide calm modern warehouse operating smoothly, neutral warm light, orderly, cinematic, quiet, no clutter --ar 16:9 --style raw`
- **Negativos extra:** `busy workers rushing, dramatic light`

**S15 — Ritmo mensual gobernado → aclara**
- **Video:** `Continued slow zoom-out, the scene calmly abstracts as the background lightens toward a clean off-white, a subtle sense of a governed monthly cadence, minimal, calm, cinematic transition.`
- **Imagen clave (MJ):** `calm operations scene gently abstracting toward a clean off-white background, minimal, sense of rhythm and order, cinematic --ar 16:9`
- **Negativos extra:** `busy graphics, neon`

**S16 — Cierre: núcleo teal + logo**
- **Video:** `Clean off-white frame, a small teal core pulses once at center like a calm heartbeat, then a minimal wordmark and tagline fade in, elegant restraint, no glow, single final beat.`
- **Imagen clave (MJ):** `clean off-white frame, a small teal core dot at center, minimal elegant wordmark below, lots of negative space, calm, restrained --ar 16:9`
- **Negativos extra:** `glow bloom, gradient background, particles`

---

## 6. Audio & música (cue sheet)

### 6.1 Arco musical

| Tramo | TC | Textura | Regla |
|---|---|---|---|
| Tensión granulada | 0:00–0:18 | Drone bajo + disonancia leve; tic-tac de reloj; teclado; respiración corta. Unease creciente. | Sin melodía, sin build. Incomodidad sostenida. |
| **Quiebre** | 0:18–0:24 | Corte casi a silencio + **primer latido teal** (motivo, ~60 BPM). | El silencio es el gancho. El latido calma el montaje. |
| Pad cálido minimal | 0:24–0:50 | Pad cálido lento entra; el latido sigue como pulso; "thunk" de compuerta como puntuación rítmica. | Nunca alegre de más. Precisión, no drama. |
| Amanecer | 0:50–1:18 | Suave swell cálido (Rhodes/pad tenue); nada de percusión épica. | **Nunca triunfalista.** La resolución es calma. |
| Cierre | 1:18–1:40 | El pad se adelgaza; termina en **un único latido teal** + cola de pad. | Un beat y corte. Sin fanfarria. |

### 6.2 SFX por escena

| Shot | SFX |
|---|---|
| S01 | Ambiente nocturno hueco; tic-tac de reloj muy leve. |
| S02 | Zumbido de monitor; respiración corta. |
| S03 | Teclado nervioso (teclas secas), papel de post-it. |
| S04 | Clic seco por cada `#REF!` que aparece; un sub grave cuando cae la cascada. |
| S05 | Golpe suave de la laptop al cerrar → silencio → **primer latido teal** (sub-bass). |
| S06 | Silencio de casa dormida; respiración lenta de sueño; tic-tac ahora calmo. |
| S07 | **Tick de cursor** por cada cifra que se asienta; pulso teal de fondo. |
| S08 | **Compuerta mecánica "thunk"** grave con peso; una nota ámbar tensa. |
| S09 | Reintento (tick suave) → **chime verde ok** discreto (no celebratorio). |
| S10 | Ambiente cálido de mañana; leve canto de pájaro lejano (opcional, muy bajo). |
| S11 | Notificación única y suave; tick mono en las cifras de la OC. |
| S12 | Tap háptico; **ripple** sutil; un latido teal sincronizado al toque. |
| S13 | **"Thunk" de sello** de writeback; dos ticks verdes (`rollback ✓`, `firma ✓`). |
| S14 | Ambiente de operación en calma; pulso teal constante. |
| S15 | El ambiente se adelgaza hacia el silencio. |
| S16 | **Un único latido teal final** + cola de pad; corte a silencio. |

---

## 7. Derivados (recorte del master)

### 7.1 Reel vertical 9:16 · 30 s

Objetivo: mismo arco (dolor → método → firma) comprimido. Reencuadre: sujeto y cifras al **centro seguro**; en los inserts de panel, **apilar** las cifras en vez de fila. Bajar el tercio inferior para subtítulos quemados.

| Bloque | Shots del master | Dur |
|---|---|---|
| Gancho (dolor 3am) | S01 + S02 | 0–6 s |
| Puente (pregunta + latido) | S05 (recortado) | 6–9 s |
| Método corre mientras dormís | S06 + S07 | 9–16 s |
| QA veta | S08 + S09 (condensados) | 16–21 s |
| HANDOFF + firma | S11 + S12 | 21–27 s |
| Cierre | S16 | 27–30 s |

VO a usar: VO1 (recorte), VO3 (recorte), VO4 (línea "Vos firmás"), tagline. **Subtítulos quemados obligatorios** (autoplay silenciado). CTA sobreimpreso en cierre: *"Cambiá la planilla de las 3am por un ciclo mensual gobernado, conectado a tu Odoo."* → Growth.

### 7.2 Ad 1:1 (o 9:16) · 15 s

Objetivo: un solo golpe — el contraste caos→firma.

| Bloque | Shots del master | Dur |
|---|---|---|
| Gancho | S01 (03:07 AM) | 0–3 s |
| Método corre | S07 (panel, cifras `ej.`) | 3–8 s |
| Decisión preparada + firma | S11 + S12 (HANDOFF → Aprobar) | 8–13 s |
| Cierre | S16 (logo + tagline) | 13–15 s |

VO mínima: solo VO1 (recorte "3:07… no cierra") + tagline; el resto en texto quemado. Un solo claim en pantalla: **"El método no duerme. La decisión, la firmás vos."**

> **Nota de compliance para derivados:** el badge `ej.` debe quedar **legible** aun en 9:16/1:1 — no recortarlo al reencuadrar. Si una cifra pierde su rótulo `ej.` al croppear, se elimina la cifra, no el rótulo.

---

## 8. Checklist de entrega

**Video / técnico**
- [ ] Master 16:9 a 24 fps, 100 s exactos (1:40:00), 4K + HD.
- [ ] Derivado 9:16 (1080×1920) 30 s @ 30 fps, subtítulos quemados.
- [ ] Derivado 1:1 (1080×1080) 15 s @ 30 fps, subtítulos quemados.
- [ ] Grade: Acto 1 frío/desaturado + grano tenue; Acto 2 cálido/limpio sin grano.
- [ ] Paleta verificada contra oklch de §11 (no sustituir el hex por el oklch autoritativo).
- [ ] Tipografía: Inter (títulos) + IBM Plex Mono `tabular-nums` (toda cifra). Cero cifra en Inter.
- [ ] Sin "glow de IA", sin gradientes neón, sin hologramas/robots en ningún frame.

**Audio**
- [ ] Motivo "latido teal" presente en quiebre (0:18) y cierre (1:40).
- [ ] Mezcla -14 LUFS (social) / -16 LUFS (master); picos ≤ -1 dBTP.
- [ ] Cierre **no triunfalista** (sin build épico ni whoosh).
- [ ] VO en voseo neutral, registro bajo; sin locución "vendedora".

**Contenido / marca**
- [ ] Acto 2 muestra **explícitamente** firma humana + HANDOFF (S08/S09 QA-gate, S11 firma pendiente, S12 Aprobar, S13 rollback ✓ · firma ✓).
- [ ] **Toda** cifra de interfaz rotulada `ej.` y legible en los 3 aspectos.
- [ ] CTA final correcto: → **Growth (Operación Completa)**, conectado a Odoo.
- [ ] Tagline exacto: "Kern — el núcleo que tu cadena no tiene."
- [ ] Corrida final del **Claim-check §9** antes de publicar.

---

## 9. Claim-check final (reproduce y actualiza el del guion fuente)

> Cada claim del film apuntado a su fuente (§ del bible o archivo de código). Confirmado: **cero violaciones de §10**.

| Claim en pantalla / VO | Anclaje | Estado |
|---|---|---|
| "Excel sin método, sin memoria del error, depende de una persona" (VO1/VO2) | §2.1 ① contra-Kern (textual: "Excel no tiene memoria ni método"); σ cruda ≠ σ_e | ✅ auditable |
| "¿Y si el método corriera solo — pero nada se moviera sin vos?" (T4) | §2.1 ④ línea killer; **cualificado en la misma frase** con "nada se moviera sin vos" (§5.2: 3 de 4 desenlaces requieren humano) | ✅ §10-safe (ver nota) |
| "Mientras dormís, corre el método correcto. Simula antes de recomendar. Cita dos fuentes, o no entrega." (VO3) | §5.1 loop + §2.3 SUPERAR (simular-antes-de-recomendar no-opcional) + §2.3 EXPANDIR 1 (grounding con cita forzada, ≥2 de 25 fuentes) + §2.2 | ✅ auditable |
| "QA veta. Un paso que falla ⇒ no se escribe archivo." (T8) | §2.3 SUPERAR 4 (textual) + §5.1 ("QA fails ⇒ no deliverable", CLAUDE.md orquestador) | ✅ auditable |
| "Una decisión preparada, con su evidencia. Vos firmás." + badge `HANDOFF · firma pendiente` (T10/VO4) | §5.2 desenlace **HANDOFF** (paso humano ya preparado, PO pre-llenada); `src/guided.py` | ✅ auditable |
| "Se aplica sobre tu Odoo, en staging, reversible. Nunca a ciegas." + `Changeset · dry-run · tier reversible · 900 s · rollback ✓` (VO5/T13) | §5.3 writeback safety + §2.2 (writeback firmado + rollback); `src/writeback.py` (tiers `read`/`reversible`/`irreversible`, Approval 900 s TTL, `rollback()`, idempotente) + `src/connectors/odoo.py` | ✅ auditable |
| "El humano vende y decide. Kern produce el análisis —el trabajo mecánico, no la decisión— y siempre muestra su fuente." (VO6) | §0/§13 positioning ("el humano vende y decide, Kern produce 10×") + §10 (reemplaza el trabajo mecánico de producir el análisis, **no** la decisión) | ✅ auditable |
| "El método no duerme. La decisión, la firmás vos." (T14) | §2.1 ④ + §5.2 (contrato nunca-desprotegido); Kern **prepara**, no ejecuta la compra | ✅ auditable |
| "Ciclo mensual gobernado, conectado a tu Odoo" (CTA) | §9 paquete **Growth** + §7 value prop Director de Ops (textual) | ✅ auditable |
| Cifras de interfaz: `MAPE 18%→11%`, `s=180 · Q=420`, `SKU-0417`, `excess`-style | Rotuladas **`ej.`** en pantalla = ejemplo ilustrativo del motor, **no** resultado de cliente real (§10 prohíbe casos de éxito con $ reales). `σ_e`, `(s,Q)`, `Changeset`, `rollback` = nombres reales del código; los valores son ejemplo | ✅ blindado |
| Tagline "Kern — el núcleo que tu cadena no tiene." | §3.3 / §13 (tagline maestra) | ✅ auditable |

### Verificación §10 (lo que se evitó, punto por punto)

- ❌ **"IA mágica / última generación"** → no aparece. La estética prohíbe glow/gradientes de IA (§11).
- ❌ **"% de autonomía end-to-end"** → ninguna cifra de autonomía en el film.
- ❌ **"Autonomía total / sin humano"** → **el Acto 2 exhibe firma humana + HANDOFF** (S08–S13). El único "corre solo" (T4) refiere al **método/análisis** y se cualifica en la misma frase con "nada se moviera sin vos".
- ❌ **"Ahorra X horas"** → sin claim de horas, ni suave ni dura.
- ❌ **Casos de éxito / testimonios con $ de clientes** → ninguno; toda cifra grande va `ej.`.
- ❌ **Shopify / Amazon / inventario de Mercado Libre** → no se nombran. El único sistema de registro tocado es **Odoo** (writeback real, `src/writeback.py` + `src/connectors/odoo.py`).
- ❌ **Control Tower autónomo / "Kern decide por vos"** (plan 3.0, no construido) → el film **no** muestra a Kern ejecutando el ciclo solo; muestra preparación + firma humana. VO6 dice literalmente "produce el análisis —el trabajo mecánico, no la decisión—".
- ❌ **"Reemplaza a tu equipo"** → se dice explícitamente que reemplaza el **trabajo mecánico de producir el análisis**, no la decisión.

**Nota sobre T4 ("corriera solo"):** frase heredada textual del guion fuente (§2.1 ④). Es §10-safe **solo** porque la cláusula "pero nada se moviera sin vos" viaja pegada en el mismo cartel y en el mismo aire. Si en montaje se separa la frase en dos planos, **debe** conservarse la segunda cláusula visible/audible; de lo contrario, se reescribe. Riesgo señalado y mitigado en el checklist §8.

---

*Fin del kit. Si un claim no está acá o en las fuentes citadas, verificarlo contra el código antes de publicar. La credibilidad ES el producto.*
