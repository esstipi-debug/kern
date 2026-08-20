# Port del addon Odoo: 17.0 -> 19.0

`kern_dry_run` (antes `linchpin_dry_run`). Fecha: 2026-08-19.

**El port en si toca 3 archivos** (`__manifest__.py`, `security/kern_security.xml`,
`static/description/index.html`), +43/-12 lineas. El Python (`wizards/`, `models/`,
`lib/mcp_client.py`) y las vistas del wizard, del menu y de ajustes quedan
**byte-identicos** entre las dos series. Se verifica solo:

```bash
diff -rq odoo_addon/17.0/kern_dry_run odoo_addon/19.0/kern_dry_run
```

Todo lo demas del diff de esta rama es el rename a Kern y la reestructura en
`17.0/` + `19.0/`, que son decisiones del operador, no del port (seccion 3).

---

## 1. Que cambio, y por que

### 1.1 `res.groups.category_id` -> `privilege_id` — ESTO ROMPIA LA INSTALACION

Odoo 19 saco `res.groups` de `res_users.py` a un archivo propio y **elimino el campo
`category_id`**, reemplazandolo por `privilege_id`, un Many2one al modelo nuevo
`res.groups.privilege` — que es el que ahora lleva el link a `ir.module.category`.

| | 17.0 | 19.0 |
|---|---|---|
| archivo | [`res_users.py`](https://github.com/odoo/odoo/blob/17.0/odoo/addons/base/models/res_users.py) | [`res_groups.py`](https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/models/res_groups.py) |
| campo | `category_id = fields.Many2one('ir.module.category', string='Application', index=True)` | `privilege_id = fields.Many2one('res.groups.privilege', string='Privilege', index=True)` — **no hay `category_id`** |
| modelo nuevo | — | [`res_groups_privilege.py`](https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/models/res_groups_privilege.py) |

Un `<field name="category_id"/>` sobre un `res.groups` en 19 no es un warning: es un
campo inexistente en un registro XML de datos, o sea fallo de instalacion.

La forma correcta la muestra el propio core en
[`addons/stock/security/stock_security.xml`](https://github.com/odoo/odoo/blob/19.0/addons/stock/security/stock_security.xml):
un record `res.groups.privilege` mas `privilege_id` en el grupo. Es lo que se hizo.

**El privilege es propio de Kern a proposito, no el de stock.** Un privilege se
renderiza como UN solo select en el formulario de usuario, asi que colgar el grupo del
`stock.res_groups_privilege_inventory` lo volveria mutuamente excluyente con Inventory
User / Administrator — exactamente lo contrario de lo que pide el `comment` del grupo
("Deliberately separate from Inventory Administrator"). El privilege propio lo deja como
un opt-in independiente, con placeholder "No" cuando no esta otorgado.

Efecto colateral: el grupo pasa a mostrarse como `Kern / User` (el `full_name` de 19 es
`privilege / grupo`), antes era `Linchpin User`. `index.html` dice lo mismo ahora.

### 1.2 `"category"` del manifest: `Inventory/Inventory` -> `Supply Chain/Inventory`

No es cosmetico. Odoo **genera** el xmlid de categoria a partir de este string
([`odoo/modules/db.py`](https://github.com/odoo/odoo/blob/19.0/odoo/modules/db.py),
`create_categories`), y en 19 el core movio Inventory bajo Supply Chain
([`stock/__manifest__.py`](https://github.com/odoo/odoo/blob/19.0/addons/stock/__manifest__.py)
dice `'category': 'Supply Chain/Inventory'`; en
[17.0](https://github.com/odoo/odoo/blob/17.0/addons/stock/__manifest__.py) decia
`'Inventory/Inventory'`). Consecuencia: **`base.module_category_inventory_inventory` ya
no existe en 19** — y el modulo lo referenciaba. Se usa
`base.module_category_supply_chain`, que si esta en
[`ir_module_category_data.xml`](https://github.com/odoo/odoo/blob/19.0/odoo/addons/base/data/ir_module_category_data.xml).

> Matiz: en una base **migrada** desde 17 la fila `ir_model_data` vieja sobrevive (se
> crea con `noupdate=True`), asi que el `ref` resolveria igual. No salva nada: el campo
> `category_id` ya no existe. En instalacion limpia no existe ninguno de los dos.

### 1.3 `"version"`: `17.0.1.0.0` -> `19.0.1.0.0` — falla en SILENCIO si no se toca

No es convencion de numeracion. En
[`odoo/modules/module.py`](https://github.com/odoo/odoo/blob/19.0/odoo/modules/module.py):

```python
if manifest['installable'] and not check_version(str(manifest['version']), should_raise=False):
    _logger.warning("The module %s has an incompatible version, setting installable=False", module)
    manifest['installable'] = False
```

y `check_version` solo devuelve True si la version arranca con la serie corriendo. O
sea: con `17.0.1.0.0` en un Odoo 19 el modulo **desaparece de la lista de instalables** y
lo unico que queda es un warning en el log. `adapt_version` ademas exige entre 2 y 5
partes numericas.

### 1.4 `index.html`: dos violaciones de las reglas vigentes del Apps Store

Ambas existian ya en 17.0 y estan corregidas en las dos series:

1. **Enlace externo.** La pagina de descripcion solo puede enlazar a archivos dentro de
   `static/description` (mas YouTube canonico / MS Teams). Habia un `<a>` a
   `linchpin.fly.dev/paquetes-docs/partner-odoo.md`. Eliminado.
2. **Bloque promocional.** La descripcion no puede incluir promociones ni terminos
   comerciales. El bloque "For partners" listaba 20% de revenue share y un fee mensual de
   white-label. Reemplazado por una frase neutra, sin precios ni link. Tambien se saco el
   "available directly, outside the Odoo Apps Store".

Detalle operativo: un error de manifest **despublica todos los modulos del repositorio**,
no solo el roto.

### 1.5 La nota de Odoo Online, ampliada (manifest y `index.html`)

Antes decia solo que no se puede. Ahora dice que **el cliente de Online no queda sin
salida**: Kern lee la base desde afuera por la External API, que es la unica ruta que
cubre Online, Odoo.sh y on-premise con una sola integracion, sin instalar modulo.

Tres precisiones que salieron de la
[doc oficial 19.0](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html)
y que **corrigen el enunciado original de la tarea**:

- En 19 se llama **External JSON-2 API** (`POST /json/2`, API key como bearer token).
  "XML-RPC" ya no es el nombre vigente.
- XML-RPC y JSON-RPC (`/xmlrpc`, `/xmlrpc/2`, `/jsonrpc`) siguen funcionando pero estan
  **deprecados, con remocion prevista para Odoo 22 (otonio 2028)**. Mandar a un cliente
  nuevo a XML-RPC seria mandarlo a una via muerta.
- **"Access to data via the external API is only available on Custom Odoo pricing
  plans"** — no esta en One App Free ni Standard. Sin esta linea la alternativa suena
  universal y no lo es; es justo el dato que evita que alguien planifique sobre algo que
  su plan no incluye.

---

## 2. Evaluado y descartado: breaking changes que NO aplican

Cada uno verificado contra fuente, no de memoria. **Odoo 18.0 no aporta ningun cambio que
toque este modulo** — todo lo que rompe es de 19.0.

| Cambio | Serie | Por que no aplica |
|---|---|---|
| [`<tree>` -> `<list>`](https://www.odoo.com/documentation/18.0/developer/reference/user_interface/view_architectures.html) | 18.0 | El modulo solo trae vistas `<form>`. No hay ningun `<tree>`. |
| [`view_mode` default `tree,form` -> `list,form`](https://www.odoo.com/documentation/18.0/developer/reference/backend/actions.html) | 18.0 | La accion del wizard devuelve `"view_mode": "form"`. `form` no cambio. |
| [`user_has_groups()` -> `self.env.user.has_group()`](https://www.odoo.com/documentation/18.0/developer/reference/backend/security.html) | 18.0 | No se llama a ninguna de las dos; el acceso se controla por `ir.model.access.csv` y `groups=` en el menuitem. |
| `res.users.groups_id` -> `group_ids`; `res.groups.users` -> `user_ids` | 19.0 | El XML del modulo no asigna usuarios al grupo. |
| `record._cr` / `._uid` / `._context` deprecados | 19.0 | El wizard usa `self.env` en todos los casos. |
| `read_group` -> `_read_group` / `formatted_read_group` | 19.0 | El wizard usa `search()`, no agrupa. |
| `check_access_rights` / `check_access_rule` -> `check_access` / `has_access` | 19.0 | No se llaman. |
| `_sql_constraints` -> `models.Constraint` | 19.0 | El modulo no declara constraints. |
| [Estructura `<app>` / `<block>` / `<setting>` de ajustes](https://github.com/odoo/odoo/blob/19.0/addons/stock/views/res_config_settings_views.xml) | 19.0 | **Sin cambios.** `<app name="stock">` sigue existiendo con el mismo tag, `stock.res_config_settings_view_form` sigue siendo el xmlid, `<setting>` sigue tomando `id`/`string`/`help` (`string` NO se renombro a `label`) y `<div class="content-group">` lo sigue usando el core. `views/res_config_settings_views.xml` no se toco. |
| `password="True"` en un `<field>` | 19.0 | Sigue soportado: [`char_field.js`](https://github.com/odoo/odoo/blob/19.0/addons/web/static/src/views/fields/char/char_field.js) hace `isPassword: exprToBoolean(attrs.password)`. |
| `stock.menu_stock_inventory_control` | 19.0 | Sigue existiendo, mismo id, mismo padre, misma secuencia, en [`stock_menu_views.xml`](https://github.com/odoo/odoo/blob/19.0/addons/stock/views/stock_menu_views.xml). |
| `<menuitem groups="...">` | 19.0 | Sigue funcionando: `odoo/tools/convert.py` mapea el atributo XML al campo renombrado. |
| `_transient_max_hours` | 19.0 | Sigue siendo atributo de clase valido de `TransientModel`, ahora en [`odoo/orm/models_transient.py`](https://github.com/odoo/odoo/blob/19.0/odoo/orm/models_transient.py) como `lazy_classproperty`; un float plano en la subclase lo sigue pisando. |
| `from odoo import _, fields, models` | 19.0 | Sigue resolviendo: `odoo/init.py` hace `from .tools.translate import _, _lt` y `odoo._ = _`. El core 19 lo sigue usando. Sin deprecacion. |
| Formato de `ir.model.access.csv` | 18.0 / 19.0 | Mismas 8 columnas, misma semantica. |
| `config_parameter=` en `res.config.settings` | 19.0 | Sin cambios. |
| `OPL-1` como license | 19.0 | Sigue siendo valor aceptado. |
| `external_dependencies` validado como PEP 508 | 19.0 | El modulo no declara ninguna: `lib/mcp_client.py` es stdlib puro a proposito. |
| `depends` | 19.0 | `["base","stock","sale"]`: los tres siguen existiendo y no aparecio ningun modulo intermedio requerido. |

### Campos que el wizard lee — verificados uno por uno contra el codigo de 19.0

| Modelo | Campo | 19.0 | Nota |
|---|---|---|---|
| `sale.order.line` | `product_uom_qty`, `price_unit`, `order_id`, `product_id` | los cuatro existen | En la misma clase se renombro `product_uom` -> `product_uom_id`, pero el modulo **no lo usa**. |
| `sale.order` | `state`, `date_order` | existen | `SALE_ORDER_STATE` sigue siendo exactamente `draft/sent/sale/cancel`: el dominio `('order_id.state','=','sale')` sigue siendo correcto. |
| `product.product` | `default_code`, `standard_price` | existen | `standard_price` sigue `company_dependent=True`, `groups="base.group_user"`. |
| `product.template` | `seller_ids` | existe | `product.product` la hereda por `_inherits`, igual que en 17. |
| `product.supplierinfo` | `delay` | existe | **El caso a mirar con lupa.** La etiqueta cambio de `'Delivery Lead Time'` (17) a `'Lead Time'` (19), pero el campo, el `help` y la semantica son identicos: sigue siendo el plazo **declarado** entre confirmacion de la OC y recepcion. Y el `_order` de `product.supplierinfo` sigue siendo `'sequence, min_qty DESC, price, id'`, asi que `seller_ids[:1]` sigue siendo el proveedor primario. Cambio de etiqueta, no de significado. |

### Lo que no se pudo confirmar contra fuente

- **Odoo no publica ninguna guia oficial de breaking changes ni de migracion de modulos
  para 18.0 ni para 19.0.** `developer/reference/upgrades/` solo documenta el tooling de
  upgrade scripts. Todo lo de arriba salio de diffear las ramas `17.0`/`18.0`/`19.0` del
  codigo y las paginas de doc, no de un changelog del proveedor.
- **La pagina oficial de seguridad de 19.0 esta desactualizada**: sigue documentando
  `category_id` como campo de `res.groups` y no menciona `res.groups.privilege`. Confiar
  en la prosa oficial en vez del codigo habria dejado pasar el cambio que rompe la
  instalacion.
- La **guia de migracion de la OCA para 19.0 no existe todavia**.
- Odoo 19 trae un CLI oficial de reescritura, `odoo-bin upgrade_code --from 17.0 --to 19.0`
  ([`odoo/upgrade_code`](https://github.com/odoo/odoo/tree/19.0/odoo/upgrade_code)). **Se
  corrio como cross-check y no propone un solo cambio en ninguno de los dos arboles** — ni
  siquiera en el 17.0 sin portar, que es el que NO instala en 19. Ver seccion 4: la
  herramienta oficial, sola, habria dejado el modulo roto.
- **No hay medidas oficiales en pixeles** para icono, cover ni capturas del Store.

---

## 3. Decisiones del operador (no del port)

Ambas resueltas por el duenio en esta sesion.

**Rename a Kern, ahora.** `HANDOFF.md` item 3 pedia hacerlo "en la PROXIMA version
funcional, no ahora" — este port *es* esa version. Y `GTM_SUBMISSIONS.md` mostraba los
pasos de publicacion como pendientes: **el modulo nunca llego a publicarse**, asi que no
hay instalaciones que romper y no hace falta hook de migracion. `CLAUDE.md` y `HANDOFF.md`
quedaron actualizados en el mismo commit, como pide `CLAUDE.md`.

**Las dos series en paralelo**, `odoo_addon/17.0/` y `odoo_addon/19.0/`, con el **mismo
nombre tecnico** `kern_dry_run` en las dos: el Store trata un modulo como *la misma app* en
varias versiones solo si el directorio se llama igual en cada rama.

**Tres identificadores siguen diciendo "linchpin" a proposito**, porque son de cable, no de
marca — cambiarlos rompe la integracion:

| Identificador | Por que |
|---|---|
| `https://linchpin.fly.dev` | Es la app de Fly desplegada (`fly.toml: app = "linchpin"`). No existe dominio Kern; `HANDOFF.md` item 2 decide mantenerlo hasta tener dominio propio. |
| `linchpin_inventory_optimize` | Nombre de la tool registrado server-side (`tests/test_mcp_server.py`). |
| `lpk_` | Prefijo de API key fijado a proposito en `src/mcp_keys.py:27` para que las claves ya emitidas sigan funcionando. |

> Consecuencia a tener presente: la ficha dira Kern y la URL dira `linchpin.fly.dev`. Es
> coherente con la decision de infra ya tomada, pero es una inconsistencia visible para el
> comprador. Se cierra cuando se ejecute el item 2 de `HANDOFF.md`.

---

## 4. Prueba de instalacion — reproducible

Un Odoo 19 limpio en Docker, base nueva, el modulo instalado de verdad.

```bash
cd odoo_addon/test
docker compose run --rm odoo odoo -d kern_test -i kern_dry_run --stop-after-init --log-level=warn
```

Verificacion post-instalacion (grupo/privilege, menu, ACL, arch de ajustes renderizado,
semantica de campos sobre datos demo, guard SSRF y wizard punta a punta):

```bash
docker compose run --rm -T odoo odoo shell -d kern_test --log-level=warn < verify_install.py
```

Para levantarlo y mirarlo a mano en `http://localhost:8069`:

```bash
docker compose up
```

`db` usa `tmpfs`, asi que cada `docker compose down` deja la base limpia. Para bajar todo:
`docker compose down -v`.

### Resultado (ejecutado, 2026-08-19)

Imagen `odoo:19` = **Odoo Server 19.0-20260817**, Postgres 16.15, base creada de cero.
Instalacion: **exit 0, sin errores**.

```
=== 1. module state ===
  PASS  module installed: installed
=== 2. Odoo 19 res.groups.privilege wiring (the actual breaking change) ===
  PASS  full_name='Kern / User' privilege='Kern' category='Supply Chain' groups_in_privilege=1
=== 3. menu + ACL ===
  PASS  menu: Inventory/Products/Kern Inventory Analysis
  PASS  ACL row: ['kern.analysis.wizard.kern.user']
=== 4. settings view actually rendered into stock's Inventory app ===
  PASS  both fields and the <setting> block present in the rendered arch
=== 5. FIELD SEMANTICS: known fixture -> exact expected row ===
  PASS  row == expected exactly:
        {'date': '2026-03-04', 'product_id': 'KERN-TEST-01', 'quantity': 7.0,
         'unit_cost': 12.5, 'lead_time_days': 9}
=== 6. SSRF guard must still reject private targets (NOT weakened) ===
  PASS  http://example.com        -> blocked (must start with https://)
        https://localhost         -> blocked (private/internal)
        https://127.0.0.1         -> blocked (private/internal)
        https://169.254.169.254   -> blocked (cloud metadata)
        https://10.0.0.1          -> blocked (private/internal)
=== 7. end-to-end wizard with the HTTP transport stubbed ===
  PASS  state=done summary='stub OK over 1 rows'
        report shown='## Reorder policy'
        client got base_url='https://linchpin.fly.dev' label='My Company'
        action -> view_mode='form' target='new'
=== 8. module writes NOTHING outside its own transient model ===
  PASS  only self.write() on the transient wizard
ALL CHECKS PASSED
```

**El chequeo 5 es el que importa.** La imagen oficial de Odoo crea la base **sin datos
demo**, asi que en vez de datos demo se arma un fixture con valores conocidos y se exige
que `_collect_rows()` devuelva **exactamente** esos: cantidad 7.0, costo 12.5, plazo 9 dias,
fecha 2026-03-04, SKU `KERN-TEST-01`. El producto tiene **dos** proveedores a proposito, uno
con `sequence=1, delay=9` y otro con `sequence=50, delay=99`: que salga 9 y no 99 prueba que
`seller_ids[:1]` sigue resolviendo al proveedor primario en 19. Si `standard_price`,
`product_uom_qty`, `supplierinfo.delay` o `date_order` hubieran cambiado de significado, los
numeros no coincidirian — que es exactamente el modo de falla silenciosa que se buscaba.

Chequeos extra sobre la misma base:

```
wizard form compiles: 1247 bytes
   history_days / result_summary / result_markdown / action_run_analysis : presentes
   widget="text" / special="cancel" / invisible="state != 'draft'"        : presentes
menu visible to group member : True
menu visible to non-member   : False
member can create the wizard : True
outsider blocked by ACL      : AccessError
```

Y la serie 17.0, para confirmar que el rename no la rompio (`ODOO_SERIES=17`, imagen
`odoo:17` = 17.0-20260803):

```
17.0 module state: installed | version: 17.0.1.0.0
17.0 group: Inventory / Kern User | category: Inventory
17.0 menu: Inventory/Products/Kern Inventory Analysis
```

```bash
ODOO_SERIES=17 docker compose run --rm odoo odoo -d kern17_test -i kern_dry_run --stop-after-init --log-level=warn
```

### Cross-check con la herramienta oficial de Odoo

`odoo-bin upgrade_code` es el reescritor de codigo que Odoo 19 trae para migrar fuentes.
Corrido sobre copias de los dos arboles:

```bash
docker run --rm --user root --entrypoint /usr/bin/odoo -v "<padre-del-modulo>:/w" odoo:19   upgrade_code --from 17.0 --to 19.0 --addons-path /w
```

| arbol | cambios propuestos |
|---|---|
| `17.0/kern_dry_run` (sin portar, **no instala en 19**) | ninguno |
| `19.0/kern_dry_run` (portado) | ninguno |

**El cero del arbol 17.0 es el dato interesante: la herramienta oficial no detecta nada, y
ese arbol no instala.** `upgrade_code` solo aplica las reescrituras mecanicas para las que
tiene script (renombres de API en Python, `<tree>`/`<list>` en vistas); un `<field
name="category_id"/>` dentro de un `<record model="res.groups">` no es ninguna de ellas.
Sirve de corroboracion de que el modulo no tiene deuda de API en Python — no de que este
portado.

> El primer intento dio "cero cambios" **porque la herramienta ni siquiera llego a los
> archivos**: `--addons-path` tiene que apuntar al *padre* del modulo, no al modulo. Se
> detecto plantando un canario (`<tree>` en una vista, que `upgrade_code` si reescribe) y
> comprobando que no se tocaba. Con el path corregido el canario se reescribe y el modulo
> aparece listado; recien ahi el cero significa algo.

### Suite del repo

`ruff check packs src tests examples` limpio, y `pytest` completo: **5446 passed, 30
skipped, 0 failed** (14m55s), con los 15 tests de `lib/mcp_client.py` verdes tras el rename.

### Lo que NO se probo, y por que

- **La llamada real a `linchpin.fly.dev` no se ejecuto.** No hay ninguna API key en este
  entorno (el store de claves vive en el volumen de Fly, no en el repo) y no correspondia
  emitir una contra produccion. En su lugar el chequeo 7 stubea `KernMcpClient` en el
  limite del transporte HTTP: se verifica que el wizard arma las filas, invoca la tool con
  el nombre correcto (`linchpin_inventory_optimize`), guarda el resultado y **muestra la
  recomendacion** (`state=done`, summary y markdown poblados). Lo unico sin ejercitar es el
  round-trip de red, que `lib/mcp_client.py` ya cubre con 15 tests unitarios
  (`tests/test_odoo_addon_mcp_client.py`, todos verdes tras el rename). Para cerrarlo con
  una clave real: `docker compose up`, cargar la key en Ajustes y correr el wizard.
- **No se tomaron capturas de la UI.** El servidor levanta y sirve (`/web/login` -> HTTP
  303, selector de base -> 200), y las vistas se verificaron compilando el arch por ORM,
  que es evidencia mas fuerte que una captura. No tipee credenciales en el formulario de
  login, ni siquiera las de un contenedor descartable. Para mirarlo a ojo:
  `docker compose up` y entrar a `http://localhost:8069`.

### Ruido conocido durante la instalacion, que NO es de este modulo

```
<string>:38: (ERROR/3) Unexpected indentation.
<string>:43: (WARNING/2) Block quote ends without a blank line; unexpected unindent.
```

Son de docutils parseando la descripcion RST de un modulo **del core**, no de este.
Tres evidencias: (1) pasar las descripciones de 17.0 y 19.0 por `publish_string` no produce
ni un mensaje; (2) el error cae en la linea 38 en las dos series, aunque sus descripciones
tienen largos distintos (25 vs 34 lineas) — no puede ser el mismo texto; (3) se reproduce
identico con `-i stock` y con `-i sale` en bases donde `kern_dry_run` no esta instalado,
mientras que `-i base` solo no lo produce.

---

## 5. Pendiente antes de publicar

Verificado contra las reglas vigentes del Apps Store. Ninguno es codigo; todos son decision
del duenio:

- **`"support"`** falta en el manifest. Es el mail de reclamos, de facto obligatorio para
  apps de pago (se muestra solo a compradores). No lo complete por mi cuenta: publicar un
  mail es una decision, no un default.
- **Capturas de pantalla.** Una app de pago sin capturas de sus funcionalidades puede ser
  despublicada. `index.html` hoy no tiene ninguna.
- **Cover image.** `"images"` apunta al icono; el Store los trata como cosas distintas y el
  ranking de la ficha penaliza "missing cover image".
- **`"name"`.** "Kern Inventory AI" entra en el limite de 25 caracteres, pero la guia pide
  evitar el nombre de la empresa en el titulo. Riesgo bajo.
- **Politica de privacidad.** Las reglas piden enlazarla y a la vez prohiben enlaces
  externos en la descripcion. La salida limpia es un archivo dentro de
  `static/description/`. Hoy la divulgacion de datos esta inline en los tres lugares que el
  Store exige (manifest, ficha y la propia app), que es la parte que si esta cubierta.
- ~~La serie 17.0 sin reinstalar~~ — **hecho**: instala limpio en `odoo:17` (seccion 4).
- **`knowledge/semantica/graph.json` quedo desactualizado** por el rename: sigue indexando
  `odoo_addon.linchpin_dry_run.*`. Es un artefacto **generado** de 15 MB, trackeado, con su
  propio build (`knowledge/semantica/build_graph.py`). No lo regenere y no lo edite a mano:
  su `ROOT` esta hardcodeado a `kern-main` y escribe a `kern-semantica-out/`, asi que correrlo
  desde este worktree indexaria el arbol equivocado; ademas el paquete `semantica` que importa
  no esta instalado en este entorno, y refrescarlo arrastraria todo el drift del repo, no solo
  este rename. Corresponde a quien corre esa pipeline (el commit `9baeb7d` en `main` fue
  exactamente eso).
- Las referencias a `linchpin_dry_run` que sobreviven en `CHANGELOG.md` y en
  `HANDOFF.md:2066` son **historicas a proposito** (registran lo que era cierto entonces) y no
  se tocaron. Las referencias vivas de `documentation/` si se actualizaron.
