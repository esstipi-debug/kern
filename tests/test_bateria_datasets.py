"""El armado de los datasets que la bateria pide bajo un data_root."""

import pandas as pd
import pytest

from scripts.build_bateria_datasets import sales_history


def _escribir(tmp_path, filas):
    ruta = tmp_path / "m5_CA_1_TEST.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)
    return str(tmp_path / "m5_CA_1_*.csv")


def test_las_ventas_se_agregan_por_mes_no_por_linea(tmp_path):
    """El eje XYZ mide la variabilidad de la demanda MENSUAL.

    Alimentarlo con lineas diarias mide la dispersion del tamano de pedido, que es
    otra cosa: dos dias del mismo mes tienen que colapsar en una fila.
    """
    glob = _escribir(tmp_path, [
        {"date": "2011-01-05", "product_id": "A", "quantity": 10, "unit_cost": 2.0},
        {"date": "2011-01-20", "product_id": "A", "quantity": 4, "unit_cost": 3.0},
        {"date": "2011-02-03", "product_id": "A", "quantity": 7, "unit_cost": 2.0},
    ])
    ventas = sales_history(glob)
    assert len(ventas) == 2
    enero = ventas.iloc[0]
    assert enero["quantity"] == 14
    assert enero["unit_cost"] == pytest.approx(2.5)


def test_sin_extractos_falla_por_nombre_en_vez_de_devolver_vacio(tmp_path):
    """Un dataset vacio daria un ABC de cero SKU y un CUMPLE sobre nada."""
    with pytest.raises(FileNotFoundError, match="M5"):
        sales_history(str(tmp_path / "no_existe_*.csv"))
