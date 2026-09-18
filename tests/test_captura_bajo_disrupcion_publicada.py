"""La captura bajo disrupcion publicada no puede despegarse de la linea base grabada.

`documentation/KERN_CAPTURA_BAJO_DISRUPCION.md` afirma numeros que salen de
`documentation/lineas_base/captura_bajo_shock.json`. Nada obligaba a que siguieran
coincidiendo: se podia regrabar la linea base y dejar el documento diciendo otra cosa,
o al reves, y el unico sintoma habria sido alguien citando en una propuesta una cifra
que el repo ya no puede reproducir.

Es el mismo mecanismo que `tests/test_graph_lessons_no_deriva.py` sobre GRAPH_LESSONS:
el documento se compara contra su fuente, no contra si mismo.

POR QUE NO SE RE-MIDE ACA
-------------------------
Regrabar la linea base son 25 mundos x 2 pasadas (limpia y perturbada) x 4 niveles:
~55 minutos medidos. Un test asi no corre en CI ni en la maquina de nadie, y un gate
que nadie corre es peor que no tenerlo. Lo que SI se puede verificar con lo commiteado
es que el documento cite exactamente lo que la linea base dice, y que esa linea base
sea de verdad una corrida BAJO DISRUPCION -- que es la confusion que puede hacer que
el numero signifique otra cosa.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.simworld.shocks import parse_shock
from src.verify.barrido import Distribucion

RAIZ = Path(__file__).resolve().parents[1]
LINEA_BASE = RAIZ / "documentation" / "lineas_base" / "captura_bajo_shock.json"
LINEA_BASE_LIMPIA = RAIZ / "documentation" / "lineas_base" / "captura_limpia.json"
DOC = RAIZ / "documentation" / "KERN_CAPTURA_BAJO_DISRUPCION.md"

#: El criterio de hecho de la brecha B, en el diseno de las cuatro brechas:
#: "una linea base grabada con captura bajo shock sobre 25 mundos".
MUNDOS_MINIMOS = 25


@pytest.fixture(scope="module")
def datos() -> dict:
    assert LINEA_BASE.is_file(), (
        f"falta {LINEA_BASE.relative_to(RAIZ)}. Se regraba con:\n"
        "  PYTHONPATH=. python scripts/verify_fidelidad.py --mundos 25 "
        "--shock 'lead_time_spike:*:8:3:3' "
        "--grabar-linea-base documentation/lineas_base/captura_bajo_shock.json"
    )
    return json.loads(LINEA_BASE.read_text(encoding="utf-8"))


def test_la_linea_base_es_de_verdad_una_corrida_bajo_disrupcion(datos):
    """Una linea base sin shock que se llame "bajo disrupcion" es el peor archivo posible:
    sus numeros son capturas LIMPIAS, y comparar contra ella daria siempre tranquilidad."""
    ctx = datos["contexto"]
    assert ctx["metrica"] == "capture_under_shock"
    assert ctx["shocks"], "el contexto no declara ningun shock"
    # Y cada distribucion lo lleva adentro, que es lo que hace que `frase()` lo diga.
    for d in datos["distribuciones"]:
        assert d["metrica"] == "capture_under_shock", d


def test_los_shocks_grabados_siguen_siendo_re_ejecutables(datos):
    """Se graban en el FORMATO que `parse_shock` vuelve a leer, no en prosa: si no,
    reproducir la corrida exige adivinar que se corrio."""
    for texto in datos["contexto"]["shocks"]:
        shock = parse_shock(texto)
        assert shock.as_cli() == texto, f"{texto!r} no sobrevive un ida y vuelta"


def test_el_barrido_cubre_los_mundos_que_el_criterio_pide(datos):
    ctx = datos["contexto"]
    assert ctx["mundos"] >= MUNDOS_MINIMOS, (
        f"{ctx['mundos']} mundos: el criterio de hecho de B pide {MUNDOS_MINIMOS}"
    )
    # La cobertura por grupo no puede ser menor que los mundos barridos.
    for d in datos["distribuciones"]:
        assert d["n_mundos"] <= ctx["mundos"]
        assert d["n_medibles"] <= d["n_mundos"]


def test_el_documento_cita_exactamente_lo_que_la_linea_base_dice(datos):
    """La afirmacion vendible del documento se reconstruye desde el JSON y tiene que
    aparecer LITERAL. Es lo que impide que el documento envejezca solo."""
    assert DOC.is_file(), f"falta {DOC.relative_to(RAIZ)}"
    texto = DOC.read_text(encoding="utf-8")
    faltan = []
    for d in datos["distribuciones"]:
        frase = Distribucion(**d).frase()
        if frase not in texto:
            faltan.append(frase)
    assert not faltan, (
        "el documento no cita estas frases de la linea base (regeneralas o "
        "actualiza el documento):\n  " + "\n  ".join(faltan)
    )


@pytest.fixture(scope="module")
def datos_limpios() -> dict:
    assert LINEA_BASE_LIMPIA.is_file(), (
        f"falta {LINEA_BASE_LIMPIA.relative_to(RAIZ)}. Se regraba con:\n"
        "  PYTHONPATH=. python scripts/verify_fidelidad.py --mundos 25 "
        "--grabar-linea-base documentation/lineas_base/captura_limpia.json"
    )
    return json.loads(LINEA_BASE_LIMPIA.read_text(encoding="utf-8"))


def test_la_linea_base_limpia_es_comparable_con_la_de_shock(datos, datos_limpios):
    """El documento publica las dos columnas una al lado de la otra, y esa lectura
    solo vale si los dos barridos difieren UNICAMENTE en el shock. Si alguien regraba
    una con otra escala o otras semillas, la resta de 15,4 puntos deja de significar
    'lo que costo el shock' y pasa a medir dos experimentos distintos."""
    a, b = datos_limpios["contexto"], datos["contexto"]
    for clave in ("mundos", "semilla_inicial", "n_skus", "n_proveedores",
                  "semanas", "replan_cada", "niveles"):
        assert a[clave] == b[clave], (
            f"las dos lineas base difieren en {clave!r}: limpia={a[clave]!r} "
            f"con shock={b[clave]!r}. Comparar sus capturas mediria eso, no el shock."
        )
    # Y difieren donde tienen que diferir.
    assert a["shocks"] == [] and b["shocks"]
    assert a["metrica"] == "capture" and b["metrica"] == "capture_under_shock"


def test_el_documento_cita_tambien_la_linea_base_limpia(datos_limpios):
    """Sin la columna limpia al lado, un -56,0% no se puede interpretar."""
    texto = DOC.read_text(encoding="utf-8")
    faltan = [Distribucion(**d).frase() for d in datos_limpios["distribuciones"]
              if Distribucion(**d).frase() not in texto]
    assert not faltan, "el documento no cita estas frases de la linea base limpia:\n  " + "\n  ".join(faltan)


def test_el_documento_nombra_el_shock_que_se_corrio(datos):
    """Un numero bajo disrupcion sin decir CUAL disrupcion no se puede interpretar ni
    reproducir. Va el shock en formato CLI, no una descripcion."""
    texto = DOC.read_text(encoding="utf-8")
    for s in datos["contexto"]["shocks"]:
        assert s in texto, f"el documento no nombra el shock {s!r}"
