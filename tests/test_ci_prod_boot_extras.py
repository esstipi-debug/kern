"""Toda ruta que instala esta app tiene que traer el control plane sin el cual
no arranca -- y el job que dice reproducir produccion, tambien.

Contexto medido, no hipotetico (2026-08-23):

`nosilo-core` esta deliberadamente FUERA de `[project.dependencies]` -- asi un
`pip install -e .` funciona sin token, para un evaluador externo. La contracara
es que solo llega si la linea de instalacion nombra el extra `[core]`. Entre #226
(2026-08-03, que saco `nosilo_core` del repo y lo puso en ese extra) y el
2026-08-23, NINGUNA de las cuatro rutas lo nombraba: ni el `prod-boot` de
tests.yml, ni el Dockerfile (Fly), ni el `buildCommand` de railway.json, ni el
"Run it" de docs/DEPLOYMENT.md. Las tres ultimas hasta exigen el token y
reescriben la URL de git para resolver una dependencia privada que despues no
piden.

Consecuencia, reproducida en venvs py3.12 limpios con las lineas viejas:

    ModuleNotFoundError: No module named 'nosilo_core'
    webapp/app.py:40 -> jobs/abc_xyz_job.py:18 -> from nosilo_core.guided import ...

O sea: un `docker build` desde un checkout limpio ya producia una imagen que no
puede bindear :8000, y `prod-boot` -- el job que existe justamente para agarrar
eso -- se habria puesto rojo en su primera corrida real. No la tuvo: la ultima
corrida verde (2026-08-03T16:14Z) probo un merge ref anterior a #226, y desde
entonces todas mueren en el paso de autenticacion por un secret que nunca se
configuro. La imagen viva sigue en pie solo porque se construyo de un arbol que
todavia traia `nosilo_core/` como fuente.

Lo que se fija aca:

1. El extra que trae nosilo-core esta en las CUATRO rutas. No hardcodeado: se
   deriva de pyproject, asi que si alguien renombra el extra o mueve la
   dependencia a `[project.dependencies]`, este test lo sigue.
2. `prod-boot` nunca instala MAS extras que la imagen. Esa es la direccion
   peligrosa: mas extras aca = un boot verde que revienta en Fly, que es
   exactamente el corte de produccion del 2026-07-16 que creo este job (#156).
3. Todo extra nombrado existe en pyproject. `pip` solo AVISA sobre un extra
   desconocido -- un `[mpc]` mal tipeado no instala nada, no falla, y el paso
   queda verde midiendo otra cosa.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RAIZ = Path(__file__).resolve().parents[1]

#: `pip install -e ".[a,b,c]"`, con comillas o sin ellas, y tolerando el
#: escapado que necesita el JSON de railway.
_INSTALL = re.compile(r"""pip\s+install\s+-e\s+\\?["']?\.\[([^\]]*)\]""")

#: Bloques ``` ... ``` de un markdown: en un doc, el unico texto que alguien
#: copia y pega es el que esta adentro de un fence. La prosa de alrededor
#: menciona a proposito lineas que NO hay que usar (por ejemplo `.[web]` a
#: secas, para explicar por que falla), y contarlas seria un falso positivo.
_FENCE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


# --- de donde sale cada linea de instalacion ---------------------------------


