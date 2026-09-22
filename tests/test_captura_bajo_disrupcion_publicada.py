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


# ---------------------------------------------------------------------------
# El regrabado del 2026-09-22: antes/despues, el pareado, y la mitad rechazada
# ---------------------------------------------------------------------------
#
# La mitad limpia se regrabo y la de shock NO se pudo (ver el documento). Eso deja
# cuatro archivos en juego en vez de dos, y tres afirmaciones nuevas que sin guard se
# despegan solas: cuanto mejoro, que mejoro en TODOS los mundos, y que la corrida con
# disrupcion esta rechazada por la compuerta del propio barrido.

CONGELADA_LIMPIA = RAIZ / "documentation" / "lineas_base" / "captura_limpia_b1e3293.json"
CONGELADA_SHOCK = RAIZ / "documentation" / "lineas_base" / "captura_bajo_shock_b1e3293.json"
PAREADA = RAIZ / "documentation" / "lineas_base" / "captura_limpia_pareada.json"
#: La epoca intermedia: con la cobertura provisional y SIN el arreglo de lectura de
#: `jobs/intake.py`. Se congela por la misma razon que la del 18-sep -- es la mitad
#: "antes" del segundo arreglo, y el barrido sobreescribe en el lugar.
CONGELADA_MEDIA = RAIZ / "documentation" / "lineas_base" / "captura_limpia_a7892b7.json"
RECHAZADA = RAIZ / "documentation" / "lineas_base" / "captura_bajo_shock_RECHAZADA.json"


def _leer(ruta: Path) -> dict:
    assert ruta.is_file(), f"falta {ruta.relative_to(RAIZ)}"
    return json.loads(ruta.read_text(encoding="utf-8"))


def _por_nivel(datos: dict) -> dict[int, dict]:
    return {d["nivel"]: d for d in datos["distribuciones"] if d["carril"] == "forced"}


def _doc_plano() -> str:
    """El documento con los espacios normalizados.

    Una afirmacion no puede depender de DONDE cae el salto de linea: reformatear un
    parrafo pondria el test en rojo sin que el documento haya cambiado de significado,
    y un test que se rompe al reformatear se acaba borrando en vez de arreglando.
    """
    return " ".join(DOC.read_text(encoding="utf-8").replace(">", " ").split())


def test_los_congelados_son_comparables_con_los_vigentes(datos_limpios):
    """Restar dos barridos solo mide el cambio de codigo si TODO lo demas es igual.
    Si alguien regraba con otra escala u otras semillas, el "+26,8 puntos" pasa a medir
    dos experimentos distintos sin que nada lo diga."""
    antes, ahora = _leer(CONGELADA_LIMPIA)["contexto"], datos_limpios["contexto"]
    for clave in ("mundos", "semilla_inicial", "n_skus", "n_proveedores",
                  "semanas", "replan_cada", "niveles", "shocks", "metrica"):
        assert antes[clave] == ahora[clave], (
            f"la linea base congelada y la vigente difieren en {clave!r}: "
            f"{antes[clave]!r} contra {ahora[clave]!r}. Restarlas mediria eso."
        )


def test_el_documento_cita_las_frases_de_las_lineas_base_congeladas():
    """La columna "antes" tiene que poder envejecer tan poco como la de "despues":
    es la mitad de toda resta que el documento publica."""
    texto = DOC.read_text(encoding="utf-8")
    faltan = [Distribucion(**d).frase()
              for datos in (_leer(CONGELADA_LIMPIA), _leer(CONGELADA_SHOCK),
                            _leer(CONGELADA_MEDIA))
              for d in datos["distribuciones"]
              if Distribucion(**d).frase() not in texto]
    assert not faltan, ("el documento no cita estas frases de las lineas base "
                        "congeladas:\n  " + "\n  ".join(faltan))


