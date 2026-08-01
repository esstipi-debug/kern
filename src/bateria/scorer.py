"""Scorer de la bateria: puntua Kern corriendolo, no describiendolo.

Dos reglas de diseno que no se negocian, porque son lo que hace que el numero
signifique algo:

1. **Se puntua el entregable, no la libreria.** Todo check consume la salida de
   la capa ``jobs/`` — lo que un cliente recibiria. Puntuar ``src/`` directo
   infla el resultado: la matematica suele existir en ``src/`` y el job no le
   pasa los datos (patron verificado 4+ veces, ver KERN_AUTOPSIA_FALLAS.md).
2. **Falta de dato no es falla de codigo.** Si el dataset no esta, el estado es
   ``SIN_DATO``, no ``NO_CUMPLE``. 11 de las tareas estan bloqueadas por dato del
   cliente y ningun PR las mueve; confundir las dos cosas manda al loop a
   escribir codigo que no cambia el puntaje.

Aritmetica dual (decision del 01-ago-2026). El denominador es siempre 20. Lo que
esta en disputa es el *techo*: el doc publicado cuenta 3 tareas humanas (techo
85%) y la fuente marca 5 (techo 75%). Se reportan los dos hasta que se corrija el
doc — ver ``src/bateria/README.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from src.bateria import TareaBateria, cargar_bateria, tareas_humanas

#: Puntos que suma cada estado. Un "parcial" vale medio punto, como en la corrida
#: original (7,5% = 1,5/20).
_PUNTOS = {"CUMPLE": 1.0, "PARCIAL": 0.5}

#: Tareas humanas segun el doc publicado, para reportar su techo en paralelo.
_HUMANAS_SEGUN_DOC = frozenset({"T15", "T16", "T17"})

TOTAL_TAREAS = 20


class Estado(str, Enum):
    """Resultado de correr una tarea de la bateria contra Kern."""

    CUMPLE = "cumple"
    PARCIAL = "parcial"
    NO_CUMPLE = "no_cumple"
    #: El codigo podria cumplir, pero el dato que la tarea exige no existe.
    SIN_DATO = "sin_dato"
    #: Ningun software puede cumplirla (la fuente la marca TAREA HUMANA).
    FUERA_DE_ALCANCE = "fuera_de_alcance"
    #: Todavia no se escribio la comprobacion. Nunca suma puntos.
    PENDIENTE = "pendiente"


@dataclass(frozen=True)
class ResultadoTarea:
    """El veredicto de una tarea, con la evidencia que lo sostiene."""

    tarea_id: str
    estado: Estado
    #: Por que se dio ese estado, en una linea citable.
    evidencia: str
    #: Que se corrio para llegar ahi (tool, dataset, filas).
    detalle: dict[str, object] = field(default_factory=dict)

    @property
    def puntos(self) -> float:
        return _PUNTOS.get(self.estado.name, 0.0)


@dataclass(frozen=True)
class Contexto:
    """Dónde estan los datos con los que se corre la bateria."""

    data_root: Path | None = None

    def dataset(self, nombre: str) -> Path | None:
        """Ruta al dataset si existe; ``None`` si falta (-> ``SIN_DATO``)."""
        if self.data_root is None:
            return None
        ruta = self.data_root / nombre
        return ruta if ruta.exists() else None


#: Una comprobacion corre la tarea y devuelve su veredicto con evidencia.
Comprobacion = Callable[[Contexto], ResultadoTarea]

_REGISTRO: dict[str, Comprobacion] = {}


def comprobacion(tarea_id: str) -> Callable[[Comprobacion], Comprobacion]:
    """Registra la comprobacion de una tarea."""

    def _wrap(fn: Comprobacion) -> Comprobacion:
        if tarea_id in _REGISTRO:
            raise ValueError(f"{tarea_id} ya tiene comprobacion registrada")
        _REGISTRO[tarea_id] = fn
        return fn

    return _wrap


def _pendiente(tarea: TareaBateria) -> ResultadoTarea:
    return ResultadoTarea(
        tarea_id=tarea.id,
        estado=Estado.PENDIENTE,
        evidencia="comprobacion no escrita todavia",
    )


def _fuera_de_alcance(tarea: TareaBateria) -> ResultadoTarea:
    return ResultadoTarea(
        tarea_id=tarea.id,
        estado=Estado.FUERA_DE_ALCANCE,
        evidencia="la fuente la marca TAREA HUMANA: ningun software la cumple",
    )


@dataclass(frozen=True)
class Puntaje:
    """El marcador completo, con las dos aritmeticas en disputa."""

    resultados: tuple[ResultadoTarea, ...]

    @property
    def puntos(self) -> float:
        return sum(r.puntos for r in self.resultados)

    @property
    def porcentaje(self) -> float:
        """Puntaje de Kern sobre las 20 tareas (la cifra publicable)."""
        return 100.0 * self.puntos / TOTAL_TAREAS

    @property
    def techo_segun_fuente(self) -> float:
        """Techo de cualquier software si 5 tareas son humanas (T15-T19)."""
        return 100.0 * (TOTAL_TAREAS - len(tareas_humanas())) / TOTAL_TAREAS

    @property
    def techo_segun_doc(self) -> float:
        """Techo que afirma el doc publicado, que cuenta 3 humanas."""
        return 100.0 * (TOTAL_TAREAS - len(_HUMANAS_SEGUN_DOC)) / TOTAL_TAREAS

    def por_estado(self, estado: Estado) -> tuple[ResultadoTarea, ...]:
        return tuple(r for r in self.resultados if r.estado is estado)

    @property
    def cobertura(self) -> float:
        """Fraccion de tareas de software con comprobacion escrita."""
        software = [r for r in self.resultados if r.estado is not Estado.FUERA_DE_ALCANCE]
        if not software:
            return 0.0
        escritas = [r for r in software if r.estado is not Estado.PENDIENTE]
        return len(escritas) / len(software)


def puntuar(contexto: Contexto | None = None) -> Puntaje:
    """Corre toda la bateria y devuelve el marcador.

    Las tareas humanas nunca se corren. Las que no tienen comprobacion escrita
    quedan ``PENDIENTE`` y suman cero — el puntaje nunca se infla por omision.
    """
    contexto = contexto or Contexto()
    resultados = []
    for tarea in cargar_bateria().tareas:
        if tarea.es_humana:
            resultados.append(_fuera_de_alcance(tarea))
            continue
        check = _REGISTRO.get(tarea.id)
        resultados.append(check(contexto) if check else _pendiente(tarea))
    return Puntaje(resultados=tuple(resultados))
