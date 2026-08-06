"""El generador de carteras y el agregador del barrido.

Los dos son andamiaje de verificacion, y el andamiaje que miente es peor que
no tenerlo: una cartera que no contiene los casos degenerados hace pasar tramos
que no probaron nada, y un agregador que descarta las corridas no medibles
convierte "la mitad de los mundos no discrimina" en una media limpia y falsa.
"""

from __future__ import annotations

import pytest

from src.verify.barrido import Distribucion, agregar, degrada_con_la_suciedad
from src.verify.carteras import (
    TRAMOS,
    TRAMOS_POR_NOMBRE,
    composicion,
    generar_cartera,
)

# --------------------------------------------------------------------------
# Carteras
# --------------------------------------------------------------------------


def test_la_cartera_tiene_el_numero_de_sku_pedido():
    df = generar_cartera(37, semanas=10, semilla=3)
    assert df["product_id"].nunique() == 37
    assert len(df) == 37 * 10


def test_la_misma_semilla_da_la_misma_cartera():
    a = generar_cartera(20, semanas=12, semilla=5)
    b = generar_cartera(20, semanas=12, semilla=5)
    assert a.equals(b)


def test_semillas_distintas_dan_carteras_distintas():
    a = generar_cartera(20, semanas=12, semilla=5)
    b = generar_cartera(20, semanas=12, semilla=6)
    assert not a["quantity"].equals(b["quantity"])


def test_la_serie_de_un_sku_no_depende_del_tamano_de_la_cartera():
    """Sin esto, comparar el tramo de 500 con el de 5.000 no diria nada: habrian
    cambiado a la vez el tamano y todos los datos."""
    chica = generar_cartera(30, semanas=12, semilla=9)
    grande = generar_cartera(300, semanas=12, semilla=9)
    pid = "SKU-000015"
    a = chica[chica["product_id"] == pid]["quantity"].tolist()
    b = grande[grande["product_id"] == pid]["quantity"].tolist()
    assert a == b


@pytest.mark.parametrize("n", [4, 10, 50, 500, 5_000])
def test_toda_cartera_contiene_los_dos_degenerados(n):
    """La rama de aislamiento del job solo se ejerce si el dato la provoca."""
    comp = composicion(n)
    assert comp.get("sin_demanda", 0) >= 1, comp
    assert comp.get("sin_costo", 0) >= 1, comp


def test_el_sku_sin_costo_sale_con_costo_cero():
    df = generar_cartera(50, semanas=8, semilla=2)
    sin_costo = df[df["product_id"] == "SKU-000001"]
    assert (sin_costo["unit_cost"] == 0.0).all()


def test_el_sku_sin_demanda_sale_con_cantidad_cero():
    df = generar_cartera(50, semanas=8, semilla=2)
    sin_demanda = df[df["product_id"] == "SKU-000000"]
    assert (sin_demanda["quantity"] == 0).all()


def test_la_composicion_cuadra_con_la_cartera_generada():
    n = 200
    df = generar_cartera(n, semanas=6, semilla=4)
    comp = composicion(n)
    assert sum(comp.values()) == n
    assert df["product_id"].nunique() == n


@pytest.mark.parametrize("malo", [0, -1])
def test_un_tamano_invalido_falla_fuerte(malo):
    with pytest.raises(ValueError):
        generar_cartera(malo)


def test_los_tramos_estan_ordenados_y_son_unicos():
    nombres = [t.nombre for t in TRAMOS]
    assert len(set(nombres)) == len(nombres)
    tamanos = [t.n_skus for t in TRAMOS]
    assert tamanos == sorted(tamanos)
    assert set(TRAMOS_POR_NOMBRE) == set(nombres)


def test_cada_tramo_declara_un_presupuesto_de_tiempo():
    """Un tramo sin techo de tiempo no puede detectar una regresion de rendimiento."""
    for t in TRAMOS:
        assert t.presupuesto_s > 0, t.nombre


# --------------------------------------------------------------------------
# Barrido
# --------------------------------------------------------------------------


def _reporte(capturas: dict[tuple[str, int], float | None]) -> dict:
    return {"kern": {
        f"{lane}-L{lvl}": {"lane": lane, "level": lvl, "capture": cap}
        for (lane, lvl), cap in capturas.items()
    }}


def test_agrega_una_distribucion_por_carril_y_nivel():
    reps = [_reporte({("base", 0): 0.5, ("base", 1): 0.4}) for _ in range(3)]
    dists = agregar(reps)
    assert {(d.carril, d.nivel) for d in dists} == {("base", 0), ("base", 1)}
    assert all(d.n_mundos == 3 for d in dists)


def test_la_media_y_el_peor_caso_son_los_esperados():
    reps = [_reporte({("base", 0): c}) for c in (0.2, 0.5, 0.8)]
    d = agregar(reps)[0]
    assert d.media == pytest.approx(0.5)
    assert d.peor == pytest.approx(0.2)
    assert d.mejor == pytest.approx(0.8)
    assert d.mediana == pytest.approx(0.5)


