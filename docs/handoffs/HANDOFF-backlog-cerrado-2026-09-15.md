# Handoff: backlog en cero, esperando que la cuota de Actions se renueve el 1-oct

Fecha: 2026-09-15 · Worktree principal: `C:\Users\Gamer\Music\scm\supply-chain-optimization` · Rama: `main` @ `025bbf3`

## 1. Objetivo de la proxima sesion

**No hay trabajo urgente.** El backlog esta en cero y `main` esta sano. La proxima sesion
tiene dos cosas que hacer, ninguna antes del 1 de octubre:

1. Cuando la cuota de Actions se renueve, verificar que CI vuelva sola y vigilar la tasa de
   quema los primeros dias.
2. Decidir el destino de los archivos sin commitear del checkout compartido (ver seccion 3 —
   **es lo unico que puede romper algo si se ignora**).

## 2. Estado de juego

### Hecho y verificado

- **Backlog cerrado: de 32 PRs abiertos a 0.** 17 mergeados, 14 cerrados por cumplidos, 1
  rescatado con el registro descongelado.
- **`main` @ `025bbf3` verde**: `ruff check .` limpio, **8426 passed, 0 failed**, cobertura
  94,32%. Verificado a mano en un worktree limpio con venv 3.12 y resolucion sin pinear —
  **no por CI**, que sigue cortada.
- **Registro descongelado a 60 tools** (`shopify_replenishment`, `channel_replenishment`).
  Tercera excepcion explicita a "no hay tool #54 sin cliente pago", registrada en
  `documentation/ADR-2026-09-operador.md` seccion 3.
- **Imagen de GHCR reconstruida y publicada**: `ghcr.io/esstipi-debug/kern` con etiquetas
  `:main` y `:sha-0d20f04`, digest `sha256:23b8ee49...`. Verificada haciendo `pull` desde el
  registro: arranca en 6 s sin variables, health 200, 60 tools adentro.
