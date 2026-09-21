"""`KERN_HORIZONTE_DE_LA_VARA.md` no puede despegarse de lo que se midio.

El documento contesta una pregunta que otro documento dejo abierta -- cuanto del residuo
de la brecha es la vara y no Kern -- y la contesta con un NO. Una respuesta negativa es
la mas facil de dejar podrir: nadie la vuelve a mirar, y el dia que el arnes cambie el
texto va a seguir sonando igual de seguro.

Estos tests fijan las cuatro formas en que esta medicion concreta se vuelve falsa:

1. **el control se rompe** -- piso y Kern son ciegos al horizonte; si sus costos sobre la
   ventana se movieran al mover la fecha de fin, el experimento no estaria aislando el
   horizonte y todo lo demas seria de otra cosa;
2. **el truncado deja de ser puro** -- si `truncar` re-sorteara, las fechas de fin no
   compartirian la realizacion de demanda y la comparacion interna no existiria;
3. **el credito de borde vuelve a contar solo el estante** -- el sesgo que aparecio
   midiendo, y que era asimetrico entre carriles;
4. **un span degenerado sale como numero** -- la cota superior se da vuelta en un mundo,
   y un `nan` promediado se publica como si fuera una medicion.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "documentation" / "lineas_base" / "horizonte_de_la_vara.json"
RESIDUO = RAIZ / "documentation" / "lineas_base" / "residuo_de_la_brecha.json"
DOC = RAIZ / "documentation" / "KERN_HORIZONTE_DE_LA_VARA.md"


def _plano(texto: str) -> str:
    """El documento SIN su tipografia: sin negritas, con el menos ASCII, en una linea."""
    limpio = texto.replace("−", "-").replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", limpio)


@pytest.fixture(scope="module")
def datos() -> dict:
    return json.loads(BASE.read_text())


@pytest.fixture(scope="module")
def doc() -> str:
    return _plano(DOC.read_text())


# ---------------------------------------------------------------------------
# 1. El control: piso y Kern son ciegos al horizonte
# ---------------------------------------------------------------------------


def test_el_control_se_cumple_en_todas_las_semillas(datos):
    """La comprobacion que hace que todo lo demas signifique algo.

    Si el costo del piso o de Kern sobre `[0,W)` cambiara al mover la fecha de fin, la
    fecha les habria llegado por algun camino -- la lente, el estado congelado, el rng --
    y entonces la diferencia que el documento le atribuye al techo seria de otra cosa.
    """
    colas = [str(k) for k in datos["parametros"]["colas"]]
    for fila in datos["por_semilla"]:
        ref = fila["por_cola"][colas[0]]
        for k in colas[1:]:
            for carril in ("piso", "kern"):
                assert fila["por_cola"][k][carril]["costo"] == ref[carril]["costo"], (
                    f"semilla {fila['seed']}, carril {carril}: el costo sobre la ventana "
                    f"cambio al mover la fecha de fin a K={k}"
                )


def test_el_techo_con_fecha_de_fin_llega_al_borde_VACIO(datos):
    """La afirmacion estructural de H4: un Wagner-Whitin que sabe cuando se acaba el
    mundo no deja nada pagado y sin vender -- ni en el estante ni viajando.

    Si dejara de ser cero, el contraste del experimento se diluye: el K=0 dejaria de ser
    el caso puro de 'fecha de fin conocida' y las tres filas se pareceria entre si por
    una razon que no es el horizonte.
    """
    primera = str(datos["parametros"]["colas"][0])
    for fila in datos["por_semilla"]:
        borde = fila["por_cola"][primera]["techo"]
        assert borde["a_costo"] == 0.0, f"semilla {fila['seed']}: el techo de K=0 dejo borde"
        assert borde["unidades_canieria"] == 0.0
        assert borde["unidades_estante"] == 0.0


# ---------------------------------------------------------------------------
# 2. El truncado es una transformacion PURA
# ---------------------------------------------------------------------------


def test_truncar_no_re_sortea_y_por_eso_la_ventana_es_la_misma():
    from scripts.horizonte_de_la_vara import truncar
    from src.simworld.world import generate_world

    largo = generate_world(seed=1000, n_skus=4, n_suppliers=2, weeks=36)
    corto = truncar(largo, 24)

    assert corto.weeks == 24
    assert np.array_equal(corto.demand, largo.demand[:, :24])
    assert np.array_equal(corto.real_lead_weeks, largo.real_lead_weeks[:, :24])
    # Lo que NO se toca: el catalogo y los proveedores son el mismo sorteo.
    assert corto.skus == largo.skus
    assert corto.suppliers == largo.suppliers
    assert corto.seed == largo.seed


def test_re_sortear_NO_serviria_y_por_eso_hay_que_truncar():
    """La razon por la que `truncar` existe, fijada como hecho y no como comentario.

    `_seasonal_trend_demand` normaliza la tendencia por `max(weeks, 1)` y sortea el ruido
    en un bloque `(n_skus, weeks)`, asi que dos mundos de la misma semilla y distinto
    largo difieren TAMBIEN en sus primeras semanas. Si algun dia coincidieran, truncar
    seria innecesario -- y este test, que se pondria rojo, es como se avisaria.
    """
    from src.simworld.world import generate_world

    corto = generate_world(seed=1000, n_skus=4, n_suppliers=2, weeks=24)
    largo = generate_world(seed=1000, n_skus=4, n_suppliers=2, weeks=36)
    assert not np.array_equal(corto.demand, largo.demand[:, :24])


def test_truncar_no_puede_estirar():
    from scripts.horizonte_de_la_vara import truncar
    from src.simworld.world import generate_world

    world = generate_world(seed=1000, n_skus=2, n_suppliers=1, weeks=12)
    with pytest.raises(ValueError, match="no se puede estirar"):
        truncar(world, 24)


# ---------------------------------------------------------------------------
# 3. El credito de borde incluye la caniera, y la caniera PESA
# ---------------------------------------------------------------------------


def test_el_borde_es_estante_mas_canieria(datos):
    for fila in datos["por_semilla"]:
        for cola in fila["por_cola"].values():
            for carril, v in cola.items():
                assert v["a_costo"] == pytest.approx(
                    v["estante_a_costo"] + v["canieria_a_costo"]
                ), f"semilla {fila['seed']}, {carril}: el borde no cierra"
                assert v["unidades"] == pytest.approx(
                    v["unidades_estante"] + v["unidades_canieria"]
                )


def test_la_canieria_no_es_un_detalle(datos):
    """Si fuera chica, contar solo el estante habria sido una aproximacion aceptable y
    H4 sobraria. Se mide que no lo es."""
    primera = str(datos["parametros"]["colas"][0])
    estante = canieria = 0.0
    for fila in datos["por_semilla"]:
        for carril in ("piso", "kern"):
            estante += fila["por_cola"][primera][carril]["estante_a_costo"]
            canieria += fila["por_cola"][primera][carril]["canieria_a_costo"]
    assert canieria > estante, (
        "la canieria dejo de pesar mas que el estante; H4 hay que volver a escribirla"
    )


def test_el_documento_cuenta_el_sesgo_que_aparecio_midiendo(doc):
    assert "cero exacto en las cinco semillas" in doc
    assert "1,8x el estante" in doc.replace("×", "x")
    # Y que nombra la afirmacion ajena que el defecto alcanzaba -- mas que la nombra:
    # la mando a medir, y el resultado vive en este mismo documento.
    assert "R3.3" in doc
    assert "la monotonia es falsa" in doc.replace("í", "i")
    # Lo que NO se re-midio tambien se declara, que es la otra mitad de la honestidad.
    assert "Esa linea base no se regrabo" in doc.replace("í", "i").replace("ó", "o")


# ---------------------------------------------------------------------------
# 4. Un span degenerado no puede salir vestido de numero
# ---------------------------------------------------------------------------


def test_captura_devuelve_None_y_nunca_nan_con_span_degenerado():
    import math

    from scripts.horizonte_de_la_vara import captura

    # El piso, acreditado, pasa a costar MENOS que el techo: la vara se da vuelta.
    cola = {
        "piso": {"costo": 100.0, "a_costo": 50.0},
        "kern": {"costo": 110.0, "a_costo": 10.0},
        "techo": {"costo": 60.0, "a_costo": 0.0},
    }
    v = captura(cola, credito="a_costo", s=1.0)
    assert v is None, f"un span degenerado salio como {v!r} en vez de None"
    assert not (isinstance(v, float) and math.isnan(v))

    # Y con span sano devuelve el numero.
    assert captura(cola, credito="a_costo", s=0.0) == pytest.approx((100 - 110) / (100 - 60))


def test_el_resumen_declara_cuantos_mundos_se_pudieron_medir(datos):
    n = len(datos["parametros"]["semillas"])
    degenerado = False
    for k, r in datos["resumen"].items():
        for credito in ("a_costo", "a_precio"):
            for s in ("0.00", "1.00"):
                medibles = r[credito][f"{s}_mundos_medibles"]
                assert 0 <= medibles <= n
                if r[credito][s] is None:
                    assert medibles == 0
                if medibles < n:
                    degenerado = True
    assert degenerado, (
        "ya no se degenera en ningun lado; H5 del documento describe algo que no pasa"
    )


# ---------------------------------------------------------------------------
# 5. La conclusion sale de la medicion, no de la prosa
# ---------------------------------------------------------------------------


def test_el_efecto_acreditado_es_chico_en_TODOS_los_mundos(datos):
    """El hallazgo, como propiedad por semilla y no como promedio.

    Un promedio chico puede esconder dos mundos grandes de signo opuesto. Aca no: el eje
    acreditado tiene que ser chico en cada mundo, y mucho mas chico que el mismo eje sin
    acreditar, que es lo que separa 'el horizonte no importa' de 'la convencion de borde
    era todo el efecto'.
    """
    from scripts.horizonte_de_la_vara import captura

    colas = [str(k) for k in datos["parametros"]["colas"]]
    for fila in datos["por_semilla"]:
        con = [captura(fila["por_cola"][k], credito="a_costo", s=1.0) for k in colas]
        sin = [captura(fila["por_cola"][k], credito="a_costo", s=0.0) for k in colas]
        d_con, d_sin = abs(con[-1] - con[0]), abs(sin[-1] - sin[0])
        assert d_con < 0.05, (
            f"semilla {fila['seed']}: mover la fecha de fin movio la captura acreditada "
            f"{d_con:.4f}; el documento afirma que es chico en los cinco mundos"
        )
        assert d_con < d_sin, (
            f"semilla {fila['seed']}: el efecto acreditado ({d_con:.4f}) dejo de ser "
            f"menor que el no acreditado ({d_sin:.4f}); H3 quedo invertida"
        )


def test_el_rescate_sobre_lo_publicado_casi_no_mueve_al_control(datos):
    pub = datos["rescate_sobre_lo_publicado"]["kern"]
    assert abs(pub["1.00"] - pub["0.00"]) < 0.01, (
        "borrar el castigo de fin de horizonte sobre las corridas publicadas paso a "
        "mover la captura; el camino 1 del documento hay que reescribirlo"
    )
    # Y el residuo que se queria explicar sigue siendo el que el documento cita.
    previo = json.loads(RESIDUO.read_text())["resumen"]["kern"]["capture"]
    assert pub["0.00"] == pytest.approx(previo), (
        "el camino 1 dejo de reproducir la captura publicada del residuo"
    )


def test_la_frase_citable_sale_de_la_medicion(datos, doc):
    from scripts.horizonte_de_la_vara import frase

    assert _plano(frase(datos)) in doc, (
        "la frase citable del documento tiene que ser la que emite el script, no una "
        "reescrita a mano que puede quedar vencida"
    )


def test_el_documento_declara_que_las_capturas_NO_son_comparables(datos, doc):
    """La trampa mas facil de este documento: sus capturas absolutas salen de mundos
    generados a 48 semanas y truncados, que no son los de la linea base publicada.
    Compararlas contra el -27,6 % seria exactamente el error que el documento anterior
    se cuido de no cometer."""
    assert "no son comparables" in doc
    assert "La comparacion que este camino sostiene es interna" in doc.replace("ó", "o")
