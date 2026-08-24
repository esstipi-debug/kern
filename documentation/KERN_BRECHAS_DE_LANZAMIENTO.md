# Brechas de lanzamiento de Kern (Shopify, Odoo, Stocky)

Lo que falta para que Kern se venda **por un canal** y no de a un cliente por
vez. Documento propio de Kern: entra al grafo de libros como fuente interna y
**nunca puede salir como cita a un cliente** —
`scm_agent/knowledge.py::_INTERNAL_SOURCES` lo filtra en
`ground_citations_detailed`, el único chokepoint de citas, y
`tests/test_launch_gaps_graph.py` lo prueba de forma adversarial. Misma línea
que la fuente #28 (`KERN_BRECHAS_DE_CAPACIDAD.md`): un documento propio no
aparece al lado de Chopra & Meindl.

Es un documento de **build time**. Se ingiere con
`scripts/merge_launch_gaps.py` en un PR revisado, nunca en tiempo de consulta.

**Estado del código verificado contra `69b52d1` (main)**, el 2026-08-24, después
de mergear #269 y #258 — que cerraron dos de los cinco items originales.

## Cómo leer cada ficha

- **Teoría** — id real de un nodo que ya existe en `knowledge/scm-books/graph.json`,
  y que **sobrevive un rebuild** (no pertenece a ningún libro de
  `merge_l3_rebuild.REPLACE_STEMS`). Cuando no existe ningún concepto que ancle
  de verdad, la ficha lo dice y no hay arista. No se inventó ningún nodo.
- **Código** — `archivo::símbolo` que existe hoy.
- **Sonda** — cómo se MIDE si sigue abierta, sin intervención humana.
  `tests/test_launch_gaps_state.py` corre las cinco sondas y falla si el estado
  medido no coincide con el declarado acá. Cerrar una brecha rompe ese test a
  propósito: obliga a actualizar esta tabla en el mismo PR que la cierra.
- **Cerrada cuando** — una prueba observable, no una intención.

| # | Brecha | Estado | Bloquea |
|---|--------|--------|---------|
| L1 | El ledger de leads se borra en cada deploy | CERRADA | — |
| L2 | Shopify es custom app, no app del App Store | ABIERTA | distribución por Shopify |
| L3 | El billing es un stub sin webhook de Stripe | CERRADA | — |
| L4 | El addon de Odoo está en 17.0 | CERRADA | — |
| F2 | Odoo Online no acepta módulos de terceros | **FRONTERA** | nada: no se cierra nunca |
| F1 | El plazo de proveedor es imposible desde Shopify | **FRONTERA** | nada: no se cierra nunca |

---

## L1 — El ledger de leads se borraba en cada deploy — **CERRADA**

**Cerrada por la PR #269** (`b883f18`, mergeada el 2026-08-24). `LEADS_DIR` acepta
`LINCHPIN_LEADS_DIR` con el mismo patrón que `LEAD_REPORTS_DIR`, el default no
cambia, y `fly.toml` lo apunta a `/data/leads_ledger` — durable por defecto y no
por acordarse del paso 34 del checklist. Un test espeja el del otro almacén.

**Teoría:** `knowledge::inventory_record_accuracy` — "Inventory Record Accuracy"
(Chopra & Meindl, cap. 12). Ajuste **moderado** y se dice como tal: el concepto
es de inventario, no de leads, pero el mecanismo es exactamente el mismo — un
sistema de registro cuyo estado grabado deja de corresponder con lo que
realmente pasó, y toda decisión que se apoye en él hereda el error. Arista
`conceptually_related_to`, no `cites`.

**Código:** `webapp/app.py::LEADS_DIR` (línea 126).

**Qué faltaba.** `LEADS_DIR = _REPO_ROOT / "webapp" / "_leads"` estaba fijo, sin
override de entorno. En la imagen de Fly eso es `/app/webapp/_leads`, sobre el
filesystem EFÍMERO de la Machine: **se borra en cada deploy**. El bloque `[env]`
de `fly.toml` ya documenta esta falla para otros cuatro almacenes (events,
autonomy, state y el ledger de precios), que se apuntaron al volumen `/data` y
quedaron arreglados. Éste era el quinto y el único que ni con `fly secrets` se
podía mover.

**Por qué importa más que los otros cuatro.** Ese archivo es la **única prueba
que existe de que alguien usó el embudo**: una fila por scan, con `ts`, `source`,
`email`, `dataset`, `result` y `status`.

