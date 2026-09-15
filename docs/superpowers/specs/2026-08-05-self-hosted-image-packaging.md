# Spec — Kern como imagen publicada que el cliente corre solo

**Fecha:** 2026-08-05 · **Estado:** spec, sin implementar · **Decisión del operador:** opción 1
(imagen prebuilt publicada), sobre wheel privado y sobre hosted single-tenant.

## Por qué

La decisión de producto del 2026-08-05 es **software self-serve que el cliente corre solo**, con
beachhead AU/NZ. La landing `/one-plan` ahora vende el primer paso hecho-con-el-cliente y promete que
el plan y el motor **quedan del cliente** (opción C del posicionamiento). Ese destino hoy no existe:
no hay ningún camino por el cual un tercero levante Kern.

Referencia de forma: Dashy (`lissy93/dashy`, MIT, 26k estrellas) — un `docker run` de una línea,
config en **un solo archivo**, auth opcional con privilegios y SSO, editor de UI encima de la config,
cero versión hosteada. Lo que hace que Dashy pueda prometer eso es que es público entero. Kern no lo
es, y ahí está el problema real.

## El bloqueo estructural

`Dockerfile:55` aborta el build sin un secreto:

```
if [ ! -s /run/secrets/nosilo_token ]; then
    echo "ERROR: build secret 'nosilo_token' is missing or empty." >&2
```

`nosilo-core` es dependencia privada por `git+https` contra `esstipi-debug/nosilo-core`, y
`docs/DEPLOYMENT.md` abre con **"0. The private dependency (do this before any deploy path)"**. Todo
el documento asume que el que deploya es el operador.

**`NOSILO_CORE_TOKEN` está haciendo tres cosas a la vez:** mata la CI (los 4 jobs, desde el
2026-08-04), impide que un cliente construya la imagen, y hace imposible el "corrélo vos" que la
landing ya promete. No son tres problemas: es una decisión de dependencia asomando por tres lados.

## Decisión: imagen prebuilt publicada

El cliente hace `pull`, **nunca `build`**. No necesita el token: el build lo hace el operador o CI, y
lo que se publica es el artefacto.

Descartadas y por qué:
- **Wheel privado en index propio** — protege más el core pero suma fricción de onboarding y una
  superficie de soporte nueva. Menos vendible.
- **Hosted single-tenant** — el core nunca sale, pero es multiusuario real, así que **obliga** a
  resolver primero el loader de POLICY (`webapp/security.py:55`, hoy inexistente). Y "self-serve en el
  uso, no en el hosting" no permite que el prospecto pruebe sin hablar con el operador, que es el
  punto entero.

**Costo aceptado:** el core viaja adentro de la imagen como Python legible; quien tenga la imagen
tiene el fuente. Se mitiga con licencia y contrato, no con ingeniería. Esto es decisión de negocio y
está tomada.

## Qué recibe el cliente

Un comando. Ese es el criterio de aceptación, no una aproximación:

```bash
docker run -p 8000:8000 -v ./kern-data:/data ghcr.io/esstipi-debug/kern:latest
```

Registro: **GHCR**, porque el repo ya vive en GitHub y el mismo workflow que corre los tests puede
publicar. `registry.fly.io/linchpin` no sirve: es privado y está atado al deploy del operador.

## Problema 1 — la superficie de configuración es demasiado grande

Hoy hay **33 variables `LINCHPIN_*`** en `webapp/`, `scm_agent/`, `src/`, `jobs/`. De esas, ~10 son
rutas de estado (`LINCHPIN_STATE_PATH`, `EVENTS_PATH`, `SCHEDULER_PATH`, `PRICING_LEDGER_PATH`,
`SKU_MAP_PATH`, `MCP_KEYS_PATH`, `AUTONOMY_PATH`, `CLIENTS_ROOT`, `LEAD_REPORTS_DIR`,
`PORTFOLIO_DATA_FILE`).

Pedirle 33 variables a un dueño-operador es lo contrario de self-serve. La lección de Dashy es **un
archivo, no 33 variables**.

**Regla:** la imagen expone un contrato chico y todo lo demás toma default relativo a un único
`KERN_DATA_DIR` (montado en `/data`). Las 33 siguen funcionando como override para el operador; el
cliente no las ve.

Contrato mínimo propuesto para el cliente:

| Variable | Para qué | Default |
|---|---|---|
| `KERN_DATA_DIR` | raíz de todo el estado durable | `/data` |
| `PORT` | puerto HTTP | `8000` |
| `KERN_API_KEY` | protege `POST /api/jobs` | vacío = sin auth, y **lo dice fuerte al arrancar** |

