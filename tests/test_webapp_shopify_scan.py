"""`POST /api/shopify-scan`: T03 y T11 desde una tienda Shopify viva.

Reemplaza la subida del CSV de demanda y del CSV de stock por una lectura
directa por la Admin API de una app CUSTOM, y desemboca en los MISMOS
`jobs/abc_xyz_job` y `jobs/excess_obsolete_job`. Lo que estos tests fijan, en
orden de cuanto duele que se rompa:

1. La ruta esta AUTENTICADA. Un Admin API access token de la tienda de un
   cliente no se pide en un formulario anonimo, aunque el alcance sea de solo
   lectura.
2. El token no sale por ningun lado de la respuesta, ni siquiera cuando el
   servidor lo devuelve dentro de un error.
3. Cada modo de falla sale con su propio `reason` y con un mensaje distinto: un
   400 generico no sirve en un embudo sin humano al otro lado.
4. Sin historial suficiente se responde `needs_data` nombrando lo que falta --
   nunca estirando la serie ni rellenando el costo.
5. La ruta NUNCA devuelve un plazo de proveedor, y declara por que: Shopify no
   tiene ordenes de compra ni recepciones. Una respuesta que simplemente lo
   omitiera es indistinguible de una que lo midio y le dio cero.
6. Y los numeros que publica SE MUEVEN con el dato de la tienda. Un titular
   congelado sobre motores que si se mueven es el mismo defecto una capa afuera.

Todo offline: se inyecta `shopify_connect.live_http`, o sea que se ejercita el
validador, los lectores, el preflight y el `ShopifyInventoryClient` REALES contra
el doble de transporte -- no un mock del resultado.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

from src.connectors import shopify_connect  # noqa: E402
from src.connectors.shopify_connect import (  # noqa: E402
    DOMINIO,
    DRIFT,
    REDACTED,
    REQUIRED_SCOPES,
    SCOPE,
    TOKEN,
    InMemoryShopifyAdmin,
    make_order,
)
from src.connectors.shopify_inventory import InMemoryShopifyInventory  # noqa: E402
from tests.test_connector_shopify_connect import (  # noqa: E402
    DOMINIO_OK,
    LOC_A,
    LOC_B,
    TOKEN_CLIENTE,
    _ordenes_estables,
)
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

CAMPOS = {"shop_domain": DOMINIO_OK, "access_token": TOKEN_CLIENTE}


@pytest.fixture()
def abierto(monkeypatch):
    """Sin llave configurada (el default de fabrica) y sin rate limit."""
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    security.reset_rate_limit()


def _tienda(sku1: float = 1000.0, dos_bodegas: bool = False, **kw) -> InMemoryShopifyAdmin:
    """Una tienda offline completa: ordenes, catalogo e inventario."""
    stock = {"SKU-1": {LOC_A: sku1}, "SKU-2": {LOC_A: 50.0}}
    locations = [{"id": LOC_A, "name": "Santiago", "isPrimary": True}]
    if dos_bodegas:
        stock["SKU-1"][LOC_B] = 40.0
        stock["SKU-2"][LOC_B] = 5.0
        locations.append({"id": LOC_B, "name": "Concepcion", "isPrimary": False})
    kw.setdefault("orders", _ordenes_estables())
    return InMemoryShopifyAdmin(
        inventory=InMemoryShopifyInventory(stock=stock, locations=locations), **kw
    )


def _con(monkeypatch, admin: InMemoryShopifyAdmin) -> InMemoryShopifyAdmin:
    """Inyecta el doble donde `shopify_connect` construye el cliente vivo.

    Se parchea la FABRICA y no `scan_input`, para que la ruta pase por la misma
    rama que en produccion -- la que construye el cliente y lo cierra en el
    `finally`.
    """
    monkeypatch.setattr(shopify_connect, "live_http", lambda creds, timeout=30.0: admin)
    return admin


def _post(**extra):
    return client.post("/api/shopify-scan", data={**CAMPOS, **extra})


# --- 1. la puerta ------------------------------------------------------------


def test_sin_llave_de_api_la_ruta_no_atiende(monkeypatch):
    """La decision que separa esta ruta del embudo publico de CSV."""
    monkeypatch.setattr(security, "API_KEY", "llave-del-operador")
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    security.reset_rate_limit()
    _con(monkeypatch, _tienda())

    r = _post()
    assert r.status_code in (401, 403)
    # Y con la llave si atiende: el gate es la llave, no una ruta rota.
    ok = client.post(
        "/api/shopify-scan", data=CAMPOS, headers={"X-API-Key": "llave-del-operador"}
    )
    assert ok.status_code == 200


def test_el_token_no_aparece_en_ninguna_respuesta(monkeypatch, abierto):
    """Ni siquiera cuando Shopify lo devuelve dentro de su propio error."""
    _con(monkeypatch, _tienda(errors=[{"message": f"traceback con {TOKEN_CLIENTE}"}]))
    r = _post()
    assert TOKEN_CLIENTE not in r.text
    assert REDACTED in r.text


def test_un_campo_vacio_lo_rechaza_el_handler_y_no_el_validador_de_forma(monkeypatch, abierto):
    """Un 422 de forma puede arrastrar el resto del formulario, que aqui trae el token."""
    _con(monkeypatch, _tienda())
    sin_dominio = client.post("/api/shopify-scan", data={"access_token": TOKEN_CLIENTE})
    sin_token = client.post("/api/shopify-scan", data={"shop_domain": DOMINIO_OK})

    for r, motivo in ((sin_dominio, DOMINIO), (sin_token, TOKEN)):
        assert r.status_code == 400, r.text
        assert r.json()["detail"]["reason"] == motivo
    assert TOKEN_CLIENTE not in sin_dominio.text


def test_un_parametro_no_numerico_se_rechaza_nombrando_el_campo(monkeypatch, abierto):
    _con(monkeypatch, _tienda())
    r = _post(required_days="doce")
    assert r.status_code == 400
    detalle = r.json()["detail"]
    assert detalle["reason"] == "parametro_invalido"
    assert "required_days" in detalle["message"]


# --- 2. los modos de falla ----------------------------------------------------


@pytest.mark.parametrize(
    "admin_kw, esperado",
    [
        ({"unreachable": OSError("no such host")}, DOMINIO),
        ({"status": 401}, TOKEN),
        ({"scopes": ("read_products",)}, SCOPE),
        ({"served_version": "2027-01"}, DRIFT),
    ],
)
def test_cada_modo_de_falla_sale_con_su_propio_motivo(monkeypatch, abierto, admin_kw, esperado):
    _con(monkeypatch, _tienda(**admin_kw))
    r = _post()
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == esperado


def test_los_cuatro_mensajes_son_distintos_entre_si(monkeypatch, abierto):
    """Cuatro modos que dan el mismo texto son, para el comerciante, un solo modo."""
    mensajes = []
    for kw in (
        {"unreachable": OSError("no such host")},
        {"status": 401},
        {"scopes": ("read_products",)},
        {"served_version": "2027-01"},
    ):
        _con(monkeypatch, _tienda(**kw))
        mensajes.append(_post().json()["detail"]["message"])
    assert len(set(mensajes)) == 4


def test_el_scope_faltante_viaja_nombrado_para_que_la_ui_no_parsee_texto(monkeypatch, abierto):
    _con(monkeypatch, _tienda(scopes=("read_products", "read_locations")))
    detalle = _post().json()["detail"]
    assert set(detalle["scopes_missing"]) == {"read_inventory", "read_orders"}


# --- 3. el plazo que no existe -------------------------------------------------


def test_la_ruta_nunca_devuelve_plazo_y_declara_por_que(monkeypatch, abierto):
    _con(monkeypatch, _tienda())
    cuerpo = _post().json()
    assert cuerpo["lead_time"]["status"] == "ausente"
    assert "no tiene ordenes de compra" in cuerpo["lead_time"]["reason"]
    assert "lead_time_days" not in _post().text


def test_la_ausencia_del_plazo_tambien_viaja_cuando_falta_dato(monkeypatch, abierto):
    _con(monkeypatch, _tienda(orders=_ordenes_estables()[:2]))
    cuerpo = _post(required_days="365").json()
    assert cuerpo["status"] == "needs_data"
    assert cuerpo["lead_time"]["status"] == "ausente"


# --- 4. lo que falta se nombra, nunca se rellena --------------------------------


def test_una_ventana_corta_responde_needs_data_nombrando_lo_que_falta(monkeypatch, abierto):
    _con(monkeypatch, _tienda(orders=_ordenes_estables()[:2]))
    r = _post(required_days="365")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["status"] == "needs_data"
    assert any("365" in m for m in cuerpo["missing"])
    assert shopify_connect.ALL_ORDERS_SCOPE in " ".join(cuerpo["missing"])
    # Y no publica numeros igual: `needs_data` con resultados adentro es
    # `ok` con una etiqueta puesta encima.
    assert "abc_xyz" not in cuerpo and "excess_obsolete" not in cuerpo


def test_sin_ordenes_responde_needs_data_y_no_revienta(monkeypatch, abierto):
    _con(monkeypatch, _tienda(orders=[]))
    cuerpo = _post().json()
    assert cuerpo["status"] == "needs_data"
    assert any("no hay ninguna orden de venta" in m for m in cuerpo["missing"])


def test_el_costo_mixto_bloquea_desde_la_ruta(monkeypatch, abierto):
    mixtas = [
        make_order("a", "2026-01-15T10:00:00Z", [("SKU-1", 10.0)], unit_cost=6.0),
        make_order("b", "2026-02-15T10:00:00Z", [("SKU-2", 10.0)], unit_cost=None),
        make_order("c", "2026-03-15T10:00:00Z", [("SKU-1", 10.0)], unit_cost=6.0),
    ]
    _con(monkeypatch, _tienda(orders=mixtas))
    cuerpo = _post().json()
    assert cuerpo["status"] == "needs_data"
    assert any("no tienen costo unitario" in m for m in cuerpo["missing"])


# --- 5. los numeros se mueven ---------------------------------------------------


def test_el_monto_en_riesgo_que_publica_la_ruta_cambia_con_el_stock(monkeypatch, abierto):
    """El gate endurecido, una capa afuera: dos tiendas, dos numeros distintos."""
    _con(monkeypatch, _tienda(sku1=1000.0))
    mucho = _post().json()["excess_obsolete"]
    _con(monkeypatch, _tienda(sku1=100.0))
    poco = _post().json()["excess_obsolete"]

    assert mucho["eo_value"] > poco["eo_value"] > 0
    assert mucho["eo_value"] - poco["eo_value"] == pytest.approx(900 * 6.0)


def test_la_clasificacion_que_publica_la_ruta_cambia_con_el_historial(monkeypatch, abierto):
    estable = _ordenes_estables()
    erratico = [
        make_order(f"z{m}", f"2026-{m:02d}-15T10:00:00Z", [("SKU-2", 4.0)], unit_cost=6.0)
        for m in range(1, 13)
    ] + [make_order("golpe", "2026-03-20T10:00:00Z", [("SKU-1", 120.0)], unit_cost=6.0)]

    _con(monkeypatch, _tienda(orders=estable))
    celdas_e = _post().json()["abc_xyz"]["cells"]
    _con(monkeypatch, _tienda(orders=erratico))
    celdas_z = _post().json()["abc_xyz"]["cells"]
    assert celdas_e != celdas_z


def test_el_corte_viaja_con_las_letras_porque_una_clase_sin_corte_no_es_falsable(
    monkeypatch, abierto
):
    _con(monkeypatch, _tienda())
    abc = _post().json()["abc_xyz"]
    assert abc["cut_criterion"]
    assert "80" in abc["cut_criterion"]


def test_sin_costo_la_ruta_declara_que_rankea_por_unidades(monkeypatch, abierto):
    _con(monkeypatch, _tienda(orders=_ordenes_estables(costo=None)))
    cuerpo = _post().json()
    assert cuerpo["abc_xyz"]["ranks_on_value"] is False
    assert cuerpo["preflight"]["cost_basis"] == shopify_connect.COSTO_UNIDADES
    assert any("por UNIDADES" in w for w in cuerpo["preflight"]["warnings"])


# --- 6. la respuesta completa ----------------------------------------------------


def test_la_respuesta_trae_conexion_preflight_y_las_dos_tareas(monkeypatch, abierto):
    _con(monkeypatch, _tienda(dos_bodegas=True))
    cuerpo = _post().json()

    assert cuerpo["status"] == "ok"
    assert cuerpo["connection"]["shop_domain"] == DOMINIO_OK
    assert set(cuerpo["connection"]["scopes"]) == set(REQUIRED_SCOPES)
    assert cuerpo["preflight"]["n_orders"] == 12
    assert cuerpo["preflight"]["n_periods"] == 12
    assert cuerpo["abc_xyz"]["n_skus"] == 2
    assert cuerpo["excess_obsolete"]["eo_value"] > 0
    assert shopify_connect.PLAZO_AUSENTE in cuerpo["source_notes"]


def test_la_ruta_expone_la_dimension_location_en_vez_de_plegarla(monkeypatch, abierto):
    """Sin location no hay historia de pooling, que es la fuente de valor #2."""
    _con(monkeypatch, _tienda(dos_bodegas=True))
    cuerpo = _post().json()

    nombres = {loc["name"] for loc in cuerpo["locations"]}
    assert nombres == {"Santiago", "Concepcion"}
    detalle = cuerpo["stock_by_location"]
    assert detalle["n_multi_location"] == 2
    assert detalle["truncated"] is False
    assert {f["name"] for f in detalle["skus"]["SKU-1"]} == {"Santiago", "Concepcion"}


