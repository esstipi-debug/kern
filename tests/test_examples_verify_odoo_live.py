"""El arnes de verificacion contra Odoo real, verificado el mismo. Offline.

Un verificador sin verificar no sirve: si sus expectativas estuvieran mal, diria
"FALLA" contra un servidor que se comporta bien, o peor, "ACIERTA" contra uno que
no. Aca se lo corre contra `InMemoryOdooServer` -- que por construccion se
comporta como creemos que se comporta Odoo -- y se exige que los cuatro modos
salgan ACIERTA. Cuando alguien lo corra contra un Odoo de verdad, cualquier FALLA
sera del servidor real discrepando de nuestro doble, que es exactamente lo que el
arnes existe para descubrir.

Y dos propiedades que no son sobre el resultado sino sobre como se llega a el:
la clave no aparece en la salida, y el sondeo de host inalcanzable no sale de la
maquina.
"""
from __future__ import annotations

import sys

import pytest

from examples.verify_odoo_live import CLAVE_INVALIDA, DB_INVALIDA, ENV_CLAVE, HOST_MUERTO, Caso, main
from src.connectors import odoo_connect
from src.connectors.odoo import demo_odoo_receipts

CLAVE = "clave-real-del-cliente-0f1e2d"


@pytest.fixture()
def odoo_falso(monkeypatch):
    """`server_proxies` que responde segun la url, para poder montar el sondeo
    de host inalcanzable sin red."""
    servidor = odoo_connect.InMemoryOdooServer(
        demo_odoo_receipts(), db="prod", login="ops@cliente.cl", api_key=CLAVE,
    )
    muerto = odoo_connect.InMemoryOdooServer(
        demo_odoo_receipts(), unreachable=OSError("connection refused"),
    )

    def _proxies(url, timeout=30.0):
        elegido = muerto if "127.0.0.1" in url else servidor
        return (elegido, elegido)

    monkeypatch.setattr(odoo_connect, "server_proxies", _proxies)
    monkeypatch.setenv(ENV_CLAVE, CLAVE)
    return servidor


def _correr(capsys, monkeypatch) -> str:
    monkeypatch.setattr(sys, "argv", [
        "verify_odoo_live.py", "--url", "https://miempresa.odoo.com",
        "--db", "prod", "--login", "ops@cliente.cl",
    ])
    main()
    return capsys.readouterr().out


def test_los_cuatro_modos_ejercitables_aciertan_contra_el_doble(odoo_falso, capsys, monkeypatch):
    salida = _correr(capsys, monkeypatch)

    for caso in ("conexion valida", "host inalcanzable", "base inexistente", "credencial invalida"):
        linea = next(linea for linea in salida.splitlines() if linea.strip().startswith(caso))
        assert "ACIERTA" in linea, linea
    assert "aciertan    : 4" in salida


def test_lo_que_no_se_puede_montar_sale_SIN_MEDIR_y_no_aprobado(odoo_falso, capsys, monkeypatch):
    """La mitad que se suele omitir. Un modo no ejercitado que se cuenta como
    aprobado convierte el arnes en el mismo defecto que viene a cazar."""
    salida = _correr(capsys, monkeypatch)

    assert "NO EJERCITADO" in salida
    assert "sin medir   : 2" in salida
    assert "permisos insuficientes" in salida and "modulo ausente" in salida
    # Y dice COMO montarlos, o el reporte no es accionable.
    assert "segundo usuario SIN acceso a Compras" in salida


def test_la_clave_real_no_aparece_en_la_salida(odoo_falso, capsys, monkeypatch):
    salida = _correr(capsys, monkeypatch)
    assert CLAVE not in salida


def test_el_sondeo_de_host_muerto_no_sale_de_la_maquina():
    """Un dominio inventado puede ser de alguien. El puerto discard no es de nadie."""
    assert HOST_MUERTO.startswith("http://127.0.0.1")


def test_la_clave_invalida_del_sondeo_se_ve_invalida():
    """Si pareciera una clave real, alguien la copiaria a un .env pensando que sirve."""
    assert "invalida" in CLAVE_INVALIDA and "verificacion" in CLAVE_INVALIDA
    assert "no-existe" in DB_INVALIDA or "no_existe" in DB_INVALIDA


def test_un_modo_que_no_acierta_se_reporta_como_FALLA_y_corta(odoo_falso, capsys, monkeypatch):
    """EL TEST QUE HACE QUE EL ARNES SIRVA.

    Si siempre dijera ACIERTA, no mediria nada. Se le cambia al doble el
    comportamiento de una base inexistente -- que devuelva un fault que no nombra
    la base -- y el arnes tiene que decirlo y salir distinto de cero.
    """
    import xmlrpc.client

    original = odoo_falso.authenticate

    def _sin_nombrar_la_base(db, login, key, ctx):
        if db == DB_INVALIDA:
            raise xmlrpc.client.Fault(1, "odoo.exceptions.UserError: algo indescifrable")
        return original(db, login, key, ctx)

    monkeypatch.setattr(odoo_falso, "authenticate", _sin_nombrar_la_base)

    with pytest.raises(SystemExit) as exc:
        _correr(capsys, monkeypatch)

    salida = capsys.readouterr().out
    assert exc.value.code == 1
    assert "FALLA" in salida
    assert "_DB_MARKERS NO COINCIDIO" in salida
    # Y deja la evidencia para poder arreglarlo, no solo el veredicto.
    assert "algo indescifrable" in salida


def test_si_el_camino_bueno_falla_no_se_corren_los_demas_sondeos(odoo_falso, capsys, monkeypatch):
    """Sin una conexion buena de referencia, un 'no conecta' no distingue la
    instancia del cliente de un bug nuestro."""
    monkeypatch.setenv(ENV_CLAVE, "clave-que-no-es")

    with pytest.raises(SystemExit) as exc:
        _correr(capsys, monkeypatch)

    salida = capsys.readouterr().out
    assert exc.value.code == 1
    assert "FALLO EL CAMINO BUENO" in salida
    assert "host inalcanzable" not in salida  # no se llego a sondear


def test_el_veredicto_de_un_caso_no_ejercitado_nunca_es_acierta():
    assert not Caso("x", "-", ejercitado=False).acierta
    assert Caso("x", "ok", obtenido="ok").acierta
    assert not Caso("x", "ok", obtenido="otro").acierta


def test_los_pasos_del_trial_viven_en_el_script_y_no_en_un_md(capsys, monkeypatch):
    """Una copia en otro archivo es la que queda vieja. El que corre el script es
    el que necesita los pasos, asi que viajan juntos."""
    from examples.verify_odoo_live import PASOS

    monkeypatch.setattr(sys, "argv", ["verify_odoo_live.py", "--pasos"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "Probar gratis" in capsys.readouterr().out

    # Y nombran las tres trampas que este onboarding tiene medidas, no genericos:
    assert "subdominio" in PASOS                    # la base = el subdominio
    assert "distinto de 1" in PASOS                 # el delay=1 default de Odoo
    assert "TRES ordenes" in PASOS                  # el minimo del scorecard
    assert "el mismo dia" in PASOS                  # sin separar fechas no hay brecha
