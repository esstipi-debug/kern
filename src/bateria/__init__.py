"""Bateria de 20 tareas del oficio: la vara externa con la que se puntua Kern.

Este modulo expone la *fuente* (``tareas.json``) como objetos tipados. No puntua
nada todavia: el scorer que corre cada tarea contra el motor vive aparte, igual
que ``src/simworld/`` separa el mundo simulado de sus runners.

La regla que hace valida la vara: la bateria **no se derivo de las capacidades de
Kern**. Editar ``tareas.json`` para acomodar un resultado destruye la vara — ver
``tests/test_bateria_tareas.py``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_FUENTE = Path(__file__).with_name("tareas.json")

#: Los seis campos que toda tarea de la bateria debe declarar.
CAMPOS_TAREA = (
    "tarea",
    "frecuencia",
    "input_minimo",
    "output_exigido",
    "criterio_de_aprobado",
    "por_que_importa",
)

#: Marca textual con la que la fuente senala una tarea fuera del alcance de software.
_MARCA_HUMANA = "TAREA HUMANA"


@dataclass(frozen=True)
class TareaBateria:
    """Una tarea del oficio, tal como la escribio la fuente. Inmutable a proposito."""

    id: str
    tarea: str
    frecuencia: str
    input_minimo: str
    output_exigido: str
    criterio_de_aprobado: str
    por_que_importa: str

    @property
    def es_humana(self) -> bool:
        """``True`` si la fuente la marca como fuera del alcance de cualquier software."""
        return _MARCA_HUMANA in self.tarea.upper()


@dataclass(frozen=True)
class Bateria:
    """La bateria completa, con su procedencia."""

    tareas: tuple[TareaBateria, ...]
    fuente_del_estandar: str
    nota_de_imparcialidad: str


@lru_cache(maxsize=1)
def cargar_bateria() -> Bateria:
    """Carga la bateria desde ``tareas.json`` (cacheada: la fuente es inmutable)."""
    crudo = json.loads(_FUENTE.read_text(encoding="utf-8"))
    tareas = tuple(
        TareaBateria(id=t["id"], **{campo: t[campo] for campo in CAMPOS_TAREA})
        for t in crudo["tareas"]
    )
    return Bateria(
        tareas=tareas,
        fuente_del_estandar=crudo["fuente_del_estandar"],
        nota_de_imparcialidad=crudo["nota_de_imparcialidad"],
    )


def tareas_humanas(bateria: Bateria | None = None) -> tuple[TareaBateria, ...]:
    """Tareas que ningun software puede cumplir: el techo estructural de la vara."""
    bateria = bateria or cargar_bateria()
    return tuple(t for t in bateria.tareas if t.es_humana)


def tareas_software(bateria: Bateria | None = None) -> tuple[TareaBateria, ...]:
    """Tareas alcanzables por software: el universo que el harness puede puntuar."""
    bateria = bateria or cargar_bateria()
    return tuple(t for t in bateria.tareas if not t.es_humana)
