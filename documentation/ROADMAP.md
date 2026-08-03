# Kern — Roadmap para GitHub Issues

Diez items listos para pegar como issues. Cada uno tiene titulo, labels sugeridos
y cuerpo. Lo que un reclutador debe leer entre lineas: hay vision de producto,
hay priorizacion explicita, y la deuda tecnica esta nombrada, no escondida.

Orden de prioridad sugerido: **1, 6, 2, 3, 10, 5, 7, 4, 8, 9**.
(Primero lo que reduce el tiempo de evaluacion de un tercero; despues lo que
extiende la superficie del producto.)

Crear las labels una sola vez:

```bash
gh label create integration   --color 0e8a16 --description "Conectores y superficies externas"
gh label create observability --color 5319e7 --description "Logs, metricas, trazas"
gh label create devops        --color 1d76db --description "CI/CD, imagenes, deploy"
gh label create dx            --color fbca04 --description "Experiencia de quien evalua o contribuye"
gh label create benchmark     --color c5def5 --description "Medicion reproducible contra baseline"
```

---

## 1. Generar la tabla de capacidades desde el registry ~~y romper CI si hay drift~~

**Labels:** `documentation`, `automation`, `good first issue`
**Estado:** la mitad de deteccion ya esta hecha (`tests/test_readme_capability_drift.py`); falta la generacion.

El README mantiene a mano la tabla de las 49 capacidades. Ya divergio (el texto
decia "48 agent-routable" en un parrafo y "49 registered tools" en otro).

**Hecho:** `tests/test_readme_capability_drift.py` compara el conjunto de tools
documentadas contra `build_default_registry()` y verifica que todos los conteos
en la prosa coincidan con `len(registry)`. Es igualdad de conjuntos, no conteo:
un conteo pasa cuando se renombra una tool o se agrega una y se quita otra, que
es justo cuando la prosa alrededor tambien esta mal. Verificado contra el README
viejo — falla ahi, pasa aca.

**Falta:** que la tabla se *genere* en vez de solo validarse. Hoy el guard dice
que algo divergio pero no lo arregla; con un generador el fix es correr un
comando. Requiere agrupar por area, y `Tool` (en `nosilo_core/registry.py`) no
tiene campo `area` — la agrupacion del README es editorial. Decidir si se agrega
el campo al `Tool` o se mantiene un mapa aparte en el script.

**Definition of done:** `scripts/gen_capability_table.py`, seccion delimitada por
marcadores en README.md, y el guard existente extendido para comparar contra la
salida del generador.

---

## 2. Publicar la imagen en GHCR en cada tag de version

**Labels:** `enhancement`, `devops`

Hoy un cliente o evaluador tiene que clonar y construir. Con `docker.yml`
publicando en tags, la evaluacion pasa a ser `docker run ghcr.io/esstipi-debug/kern:2.9.0`.

**Definition of done:** workflow `docker.yml` con push a GHCR en `v*`, paquete
publico, instruccion de `docker pull` en el README.

---

## 3. Observabilidad estructurada: request id, latencia por tool, tasa de QA fallado

**Labels:** `observability`, `enhancement`

`LINCHPIN_LOG_JSON` ya da logs estructurados. Falta la capa de arriba: correlacion
por request id a traves de orchestrator -> tool -> deliverable, y metricas por
capacidad. En un forward deployment la pregunta operativa no es "anda?", es "que
tool falla QA en que cliente y con que datos".

**Definition of done:** middleware de request id, contadores por tool
(`runs`, `qa_failed`, `p95_latency`), endpoint `/metrics` o export a Sentry.

---

## 4. Completar el rename `linchpin` -> `kern` en identificadores de infra

**Labels:** `chore`, `breaking-change`

Env vars (`LINCHPIN_*`), nombres de tools MCP, el modulo Odoo
(`odoo_addon/linchpin_dry_run`) y el hostname siguen con el nombre anterior. Es
deliberado y esta documentado, pero cuesta credibilidad a quien lee el repo por
primera vez. El checklist de migracion ya existe en `HANDOFF.md`.