**Medición del 2026-08-24 sobre el archivo LOCAL**, tomada ANTES del arreglo
(el de producción hasta ese día era irrecuperable): 249 filas entre el 12-jul y hoy, **6 dominios
distintos y uno solo explica 149**, y **162 de 249 (65%) terminaron en
`qa_failed`**. O sea: el ledger legible es casi todo autotesteo, y de las
corridas reales dos de cada tres no pasan el gate de datos. Desde el próximo
deploy eso SÍ se puede medir en producción, que es todo el punto de la brecha.

**Sonda:** la SENTENCIA que asigna `LEADS_DIR` no menciona `environ` ni `getenv`.
La primera versión de esta sonda miraba sólo la primera línea y el arreglo real
puso el `os.environ.get(...)` en una continuación — habría seguido reportando
ABIERTA sobre una brecha cerrada. Corregido: lee hasta el paréntesis que cierra.

**Cerrada cuando** una prueba apunta `LEADS_DIR` a un directorio arbitrario por
variable de entorno, escribe un lead y lo lee de ahí — y el default sin esa
variable sigue siendo el de hoy.

---

## L2 — Shopify es custom app, no app del App Store

**Teoría:** **ninguna.** No existe en el grafo ningún concepto sobre distribuir
software por el marketplace de una plataforma. Se revisaron y se rechazaron por
forzados `knowledge::omnichannel_retailing` y
`knowledge::multi_channel_distribution` (venta multicanal de PRODUCTO, no
distribución de software; el segundo además vive en un libro que el rebuild
re-extrae, así que ni siquiera sería durable). **Esta brecha no tiene ancla de
teoría y no se le fabricó una.** Queda anclada sólo al código.

**Código:** `src/connectors/shopify_connect.py::check_connection`.

**Qué falta.** La cabecera del propio módulo lo dice: el comerciante crea una
app en su admin y saca un Admin API access token; *"NO es una app del App Store:
eso exige OAuth, app embebida, los tres webhooks obligatorios de GDPR y Billing
API, y nada de eso está en esta ruta."* Verificado el 2026-08-24: `webapp/app.py`
no tiene **ninguna** ruta que coincida con `oauth`, `webhook`, `auth/callback`,
`install` ni `stripe`.

**No es lo próximo que hay que construir.** `documentation/MONETIZATION_BRIEF.md`
pone la app real en **Fase 2**, con decisión aparte y sólo con los números de
Fase 1 en mano. La Fase 0 es el Chequeo Stocky manual, sin desarrollo, con
criterio de kill explícito: *0 ventas con ≥300 contactos → matar el ángulo
Shopify*. Esta ficha existe para que el trabajo esté dimensionado cuando esa
decisión llegue, no para adelantarla.

**Sonda:** ninguna ruta de `webapp/app.py` menciona oauth / webhook /
auth/callback / install.

**Cerrada cuando** una prueba recorre el alta de una tienda de punta a punta
contra un stub: redirección OAuth, callback que persiste el token, los tres
webhooks de GDPR (`customers/data_request`, `customers/redact`, `shop/redact`)
respondiendo 200 con HMAC verificado, y un cargo creado por Billing API.

---

## L3 — El billing no tenía webhook de pago — **CERRADA**

**Cerrada en esta misma rama.** `POST /webhooks/stripe` verifica la firma de
Stripe y emite la credencial MCP de la compra, atada al email del comprador y a
la referencia del pago. Un evento firmado hace que `POST /mcp/` deje de devolver
401 — que es literalmente el *cerrada cuando* de abajo.

**Sin SDK, y no por ahorro.** La firma es HMAC-SHA256 sobre `"{t}.{cuerpo
crudo}"`: `hmac` y `hashlib` alcanzan. Meter el SDK agregaría una dependencia con
acceso a red en la ruta que recibe dinero, y el Dockerfile de producción no la
instala. La promesa que `billing.py` ya hacía se mantiene.

**Idempotencia por esquema, no por memoria.** Stripe reintenta hasta recibir un
2xx y reenvía a mano desde su panel. Un índice único parcial sobre `payment_ref`
hace que el segundo intento **no pueda** acuñar una segunda credencial: lo impide
SQLite, no el código. Un proceso que recuerda lo ya atendido olvida en cada
deploy — que es justo el modo de falla que L1 acababa de cerrar.

