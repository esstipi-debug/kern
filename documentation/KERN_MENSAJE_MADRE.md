# Kern — El mensaje madre

> *Cómo respondemos "eso es automatización" · 24-jul-2026 · v1.0*
>
> **Qué es este documento:** el paraguas de mensaje que va arriba de todo lo demás. No reemplaza el mapa competitivo (§2 del bible), ni el producto (§5), ni los claims prohibidos (§10). Reencuadra **la respuesta a la objeción que mata la venta** y reemplaza la §4 del bible.
>
> **Regla dura (§10 del bible, sin excepción):** cada claim auditable contra código o fuente citada, o no se dice.
>
> Fuentes de verdad de precios y cadencias: `scm_agent/package_specs.py`. Fuentes de verdad de mecanismo: `scm_agent/orchestrator.py`, `src/writeback.py`, `src/guided.py`, `src/forecasting.py`, `scm_agent/citation_gate.py`, `src/contingent_fee.py`.

---

## 0. La objeción, en una línea + la respuesta en una línea

**La objeción:** *"Pero básicamente estás ofreciendo automatización."*

**La respuesta:** *"Sí — igual que Netstock, que Stocky y que la regla min/max que ya tenés en tu ERP. La diferencia no es si hay automatización: es **quién la opera** y **quién responde cuando el número sale mal**. La de ellos la operás vos. Ésta la opero yo, y por escrito."*

No peleamos la palabra. La concedemos y movemos el eje. Pelear "automatización vs. autonomía" es la derrota: es una distinción de ingeniería que un dueño de un negocio de USD 2M no puede sentir, y que ya concede que la discusión es sobre tecnología (donde perdemos por precio contra un SaaS de USD 49-349/mes).

---

## 1. El mensaje madre

### La frase madre (canónica)

> **"No te vendo un sistema que operás vos: te cubro la función de compras que todavía no podés contratar. La decisión llega tomada y fundada, vos la aprobás, y hay alguien con nombre que responde — por escrito."**

### Variantes

**① Llamada de ventas** (concesión + judo, es la que se dice en vivo):
> *"Sí, adentro hay automatización. Igual que en Netstock, en Stocky y en la regla min/max de tu ERP. La pregunta útil no es si hay automatización — la hay en los cinco. Es qué se automatiza, y quién queda a cargo. Ellos te venden la herramienta y te dejan operándola. Yo te entrego el trabajo hecho: qué reponer, cuánto y a qué precio, con el porqué escrito. Vos aprobás. Y si el número sale mal, tenés a quién llamar y tenés escrito qué pasa."*

**② Video / hook de 3 segundos** (la que pega sin traducción):
> *"Hoy el encargado de compras de tu negocio sos vos, un domingo a la noche, con una planilla."*

**③ Bio / landing** (una línea, sin jerga):
> *"La función de compras, hecha por nosotros. Decisión tomada y fundada — vos aprobás."*

### Qué categoría ocupamos

**Servicio profesional externalizado por función.** El mismo estante mental donde el dueño ya tiene al contador, al estudio de sueldos, al despachante y al operador logístico: una función crítica comprada como servicio, entregada por un humano que responde, corriendo software que el cliente nunca toca.

- **Partida presupuestaria:** honorarios / servicios profesionales — **no** licencias de software.
- **Etiqueta que se dice en voz alta:** *"la parte del encargado de compras que decide qué comprar, cuánto y cuándo"*. **Nunca** *"plataforma de planificación de inventario"* ni *"compras tercerizadas"* a secas (esa promete negociar con proveedores, que no hacemos).
- **Test de categoría bien hecha:** cuando el dueño se lo cuenta al socio, dice *"conseguí quién me lleve las compras"*, no *"compré un sistema"*.

**Uso de la analogía del contador — con freno de mano.** Sirve para explicar **el modo de compra** (funcionás con un externo que responde y no mirás su software). **No** sirve para explicar por qué no se puede saltear (al contador lo tenés porque te obliga el fisco) ni para anclar precio (en LATAM el contador de una PyME sale USD 200-600/mes y ancla hacia abajo). Regla operativa: **nunca la palabra "contador" en la misma frase que una cifra**, y acotarla en el mismo aliento: *"no tengo matrícula; lo que sí tengo es escrito qué pasa si nos equivocamos"*.

### Contra qué presupuesto nos compara el comprador

En este orden, y el orden importa:

1. **Sus propias horas operando lo que ya tiene.** Es el ancla honesta e inmediata (tesis §4) y funciona en toda geografía. *"No reemplazás tu app: reemplazás las horas que hoy ponés vos operándola."*
2. **El costo del error de compra.** La orden que quedó muerta en el depósito y el quiebre del artículo estrella en la semana buena. Es el único número que sale de **sus** datos (`excess_value` en el Diagnóstico), y por eso es el ancla más fuerte donde no hay referencias.
3. **El sueldo cargado del primer encargado de compras** — **solo en Anglosfera (AU/NZ/US/UK)**. Ver el envelope abajo.

Bajo este marco el comprador **deja** de compararnos contra la línea de USD 49-349/mes de software self-serve.

### Envelope de uso — dónde este marco NO se dice tal cual

