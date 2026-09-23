"""`KERN_PALANCAS_SOBRE_25_MUNDOS.md` no puede despegarse de lo que se midio.

Este documento **reordena las palancas de la rama**: dice cual es la mas grande, dice que
cuatro de siete cambian de signo, y corrige por escrito afirmaciones de otros dos
documentos publicados. Un documento que reordena prioridades se cita para decidir en que
trabajar, asi que las formas de volverlo falso son caras:

1. **el control deja de reproducir las lineas base congeladas.** Entonces esto no compara
   dos tamanos de muestra sino dos experimentos, y toda la comparacion se cae;
2. **la separacion entre familias se mezcla.** El hallazgo no es "25 > 5" sino que una
   palanca de "compra menos" escala y una de "compra mas" no, y eso vive en el rango de
   la correlacion;
3. **las cinco lineas base de cinco mundos se tocan.** Siguen siendo la medicion correcta
   de su muestra: si alguien las regraba con 25, se pierde la evidencia del contraste;
4. **el resultado nulo se lee como "no lo vimos".** La potencia contra el tamano
   publicado es lo que lo vuelve una decision, y se recalcula desde la fuente.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
BASES = RAIZ / "documentation" / "lineas_base"
COMPARACION = BASES / "palancas_sobre_25_mundos.json"
DOC = RAIZ / "documentation" / "KERN_PALANCAS_SOBRE_25_MUNDOS.md"

CANONICAS = (1000, 1001, 1002, 1003, 1004)


def _plano(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("−", "-").replace("**", "").replace("*", ""))


@pytest.fixture(scope="module")
def datos() -> dict:
    return json.loads(COMPARACION.read_text())["palancas"]


@pytest.fixture(scope="module")
def doc() -> str:
    return _plano(DOC.read_text())


def test_el_control_reproduce_las_lineas_base_congeladas(datos):
    """Sin esto no hay comparacion. Las cinco semillas canonicas estan DENTRO de las 25,
    asi que su carril de control tiene que salir identico al congelado -- si se separa,
    lo que cambio no fue el tamano de la muestra."""
    for nombre, v in datos.items():
        assert sorted(v["control_reproduce_el_congelado"]) == list(CANONICAS), (
            f"{nombre}: el control no reprodujo las cinco canonicas "
            f"({v['control_reproduce_el_congelado']})"
        )
        # Y el carril, no solo el control: sobre esas cinco tiene que dar el numero
        # publicado, o el "se encoge Nx" estaria restando contra otra corrida.
        assert v["en_5_mundos_de_esta_corrida"] == pytest.approx(
            v["publicado_en_5_mundos"], abs=1e-9
        ), f"{nombre}: sobre las cinco canonicas ya no da lo publicado"


def test_la_palanca_de_comprar_menos_ESCALA(datos, doc):
    """El titular. La fecha de fin por SKU sobrevive el quintuplicar la muestra, y es la
    unica afirmacion de esta rama que queda con p < 0,001. Si cayera, el documento
    estaria reordenando las prioridades del producto sobre nada."""
    h = datos["horizonte_estimado"]
    assert h["en_25_mundos"] > 0.10, f"cayo a {h['en_25_mundos']:+.4f}"
    assert h["mundos_que_mejoran"] >= 22, f"mejora en {h['mundos_que_mejoran']}/25"
    assert h["p"] < 0.001, f"p subio a {h['p']:.5f}"
    assert h["ic95_bajo"] > 0.0, "el intervalo dejo de excluir al cero"
    # Y se encoge POCO -- que es lo que la separa de las de 'compra mas'.
    assert h["encogimiento"] < 1.5, f"se encogio {h['encogimiento']:.2f}x"
    assert "la más grande" in doc


def test_la_familia_de_comprar_mas_NO_escala_y_cuatro_cambian_de_signo(datos, doc):
    """El otro medio hallazgo. `servicio_econ` conserva el signo pero pierde tres cuartos
    y deja de distinguirse de cero; los otros cuatro carriles se dan vuelta."""
    s = datos["servicio_econ"]
    assert s["encogimiento"] > 2.5, f"el nivel de servicio se encogio solo {s['encogimiento']:.2f}x"
    assert s["p"] > 0.05, f"volvio a distinguirse de cero (p={s['p']:.3f})"

    dieron_vuelta = [
        n for n, v in datos.items()
        if v["publicado_en_5_mundos"] > 0 and v["en_25_mundos"] < 0
    ]
    assert sorted(dieron_vuelta) == ["plazo_obs", "proveedor_medido", "sigma_obs", "todo"], (
        f"cambio el conjunto de carriles que se dan vuelta: {sorted(dieron_vuelta)}"
    )
    assert "cuatro de siete" in doc.lower() or "cuatro de los" in doc


def test_la_correlacion_separa_las_dos_familias_SIN_solapamiento(datos, doc):
    """El mecanismo, y la unica parte que generaliza fuera de estos carriles.

    Si los dos rangos se solaparan, el documento tendria un "25 > 5" sin explicacion y la
    regla que deja escrita -- que la muestra se elige por donde vive la ventaja de la
    palanca -- dejaria de tener evidencia.
    """
    menos = [v["corr_con_la_captura_del_control"]
             for v in datos.values() if v["familia"] == "compra menos"]
    mas = [v["corr_con_la_captura_del_control"]
           for v in datos.values() if v["familia"] == "compra mas"]
    assert menos and mas
    assert min(menos) > max(mas), (
        f"las familias se solapan: 'compra menos' llega a {min(menos):+.2f} y "
        f"'compra mas' a {max(mas):+.2f}"
    )
    assert "sin solapamiento" in doc


def test_el_resultado_nulo_es_una_decision_y_no_una_medicion_corta(datos, doc):
    """La potencia contra el tamano PUBLICADO. Si `servicio_econ` valiera de verdad el
    +18,9 que se le atribuia, esta medicion lo habria visto -- y por eso "no paga" es una
    conclusion y no una limitacion del barrido."""
    assert datos["servicio_econ"]["potencia_para_ver_lo_publicado"] > 0.95
    assert datos["censura_corregida"]["potencia_para_ver_lo_publicado"] > 0.90
    assert "100 %" in doc


def test_las_lineas_base_de_CINCO_mundos_siguen_siendo_de_cinco(datos):
    """La evidencia del contraste vive en que las dos muestras coexistan. Regrabar las
    congeladas con 25 semillas borraria justamente lo que este documento compara."""
    for archivo, esperado in (
        ("horizonte_por_sku.json", 5),
        ("residuo_de_la_brecha.json", 5),
        ("horizonte_por_sku_25.json", 25),
        ("residuo_de_la_brecha_25.json", 25),
    ):
        d = json.loads((BASES / archivo).read_text())
        n = len(d["por_semilla"])
        assert n == esperado, f"{archivo} pasó a tener {n} mundos, no {esperado}"
        if esperado == 5:
            assert [f["seed"] for f in d["por_semilla"]] == list(CANONICAS)


def test_los_documentos_de_cinco_mundos_declaran_que_fueron_superados():
    """Un lector que llegue a `KERN_HORIZONTE_POR_SKU.md` por un link tiene que enterarse
    ahi mismo de que su orden de palancas cambio, sin depender de encontrar el documento
    nuevo por su cuenta."""
    for nombre in ("KERN_HORIZONTE_POR_SKU.md", "KERN_RESIDUO_DE_LA_BRECHA.md"):
        texto = (RAIZ / "documentation" / nombre).read_text()
        assert "KERN_PALANCAS_SOBRE_25_MUNDOS.md" in texto, (
            f"{nombre} no apunta a la re-medicion sobre 25 mundos"
        )


def test_la_prediccion_se_escribio_antes_y_el_documento_dice_cual_fallo(doc):
    """Las tres predicciones son parte del resultado: publicarlas solo cuando aciertan es
    lo que vuelve inservible a una prediccion. El documento tambien tiene que decir lo que
    NO anticipo."""
    assert "La predicción, escrita antes de correr" in DOC.read_text()
    for p in ("P1", "P2", "P3"):
        assert p in doc
    assert "no anticipó" in doc or "no anticipo" in doc