**Lo que NO se construyó, y por qué no es esta brecha.** `create_checkout_session`
sigue sin llamar a la API de Stripe, y está bien: con un Payment Link el
comprador paga y Stripe dispara `checkout.session.completed` igual. La mitad
saliente sólo hace falta el día que el precio deba calcularse por visitante
(`src/commercial_pricing.quote_price`), que es una feature de pricing, no de
cobro. **Lo que sí falta es la entrega**: no hay envío de correo en el repo, así
que la credencial se emite y queda esperando que un humano la mande. Eso es lo
que mantiene abierta la brecha de capacidad 7 (onboarding self-serve), no ésta.

**Teoría:** `knowledge::supplier_onboarding_scf` — "SCF Supplier Onboarding"
(Khan, comunidad 68). Ajuste **moderado**: su racional nombra al onboarding como
la restricción que ata el tiempo de puesta en marcha, que es exactamente el rol
que juega acá el alta paga. Arista `conceptually_related_to`.

**Código:** `webapp/stripe_webhook.py::verify_signature`.

**Qué faltaba.** El módulo lo declaraba en su propio docstring: *"it does NOT call
the Stripe API"*. `create_checkout_session` sólo resuelve una variable
`STRIPE_LINK_<SLUG>` configurada a mano y degrada a `mailto`. No hay handler de
webhook en todo `webapp/`, y verificado el 2026-08-24 **ningún `.py` de producto
menciona `stripe`** — sólo lo hacen cinco tests y las páginas que arman el CTA.
Sin ese evento no existe el disparador que daría de alta una credencial sola.

**Era el bloqueo aguas arriba de dos cosas a la vez:** de la brecha de capacidad 7
(onboarding self-serve) y de L2, porque la Billing API de Shopify necesita del
otro lado un cobro. Con esto, L2 deja de estar bloqueada por abajo.

**Sonda:** existe una ruta de webhook en `webapp/app.py` Y un módulo que verifica
la firma. La primera versión de esta sonda también exigía que `billing.py` dejara
de declarar *"does NOT call the Stripe API"* — y eso medía la cosa equivocada:
esa frase sigue siendo cierta y correcta con un Payment Link. La brecha era el
webhook entrante, no la llamada saliente.

**Cerrada cuando** una prueba manda un evento de pago firmado a una ruta de
webhook, ésta lo verifica y termina llamando a `src/mcp_keys.py::McpKeyStore.issue`,
y una llamada inmediata a `POST /mcp/` con esa key devuelve 200 en vez del 401
que hoy emite `webapp/mcp_auth.py`.

---

## L4 — El addon de Odoo estaba en 17.0 — **CERRADA**

**Cerrada por la PR #258** (`69b52d1`, mergeada el 2026-08-24), verificada
instalando el módulo en una base Odoo 19.0-20260817 limpia en Docker, no leyendo
el código. Las dos series conviven bajo el mismo nombre técnico
(`odoo_addon/17.0/kern_dry_run/` y `19.0/`), que es lo que la Apps Store exige
para que dos series sean la misma app.

El segundo techo que esta ficha mezclaba —Odoo Online— **no era una brecha**: es
una restricción de la plataforma. Se separó a F2.

**Teoría:** `knowledge::enterprise_resource_planning` — "Enterprise Resource
Planning (ERP)" (Chopra & Meindl, cap. 3). Ajuste **moderado**: el concepto es el
ERP como sistema, y la brecha es sobre el modelo de despliegue de ese ERP.
Arista `conceptually_related_to`. Secundario: `knowledge::erp_data_integration`
(Khan), la interfaz de integración, que es literalmente lo que el addon es.

**Código:** `odoo_addon/19.0/kern_dry_run/__manifest__.py`.

**Qué falta, y son dos techos distintos.**

1. **Versión.** El manifest declara `"version": "17.0.1.0.0"`. La PR #258 lo
   porta a 19 y está abierta sin mergear.
2. **Odoo Online.** El propio manifest lo dice: *"Requires Odoo.sh (private
   repository) or an on-premise installation - not compatible with Odoo Online,
   since it includes Python code."* Eso deja afuera al segmento más grande de
   Odoo, y **no se arregla portando versiones**: es una restricción de la
   plataforma para módulos con código Python. Cerrar el techo 1 no toca el 2.

**Además el addon es un dry run**: manda historial de ventas confirmadas,
muestra recomendaciones y **no escribe nada de vuelta**. Vender ejecución por
Odoo es la brecha de capacidad 3, no ésta.

**Sonda:** la serie que declara `odoo_addon/19.0/kern_dry_run/__manifest__.py`.

