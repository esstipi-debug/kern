# Publicar `linchpin_dry_run` en apps.odoo.com

**Meta:** el módulo instalable en un Odoo 17 limpio, aceptado por la revisión de
apps.odoo.com, con las conexiones verificadas contra un Odoo real y no contra un
simulador.

**Estado:** los 15 tests de integración pasan contra un Odoo 17 real (§4). El
módulo se instala en un Odoo limpio y la frontera de seguridad (OC en borrador)
está comprobada contra el servidor, no contra un simulador.

**Falta para enviar:** las cinco decisiones de la §3, que son de negocio y no de
código.

---

## 1. Lo que ya está cerrado

| | Estado |
|---|---|
| Sin dependencias externas de Python | ✅ solo stdlib: `urllib`, `json`, `socket`, `ipaddress`, `uuid` |
| Sin `.pyc` versionados | ✅ `__pycache__/` está en `.gitignore` |
| Icono 140×140 | ✅ `static/description/icon.png` |
| `static/description/index.html` | ✅ presente |
| Versión con formato de serie | ✅ `17.0.1.0.0` |
| `ir.model.access.csv` para cada modelo | ✅ el wizard tiene su regla |
| Grupo de seguridad propio | ✅ `security/linchpin_security.xml` |
| Divulgación del servicio externo | ✅ la descripción dice qué campos salen, a dónde y por qué |
| Sin manipulación de `sys.path` | ✅ resuelto — ver §2 |

## 2. `sys.path`: por qué se cambió y qué lo protege

El wizard importaba su librería con `sys.path.insert(0, lib_dir)`. Eso registra un
módulo llamado `mcp_client` **al principio de la ruta de búsqueda global del
proceso**: en una instancia de Odoo con varios addons, cualquier otro módulo con
ese nombre queda ensombrecido por este, o al revés. Es además de las primeras
cosas que marca una revisión de apps.odoo.com.

Ahora `lib/` es un subpaquete de verdad y el wizard hace
`from ..lib.mcp_client import ...`, que funciona porque Odoo carga cada addon como
`odoo.addons.<módulo>`.

Lo protegen seis tests en `tests/test_odoo_addon_paquete.py`, que simulan `odoo`
con la superficie mínima y comprueban que el paquete se importa entero, que no deja
nada en `sys.path`, que `mcp_client` no queda como módulo de nivel superior, y —por
AST, no por texto— que ningún fichero del addon toca `sys.path`.

> El daño no era teórico: `tests/test_odoo_addon_mcp_client.py` hacía el mismo hack
> y contaminaba a los tests que corrían después en la suite completa. El mismo
> problema que causa dentro de Odoo, reproducido a escala de un proceso de pytest.

## 3. Cinco decisiones que no son técnicas — las toma el dueño

Ninguna se puede inventar desde el código, y las cinco bloquean el envío.

| # | Decisión | Por qué bloquea |
|---|---|---|
| 1 | **Licencia y precio.** Hoy `OPL-1`, que es la licencia propietaria de Odoo y se usa para apps de pago — pero el manifest no declara `price` ni `currency`, y el resumen dice "a free dry run". Es incoherente: o es gratis y va con `LGPL-3`, o es de pago y necesita `price` + `currency`. | La revisión rechaza la incoherencia |
| 2 | **Correo de soporte.** El manifest no lleva `support`. Odoo lo pide para publicar. | No hay a quién escalar una incidencia |
| 3 | **Imágenes de la ficha.** `images` apunta al icono. La ficha de la tienda usa esa lista como material promocional: hacen falta capturas reales del wizard y del resultado. | La ficha queda sin ilustrar |
| 4 | **Autor y web.** `author: "Linchpin"` y `website: https://linchpin.fly.dev`. Si el producto se publica como **Kern**, los dos cambian, y el nombre del módulo con ellos. | Publicar con dos marcas confunde y no se puede deshacer |
| 5 | **`application: True`.** Declara una aplicación con menú propio. Para un wizard bajo Inventario puede ser más honesto `False`. | Afecta a cómo se lista y a qué espera el usuario |

