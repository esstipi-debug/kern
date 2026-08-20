"""Onboarding autoservicio de Shopify por app custom: validar, leer, y NO medir de mas.

La app custom es la que el comerciante crea en su propio admin y de la que saca un
Admin API access token. Lo que faltaba no era el conector -- ``shopify_inventory.py``
ya habla GraphQL -- sino el tramo de DOS campos hasta los mismos motores de T03
(ABC/XYZ) y T11 (excedente/obsoleto) que hoy consumen un CSV.

**La regla del gate endurecido.** Que un campo exista no acredita nada. Este
proyecto ya pago 17,5 puntos porcentuales por comprobaciones que acreditaban por
presencia de nombre de campo: ``assert "abc" in fila`` pasa igual de verde sobre
una clasificacion correcta que sobre una constante. Cada test de aqui abajo que
afirma que algo se MIDE lo demuestra corriendo DOS insumos distintos y
comprobando que el numero de salida se movio, y en el sentido correcto. Los que
afirman que algo se CLASIFICA producen los cuatro modos y comprueban que salen
cuatro respuestas distintas, no cuatro veces la misma.

**Lo que este archivo tambien tiene que probar es una ausencia.** Shopify no
tiene ordenes de compra ni recepciones: el plazo de proveedor es imposible por
esta ruta. Un test comprueba que se declara ausente con motivo y que ninguna
salida trae un plazo -- porque una salida que simplemente lo omite es
indistinguible de una que lo midio y le dio cero.
"""
from __future__ import annotations

import pandas as pd
import pytest

from jobs import abc_xyz_job, excess_obsolete_job
from src.connectors.shopify_api_version import PINNED_VERSION
from src.connectors.shopify_connect import (
    ALL_ORDERS_SCOPE,
    CATALOG_QUERY,
    COSTO_REAL,
    COSTO_UNIDADES,
    DOMINIO,
    DRIFT,
    FOUR_MODES,
    ORDERS_QUERY,
    ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS,
    PLAZO_AUSENTE,
    REDACTED,
    REQUIRED_SCOPES,
    SCOPE,
    SCOPE_COPY,
    SCOPES_QUERY,
    SERVIDOR,
    SHOP_QUERY,
    STOCK_QUERY,
    TOKEN,
    InMemoryShopifyAdmin,
    ShopifyConnectError,
    ShopifyCredentials,
    StockRead,
    bare_client,
    bulk_orders_query,
    check_connection,
    download_jsonl,
    eo_rows,
    fold_locations,
    make_order,
    normalize_shop_domain,
    preflight,
    read_catalog,
    read_sales_history,
    read_sales_history_bulk,
    read_stock_on_hand,
    scan_input,
    setup_copy,
)
from src.connectors.shopify_inventory import InMemoryShopifyInventory

TOKEN_CLIENTE = "shpat_0f1e2d3c-token-del-comerciante"
DOMINIO_OK = "mitienda.myshopify.com"
LOC_A = "gid://shopify/Location/1"
LOC_B = "gid://shopify/Location/2"


def _creds(dominio: str = DOMINIO_OK, token: str = TOKEN_CLIENTE) -> ShopifyCredentials:
    return ShopifyCredentials(dominio, token)


def _ordenes_estables(unidades: float = 10.0, costo: float | None = 6.0) -> list[dict]:
    """Doce meses, la misma cantidad todos los meses: el caso X (estable)."""
    return [
        make_order(
            f"e{mes}",
            f"2026-{mes:02d}-15T10:00:00Z",
            [("SKU-1", unidades), ("SKU-2", 4.0)],
            unit_cost=costo,
        )
        for mes in range(1, 13)
    ]


def _ordenes_erraticas(costo: float | None = 6.0) -> list[dict]:
    """Las MISMAS unidades totales de SKU-1, pero todas en un mes: el caso Z.

    SKU-2 vende todos los meses a proposito: es lo que sostiene el rango de doce
    meses cuando SKU-1 no vende. Sin ese ancla, la ventana observada colapsaria a
    un mes y el test dejaria de probar lo que dice probar.
    """
    ordenes = [
        make_order(f"z{mes}", f"2026-{mes:02d}-15T10:00:00Z", [("SKU-2", 4.0)], unit_cost=costo)
        for mes in range(1, 13)
    ]
    ordenes.append(
        make_order("z-golpe", "2026-03-20T10:00:00Z", [("SKU-1", 120.0)], unit_cost=costo)
    )
    return ordenes


def _inventario(sku1: float = 1000.0, sku2: float = 50.0, dos_bodegas: bool = False) -> InMemoryShopifyInventory:
    stock = {"SKU-1": {LOC_A: sku1}, "SKU-2": {LOC_A: sku2}}
    locations = [{"id": LOC_A, "name": "Santiago", "isPrimary": True}]
    if dos_bodegas:
        stock["SKU-1"][LOC_B] = 40.0
        stock["SKU-2"][LOC_B] = 5.0
        locations.append({"id": LOC_B, "name": "Concepcion", "isPrimary": False})
    return InMemoryShopifyInventory(stock=stock, locations=locations)


def _tienda(admin: InMemoryShopifyAdmin, inv: InMemoryShopifyInventory | None = None) -> InMemoryShopifyAdmin:
    """Une el doble de transporte con su inventario: una sola tienda.

    `scan_input` ya no toma un `rpc` aparte -- el roster de bodegas y los niveles
    salen del mismo transporte -- asi que el inventario se cuelga del doble.
    """
    admin._inventory = inv if inv is not None else _inventario()
    return admin


def _stock(inv: InMemoryShopifyInventory, skus=None, *, admin=None, **kw) -> "StockRead":
    """La lectura por lote contra un inventario en memoria.

    Reemplaza al viejo `read_stock_on_hand(inv, [skus])`, que resolvia una
    location por llamada. El roster sigue saliendo del conector; los niveles van
    en lote por bodega.
    """
    adm = admin if admin is not None else InMemoryShopifyAdmin(_ordenes_estables(), inventory=inv, **kw)
    if adm._inventory is None:
        adm._inventory = inv
    return read_stock_on_hand(adm, inv.list_locations(), skus=skus)


def _clasificar(rows) -> dict[str, str]:
    """Las filas -> la celda ABC/XYZ por SKU, pasando por el motor real."""
    df = pd.DataFrame(list(rows))
    items = abc_xyz_job.aggregate_skus(df, product_col="product_id", demand_col="quantity", cost_col="unit_cost")
    reporte = abc_xyz_job.run(items)
    return {c.product_id: c.cell for c in reporte.classifications}


def _excedente(scan) -> float:
    """Las filas -> el valor en riesgo de E&O, pasando por el motor real."""
    payload = excess_obsolete_job.prepare_records(pd.DataFrame(list(scan.eo_rows)))
    return excess_obsolete_job.run(payload).eo_value


# -- los cuatro modos de falla ---------------------------------------------------


def test_dominio_invalido_se_nombra_y_no_se_confunde_con_token():
    admin = InMemoryShopifyAdmin(unreachable=OSError("Name or service not known"))
    check = check_connection(_creds(), http=admin)
    assert check.reason == DOMINIO
    assert check.reason != TOKEN
    assert "dominio" in check.message.lower()
    assert check.actionable


def test_un_dominio_que_no_es_myshopify_se_rechaza_antes_de_salir_a_la_red():
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    check = check_connection(ShopifyCredentials("www.mitienda.cl", TOKEN_CLIENTE), http=admin)
    assert check.reason == DOMINIO
    assert "myshopify" in check.message
    # No se intento nada: adivinar el subdominio a partir del dominio publico
    # daria otra tienda, y una lectura correcta sobre la tienda equivocada es lo
    # unico peor que fallar.
    assert admin.calls == []


def test_token_invalido_se_nombra_como_token_y_no_como_dominio():
    admin = InMemoryShopifyAdmin(status=401)
    check = check_connection(_creds(), http=admin)
    assert check.reason == TOKEN
    assert "401" in check.message


