"""El generador del corpus de cabeceras sucias, y las dos cosas que lo hacen valer.

El corpus existe para medir `jobs.intake.detect_columns` contra exports que NO
salieron de su propia tabla de alias. Si saliera de ella, la bateria puntuaria
~100 % midiendo la promesa contra si misma -- el mismo defecto que
`webapp/demo_lead_time_scan.py` evita al negarse a leer una columna
`lead_time_days` como observacion.

De ahi las dos invariantes que este archivo protege:

  ceguera      `scripts/build_intake_headers_corpus.py` no importa `jobs.intake`.
               Se verifica sobre el AST del modulo, no sobre lo que este cargado.
  no-espejo    una fraccion del corpus tiene que ser cabeceras que la tabla NO
               conoce. Si ese numero cae a cero el corpus es un espejo, y la
               bateria deja de poder encontrar nada.

Este test SI importa `jobs.intake`: comprobar que el generador es ciego exige
mirar de que es ciego.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jobs.intake import ALIASES, _norm
from scripts.build_intake_headers_corpus import (
    CAMPOS_POR_FORMA,
    DISTRACTORES,
    PERSONALIDADES,
    construir_corpus,
    filas_csv,
)

MODULO = Path(__file__).resolve().parents[1] / "scripts" / "build_intake_headers_corpus.py"

#: Piso de cabeceras desconocidas para la tabla. No es una meta: es el punto por
#: debajo del cual el corpus deja de ser un instrumento de medida.
MIN_CABECERAS_FUERA_DE_TABLA = 20


def _claves_de_alias() -> frozenset[str]:
    return frozenset(_norm(a) for formas in ALIASES.values() for a in formas)


# --- ceguera ---------------------------------------------------------------


def test_el_generador_no_importa_el_modulo_que_mide():
    """La invariante que hace honesto el marcador, verificada sobre el AST.

    Comprobarla sobre `sys.modules` no serviria: para cuando este test corre,
    el propio test ya importo `jobs.intake`.
    """
    arbol = ast.parse(MODULO.read_text(encoding="utf-8"))
    importados: list[str] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados += [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.append(nodo.module)

    culpables = [m for m in importados if m == "jobs" or m.startswith("jobs.")]
    assert not culpables, (
        f"El generador importa {culpables}. El corpus tiene que construirse ciego "
        "a la tabla de alias: si la lee, la bateria mide la tabla contra si misma."
    )


def test_el_corpus_no_es_un_espejo_de_la_tabla_de_alias():
    """Sin cabeceras desconocidas, un 100 % no significa nada."""
    claves = _claves_de_alias()
    corpus = construir_corpus()
    fuera = {c.encabezado for c in corpus if _norm(c.encabezado) not in claves}

    assert len(fuera) >= MIN_CABECERAS_FUERA_DE_TABLA, (
        f"Solo {len(fuera)} cabeceras del corpus son desconocidas para ALIASES "
        f"(minimo {MIN_CABECERAS_FUERA_DE_TABLA}). Un corpus que la tabla ya "
        "cubre entero no puede encontrar nada."
    )


# --- determinismo ----------------------------------------------------------


def test_el_corpus_es_identico_entre_corridas():
    """Misma clave -> misma serie, la regla que `console/charts.js` ya aplica.

    Un corpus que cambia al regenerarlo hace que el `git diff` del fixture sea
    ruido, y que dos marcadores no se puedan comparar.
    """
    assert filas_csv(construir_corpus()) == filas_csv(construir_corpus())


def test_el_generador_no_usa_azar():
    """El determinismo de arriba se puede cumplir por casualidad con una semilla fija.

    Esto prohibe la fuente, no solo el sintoma: sin `random` ni `Math.random`
    equivalente, no hay forma de que el corpus dependa del orden de ejecucion.
    """
    arbol = ast.parse(MODULO.read_text(encoding="utf-8"))
    modulos = {
        n.module
        for n in ast.walk(arbol)
        if isinstance(n, ast.ImportFrom) and n.module
    } | {
        a.name
        for n in ast.walk(arbol)
        if isinstance(n, ast.Import)
        for a in n.names
    }
    assert "random" not in modulos and "numpy.random" not in modulos


# --- forma del corpus ------------------------------------------------------


def test_cada_export_lleva_todos_los_campos_requeridos_de_su_forma():
    """Un export al que le falta un requerido mide otra cosa.

    `detect_columns` marca `unmatched_required`, y un caso al que el requerido
    le falta POR CONSTRUCCION contaria como fallo de deteccion sin serlo.
    """
    corpus = construir_corpus()
    por_export: dict[str, set[str]] = {}
    for c in corpus:
        por_export.setdefault(c.case_id, set()).add(c.campo_canonico)

    for case_id, campos in por_export.items():
        forma = case_id.split("-")[0]
        requeridos = set(CAMPOS_POR_FORMA[forma].requeridos)
        faltan = requeridos - campos
        assert not faltan, f"{case_id} no trae {faltan}"


def test_hay_distractores_y_no_declaran_campo():
    """La otra mitad del marcador: una columna que no es ningun campo canonico.

    Sin distractores la bateria solo puede medir cobertura, y una tabla que
    mapea todo a algo sacaria 100 % siendo inservible.
    """
    corpus = construir_corpus()
    distractores = [c for c in corpus if c.campo_canonico == "ninguno"]
    assert len(distractores) >= 20
    assert all(c.encabezado for c in distractores)


def test_todos_los_distractores_se_usan_al_menos_una_vez():
    """Un distractor declarado y nunca sorteado no prueba nada.

    La primera version rotaba con un modulo sobre el indice de la personalidad,
    y los idiomas con pocas personalidades solo alcanzaban una ventana de su
    lista: `line total` no salia jamas, siendo que `line_total` ESTA en los
    alias de `revenue` y es por tanto uno de los pocos distractores con
    consecuencia medible.
    """
    usados = {_norm(c.encabezado) for c in construir_corpus() if c.campo_canonico == "ninguno"}
    for idioma, formas in DISTRACTORES.items():
        sin_usar = [f for f in formas if _norm(f) not in usados]
        assert not sin_usar, f"distractores {idioma} nunca sorteados: {sin_usar}"


def test_dentro_de_un_export_no_hay_dos_columnas_con_el_mismo_nombre():
    """Un DataFrame con columnas repetidas no mide lo que la bateria cree."""
    por_export: dict[str, list[str]] = {}
    for c in construir_corpus():
        por_export.setdefault(c.case_id, []).append(c.encabezado)
    for case_id, encabezados in por_export.items():
        assert len(encabezados) == len(set(encabezados)), f"{case_id} repite columna"


def test_las_personalidades_no_se_repiten():
    ids = [p.id for p in PERSONALIDADES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("forma", sorted(CAMPOS_POR_FORMA))
def test_cada_forma_produce_al_menos_un_export(forma):
    assert any(c.case_id.startswith(f"{forma}-") for c in construir_corpus())
