"""`POST /api/odoo-lead-time`: la salida #1 desde un Odoo Online vivo.

Reemplaza la subida del CSV de recepciones por una lectura directa por la
External API, y desemboca en el MISMO `scan_from_deliveries`. Lo que estos tests
fijan, en orden de cuanto duele que se rompa:

1. La ruta esta AUTENTICADA. Una credencial del ERP de un cliente no se pide en
   un formulario anonimo, aunque el alcance sea de solo lectura.
2. La clave de API no sale por ningun lado de la respuesta, ni siquiera cuando
   el servidor la devuelve dentro de un error.
3. Cada modo de falla de la conexion sale con su propio `reason`, y con un
   mensaje distinto: un 400 generico no sirve en un embudo sin humano.
4. Sin recepciones con fecha se responde `needs_data` nombrando lo que falta --
   nunca cayendo al plazo declarado de la ficha del proveedor.
5. Y el numero que publica la ruta SE MUEVE con el dato del Odoo. Un titular
   congelado sobre un motor que si se mueve es el mismo defecto una capa afuera.

Todo offline: se inyecta `server_proxies`, o sea que se ejercita el validador,
el lector y el preflight REALES contra el doble de transporte, y no un mock del
resultado.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from src.connectors import odoo_connect  # noqa: E402
from tests.test_connector_odoo_connect import CLAVE, _odoo, _server  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

CAMPOS = {
    "url": "https://miempresa.odoo.com",
    "db": "prod",
    "login": "ops@cliente.cl",
    "api_key": CLAVE,
}


@pytest.fixture()
def abierto(monkeypatch):
    """Sin llave configurada (el default de fabrica) y sin rate limit."""
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    security.reset_rate_limit()


def _con(monkeypatch, srv):
    """Inyecta el doble de transporte donde `odoo_connect` construye los proxies."""
    monkeypatch.setattr(odoo_connect, "server_proxies", lambda url, timeout=30.0: (srv, srv))
    return srv


def _post(**extra):
    return client.post("/api/odoo-lead-time", data={**CAMPOS, **extra})


# --- 1. la puerta ------------------------------------------------------------


def test_sin_llave_de_api_la_ruta_no_atiende(monkeypatch):
    """La decision que separa esta ruta del embudo publico de CSV."""
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    security.reset_rate_limit()
    _con(monkeypatch, _server())

    assert _post().status_code == 401
    con_llave = client.post(
        "/api/odoo-lead-time", data=CAMPOS, headers={"X-API-Key": "s3cret"}
    )
    assert con_llave.status_code == 200


# --- 2. el secreto -----------------------------------------------------------


def test_la_clave_no_aparece_en_ninguna_respuesta(monkeypatch, abierto):
    """Ni en la buena, ni en la que el servidor devuelve con la clave adentro."""
    _con(monkeypatch, _server())
    assert CLAVE not in _post().text

    _con(monkeypatch, _server(auth_fault=f"Traceback ... {CLAVE} ..."))
    fallida = _post()
    assert CLAVE not in fallida.text
    assert odoo_connect.REDACTED in fallida.text


def test_un_campo_vacio_lo_rechaza_el_handler_y_no_el_validador_de_forma(
    monkeypatch, abierto
):
    """Los cuatro campos llegan con default "" a proposito: un 422 de pydantic
    puede arrastrar el resto del formulario -- con la clave dentro -- al cuerpo
    del error, y ese cuerpo no lo controla este codigo."""
    _con(monkeypatch, _server())
    r = _post(db="")

    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == odoo_connect.DATABASE
    assert CLAVE not in r.text


# --- 3. los modos de falla llegan al cliente separados -----------------------


@pytest.mark.parametrize(
    "montaje,campos,esperado",
    [
        (dict(unreachable=OSError("sin ruta")), {}, odoo_connect.UNREACHABLE),
        (dict(db="prod"), {"db": "otra"}, odoo_connect.DATABASE),
        ({}, {"api_key": "mala"}, odoo_connect.CREDENTIALS),
        (dict(denied=("purchase.order",)), {}, odoo_connect.PERMISSIONS),
    ],
)
def test_cada_modo_de_falla_sale_con_su_propio_motivo(
    monkeypatch, abierto, montaje, campos, esperado
):
    _con(monkeypatch, _server(**montaje))
    r = _post(**campos)

    assert r.status_code == 400
    detalle = r.json()["detail"]
    assert detalle["reason"] == esperado
    assert detalle["message"]  # nunca un 400 mudo


def test_los_cuatro_mensajes_son_distintos_entre_si(monkeypatch, abierto):
    """Cuatro `reason` distintos con el mismo texto mandarian a los cuatro
    clientes a la misma pantalla equivocada."""
    montajes = [
        (dict(unreachable=OSError("sin ruta")), {}),
        (dict(db="prod"), {"db": "otra"}),
        ({}, {"api_key": "mala"}),
        (dict(denied=("stock.move",)), {}),
    ]
    mensajes = set()
    for montaje, campos in montajes:
        _con(monkeypatch, _server(**montaje))
        mensajes.add(_post(**campos).json()["detail"]["message"])
    assert len(mensajes) == 4


# --- 4. sin datos medibles: se nombra lo que falta ---------------------------


def test_sin_recepciones_con_fecha_responde_needs_data_y_no_usa_el_delay(
    monkeypatch, abierto
):
    """El Odoo tiene el plazo declarado poblado y ni una recepcion validada.
    Devolver una brecha aqui seria comparar la promesa consigo misma."""
    vacio = _odoo(n=0)
    assert vacio.records("product.supplierinfo")[400]["delay"] == 7.0
    _con(monkeypatch, _server(vacio))

    cuerpo = _post().json()

    assert cuerpo["status"] == "needs_data"
    assert cuerpo["preflight"]["n_receipts"] == 0
    assert cuerpo["missing"] and "recepcion" in " ".join(cuerpo["missing"])
    assert "headline" not in cuerpo  # ningun numero que se pueda leer como medicion


def test_sin_plazo_declarado_el_qa_del_scan_es_el_que_rechaza(monkeypatch, abierto):
    sin_promesa = _odoo(dias=20)
    sin_promesa.records("product.supplierinfo").clear()
    _con(monkeypatch, _server(sin_promesa))

    cuerpo = _post().json()

    assert cuerpo["status"] == "qa_failed"
    assert cuerpo["preflight"]["n_receipts"] == 3  # se leyo bien, falta con que comparar
    assert any("maestro de articulos" in i for i in cuerpo["qa_issues"])


# --- 5. el numero publicado se mueve con el dato -----------------------------


def test_el_titular_de_la_ruta_cambia_cuando_cambia_el_odoo(monkeypatch, abierto):
    """EL GATE ENDURECIDO, EN LA CAPA HTTP.

    Dos Odoo con el mismo declarado y comportamiento distinto tienen que producir
    respuestas distintas. Si no se movieran, la ruta estaria publicando una
    constante con forma de medicion.
    """
    def _corrida(dias):
        _con(monkeypatch, _server(_odoo(dias=dias)))
        r = _post()
        assert r.status_code == 200, r.text
        return r.json()

    lento, rapido = _corrida(40), _corrida(20)

    assert lento["status"] == rapido["status"] == "ok"
    assert lento["headline"]["worst_observed"] == 40.0
    assert rapido["headline"]["worst_observed"] == 20.0
    assert lento["headline"]["worst_declared"] == rapido["headline"]["worst_declared"] == 7.0
    assert lento["findings"] != rapido["findings"]


def test_la_respuesta_completa_trae_el_preflight_y_el_titular(monkeypatch, abierto):
    _con(monkeypatch, _server(_odoo(dias=34, n=3)))
    cuerpo = _post().json()

    assert cuerpo["status"] == "ok"
    assert cuerpo["connection"] == {"host": "miempresa.odoo.com", "server_version": "17.0+e"}
    pre = cuerpo["preflight"]
    assert (pre["n_receipts"], pre["n_suppliers"], pre["n_with_declared"]) == (3, 1, 1)
    assert pre["first_receipt"] and pre["last_receipt"] and pre["span_days"] == 56
    assert cuerpo["headline"]["worst_supplier"] == "Acme"
    assert cuerpo["headline"]["n_skus_to_recalculate"] == 1
    assert cuerpo["cta_url"]


def test_since_acota_la_lectura_y_el_resultado_lo_refleja(monkeypatch, abierto):
    """Un cliente con anos de historia no deberia leer todo para ver la brecha,
    y el recorte tiene que verse en el conteo -- no ser un parametro decorativo."""
    _con(monkeypatch, _server(_odoo(dias=20, n=3)))
    todo = _post().json()
    _con(monkeypatch, _server(_odoo(dias=20, n=3)))
    recortado = _post(since="2026-02-01").json()

    assert todo["status"] == "ok" and todo["preflight"]["n_receipts"] == 3
    assert recortado["preflight"]["n_receipts"] == 2
    assert recortado["preflight"]["first_receipt"] > todo["preflight"]["first_receipt"]
    # Y recortar por debajo del minimo de recepciones no devuelve un promedio de
    # dos: cae en la compuerta de QA del scan, que es la unica autoridad sobre eso.
    assert recortado["status"] == "qa_failed"


# --- 6. solo lectura ---------------------------------------------------------


def test_la_ruta_no_ejecuta_un_solo_metodo_de_escritura_contra_el_odoo(
    monkeypatch, abierto
):
    """El argumento de venta entero, comprobado por lo que se invoco."""
    srv = _con(monkeypatch, _server())
    _post()

    metodos = {m for m, _ in srv.calls}
    assert metodos and not (metodos & {"create", "write", "unlink"})


def test_la_ruta_no_escribe_la_credencial_en_el_archivo_de_leads(
    monkeypatch, abierto, tmp_path
):
    """El embudo del CSV registra una linea de telemetria por visitante. Esta
    ruta no registra nada: la identidad del ERP de un cliente no tiene por que
    aterrizar en disco para medir un embudo."""
    import webapp.app as appmod

    leads = tmp_path / "leads.jsonl"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    _con(monkeypatch, _server())

    _post()

    assert not leads.exists()