Tres límites, y son de targeting/geografía, no de redacción:

| Situación | Qué pasa | Qué se dice en su lugar |
|---|---|---|
| **LATAM, arriba de Starter LatAm (USD 250-300/mes)** | El ancla salarial **se invierte**: Growth (USD 1.500/mes) son 2-3 sueldos de analista LatAm (~USD 400-650/mes) y el dueño hace esa cuenta de cabeza. `package_specs.py` ya documenta que la lógica de horas-del-fundador-como-%-de-salario ancla el precio **anglosajón**, no el LatAm. | Sacar el sueldo del pitch. Anclar a **horas propias + costo del error** (punto 2). |
| **Ya hay un encargado de compras / planificador** | "Te cubro la función" es una amenaza de despido dicha delante de quien evalúa el piloto — es el saboteador #1 del teardown, y además roza el claim prohibido "reemplaza a tu equipo" (§10). | *"Le devolvemos tiempo a tu encargado corriendo la cola larga que no alcanza a tocar."* Nunca nombrar el puesto. |
| **Calificación obligatoria, primeros 60 segundos** | — | **"¿Quién decide hoy qué reponer?"** Si sale un nombre propio, se cambia de marco o se descalifica. |

### Tres bloqueantes antes de usar esto en vivo

1. **Reconciliar precios entre documentos.** `package_specs.py` y el bible §9 dicen 900 / 1.500 / 3.200 / 4.500. `KERN_AGENCIA_IA_TESIS_COMERCIAL.md` §4 todavía corre la aritmética con 2.000 / 4.000 / 7.500 / 9.000-12.000 (precios muertos). **Un marco cuyo eje es "confiá en mis números" no puede tener dos precios propios.** Fuente única de verdad: `package_specs.py`.
2. **Reconciliar la cantidad de fuentes.** El bible y CLAUDE.md dicen **25**; la tesis dice 33 con 1.953 nodos. Usar **25** hasta reconciliar contra el grafo. Nunca 33.
3. **Firmar la Carta de Responsabilidad** (§3, abajo). Sin ella, "hay alguien que responde" es infalsificable y muere en el segundo follow-up.

---

## 2. Por qué NO peleamos la palabra "automatización"

### El error del encuadre viejo

La §4.2 anterior del bible defendía una tabla **"Automatización vs. Autonomía"**. El problema no es que sea falsa: es que es una **distinción de ingeniería interna**.

- El dueño de un negocio de USD 2M **no puede sentir** la diferencia entre "ejecuta reglas predefinidas" y "decide con datos reales y modelos actualizados". Las dos son, desde su silla, *"el programa te tira un número"*.
- Peor: al defender la palabra ya aceptamos que la discusión es **sobre tecnología**. Y en esa cancha nos compara contra un SaaS de USD 49-349/mes y perdemos por precio, siempre.
- Y peor todavía: invita la comparación equivocada. **Si somos automatización, competimos contra software. Si somos otra cosa, competimos contra contratar a alguien.**

### El judo

*"Eso es automatización"* es **más cierto de los competidores que de nosotros.** Netstock, Inventory Planner, Cin7, Cogsy, Prediko, Stocky: son software de automatización **que el cliente opera**. Su propia lista de precios lo prueba sin que nosotros afirmemos nada sobre ellos:

> **"USD 49 a 349 por mes no compra el tiempo de nadie. Compra un login. Con eso, el encargado de compras seguís siendo vos."**

(Uso disciplinado: la aritmética se dice contra el tramo **49-119**. Contra Inventory Planner ~245 y Cin7 Core 349+, que traen humanos de onboarding, el eje se mueve a: *"su humano te la **implementa** una vez; el mío la **opera** todos los meses"*. Nunca afirmar un déficit de capacidad de un rival — se verifica en 30 segundos en su página de ayuda y quema el pitch entero; ésa fue la lección del fix de cobertura nativa de Stocky.)

### El segundo movimiento — el que convierte la amenaza en argumento

Shopify Sidekick, Odoo 18/19 y Amazon Seller Assistant ya sugieren cuánto reponer, **gratis**, adentro de apps que el cliente ya paga. Eso mata el pitch de herramienta y **alimenta** el nuestro:

> *"Tu Shopify o tu Odoo ya te sugieren reponer, gratis, hace dos años. ¿Cuándo fue la última vez que abriste esa pantalla y cambiaste algo con lo que te dijo? Ahí tenés la prueba: el problema nunca fue que falte automatización. Falta el puesto que la opere."*

### La honestidad que hace que el judo funcione

**Sí, por dentro Kern automatiza.** Concederlo es lo que nos da derecho a mover el eje. Lo que cambia es:

1. **QUÉ automatiza** — el criterio del oficio, no los clicks del cliente.
2. **Que rinde cuentas** — se niega a entregar su propia mala salida, no rellena citas, y pide permiso antes de tocar nada irreversible.
3. **Que lo entrega un humano que responde**, con qué significa "responder" puesto por escrito.

**Disciplina de copy:** nunca decir *"nosotros no automatizamos"* ni *"nosotros no usamos IA"* — es falso (el núcleo determinístico corre con o sin LLM, §5.4) y suena a esquive. La forma es afirmativa: **sí automatizamos; automatizamos el criterio, no tus clicks.**

