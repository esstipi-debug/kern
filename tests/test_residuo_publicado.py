"""`KERN_RESIDUO_DE_LA_BRECHA.md` no puede despegarse de lo que se midio.

Un documento con numeros es una deuda: el dia que alguien regraba la linea base y no
actualiza el texto, el texto sigue sonando igual de seguro. Estos tests atan el
documento a `lineas_base/residuo_de_la_brecha.json` y, sobre todo, fijan las tres formas
en que un documento asi miente sin que nadie lo note:

1. **midiendo otro experimento** y presentandolo como continuacion de uno publicado;
2. **llamando arreglo a un carril que un cliente no puede correr**;
3. **publicando una mejora promedio** sobre una dispersion que la vuelve no accionable.

El primero se cierra con la comprobacion mas fuerte que hay a mano: el carril de control
de esta medicion tiene que salir IDENTICO -- al ultimo digito -- al carril `kern` de
`por_que_bajo_el_piso_con_cobertura.json`. No parecido: identico. Si difiere, algo del
mundo, del arnes o del motor cambio, y entonces el residuo que se diagnostica no es el
residuo que se publico.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "documentation" / "lineas_base" / "residuo_de_la_brecha.json"
COBERTURA = RAIZ / "documentation" / "lineas_base" / "por_que_bajo_el_piso_con_cobertura.json"
DOC = RAIZ / "documentation" / "KERN_RESIDUO_DE_LA_BRECHA.md"

#: Los carriles que un cliente podria correr HOY con sus propias recepciones, y los que
#: solo miden una causa. Separarlos es el punto del documento; mezclarlos seria
#: prometer un arreglo que no existe.
DESPLEGABLES = {"plazo_obs", "sigma_obs", "proveedor_medido"}
DIAGNOSTICOS = {"servicio_econ", "todo"}


def _plano(texto: str) -> str:
    """El documento SIN su tipografia: sin negritas, con el menos ASCII y en una sola
    linea.

    Las afirmaciones de abajo son sobre el CONTENIDO. Compararlas contra el markdown
    crudo obligaria a escribir la prosa alrededor de los saltos de linea y a no poder
    poner una frase en negrita sin romper un test, que es la cola meneando al perro --
    y ademas dejaria pasar el caso real: un numero escrito con el menos tipografico
    (U+2212) no es el mismo string que el que emite `f"{x:.4f}"`.
    """
    limpio = texto.replace("\u2212", "-").replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", limpio)


@pytest.fixture(scope="module")
def datos() -> dict:
    return json.loads(BASE.read_text())


@pytest.fixture(scope="module")
def doc() -> str:
    return _plano(DOC.read_text())


# ---------------------------------------------------------------------------
# 1. Es el MISMO experimento que el que dejo la pregunta abierta
# ---------------------------------------------------------------------------


def test_el_control_es_identico_al_carril_publicado(datos):
    """La comprobacion que hace que todo lo demas signifique algo.

    El documento anterior publico -27,6% con la cobertura provisional puesta. Si este
    carril de control no reprodujera ESE numero, el residuo que se diagnostica aqui
    seria otro, y las comparaciones contra el no valdrian.
    """
    previo = json.loads(COBERTURA.read_text())

    assert datos["resumen"]["kern"]["capture"] == previo["promedios"]["kern"]["capture"]
    # Y no solo la captura: las tres varas del cociente, al ultimo digito.
    for carril in ("piso", "techo", "kern"):
        assert datos["resumen"][carril]["total"] == previo["promedios"][carril]["total"], (
            f"el carril {carril} no sale igual que en la linea base publicada; "
            "algo del mundo, del arnes o del motor cambio"
        )


def test_los_parametros_son_los_de_la_linea_base(datos):
    p, previo = datos["parametros"], json.loads(COBERTURA.read_text())["contexto"]
    assert p["semillas"] == previo["semillas"]
    assert p["n_skus"] == previo["n_skus"]
    assert p["n_proveedores"] == previo["n_proveedores"]
    assert p["semanas"] == previo["semanas"]
    assert p["replan"] == previo["replan_cada"]
    assert p["nivel"] == previo["nivel"]


def test_el_documento_cita_el_control_que_midio(datos, doc):
    captura = datos["resumen"]["kern"]["capture"]
    assert f"{abs(captura) * 100:.2f}" .replace(".", ",") in doc, (
        "el documento tiene que citar el control con el que se comparo todo"
    )
    assert "es el mismo experimento" in doc


# ---------------------------------------------------------------------------
# 2. Desplegable y diagnostico no se pueden mezclar
# ---------------------------------------------------------------------------


def test_cada_carril_declara_su_clase(datos):
    for carril in DESPLEGABLES:
        assert datos["resumen"][carril]["clase"] == "desplegable"
    for carril in DIAGNOSTICOS:
        assert datos["resumen"][carril]["clase"] == "diagnostico"
    assert datos["resumen"]["kern"]["clase"] == "control"


def test_el_documento_dice_por_que_el_carril_grande_NO_es_un_arreglo(doc, datos):
    """La trampa mas facil de este documento: `servicio_econ` es la palanca mas grande
    y es justo la que Kern no puede accionar. Publicar su numero sin decirlo seria
    prometer +18,9 puntos que nadie puede cobrar."""
    mejor = max(DESPLEGABLES | DIAGNOSTICOS,
                key=lambda c: datos["resumen"][c]["capture"])
    assert mejor in DIAGNOSTICOS, (
        "si la palanca mas grande pasara a ser desplegable, este test sobra y el "
        "documento hay que reescribirlo -- que es el punto de que falle"
    )
    assert "no puede accionar" in doc
    assert "no exporta precio" in doc
    assert "canal, no de motor" in doc


def test_los_carriles_desplegables_no_miran_el_futuro():
    """La regla se cumple en el codigo, no solo en la prosa: `_plazo_observado` solo
    cuenta ordenes que YA llegaron, y un SKU sin ninguna llegada no entra al override
    -- no se le inventa una media."""
    from scripts.residuo_de_la_brecha import _plazo_observado
    from src.simworld.world import generate_world

    world = generate_world(seed=1000, n_skus=4, n_suppliers=2, weeks=12)

    class _Out:
        def __init__(self, week, ordered):
            self.week, self.ordered = week, ordered

    # Nadie pidio nada todavia: no hay nada observado, y eso es un dict VACIO, no ceros.
    medias, desvios = _plazo_observado(world, [], week=6)
    assert medias == {} and desvios == {}

    # Una sola orden llegada da media pero NO desvio: con una observacion no hay
    # dispersion que estimar, y publicar 0,0 diria "medida, y no varia".
    import numpy as np
    una = [_Out(0, np.array([1.0, 0.0, 0.0, 0.0]))]
    medias, desvios = _plazo_observado(world, una, week=11)
    assert set(medias) <= {world.skus[0].sku}
    assert desvios == {}


# ---------------------------------------------------------------------------
# 3. La dispersion se publica, no se esconde detras del promedio
# ---------------------------------------------------------------------------


def test_el_documento_publica_la_tabla_por_semilla(datos, doc):
    """Un promedio a un decimal sobre un control que va de -0,02 a -0,60 sugiere una
    precision que no existe. La tabla por semilla tiene que estar."""
    for fila in datos["por_semilla"]:
        esperado = f"{fila['kern']['capture']:.4f}".replace(".", ",")
        assert esperado in doc, (
            f"falta la captura {esperado} de la semilla {fila['seed']} en la tabla "
            "por semilla del documento"
        )


def test_un_carril_que_empeora_en_la_mayoria_no_se_presenta_como_mejora(datos, doc):
    """`proveedor_medido` promedia +4,9 puntos y empeora en 3 de 5 mundos. Un documento
    que publique el promedio sin el conteo recomienda algo que falla mas veces de las
    que funciona."""
    pm = datos["resumen"]["proveedor_medido"]
    assert pm["mejora_vs_kern"] > 0, "si dejara de mejorar en promedio, reescribir el parrafo"
    assert pm["mundos_que_mejoran"] < 3, (
        "si pasara a mejorar en la mayoria, la advertencia del documento sobra"
    )
    assert "no es confiable" in doc
    assert f"{pm['mundos_que_mejoran']}/5" in doc


def test_apilar_palancas_es_PEOR_que_la_mejor_sola_y_el_documento_lo_dice(datos, doc):
    """El hallazgo contraintuitivo, y el que mas facil se pierde al resumir."""
    todo = datos["resumen"]["todo"]["capture"]
    mejor_sola = datos["resumen"]["servicio_econ"]["capture"]
    assert todo < mejor_sola, (
        "si apilar dejara de ser peor, la seccion R3.3 del documento quedo invertida"
    )
    assert "Apilar palancas es peor" in doc


def test_el_mecanismo_es_el_stock_terminal_y_es_monotono(datos, doc):
    """La explicacion no es una narracion: es una columna que sube en el mismo orden en
    que las palancas mejoran el quiebre. Si dejara de ser monotona, la explicacion del
    documento seria una coincidencia y no un mecanismo."""
    r = datos["resumen"]
    orden = ["kern", "sigma_obs", "proveedor_medido", "servicio_econ", "todo"]
    terminales = [r[c]["stock_terminal_usd"] for c in orden]
    assert terminales == sorted(terminales), (
        f"el stock terminal dejo de subir con cada palanca: {dict(zip(orden, terminales))}"
    )
    assert r["techo"]["stock_terminal_usd"] == pytest.approx(0.0, abs=1.0)
    # Y el remate: `todo` compra MAS que el oraculo, que tiene informacion perfecta.
    assert r["todo"]["unidades_pedidas"] > r["techo"]["unidades_pedidas"]
    assert "Comprar más que el oráculo" in doc


# ---------------------------------------------------------------------------
# 4. El defecto que aparecio midiendo sigue arreglado
# ---------------------------------------------------------------------------


def test_sigma_obs_NO_puede_volver_a_salir_identico_al_control(datos):
    """El guard de regresion del arreglo de `scm_agent/tools.py`.

    Antes de arreglarlo, este carril salia identico al de control en los cinco mundos
    porque el agente tiraba `lead_time_stds` en silencio. Si volviera a pasar, la linea
    base lo mostraria asi y el documento seguiria contando una historia que ya no
    ocurre. El contrato de comportamiento vive en `tests/test_overrides_no_se_tiran.py`;
    esto fija que la MEDICION tambien lo refleje.
    """
    for fila in datos["por_semilla"]:
        assert fila["sigma_obs"]["capture"] != fila["kern"]["capture"], (
            f"semilla {fila['seed']}: el carril de la dispersion del plazo volvio a "
            "salir identico al control; el agente esta tirando `lead_time_stds` otra vez"
        )


def test_el_documento_cuenta_el_defecto_y_su_magnitud(doc):
    assert "nunca lo reenviaba" in doc
    assert "11,9 a 238,4" in doc
    # Y lo encuadra como lo que es: el defecto de la rama AL REVES.
    assert "al revés" in doc.replace("**", "")


# ---------------------------------------------------------------------------
# 5. Lo que el documento declara que NO dice
# ---------------------------------------------------------------------------


def test_el_documento_declara_sus_limites(datos, doc):
    assert "sino 5" in doc, "tiene que decir que mide 5 de los 25 mundos"
    assert "no mide que una política con horizonte sí lo cerraría" in doc, (
        "la conclusion apunta al horizonte SIN haberlo medido, y eso hay que decirlo"
    )
    assert "No toca la asignación entre SKU" in doc


def test_la_frase_citable_sale_de_la_medicion(datos, doc):
    from scripts.residuo_de_la_brecha import frase

    assert _plano(frase(datos)) in doc, (
        "la frase citable del documento tiene que ser la que emite el script, no una "
        "reescrita a mano que puede quedar vencida"
    )