def test_un_token_que_no_es_de_app_custom_recibe_la_pista_del_prefijo():
    admin = InMemoryShopifyAdmin(status=401)
    con_shpat = check_connection(_creds(token="shpat_bueno-pero-revocado"), http=admin)
    sin_shpat = check_connection(_creds(token="shpca_esto-es-otra-cosa"), http=admin)
    assert con_shpat.reason == TOKEN and sin_shpat.reason == TOKEN
    # La pista solo aparece cuando el prefijo NO es el de app custom: agregarla
    # siempre la volveria ruido que nadie lee.
    assert "shpat_" in sin_shpat.message
    assert "shpat_" not in con_shpat.message


def test_scope_faltante_dice_CUAL_falta_y_no_solo_que_falta_alguno():
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), scopes=("read_products", "read_locations")
    )
    check = check_connection(_creds(), http=admin)
    assert check.reason == SCOPE
    assert set(check.scopes_missing) == {"read_inventory", "read_orders"}
    for s in check.scopes_missing:
        assert s in check.message
    # Y no nombra los que SI estan: mandar al comerciante a marcar algo ya
    # marcado es como se pierde la unica vuelta de atencion que da.
    assert "read_products" not in check.message.split("faltan")[1].split(".")[0]


def test_los_scopes_faltantes_se_reportan_TODOS_de_una_vez():
    admin = InMemoryShopifyAdmin(_ordenes_estables(), scopes=())
    check = check_connection(_creds(), http=admin)
    assert check.reason == SCOPE
    # Los cuatro, no el primero que fallo: un comerciante que vuelve a la
    # pantalla de permisos cuatro veces seguidas abandona.
    assert set(check.scopes_missing) == set(REQUIRED_SCOPES)


def test_drift_de_version_se_nombra_como_drift_y_no_como_error_del_servidor():
    admin = InMemoryShopifyAdmin(_ordenes_estables(), served_version="2027-01")
    check = check_connection(_creds(), http=admin)
    assert check.reason == DRIFT
    assert check.api_version_served == "2027-01"
    assert check.api_version_requested == PINNED_VERSION
    # No es un problema de la tienda, y el mensaje tiene que decirlo: mandar al
    # comerciante a revisar SU configuracion por un pin vencido nuestro es media
    # hora perdida de alguien que no puede arreglarlo.
    assert "no es un problema de tu tienda" in check.message.lower()


def test_los_cuatro_modos_producen_cuatro_diagnosticos_distintos():
    """El punto entero de clasificar: cuatro casos, cuatro pantallas distintas."""
    casos = {
        DOMINIO: InMemoryShopifyAdmin(unreachable=OSError("no such host")),
        TOKEN: InMemoryShopifyAdmin(status=401),
        SCOPE: InMemoryShopifyAdmin(_ordenes_estables(), scopes=("read_products",)),
        DRIFT: InMemoryShopifyAdmin(_ordenes_estables(), served_version="2027-01"),
    }
    obtenidos = {esperado: check_connection(_creds(), http=admin) for esperado, admin in casos.items()}

    assert {esperado: c.reason for esperado, c in obtenidos.items()} == {m: m for m in FOUR_MODES}
    mensajes = [c.message for c in obtenidos.values()]
    assert len(set(mensajes)) == 4, "cuatro modos que dan el mismo texto son un solo modo"
    assert all(c.actionable for c in obtenidos.values())


def test_una_respuesta_que_no_se_reconoce_no_se_fuerza_dentro_de_los_cuatro():
    admin = InMemoryShopifyAdmin(errors=[{"message": "algo raro del lado de Shopify"}])
    check = check_connection(_creds(), http=admin)
    assert check.reason == SERVIDOR
    assert check.reason not in FOUR_MODES
    assert "algo raro" in check.message


def test_lo_que_contesta_pero_no_es_shopify_es_dominio_y_no_token():
    admin = InMemoryShopifyAdmin(payload={"pagina": "de login de otro sistema"})
    check = check_connection(_creds(), http=admin)
    assert check.reason == DOMINIO


def test_una_conexion_buena_pasa_y_reporta_la_tienda_y_los_scopes():
    admin = InMemoryShopifyAdmin(_ordenes_estables(), shop_name="Ferreteria Sur")
    check = check_connection(_creds(), http=admin)
    assert check.ok and check.reason == ""
    assert check.shop_name == "Ferreteria Sur"
    assert set(check.scopes_granted) == set(REQUIRED_SCOPES)


# -- el secreto ------------------------------------------------------------------


def test_el_token_nunca_viaja_en_el_mensaje_de_error():
    # Un cuerpo de error que trae el token dentro: no lo controlamos nosotros.
    admin = InMemoryShopifyAdmin(errors=[{"message": f"fallo con {TOKEN_CLIENTE} adjunto"}])
    check = check_connection(_creds(), http=admin)
    assert TOKEN_CLIENTE not in check.message
    assert REDACTED in check.message


def test_el_token_no_entra_al_repr_de_las_credenciales():
    r = repr(_creds())
    assert TOKEN_CLIENTE not in r
    assert DOMINIO_OK in r


def test_ningun_resultado_de_la_ruta_completa_lleva_el_token():
    admin = InMemoryShopifyAdmin(errors=[{"message": f"traceback con {TOKEN_CLIENTE}"}])
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()))
    assert TOKEN_CLIENTE not in repr(scan)
    assert TOKEN_CLIENTE not in str(scan.check.message)


# -- solo lectura ----------------------------------------------------------------


def test_la_ruta_entera_no_ejecuta_una_sola_escritura_de_inventario():
    inv = _inventario()
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    scan = scan_input(_creds(), http=_tienda(admin, inv))
    assert scan.ok
    # `InMemoryShopifyInventory` solo apunta una clave de idempotencia cuando
    # alguien llama a adjust_inventory o set_inventory. Vacia = no se escribio.
    assert inv.idempotency_keys == []
    # Y del lado GraphQL, solo consultas.
    assert set(admin.calls) <= {
        "shop", "accessScopes", "catalog", "orders", "locations", "stockAtLocation"
    }


def test_el_modulo_no_importa_el_plano_de_escritura():
    """Solo lectura por construccion, no por disciplina de quien lo edite.

    Se comprueba sobre el AST y no sobre el texto. Un ``"set_inventory" not in
    fuente`` da rojo por la prosa del docstring que declara que NO se escribe, y
    verde ante un ``getattr(rpc, "set_" + "inventory")``: o sea, falla en los dos
    sentidos. El arbol responde la pregunta que importa -- que se importa y que
    se llama.
    """
    import ast
    from pathlib import Path

    arbol = ast.parse(Path("src/connectors/shopify_connect.py").read_text(encoding="utf-8"))

    importados = set()
    llamados = set()
    atributos = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module)
        elif isinstance(nodo, ast.Import):
            importados.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) is False and isinstance(nodo, ast.Attribute):
            atributos.add(nodo.attr)
        if isinstance(nodo, ast.Call):
            f = nodo.func
            llamados.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))

    assert not any(m.startswith("nosilo_core") for m in importados), importados
    assert "ShopifyInventoryStore" not in llamados
    for escritura in ("adjust_inventory", "set_inventory", "commit", "rollback", "claim"):
        assert escritura not in llamados, f"la ruta de solo lectura llama a {escritura}"
        assert escritura not in atributos, f"la ruta de solo lectura referencia {escritura}"


def test_los_scopes_pedidos_son_los_cuatro_de_lectura_y_ninguno_de_escritura():
    assert REQUIRED_SCOPES == ("read_products", "read_inventory", "read_orders", "read_locations")
    assert not any(s.startswith("write_") for s in REQUIRED_SCOPES)


def test_un_scope_de_escritura_concedido_se_reporta_en_vez_de_pasar_desapercibido():
    """"Solo lectura" es una afirmacion sobre permisos: si nadie la mide, es una promesa."""
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), scopes=REQUIRED_SCOPES + ("write_inventory",)
    )
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()))
    assert scan.check.write_scopes == ("write_inventory",)
    assert any("write_inventory" in w for w in scan.preflight.warnings)
    # Sigue sin escribir: reportarlo no es usarlo.
    assert scan.ok


# -- el plazo que NO existe -------------------------------------------------------


def test_el_plazo_de_proveedor_se_declara_ausente_con_motivo_y_no_se_deriva():
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()))
    assert scan.ok
    assert PLAZO_AUSENTE in scan.source_notes
    assert "AUSENTE" in PLAZO_AUSENTE and "no tiene ordenes de compra" in PLAZO_AUSENTE
    # Ninguna fila trae nada con forma de plazo. Una salida que simplemente lo
    # omite es indistinguible de una que lo midio y le dio cero.
    for fila in list(scan.demand_rows) + list(scan.eo_rows) + list(scan.stock_rows):
        assert not any("lead" in k or "plazo" in k for k in fila)