def _texto_prod_boot() -> str:
    wf = yaml.safe_load((RAIZ / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
    runs = [
        str(p["run"])
        for p in wf["jobs"]["prod-boot"]["steps"]
        if "run" in p and _INSTALL.search(str(p["run"]))
    ]
    assert len(runs) == 1, f"se esperaba un solo paso que instale en prod-boot, hay {len(runs)}"
    return runs[0]


def _texto_dockerfile() -> str:
    return (RAIZ / "Dockerfile").read_text(encoding="utf-8")


def _texto_railway() -> str:
    return json.loads((RAIZ / "railway.json").read_text(encoding="utf-8"))["build"]["buildCommand"]


def _texto_deployment_md() -> str:
    doc = (RAIZ / "docs/DEPLOYMENT.md").read_text(encoding="utf-8")
    return "\n".join(_FENCE.findall(doc))


#: Las cuatro rutas por las que esta app llega a estar instalada en algun lado.
FUENTES = {
    "prod-boot (.github/workflows/tests.yml)": _texto_prod_boot,
    "Dockerfile (Fly)": _texto_dockerfile,
    "railway.json (buildCommand)": _texto_railway,
    "docs/DEPLOYMENT.md (Run it)": _texto_deployment_md,
}


def _extras(fuente: str) -> set[str]:
    """Los extras de la UNICA linea `pip install -e ".[...]"` de esa fuente.

    Deliberadamente limitado, por la misma razon que `_evaluar` en
    test_ci_authenticate_core.py: si aparece una segunda linea de instalacion,
    este test revienta en vez de elegir una y aprobar a ciegas.
    """
    encontrados = _INSTALL.findall(FUENTES[fuente]())
    assert len(encontrados) == 1, (
        f'se esperaba exactamente un `pip install -e ".[...]"` en {fuente}, hay '
        f"{len(encontrados)}: {encontrados}"
    )
    return {e.strip() for e in encontrados[0].split(",") if e.strip()}


# --- 1. ninguna ruta de instalacion necesita una credencial ------------------
#
# El espejo exacto del test que vivia aca hasta el 2026-09-11. Hasta entonces
# `nosilo_core` era un repo privado y lo que habia que vigilar era lo contrario:
# que las cuatro rutas NOMBRARAN el extra que lo traia, porque una que lo perdiera
# desplegaba una imagen que no bindea el puerto. Ahora el core vive en este repo
# y lo que hay que vigilar es que ninguna ruta vuelva a pedir un secreto: eso
# costo diez dias sin imagen publicada y dejaba a un partner pidiendo credenciales
# para correr Kern.

_CREDENCIAL = re.compile(
    r"NOSILO_CORE_TOKEN|NOSILO_CORE_SSH_KEY|nosilo_token|x-access-token|"
    r"git\+https://github\.com/esstipi-debug/nosilo-core",
    re.I,
)


@pytest.mark.parametrize("fuente", list(FUENTES))
def test_ninguna_ruta_de_instalacion_pide_una_credencial(fuente):
    """EL TEST QUE IMPORTA, dado vuelta.

    `import webapp.app` recorre jobs/ y scm_agent/, que importan `nosilo_core` en
    el top level. Eso no cambio: lo que cambio es de donde sale el modulo. Vive en
    `nosilo_core/` de este mismo repo, asi que cualquier reaparicion de un token,
    de una deploy key o de la URL git del repo privado en una ruta de instalacion
    es una regresion al estado que este PR cerro.
    """
    encontrado = _CREDENCIAL.findall(FUENTES[fuente]())
    assert not encontrado, (
        f"{fuente} volvio a pedir una credencial para el core: {sorted(set(encontrado))}. "
        "nosilo_core vive en el repo (nosilo_core/); no hay nada que autenticar."
    )


def test_el_core_esta_en_el_repo_y_en_el_wheel():
    """Que este en el arbol no alcanza: si el wheel no lo lleva, `pip install .`
    produce un paquete que muere en el primer import. Ese error ya paso una vez
    con doce subpaquetes de src/ y nadie lo vio, porque ningun deploy instala el
    wheel -- todos hacen `pip install -e .` y resuelven por PYTHONPATH."""
    assert (RAIZ / "nosilo_core" / "__init__.py").exists()
    incluidos = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    assert any(patron.startswith("nosilo_core") for patron in incluidos), (
        f"nosilo_core no esta en packages.find.include ({incluidos}): el wheel "
        "saldria sin el control plane"
    )


def _pyproject() -> dict:
    return tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))


def test_prod_boot_no_instala_mas_extras_que_la_imagen():
    """La asimetria tiene una sola direccion segura.

    Mas estricto que la imagen (menos extras) = puede gritar por una dependencia
    que produccion si tiene: molesto, visible, arreglable. Mas permisivo (mas
    extras) = un boot verde que revienta en el deploy, que es literalmente el
    corte del 2026-07-16 que hizo nacer este job (#156).
    """
    de_mas = _extras("prod-boot (.github/workflows/tests.yml)") - _extras("Dockerfile (Fly)")
    assert not de_mas, (
        f"prod-boot instala extras que la imagen no tiene: {sorted(de_mas)}. Un boot "
        f"que pasa aca puede reventar igual en el deploy."
    )


# --- 3. un extra mal tipeado no falla solo -----------------------------------


@pytest.mark.parametrize("fuente", list(FUENTES))
def test_todo_extra_nombrado_existe_en_pyproject(fuente):
    """`pip` solo emite un WARNING sobre un extra desconocido. Un `[mpc]` en vez
    de `[mcp]` no instala nada, no falla, y el paso queda verde probando otra
    cosa."""
    declarados = set(_pyproject()["project"].get("optional-dependencies", {}))
    desconocidos = _extras(fuente) - declarados
    assert not desconocidos, (
        f"{fuente} nombra extras que pyproject no declara: {sorted(desconocidos)}. "
        f"pip los ignora con un warning y el paso queda verde sin instalarlos."
    )
