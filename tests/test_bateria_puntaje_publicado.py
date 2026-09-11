"""El puntaje publicado de la bateria es el que el harness produce, y esta congelado.

Dos cosas que este archivo cierra (diagnostico del 2026-09-05, hallazgo E1):

1. El README de la bateria decia 37,50% y el harness daba 65,00%: 160 commits de
   distancia entre el numero escrito y el medido, en la unica vara publica que Kern
   propone para si mismo. Ahora el documento tiene que decir lo que dice
   ``scripts/bateria_puntaje.py``.
2. Nada congelaba el agregado: un cambio en ``jobs/`` podia mover el marcador sin
   que nada se pusiera rojo. Ahora el vector de estados esta fijado aqui; subirlo
   o bajarlo es un diff explicito sobre ``PUNTAJE_CONGELADO``.

Corre la bateria entera (unos 60-90 s): es el precio de que el numero sea verdad.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.bateria_puntaje import markdown, resumen

REPO = Path(__file__).resolve().parents[1]
README = REPO / "src" / "bateria" / "README.md"
DOC = REPO / "documentation" / "KERN_BATERIA_20_TAREAS.md"

# Medido sobre main @ 1aa5098 (2026-09-05) con Contexto() vacio.
PUNTAJE_CONGELADO = "65,00% · 13/20"
ESTADOS_CONGELADOS = {
    "cumple": ["T01", "T02", "T04", "T05", "T06", "T07", "T08", "T09", "T10", "T12", "T13", "T14", "T20"],
    "sin_dato": ["T03", "T11"],
    "fuera_de_alcance": ["T15", "T16", "T17", "T18", "T19"],
}


@pytest.fixture(scope="module")
def puntaje():
    import src.bateria.comprobaciones  # noqa: F401  (sin esto todo sale PENDIENTE)
    from src.bateria.scorer import puntuar

    return puntuar()


def test_el_harness_reproduce_el_puntaje_congelado(puntaje):
    assert resumen(puntaje) == PUNTAJE_CONGELADO, (
        f"la bateria dio {resumen(puntaje)}; si el cambio es deliberado, actualiza "
        "PUNTAJE_CONGELADO, ESTADOS_CONGELADOS y los dos documentos con "
        "`python scripts/bateria_puntaje.py --markdown`"
    )
    por_estado = {estado: sorted(t) for estado, _n, t in _tabla(puntaje)}
    assert por_estado == ESTADOS_CONGELADOS


def _tabla(p):
    from scripts.bateria_puntaje import tabla

    return tabla(p)


@pytest.mark.parametrize("doc", [README, DOC], ids=["README", "KERN_BATERIA_20_TAREAS"])
def test_el_documento_publica_el_mismo_numero_que_el_harness(puntaje, doc):
    texto = doc.read_text(encoding="utf-8")
    assert resumen(puntaje) in texto, f"{doc.name} no dice {resumen(puntaje)!r}"
    # ningun puntaje viejo puede quedar como "el de hoy"
    viejos = re.findall(r"\*\*(\d{1,2},\d{2}% · \d{1,2}(?:,\d)?/20)\*\*", texto)
    assert all(v == resumen(puntaje) for v in viejos if "hoy" in texto), viejos


def test_el_readme_trae_la_tabla_por_estado_que_emite_el_script(puntaje):
    assert markdown(puntaje) in README.read_text(encoding="utf-8")
