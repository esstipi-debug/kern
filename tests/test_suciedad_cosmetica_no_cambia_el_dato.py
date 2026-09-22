"""Un nivel de suciedad COSMETICO tiene que leerse igual que el archivo limpio.

`src/simworld/lens.py` declara sus tres inyectores de nivel 1 como "cosmetico puro":
la misma SKU mal escrita, el mismo dia en otro formato, el mismo numero en formato
europeo. Si eso es cierto, `jobs/intake.py` tiene que devolver el MISMO dato.

No lo era, y el precio estaba medido: sobre 25 mundos la captura del nivel 1 salia
-117,0% contra -13,8% del nivel 0, y mejoro exactamente +0,0 puntos en los 25 cuando
llego la cobertura provisional -- porque la cobertura solo se dispara cuando el motor
se NIEGA, y a nivel 1 el motor no se negaba: emitia una politica confiada sobre una
serie que se habia inventado.

Las dos causas, cada una con su test abajo:

1. `" sku-1003"` y `"SKU-1003"` eran DOS productos. `.str.strip()` limpiaba el espacio
   y no la caja, asi que 8 SKU se volvian 16 y cada serie perdia la mitad de su
   historia.
2. `"06/01/2025"` (6 de enero escrito dd/mm/yyyy) se parseaba month-first -> 1 de
   junio. Una sola fecha estiraba la grilla semanal de 4 periodos a 21, rellenos de
   ceros que nadie midio, y con eso cruzaba `MIN_PERIODS_FOR_POLICY`.

El test de cierre es el invariante entero, no las piezas: LEVEL 1 == LEVEL 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jobs import intake
from src.simworld import lens


# ---------------------------------------------------------------------------
# 1. La identidad de un producto no depende de como se escriba
# ---------------------------------------------------------------------------

def _csv(tmp_path, filas):
    p = tmp_path / "hist.csv"
    pd.DataFrame(filas).to_csv(p, index=False)
    return p


def test_dos_grafias_del_mismo_codigo_son_un_solo_producto(tmp_path):
    p = _csv(tmp_path, [
        {"date": "2025-01-06", "product_id": "SKU-1", "quantity": 10, "unit_cost": 5.0},
        {"date": "2025-01-13", "product_id": " sku-1", "quantity": 20, "unit_cost": 5.0},
        {"date": "2025-01-20", "product_id": "SKU-1 ", "quantity": 30, "unit_cost": 5.0},
    ])
    out, calidad = intake.prepare_tracked(p)
    assert out["product_id"].nunique() == 1, "la caja no puede partir un producto en dos"
    assert out["quantity"].sum() == 60, "no se puede perder historia al plegar"
    assert calidad.folded_ids, "plegar en silencio es el defecto al reves"


def test_la_etiqueta_es_la_grafia_MAS_FRECUENTE():
    """El entregable le muestra al cliente el codigo como su archivo lo escribe casi
    siempre, no como lo escribio la primera fila."""
    ids = pd.Series(["sku-1", "SKU-1", "SKU-1", "SKU-1"])
    etiquetas, plegados = intake._fold_ids(ids)
    assert set(etiquetas) == {"SKU-1"}
    assert plegados == (("SKU-1", ("sku-1",)),)


def test_el_empate_se_rompe_alfabeticamente_y_no_por_orden_de_fila():
    """Dos corridas del mismo archivo tienen que coincidir en como se llama el producto.
    Con el orden de aparicion, reordenar el CSV cambiaria la etiqueta."""
    a, _ = intake._fold_ids(pd.Series(["b-1", "B-1"]))
    b, _ = intake._fold_ids(pd.Series(["B-1", "b-1"]))
    assert set(a) == set(b) == {"B-1"}


def test_dos_codigos_de_verdad_distintos_NO_se_pliegan(tmp_path):
    p = _csv(tmp_path, [
        {"date": "2025-01-06", "product_id": "SKU-1", "quantity": 10, "unit_cost": 5.0},
        {"date": "2025-01-13", "product_id": "SKU-2", "quantity": 20, "unit_cost": 5.0},
    ])
    out, calidad = intake.prepare_tracked(p)
    assert out["product_id"].nunique() == 2
    assert calidad.folded_ids == (), "no se pliego nada, no hay nada que declarar"


# ---------------------------------------------------------------------------
# 2. El orden de una fecha con barra se LEE de la columna
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("valores, esperado", [
    # Una sola fecha inequivoca decide por todas las ambiguas de la columna.
    (["2025-01-06", "06/01/2025", "27/01/2025"], ["2025-01-06", "2025-01-06", "2025-01-27"]),
    (["2025-01-06", "01/06/2025", "01/27/2025"], ["2025-01-06", "2025-01-06", "2025-01-27"]),
    # ISO puro: no se toca.
    (["2025-01-06", "2025-01-13"], ["2025-01-06", "2025-01-13"]),
])
def test_el_orden_sale_de_la_propia_columna(valores, esperado):
    out = intake._parse_dates(pd.Series(valores))
    assert [str(x.date()) for x in out] == esperado


def test_sin_evidencia_o_con_evidencia_contradictoria_se_cae_al_comportamiento_viejo():
    """Adivinar seria peor que el default declarado: una columna con '13/01' Y '01/13'
    no tiene una sola convencion, y elegir una corrompe la mitad."""
    assert intake._slash_date_order(pd.Series(["06/01/2025", "07/01/2025"])) is None
    assert intake._slash_date_order(pd.Series(["13/01/2025", "01/13/2025"])) is None
    assert intake._slash_date_order(pd.Series(["27/01/2025"])) is True
    assert intake._slash_date_order(pd.Series(["01/27/2025"])) is False


def test_una_fecha_ambigua_ya_no_estira_la_grilla(tmp_path):
    """El defecto medido, punta a punta: cuatro semanas de enero tienen que seguir
    siendo cuatro periodos. Antes, la del 6 caia en junio y la grilla daba 21."""
    filas = [{"date": d, "product_id": "SKU-1", "quantity": 10, "unit_cost": 5.0}
             for d in ("06/01/2025", "13/01/2025", "20/01/2025", "27/01/2025")]
    out, _ = intake.prepare_tracked(_csv(tmp_path, filas))
    fechas = pd.to_datetime(out["date"])
    assert len(out) == 4
    assert (fechas.max() - fechas.min()).days == 21, "cuatro lunes seguidos, no cinco meses"


# ---------------------------------------------------------------------------
# 3. El invariante entero, que es la razon por la que existen los dos de arriba
# ---------------------------------------------------------------------------

def _leido(df, tmp_path, nombre):
    p = tmp_path / nombre
    df.to_csv(p, index=False)
    out, _ = intake.prepare_tracked(p)
    return out.sort_values(["product_id", "date"]).reset_index(drop=True)


def test_el_nivel_cosmetico_se_lee_IGUAL_que_el_limpio(tmp_path):
    """El invariante de cierre. Si alguna vez se pone rojo, el nivel 1 dejo de ser
    cosmetico -- o el intake dejo de poder leerlo, que es como estaba."""
    fechas = pd.date_range("2025-01-06", periods=20, freq="W-MON")
    limpio = pd.DataFrame([
        {"date": d.strftime("%Y-%m-%d"), "product_id": f"SKU-100{i}",
         "quantity": float(10 + i + (n % 7)), "unit_cost": 19.89,
         "lead_time_days": 7.0, "lead_time_std": 0.0}
        for n, d in enumerate(fechas) for i in range(8)
    ])
    sucio = limpio.copy()
    rng = np.random.default_rng(1000)
    for inyector in lens.DIRT_BY_LEVEL[lens.COSMETIC_LEVEL]:
        sucio = inyector(sucio, rng)
    assert not sucio.equals(limpio), "el fixture no ensucio nada: el test no probaria nada"

    a = _leido(limpio, tmp_path, "limpio.csv")
    b = _leido(sucio, tmp_path, "sucio.csv")

    assert list(a["product_id"]) == list(b["product_id"]), "la suciedad cosmetica partio un SKU"
    assert list(a["date"].astype(str)) == list(b["date"].astype(str)), "movio una fecha"
    # `inject_thousand_separators` formatea con dos decimales, asi que redondea de
    # verdad: eso es perdida del INYECTOR, no del intake, y no se puede exigir cero.
    assert (a["quantity"] - b["quantity"]).abs().max() <= 0.01