Todo lo demás: default derivado. Ninguna ruta puede quedar apuntando al rootfs efímero — ese error ya
pasó y costó los 5 stores (PR #188).

## Problema 2 — auth: qué default es honesto

Hoy `LINCHPIN_REQUIRE_SECURE=1` **rompe el boot** si no hay política, y no hay forma de cargar una:
`webapp/security.py:55` declara `POLICY: PolicySet | None = None` y nadie se lo asigna nunca.

Para una instancia de un cliente en su propia máquina, el modelo de Dashy es el correcto: **auth
opcional, apagada por defecto, y la diferencia entre "no configuraste" y "configuraste vacío" es
explícita**. Un dueño-operador solo, en su LAN, no necesita control de acceso el primer día.

**Consecuencia:** el loader de POLICY deja de ser bloqueante para ESTA vía. Sigue siendo obligatorio
el día que entre la segunda persona del cliente, o si alguna vez se elige hosted. No lo cancela: lo
desordena de la ruta crítica.

Lo que la imagen SÍ debe hacer: arrancar con un banner inequívoco cuando corre sin API key,
en vez de arrancar callada o morir.

## Problema 3 — RESUELTO, la premisa estaba vieja

**Corrección 2026-08-06 (verificado superficie por superficie).** La versión previa de esta spec
afirmaba que "la descripción del tool promete AutoETS/TSB" mientras la imagen corre SES/Croston, y
declaraba eso bloqueante para publicar. **Es falso.** Todas las superficies ya son condicionales:

| Superficie | Texto real |
|---|---|
| `scm_agent/tools.py:1334` | "AutoETS/TSB **when installed**, else SES/Croston" |
| `webapp/mcp_tool_specs.py:104` | "SES/Croston, or AutoETS/TSB **when installed**" |
| `webapp/app.py:514` | "auto (AutoETS/TSB **when [forecast] installed**)" |
| `jobs/deliverables.py:126` | `METHOD_LABEL` — la sección Methodology nombra **los métodos que corrieron** |
| `documentation/*` | no mencionan AutoETS |

El bug existió y ya fue arreglado, con guard propio: `tests/test_deliverable_methodology_honesty.py`,
cuyo docstring lo documenta ("the report claim 'simple exponential smoothing (or Croston)' long after
`src/forecasting.py` started routing to AutoETS/TSB"). El deliverable que recibe el cliente nombra el
método que efectivamente se usó, no una plantilla.

**Consecuencia: sumar el extra `forecast` deja de ser condición para publicar.** Pasa a ser una
decisión de capacidad, no de honestidad, y la evidencia disponible la vuelve poco urgente: medido
sobre Olist, la diferencia es MASE 3,02 con la librería contra 3,14 sin ella. El cuello real no es la
sofisticación del modelo — ninguno de los dos ve tendencia ni estación, y `alpha` está clavado en un
literal (`src/forecasting.py:185`, SES 0,3 / Croston 0,1). Eso lo ataca el barrido de `alpha`, no un
extra más pesado.

Queda como mejora opcional, después de que la CI vuelva a verde, porque el guard que cubre ese riesgo
—el job `prod-boot`, que es lo que atrapó la regresión de `bs4`— hoy no puede correr.

## Qué tiene que ser verdad antes de publicar

1. Existe el secret `NOSILO_CORE_TOKEN` en `esstipi-debug/kern` (bloqueo abierto, acción manual).
   Sin eso ni la CI puede construir la imagen, mucho menos publicarla.
2. ~~Problema 3 resuelto~~ — **ya no aplica**: las superficies son honestas hoy (ver arriba).
3. `KERN_DATA_DIR` implementado y **todas** las rutas de estado derivadas de él, con un test que falle
   si una ruta nueva no lo respeta.
4. Arranque sin API key = banner explícito, no silencio ni crash.
5. Un smoke de contenedor en CI: `docker run` de la imagen publicada, `GET /api/health` y un job real
   end-to-end. Verde en CI y muerto en prod ya pasó dos veces (`bs4`, POLICY); esto es el guard.
6. Licencia y términos escritos, dado que el core viaja legible adentro.

## Fuera de alcance

- Multi-tenant, SSO, billing. Un cliente, una instancia, su máquina.
- Conectores Cin7/Unleashed. Son la otra mitad del beachhead y van por su propia vía.
- Editor de UI sobre la config (lo que Dashy tiene). Deseable, no de la primera versión.

## Notas

- La decisión de C en el posicionamiento **depende** de que esto exista. Hoy la landing promete un
  destino que no se puede entregar; el hueco es de semanas, no de trimestres, pero es real.
- Nada de esto es motor nuevo. Es empaquetado, y sale antes que cualquier tool #51.