- **Produccion sana**: `https://linchpin.fly.dev/api/health` da `{"ok":true,"skus":8}`.
- **Entorno `production`**: restriccion de rama puesta (solo `main`).
- **`price-watch-cron` apagado** (#362) por dos mecanismos: `schedule:` comentado + estado
  `disabled_manually` en GitHub.

### Bloqueado

- **CI de GitHub Actions**, desde el 2026-09-06. **No es un muro de pago**: se agotaron los
  2.000 min/mes del plan Free (~1.930 gastados en los primeros 6 dias del mes). **Se renueva
  sola el 1 de octubre.** Confirmar en github.com/settings/billing > Actions.
- **Revisor obligatorio en el entorno `production`**: imposible en plan Free sobre repo
  privado (HTTP 422). Todo el diseno de la compuerta de deploy de julio dependia de eso. Ver
  `docs/DEPLOYMENT.md` seccion 7, ya corregida.

### Cobertura mientras tanto

Tarea programada **`kern-nocturno-suplente`**, diaria 07:00 local, activa y verificada. Corre
el equivalente del canario nocturno: worktree limpio, venv 3.12, resolucion sin pinear, ruff
+ suite con el gate de cobertura, health de prod y conteo de PRs. Solo corre con la app
abierta.

## 3. Estado del arbol

### El checkout compartido esta SUCIO, y eso ahora choca con `main`

`C:\Users\Gamer\Music\scm\supply-chain-optimization` esta parado en la rama
`feat/fulfillment-milestones` @ `f2a1ffa`, **~30 commits detras de `main`**, con 16 archivos
modificados y 10 sin trackear.

**La trampa concreta:** tres de esos archivos sin trackear —
`src/fulfillment_milestones.py`, `src/connectors/milestones.py`,
`jobs/fulfillment_chain_job.py` — **ya existen en `main`** (entraron por #356) y las copias
locales son **DISTINTAS** (mas viejas: les faltan la declaracion `NOT_CONNECTORS`, el skip
del test de ruteo y la confianza derivada de la cobertura).

Consecuencia: `git checkout main` o `git pull` en ese checkout **van a fallar** con
_"untracked working tree files would be overwritten"_.

Los 16 archivos modificados son los espejos de registro de la tool `fulfillment_chain`, que
**deliberadamente NO se trajeron** a `main` (el registro estaba congelado en ese momento).
Hoy el registro esta en 60 y esa decision se puede revisar.

**No se toco nada de eso**: es un checkout compartido con otras sesiones y borrar ahi ya
destruyo trabajo ajeno una vez. Ver seccion 4.

### Stashes

Cuatro, todos de OTRAS sesiones, ninguno de este trabajo:

```
stash@{0}  feat/retencion-de-datos: WIP pre-merge main (14 archivos)
stash@{1}  feat/shopify-replenishment-tool: otra-sesion: WIP antes de recuperar fantasmas
stash@{2}  fix/valorizar-excepciones-ap: WIP webapp/app.py
stash@{3}  feat/warehouse-spatial-twin: warehouse-branch user WIP
```

### PRs

Ninguno abierto.

### Comando de verificacion

Prueba que `main` sigue sano (~20 min). Vale tal cual, sin adaptar:

```bash
cd /c/Users/Gamer/Music/scm/supply-chain-optimization && git fetch -q origin
WT="verify-$(date +%Y%m%d-%H%M%S)-$$"
git worktree add --detach "/tmp/$WT" origin/main && cd "/tmp/$WT"
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt -q
export PYTHONPATH=. OMP_NUM_THREADS=1
.venv/Scripts/ruff.exe check .
.venv/Scripts/python.exe -m pytest tests/ -q --cov=packs --cov=src --cov=scm_agent --cov=jobs --cov-fail-under=90
```

Referencia: **8426 passed, 0 failed, 94,32%**.

## 4. Decisiones abiertas

### A. Los archivos sueltos del checkout compartido

- **Borrar las 3 copias sin trackear** (ya estan en `main`, mas nuevas) y dejar los 16
  modificados. Desbloquea el `pull` sin tocar el resto. **RECOMENDADA.**
- Registrar la tool `fulfillment_chain` (#61) usando los 16 modificados como base. El
  registro ya no esta congelado. Es trabajo, no limpieza.
- No tocar nada. El checkout queda imposible de actualizar.

**Antes de cualquiera de las tres: confirmar que ninguna sesion viva este usando ese
checkout.** Ver `git status` en ese momento, no el de este documento.

### B. `FLY_API_TOKEN`

- **No cargarlo** y seguir desplegando con `flyctl deploy` local, como hoy. **RECOMENDADA**
  mientras no haya auto-deploy.
- Cargarlo como secreto de *environment* en `production` (no de repositorio). Habilita el
  deploy manual desde Actions. **Es seguro**: el disparo `push:` esta comentado, asi que no
  habilita ningun despliegue desatendido.

### C. Pagar GitHub Team (~4 USD/mes)

- **No pagar.** La cuota vuelve sola el 1-oct. **RECOMENDADA.**
- Pagar. Sube a 3.000 min **y** habilita el revisor obligatorio, que es lo unico que
  destrabaria descomentar `push:` en el deploy.

### D. El monitoreo de precios

Apagado (#362). Si en octubre se decide que importa: cargar `KERN_JOBS_API_KEY` como secreto
de **repositorio** con el mismo valor que `LINCHPIN_API_KEY` de la app, descomentar el
`schedule:` y reactivar el workflow en la UI. Los pasos estan escritos arriba del `on:` en el
propio archivo.

## 5. Trampas ya pisadas

- **Un check rojo en un PR de Kern NO es un test roto**: es el job que no arranco por falta
  de cuota. Nunca leer los rojos de GitHub como fallas de codigo mientras dure el corte.
- **Git auto-fusiona y rompe sin marcar conflicto.** Paso dos veces (#256: `SyntaxError` por
  un `continue` fuera de bucle; #244: 41 errores F821 por helpers que `main` habia eliminado).
  Despues de fusionar `main` en una rama vieja: **compilar y correr**. "No hubo conflictos" no
  prueba nada.
- **Un PR viejo suele estar CUMPLIDO, no pendiente.** 11 de los 32 ya estaban en `main` por
  otra via. Se detecta con grep de los simbolos que el PR introduce, nunca por el titulo.
- **Señal para rescatar o rehacer:** si los bloques de conflicto se suman (imports, campos,
  secciones), se rescata. Si son dos redacciones rivales del mismo docstring o dos
  descomposiciones de la misma funcion, `main` ya resolvio ese problema de otra forma: se
  rehace, reusando el motor verbatim y recableando.
- **Verificar de a uno y mergear de a uno no sirve con CI muerta**: cada merge invalida la
  verificacion de los demas. Fusionar todo a UNA rama de integracion, correr una sola vez, y
  despues confirmar `git diff origin/main <integracion>` vacio.
- **Seguir las instrucciones de un PR viejo al pie puede romper algo posterior.** El script de
  Vandeput manda re-correr Louvain; hacerlo movio 12 nodos de la comunidad de control
  documental y rompio 2 tests. La fuente #30 habia fijado la regla contraria despues.
- **`gh auth status` miente sobre los scopes.** Lo autoritativo es
  `gh api -i user | grep -i x-oauth-scopes`.
- **`docker login ghcr.io` dice "Login Succeeded" aunque falte `write:packages`.** Autenticar
  y poder escribir son permisos distintos; el 403 aparece recien en el `push`.
- **El flujo de dispositivo de `gh auth refresh` necesita que el proceso siga VIVO.** En
  primer plano con timeout imprime el codigo y muere, y el codigo queda inutil. Va en segundo
  plano.
- **El registro del scheduler no sobrevive al reinicio de la app.** Los `SKILL.md` persisten,
  el registro no. Antes de apoyarse en una tarea programada, verificar con
  `list_scheduled_tasks` que este REGISTRADA, no que exista el archivo.
- **`fly secrets` no tiene `reveal`.** Un secreto de Fly no se puede leer: o se tiene
  guardado, o se rota en los dos lados a la vez.
- **Workflow fantasma `quality`**: `gh workflow list` lo muestra pero no existe en `main` (se
  borro en `c4dbf9d`). Solo sobrevive en ramas viejas. No gasta nada, no hay que hacerle nada.

## 6. Skills a usar

- **`graphify`** — para cualquier pregunta sobre arquitectura o relaciones entre archivos de
  este repo, antes de leer a mano. Ojo: el server MCP venia fallando al conectar.
- **`anthropic-skills:cavecrew`** — delegacion en este repo. Los presets no estan instalados
  como agentes; verificar antes de spawnear.
- **`superpowers:writing-plans`** / **`superpowers:brainstorming`** — si la proxima sesion
  arranca algo nuevo. El usuario pide plan a la vista ANTES de tocar codigo.
- **`code-modernization:modernize-extract-rules`** — instalado el 14-sep. Util solo para
  desenterrar reglas de negocio de los jobs; el resto del plugin apunta a legacy y Kern no lo es.

## 7. Artefactos

- ADR del registro: `documentation/ADR-2026-09-operador.md` (seccion 3, fila del 2026-09-15)
- Deploy: `docs/DEPLOYMENT.md` seccion 7 (corregida — dice que el revisor obligatorio es
  imposible en este plan)
- Workflows: `.github/workflows/{tests,docker,fly-deploy,price-watch-cron}.yml`
- Tarea programada: `C:\Users\Gamer\.claude\scheduled-tasks\kern-nocturno-suplente\SKILL.md`
- Imagen: `ghcr.io/esstipi-debug/kern:main` y `:sha-0d20f04`
- PRs de esta sesion: [#356](https://github.com/esstipi-debug/kern/pull/356),
  [#357](https://github.com/esstipi-debug/kern/pull/357),
  [#358](https://github.com/esstipi-debug/kern/pull/358),
  [#359](https://github.com/esstipi-debug/kern/pull/359),
  [#360](https://github.com/esstipi-debug/kern/pull/360),
  [#238](https://github.com/esstipi-debug/kern/pull/238),
  [#361](https://github.com/esstipi-debug/kern/pull/361),
  [#362](https://github.com/esstipi-debug/kern/pull/362)
- Memoria relevante: `kern-ci-bloqueada-por-facturacion`, `kern-pending-secrets-handoff`,
  `kern-autonomous-loop-config`, `kern-nocturno-worktree-borrado-concurrente`
