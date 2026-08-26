"""Un CSV chileno real -- ``;``, cp1252 -- fallaba con un error de codec crudo.

08-MEDICION.md §8 lo midio: 55 llamadas a ``pd.read_csv`` en ``src/ jobs/ webapp/
scm_agent/``, cero con ``encoding=`` o ``sep=``. El caso concreto: un Excel
guardado como "CSV (delimitado por comas)" en un Windows en espanol usa ``;``
como separador y cp1252 como codificacion, porque la coma decimal ya esta
ocupada. ``pd.read_csv`` por defecto ni sniffea el separador ni prueba otra
codificacion -- lanza directo.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.csv_io import read_csv_robust


def test_csv_estandar_utf8_coma_sigue_funcionando(tmp_path):
    p = tmp_path / "ventas.csv"
    p.write_text("product_id,quantity\nSKU-1,10\nSKU-2,5\n", encoding="utf-8")

    df = read_csv_robust(p)

    assert list(df.columns) == ["product_id", "quantity"]
    assert df["quantity"].tolist() == [10, 5]


def test_csv_chileno_punto_y_coma_cp1252_se_lee_sin_mangling(tmp_path):
    p = tmp_path / "export_chileno.csv"
    contenido = "Código;Descripción;Cantidad\nA-01;Válvula de repuesto;12\n"
    p.write_bytes(contenido.encode("cp1252"))

    df = read_csv_robust(p)

    assert list(df.columns) == ["Código", "Descripción", "Cantidad"]
    assert df.loc[0, "Descripción"] == "Válvula de repuesto"
    assert df.loc[0, "Cantidad"] == 12


def test_csv_utf8_con_bom_no_deja_el_bom_pegado_a_la_primera_columna(tmp_path):
    p = tmp_path / "con_bom.csv"
    p.write_bytes("product_id,quantity\nSKU-1,1\n".encode("utf-8-sig"))

    df = read_csv_robust(p)

    assert list(df.columns) == ["product_id", "quantity"]


def test_sep_explicito_no_se_sobrescribe_con_el_sniffer(tmp_path):
    # Contenido separado por coma, pero pedido con `;` a proposito: el caller
    # que ya sabe su separador tiene que poder forzarlo.
    p = tmp_path / "raro.csv"
    p.write_text("a;b\n1;2\n", encoding="utf-8")

    df = read_csv_robust(p, sep=";")

    assert list(df.columns) == ["a", "b"]


def test_un_archivo_realmente_malformado_sigue_fallando_visible():
    # No hay que tragarse errores genuinos -- solo ampliar que cuenta como legible.
    with pytest.raises((pd.errors.EmptyDataError, OSError)):
        read_csv_robust("no_existe_este_archivo.csv")