def test_una_tienda_de_una_sola_bodega_no_inventa_candidatos_de_pooling(monkeypatch, abierto):
    _con(monkeypatch, _tienda(dos_bodegas=False))
    detalle = _post().json()["stock_by_location"]
    assert detalle["n_multi_location"] == 0
    assert detalle["skus"] == {}


# --- 7. la lectura masiva por la ruta ------------------------------------------------


def _sin_red(monkeypatch, admin):
    """El descargador del resultado, servido por el doble en vez de por la red."""
    monkeypatch.setattr(shopify_connect, "download_jsonl", lambda url: admin.jsonl())
    return admin


def test_el_modo_masivo_publica_los_mismos_numeros_que_el_paginado(monkeypatch, abierto):
    """Dos puertas al mismo motor: si dieran distinto, una de las dos miente."""
    admin = _con(monkeypatch, _tienda())
    paginado = _post().json()

    _sin_red(monkeypatch, admin)
    masivo = _post(orders_mode="bulk").json()

    assert masivo["status"] == paginado["status"] == "ok"
    assert masivo["abc_xyz"] == paginado["abc_xyz"]
    assert masivo["excess_obsolete"] == paginado["excess_obsolete"]
    assert masivo["preflight"]["n_orders"] == paginado["preflight"]["n_orders"]
    # Y la respuesta dice por que puerta entro.
    assert masivo["connection"]["orders_mode"] == "bulk"
    assert paginado["connection"]["orders_mode"] == "paged"