---

## 3. Las 3 diferencias que sí se sienten

### ① Pericia — automatiza el criterio, no tus clicks

**La frase (en idioma de comprador, no de fórmula):** *"Lo que vos llamás automatización ejecuta TU regla: si el stock baja de 20, comprá 100. ¿De dónde salió ese 20? Lo pusiste vos, hace años, y nadie lo volvió a mirar. Lo que corremos es el criterio que hoy no tenés corriendo."*

Traducción a plata, siempre — la fórmula sólo como prueba a pedido: *"la diferencia entre las dos formas de calcular el colchón es comprar de más y dejar caja atrapada, o quedarte corto y perder la venta de la semana buena."*

**Razón para creer (auditable):**
- El colchón se dimensiona sobre el **error del pronóstico**, no sobre la dispersión cruda de la venta — el error más común del oficio, hecho el default. `src/forecasting.py:49-62`. **Decirlo con su borde:** *"si no hay períodos suficientes para estimarlo, cae a la dispersión cruda **y te lo dice**"* (`sigma = self.error_std if self.error_std > 0 else self.demand_std`). Afirmar que "no es una casilla que se pueda desmarcar" es sobre-claim.
- **El patrón de demanda elige el modelo, no el usuario:** clasificación ADI/CV² (`src/forecastability.py:23-26`, umbrales 1.32 / 0.49, Syntetos-Boylan-Croston 2005) y de ahí sale la política `(R,S)` si es intermitente, `(s,Q)` si es pareja (`jobs/inventory_optimization.py:131-153`).
- **El SKU #4.000 es literal, no retórico:** el ciclo corre `for pid in source.list_products()` — el catálogo entero, no los ítems A (`jobs/inventory_optimization.py:120`).
- **Ojo con el ICP LATAM:** muchos prospectos tienen 300-900 SKUs y la cola larga NO es donde está su plata. Ahí la frase correcta no es "el SKU #4.000" sino *"los 60 que sí mirás — pero el mes que estás apurado no mirás"*.

### ② Rinde cuentas — se niega a entregar su propia mala salida

**Razón para creer (auditable):**
- **La QA veta el entregable, en un solo lugar para las 45 capacidades:** si `tool.qa()` devuelve issues ⇒ `STATUS_QA_FAILED` con `deliverables={}` y *"QA failed; no deliverables written"* (`scm_agent/orchestrator.py:156-162`). **Es demostrable en vivo:** forzar el fallo y mostrar el directorio de entregables vacío. Es el activo más fuerte del set y hay que **abrir con él**, no cerrarlo.
- **No rellena citas para verse fundamentado:** `MIN_CITATIONS=2`, `MAX_HOPS=2` y `EXCLUDED_CONCEPTS` anti-falso-amigo; si sobreviven menos de dos, tira el lote entero a vacío en vez de mostrar una cita conveniente (`scm_agent/citation_gate.py:43-44, 233-239`). **Formulación correcta:** *"si no puede fundamentar con al menos dos fuentes, no te muestra ninguna"*. **Formulación incorrecta y refutable:** "si no puede citar, no entrega nada" — el entregable sale igual, sin citas.
- **De los cuatro finales posibles, tres paran en un humano por diseño:** `EXECUTED` / `OPTIONS` / `HANDOFF` / `ESCALATED`, y el verificador rechaza toda salida sin camino ejecutable (`src/guided.py:24-29, 115-125`). **Nunca decir "3 de cada 4 veces"** — eso afirma una frecuencia del 75% que nadie mide y roza el "% de autonomía" prohibido en §10.
- **Nada irreversible toca tu sistema sin tu firma:** tiers `read`/`reversible`/`irreversible`, el irreversible **siempre** exige humano, aprobación firmada con HMAC atado al hash del changeset, TTL configurable (900 s por defecto), aplicación idempotente y `rollback()` (`src/writeback.py:29, 42, 158, 288-299`). Conectores reales: Odoo y el Excel del cliente con rollback celda por celda (`src/connectors/excel.py`). **Corrección obligatoria:** *no* decir "nada toca tu sistema sin tu firma" a secas — `src/connectors/odoo.py:346, 427` trae `auto_apply_reversible=True` por defecto. La versión defendible es: ***"nada irreversible sin tu firma; lo reversible queda con rollback celda por celda y bitácora."***

**Y el antídoto obligatorio a cada freno.** Un comprador impaciente oye "entonces igual lo tengo que revisar yo". Cada freno se dice pegado a quién hace el trabajo:
> *"No te pide que revises el análisis. Te pide que autorices el movimiento de plata."*

Y si la QA veta y el ciclo no sale, hay que decir qué pasa: **ese ciclo no se factura** (ver la Carta). Silencio prolijo, bajo marco de función, se lee como ausentismo.

### ③ Un humano responde — y está escrito qué significa

Ésta es la pata que saca a Kern de la casilla software, y también la más fácil de sobre-prometer. Por eso viene con documento.

