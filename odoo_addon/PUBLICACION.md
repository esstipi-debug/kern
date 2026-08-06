# Publicar `linchpin_dry_run` en apps.odoo.com

**Meta:** el módulo instalable en un Odoo 17 limpio, aceptado por la revisión de
apps.odoo.com, con las conexiones verificadas contra un Odoo real y no contra un
simulador.

**Hecho cuando:** los 12 tests de `tests/integracion/test_odoo_real.py` pasan contra
un Odoo 17 de verdad, y las cinco decisiones de la §3 están tomadas.

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

## 4. Lo que falta verificar, y el comando

```bash
docker compose -f docker-compose.odoo.yml up -d
uv run --no-sync pytest -m integracion
docker compose -f docker-compose.odoo.yml down -v
```

Los 12 tests de `tests/integracion/test_odoo_real.py` cubren exactamente lo que los
81 tests de la suite normal **no pueden** cubrir, porque todos corren contra un
proxy XML-RPC falso:

1. **Que los campos existan en Odoo 17** — `default_code`, `standard_price`,
   `seller_ids`, `product_qty`, `usage`... Un rename entre versiones pasa los 81
   tests y falla en el cliente.
2. **Que la OC quede en `draft`.** El borrador sin confirmar es la frontera de
   seguridad del diseño, y no la impone Kern: la impone el estado en que Odoo la
   deja. Solo se puede comprobar contra Odoo.
3. **Que reintentar con la misma clave de idempotencia no duplique.** Es el fallo
   más caro: una red que corta después de que Odoo creó la orden pero antes de que
   Kern lea la respuesta produce exactamente ese reintento. Si duplica, el cliente
   compra dos veces.
4. **Que un fallo no deje una OC a medias** — cabecera sin líneas parece una orden
   real.
5. **Que el módulo aparezca instalable** en un Odoo limpio.

> **Estos 12 no se han ejecutado nunca.** Se escribieron con el daemon de Docker
> parado: están verificados como código, no contra un servidor. Espera que alguno
> falle en la primera corrida — su razón de existir es justo lo que el simulador no
> podía decir. Un test de integración que pasa a la primera suele significar que no
> estaba probando nada.

Están fuera de la suite por defecto (`addopts = "-m 'not integracion'"`) para que
no haga falta infraestructura para trabajar. Eso no los hace opcionales: esta meta
no está cumplida hasta que pasen.

## 5. Orden sugerido

1. Levantar Odoo y correr `-m integracion`. Arreglar lo que salga. **Es lo único
   que quita riesgo real de aquí.**
2. Tomar las cinco decisiones de la §3 y aplicarlas al manifest.
3. Sacar las capturas de pantalla y actualizar `images`.
4. Instalar el módulo en un Odoo 17 limpio a mano, recorrer el wizard de punta a
   punta, y comprobar que el texto que ve el usuario dice la verdad sobre qué datos
   salen del sistema.
5. Enviar.