def test_mientras_la_lectura_masiva_corre_la_ruta_devuelve_in_progress(monkeypatch, abierto):
    admin = _con(monkeypatch, _tienda(bulk_polls_running=99))
    _sin_red(monkeypatch, admin)
    monkeypatch.setattr("webapp.app._BULK_MAX_WAIT_SEGUNDOS", 0.0)

    cuerpo = _post(orders_mode="bulk").json()
    assert cuerpo["status"] == "in_progress"
    assert cuerpo["bulk_id"]
    assert cuerpo["bulk_status"] == "RUNNING"
    # Sin resultados: un parcial con etiqueta de final no se distingue de uno completo.
    assert "abc_xyz" not in cuerpo and "excess_obsolete" not in cuerpo
    # Y la ausencia del plazo viaja igual.
    assert cuerpo["lead_time"]["status"] == "ausente"


def test_volver_con_el_bulk_id_no_arranca_una_segunda_lectura(monkeypatch, abierto):
    admin = _con(monkeypatch, _tienda())
    _sin_red(monkeypatch, admin)

    cuerpo = _post(orders_mode="bulk", bulk_id="gid://shopify/BulkOperation/1").json()
    assert cuerpo["status"] == "ok"
    assert "bulkRun" not in admin.calls


def test_un_modo_de_ordenes_desconocido_se_rechaza_nombrandolo(monkeypatch, abierto):
    _con(monkeypatch, _tienda())
    r = _post(orders_mode="turbo")
    assert r.status_code == 400
    detalle = r.json()["detail"]
    assert detalle["reason"] == "parametro_invalido"
    assert "orders_mode" in detalle["message"] and "turbo" in detalle["message"]