**Razón para creer (auditable):**
- El rol del operador es un cargo documentado, no una promesa de venta: `documentation/operator/` (charter, división de trabajo, runbooks, catálogo de entregables).
- La escalación sale ruteada a un rol con plazo por disparador, dentro del propio resultado (`src/escalation.py`: `route_to` + `sla`).
- **El único mecanismo con piel financiera real hoy:** `src/contingent_fee.py` — 10-20% del cash efectivamente recuperado, **piso USD 1.500**, fee topado al cash recuperado, **USD 0 si no se recupera nada**. Decir el piso nosotros primero: en recuperos chicos el piso manda y el % efectivo sube.

#### La Carta de Responsabilidad (una página, se firma antes de la primera llamada)

Qué significa **"responde"**, exactamente:

| Compromiso | Alcance |
|---|---|
| **Retrabajo sin cargo** | Si un entregable tiene un error de método, se rehace sin cargo dentro de la ventana acordada. |
| **Ciclo sin entregable = ciclo sin factura** | Si la QA veta y el ciclo no sale, ese ciclo no se cobra. |
| **Fee contingente** | Donde aplica (Sprint de Liquidación): % del cash recuperado, piso 1.500, 0 si no se recupera nada. |
| **Una persona con nombre** | En todos los peldaños. **La palabra "SLA" se reserva al Retainer Ejecutivo (USD 4.500/mes)**, que es donde existe por contrato (`package_specs.py`). Prometer SLA en Starter es una sobrepromesa verificable en el propio contrato. |
| **Lo que NO es** | No hay garantía de resultado ni indemnización por pérdidas. No hay matrícula profesional. Se dice de frente, primero. |

#### Y el trabajo humano que no es apretar un botón

Respuesta preparada a *"o sea que te pago para que aprietes enter"*:
- **Intake y limpieza:** los catálogos PyME traen GTINs sucios o faltantes; el onboarding degrada con elegancia (IDs internos + el hueco marcado como hallazgo), y ahí van las horas de verdad.
- **Los parámetros durables del cliente** (nivel de servicio, tasa de mantenimiento, costo de pedido, lead time) se contestan a mano una vez y se persisten (`src/client_profile.py`); no se derivan de nada.
- **Elegir entre las OPTIONS rankeadas** que el motor devuelve.
- **El libro de decisiones del ciclo** — el artefacto que prueba que hubo trabajo: qué recomendaciones **no** salieron y por qué las vetó la QA, cuáles escalaron y a quién, cuáles se anularon a mano. *"El software escupe números; el trabajo es decidir cuáles son mentira. Este mes fueron éstos, y por esto."*

---

## 4. Guion de objeciones (el follow-up doble)

Regla de oro del guion: **cada respuesta tiene que traer municipio de segunda ronda pegado.** Una frase que se entiende en 5 segundos y no tiene respuesta de segundo nivel quema la reunión en vez de abrirla.

