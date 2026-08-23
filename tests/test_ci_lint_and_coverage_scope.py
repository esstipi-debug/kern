"""El gate de la CI tiene que mirar TODO el codigo que este repo despacha.

Contexto medido, no hipotetico (2026-08-23, sobre dfaffb7):

* `ruff check packs src tests examples` dejaba SIETE paquetes con .py rastreado
  enteramente fuera del unico lint que existe: `scm_agent/` (20 modulos),
  `jobs/` (79), `webapp/` (29), `scripts/` (17), `odoo_addon/` (7),
  `warehouse/` (6) y `knowledge/` (2). Los dos primeros son donde vive el
  agente. Sobre un checkout limpio ese hueco escondia un I001 real en
  `webapp/security.py`: quedo mal ordenado cuando #226 saco `nosilo_core` del
  repo y lo convirtio en dependencia externa -- la agrupacion que era correcta
  con el paquete adentro dejo de serlo -- y nadie lo vio porque nadie linteaba
  `webapp/`.
* `pytest --cov=packs --cov=src` media lo mismo de menos: el orquestador, el
  router de intents y los 79 jobs podian irse a cero lineas cubiertas sin mover
  el numero que bloquea el merge.

Lo que se fija aca:

1. Ningun paquete con .py rastreado queda fuera del lint. No por lista: se
   deriva de `git ls-files`, asi que un paquete nuevo entra solo.
2. `scm_agent/` y `jobs/` se miden en cobertura.
3. La CI y pyproject miden el MISMO conjunto. Dos fuentes que se despegan hacen
   que el numero que ve el dev no sea el que bloquea el merge.
4. El umbral no se ablanda para que entre un conjunto mas grande.
5. Y el lint corre de verdad aca: mientras la CI siga sin credencial (ver
   tests/test_ci_authenticate_core.py) correr `pytest` a mano es la unica
   proteccion que tiene el repo, y un lint que solo vive en un YAML que nunca
   se ejecuta no protege nada.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOW = RAIZ / ".github" / "workflows" / "tests.yml"

#: Umbral de cobertura por debajo del cual no se baja. Existe para que ampliar
#: el conjunto medido no se pueda "pagar" bajando el numero: si la cobertura
#: real cae debajo, la decision es escribir tests, no mover esta constante.
UMBRAL_MINIMO = 80

#: Directorios con .py rastreado que NO son codigo despachado y por eso quedan
#: fuera del lint. Cada uno con su motivo escrito: una exclusion sin motivo es
#: la que nadie vuelve a revisar.
FUERA_DEL_LINT = {
    ".claude": "config de la herramienta, no del producto; .gitignore la tapa casi entera",
}


# --- lectura del workflow ----------------------------------------------------


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _paso(nombre: str) -> dict:
    """El paso del job `test` cuyo `name` es exactamente `nombre`."""
    pasos = _workflow()["jobs"]["test"]["steps"]
    coincidencias = [p for p in pasos if p.get("name") == nombre]
    assert len(coincidencias) == 1, (
        f"se esperaba exactamente un paso llamado {nombre!r}, hay {len(coincidencias)}"
    )
    return coincidencias[0]


def _comando(nombre: str) -> list[str]:
    """Los tokens del `run:` de un paso de una sola linea.

    Deliberadamente limitado, por la misma razon que `_evaluar` en
    test_ci_authenticate_core.py: si alguien convierte el paso en un script de
    varias lineas, este test revienta en vez de aprobarlo sin entenderlo.
    """
    run = str(_paso(nombre)["run"]).strip()
    assert "\n" not in run, (
        f"el paso {nombre!r} dejo de ser un comando de una sola linea; este test no "
        f"lo sabe leer y prefiere fallar antes que aprobarlo a ciegas:\n{run}"
    )
    return shlex.split(run)


# --- helpers puros -----------------------------------------------------------


def _paquetes_rastreados_con_python() -> set[str]:
    """Directorios de primer nivel que contienen algun .py RASTREADO.

    Rastreado, y no "que existe en el disco", a proposito: el disco tiene restos
    (por ejemplo el `nosilo_core/__pycache__` que dejo #226) que no son codigo
    de este repo -- y que ademas, si ruff los ve, le cambian la clasificacion
    first-party/third-party y le hacen exigir el orden de imports contrario al
    que exige la CI.
    """
    salida = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=RAIZ, capture_output=True, text=True, check=True,
    ).stdout
    paquetes = {
        partes[0]
        for linea in salida.splitlines()
        if len(partes := linea.strip().split("/")) > 1
    }
    assert paquetes, "git ls-files no devolvio ningun .py; el test no puede medir nada"
    return paquetes


def _sin_cubrir(args_ruff: list[str], paquetes: set[str]) -> set[str]:
    """Paquetes que el comando de lint NO alcanza."""
    rutas = [a for a in args_ruff if not a.startswith("-")]
    if "." in rutas:
        return set()
    return {p for p in paquetes if p not in rutas}


def _cov_targets(args_pytest: list[str]) -> set[str]:
    return {a.split("=", 1)[1] for a in args_pytest if a.startswith("--cov=")}


def _cov_fail_under(args_pytest: list[str]) -> int:
    valores = [a.split("=", 1)[1] for a in args_pytest if a.startswith("--cov-fail-under=")]
    assert len(valores) == 1, f"se esperaba un solo --cov-fail-under, hay {valores}"
    return int(valores[0])


def _pyproject() -> dict:
    return tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))


# --- 1. nada con python queda fuera del lint ---------------------------------


def test_el_lint_no_deja_ningun_paquete_con_python_afuera():
    """EL TEST QUE IMPORTA.

    Una lista de rutas hay que editarla cada vez que aparece un paquete, y NO
    falla nada cuando no se edita. Asi se fueron siete.
    """
    args = _comando("Lint")
    assert args[:2] == ["ruff", "check"], f"el paso Lint ya no es un `ruff check`: {args}"

    paquetes = _paquetes_rastreados_con_python() - set(FUERA_DEL_LINT)
    sin_cubrir = _sin_cubrir(args[2:], paquetes)
    assert not sin_cubrir, (
        "estos paquetes tienen .py rastreado y ningun lint los mira: "
        f"{sorted(sin_cubrir)}. Ampliar el alcance del paso Lint, o agregarlos a "
        "FUERA_DEL_LINT con el motivo escrito."
    )


def test_el_chequeo_de_alcance_muerde_de_verdad():
    """Un gate solo acredita si la salida cambia entre dos insumos.

    Con el alcance real no falta nada; con el alcance viejo faltan justo los
    paquetes que motivaron este archivo. Sin esta contraprueba, el test de
    arriba pasaria igual con un `_sin_cubrir` que devolviera siempre `set()`.
    """
    paquetes = _paquetes_rastreados_con_python() - set(FUERA_DEL_LINT)

    assert _sin_cubrir(_comando("Lint")[2:], paquetes) == set()

    alcance_viejo = ["packs", "src", "tests", "examples"]
    perdidos = _sin_cubrir(alcance_viejo, paquetes)
    assert {"scm_agent", "jobs"} <= perdidos, (
        f"el alcance viejo tenia que dejar afuera scm_agent y jobs; dejo {sorted(perdidos)}"
    )


def test_las_exclusiones_del_lint_existen_y_traen_motivo():
    """Una exclusion de un directorio que ya no existe no protege nada y esconde
    que la lista quedo vieja."""
    for nombre, motivo in FUERA_DEL_LINT.items():
        assert (RAIZ / nombre).is_dir(), f"{nombre!r} esta excluido del lint y no existe"
        assert motivo.strip(), f"{nombre!r} esta excluido sin motivo escrito"


# --- 2. la cobertura mide donde vive el agente -------------------------------


def test_la_cobertura_mide_scm_agent_y_jobs():
    medidos = _cov_targets(_comando("Run tests with coverage"))
    assert {"packs", "src", "scm_agent", "jobs"} <= medidos, (
        f"la CI mide {sorted(medidos)}; scm_agent/ y jobs/ son donde vive el agente"
    )


def test_pyproject_y_la_ci_miden_el_mismo_conjunto():
    """Dos fuentes que se despegan hacen que el `pytest --cov` local reporte un
    numero distinto del que bloquea el merge, y el que manda es el que nadie
    mira hasta que la PR se pone roja."""
    en_pyproject = set(_pyproject()["tool"]["coverage"]["run"]["source"])
    en_ci = _cov_targets(_comando("Run tests with coverage"))
    assert en_pyproject == en_ci, (
        f"pyproject mide {sorted(en_pyproject)} y la CI mide {sorted(en_ci)}"
    )


def test_el_umbral_no_se_ablanda():
    """Ampliar el conjunto medido baja el porcentaje. La respuesta a eso es
    escribir tests, no bajar el numero: un gate que se acomoda al resultado que
    obtuvo no es un gate."""
    en_pyproject = int(_pyproject()["tool"]["coverage"]["report"]["fail_under"])
    en_ci = _cov_fail_under(_comando("Run tests with coverage"))

    assert en_ci == en_pyproject, f"la CI corta en {en_ci} y pyproject en {en_pyproject}"
    assert en_ci >= UMBRAL_MINIMO, (
        f"el umbral bajo a {en_ci}; el piso acordado es {UMBRAL_MINIMO}"
    )


# --- 3. el lint corre aca, no solo en un YAML --------------------------------


def test_el_repo_pasa_el_mismo_lint_que_corre_la_ci():
    """Mientras la CI no tenga credencial no corre NADA (ver
    test_ci_authenticate_core.py), asi que correr la suite a mano es la unica
    proteccion real. Un lint que solo vive en el workflow no es proteccion.

    Se ejecuta el mismo subcomando y los mismos argumentos que el workflow, pero
    con `sys.executable -m ruff` en vez del `ruff` del PATH: en la CI son el
    mismo binario (mismo env que instalo requirements-dev.txt), y en una maquina
    de desarrollo el del PATH puede ser el de OTRO virtualenv, con otra version
    y otras reglas.
    """
    args = _comando("Lint")
    proc = subprocess.run(
        [sys.executable, "-m", *args, "--output-format", "concise"],
        cwd=RAIZ, capture_output=True, text=True,
    )
    if proc.returncode == 0:
        return

    salida = (proc.stdout or "").strip() or (proc.stderr or "").strip()

    # Un .py local sin commitear tambien lo agarra ruff, y ese no va a llegar a
    # la CI nunca. Decirlo aca evita mandar a alguien a buscar un fallo de CI
    # que no existe.
    rastreados = set(subprocess.run(
        ["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=True,
    ).stdout.splitlines())
    senalados = {
        m.group(1).replace("\\", "/")
        for m in re.finditer(r"^(.+?\.py):\d+:\d+:", salida, flags=re.MULTILINE)
    }
    sueltos = sorted(senalados - rastreados)

    pytest.fail(
        "`" + " ".join(args) + "` no pasa sobre este arbol:\n" + salida
        + ("\n\nSin rastrear por git (local tuyo, no llega a la CI): "
           f"{sueltos}" if sueltos else "")
    )