def test_una_lectura_masiva_fallida_no_publica_numeros(monkeypatch, abierto):
    admin = _con(monkeypatch, _tienda(bulk_status="FAILED", bulk_error_code="TIMEOUT"))
    _sin_red(monkeypatch, admin)
    cuerpo = _post(orders_mode="bulk").json()
    assert cuerpo["status"] == "needs_data"
    assert any("TIMEOUT" in m for m in cuerpo["missing"])
    assert "abc_xyz" not in cuerpo


# --- 8. solo lectura --------------------------------------------------------------


def test_la_ruta_no_ejecuta_una_sola_escritura_contra_shopify(monkeypatch, abierto):
    """El argumento de venta entero, comprobado por lo que se invoco."""
    admin = _con(monkeypatch, _tienda())
    _post()

    assert admin.calls, "la ruta no llamo a nada: el test no probaria nada"
    assert set(admin.calls) <= {
        "shop", "accessScopes", "catalog", "orders", "locations", "stockAtLocation"
    }
    assert admin._inventory.idempotency_keys == []


def test_un_scope_de_escritura_concedido_se_reporta_para_que_lo_desmarquen(monkeypatch, abierto):
    _con(monkeypatch, _tienda(scopes=REQUIRED_SCOPES + ("write_inventory",)))
    cuerpo = _post().json()
    assert cuerpo["connection"]["write_scopes"] == ["write_inventory"]
    assert any("ESCRITURA" in w for w in cuerpo["preflight"]["warnings"])


def test_la_ruta_no_escribe_el_token_en_el_archivo_de_leads(monkeypatch, abierto, tmp_path):
    """El embudo del CSV registra una linea de telemetria por visitante. Esta
    ruta no registra nada: el token de la tienda de un cliente no tiene por que
    aterrizar en disco para medir un embudo."""
    import webapp.app as appmod

    leads = tmp_path / "leads.jsonl"
    monkeypatch.setattr(appmod, "LEADS_FILE", leads)
    _con(monkeypatch, _tienda())

    _post()
    assert not leads.exists()