| Lo que dice el comprador | Lo que respondemos (palabras exactas) |
|---|---|
| **"Eso es automatización."** *(ronda 1)* | "Sí. Igual que Netstock, que Stocky y que la regla min/max de tu ERP: la hay en los cinco. La pregunta útil es qué se automatiza y quién queda a cargo cuando el número sale mal. Ellos te venden la herramienta y te dejan operándola — por eso sale doscientos dólares: a ese precio no entra el tiempo de nadie, entra un login. Yo te entrego el trabajo hecho." |
| **"Sí, pero el trabajo lo hace el programa igual. Te pago para que aprietes enter."** *(ronda 2 — la que decide)* | "El software escupe números. El trabajo es decidir cuáles son mentira. Este ciclo, tres recomendaciones no salieron porque las vetó el control de calidad, dos escalaron porque el lead time del proveedor no cerraba, y los parámetros con los que corre — tu nivel de servicio, tu costo de pedido, tu lead time real — los contesté yo con vos, no salieron de un default genérico. Te lo entrego escrito: qué salió, qué no salió y por qué. Eso no lo tiene el que aprieta un botón." |
| **"Eso lo hace mi ERP con min/max."** | "¿De dónde salió el mínimo que tenés cargado hoy? Lo pusiste vos, o venía así de antes, y nadie lo volvió a mirar desde entonces. Tu regla es estática: no sabe cuánto se equivocó tu pronóstico el mes pasado ni si ese producto se vende parejo o a saltos. Y no reemplazamos tu Odoo: lo dejamos como el brazo que ejecuta una política gobernada, en borrador y reversible." |
| **"Ya tengo un SaaS de inventario."** | "Perfecto, no te lo saco. Pregunta: ¿cuándo fue la última vez que abriste esa pantalla y cambiaste algo con lo que te dijo? Ahí está el punto — el problema nunca fue que falte automatización, es que falta quien la opere. No reemplazás tu app: reemplazás las horas que hoy ponés vos operándola." |
| **"¿Es IA que inventa números?"** | "Es lo contrario, por construcción. Si el análisis no pasa su propio control de calidad, no se escribe ningún archivo — no te llega un entregable flojo, no te llega nada. Y si no puede fundamentar una recomendación con al menos dos fuentes, no te muestra ninguna: nunca rellena con una cita conveniente. Además el núcleo de los números es determinístico: corre con o sin modelo de lenguaje. Mostrame la herramienta de IA que se niega a contestar cuando no puede citar su trabajo." |
| **"¿Y si se equivoca, quién responde?"** | "Está escrito, y te lo doy antes de firmar. Uno: si un entregable tiene un error de método, se rehace sin cargo. Dos: si el control de calidad veta y el ciclo no sale, ese ciclo no se factura. Tres: en liquidación cobramos un porcentaje del cash que efectivamente recuperamos — piso mil quinientos, y cero si no recuperamos nada. Y lo que NO es, te lo digo yo primero: no hay garantía de resultado ni indemnización por pérdidas, y no tengo matrícula. Lo que tenés es a alguien con nombre y un papel que dice qué pasa si nos equivocamos. Preguntale a tu proveedor de software qué pasa si su recomendación te hace comprar mal — no hay respuesta, porque nunca hubo nadie del otro lado." |
| **"¿Por qué no contrato a alguien?"** | "En algún momento lo vas a hacer, y está bien. Pero seamos exactos con qué te cubro y qué no: **yo no negocio con tu proveedor, no persigo la orden, no recibo la mercadería y no camino tu depósito.** Eso lo seguís haciendo vos o la persona que contrates. Lo que te saco de encima es decidir **qué pedir, cuánto y cuándo** — que es la parte que hoy hacés el domingo a la noche y la primera que se salta cuando estás apurado. Y hay dos cosas que la persona no te da: corre igual el mes que estás apurado o de vacaciones, y corre sobre todo el catálogo, no sobre los que llegás a mirar." <br><br>*(En Anglosfera se agrega el ancla salarial. En LATAM arriba de Starter LatAm, NO: ahí el sueldo cargado está al lado o abajo de nuestro precio y la cuenta se da vuelta.)* |
| **"¿Por qué no uso ChatGPT?"** | "Podés, y te va a dar un número. El problema es que te lo va a dar siempre — nunca te va a decir 'con estos datos no puedo'. Acá si el análisis no pasa el control, no hay entregable; si no hay dos fuentes que lo sostengan, no hay citas; y nada escribe en tu sistema sin una aprobación tuya que vence y se puede deshacer. Un chatbot no tiene forma de negarse a su propia mala salida, y no hay nadie del otro lado cuando el número sale mal." |
| **"No tenés clientes."** *(la que va a venir igual)* | "Cierto, y no te voy a vender referencias que no tengo. Desriesguemos al revés: corremos el diagnóstico sobre **tus** datos, con tu export de Excel, y te doy un número de plata muerta que podés ir a verificar al depósito con tus propias manos. Si el número no te cierra, no seguimos. Y te muestro la lista de lo que tengo prohibido afirmarme a mí mismo — nadie que venda humo publica sus propios límites." |

**Movimiento gratis y sub-utilizado:** mostrar la §10 (claims prohibidos) en la llamada. Es la prueba conductual más fuerte que tiene este posicionamiento y no cuesta nada.

---

## 5. Marcos alternativos considerados (y por qué no ganaron)

| Marco | Su mensaje | Veredicto |
|---|---|---|
| **El que responde por el número** (responsabilidad / transferencia de riesgo) | "Comprás automatización y el riesgo del error queda de tu lado; comprás esto y queda del nuestro." | **Absorbido, no descartado.** Aporta la mejor razón-para-creer del set (QA veto, writeback firmado, fee contingente) y el eje "quién responde". Se descarta como **titular** porque "el riesgo queda de nuestro lado" es un claim de resultado con 0 clientes y contradice el propio mecanismo (si vos firmás, el que decide sos vos). Se conserva **con Carta escrita**, nunca como promesa suelta. |
| **El puesto tercerizado (el contador de tu inventario)** | "Tenés un contador para tu plata y no tenés a nadie para tu stock, que es la misma plata." | **Espina dorsal adoptada** (categoría = función, partida = honorarios). Se corrige lo que lo hundía: la analogía del contador importa matrícula y ancla precio hacia abajo, y el ancla salarial se invierte en LATAM. Se conserva como **modo de compra**, no como justificación ni como ancla de precio. |
| **Automatización con firma** (judo competitivo) | "Automatización tienen todos: la de ellos la operás vos, la mía la opero yo." | **Absorbido como respuesta a la objeción** (§0 y §2). Se descarta como marco completo porque su pata central — "automatizamos el criterio" — es un reclamo de *calidad de automatización* que colapsa en la segunda ronda: el criterio correcto es un default, y un default se copia en un release. |
| **El puesto cubierto, no el programa comprado** (jobs-to-be-done) | "Kern es tu función de compras hecha por nosotros; igual que con el contador, no comprás el programa, comprás que esté hecho." | **Absorbido parcialmente**: aporta el lenguaje del comprador ("el domingo a la noche con la planilla") y el de-scope explícito. Se descarta como titular porque su mensaje madre no contiene ninguna de sus propias razones para creer — lo puede decir cualquier agencia el día cero, y ahí la venta se decide por precio y referencias, que es donde tenemos cero. |
| **Criterio embebido** ("automatizamos el oficio, no tus clicks") | "Kern corre el criterio de un planificador sobre cada SKU y cada ciclo." | **Descartado como paraguas.** Empata en palabras con lo que ya dicen Netstock e Inventory Planner y sólo gana bajo auditoría de código; e invita la pregunta de credencial ("¿sos CPIM?") que nos devuelve a la casilla software. Se conserva su mejor activo: **la pregunta de apertura** *"¿de dónde salió el mínimo que tenés cargado?"*. |

