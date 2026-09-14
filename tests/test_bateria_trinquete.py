"""Trinquete: la bateria puede subir, nunca bajar.

**Por que este fichero existe.** ``src/bateria`` es la unica vara que mide si el
producto sirve para el oficio, y hasta ahora nada la congelaba: su propio README
lo admitia -- "un cambio en ``jobs/`` puede mover el marcador sin que nada se
ponga rojo. Re-corrrela antes de citarla". Eso convierte cada cifra publicada en
una foto que caduca en el siguiente commit, y ya paso: un handoff cito 35,00%
con T13 y T14 en CUMPLE cuando la pila de PR que las cerraba no habia bajado.

``tests/test_bateria_scorer.py`` cubre la aritmetica del marcador; esto cubre el
resultado. Son cosas distintas: la aritmetica puede estar perfecta mientras el
producto se degrada.

**Solo trinquete, nunca objetivo.** Estos tests no exigen que el puntaje suba.
Exigen que no baje sin que alguien lo decida a mano. Si una mejora legitima
cambia un veredicto a la baja -- endurecer un check, corregir un criterio mal
leido -- la linea base de abajo se actualiza EN EL MISMO PR, con la razon en el
mensaje de commit. Lo que no puede pasar es que baje y nadie se entere.

**No relajar un check para que esto pase.** Si este fichero se pone rojo, la
respuesta correcta es arreglar ``jobs/`` o registrar la bajada a proposito.
Aflojar ``comprobaciones.py`` sube el marcador sin arreglar nada y sin tocar la
fuente, asi que no deja rastro donde se mira -- es la forma mas silenciosa que
tiene este repo de mentirse.
"""

from __future__ import annotations

import pytest

# El import NO es opcional: puebla el registro de checks por efecto secundario
# del decorador ``@comprobacion``. Sin el, las 15 tareas salen PENDIENTE, que
# suma cero, y el marcador da 0,0% sin un solo error -- un trinquete montado
# sobre ese cero pasaria siempre y no protegeria nada.
import src.bateria.comprobaciones  # noqa: F401
from src.bateria.scorer import Estado, puntuar

#: Medido sobre 976124e con ``Contexto()`` vacio. Es el suelo, no la meta.
PUNTOS_MINIMOS = 7.5

#: El veredicto minimo aceptable por tarea. Congelar SOLO el agregado dejaria
#: pasar el intercambio silencioso -- una tarea que cae de CUMPLE a PARCIAL
#: mientras otra sube de NO_CUMPLE a PARCIAL deja el total intacto y esconde
#: una regresion real detras de una mejora real.
#:
#: Las cinco de T15-T19 no aparecen: la fuente las marca TAREA HUMANA y su
#: veredicto no depende del codigo.
SUELO_POR_TAREA: dict[str, Estado] = {
    "T01": Estado.CUMPLE,
    "T02": Estado.PARCIAL,
    "T03": Estado.SIN_DATO,
    "T04": Estado.CUMPLE,
    "T05": Estado.NO_CUMPLE,
    "T06": Estado.CUMPLE,
    "T07": Estado.NO_CUMPLE,
    "T08": Estado.CUMPLE,
    "T09": Estado.CUMPLE,
    "T10": Estado.NO_CUMPLE,
    "T11": Estado.SIN_DATO,
    "T12": Estado.PARCIAL,
    "T13": Estado.PARCIAL,
    "T14": Estado.NO_CUMPLE,
    "T20": Estado.CUMPLE,
}

#: Cuanto vale cada veredicto, para poder decir "no bajo". SIN_DATO no es peor
#: que NO_CUMPLE ni mejor: es otra cosa (falta dato del cliente, ningun PR la
#: mueve), asi que se compara por identidad y no por orden -- ver el test.
VALOR = {
    Estado.NO_CUMPLE: 0,
    Estado.PARCIAL: 1,
    Estado.CUMPLE: 2,
}


@pytest.fixture(scope="module")
def puntaje():
    return puntuar()


def test_el_puntaje_agregado_no_baja(puntaje):
    assert puntaje.puntos >= PUNTOS_MINIMOS, (
        f"la bateria bajo a {puntaje.puntos} puntos ({puntaje.porcentaje:.2f}%), "
        f"por debajo del suelo de {PUNTOS_MINIMOS}. Arregla la regresion, o baja "
        f"PUNTOS_MINIMOS en este mismo PR explicando por que. No relajes "
        f"src/bateria/comprobaciones.py."
    )


def test_ninguna_tarea_retrocede(puntaje):
    """El intercambio silencioso: una sube, otra baja, el total no se mueve."""
    por_id = {r.tarea_id: r.estado for r in puntaje.resultados}
    retrocesos = []
    for tarea, suelo in SUELO_POR_TAREA.items():
        actual = por_id[tarea]
        if actual is suelo:
            continue
        # SIN_DATO y FUERA_DE_ALCANCE no estan en la escala de calidad: pasar a
        # uno de ellos no es "mejorar" ni "empeorar", es cambiar de categoria, y
        # eso exige una decision humana igual que un retroceso.
        if suelo not in VALOR or actual not in VALOR:
            retrocesos.append(f"{tarea}: {suelo.value} -> {actual.value} (cambio de categoria)")
        elif VALOR[actual] < VALOR[suelo]:
            retrocesos.append(f"{tarea}: {suelo.value} -> {actual.value}")
    assert not retrocesos, (
        "tareas que retrocedieron o cambiaron de categoria: " + "; ".join(retrocesos)
    )


def test_toda_tarea_puntuable_tiene_su_suelo_declarado(puntaje):
    """Una comprobacion nueva sin suelo quedaria fuera del trinquete en silencio."""
    puntuables = {
        r.tarea_id for r in puntaje.resultados
        if r.estado is not Estado.FUERA_DE_ALCANCE
    }
    sin_suelo = puntuables - set(SUELO_POR_TAREA)
    assert not sin_suelo, (
        f"tareas sin suelo en SUELO_POR_TAREA: {sorted(sin_suelo)}. "
        "Anade su veredicto actual para que el trinquete la cubra."
    )


def test_ninguna_tarea_queda_pendiente(puntaje):
    """PENDIENTE suma cero y no se distingue de un check roto que devuelve nada.

    Si una comprobacion desaparece del registro -- un import que se cae, un
    decorador borrado en un merge -- su tarea vuelve a PENDIENTE y el puntaje
    baja sin que nadie toque la logica. El test agregado ya lo atraparia, pero
    este nombra la causa en vez de dejar un numero mas bajo sin explicacion.
    """
    pendientes = [r.tarea_id for r in puntaje.resultados if r.estado is Estado.PENDIENTE]
    assert not pendientes, (
        f"tareas en PENDIENTE: {pendientes}. Su comprobacion no se registro "
        "-- revisa que src.bateria.comprobaciones se importe y que el decorador "
        "@comprobacion siga puesto."
    )


def test_el_techo_sigue_derivandose_de_la_fuente(puntaje):
    """75% = 15 alcanzables de 20. Si la fuente marca una humana mas, el techo
    se mueve solo; lo que no puede es quedarse fijo mintiendo."""
    assert puntaje.techo_segun_fuente == pytest.approx(75.0)
    assert puntaje.puntos <= puntaje.techo_segun_fuente * 20 / 100.0
