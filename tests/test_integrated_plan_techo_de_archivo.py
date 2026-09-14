"""El techo de archivo del playbook A5.

REPRODUCCION DEL HALLAZGO. ``jobs/integrated_plan.py`` habia quedado en 1051
lineas (1119 despues de la ronda de plazos), contra un techo de 800 en el repo.
La ronda anterior habia extraido 170 lineas a ``jobs/integrated_plan_cash.py``
y despues habia metido 300 de vuelta, asi que el archivo CRECIO 130 netas
mientras la extraccion figuraba como hecha. Peor: el docstring del modulo
declaraba la violacion por escrito ("this file was already over the repo's
800-line ceiling") en vez de cerrarla, que es como una regla rota se vuelve
documentacion.

La reproduccion es contar lineas, y por eso el primer test de abajo FALLA contra
el archivo de antes del fix y pasa contra el de despues.

El segundo test extiende una guarda que ya existia. ``test_integrated_plan_job.py``
mide funciones de mas de 50 lineas, pero SOLO sobre ``ip.__file__``: una funcion
larga que se mude a un hermano deja de estar medida justo cuando la extraccion la
mueve. Ese test no fallaba antes del fix y no se pretende que lo haga -- cubre los
modulos que el fix creo, para que el eje no pueda volver a engordar del otro lado
del corte, que es exactamente como la ronda anterior perdio las 130 lineas.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jobs import integrated_plan as ip

#: CLAUDE.md / rules de estilo del repo: 200-400 lineas tipico, 800 maximo.
TECHO_LINEAS_ARCHIVO = 800
#: Misma fuente: funciones de menos de 50 lineas.
TECHO_LINEAS_FUNCION = 50


def _modulos_del_playbook() -> list[Path]:
    """``integrated_plan.py`` y cada hermano al que se le extrajo un eje.

    Se descubren por glob y no por lista escrita a mano a proposito: un eje nuevo
    que salga a un hermano nuevo queda medido el dia que se crea, sin que nadie se
    acuerde de agregarlo aca.
    """
    return sorted(Path(ip.__file__).parent.glob("integrated_plan*.py"))


def _fuente(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


def test_el_glob_encuentra_el_modulo_principal_y_al_menos_un_hermano():
    """Ancla la lista que los dos tests parametrizados recorren.

    Sin esto, un glob que dejara de encontrar archivos convertiria a los dos
    tests de abajo en cero casos, y cero casos verdes se leen igual que una
    guarda que paso.
    """
    nombres = {ruta.name for ruta in _modulos_del_playbook()}
    assert "integrated_plan.py" in nombres
    assert nombres - {"integrated_plan.py"}, "no se encontro ningun modulo hermano"


@pytest.mark.parametrize("ruta", _modulos_del_playbook(), ids=lambda r: r.name)
def test_ningun_modulo_del_playbook_pasa_el_techo_de_800_lineas(ruta: Path):
    """La reproduccion del hallazgo: contar."""
    lineas = len(_fuente(ruta).splitlines())
    assert lineas <= TECHO_LINEAS_ARCHIVO, (
        f"{ruta.name}: {lineas} lineas, {lineas - TECHO_LINEAS_ARCHIVO} sobre el techo de "
        f"{TECHO_LINEAS_ARCHIVO}. Extraer un eje a un modulo hermano, no documentar el exceso."
    )


@pytest.mark.parametrize("ruta", _modulos_del_playbook(), ids=lambda r: r.name)
def test_ninguna_funcion_del_playbook_pasa_el_techo_de_50_lineas(ruta: Path):
    """La guarda de funciones, ahora tambien sobre los hermanos."""
    arbol = ast.parse(_fuente(ruta))
    largas = {
        nodo.name: nodo.end_lineno - nodo.lineno + 1
        for nodo in ast.walk(arbol)
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
        and nodo.end_lineno - nodo.lineno + 1 > TECHO_LINEAS_FUNCION
    }
    assert largas == {}, f"{ruta.name}: funciones sobre {TECHO_LINEAS_FUNCION} lineas: {largas}"