**Cómo quedó armado el ganador:** categoría del **rol no contratable** + razón-para-creer de la **rendición de cuentas** + **judo** como respuesta a la objeción, con tres reparaciones que ningún marco individual traía: la Carta de Responsabilidad, el envelope geográfico del ancla salarial, y el artefacto de segunda ronda (el libro de decisiones del ciclo).

**Trade-off admitido, no resuelto:** este marco **vende explícitamente lo que no escala**. La responsabilidad escala con horas de un operador, no con software — techo realista de pocos clientes. Es posicionamiento de práctica boutique: defendible si el objetivo es facturación de un operador, incoherente con una ambición de producto self-serve. El día que exista el cockpit self-serve, la frase "la de ellos la operás vos, ésta la opero yo" es un argumento contra nuestro propio producto nuevo. Formulación que no cierra esa puerta: *"hoy te lo entrego operado; el día que lo quieras operar vos, te paso el volante con el mismo método adentro."*

---

## 6. Reemplazo para §4 del bible (copiar tal cual)

> Reemplaza íntegramente la §4 de `KERN_BRAND_PRODUCT_BIBLE.md`. **Los subtítulos 4.1, 4.3 y 4.4 se conservan con su nombre exacto** (otros documentos los citan). Cambia el título de la §4, su nota de "por qué importa", la §4.2 completa, y se agrega una §4.5.

```markdown
## 4. La filosofía: quién opera y quién responde

> **Por qué importa:** es la respuesta a la objeción #1 que recibimos — *"pero básicamente
> estás ofreciendo automatización"*. No se pelea la palabra: se concede y se mueve el eje.
> Sí, adentro hay automatización, igual que en los cinco competidores de §2. La diferencia
> es **qué** se automatiza (el criterio del oficio, no los clicks del cliente), que **rinde
> cuentas** (se niega a entregar su propia mala salida) y que **la entrega un humano que
> responde**, con lo que "responder" significa puesto por escrito. La autonomía, mientras
> tanto, se gana con evidencia — no se declara.

### 4.1 El arco de 4 escenas (storytelling / timeline animado)
- **Mes 1:** un monitor detecta un SKU en punto de reorden → el agente propone la orden → como
  es la primera vez, **escala a un humano** (T3). El humano aprueba.
- **Mes 2:** compara demanda predicha vs. real. MAPE 8%, bias +3%. Ajusta parámetros solo.
- **Mes 3:** mismo evento; la tool ya tiene 94% de precisión → baja a **un click** (T2).
- **Mes 6:** tras 4 ciclos con precisión >92% → **auto-ejecuta** (T1) y notifica: "Orden
  ejecutada. Evidencia en A4."

**Remate:** *La autonomía no se asume, se gana. No se configura, se demuestra.*

> **Dos aclaraciones que hacen que este arco no contradiga el mensaje madre:**
> 1. Las cifras (8%, 94%, >92%, 4 ciclos) son **ejemplo de diseño del arco**, no una medición
>    de un cliente. Rotularlas así en pantalla y en copy.
> 2. La autonomía que se gana aplica a la **ejecución mecánica dentro de límites**, nunca al
>    compromiso de plata: lo irreversible siempre exige un humano (`src/writeback.py`), y un
>    monto por encima del umbral se re-rutea a `ESCALATED` (`src/escalation.py`). T1 no
>    significa "sin humano" — significa "sin humano para el paso mecánico ya probado".

### 4.2 "Eso es automatización" — la respuesta (carrusel de 2 columnas)

**La línea de apertura, textual:** *"Sí. Igual que Netstock, que Stocky y que la regla min/max
de tu ERP: la hay en los cinco. La pregunta útil no es si hay automatización — es quién la
opera y quién responde cuando el número sale mal."*

| Automatización que **operás vos** | Una función que **rinde cuentas y firma un humano** |
|---|---|
| Ejecuta la regla que escribiste vos, hace años, y nadie volvió a mirar | Corre el criterio del oficio: el colchón sobre el error del pronóstico, y el patrón de demanda elige el modelo |
| Siempre entrega algo, aunque no aguante | Si no pasa su propio control de calidad, **no se escribe ningún archivo** |
| Te da un número sin decir de dónde salió | Si no puede fundamentarlo con al menos dos fuentes, no te muestra ninguna — nunca rellena con una cita conveniente |
| Escribe en tu sistema y después te enterás | Nada irreversible sin tu firma; lo reversible queda con rollback y bitácora |
| Si sale mal, la consecuencia es tuya y no hay a quién llamar | Hay alguien con nombre, y está escrito qué pasa si nos equivocamos |
| Cuesta USD 49-349/mes: a ese precio no entra el tiempo de nadie, entra un login | Cuesta lo que cuesta una función, porque incluye a quien la opera |

**El segundo movimiento (el que convierte la amenaza de plataforma en argumento):**
*"Tu Shopify o tu Odoo ya te sugieren reponer, gratis, hace dos años. ¿Cuándo fue la última
vez que abriste esa pantalla y cambiaste algo con lo que te dijo? El problema nunca fue que
falte automatización — falta quien la opere."*

**Disciplina de copy (obligatoria):**
- Nunca decir "no automatizamos" ni "no usamos IA": es falso (§5.4) y suena a esquive.
- Nunca decir "3 de cada 4 veces para y pide permiso" (frecuencia no medida, roza §10). Sí:
  **"de los cuatro finales posibles, tres paran en un humano por diseño"**.
- Nunca decir "si no puede citar, no entrega nada". Sí: **"si no puede fundamentar con dos
  fuentes, no te muestra ninguna"**.
- Nunca decir "nada toca tu sistema sin tu firma". Sí: **"nada irreversible sin tu firma"**.
- Nunca decir la palabra "SLA" fuera del Retainer Ejecutivo.

### 4.3 La metáfora del organismo (motion graphics)
Kern no es un edificio que se construye planta por planta. Es un **organismo que crece
añadiendo vasos sanguíneos:** cada sensor es un capilar, cada tool un músculo, cada
verificación un reflejo más fuerte. Crece porque **nunca deja de medirse.**

### 4.4 Cita de cierre (frase final de video)
> *"No es que la máquina sea perfecta. Es que la máquina sabe cuánto se equivoca y lo dice
> antes de que tú lo preguntes."* — Principio de diseño Kern A4

### 4.5 Qué significa "responde" (la Carta de Responsabilidad)

"Hay un humano que firma" no se dice sin esto detrás. Se entrega por escrito antes de la
primera propuesta:

| Compromiso | Alcance |
|---|---|
| Retrabajo sin cargo | Error de método en un entregable ⇒ se rehace sin cargo dentro de la ventana acordada |
| Ciclo sin entregable = ciclo sin factura | Si la QA veta y el ciclo no sale, ese ciclo no se cobra |
| Fee contingente | Sprint de Liquidación: % del cash recuperado, piso USD 1.500, **USD 0 si no se recupera nada** (`src/contingent_fee.py`) |
| Una persona con nombre | En todos los peldaños. **SLA contractual sólo en el Retainer Ejecutivo** (`package_specs.py`) |
| Lo que **no** es | No hay garantía de resultado ni indemnización por pérdidas. No hay matrícula profesional. Se dice primero, de frente. |

**Y la frontera de alcance, en la misma frase que la promesa:** *"Vos seguí hablando con tus
proveedores. Lo que te sacamos de encima es decidir qué pedirles, cuánto y cuándo."* Kern no
negocia con proveedores, no persigue la orden, no recibe mercadería y no camina el depósito.
```