def test_la_ausencia_del_plazo_se_declara_incluso_cuando_la_conexion_falla():
    admin = InMemoryShopifyAdmin(status=401)
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()))
    assert not scan.ok
    assert PLAZO_AUSENTE in scan.source_notes


# -- T03: la clasificacion se MUEVE ----------------------------------------------


def test_dos_historiales_distintos_dan_clasificaciones_ABC_XYZ_distintas():
    """El gate endurecido: dos insumos, y la LETRA de salida se mueve."""
    estable = read_sales_history(InMemoryShopifyAdmin(_ordenes_estables()))
    erratico = read_sales_history(InMemoryShopifyAdmin(_ordenes_erraticas()))

    celdas_e = _clasificar(estable.rows)
    celdas_z = _clasificar(erratico.rows)

    assert celdas_e["SKU-1"][1] == "X", celdas_e
    assert celdas_z["SKU-1"][1] == "Z", celdas_z
    assert celdas_e["SKU-1"] != celdas_z["SKU-1"]


def test_los_meses_sin_venta_se_emiten_porque_sin_ellos_la_letra_sale_mal():
    """Un mes sin lineas vendio cero, y ese cero es lo que hace erratico a un SKU.

    Sin las filas en cero, SKU-1 tendria UNA sola observacion (120 unidades), un
    coeficiente de variacion de 0 y saldria X (estable) siendo el caso mas
    erratico del catalogo. El defecto no se ve en la forma de la salida: se ve
    como una letra equivocada al lado de un SKU.
    """
    historial = read_sales_history(InMemoryShopifyAdmin(_ordenes_erraticas()))
    filas_sku1 = [f for f in historial.rows if f["product_id"] == "SKU-1"]
    assert len(filas_sku1) == 12
    assert sum(1 for f in filas_sku1 if f["quantity"] == 0.0) == 11
    assert sum(f["quantity"] for f in filas_sku1) == 120.0

    # La prueba de que importa: la serie SIN los ceros clasifica al reves.
    sin_ceros = [f for f in historial.rows if f["quantity"] > 0]
    assert _clasificar(historial.rows)["SKU-1"][1] == "Z"
    assert _clasificar(sin_ceros)["SKU-1"][1] == "X"


def test_las_filas_de_shopify_entran_al_motor_por_el_MISMO_camino_que_el_csv(tmp_path):
    """El motor no se entera del origen: mismo lector de CSV, misma salida."""
    historial = read_sales_history(InMemoryShopifyAdmin(_ordenes_estables()))
    csv = tmp_path / "demanda.csv"
    pd.DataFrame(list(historial.rows)).to_csv(csv, index=False)

    # `prepare` es el camino del CSV: olfatea columnas y no sabe de Shopify.
    desde_csv = abc_xyz_job.run(abc_xyz_job.prepare(str(csv)))
    desde_shopify = abc_xyz_job.run(
        abc_xyz_job.aggregate_skus(
            pd.DataFrame(list(historial.rows)),
            product_col="product_id", demand_col="quantity", cost_col="unit_cost",
        )
    )
    assert {c.product_id: c.cell for c in desde_csv.classifications} == {
        c.product_id: c.cell for c in desde_shopify.classifications
    }


# -- T11: el excedente se MUEVE ---------------------------------------------------


def test_dos_stocks_distintos_dan_montos_de_excedente_distintos():
    """El gate endurecido: dos insumos, y el MONTO en riesgo se mueve, y en el sentido correcto."""
    ordenes = _ordenes_estables()
    mucho = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(ordenes), _inventario(sku1=1000.0)))
    poco = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(ordenes), _inventario(sku1=100.0)))
    assert mucho.ok and poco.ok

    valor_mucho, valor_poco = _excedente(mucho), _excedente(poco)
    assert valor_mucho > valor_poco > 0, (valor_mucho, valor_poco)
    # Y la diferencia es la del stock valorizado, no un desplazamiento cualquiera:
    # 900 unidades de mas a 6.0 de costo.
    assert valor_mucho - valor_poco == pytest.approx(900 * 6.0)


def test_mas_demanda_con_el_mismo_stock_baja_el_excedente():
    """El otro eje del mismo motor: mover la DEMANDA mueve el monto al reves."""
    lento = scan_input(
        _creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables(unidades=10.0)), _inventario(sku1=1000.0))
    )
    rapido = scan_input(
        _creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables(unidades=60.0)), _inventario(sku1=1000.0))
    )
    assert _excedente(rapido) < _excedente(lento)


def test_un_sku_con_stock_y_sin_ventas_llega_al_motor_como_demanda_cero():
    """Cero observado, no dato faltante: la lectura cubrio la ventana entera."""
    inv = InMemoryShopifyInventory(
        stock={"SKU-1": {LOC_A: 100.0}, "SKU-2": {LOC_A: 20.0}, "SKU-MUERTO": {LOC_A: 500.0}},
        locations=[{"id": LOC_A, "name": "Santiago", "isPrimary": True}],
    )
    historial = read_sales_history(InMemoryShopifyAdmin(_ordenes_estables()))
    # `read_stock_on_hand` se pide sobre los SKU vendidos; el muerto entra si se
    # lo pide explicitamente, que es lo que hace un catalogo completo.
    stock = _stock(inv, ["SKU-1", "SKU-2", "SKU-MUERTO"],
                   admin=InMemoryShopifyAdmin(_ordenes_estables(), inventory=inv,
                       catalog=[("SKU-1", 6.0), ("SKU-2", 6.0), ("SKU-MUERTO", 6.0)])).rows
    filas = {f["product_id"]: f for f in eo_rows(historial, stock)}
    assert filas["SKU-MUERTO"]["daily_demand"] == 0.0
    assert filas["SKU-1"]["daily_demand"] > 0.0
    reporte = excess_obsolete_job.run(excess_obsolete_job.prepare_records(pd.DataFrame(list(filas.values()))))
    assert reporte.n_dead >= 1


# -- el catalogo: el universo de SKU no puede salir de lo que vendio ---------------


def test_un_sku_con_stock_y_sin_ventas_llega_a_las_DOS_tareas_por_la_ruta_completa():
    """El defecto que este catalogo evita no deja hueco: la salida se ve completa igual.

    Si el universo de SKU saliera del historial de venta, un SKU que no vendio
    nada no apareceria en ninguna orden y por lo tanto no existiria para la
    salida. Es decir: el analisis de stock muerto seria ciego justo al stock
    muerto, y nadie lo notaria mirando el reporte.
    """
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(),
        catalog=[("SKU-1", 6.0), ("SKU-2", 6.0), ("SKU-MUERTO", 6.0)],
    )
    inv = InMemoryShopifyInventory(
        stock={"SKU-1": {LOC_A: 1000.0}, "SKU-2": {LOC_A: 50.0}, "SKU-MUERTO": {LOC_A: 500.0}},
        locations=[{"id": LOC_A, "name": "Santiago", "isPrimary": True}],
    )
    scan = scan_input(_creds(), http=_tienda(admin, inv))
    assert scan.ok

    # T03: valor cero y variabilidad infinita -> CZ, candidato a descontinuar.
    assert _clasificar(scan.demand_rows)["SKU-MUERTO"] == "CZ"
    # T11: sin demanda -> muerto, con sus 500 unidades valorizadas.
    reporte = excess_obsolete_job.run(
        excess_obsolete_job.prepare_records(pd.DataFrame(list(scan.eo_rows)))
    )
    muerto = {e.product_id: e for e in reporte.lines}["SKU-MUERTO"]
    assert muerto.classification == "dead"
    assert muerto.excess_value == pytest.approx(500 * 6.0)


def test_el_catalogo_aporta_el_costo_de_lo_que_NO_vendio():
    """La linea de venta solo trae el costo de lo que se vendio."""
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(costo=None),  # las ordenes no traen costo...
        catalog=[("SKU-1", 8.0), ("SKU-2", 8.0)],  # ...el catalogo si
    )
    historial = read_sales_history(admin, catalog=read_catalog(admin))
    assert historial.cost_basis == COSTO_REAL
    assert historial.cost_by_sku == {"SKU-1": 8.0, "SKU-2": 8.0}


