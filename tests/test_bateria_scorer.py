"""El scorer no puede inflar el puntaje. Estos tests protegen esa propiedad."""

from __future__ import annotations

from src.bateria.scorer import TOTAL_TAREAS, Contexto, Estado, ResultadoTarea, puntuar


def test_puntua_las_20_tareas_siempre():
    assert len(puntuar().resultados) == TOTAL_TAREAS


def test_las_humanas_quedan_fuera_de_alcance_y_no_se_corren():
    fuera = {r.tarea_id for r in puntuar().por_estado(Estado.FUERA_DE_ALCANCE)}
    assert fuera == {"T15", "T16", "T17", "T18", "T19"}


def test_una_tarea_pendiente_suma_cero():
    """Omitir una comprobacion nunca puede subir el puntaje."""
    pendiente = ResultadoTarea("T99", Estado.PENDIENTE, "sin escribir")
    assert pendiente.puntos == 0.0


def test_sin_dato_tampoco_suma():
    """Falta de dato no es cumplimiento; tampoco es falla de codigo."""
    assert ResultadoTarea("T99", Estado.SIN_DATO, "falta CSV").puntos == 0.0


def test_parcial_vale_medio_punto():
    """Regla de la corrida original: 7,5% = 1,5/20."""
    assert ResultadoTarea("T99", Estado.PARCIAL, "a medias").puntos == 0.5
    assert ResultadoTarea("T99", Estado.CUMPLE, "entero").puntos == 1.0


def test_reporta_las_dos_aritmeticas_en_disputa():
    """Decision del 01-ago: se reportan los dos techos hasta corregir el doc."""
    p = puntuar()
    assert p.techo_segun_fuente == 75.0  # 15 de 20 alcanzables por software
    assert p.techo_segun_doc == 85.0  # el doc cuenta 3 humanas, no 5


def test_el_porcentaje_se_calcula_sobre_20_no_sobre_las_alcanzables():
    """La cifra publicable es sobre la bateria entera, como el 7,5% original."""
    p = puntuar()
    assert p.porcentaje == 100.0 * p.puntos / TOTAL_TAREAS


def test_sin_datasets_ninguna_comprobacion_explota():
    """Correr la bateria sin data/ debe dar un marcador, no una excepcion."""
    p = puntuar(Contexto(data_root=None))
    assert len(p.resultados) == TOTAL_TAREAS
    assert all(isinstance(r.estado, Estado) for r in p.resultados)


def test_la_cobertura_reporta_cuantas_comprobaciones_faltan():
    p = puntuar()
    assert 0.0 <= p.cobertura <= 1.0