**Definition of done:** periodo de alias con deprecation warning, migracion del
deploy, checklist de HANDOFF.md cerrado.

---

## 5. Segundo conector ERP (SAP Business One o NetSuite) sobre el protocolo de writeback existente

**Labels:** `integration`, `enhancement`

`nosilo_core/writeback.py` define la superficie (changeset -> risk tier ->
approval -> apply idempotente -> rollback) y hoy la implementan Odoo y Excel. Un
tercer conector prueba que la abstraccion generaliza — que es exactamente la
objecion que recibe cualquier propuesta forward-deployed.

**Definition of done:** conector con stand-in en memoria (como Odoo), tests de
contrato compartidos entre los tres conectores, seccion en el README.

---

## 6. ~~`make demo` de un comando~~ + dataset de demo mas sucio

**Labels:** `documentation`, `dx`
**Estado:** el comando ya esta (`Makefile` + `examples/run_demo.py`); falta el dataset realista.

Quien evalua el repo (reclutador tecnico, cliente, contribuidor) tenia que elegir
un ejemplo entre 40 archivos en `examples/`.

**Hecho:** `make demo` corre cuatro briefs por el orquestador real en ~15s, sin
claves y sin red, sobre los samples commiteados. El cuarto paso es el que
importa: el mismo brief de reposicion contra un archivo inservible, que debe
terminar sin entregable. El exit code depende de los cuatro, asi que el demo
falla si el sistema deja de refusar bien. Cubierto en CI via `make demo` (no
`python` directo) para que el Makefile tambien quede testeado.

**Falta:** los samples son chicos y limpios. Un dataset sucio de verdad —
duplicados, huecos de fecha, unidades mezcladas, cantidades negativas — ejercita
el gate de QA mucho mas que un CSV de 8 SKUs, y es lo que un evaluador tecnico
quiere ver. Ver tambien el hallazgo abierto: hoy una cantidad negativa **no**
levanta ninguna objecion.

**Definition of done:** dataset sucio versionado (o generador con seed fijo), un
quinto paso del demo que lo use, y capturas en el README.

---

## 7. Tests de contrato para la superficie MCP

**Labels:** `testing`, `integration`

`/mcp` es una API publica consumida por otros agentes, con emision de claves por
cliente. Un cambio de firma en una tool rompe consumidores externos en silencio.

**Definition of done:** snapshot del schema de tools expuestas, test que falla
ante cualquier cambio no declarado, nota de versionado en `docs/MCP_SERVER.md`.

---

## 8. Tracking de accuracy por tool alimentando los tiers de autonomia T1/T2/T3

**Labels:** `enhancement`, `research`

`scm_agent/autonomy.py` define que una tool avanza hacia mas resultados
`EXECUTED` cuando su accuracy medida cruza un umbral. Falta el loop que mide y
persiste esa accuracy por tool y por cliente. Sin eso, la autonomia sigue siendo
una politica declarada en vez de ganada.

**Definition of done:** store de outcomes, calculo de accuracy por tool/cliente,
promocion de tier automatica con registro de auditoria.

---

## 9. Traducir al ingles la documentacion del operador

**Labels:** `documentation`

`documentation/operator/` (role charter, division of labor, metodologias,
runbooks, catalogo de entregables, mapa de competencias) es el material mas
diferenciado del repo y hoy esta solo en espanol, lo que lo vuelve invisible para
un hiring manager internacional.

**Definition of done:** version en ingles de los 6 documentos nucleo, indice
bilingue en `documentation/operator/README.md`.

---

## 10. Suite de benchmark sobre datasets publicos, corrida en CI con deltas publicados

**Labels:** `testing`, `benchmark`

El README afirma reduccion de costo mediana vs baseline sobre Online Retail I/II
y Superstore. Hoy es una imagen; deberia ser un artefacto reproducible que CI
regenera, para que la afirmacion sea verificable por un tercero.

**Definition of done:** `benchmarks/` con datasets fijados por hash, job de CI
(nightly), tabla de resultados versionada y grafico regenerado automaticamente.