**Cerrada cuando** el manifest declara la serie que se soporta, una prueba la
contrasta contra la que el repo dice soportar, y la incompatibilidad con Odoo
Online está declarada en un lugar que una prueba puede leer — no sólo en prosa
del `description`.

---

## F2 — FRONTERA: Odoo Online no acepta módulos de terceros

**Esto no es una brecha y no se cierra nunca.** Estaba mezclado dentro de L4, que
lo hacía parecer trabajo pendiente; separarlo es la mitad del valor de esta
revisión.

**Teoría:** `knowledge::erp_data_integration` — "ERP Data Integration Interface"
(Khan). Ajuste **moderado**: el módulo es exactamente eso, y la frontera es que
esa interfaz no puede vivir dentro del SaaS. Arista `conceptually_related_to`.

**Código:** `src/connectors/odoo_connect.py::check_connection` — la ruta que sí
cubre Odoo Online.

**El hecho.** Odoo Online (el SaaS de odoo.com) no admite módulos de terceros con
código Python. Ninguna versión del addon va a correr ahí, y portarlo de serie no
lo cambia.

**Y por qué no bloquea el canal.** El manifest de la serie 19.0 lo dice de frente:
*"On Odoo Online you do not need this module to get the same analysis"* — la
External API que `src/connectors/odoo_connect.py` ya habla cubre Odoo Online,
Odoo.sh y on-premise con una sola ruta, de sólo lectura, con cuatro campos. O sea
el addon es una comodidad para quien puede instalarlo, no el único camino.

**Se rompe cuando** alguien vuelve a tratar esto como trabajo pendiente, o cuando
la ruta de la External API deja de existir y el addon queda como único camino.

---

## F1 — FRONTERA: el plazo de proveedor es imposible desde Shopify

**Esto no es una brecha y no se cierra nunca.** Está acá para que nadie la
"resuelva".

**Teoría:** `knowledge::information_sharing_difficulty` — "Breakdown of
Information Sharing and Coordination" (Khan). Ajuste **moderado**: el dato no
existe del otro lado, y ninguna cantidad de trabajo del lado de acá lo crea.
Arista `conceptually_related_to`.

**Código:** `src/connectors/shopify_connect.py::PLAZO_AUSENTE`.

**El hecho.** Shopify no tiene órdenes de compra ni recepciones de proveedor: el
objeto no existe en la Admin API. Por lo tanto el plazo real de proveedor — la
salida #1 del embudo, la que `src/connectors/odoo_connect.py` sí mide — **es
imposible** desde datos de Shopify. El módulo se niega a derivarlo por ninguna
vía y lo declara ausente con motivo.

**Lo que define para el canal Shopify:** ahí se puede vender análisis de venta,
clasificación y precio. Reposición con plazo real, no — salvo que el comerciante
aporte el dato por otra vía.

**Se rompe cuando** alguien hace que Kern devuelva un plazo de proveedor a
partir de datos de Shopify. La prueba fija que `PLAZO_AUSENTE` sigue existiendo
y que el módulo sigue declarando la ausencia en vez de rellenarla — el contrato
de la casa es que la ausencia se declara y nunca se rellena.

---

## Tabla de anclaje

| # | Brecha | Teoría (id real) | Ajuste | Arista | Código |
|---|--------|------------------|--------|--------|--------|
| L1 | Ledger de leads efímero | `knowledge::inventory_record_accuracy` | moderado | `conceptually_related_to` | `webapp/app.py::LEADS_DIR` |
| L2 | Shopify sin App Store | — *(no existe ancla)* | — | — | `src/connectors/shopify_connect.py::check_connection` |
| L3 | Billing sin webhook de pago | `knowledge::supplier_onboarding_scf` | moderado | `conceptually_related_to` | `webapp/stripe_webhook.py::verify_signature` |
| L4 | Addon Odoo en 17.0 | `knowledge::enterprise_resource_planning` | moderado | `conceptually_related_to` | `odoo_addon/19.0/kern_dry_run/__manifest__.py` |
| F2 | Odoo Online sin terceros | `knowledge::erp_data_integration` | moderado | `conceptually_related_to` | `src/connectors/odoo_connect.py::check_connection` |
| F1 | Plazo imposible desde Shopify | `knowledge::information_sharing_difficulty` | moderado | `conceptually_related_to` | `src/connectors/shopify_connect.py::PLAZO_AUSENTE` |