def test_un_sku_que_no_vendio_se_nombra_en_el_preflight_y_no_pasa_callado():
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), catalog=[("SKU-1", 6.0), ("SKU-2", 6.0), ("SKU-MUERTO", 6.0)]
    )
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()))
    assert any("no vendieron ni una unidad" in w for w in scan.preflight.warnings)
    assert scan.preflight.n_skus == 3


def test_una_variante_sin_sku_no_recibe_un_identificador_inventado():
    admin = InMemoryShopifyAdmin(_ordenes_estables(), catalog=[("SKU-1", 6.0), ("", 6.0)])
    catalogo = read_catalog(admin)
    assert catalogo.skus == ("SKU-1",)
    assert catalogo.dropped["variante_sin_sku"] == 1
    assert catalogo.n_variants == 2


def test_un_catalogo_truncado_bloquea_en_vez_de_seguir_con_medio_catalogo():
    """Sin regla de tope silencioso: medio catalogo se ve igual de completo que uno entero."""
    admin = InMemoryShopifyAdmin(_ordenes_estables(), catalog=[("SKU-1", 6.0), ("SKU-2", 6.0)])

    class _Cortado(InMemoryShopifyAdmin):
        def post(self, path, *, json=None, **kw):
            r = super().post(path, json=json, **kw)
            cuerpo = r.json()
            variantes = (cuerpo.get("data") or {}).get("productVariants") if isinstance(cuerpo, dict) else None
            if variantes:
                variantes["pageInfo"] = {"hasNextPage": True, "endCursor": "1"}
            return r

    cortado = _Cortado(_ordenes_estables(), catalog=[("SKU-1", 6.0), ("SKU-2", 6.0)])
    catalogo = read_catalog(cortado, max_variants=2)
    assert catalogo.truncated
    historial = read_sales_history(admin, catalog=catalogo)
    pre = preflight(historial, _stock(_inventario(), ["SKU-1", "SKU-2"]))
    assert not pre.ok
    assert any("catalogo" in m and "incompleto" in m for m in pre.missing)


# -- el copy del onboarding, y el permiso que nadie marca ---------------------------


def test_sin_read_all_orders_se_avisa_SIEMPRE_y_no_solo_si_se_pidio_una_ventana():
    """El tope silencioso que tenia este modulo, y la razon de que el copy exista.

    Shopify contesta 200 con una lista corta, no con un error. Antes el aviso solo
    salia cuando el llamador pedia `required_days`; sin ese parametro el
    comerciante recibia un analisis armado sobre 60 dias y NADA se lo decia -- el
    preflight contaba lo que llego y la clasificacion salia con la forma correcta
    y las letras equivocadas. Un tope que nadie declara se lee como "esto es todo
    lo que hay".
    """
    admin = InMemoryShopifyAdmin(_ordenes_estables()[:2])  # sin read_all_orders
    scan = scan_input(_creds(), http=_tienda(admin))  # y sin pedir ventana

    assert scan.ok, "no bloquea: corre igual, pero avisando"
    aviso = [w for w in scan.preflight.warnings if ALL_ORDERS_SCOPE in w]
    assert aviso, scan.preflight.warnings
    assert str(ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS) in aviso[0]


def test_con_read_all_orders_no_se_avisa_de_los_60_dias():
    """Avisar de un permiso ya marcado es la vuelta perdida del comerciante."""
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), scopes=REQUIRED_SCOPES + (ALL_ORDERS_SCOPE,)
    )
    scan = scan_input(_creds(), http=_tienda(admin))
    assert scan.check.all_orders
    assert not [w for w in scan.preflight.warnings if ALL_ORDERS_SCOPE in w]


def test_el_copy_cubre_todos_los_permisos_que_el_codigo_exige():
    """El guardia contra la desincronizacion.

    Si alguien agrega un scope a REQUIRED_SCOPES y el copy fuera una lista
    escrita a mano en otro lado, el comerciante configuraria de menos y se
    enteraria recien al fallar -- y la lista vieja es justamente la que lee antes
    de configurar.
    """
    assert set(REQUIRED_SCOPES) <= set(SCOPE_COPY)
    copy = setup_copy()
    assert [p["handle"] for p in copy["permisos"]["obligatorios"]] == list(REQUIRED_SCOPES)
    assert all(p["para_que"] for p in copy["permisos"]["obligatorios"])


def test_el_copy_nombra_read_all_orders_como_el_que_se_pasa_por_alto():
    copy = setup_copy()
    recomendado = copy["permisos"]["recomendado"][0]
    assert recomendado["handle"] == ALL_ORDERS_SCOPE
    assert str(ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS) in recomendado["para_que"]
    # Y dice que la respuesta es exitosa, que es lo que lo vuelve invisible.
    assert "EXITOSA" in recomendado["para_que"].upper()


def test_el_copy_declara_que_nunca_se_piden_permisos_de_escritura():
    """El argumento de venta entero, en la pantalla donde el comerciante decide."""
    copy = setup_copy()
    nunca = copy["permisos"]["nunca_se_piden"]
    assert "write_inventory" in nunca
    assert not set(nunca) & set(REQUIRED_SCOPES)
    # Y el copy tampoco esconde lo que esta ruta NO puede dar.
    assert copy["lo_que_esto_no_puede_dar"] == PLAZO_AUSENTE


def test_el_copy_de_los_pasos_manda_a_pantallas_concretas_del_admin():
    """Un paso sin la pantalla donde se hace es una instruccion que no se sigue."""
    pasos = setup_copy()["pasos"]
    assert len(pasos) >= 4
    for p in pasos:
        assert p["titulo"] and p["donde"] and p["detalle"]
    texto = " ".join(p["donde"] + p["detalle"] for p in pasos)
    assert "Admin API access scopes" in texto
    assert "shpat_" in texto
    assert "myshopify.com" in texto


def test_el_mensaje_de_scope_faltante_explica_que_se_pierde_sin_cada_permiso():
    admin = InMemoryShopifyAdmin(_ordenes_estables(), scopes=("read_products", "read_locations"))
    check = check_connection(_creds(), http=admin)
    assert check.reason == SCOPE
    # No solo el handle: el para-que, que es lo que hace accionable el mensaje.
    assert SCOPE_COPY["read_orders"][:30] in check.message
    assert SCOPE_COPY["read_inventory"][:30] in check.message


# -- la lectura masiva (bulk operations) -------------------------------------------


def _bulk(admin, **kw):
    """Corre el camino masivo contra el doble, sin dormir de verdad."""
    return read_sales_history_bulk(
        admin, fetch_jsonl=lambda url: admin.jsonl(), sleep=lambda _s: None, **kw
    )


def test_los_dos_caminos_dan_EXACTAMENTE_la_misma_historia():
    """La razon de que compartan un solo agregador.

    Si cada camino bucketizara por su cuenta, la MISMA tienda daria dos
    clasificaciones segun por que puerta entro -- y ninguna de las dos se veria
    mal mirandola sola. Este test es lo unico que lo impide.
    """
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    catalogo = read_catalog(admin)
    paginado = read_sales_history(admin, catalog=catalogo)
    masivo, estado = _bulk(admin, catalog=catalogo)

    assert estado.ok
    assert masivo.rows == paginado.rows
    assert masivo.units_by_sku == paginado.units_by_sku
    assert masivo.last_sale_by_sku == paginado.last_sale_by_sku
    assert (masivo.n_orders, masivo.first_order, masivo.last_order, masivo.periods) == (
        paginado.n_orders, paginado.first_order, paginado.last_order, paginado.periods
    )
    # Y la clasificacion que sale de cada uno, que es lo que ve el cliente.
    assert _clasificar(masivo.rows) == _clasificar(paginado.rows)


