# Handshake: el laboratorio Odoo de docker no corre en una sesion de Claude Code

Fecha: 2026-09-19 | Sesion: Claude Code on the web | Rama: `claude/kern-sales-agency-z11nov` @ `b01b1bb`

No es hallazgo de una sesion de trabajo normal sobre el repo: es una propiedad del
entorno donde corrio esta sesion, registrada para que la proxima no pierda una tarde
re-descubriendola. Se escribe como archivo aparte y no como fila en
`documentation/ADR-2026-09-operador.md` seccion 4 (esa seccion esta atada a la puerta
de negocio de la semana 6 -- libros, OC, Amazon FBA -- y esto es infraestructura de
testing, no doctrina).

## 1. Objetivo de la proxima sesion

Nada urgente. Dos caminos posibles y ninguno bloquea la puerta de la semana 6 del ADR:

1. Si toca correr `pytest -m integracion` de verdad antes de que un partner conecte su
   Odoo, hacerlo desde un entorno cuya politica de red permita salir a Docker Hub (ver
   seccion 3).
2. Si no hace falta antes de eso, no hacer nada: el circuito ya esta verificado por otra
   via (seccion 2) y el bloqueo no toca ni codigo ni CI real.

### El bloqueo (externo a este entorno, no de codigo)

`scripts/odoo_test_setup.py` y `docker-compose.odoo.yml` estan bien escritos y no
tienen nada que corregir. Lo que fallo es el `docker compose up`: el `pull` de
`postgres:16` y `odoo:17` vuelve `403 Forbidden` contra
`production.cloudfront.docker.com` (la CDN de Docker Hub), rechazado por la politica de
red de ESTA sesion de Claude Code -- confirmado en
`http://127.0.0.1:36319/__agentproxy/status`, `recentRelayFailures` marca
`connect_rejected` contra ese host. No es un timeout ni un error de credenciales: es un
`CONNECT` que el proxy de la sesion corta a proposito, asi que no se reintenta ni se
rodea (la guia del proxy de esta sesion es explicita en eso).

Para destrabarlo hace falta, del lado del operador:
1. Un entorno de Claude Code (o cualquier maquina) cuya politica de red de salida
   permita `production.cloudfront.docker.com` u otro registro que sirva
   `postgres:16` / `odoo:17` -- un mirror interno, o correrlo en la maquina local en vez
   de en la nube.
2. Con eso resuelto, el camino documentado ya alcanza:
   `python scripts/odoo_test_setup.py && pytest -m integracion`.

Docker mismo SI funciona en este entorno (el daemon levanta con
`dangerouslyDisableSandbox`); lo que no llega es la imagen.

## 2. Lo que SI se verifico en esta sesion, sin el laboratorio de contenedor

Contra `src/connectors/odoo.py::InMemoryOdoo` (el doble en memoria que ya usan los
tests y `examples/run_odoo.py` cuando no hay `ODOO_*`), no contra un Odoo real:

- **257 tests en verde**: circuito PR-0 completo (aprobacion nombrada, ledger en disco,
  MOQ del proveedor, refresco de catalogo), neteo en transito, irreversibilidad de la
  PO, lectura de lead time, el addon, el cliente MCP.
- **`examples/run_odoo.py` corrido a mano en sus cuatro caminos**: dry-run sin
  `--approved-by` (stagea, no escribe), reorder points con `--approved-by` (aplica,
  deja fila en el ledger sqlite con quien y cuando, un segundo apply con la misma clave
  sale `idempotent_skip=True`), PO draft con `--approved-by` (crea la PO agrupada por
  proveedor), PO draft SIN `--approved-by` (rechazado en el parser, exit 2, nunca toca
  Odoo).
- **Apply -> rollback -> re-rollback verificado a mano**: la mutacion real del
  `stock.warehouse.orderpoint` se confirma antes/despues, y un segundo rollback sobre
  la misma clave sale con `KeyError` (no revierte dos veces). De paso, una regla de
  diseno que casi se reporta como bug y no lo es: una regla de reorden recien creada
  queda en pie despues del rollback (`_apply_restore`, comentario en
  `src/connectors/odoo.py`: "a freshly-created rule is left in place (still
  reversible)") -- distinto de la PO draft, que si se borra al revertir
  (`tests/test_odoo_po_irreversible.py::test_rollback_removes_the_purchase_orders_it_created`).

Lo que esto NO prueba: que el XML-RPC real contra un Odoo con Postgres vivo funcione
igual que el doble en memoria. El conector (`OdooClient`) y el doble comparten la misma
interfaz `OdooRPC`, pero la unica forma de probar la conversacion real es
`pytest -m integracion` contra un Odoo levantado, o `examples/verify_odoo_live.py`
contra la cuenta de un partner.

## 3. Alcance real del bloqueo: no toca CI

Verificado leyendo los 4 workflows de `.github/workflows/`:

- El unico paso `pytest` de CI (`tests.yml:130`, `pytest tests/ -q --cov=... `) no pasa
  `-m integracion` ni ningun override de `-m`, asi que hereda
  `addopts = "-m 'not integracion'"` de `pyproject.toml:238` y **ya excluye** el
  laboratorio Odoo -- igual que corriendo `pytest -m 'not integracion'` a mano.
- Ningun workflow define `services:`, `docker compose`, ni una imagen `postgres` u
  `odoo`. El unico contenedor que arma CI (`docker.yml`, job `build-and-smoke`) es la
  propia imagen de produccion de Kern, no un Odoo.

O sea: el laboratorio de contenedor **nunca corrio en CI**, ni antes de este hallazgo
ni ahora. El bloqueo de esta sesion no rompe nada que ya estuviera verde -- deja
exactamente donde estaba una verificacion que hoy solo se hace a mano, desde una
maquina con salida a Docker Hub.

## 4. Por que no es un issue de GitHub

Se reviso el repo (`esstipi-debug/kern`, 4 issues abiertas, 0 cerradas, ninguna
relacionada) antes de escribir esto. No se abrio uno nuevo: un issue lo presentaria
como trabajo de codigo pendiente, y no lo es -- `docker-compose.odoo.yml` y
`scripts/odoo_test_setup.py` funcionan tal cual en una maquina sin esta politica de
red. Es una propiedad del entorno de ESTA sesion, no del repo.