## 4. Verificado contra un Odoo 17 real — 15 tests en verde

```bash
docker compose -f docker-compose.odoo.yml up -d
python scripts/odoo_test_setup.py
uv run --no-sync pytest -m integracion
# al terminar:  docker compose -f docker-compose.odoo.yml down -v
```

Reproducible desde cero: base borrada, `up`, un comando de setup, **15 passed ·
1 xfailed**. Lo que quedó comprobado y antes era una suposición:

- ✅ **El addon se instala en un Odoo 17 limpio.** Eso además prueba que el import
  relativo funciona bajo el cargador real, no sólo bajo el simulacro de la §2.
- ✅ **Los seis modelos y todos sus campos existen**: `product.product`,
  `purchase.order`, `purchase.order.line`, `stock.quant`, `stock.location`,
  `sale.order.line`. Un rename de versión habría pasado los 81 tests unitarios.
- ✅ **La OC queda en `draft`.** La frontera de seguridad la impone Odoo, no Kern.
- ✅ **Reintentar el mismo restock no duplica la orden.** La clave de idempotencia
  se deriva del contenido del changeset. Era el fallo más caro posible.
- ✅ **La OC lleva sus líneas con la cantidad pedida** — una cabecera vacía parece
  una orden real.
- ✅ **Un SKU sin proveedor sale en `unsourced`**, no se descarta en silencio.

### Tres prerequisitos que ningún sitio decía, y que un cliente va a sufrir

Los tres se descubrieron en la primera corrida, y los tres producen síntomas que
apuntan al sitio equivocado. Están automatizados en `scripts/odoo_test_setup.py`,
pero un cliente real no tiene ese script:

| # | Prerequisito | Lo que ve el cliente si falta |
|---|---|---|
| 1 | **El módulo Purchase debe estar instalado.** El addon depende de base/stock/sale, pero el *conector* escribe OC | `Object purchase.order doesn't exist` — parece un bug de Kern |
| 2 | **El usuario de API necesita cuatro grupos**: Sales/Administrator, Inventory/Administrator, Purchase/Manager, Settings. En un Odoo 17 recién creado **ni `admin` los tiene todos** | Un fault crudo de Odoo listando grupos, sin decir cuál añadir |
| 3 | **Instalar un módulo no lo hace visible al servidor en marcha** — hay que reiniciarlo | Idéntico al síntoma 1, así que se persigue el bug equivocado |

**Esto es material de documentación de cliente, no sólo de test.** El #2 en
particular: el mensaje que llega es de Odoo y no dice que basta con añadir un
grupo.

### Un defecto de producto encontrado, fijado como `xfail`

`OdooConnector` cachea el mapa SKU↔id en el primer uso y **no lo refresca nunca**
(`_ensure_catalog` sólo actúa si la caché está vacía). Un conector de vida larga
—el de un job programado— no ve un producto dado de alta después.

Lo peor no es que no lo vea, es **cómo lo reporta**: el SKU cae en `unsourced`,
que significa "sin proveedor en Odoo". El cliente irá a darle un proveedor que ya
tiene, y volverá a fallar igual. El síntoma apunta al sitio equivocado.

Está fijado en `test_el_catalogo_cacheado_no_ve_un_sku_creado_despues` con
`xfail(strict=True)`: cuando alguien añada el refresco, el test pasará a verde
inesperado y fallará, que es como debe enterarse. **Merece su propio PR** — no va
en éste, que es sobre publicar el addon.

## 5. Orden sugerido

1. Levantar Odoo y correr `-m integracion`. Arreglar lo que salga. **Es lo único
   que quita riesgo real de aquí.**
2. Tomar las cinco decisiones de la §3 y aplicarlas al manifest.
3. Sacar las capturas de pantalla y actualizar `images`.
4. Instalar el módulo en un Odoo 17 limpio a mano, recorrer el wizard de punta a
   punta, y comprobar que el texto que ve el usuario dice la verdad sobre qué datos
   salen del sistema.
5. Enviar.