def test_el_camino_masivo_descarta_lo_mismo_que_el_paginado():
    ordenes = _ordenes_estables() + [
        make_order("cancel", "2026-06-10T10:00:00Z", [("SKU-1", 500.0)], cancelled=True, unit_cost=6.0),
        make_order("prueba", "2026-06-11T10:00:00Z", [("SKU-1", 500.0)], test=True, unit_cost=6.0),
        make_order("sinsku", "2026-06-12T10:00:00Z", [("", 9.0)], unit_cost=6.0),
    ]
    admin = InMemoryShopifyAdmin(ordenes)
    catalogo = read_catalog(admin)
    paginado = read_sales_history(admin, catalog=catalogo)
    masivo, _ = _bulk(admin, catalog=catalogo)

    for clave in ("cancelada", "de_prueba", "sin_sku"):
        assert masivo.dropped[clave] == paginado.dropped[clave] == (1 if clave != "sin_sku" else 1)
    assert masivo.units_by_sku == paginado.units_by_sku


def test_la_lectura_masiva_no_puede_truncar_lineas_por_construccion():
    """La ventaja que no es de velocidad: sin paginacion no hay orden cortada.

    En el camino paginado una orden con mas lineas de las que entran en la pagina
    se reporta truncada y su demanda sale baja. La consulta masiva no lleva
    ``first``, asi que ese descarte deja de existir en vez de quedar reportado.
    """
    gorda = make_order("gorda", "2026-01-15T10:00:00Z", [("SKU-1", 10.0)],
                       unit_cost=6.0, lines_truncated=True)
    admin = InMemoryShopifyAdmin([gorda] + _ordenes_estables()[1:])
    paginado = read_sales_history(admin)
    masivo, _ = _bulk(admin)

    assert paginado.dropped["lineas_truncadas"] == 1
    assert masivo.dropped["lineas_truncadas"] == 0
    assert "first" not in bulk_orders_query()


def test_mientras_corre_no_devuelve_nada_y_entrega_el_id_para_volver():
    """Un resultado parcial presentado como final es indistinguible de uno completo."""
    admin = InMemoryShopifyAdmin(_ordenes_estables(), bulk_polls_running=99)
    historia, estado = read_sales_history_bulk(
        admin, fetch_jsonl=lambda url: admin.jsonl(), sleep=lambda _s: None,
        max_wait=6.0, poll_every=3.0,
    )
    assert historia is None
    assert estado.status == "RUNNING" and not estado.done
    assert estado.id
    # La espera fue acotada: no se quedo dando vueltas para siempre.
    assert admin.calls.count("bulkPoll") == 3


def test_volver_con_el_id_no_arranca_una_segunda_operacion():
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    historia, estado = _bulk(admin, bulk_id="gid://shopify/BulkOperation/1")
    assert historia is not None and estado.ok
    assert "bulkRun" not in admin.calls, "arrancar de nuevo pagaria dos veces la misma lectura"


def test_una_lectura_masiva_fallida_no_se_completa_con_datos_parciales():
    admin = InMemoryShopifyAdmin(_ordenes_estables(), bulk_status="FAILED", bulk_error_code="TIMEOUT")
    with pytest.raises(ShopifyConnectError) as exc:
        _bulk(admin)
    assert "TIMEOUT" in str(exc.value)
    assert "partialDataUrl" in str(exc.value)


def test_si_shopify_rechaza_el_arranque_se_dice_por_que():
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(),
        bulk_user_errors=[{"field": None, "message": "A bulk query operation is already running"}],
    )
    with pytest.raises(ShopifyConnectError) as exc:
        _bulk(admin)
    assert "already running" in str(exc.value)


def test_una_tienda_sin_ordenes_en_el_rango_no_es_una_falla():
    admin = InMemoryShopifyAdmin([])
    historia, estado = _bulk(admin)
    assert estado.ok
    assert historia is not None and historia.n_orders == 0 and historia.rows == ()


def test_una_linea_cuyo_padre_no_es_la_orden_en_curso_se_cuenta_huerfana():
    """Adjudicarla a la orden equivocada seria demanda cargada al mes que no fue."""
    admin = InMemoryShopifyAdmin(_ordenes_estables()[:1])
    lineas = admin.jsonl() + [
        '{"quantity": 99, "sku": "SKU-1", "__parentId": "gid://shopify/Order/inexistente"}'
    ]
    historia = read_sales_history_bulk(
        admin, fetch_jsonl=lambda url: lineas, sleep=lambda _s: None
    )[0]
    assert historia.dropped["linea_huerfana"] == 1
    assert historia.units_by_sku["SKU-1"] == 10.0  # sin las 99 de la huerfana


# -- la mutacion que NO escribe ------------------------------------------------------


def test_la_unica_mutacion_es_la_que_arranca_una_LECTURA():
    """El matiz que el camino masivo introduce, y que no se puede dejar implicito.

    `bulkOperationRunQuery` es una mutacion del esquema, asi que la afirmacion
    "esta ruta no ejecuta mutaciones" deja de ser cierta al pie de la letra. Lo
    que importa nunca fue esa frase: es que no se escriba en el sistema de
    registro del comerciante. Esta mutacion le entrega a Shopify una consulta de
    LECTURA para correrla del lado de ellos, y Shopify pide para eso solo los
    permisos de lectura de los datos consultados. Aqui se comprueba las dos
    mitades: que la unica mutacion sea esa, y que lo que lleva adentro no escriba.
    """
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    _bulk(admin)

    assert admin.calls.count("bulkRun") == 1
    adentro = admin.bulk_query
    assert adentro.startswith("{"), "la consulta interna tiene que ser una consulta, no una mutacion"
    for prohibido in ("mutation", "inventorySet", "inventoryAdjust", "Update", "Delete", "Create"):
        assert prohibido not in adentro, f"la lectura masiva lleva {prohibido!r} adentro"
    # Y no hace falta ningun scope de escritura para correrla.
    assert not any(s.startswith("write_") for s in REQUIRED_SCOPES)


def test_el_resultado_se_baja_SIN_el_token_de_shopify():
    """La url del resultado es de un tercero: mandarle el token seria regalarselo."""
    httpx = pytest.importorskip("httpx")
    with bare_client() as cliente:
        cabeceras = {k.lower() for k in cliente.headers}
        assert "x-shopify-access-token" not in cabeceras
        assert "authorization" not in cabeceras
    assert isinstance(cliente, httpx.Client)

    # Y el descargador solo recibe la url: no hay por donde colarle el token.
    import inspect

    assert list(inspect.signature(download_jsonl).parameters) == ["url"]


def test_la_url_del_resultado_no_es_del_dominio_de_la_tienda():
    """Si lo fuera, reusar el cliente autenticado seria inofensivo -- y no lo es."""
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    _bulk(admin)
    assert "myshopify.com" not in admin.bulk_url


# -- el techo de costo por consulta ------------------------------------------------

#: Shopify rechaza una consulta cuyo costo calculado pase de esto, ANTES de
#: ejecutarla. No es un limite de velocidad que se sortea esperando: es un no.
MAX_COSTO_POR_CONSULTA = 1_000

_LIBRES = {"edges", "pageInfo"}


def _campos(texto: str, i: int = 0):
    """El arbol de la consulta: (nombre, argumentos, hijos)."""
    import re

    salida = []
    while i < len(texto):
        if texto[i] == "}":
            return salida, i + 1
        m = re.match(r"\s*([A-Za-z_]\w*)\s*(\([^)]*\))?\s*", texto[i:])
        if not m:
            i += 1
            continue
        nombre, args = m.group(1), m.group(2) or ""
        i += m.end()
        hijos = []
        if i < len(texto) and texto[i] == "{":
            hijos, i = _campos(texto, i + 1)
        salida.append((nombre, args, hijos))
    return salida, i


def _costo(campos) -> int:
    """El costo calculado, con las reglas publicadas de Shopify.

    Objetos 1 punto, escalares 0, y una conexion cuesta ``first`` por el costo de
    cada nodo. ``edges`` y ``pageInfo`` no cuestan: son envoltorios.
    """
    import re

    total = 0
    for nombre, args, hijos in campos:
        if not hijos:
            continue  # escalar
        if nombre == "pageInfo":
            continue
        m = re.search(r"first:\s*(\d+)", args)
        interno = _costo(hijos)
        if m:
            total += int(m.group(1)) * max(1, interno)
        elif nombre in _LIBRES:
            total += interno
        else:
            total += 1 + interno
    return total


def _costo_de(query: str) -> int:
    cuerpo = query[query.index("{") + 1:]
    return _costo(_campos(cuerpo)[0])


