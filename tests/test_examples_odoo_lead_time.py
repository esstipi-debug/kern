"""`examples/run_odoo_lead_time.py`: el ejemplo corre, y muestra una brecha REAL.

Un ejemplo que no se ejercita se pudre igual que cualquier otro codigo, y este
ademas es lo unico que un cliente tiene hoy para usar `POST /api/odoo-lead-time`
sin escribir curl a mano.

Dos propiedades, y la segunda es la que importa:

1. Corre offline de punta a punta y produce el entregable.
2. El numero que muestra sale de RESTAR FECHAS, no del campo `delay` de la ficha.
   Un fixture donde promesa y comportamiento coinciden no mostraria nada, y el
   ejemplo pareceria funcionar igual.
"""
from __future__ import annotations

import sys

import pytest

from examples.run_odoo_lead_time import ENV_CLAVE, main
from src.connectors.odoo import demo_odoo_receipts
from src.connectors.odoo_connect import InMemoryOdooServer, OdooCredentials, scan_input
from webapp.demo_lead_time_scan import scan_from_deliveries


def _correr(capsys, *argv) -> str:
    sys.argv = ["run_odoo_lead_time.py", *argv]
    main()
    return capsys.readouterr().out


def test_el_modo_demo_corre_offline_y_entrega(capsys):
    salida = _correr(capsys, "--demo")

    assert "Odoo simulado" in salida
    assert "Acme" in salida
    assert "Titular" in salida
    assert "Hallazgos" in salida


def test_el_numero_que_muestra_sale_de_las_fechas_y_no_de_la_ficha(capsys):
    """LO QUE HACE QUE EL EJEMPLO SIRVA PARA ALGO.

    La ficha promete 7. Si el ejemplo mostrara 7 como observado, estaria
    comparando la promesa consigo misma -- brecha cero sobre el defecto exacto
    que este escaneo viene a mostrar -- y aun asi imprimiria una tabla completa.
    """
    salida = _correr(capsys, "--demo")

    assert "la ficha dice 7 dias, las recepciones dicen 20." in salida
    assert "1 SKU(s)" in salida


def test_el_fixture_offline_tiene_una_brecha_de_verdad():
    """El fixture es parte del contrato: sin brecha, el demo no demuestra nada.

    Se mide por el mismo camino que usa el ejemplo, no por inspeccion del dict.
    """
    servidor = InMemoryOdooServer(demo_odoo_receipts())
    creds = OdooCredentials(url="https://demo.odoo.com", db="prod",
                            login="ops@cliente.cl", api_key="clave-de-api")
    entrada = scan_input(creds, common=servidor, models=servidor)
    scan = scan_from_deliveries(entrada.grouped, entrada.dropped, entrada.source_notes)

    peor = scan.worst
    assert peor.declared_lead_time == 7.0        # lo que promete la ficha
    assert peor.avg_lead_time > 19               # lo que dicen sus recepciones
    assert peor.lead_time_gap > 12               # la brecha, que es el punto
    assert peor.skus_to_recalculate              # y a que SKU le pega


def test_sin_los_campos_obligatorios_dice_cuales_faltan(capsys):
    with pytest.raises(SystemExit) as exc:
        _correr(capsys, "--url", "https://x.odoo.com")
    assert "--db" in str(exc.value) and "--login" in str(exc.value)


def test_la_clave_no_se_acepta_por_la_linea_de_comandos(capsys):
    """GUARDIA CONTRA UNA "MEJORA" RAZONABLE.

    Alguien va a querer agregar `--api-key` para poder scriptearlo. Queda en el
    historial del shell y, en una maquina compartida, a la vista de cualquiera
    que corra `ps`. La via para scriptear ya existe y es la variable de entorno.
    """
    with pytest.raises(SystemExit):
        _correr(capsys, "--demo", "--api-key", "secreta")
    error = capsys.readouterr().err
    assert "unrecognized arguments" in error or "--api-key" in error


def test_la_variable_de_entorno_es_la_via_para_scriptear(monkeypatch):
    """La contracara: sin ella, un CI no podria correr esto nunca."""
    from examples import run_odoo_lead_time as ejemplo

    monkeypatch.setenv(ENV_CLAVE, "clave-desde-el-entorno")
    assert ejemplo._obtener_clave() == "clave-desde-el-entorno"


def test_sin_terminal_ni_variable_falla_diciendo_como_pasarla(monkeypatch):
    """Un prompt de `getpass` contra un stdin cerrado colgaria o reventaria feo."""
    from examples import run_odoo_lead_time as ejemplo

    monkeypatch.delenv(ENV_CLAVE, raising=False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        ejemplo._obtener_clave()
    assert ENV_CLAVE in str(exc.value)