---

## 7. Ad reframeado — SAP IBP USD 100k (reemplaza Familia C, guion 3)

> **Por qué se reescribe:** el guion anterior decía, en el fondo, *"el mismo método, más barato"*. Eso nos deja como **automatización commodity que compite por precio** — exactamente la casilla de la que este documento nos saca. El nuevo dice otra cosa: **ese precio está tasado para una empresa que YA TIENE un equipo de planeación adentro. Vos no lo tenés y todavía no lo podés contratar. Kern es esa función, a un precio que sí pagás.** El $100k argumenta *"por fin podés tener la capacidad"*, no *"robot más barato"*.

### 3. Cien mil al año compran un equipo que vos no tenés

- **Formato & duración:** ad **6–20s** (contraste de número gigante). *Justificación de los 20s:* el reframe tiene tres beats obligatorios (el precio → **para quién está tasado** → la función que te falta) donde el guion viejo tenía dos (precio → precio). El beat del medio es todo el reframe; a 15s se pierde y volvemos a "más barato".
- **Objetivo:** vender (accesibilidad reencuadrada como *función faltante*, no como descuento)
- **Audiencia:** dueño/CEO o Director de Ops de mid-market que descartó una suite enterprise por precio y **no tiene equipo de planeación** · paquetes Growth / Scale (§9)
- **Idea visual central / metáfora:** el precio enterprise no aplasta a la PyME por ser grande — **aplasta porque incluye sillas que la PyME no tiene ocupadas.** Las sillas se vacían una por una hasta que queda una sola, con tu nombre.
- **Gancho (0–3s):** *Primer frame:* full-bleed, número gigante en Plex Mono rojo riesgo — `USD 100.000 / año`, micro-caption `SAP IBP · precio de lista · ancla de mercado 07-2026`. *Primera línea:* **"Cien mil dólares al año. Y eso es sólo la licencia."**