@pytest.mark.parametrize(
    "nombre, query",
    [
        ("catalogo", CATALOG_QUERY),
        ("ordenes", ORDERS_QUERY),
        ("stock", STOCK_QUERY),
        ("tienda", SHOP_QUERY),
        ("scopes", SCOPES_QUERY),
    ],
)
def test_ninguna_consulta_pasa_el_techo_de_costo_de_shopify(nombre, query):
    """El defecto que este test existe para atajar no se ve offline.

    El doble no cobra puntos, asi que una consulta cien veces por encima del
    techo pasa toda la bateria en verde y falla en TODA tienda real -- con un
    rechazo previo a la ejecucion, no con un resultado raro. Paso: la consulta de
    ordenes pedia 100 ordenes x 250 lineas bajando a unitCost en cada una,
    ~100.100 puntos.
    """
    costo = _costo_de(query)
    assert 0 < costo <= MAX_COSTO_POR_CONSULTA, f"{nombre} cuesta {costo}"


def test_el_calculador_de_costo_detecta_la_consulta_que_fallaba():
    """El oraculo del test de arriba: sobre la consulta vieja tiene que dar rojo."""
    vieja = """
    query SalesHistory($q: String!, $cursor: String) {
      orders(first: 100, after: $cursor, query: $q, sortKey: CREATED_AT) {
        pageInfo { hasNextPage endCursor }
        edges { node { id createdAt cancelledAt test
          lineItems(first: 250) {
            pageInfo { hasNextPage }
            edges { node { quantity sku
              variant { inventoryItem { unitCost { amount } } } } }
          } } }
      }
    }
    """
    assert _costo_de(vieja) > MAX_COSTO_POR_CONSULTA * 50
    # Y la de ahora, la misma lectura, entra con margen.
    assert _costo_de(ORDERS_QUERY) < MAX_COSTO_POR_CONSULTA


# -- el costo de la lectura de stock ----------------------------------------------


def _catalogo_grande(n_skus: int, n_bodegas: int, unidades: float = 10.0):
    """Una tienda de ``n_skus`` x ``n_bodegas``, para medir cuanto cuesta leerla."""
    skus = [f"SKU-{i:04d}" for i in range(n_skus)]
    locations = [
        {"id": f"gid://shopify/Location/{b}", "name": f"Bodega {b}", "isPrimary": b == 0}
        for b in range(n_bodegas)
    ]
    inv = InMemoryShopifyInventory(
        stock={s: {loc["id"]: unidades for loc in locations} for s in skus},
        locations=locations,
    )
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), inventory=inv, catalog=[(s, 6.0) for s in skus]
    )
    return admin, inv, skus


def test_el_costo_de_leer_stock_no_crece_con_el_numero_de_bodegas_por_sku():
    """La razon de existir del lote: antes eran N*M viajes, uno por (SKU, bodega).

    Con `get_inventory_level` resolviendo una location por llamada, pasar de una
    bodega a tres TRIPLICABA los pedidos. Contra el bucket de puntos de Shopify
    eso no es lento: es inviable, y el presupuesto son 25 minutos por cuenta al
    mes.
    """
    una, _, skus = _catalogo_grande(40, 1)
    tres, _, _ = _catalogo_grande(40, 3)

    r_una = read_stock_on_hand(una, una._inventory.list_locations(), skus=skus)
    r_tres = read_stock_on_hand(tres, tres._inventory.list_locations(), skus=skus)

    # Una pagina por bodega, y nada mas: el numero de pedidos sigue al numero de
    # bodegas (3), NO al producto SKU x bodega (120).
    assert r_una.n_requests == 1
    assert r_tres.n_requests == 3
    assert r_tres.n_requests < 40 * 3
    # Y la grilla sale completa igual.
    assert len(r_tres.rows) == 40 * 3


def test_el_costo_de_leer_stock_no_crece_con_el_numero_de_sku_dentro_de_una_pagina():
    """Diez SKU o doscientos: un solo pedido mientras entren en la pagina."""
    chico, _, skus_chico = _catalogo_grande(10, 2)
    grande, _, skus_grande = _catalogo_grande(200, 2)

    r_chico = read_stock_on_hand(chico, chico._inventory.list_locations(), skus=skus_chico)
    r_grande = read_stock_on_hand(grande, grande._inventory.list_locations(), skus=skus_grande)

    assert r_chico.n_requests == r_grande.n_requests == 2
    assert r_grande.n_levels == 400


def test_cuando_no_entran_en_una_pagina_se_pagina_por_cursor_y_no_se_pierde_nada():
    """El tope por pagina no puede convertirse en un tope de resultados."""
    admin, _, skus = _catalogo_grande(10, 1)
    admin._stock_page_size = 3  # fuerza cuatro paginas

    r = read_stock_on_hand(admin, admin._inventory.list_locations(), skus=skus)
    assert r.n_requests == 4
    assert r.n_levels == 10
    assert len(r.rows) == 10
    assert all(f["on_hand"] == 10.0 for f in r.rows)


def test_el_stock_por_lote_da_exactamente_lo_mismo_que_dice_el_inventario():
    """El oraculo: las filas se comparan contra el estado del doble, no contra otra lectura.

    Compararlas contra la implementacion vieja probaria que las dos coinciden y
    no que alguna acierta. Aqui se arma la respuesta esperada desde el inventario
    mismo, SKU por SKU y bodega por bodega.
    """
    inv = _inventario(sku1=1000.0, sku2=50.0, dos_bodegas=True)
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), inventory=inv, catalog=[("SKU-1", 6.0), ("SKU-2", 6.0)]
    )
    r = read_stock_on_hand(admin, inv.list_locations(), skus=["SKU-1", "SKU-2"])

    esperado = {
        (sku, loc["id"]): float(
            inv.get_inventory_level(inv.find_inventory_item_by_sku(sku)["inventory_item_id"], loc["id"])
        )
        for sku in ("SKU-1", "SKU-2")
        for loc in inv.list_locations()
    }
    obtenido = {(f["product_id"], f["location_id"]): f["on_hand"] for f in r.rows}
    assert obtenido == esperado
    # Incluida la bodega donde el SKU tiene cero: la fila existe y dice cero.
    assert esperado[("SKU-1", LOC_B)] == 40.0


def test_un_sku_sin_seguimiento_de_cantidad_no_entra_como_stock_cero():
    """Shopify no lleva su cuenta: un cero ahi seria una existencia inventada.

    Y el motor de E&O lee `daily_demand` bajo con on_hand 0 como stock muerto, o
    sea que un SKU sin seguimiento aparecerria en la lista de liquidacion.
    """
    inv = InMemoryShopifyInventory(
        stock={"SKU-1": {LOC_A: 100.0}, "SKU-SIN": {LOC_A: 0.0}},
        locations=[{"id": LOC_A, "name": "Santiago", "isPrimary": True}],
    )
    original = inv.find_inventory_item_by_sku

    def _sin_seguimiento(sku):
        item = original(sku)
        if item and sku == "SKU-SIN":
            return {**item, "tracked": False}
        return item

    inv.find_inventory_item_by_sku = _sin_seguimiento
    admin = InMemoryShopifyAdmin(
        _ordenes_estables(), inventory=inv, catalog=[("SKU-1", 6.0), ("SKU-SIN", 6.0)]
    )
    r = read_stock_on_hand(admin, inv.list_locations())

    assert {f["product_id"] for f in r.rows} == {"SKU-1"}
    assert r.dropped["no_rastreado"] == 1

    historial = read_sales_history(admin, catalog=read_catalog(admin))
    pre = preflight(historial, r)
    assert any("sin seguimiento de" in w for w in pre.warnings)


def test_una_lectura_de_stock_truncada_bloquea_en_vez_de_reportar_medio_inventario():
    admin, _, skus = _catalogo_grande(10, 1)
    admin._stock_page_size = 3
    r = read_stock_on_hand(admin, admin._inventory.list_locations(), skus=skus, max_levels=3)

    assert r.dropped["niveles_truncados"] == 1
    historial = read_sales_history(admin, catalog=read_catalog(admin))
    pre = preflight(historial, r)
    assert not pre.ok
    assert any("lectura de stock se corto" in m for m in pre.missing)


