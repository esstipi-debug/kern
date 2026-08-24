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

**Estado del código verificado contra el commit `dc2eff5`** (rama
`fix/ci-gate-alcance-lint-y-cobertura`), el 2026-08-24.

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
| L1 | El ledger de leads se borra en cada deploy | ABIERTA | medir cualquier embudo |
| L2 | Shopify es custom app, no app del App Store | ABIERTA | distribución por Shopify |
| L3 | El billing es un stub sin webhook de Stripe | ABIERTA | cobro self-serve (L2 y el peldaño Alerts) |
| L4 | El addon de Odoo está en 17.0 y no corre en Odoo Online | ABIERTA | distribución por Odoo |
| F1 | El plazo de proveedor es imposible desde Shopify | **FRONTERA** | nada: no se cierra nunca |

---

## L1 — El ledger de leads se borra en cada deploy

**Teoría:** `knowledge::inventory_record_accuracy` — "Inventory Record Accuracy"
(Chopra & Meindl, cap. 12). Ajuste **moderado** y se dice como tal: el concepto
es de inventario, no de leads, pero el mecanismo es exactamente el mismo — un
sistema de registro cuyo estado grabado deja de corresponder con lo que
realmente pasó, y toda decisión que se apoye en él hereda el error. Arista
`conceptually_related_to`, no `cites`.

**Código:** `webapp/app.py::LEADS_DIR` (línea 126).

**Qué falta.** `LEADS_DIR = _REPO_ROOT / "webapp" / "_leads"` está fijo, sin
override de entorno. En la imagen de Fly eso es `/app/webapp/_leads`, sobre el
filesystem EFÍMERO de la Machine: **se borra en cada deploy**. El bloque `[env]`
de `fly.toml` ya documenta esta falla para otros cuatro almacenes (events,
autonomy, state y el ledger de precios), que se apuntaron al volumen `/data` y
quedaron arreglados. Éste es el quinto y el único que ni con `fly secrets` se
podía mover. La PR #269 lo arregla y está abierta.

**Por qué importa más que los otros cuatro.** Ese archivo es la **única prueba
que existe de que alguien usó el embudo**: una fila por scan, con `ts`, `source`,
`email`, `dataset`, `result` y `status`.

**Medición del 2026-08-24 sobre el archivo LOCAL** (el de producción es
irrecuperable por lo de arriba): 249 filas entre el 12-jul y hoy, **6 dominios
distintos y uno solo explica 149**, y **162 de 249 (65%) terminaron en
`qa_failed`**. O sea: el ledger legible es casi todo autotesteo, y de las
corridas reales dos de cada tres no pasan el gate de datos. Ninguna de las dos
cosas se puede confirmar en producción hasta que esto se cierre.

**Sonda:** la línea que asigna `LEADS_DIR` no menciona `environ` ni `getenv`.

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

## L3 — El billing es un stub sin webhook de Stripe

**Teoría:** `knowledge::supplier_onboarding_scf` — "SCF Supplier Onboarding"
(Khan, comunidad 68). Ajuste **moderado**: su racional nombra al onboarding como
la restricción que ata el tiempo de puesta en marcha, que es exactamente el rol
que juega acá el alta paga. Arista `conceptually_related_to`.

**Código:** `webapp/billing.py::create_checkout_session`.

**Qué falta.** El módulo lo declara en su propio docstring: *"it does NOT call
the Stripe API"*. `create_checkout_session` sólo resuelve una variable
`STRIPE_LINK_<SLUG>` configurada a mano y degrada a `mailto`. No hay handler de
webhook en todo `webapp/`, y verificado el 2026-08-24 **ningún `.py` de producto
menciona `stripe`** — sólo lo hacen cinco tests y las páginas que arman el CTA.
Sin ese evento no existe el disparador que daría de alta una credencial sola.

**Es el bloqueo aguas arriba de dos cosas a la vez:** de la brecha de capacidad 7
(onboarding self-serve) y de L2, porque la Billing API de Shopify necesita del
otro lado un cobro que hoy no existe.

**Sonda:** `webapp/billing.py` sigue declarando que no llama a Stripe, y no hay
ruta de webhook en `webapp/app.py`.

**Cerrada cuando** una prueba manda un evento de pago firmado a una ruta de
webhook, ésta lo verifica y termina llamando a `src/mcp_keys.py::McpKeyStore.issue`,
y una llamada inmediata a `POST /mcp/` con esa key devuelve 200 en vez del 401
que hoy emite `webapp/mcp_auth.py`.

---

## L4 — El addon de Odoo está en 17.0 y no corre en Odoo Online

**Teoría:** `knowledge::enterprise_resource_planning` — "Enterprise Resource
Planning (ERP)" (Chopra & Meindl, cap. 3). Ajuste **moderado**: el concepto es el
ERP como sistema, y la brecha es sobre el modelo de despliegue de ese ERP.
Arista `conceptually_related_to`. Secundario: `knowledge::erp_data_integration`
(Khan), la interfaz de integración, que es literalmente lo que el addon es.

**Código:** `odoo_addon/linchpin_dry_run/__manifest__.py`.

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

**Sonda:** la serie del manifest, y la presencia de la cláusula de
incompatibilidad con Odoo Online.

**Cerrada cuando** el manifest declara la serie que se soporta, una prueba la
contrasta contra la que el repo dice soportar, y la incompatibilidad con Odoo
Online está declarada en un lugar que una prueba puede leer — no sólo en prosa
del `description`.

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
| L3 | Billing stub sin webhook | `knowledge::supplier_onboarding_scf` | moderado | `conceptually_related_to` | `webapp/billing.py::create_checkout_session` |
| L4 | Addon Odoo 17.0 / sin Online | `knowledge::enterprise_resource_planning` | moderado | `conceptually_related_to` | `odoo_addon/linchpin_dry_run/__manifest__.py` |
| F1 | Plazo imposible desde Shopify | `knowledge::information_sharing_difficulty` | moderado | `conceptually_related_to` | `src/connectors/shopify_connect.py::PLAZO_AUSENTE` |