def test_el_delta_publicado_es_la_resta_de_los_dos_archivos(datos_limpios):
    """+26,8 puntos no es un numero tipeado: es `vigente - congelada` en L0."""
    antes, ahora = _por_nivel(_leer(CONGELADA_LIMPIA)), _por_nivel(datos_limpios)
    delta = (ahora[0]["media"] - antes[0]["media"]) * 100
    assert round(delta, 1) == 26.8, f"el delta real es {delta:+.1f}, no +26,8"
    texto = DOC.read_text(encoding="utf-8")
    assert "+26,8" in texto
    # Y los extremos que la tabla del documento publica, uno por uno.
    for campo, esperado in (("mediana", 30.1), ("p90", 27.2), ("mejor", 28.7),
                            ("peor", 12.3)):
        real = (ahora[0][campo] - antes[0][campo]) * 100
        assert round(real, 1) == esperado, f"{campo}: real {real:+.1f}, documento {esperado:+.1f}"


def test_el_pareado_sostiene_que_mejoro_en_TODOS_los_mundos():
    """"Mejora en 25 de 25" es la afirmacion fuerte, y la que una media no puede dar.
    Si algun mundo empeorara, la frase del documento seria falsa aunque la media subiera."""
    r = _leer(PAREADA)["resumen"]["forced-L0"]
    assert r["n_pareables"] == 25
    assert r["mejoran"] == 25 and r["empeoran"] == 0, r
    texto = DOC.read_text(encoding="utf-8")
    assert "25 de 25 mundos" in texto
    for valor in (f"+{r['min_delta'] * 100:.1f}".replace(".", ","),
                  f"+{r['max_delta'] * 100:.1f}".replace(".", ","),
                  f"+{r['mediana_delta'] * 100:.1f}".replace(".", ",")):
        assert valor in texto, f"el documento no cita {valor} del pareado"


def test_el_pareado_reproduce_los_agregados_de_SU_epoca():
    """El control de que el pareado es el MISMO experimento que los agregados, y no una
    tercera corrida parecida.

    Su "despues" es la epoca INTERMEDIA (`a7892b7`), no la vigente: el pareado mide la
    cobertura provisional, y despues de el llego un segundo arreglo que movio los niveles
    sucios. Compararlo contra la linea base de hoy mediria las dos palancas juntas y
    diria que el pareado esta mal.
    """
    r = _leer(PAREADA)["resumen"]
    antes, despues = _por_nivel(_leer(CONGELADA_LIMPIA)), _por_nivel(_leer(CONGELADA_MEDIA))
    for nivel in (0, 1):  # L2/L3 tienen mundos no medibles, asi que la media difiere
        clave = f"forced-L{nivel}"
        assert r[clave]["media_antes"] == pytest.approx(antes[nivel]["media"], abs=1e-9)
        assert r[clave]["media_despues"] == pytest.approx(despues[nivel]["media"], abs=1e-9)
    assert _leer(PAREADA)["procedencia"]["sha_despues"] in CONGELADA_MEDIA.name


def test_el_pareado_nombra_de_que_dos_commits_es():
    """Su mitad "antes" no se puede rehacer desde este checkout -- hace falta un worktree
    del commit viejo. Sin los dos sha adentro, el archivo no es reproducible."""
    proc = _leer(PAREADA)["procedencia"]
    assert proc["sha_antes"] and proc["sha_despues"]
    assert proc["sha_antes"] != proc["sha_despues"]
    assert "worktree" in proc["que_es"].lower()


def test_la_corrida_con_shock_esta_rechazada_y_no_se_usa_como_linea_base():
    """`verify_fidelidad.py` se NEGO a grabarla (`degrada_con_la_suciedad` disparo). Se
    conserva como evidencia. El dia que alguien la ascienda a linea base sin volver a
    medir, esto se pone rojo."""
    ctx = _leer(RECHAZADA)["contexto"]
    assert "RECHAZADA" in ctx, "el archivo no declara que esta rechazado"
    assert "degrada_con_la_suciedad" in ctx["RECHAZADA"]
    # No puede ser la vigente: la vigente es la que el documento cita como del 18-sep.
    assert _leer(RECHAZADA)["distribuciones"] != _leer(LINEA_BASE)["distribuciones"]
    assert "no es una linea base" in _doc_plano()