def test_las_corridas_no_medibles_se_cuentan_no_se_descartan():
    """El fallo que este modulo existe para evitar: promediar sobre los que si
    midieron y callar cuantos no."""
    reps = [_reporte({("base", 0): c}) for c in (0.9, None, None, 0.7)]
    d = agregar(reps)[0]
    assert d.n_mundos == 4
    assert d.n_medibles == 2
    assert d.cobertura == pytest.approx(0.5)
    assert d.media == pytest.approx(0.8)
    assert "2/4 mundos medibles" in d.frase()


def test_un_grupo_sin_ninguna_medible_no_inventa_una_media():
    reps = [_reporte({("base", 0): None}) for _ in range(3)]
    d = agregar(reps)[0]
    assert d.media is None and d.peor is None
    assert d.n_medibles == 0
    assert "ninguno de los 3 mundos fue medible" in d.frase()


def test_un_solo_mundo_no_reporta_desviacion():
    """Una desviacion de una muestra seria cero, y un cero ahi se lee como
    'muy estable' en vez de 'no hay muestra'."""
    d = agregar([_reporte({("base", 0): 0.6})])[0]
    assert d.n_medibles == 1
    assert d.desviacion is None
    assert "+/-" not in d.frase()


def test_la_frase_lleva_el_peor_mundo_que_es_lo_que_se_compra():
    reps = [_reporte({("base", 0): c}) for c in (0.31, 0.58, 0.72)]
    frase = agregar(reps)[0].frase()
    assert "peor mundo 31.0%" in frase


def test_detecta_que_ensuciar_los_datos_mejoro_el_resultado():
    """Sintoma clasico de fuga de informacion entre el generador de suciedad y
    el marcador: mas ruido no puede hacer a Kern mejor."""
    dists = [
        Distribucion("base", 0, 5, 5, 0.50, 0.02, 0.48, 0.50, 0.52, 0.48, 0.52),
        Distribucion("base", 1, 5, 5, 0.70, 0.02, 0.68, 0.70, 0.72, 0.68, 0.72),
    ]
    anomalias = degrada_con_la_suciedad(dists, "base")
    assert len(anomalias) == 1
    assert "ensuciar mejoro el resultado" in anomalias[0]


def test_una_mejora_grande_con_muestra_ruidosa_no_dispara_la_alarma():
    """El falso positivo que tuvo la primera version: con 3 mundos y desviacion
    del 11%, el error estandar de la media ronda los 6 puntos, asi que una
    diferencia de 9 no distingue senal de azar. Una alarma que salta con muestra
    pequena entrena a quien la lee a ignorarla."""
    dists = [
        Distribucion("base", 0, 3, 3, -0.105, 0.108, -0.18, -0.10, -0.02, -0.189, -0.02),
        Distribucion("base", 1, 3, 3, -0.015, 0.159, -0.125, -0.01, 0.10, -0.138, 0.10),
    ]
    assert degrada_con_la_suciedad(dists, "base") == []


def test_la_misma_diferencia_con_muestra_grande_si_dispara():
    """Barrer mas mundos vuelve la alarma mas sensible, que es el incentivo
    correcto: para afirmar algo, muestrea mas."""
    dists = [
        Distribucion("base", 0, 300, 300, -0.105, 0.108, -0.18, -0.10, -0.02, -0.189, -0.02),
        Distribucion("base", 1, 300, 300, -0.015, 0.159, -0.125, -0.01, 0.10, -0.138, 0.10),
    ]
    assert len(degrada_con_la_suciedad(dists, "base")) == 1


def test_sin_muestra_para_estimar_el_ruido_no_se_inventa_una_alarma():
    dists = [
        Distribucion("base", 0, 1, 1, 0.20, None, 0.20, 0.20, 0.20, 0.20, 0.20),
        Distribucion("base", 1, 1, 1, 0.90, None, 0.90, 0.90, 0.90, 0.90, 0.90),
    ]
    assert degrada_con_la_suciedad(dists, "base") == []


def test_una_degradacion_normal_no_se_marca_como_anomalia():
    dists = [
        Distribucion("base", 0, 5, 5, 0.70, 0.02, 0.68, 0.70, 0.72, 0.68, 0.72),
        Distribucion("base", 1, 5, 5, 0.69, 0.02, 0.67, 0.69, 0.71, 0.67, 0.71),
        Distribucion("base", 2, 5, 5, 0.55, 0.03, 0.52, 0.55, 0.58, 0.52, 0.58),
    ]
    assert degrada_con_la_suciedad(dists, "base") == []


def test_una_mejora_dentro_del_ruido_no_se_marca():
    """Kern limpia parte de la suciedad a proposito: L1 casi igual a L0 es exito."""
    dists = [
        Distribucion("base", 0, 5, 5, 0.700, 0.01, 0.69, 0.70, 0.71, 0.69, 0.71),
        Distribucion("base", 1, 5, 5, 0.705, 0.01, 0.70, 0.705, 0.71, 0.70, 0.71),
    ]
    assert degrada_con_la_suciedad(dists, "base") == []
