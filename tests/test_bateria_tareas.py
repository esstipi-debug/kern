"""La bateria de 20 tareas es la vara externa con la que se puntua Kern.

Estos tests protegen la *fuente*, no el puntaje: si alguien edita
``src/bateria/tareas.json`` para acomodar el resultado, la vara deja de valer.
Ver ``src/bateria/README.md`` para la procedencia.
"""

from __future__ import annotations

from src.bateria import CAMPOS_TAREA, cargar_bateria, tareas_humanas, tareas_software

_ESPERADAS = 20


def test_la_bateria_tiene_exactamente_20_tareas():
    bateria = cargar_bateria()
    assert len(bateria.tareas) == _ESPERADAS


def test_los_ids_son_T01_a_T20_sin_huecos_ni_repetidos():
    ids = [t.id for t in cargar_bateria().tareas]
    assert ids == [f"T{n:02d}" for n in range(1, _ESPERADAS + 1)]


def test_toda_tarea_trae_los_seis_campos_no_vacios():
    for tarea in cargar_bateria().tareas:
        for campo in CAMPOS_TAREA:
            valor = getattr(tarea, campo)
            assert isinstance(valor, str) and valor.strip(), f"{tarea.id}.{campo} vacio"


def test_toda_tarea_declara_criterio_de_aprobado_accionable():
    """Sin criterio no hay forma de puntuar la tarea corriendola."""
    for tarea in cargar_bateria().tareas:
        assert len(tarea.criterio_de_aprobado) > 80, f"{tarea.id}: criterio demasiado corto"


def test_la_particion_humana_software_cubre_las_20_sin_solapar():
    bateria = cargar_bateria()
    humanas = {t.id for t in tareas_humanas(bateria)}
    software = {t.id for t in tareas_software(bateria)}
    assert humanas & software == set()
    assert humanas | software == {t.id for t in bateria.tareas}


def test_la_fuente_marca_cinco_tareas_humanas_no_tres():
    """Regresion sobre una discrepancia real de la vara publicada.

    ``documentation/KERN_BATERIA_20_TAREAS.md`` afirma "3 tareas deliberadamente
    humanas" (T15/T16/T17) y de ahi deriva "techo humano 85% - 17/20". La fuente
    original marca ``TAREA HUMANA`` en cinco: T15-T19. El doc tambien atribuye a
    T18 "campos tipados del ledger", cuando T18 es "sostener el plan frente a la
    presion interna". Este test congela lo que dice la fuente para que la
    correccion del doc sea deliberada y no se pierda de vista.
    """
    assert {t.id for t in tareas_humanas(cargar_bateria())} == {
        "T15",
        "T16",
        "T17",
        "T18",
        "T19",
    }


def test_la_nota_de_imparcialidad_sigue_presente():
    """Lo que hace valida la vara es que no se derivo del repo."""
    bateria = cargar_bateria()
    assert "No lei el repositorio" in bateria.nota_de_imparcialidad
    assert "SCOR" in bateria.fuente_del_estandar
