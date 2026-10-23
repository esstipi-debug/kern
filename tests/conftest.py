"""Configuracion de la suite: `tests/` entra al sys.path para los helpers locales.

Hasta el 2026-09-11 este archivo tenia otra razon de ser: saltear, bajo
KERN_CI_NO_CORE=1, todo test cuyo grafo de imports llegara a `nosilo_core`, para
que la CI pudiera correr sin la credencial del repo privado. El core vive en este
repo desde el PR-3 y la suite entera corre en cualquier checkout.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from src.connectors.lectura import ENV_INTERPRETE as ENV_LECTURA

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# El interruptor KERN_CI_NO_CORE existio del 2026-08-25 al 2026-09-11 y salteaba
# todo archivo de test cuyo grafo de imports llegara a nosilo_core -- el 70% de la
# suite -- para que la CI pudiera correr sin la credencial del repo privado. Ya no
# hay credencial ni repo privado: el core vive en nosilo_core/ y la suite entera
# corre en cualquier checkout. Un gate que saltea 5.000 tests deja de ser un gate.
collect_ignore: list[str] = []


@pytest.fixture(autouse=True, scope="session")
def _suite_sin_indice_de_lectura():
    """La suite no levanta el indice de lectura, aunque la maquina lo tenga instalado.

    ``KERN_LECTURA_PYTHON`` (ver ``src/connectors/lectura.py`` y ``.env.example``)
    enciende el indice de libros: 1,6 GB de RSS y ~17 s por consulta. Una corrida de
    paquete lo consulta una vez, y ``tests/test_packages.py`` corre los ocho paquetes
    de punta a punta -- con la variable puesta, la suite del que construyo el indice
    pasaria minutos levantando servidores que ningun test necesita, y en una maquina
    justa de memoria ademas se pondria inestable. Un test no puede depender de como
    esta configurada la maquina que lo corre.

    No deja el camino vivo sin cubrir: ``tests/test_packages_lecturas.py`` setea la
    variable a mano -- contra un servidor falso para el timeout, y contra el indice de
    verdad en el test marcado ``integracion``, que ``pyproject.toml`` deselecciona por
    defecto.
    """
    previo = os.environ.pop(ENV_LECTURA, None)
    yield
    if previo is not None:
        os.environ[ENV_LECTURA] = previo


@pytest.fixture(autouse=True, scope="session")
def _suite_con_aprobaciones_sin_firma():
    """La suite corre con el opt-in de desarrollo de las aprobaciones sin firma.

    Sin ``LINCHPIN_APPROVAL_SECRET``, ``nosilo_core.writeback.apply()`` rechaza todo
    cambio que necesita aprobacion. Los tests que hacen ``approve()`` + ``apply()``
    prueban la escritura, no la configuracion del deploy, asi que corren con
    ``LINCHPIN_ALLOW_UNSIGNED_APPROVALS=1``: el mismo interruptor que usa quien
    desarrolla en local. El secreto de la maquina se saca por la misma razon que el
    indice de lectura de arriba.

    No deja el rechazo sin cubrir: ``tests/test_writeback.py`` borra las dos
    variables con ``monkeypatch`` para probar lo que corre un deploy que no las seteo.
    """
    nombres = ("LINCHPIN_APPROVAL_SECRET", "LINCHPIN_ALLOW_UNSIGNED_APPROVALS")
    previos = {nombre: os.environ.pop(nombre, None) for nombre in nombres}
    os.environ["LINCHPIN_ALLOW_UNSIGNED_APPROVALS"] = "1"
    yield
    for nombre, valor in previos.items():
        os.environ.pop(nombre, None)
        if valor is not None:
            os.environ[nombre] = valor