def test_una_bodega_vacia_sigue_contando_como_bodega():
    """Contarlas solo cuando tienen stock esconde justo el lugar donde no hay nada."""
    inv = InMemoryShopifyInventory(
        stock={"SKU-1": {LOC_A: 100.0}},
        locations=[
            {"id": LOC_A, "name": "Santiago", "isPrimary": True},
            {"id": LOC_B, "name": "Concepcion", "isPrimary": False},
        ],
    )
    admin = InMemoryShopifyAdmin(_ordenes_estables(), inventory=inv, catalog=[("SKU-1", 6.0)])
    r = read_stock_on_hand(admin, inv.list_locations(), skus=["SKU-1"])
    historial = read_sales_history(admin, catalog=read_catalog(admin))

    assert preflight(historial, r).n_locations == 2
    assert {f["location_name"] for f in r.rows} == {"Santiago", "Concepcion"}


# -- la dimension location --------------------------------------------------------


def test_el_stock_conserva_la_location_porque_sin_ella_no_hay_pooling():
    inv = _inventario(sku1=1000.0, dos_bodegas=True)
    stock = _stock(inv, ["SKU-1", "SKU-2"]).rows
    por_sku_loc = {(f["product_id"], f["location_id"]): f["on_hand"] for f in stock}
    assert por_sku_loc[("SKU-1", LOC_A)] == 1000.0
    assert por_sku_loc[("SKU-1", LOC_B)] == 40.0
    assert {f["location_name"] for f in stock} == {"Santiago", "Concepcion"}


def test_el_pliegue_por_sku_suma_las_bodegas_y_es_el_unico_lugar_donde_se_pierde_la_location():
    inv = _inventario(sku1=1000.0, dos_bodegas=True)
    stock = _stock(inv, ["SKU-1", "SKU-2"]).rows
    assert fold_locations(stock)["SKU-1"] == 1040.0
    # El pliegue no toca las filas originales: la historia de pooling sobrevive.
    assert len([f for f in stock if f["product_id"] == "SKU-1"]) == 2


def test_dos_bodegas_se_nombran_y_se_declara_que_la_demanda_no_es_atribuible():
    scan = scan_input(
        _creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()), _inventario(dos_bodegas=True))
    )
    assert scan.preflight.n_locations == 2
    assert any("locations" in w and "stock TOTAL" in w for w in scan.preflight.warnings)


# -- paginacion por cursor ---------------------------------------------------------


def test_la_paginacion_por_cursor_lee_TODAS_las_paginas_y_no_solo_la_primera():
    ordenes = _ordenes_estables()  # 12 ordenes
    admin = InMemoryShopifyAdmin(ordenes, page_size=2)
    historial = read_sales_history(admin)
    assert historial.n_orders == 12
    # Seis paginas de dos, no una: si el cursor no avanzara, esto seria 1.
    assert admin.calls.count("orders") == 6
    assert historial.units_by_sku["SKU-1"] == 120.0


def test_leer_de_a_una_pagina_o_de_a_muchas_da_exactamente_lo_mismo():
    ordenes = _ordenes_estables()
    a = read_sales_history(InMemoryShopifyAdmin(ordenes, page_size=1))
    b = read_sales_history(InMemoryShopifyAdmin(ordenes, page_size=100))
    assert a.rows == b.rows
    assert a.units_by_sku == b.units_by_sku


# -- lo que se descarta, y por que -------------------------------------------------


def test_una_orden_cancelada_no_cuenta_como_demanda():
    base = _ordenes_estables()
    con_cancelada = base + [
        make_order("cancelada", "2026-06-10T10:00:00Z", [("SKU-1", 500.0)], cancelled=True, unit_cost=6.0)
    ]
    limpio = read_sales_history(InMemoryShopifyAdmin(base))
    sucio = read_sales_history(InMemoryShopifyAdmin(con_cancelada))
    assert sucio.units_by_sku["SKU-1"] == limpio.units_by_sku["SKU-1"]
    assert sucio.dropped["cancelada"] == 1


def test_una_orden_de_prueba_no_cuenta_como_demanda():
    base = _ordenes_estables()
    con_prueba = base + [
        make_order("prueba", "2026-06-10T10:00:00Z", [("SKU-1", 500.0)], test=True, unit_cost=6.0)
    ]
    assert (
        read_sales_history(InMemoryShopifyAdmin(con_prueba)).units_by_sku["SKU-1"]
        == read_sales_history(InMemoryShopifyAdmin(base)).units_by_sku["SKU-1"]
    )


def test_una_orden_con_lineas_truncadas_se_cuenta_y_se_nombra_en_vez_de_pasar_callada():
    ordenes = _ordenes_estables()
    ordenes[0] = make_order(
        "gorda", "2026-01-15T10:00:00Z", [("SKU-1", 10.0)], unit_cost=6.0, lines_truncated=True
    )
    historial = read_sales_history(InMemoryShopifyAdmin(ordenes))
    assert historial.dropped["lineas_truncadas"] == 1
    pre = preflight(historial, _stock(_inventario(), ["SKU-1", "SKU-2"]))
    assert any("250 lineas" in w for w in pre.warnings)


def test_una_linea_sin_sku_no_se_inventa_un_identificador():
    ordenes = [make_order("x", "2026-01-15T10:00:00Z", [("", 5.0), ("SKU-1", 3.0)], unit_cost=6.0)]
    historial = read_sales_history(InMemoryShopifyAdmin(ordenes))
    assert historial.dropped["sin_sku"] == 1
    assert set(historial.units_by_sku) == {"SKU-1"}


# -- el costo: declarar la base, nunca rellenarla ----------------------------------


def test_sin_ningun_costo_el_abc_se_declara_por_unidades_y_la_pagina_lo_dice():
    historial = read_sales_history(InMemoryShopifyAdmin(_ordenes_estables(costo=None)))
    assert historial.cost_basis == COSTO_UNIDADES

    df = pd.DataFrame(list(historial.rows))
    reporte = abc_xyz_job.run(
        abc_xyz_job.aggregate_skus(df, product_col="product_id", demand_col="quantity", cost_col="unit_cost")
    )
    # El motor lo detecta solo y lo imprime: el lector se entera por la pagina,
    # no por una nota al pie del conector.
    assert not reporte.value_basis.discriminates
    deck = abc_xyz_job.build_deck(reporte)
    assert any("Ranked on units" in f.title for f in deck.findings)


def test_el_costo_sale_del_catalogo_y_de_ningun_otro_lado():
    """La linea de venta ya no trae costo, y es a proposito.

    Bajar a ``variant -> inventoryItem -> unitCost`` en cada linea multiplicaba
    el costo calculado de la consulta de ordenes hasta cien veces el techo de
    1.000 puntos de Shopify. Y era ademas redundante: el catalogo ya trae el
    costo, y lo trae TAMBIEN para lo que no vendio -- que es justo lo que el
    analisis de excedente necesita valorizar.
    """
    admin = InMemoryShopifyAdmin(_ordenes_estables(costo=6.0))
    sin_catalogo = read_sales_history(admin)
    assert sin_catalogo.cost_basis == COSTO_UNIDADES
    assert sin_catalogo.cost_by_sku == {}

    con_catalogo = read_sales_history(admin, catalog=read_catalog(admin))
    assert con_catalogo.cost_basis == COSTO_REAL
    assert con_catalogo.cost_by_sku["SKU-1"] == 6.0


def test_el_costo_MIXTO_se_bloquea_porque_un_C_por_falta_de_dato_no_es_un_C_medido():
    ordenes = [
        make_order("a", "2026-01-15T10:00:00Z", [("SKU-1", 10.0)], unit_cost=6.0),
        make_order("b", "2026-02-15T10:00:00Z", [("SKU-2", 10.0)], unit_cost=None),
        make_order("c", "2026-03-15T10:00:00Z", [("SKU-1", 10.0)], unit_cost=6.0),
    ]
    admin = InMemoryShopifyAdmin(ordenes)
    historial = read_sales_history(admin, catalog=read_catalog(admin))
    pre = preflight(historial, _stock(_inventario(), ["SKU-1", "SKU-2"]))
    assert not pre.ok
    assert any("no tienen costo unitario" in m for m in pre.missing)
    # Y la ruta completa no entrega filas: bloquear y devolver datos igual seria
    # bloquear en el papel.
    scan = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(ordenes), _inventario()))
    assert scan.demand_rows == () and scan.eo_rows == ()


