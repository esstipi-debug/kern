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


# --- lo que pyproject dice sobre nosilo-core ---------------------------------


def _pyproject() -> dict:
    return tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))


def _nombre_pedido(requisito: str) -> str:
    """`nosilo-core @ git+https://...` -> `nosilo-core`."""
    return re.split(r"[\s@\[<>=!;]", requisito.strip(), maxsplit=1)[0].lower()


def _extras_que_traen(paquete: str) -> set[str]:
    opcionales = _pyproject()["project"].get("optional-dependencies", {})
    return {
        nombre
        for nombre, reqs in opcionales.items()
        if any(_nombre_pedido(r) == paquete for r in reqs)
    }


def _es_dependencia_dura(paquete: str) -> bool:
    return any(
        _nombre_pedido(r) == paquete for r in _pyproject()["project"].get("dependencies", [])
    )


def _falta_el_core(extras_instalados: set[str]) -> bool:
    """True si esa linea de instalacion NO va a traer nosilo-core."""
    if _es_dependencia_dura("nosilo-core"):
        return False
    return not (_extras_que_traen("nosilo-core") & extras_instalados)


# --- 1. el control plane llega a las cuatro rutas ----------------------------


def test_pyproject_sigue_teniendo_una_via_para_traer_el_core():
    """Si nosilo-core deja de ser alcanzable desde pyproject, los tests de abajo
    pasarian por vacuidad en vez de por estar bien."""
    assert _es_dependencia_dura("nosilo-core") or _extras_que_traen("nosilo-core"), (
        "nosilo-core no esta ni en [project.dependencies] ni en ningun extra de "
        "pyproject.toml: ninguna linea de instalacion puede traerlo"
    )


@pytest.mark.parametrize("fuente", list(FUENTES))
def test_cada_ruta_de_instalacion_trae_el_core(fuente):
    """EL TEST QUE IMPORTA.

    `import webapp.app` recorre jobs/ y scm_agent/, que importan nosilo_core en
    el top level. Sin el extra, ninguna de estas rutas produce algo que arranque:
    prod-boot no prueba el arranque de produccion sino un ModuleNotFoundError, y
    las otras tres despliegan una imagen que no bindea el puerto.
    """
    extras = _extras(fuente)
    assert not _falta_el_core(extras), (
        f"{fuente} instala {sorted(extras)} y ninguno trae nosilo-core "
        f"(lo traen: {sorted(_extras_que_traen('nosilo-core'))})"
    )


def test_el_chequeo_del_core_muerde_de_verdad():
    """Un gate solo acredita si la salida cambia entre dos insumos.

    Con los extras reales no falta nada; con los de antes del 2026-08-23 falta
    justo lo que tiraba el arranque. Sin esta contraprueba, el test de arriba
    pasaria igual con un `_falta_el_core` que devolviera siempre False.
    """
    for fuente in FUENTES:
        assert not _falta_el_core(_extras(fuente)), fuente

    assert _falta_el_core({"web", "mcp"}), "la linea vieja de prod-boot/railway/docs tenia que faltar"
    assert _falta_el_core({"web", "mcp", "pricing-intel", "dataquality"}), (
        "la linea vieja del Dockerfile tenia que faltar"
    )


# --- 2. prod-boot nunca es MAS permisivo que la imagen -----------------------


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
