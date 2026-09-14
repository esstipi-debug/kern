# nosilo-core: por que estuvo afuera, y por que volvio

> **Este documento describe algo que ya no pasa.** Se conserva como registro: era
> la guia de "como correr Kern sin la credencial del core privado", y la receta de
> vendorizado que proponia tenia cuatro errores que solo se vieron al ejecutarla.
> Fecha de cierre: **2026-09-11** (PR-3).

## Lo que pasaba

`nosilo_core` salio de este repo el 2026-08-03 (#226) hacia un repo privado,
pinneado por tag. Cada camino de build lo resolvia por `git+https`, asi que cada
camino de build necesitaba una credencial: la CI, el Dockerfile, Fly, Railway y
el `pip install` de cualquiera que quisiera evaluar el motor.

La pregunta que este documento contestaba era real: *un comprador que corre
`pip install -e .` y ve cero tests es una senal de alarma*. La respuesta que daba
-- instalar con `--no-deps` y correr solo los tests del motor puro -- funcionaba,
pero dejaba fuera el 70% de la suite.

## Lo que costo, medido

- Un secreto repartido en seis lugares de build y **una sola cuenta** que podia rotarlo.
- 24 dias de CI corriendo ciega (3-ago a 27-ago) sobre un PAT que **nunca existio**.
- 10 dias sin imagen publicada (26-ago a 5-sep) sobre una premisa falsa: que BuildKit
  no reenviaba SSH. Si lo reenvia; #327 lo demostro con el deploy key que el repo ya tenia.
- Un consumidor real: **este repo**. `cobra-kobra` es TypeScript y no puede importarlo;
  el pack `source_to_pay` vive adentro de Kern.

## La receta que este documento proponia, y sus cuatro errores

`git subtree add --prefix=nosilo_core ...` sonaba bien y no lo era:

1. **Prefijo anidado.** El repo remoto ya tiene `nosilo_core/` como subdirectorio, asi
   que `--prefix=nosilo_core` produce `nosilo_core/nosilo_core/`.
2. **`rm .git` no hace nada** en un subtree: no hay `.git` que borrar.
3. **`file://` relativo** no resuelve como el documento asumia.
4. **Subestimaba el tamano 20 veces**: hablaba de "~180 lineas". Son **3.569** en 16
   archivos, con `access/` (915) y `entities/` (1.246) como subpaquetes completos.

Lo que se hizo en su lugar: restaurar el arbol desde la **propia historia de este
repo** (`git checkout 3e47b43^ -- nosilo_core`), que es byte a byte identico al
`v0.1.0` que corria en produccion, mas el unico cambio posterior del core (19 lineas
en `guided.py`: rechazar un status inventado que pasaba el control de calidad sin
decir nada) y sus cuatro tests.

## Lo que NO volvio atras

La **frontera** entre el control plane y el dominio. `nosilo_core` sigue sin saber
nada de SKUs, proveedores ni stock de seguridad; `src/` y `packs/` dependen de el y
nunca al reves. Lo guardan `tests/test_core_boundary.py` y `tests/test_pack_boundary.py`.
Lo que se fue es la credencial, no la separacion -- y por eso el dia que exista un
segundo pack en Python, sacarlo de nuevo cuesta un `git subtree split`.

Ver `docs/DEPLOYMENT.md` seccion 0.