# -- el preflight ------------------------------------------------------------------


def test_el_preflight_cuenta_ordenes_skus_locations_y_rango_de_fechas():
    scan = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()), _inventario()))
    pre = scan.preflight
    assert pre.n_orders == 12
    assert pre.n_skus == 2
    assert pre.n_locations == 1
    assert pre.first_order == "2026-01-15"
    assert pre.last_order == "2026-12-15"
    assert pre.n_periods == 12


def test_lo_que_el_preflight_reporta_cambia_cuando_cambian_los_datos():
    """Un preflight que informa lo mismo pase lo que pase no informa nada."""
    largo = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()), _inventario())).preflight
    cortas = _ordenes_estables()[:3]
    corto = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(cortas), _inventario())).preflight

    assert (largo.n_orders, largo.n_periods, largo.last_order) != (
        corto.n_orders, corto.n_periods, corto.last_order
    )
    assert corto.n_orders == 3 and corto.n_periods == 3
    assert largo.span_days > corto.span_days


def test_el_preflight_no_hace_una_sola_llamada_extra():
    """PURO sobre lo ya leido: el presupuesto son 25 minutos por cuenta al mes."""
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    inv = _inventario()
    historial = read_sales_history(admin)
    stock = _stock(inv, ["SKU-1", "SKU-2"], admin=admin)
    antes = len(admin.calls)

    preflight(historial, stock)
    preflight(historial, stock, required_days=999)
    assert len(admin.calls) == antes


def test_un_historial_mas_corto_que_la_ventana_pedida_se_rechaza_nombrando_lo_que_falta():
    admin = InMemoryShopifyAdmin(_ordenes_estables()[:2])  # ~31 dias
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()), required_days=365)
    assert not scan.ok
    faltante = " ".join(scan.preflight.missing)
    assert "365" in faltante
    assert str(scan.preflight.span_days) in faltante
    # No se rellena: sin filas de salida no hay forma de correr igual "con lo que haya".
    assert scan.demand_rows == ()


def test_la_ventana_corta_nombra_read_all_orders_porque_shopify_la_recorta_en_silencio():
    """Shopify devuelve 200 y una lista corta, no un error: el sintoma es el silencio."""
    sin_permiso = scan_input(
        _creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()[:2]), _inventario()), required_days=365
    )
    assert ALL_ORDERS_SCOPE in " ".join(sin_permiso.preflight.missing)

    con_permiso = scan_input(
        _creds(),
        http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()[:2], scopes=REQUIRED_SCOPES + (ALL_ORDERS_SCOPE,)), _inventario()),
        required_days=365,
    )
    # Sigue bloqueado -- la ventana es corta igual -- pero ya no culpa al permiso:
    # mandar a marcar un permiso que ya esta marcado es la vuelta perdida.
    assert not con_permiso.ok
    assert ALL_ORDERS_SCOPE not in " ".join(con_permiso.preflight.missing)


def test_pocos_periodos_se_rechazan_porque_un_cv_sobre_dos_meses_no_distingue_nada():
    scan = scan_input(
        _creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()[:2]), _inventario()), min_periods=6
    )
    assert not scan.ok
    assert any("periodo(s) mensual(es)" in m for m in scan.preflight.missing)


def test_sin_ordenes_se_nombra_la_causa_dominante_y_no_un_generico():
    solo_canceladas = [
        make_order("c1", "2026-01-15T10:00:00Z", [("SKU-1", 5.0)], cancelled=True, unit_cost=6.0)
    ]
    pre_cancel = preflight(read_sales_history(InMemoryShopifyAdmin(solo_canceladas)), StockRead())
    pre_vacio = preflight(read_sales_history(InMemoryShopifyAdmin([])), StockRead())

    assert not pre_cancel.ok and not pre_vacio.ok
    assert "canceladas" in " ".join(pre_cancel.missing)
    assert "no hay ninguna orden de venta" in " ".join(pre_vacio.missing)
    # Dos causas distintas, dos remedios distintos: un unico "no se encontraron
    # datos" los manda a los dos lados a la vez.
    assert pre_cancel.missing != pre_vacio.missing


def test_una_conexion_fallida_no_devuelve_filas_ni_revienta():
    scan = scan_input(_creds(), http=_tienda(InMemoryShopifyAdmin(status=401), _inventario()))
    assert not scan.ok
    assert scan.preflight is None
    assert scan.demand_rows == () and scan.stock_rows == () and scan.eo_rows == ()
    assert scan.check.actionable


def test_un_rechazo_al_leer_ordenes_se_convierte_en_bloqueante_y_no_en_excepcion():
    """El scope alcanza para conectar pero Shopify niega `orders` igual."""
    admin = InMemoryShopifyAdmin(_ordenes_estables(), scopes=REQUIRED_SCOPES)
    admin._scopes = REQUIRED_SCOPES  # pasa el check...
    scan = scan_input(_creds(), http=_tienda(admin, _inventario()))
    assert scan.ok  # caso control: sin negacion, corre

    negado = InMemoryShopifyAdmin(_ordenes_estables(), scopes=("read_products", "read_inventory", "read_locations", "read_orders"))
    negado._scopes = ("read_products", "read_inventory", "read_locations")  # el check ya paso
    scan2 = scan_input(_creds(), http=_tienda(negado, _inventario()))
    assert not scan2.ok


# -- la ruta completa --------------------------------------------------------------


def test_la_ruta_completa_de_dos_campos_a_las_dos_tareas():
    """Dominio + token -> T03 clasificada y T11 valorizada, sin tocar un CSV."""
    scan = scan_input(
        _creds(),
        http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()), _inventario(sku1=1000.0, dos_bodegas=True)),
        as_of="2026-12-31",
    )
    assert scan.ok

    celdas = _clasificar(scan.demand_rows)
    assert set(celdas) == {"SKU-1", "SKU-2"}
    assert all(len(c) == 2 for c in celdas.values())

    reporte = excess_obsolete_job.run(
        excess_obsolete_job.prepare_records(pd.DataFrame(list(scan.eo_rows)))
    )
    assert reporte.eo_value > 0
    assert not excess_obsolete_job.verify(reporte)

    # Y la location sobrevivio para la historia de pooling.
    assert {f["location_name"] for f in scan.stock_rows} == {"Santiago", "Concepcion"}


def test_days_since_last_sale_sale_de_la_ultima_venta_observada_y_no_de_hoy():
    scan = scan_input(
        _creds(), http=_tienda(InMemoryShopifyAdmin(_ordenes_estables()), _inventario()), as_of="2026-12-31"
    )
    fila = {f["product_id"]: f for f in scan.eo_rows}["SKU-1"]
    assert fila["days_since_last_sale"] == 16.0  # 2026-12-15 -> 2026-12-31


# -- normalizacion del dominio ------------------------------------------------------


@pytest.mark.parametrize(
    "pegado",
    [
        "mitienda.myshopify.com",
        "  mitienda.myshopify.com  ",
        "https://mitienda.myshopify.com",
        "https://mitienda.myshopify.com/",
        "mitienda",
        "https://admin.shopify.com/store/mitienda",
        "https://admin.shopify.com/store/mitienda/apps",
        "admin.shopify.com/store/mitienda/settings",
        "<https://mitienda.myshopify.com>",
    ],
)
def test_las_formas_en_que_un_comerciante_pega_su_tienda_dan_el_mismo_dominio(pegado):
    assert normalize_shop_domain(pegado) == "mitienda.myshopify.com"


def test_un_dominio_publico_no_se_traduce_a_myshopify_adivinando():
    """Acertar a veces es peor que fallar: seria leer la tienda equivocada."""
    assert normalize_shop_domain("www.mitienda.cl") == "www.mitienda.cl"


def test_los_campos_vacios_se_nombran_uno_por_uno():
    admin = InMemoryShopifyAdmin(_ordenes_estables())
    sin_dominio = check_connection(ShopifyCredentials("", TOKEN_CLIENTE), http=admin)
    sin_token = check_connection(ShopifyCredentials(DOMINIO_OK, ""), http=admin)
    assert sin_dominio.reason == DOMINIO and "dominio" in sin_dominio.message
    assert sin_token.reason == TOKEN and "token" in sin_token.message
    assert sin_dominio.message != sin_token.message