def test_las_dos_columnas_son_de_la_MISMA_epoca(datos, datos_limpios):
    """Reemplaza al guard que exigia que la columna con shock siguiera siendo la del
    18-sep. Ese se puso rojo el 22-sep cuando por fin se logro regrabarla, que es
    exactamente el dia para el que se escribio.

    Lo que hay que fijar ahora es lo contrario: que las dos columnas se hayan medido
    juntas. La resta entre ellas es "el costo del shock", y con una mitad de otra epoca
    mide la diferencia entre dos versiones del codigo con forma de disrupcion.
    """
    assert _leer(CONGELADA_SHOCK)["distribuciones"] != datos["distribuciones"], (
        "la columna con shock volvio a ser la congelada del 18-sep: el documento no "
        "puede publicar un costo del shock restandole la limpia de hoy"
    )
    # El mismo experimento en todo menos el shock, que es lo que hace valida la resta.
    a, b = datos_limpios["contexto"], datos["contexto"]
    for clave in ("mundos", "semilla_inicial", "n_skus", "n_proveedores",
                  "semanas", "replan_cada", "niveles"):
        assert a[clave] == b[clave], clave


def test_el_costo_del_shock_publicado_es_la_resta_de_los_dos_archivos(datos, datos_limpios):
    """-7,1 puntos no es un numero tipeado: sale de restar las dos lineas base."""
    limpia, shock = _por_nivel(datos_limpios), _por_nivel(datos)
    costo = (shock[0]["media"] - limpia[0]["media"]) * 100
    assert round(costo, 1) == -7.1, f"el costo real es {costo:+.1f}, no -7,1"
    texto = DOC.read_text(encoding="utf-8")
    assert "7,1 puntos" in texto
    # Y en los niveles sucios sale POSITIVO, que el documento tiene que explicar en vez
    # de publicarlo como si la disrupcion ayudara.
    for nivel in (2, 3):
        assert (shock[nivel]["media"] - limpia[nivel]["media"]) > 0, nivel
    assert "el piso tambien sufre el shock" in _doc_plano()


def test_la_inversion_de_la_columna_limpia_esta_EXPLICADA_por_la_fidelidad(datos_limpios):
    """L3 captura mejor que L2 en la linea base publicada, y eso ya no es una anomalia.

    Este test fija las tres mitades del argumento a la vez, sobre el archivo publicado y
    no sobre un fixture: que la inversion SUPERA el margen de ruido (o sea que la
    compuerta vieja, la que comparaba ordinales, habria disparado); que el archivo del
    nivel 3 es MAS fiel; y que por eso la compuerta calla pero `inversiones_explicadas`
    habla. Si alguna de las tres deja de valer, el documento publica una tabla cuyo orden
    no puede justificar.
    """
    from src.verify.barrido import (
        _margen,
        degrada_con_la_suciedad,
        inversiones_explicadas,
    )

    d = {nivel: Distribucion(**x) for nivel, x in _por_nivel(datos_limpios).items()}
    brecha = (d[3].media - d[2].media) * 100
    margen = _margen(d[2], d[3]) * 100
    assert brecha > margen, (
        "la inversion cabe en el ruido: este test no esta probando la exencion, y el "
        "documento no deberia estar explicandola"
    )
    assert d[3].error_de_fidelidad < d[2].error_de_fidelidad, (
        "si el archivo del nivel 3 dejara de ser el mas fiel, la inversion vuelve a ser "
        "una anomalia y hay que investigarla, no publicarla"
    )
    dists = tuple(d.values())
    assert degrada_con_la_suciedad(dists, "forced") == []
    assert len(inversiones_explicadas(dists, "forced")) == 1

    texto = DOC.read_text(encoding="utf-8")
    for valor in (f"{d[2].fidelidad_media:.3f}".replace(".", ","),
                  f"{d[3].fidelidad_media:.3f}".replace(".", ",")):
        assert valor in texto, f"el documento no cita la fidelidad {valor}"


def test_toda_distribucion_vigente_declara_su_fidelidad(datos_limpios):
    """Sin fidelidad medida la compuerta acusa (no se exculpa con una ausencia), asi que
    una linea base vigente sin el campo no se podria volver a grabar."""
    for x in datos_limpios["distribuciones"]:
        assert x.get("fidelidad_media") is not None, x