| Tiempo | En pantalla / Visual | Voz en off / Texto | Dirección |
|---|---|---|---|
| 0–3s | `USD 100.000/año` llena la pantalla, rojo riesgo; micro-caption `SAP IBP · precio de lista · ancla de mercado 07-2026` | VO: "Cien mil dólares al año. Y eso es sólo la licencia." | El número aplasta: frío, monolítico, sin movimiento |
| 3–8s | Debajo del número aparecen tres casilleros grises rotulados en Plex Mono: `planeador de demanda` · `analista de inventario` · `dueño del dato` | VO: "Ese precio está tasado para una empresa que ya tiene esto adentro." | Los casilleros se encienden uno por uno, con peso; sonido de tick seco |
| 8–12s | Los tres casilleros **se vacían** en secuencia. Queda uno solo, en teal, rotulado `vos` | VO: "Vos no lo tenés. Y todavía no lo podés contratar." | Silencio total al vaciarse el tercero. El teal es lo único vivo en cuadro |
| 12–17s | El casillero `vos` se duplica: al lado aparece `USD 300–5.000/mes` en teal, Plex Mono; badge `la función, operada por nosotros · vos aprobás` | VO: "Kern es esa función, hecha por nosotros, a un precio que este negocio sí paga. Vos aprobás." | La cifra teal se sostiene, estable, sin efecto |
| 17–20s | Logo + tagline | Texto: "No es la suite más barata. Es la función que te falta." | Cierre seco, corte a negro |

- **Música / tono:** un solo drone grave sostenido; **se corta en seco** cuando se vacía el tercer casillero (8–12s) y no vuelve. Los últimos 8 segundos van sobre silencio y tick de máquina. Nunca whoosh épico.
- **Texto en pantalla clave (kinetic):** "Cien mil al año compran un equipo." · "Vos no tenés ese equipo." · "USD 300–5.000/mes: la función, operada." · "No es la suite más barata."
- **CTA:** "La función de compras que todavía no podés contratar — empezá por el Diagnóstico de Arranque: en 2 semanas sabés cuánta plata tenés atrapada." (§9, paquete 1)
- **Claim-check:**
  - `USD 100.000/año` → §2.1 ③ + §12 del bible. **Rotulado en pantalla** como *precio de lista / ancla de mercado 07-2026*, nunca como cotización viva.
  - `USD 300–5.000/mes` → §2.2 fila de precio + §12; consistente con `package_specs.py` (Starter LatAm 250-300 … Retainer 4.500). **Verificar contra `package_specs.py`, no contra la tesis comercial** (§4 de la tesis tiene precios muertos).
  - "está tasado para una empresa que ya tiene un equipo" → §2.1 ③ (*"el arsenal completo para grandes empresas"*, *"medianas-grandes y grandes"*) + §7 ICP (*"sin equipo de data science ni presupuesto SAP"*). Es una afirmación sobre **para quién está tasado el producto**, no sobre lo que el producto hace.
  - "la función, operada por nosotros · vos aprobás" → §2.2 fila *¿quién lo opera?* (*"Kern + un operador que firma"*) + §5.2 (3 de 4 desenlaces requieren humano).
  - **Verificado contra §10:** ❌ no dice "reemplaza a tu equipo" — el ad dice justamente que **no hay equipo que reemplazar**, y ése es el punto. ❌ no compara función por función contra SAP IBP (no se afirma que Kern haga lo que hace SAP; se afirma para quién está tasado su precio). ❌ sin magia, sin "% de autonomía", sin "sin humano" (el `vos aprobás` está en cuadro), sin testimonios, sin Shopify/Amazon/MELI-inventario.

---

## 8. Qué NO cambia

Este documento **sólo reencuadra el paraguas de mensaje**. Todo lo demás del bible sigue vigente tal cual:

- **§2 — mapa competitivo.** Las 6 alternativas y la matriz maestra se mantienen íntegras; son el sustrato del judo de §2 de este documento. La línea killer del planificador (§2.1 ④) sigue siendo la mejor frase de **retención** — sobrevive el día que el cliente contrata a alguien, cosa que el marco de función no hace.
- **§3 — la marca.** Nombre, qué Kern NO es, los tres tonos y la tagline maestra no se tocan.
- **§5 — el producto.** El loop, el contrato "nunca desprotegido", las 3 garantías y "corre con o sin LLM" son la razón-para-creer de este mensaje, no un anexo.
- **§6, §7, §8, §9 — capacidades, ICP, ángulos de pauta y paquetes.** Sin cambios. La nota de honestidad de §6 (el Control Tower autónomo es plan 3.0, no construido) **refuerza** este marco: hoy Kern es el motor de producción de un operador humano, y eso es exactamente lo que vendemos.
- **§10 — claims prohibidos.** Intacta y soberana. Este documento agrega disciplina (§4.2 del bloque nuevo: nada de "3 de cada 4 veces", nada de "si no cita no entrega nada", nada de "nada toca tu sistema sin tu firma", nada de "SLA" fuera del Retainer), pero no relaja ni una sola prohibición.
- **§11, §12, §13, §14 — visual, números de rigor, banco de copy y mapa pieza→sección.** Sin cambios, salvo dos entradas de §14 que ahora apuntan acá: *"Carrusel automatización vs autonomía"* → **§4.2 reencuadrada**, y el ad de SAP IBP → **§7 de este documento**.

**Lo único que se retira:** la tabla "Automatización vs. Autonomía" de la §4.2 anterior. No porque sea falsa, sino porque su eje es de ingeniería interna y el comprador no lo puede sentir.

---

*Fin. Antes de usar una sola pieza con este marco: reconciliar precios contra `package_specs.py`, reconciliar la cantidad de fuentes, y tener firmada la Carta de Responsabilidad. Sin esas tres cosas, esto es un buen guion sobre una promesa vacía — y el primer cliente lo descubre en la primera factura, no en la primera reunión.*
