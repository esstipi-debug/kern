"""Configuracion de la suite: `tests/` entra al sys.path para los helpers locales.

Hasta el 2026-09-11 este archivo tenia otra razon de ser: saltear, bajo
KERN_CI_NO_CORE=1, todo test cuyo grafo de imports llegara a `nosilo_core`, para
que la CI pudiera correr sin la credencial del repo privado. El core vive en este
repo desde el PR-3 y la suite entera corre en cualquier checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

# El interruptor KERN_CI_NO_CORE existio del 2026-08-25 al 2026-09-11 y salteaba
# todo archivo de test cuyo grafo de imports llegara a nosilo_core -- el 70% de la
# suite -- para que la CI pudiera correr sin la credencial del repo privado. Ya no
# hay credencial ni repo privado: el core vive en nosilo_core/ y la suite entera
# corre en cualquier checkout. Un gate que saltea 5.000 tests deja de ser un gate.
collect_ignore: list[str] = []
