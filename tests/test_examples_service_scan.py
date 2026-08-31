"""El CLI de service_scan sigue corriendo. Guard de bitrot, no de diseno.

El comportamiento que importa vive en ``tests/test_connector_service_scan.py`` y se
escribio antes que el codigo. Este archivo cubre lo otro: que el script siga arrancando
cuando alguien renombre un campo de ``ServiceScan`` o de ``JobReport``. Un ejemplo roto es
peor que ninguno -- es lo primero que corre alguien que llega al repo, y falla en la cara.

Se prueba tambien el camino SIN credenciales, que no es un detalle: un script que revienta
con un ``KeyError`` sobre una variable de entorno ausente hace pensar que el problema es el
codigo, y el problema es que faltan dos variables.
"""

from __future__ import annotations

import pytest

from examples import run_service_scan


def test_el_demo_corre_entero_e_imprime_servicio_observado(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_service_scan.py", "--demo"])
    assert run_service_scan.main() == 0
    salida = capsys.readouterr().out
    assert "SERVICIO MEDIDO POR SKU" in salida
    assert "con servicio OBSERVADO en el entregable" in salida
    # La cartera se reporta como medida, no como no medida: es la prueba de que el frame
    # de order_lines llego hasta el job y no se perdio en el camino.
    assert "provenance observed" in salida


def test_sin_credenciales_explica_que_falta_en_vez_de_reventar(monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_service_scan.py"])
    monkeypatch.delenv("UNLEASHED_API_ID", raising=False)
    monkeypatch.delenv("UNLEASHED_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="UNLEASHED_API_ID"):
        run_service_scan.main()


def test_una_salida_sin_medir_no_se_imprime_como_cero_por_ciento():
    """``_pct(None)`` es 'no medido'. Formatearlo como 0.0% es como una ausencia se
    convierte en un quiebre en la pantalla que ve el cliente, despues de que todo el
    motor se cuido de no hacerlo."""
    assert run_service_scan._pct(None) == "no medido"
    assert run_service_scan._pct(0.0) == "0.0%"
    assert run_service_scan._plata(None) == "no medida"
    assert run_service_scan._plata(0.0) == "$0"
