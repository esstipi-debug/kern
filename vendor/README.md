# `vendor/` — ruedas locales para builds sin secreto

Este directorio existe para que `COPY vendor/ /wheels/` en el `Dockerfile` no
falle: Docker aborta el build si el origen de un `COPY` no existe, así que el
directorio se versiona aunque su contenido no.

**Los `.whl` acá adentro están gitignorados a propósito.** `nosilo-core` es una
dependencia privada; una rueda suya no se commitea a este repo.

## Para qué sirve

Por defecto el `Dockerfile` resuelve `nosilo-core` por `git+https`, lo que exige
el secreto de build `nosilo_token` (ver `docs/DEPLOYMENT.md`, sección 0). Eso
está bien para CI y para el deploy del operador, y es un obstáculo cuando querés
construir la imagen **en tu propia máquina**, donde el paquete ya está instalado
en el venv.

El camino offline usa la rueda local en vez del token:

```bash
# 1. Empaquetar lo que ya está instalado en el venv (no baja nada de la red)
python -m wheel pack <ruta_al_venv>/Lib/site-packages/nosilo_core \
  -d vendor/    # necesita también nosilo_core-<ver>.dist-info en el mismo árbol

# 2. Construir sin ningún secreto
docker build --build-arg CORE_FROM_WHEEL=1 -t kern:local .
```

Con `CORE_FROM_WHEEL=1` el build instala la rueda primero y reemplaza el
requerimiento por URL directa de `pyproject.toml` **dentro de la imagen** por un
`nosilo-core` a secas, para que pip lo dé por satisfecho en vez de volver a
buscarlo en git. `pyproject.toml` en tu working tree no se toca.

## Lo que NO resuelve

La CI. Un runner limpio no tiene el venv del que sale la rueda, así que
`.github/workflows/tests.yml` sigue necesitando el secret `NOSILO_CORE_TOKEN`.
Publicar la imagen tampoco sale de acá: eso es una decisión de distribución, no
de build local (ver
`docs/superpowers/specs/2026-08-05-self-hosted-image-packaging.md`).
