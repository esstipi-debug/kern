"""El documento de la fraccion con fecha de fin dice lo que dice su linea base.

`documentation/KERN_FRACCION_CON_FECHA_DE_FIN.md` publica numeros medidos sobre Olist,
que es un dataset gitignored que se baja aparte. Asi que la medicion NO se re-corre aca:
lo que se fija es que cada cifra del documento salga del archivo congelado, y que las
afirmaciones estructurales que sostienen su conclusion sigan siendo ciertas CONTRA EL
CODIGO -- no contra el texto.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
BASE = RAIZ / "documentation" / "lineas_base" / "fraccion_con_fecha_de_fin.json"
DOC = RAIZ / "documentation" / "KERN_FRACCION_CON_FECHA_DE_FIN.md"


@pytest.fixture(scope="module")
def base() -> dict:
    return json.loads(BASE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _pct(x: float) -> str:
    """0.2239 -> '22,4' — la forma en que el documento escribe un porcentaje."""
    return f"{x * 100:.1f}".replace(".", ",")


def test_la_linea_base_existe_y_declara_su_fuente(base):
    assert "Olist" in base["fuente"]
    assert base["skus"] == 32729
    assert base["vendedores"] == 3053
    assert base["lineas"] == 112101


def test_el_control_contra_el_lector_real_es_perfecto(base):
    """Sin esto el estudio mediria una grilla que no es la que el motor ve."""
    c = base["control_lector"]
    assert c["muestreados"] == 250
    assert c["coinciden"] == c["muestreados"]


def test_cada_fraccion_publicada_sale_del_archivo(base, doc):
    for f in base["fracciones"]:
        assert _pct(f["frac_skus"]) in doc, f"falta {f['cartera']} % SKU"
        assert _pct(f["frac_facturacion"]) in doc, f"falta {f['cartera']} % facturacion"


def test_pesar_por_plata_siempre_da_MENOS_que_contar_skus(base):
    """El hallazgo economico: los SKU que mueren son los chicos, en TODAS las carteras."""
    for f in base["fracciones"]:
        assert f["frac_facturacion"] < f["frac_skus"], f["cartera"]


def test_el_nucleo_muere_menos_que_la_cola(base):
    """Cuanto mas se parece a un rango repuesto, menos fraccion muere."""
    por_nombre = {f["cartera"]: f for f in base["fracciones"]}
    nucleo = por_nombre["nucleo (>=12 semanas con venta)"]["frac_skus"]
    cola = por_nombre["con >=3 semanas con venta"]["frac_skus"]
    recurrente = por_nombre["rango recurrente (>=6 semanas con venta)"]["frac_skus"]
    assert nucleo < recurrente < cola


def test_el_falso_positivo_hace_piso_y_el_documento_lo_publica(base, doc):
    """El resultado que justifica la regla de seguridad: no se puede deducir la fecha."""
    fp = [c["falso_positivo"] for c in base["calibracion"]]
    assert min(fp) > 0.10, "si el FP bajara de 10% la conclusion del documento cambia"
    mejor = min(base["calibracion"], key=lambda c: c["falso_positivo"])
    assert mejor["k"] == base["criterio"]["k"]
    assert mejor["piso"] == base["criterio"]["piso"]
    assert _pct(mejor["falso_positivo"]) in doc


def test_apretar_el_criterio_cuesta_hallazgos(base):
    """No hay ajuste preciso y completo a la vez; el documento lo afirma."""
    por = {(c["k"], c["piso"]): c for c in base["calibracion"]}
    flojo, duro = por[(2, 8)], por[(4, 26)]
    assert duro["falso_positivo"] < flojo["falso_positivo"] / 1.5
    assert duro["llamados_muertos"] < flojo["llamados_muertos"] / 2.5


def test_todos_los_ya_muertos_salen_expired(base):
    """La fraccion OBSERVABLE es justo donde la palanca no hace nada."""
    v = base["veredicto_de_los_ya_muertos"]
    assert set(v) == {"expired"}, f"si aparece 'applied' el documento esta mal: {v}"
    por_nombre = {f["cartera"]: f for f in base["fracciones"]}
    assert v["expired"] == por_nombre["con >=3 semanas con venta"]["muertos"]


def test_el_relleno_de_ceros_ya_achica_mas_a_un_muerto_que_a_un_vivo(base):
    d = base["ya_descontado_por_el_relleno"]
    assert d["factor_mediano_muertos"] < d["factor_mediano_vivos"]
    razon = d["factor_mediano_vivos"] / d["factor_mediano_muertos"]
    assert 2.0 < razon < 3.5, f"el documento publica 2,6 veces; salio {razon:.2f}"


def test_la_dispersion_entre_carteras_es_de_un_orden_de_magnitud(base):
    """Prometer "la fraccion" es prometer la mediana de algo que va de 0% a ~60%."""
    for v in base["por_vendedor"]:
        assert v["p10"] < v["mediana"] < v["p90"]
    diez = next(v for v in base["por_vendedor"] if v["min_skus"] == 10)
    assert diez["p90"] > 0.55 and diez["p10"] <= 0.01


def test_traer_la_fecha_no_es_que_la_fecha_mueva_algo(base, doc):
    """El embudo del punta a punta: la mitad trae fecha, un cuarto de esos se topea."""
    e = base["punta_a_punta"]
    assert sum(e["veredictos"].values()) == e["skus_dimensionados"]
    assert e["veredictos"]["no_date"] + e["skus_con_fecha"] == e["skus_dimensionados"]
    assert e["frac_topeados_de_los_que_traen_fecha"] < 0.35
    assert _pct(e["frac_con_fecha"]) in doc
    assert _pct(e["frac_topeados_de_los_que_traen_fecha"]) in doc


def test_el_titular_se_mueve_y_el_documento_publica_el_numero_correcto(base, doc):
    """El defecto que esta medicion encontro, ya ARREGLADO (`src.horizon.capped_cycle_stock`).

    Lo que este test cuida ahora es que no se vuelvan a confundir los DOS numeros. La
    primera version del documento publicaba 12.018 usd como "lo que el tope libera", y
    eso era la caida del TECHO de reposicion (order-up-to x costo). El titular mide el
    PROMEDIO inmovilizado (ciclo + buffer), y se mueve 5.103. El pico es 2,4 veces el
    titular porque el tope no toca el buffer, a proposito.
    """
    e = base["punta_a_punta"]
    assert e["titular_se_movio"] is True
    assert e["titular_inversion_con"] < e["titular_inversion_sin"]
    # el titular baja exactamente lo que el job dice haber liberado
    caida = e["titular_inversion_sin"] - e["titular_inversion_con"]
    assert abs(caida - e["capital_liberado_usd"]) < 0.01
    assert abs(caida - e["capital_liberado_reportado_usd"]) < 0.01
    # y el pico sigue siendo un numero distinto y mas grande, con su propio nombre
    assert e["caida_del_pico_usd"] > e["capital_liberado_usd"] * 2
    assert _pct(e["capital_liberado_frac_del_titular"]) in doc
    assert "5.103" in doc and "12.018" in doc


def test_el_arreglo_esta_en_el_codigo_y_no_solo_en_el_texto(doc):
    """El orden que causaba el defecto tiene que seguir invertido.

    El defecto era de ORDEN: `cycle_inv` / `ss_inv` se calculaban ANTES del tope, asi
    que el `InventoryItem` que alimenta el titular llevaba la politica sin topear. Lo
    que se fija aca es el orden nuevo -- el tope, despues el capital -- y que el ciclo
    pase por `capped_cycle_stock`. Un refactor que devuelva el calculo arriba del tope
    reintroduce el defecto exacto y pone esto rojo.
    """
    src = (RAIZ / "jobs" / "inventory_optimization.py").read_text(encoding="utf-8")
    lineas = src.splitlines()

    def linea_de(patron: str) -> int:
        for i, ln in enumerate(lineas, 1):
            if re.search(patron, ln):
                return i
        raise AssertionError(f"no encuentro {patron!r}")

    cyc = linea_de(r"^\s+cycle_units = pol\.expected_cycle_stock")
    topeo = linea_de(r"cycle_previo, cycle_units = cycle_units, capped_cycle_stock\(")
    cyc_inv = linea_de(r"^\s+cycle_inv = cycle_units \* meta\.mean_unit_cost")
    item = linea_de(r"items\.append\(InventoryItem\(")
    # dimensionar -> topear -> recien ahi valuar -> armar el item del presupuesto
    assert cyc < topeo < cyc_inv < item, (cyc, topeo, cyc_inv, item)


def test_el_documento_declara_la_mitad_que_no_se_puede_medir(doc):
    """Si esa declaracion se cae, el documento promete algo que no midio."""
    assert "NO medible" in doc
    assert "no se estima, se declara" in doc or "no se estima" in doc
    assert "Una sola fuente" in doc


def test_la_tasa_por_ano_es_la_de_la_ventana_reescalada(base):
    semanas = base["semanas"]
    for _nombre, t in base["tasa_de_muerte"].items():
        esperado = t["en_la_ventana"] / (semanas / 52)
        assert abs(t["por_ano"] - esperado) < 0.001
