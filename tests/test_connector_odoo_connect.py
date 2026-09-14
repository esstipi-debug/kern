"""Onboarding autoservicio de Odoo Online: validar, leer, y NO medir de mas.

Odoo Online (el SaaS de odoo.com) no acepta modulos de terceros, asi que
`odoo_addon/linchpin_dry_run` no es una opcion ahi. La unica puerta es la
External API, y lo que faltaba era el tramo de cuatro campos hasta el mismo motor
de la salida #1 que ya consume un CSV.

**La regla del gate endurecido.** Que un campo exista no acredita nada. Este
proyecto ya pago 17,5 puntos porcentuales por comprobaciones que acreditaban por
presencia de nombre de campo: `assert "lead_time_days" in fila` pasa igual de
verde sobre un numero correcto que sobre una constante. Cada test de aqui abajo
que afirma que algo se MIDE lo demuestra corriendo DOS insumos distintos y
comprobando que el numero de salida se movio, y en el sentido correcto. Los que
afirman que algo se CLASIFICA producen los cuatro modos y comprueban que salen
cuatro respuestas distintas, no cuatro veces la misma.
"""
from __future__ import annotations

import xmlrpc.client

import pandas as pd
import pytest

from src.connectors.odoo import InMemoryOdoo
from src.connectors.odoo_connect import (
    CREDENTIALS,
    DATABASE,
    FOUR_MODES,
    MODULE,
    PERMISSIONS,
    REDACTED,
    SERVER,
    UNREACHABLE,
    InMemoryOdooServer,
    OdooCredentials,
    check_connection,
    normalize_url,
    preflight,
    read_receipts,
    scan_input,
)
from webapp.demo_lead_time_scan import scan_from_deliveries, to_deliveries

CLAVE = "0f1e2d3c-clave-de-api-del-cliente"


def _odoo(dias: int = 20, declarado: float = 7.0, n: int = 3, mes_extra: bool = False) -> InMemoryOdoo:
    """Un Odoo con un proveedor que promete ``declarado`` y entrega en ``dias``.

    Parametrizado en el plazo OBSERVADO a proposito: es lo que permite correr dos
    instancias distintas y exigir que la brecha reportada se mueva.
    """
    data: dict = {
        "product.product": {
            1: {"default_code": "SKU-1", "name": "Rodamiento", "list_price": 10.0, "standard_price": 6.0},
        },
        "res.partner": {
            70: {"name": "Acme Sucursal Santiago", "commercial_partner_id": [69, "Acme"]},
            69: {"name": "Acme", "commercial_partner_id": [69, "Acme"]},
        },
        "stock.picking.type": {10: {"code": "incoming"}, 11: {"code": "outgoing"}},
        "product.supplierinfo": {
            400: {"partner_id": [70, "Acme Sucursal Santiago"], "product_id": [1, "Rodamiento"],
                  "delay": declarado, "sequence": 1},
        },
        "purchase.order": {},
        "purchase.order.line": {},
        # Un despacho a cliente, siempre presente y siempre fuera del resultado.
        # No es decorado: `InMemoryOdoo.fields_get` deriva que campos EXISTEN de
        # los registros que hay, asi que un `stock.move` vacio se disfrazaria de
        # "Compras no instalado" y el caso "instalado pero sin recepciones" -- el
        # que importa para la regla de las fechas -- no se podria montar.
        "stock.move": {
            999: {"state": "done", "date": "2026-01-02 09:00:00", "product_id": [1, "Rodamiento"],
                  "purchase_line_id": False, "picking_id": [999, "WH/OUT/1"],
                  "picking_type_id": [11, "Entregas"]},
        },
    }
    base = pd.Timestamp("2026-01-05")
    for i in range(n):
        po, pol = 200 + i, 300 + i
        orden = base + pd.Timedelta(days=28 * i)
        recepcion = orden + pd.Timedelta(days=dias)
        data["purchase.order"][po] = {
            "name": f"PO-{i:03d}", "date_order": f"{orden.date()} 09:00:00",
            "date_approve": f"{orden.date()} 11:00:00",
            "partner_id": [70, "Acme Sucursal Santiago"], "state": "purchase",
        }
        data["purchase.order.line"][pol] = {"order_id": [po, f"PO-{i:03d}"]}
        data["stock.move"][400 + i] = {
            "state": "done", "date": f"{recepcion.date()} 09:00:00", "product_id": [1, "Rodamiento"],
            "purchase_line_id": [pol, "linea"], "picking_id": [500 + i, f"WH/IN/{i}"],
            "picking_type_id": [10, "Recepciones"],
        }
    if mes_extra:
        # Una recepcion mucho mas tarde: mueve el RANGO de fechas del preflight.
        data["purchase.order"][299] = {
            "name": "PO-299", "date_order": "2026-09-01 09:00:00",
            "date_approve": "2026-09-01 11:00:00",
            "partner_id": [70, "Acme Sucursal Santiago"], "state": "purchase",
        }
        data["purchase.order.line"][399] = {"order_id": [299, "PO-299"]}
        data["stock.move"][499] = {
            "state": "done", "date": "2026-09-21 09:00:00", "product_id": [1, "Rodamiento"],
            "purchase_line_id": [399, "linea"], "picking_id": [599, "WH/IN/9"],
            "picking_type_id": [10, "Recepciones"],
        }
    return InMemoryOdoo(data)


def _creds(**kw) -> OdooCredentials:
    base = {"url": "https://miempresa.odoo.com", "db": "prod",
            "login": "ops@cliente.cl", "api_key": CLAVE}
    return OdooCredentials(**{**base, **kw})


def _server(orm=None, **kw) -> InMemoryOdooServer:
    return InMemoryOdooServer(orm or _odoo(), api_key=CLAVE, **kw)


def _check(srv, creds=None):
    return check_connection(creds or _creds(), common=srv, models=srv)


# =============================================================================
# 1. Los cuatro modos de falla: cuatro respuestas DISTINTAS, no cuatro genericas
# =============================================================================


def test_host_inalcanzable_se_nombra_y_no_se_confunde_con_credencial():
    srv = _server(unreachable=OSError("[Errno 11001] getaddrinfo failed"))
    check = _check(srv)

    assert not check.ok
    assert check.reason == UNREACHABLE
    assert "miempresa.odoo.com" in check.message


def test_base_inexistente_se_nombra_y_sugiere_la_del_subdominio():
    """En Odoo Online la base casi siempre es el subdominio. La sugerencia es lo
    que convierte un rechazo en un paso siguiente."""
    check = _check(_server(db="prod"), _creds(db="produccion"))

    assert check.reason == DATABASE
    assert "produccion" in check.message
    assert "miempresa" in check.message  # la sugerencia derivada del subdominio


def test_la_sugerencia_de_base_no_se_aplica_sola():
    """Adivinar la base y conectar a la equivocada es peor que fallar: el
    escaneo saldria correcto sobre los datos de otra empresa."""
    check = _check(_server(db="miempresa"), _creds(db="produccion"))
    assert not check.ok  # aunque la base sugerida SI existe en ese servidor


def test_credencial_invalida_cuando_la_base_si_existe():
    check = _check(_server(), _creds(api_key="clave-vieja"))

    assert check.reason == CREDENTIALS
    assert "Claves de API" in check.message  # donde se genera, no solo que fallo


def test_un_acceso_denegado_que_nombra_la_base_no_se_lee_como_base_inexistente():
    """El traceback de Odoo arrastra la linea `odoo.registry(db)`, asi que el
    fault de una credencial mala puede nombrar la base de paso. Clasificarlo por
    ahi pone al cliente a cambiar el nombre de su base teniendo mal la clave."""
    fault = (
        "Traceback: registry = odoo.registry(database) ... "
        "odoo.exceptions.AccessDenied: Access denied"
    )
    check = _check(_server(auth_fault=fault))

    assert check.reason == CREDENTIALS
    assert check.reason != DATABASE


def test_permisos_insuficientes_nombra_TODOS_los_modelos_que_faltan():
    """De a uno obligaria al cliente a volver donde su administrador N veces."""
    srv = _server(denied=("purchase.order", "purchase.order.line"))
    check = _check(srv)

    assert check.reason == PERMISSIONS
    assert set(check.models) == {"purchase.order", "purchase.order.line"}
    assert "purchase.order" in check.message


def test_una_app_sin_instalar_no_se_diagnostica_como_permisos():
    """EL CASO QUE OBLIGA A UN QUINTO MOTIVO.

    Sin Compras instalada, el modelo no existe. Diagnosticarlo como permisos
    manda al cliente a Ajustes -> Usuarios, donde no hay nada que arreglar: el
    remedio esta en Aplicaciones -> instalar Compras.
    """
    sin_compras = _odoo()
    instalados = tuple(m for m in sin_compras._data if not m.startswith("purchase."))
    check = _check(_server(sin_compras, installed=instalados))

    assert check.reason == MODULE
    assert check.reason != PERMISSIONS
    assert "Aplicaciones" in check.message


def test_una_cuenta_de_portal_se_nombra_como_lo_que_es():
    """No lee ni `ir.model`: no es un usuario interno. Probar modelo por modelo
    daria la lista entera denegada y un mensaje que no dice lo importante."""
    check = _check(_server(denied=("ir.model",)))

    assert check.reason == PERMISSIONS
    assert "interno" in check.message


def test_los_cuatro_modos_producen_cuatro_diagnosticos_distintos():
    """EL TEST QUE HACE QUE LOS CUATRO SIRVAN.

    Cada uno por separado puede pasar con un clasificador que devuelva siempre lo
    mismo si el assert mira solo `not ok`. Lo que acredita el requisito es que
    cuatro insumos distintos produzcan cuatro salidas distintas -- y cuatro
    mensajes distintos, porque el motivo sin mensaje no mueve a nadie.
    """
    casos = {
        UNREACHABLE: (_server(unreachable=OSError("sin ruta")), _creds()),
        DATABASE: (_server(db="prod"), _creds(db="otra")),
        CREDENTIALS: (_server(), _creds(api_key="mala")),
        PERMISSIONS: (_server(denied=("stock.move",)), _creds()),
    }
    obtenidos = {esperado: _check(srv, cr) for esperado, (srv, cr) in casos.items()}

    assert [c.reason for c in obtenidos.values()] == list(casos)
    assert set(casos) == set(FOUR_MODES)
    assert len({c.message for c in obtenidos.values()}) == 4
    assert all(c.actionable for c in obtenidos.values())


def test_un_fault_que_no_se_reconoce_no_se_fuerza_dentro_de_los_cuatro():
    """Un diagnostico inventado es peor que uno que admite no saber."""
    check = _check(_server(auth_fault="odoo.exceptions.UserError: mantenimiento programado"))

    assert check.reason == SERVER
    assert check.reason not in FOUR_MODES
    assert "mantenimiento programado" in check.message


def test_lo_que_contesta_pero_no_es_odoo_es_inalcanzable_y_no_credencial():
    srv = _server(unreachable=xmlrpc.client.ProtocolError("x", 404, "Not Found", {}))
    check = _check(srv)

    assert check.reason == UNREACHABLE
    assert "no" in check.message.lower()


def test_un_corte_de_red_durante_el_sondeo_no_se_disfraza_de_permisos():
    """El sondeo va contra el proxy pelado, sin el reintento de `OdooClient`.

    Sin atrapar el transporte ahi, un blip de red llegaba al endpoint como un
    500; y clasificarlo como permisos manda al cliente a pelear con su
    administrador por algo que no esta en Odoo.
    """
    srv = _server(transport_on=lambda metodo, modelo: modelo == "purchase.order")
    check = _check(srv)

    assert check.reason == UNREACHABLE
    assert check.reason != PERMISSIONS
    assert "se corto" in check.message


def test_un_corte_a_mitad_de_la_lectura_no_se_reporta_como_dato_faltante():
    """`OdooTransportError` y no `OdooError` a secas: la diferencia es a donde
    se manda al cliente. Dato faltante lo manda a revisar sus recepciones; esto
    es la red, y ahi no hay nada que revisar."""
    srv = _server(transport_on=lambda metodo, modelo: metodo == "fields_get")
    entrada = scan_input(_creds(), common=srv, models=srv)

    assert not entrada.ok
    assert entrada.check.reason == UNREACHABLE
    assert entrada.preflight is None      # no se inventa un preflight vacio
    assert entrada.grouped == {}


def test_el_doble_exige_la_firma_real_de_authenticate():
    """El cuarto argumento (`user_agent_env`) no es decorado: Odoo falla sin el.
    Un doble que lo acepte y no lo mire deja pasar una llamada que solo se rompe
    contra el Odoo del cliente, donde no hay nadie para leer el error."""
    srv = _server()
    with pytest.raises(xmlrpc.client.Fault, match="user_agent_env"):
        srv.authenticate("prod", "ops@cliente.cl", CLAVE, None)
    assert srv.authenticate("prod", "ops@cliente.cl", CLAVE, {}) == 2


def test_una_conexion_buena_pasa_y_reporta_la_version():
    check = _check(_server())
    assert check.ok and check.reason == "" and check.server_version == "17.0+e"


# =============================================================================
# 2. El secreto
# =============================================================================


def test_la_clave_nunca_viaja_en_el_mensaje_de_error():
    """Un `faultString` es un traceback armado del otro lado. Que Odoo hoy no
    devuelva la clave no es una garantia que se pueda firmar."""
    srv = _server(auth_fault=f"Traceback ... authenticate(db, login, {CLAVE!r}) ...")
    check = _check(srv)

    assert CLAVE not in check.message
    assert REDACTED in check.message


def test_la_clave_no_entra_al_repr_de_las_credenciales():
    texto = repr(_creds())
    assert CLAVE not in texto
    assert "miempresa.odoo.com" in texto  # lo demas si, para poder depurar


def test_ningun_resultado_de_la_ruta_completa_lleva_la_clave():
    """Barrido sobre todo lo que sale: mensajes, avisos y filas."""
    resultado = scan_input(_creds(), common=_server(), models=_server())
    texto = repr(resultado) + str(resultado.preflight) + str(resultado.check)
    assert CLAVE not in texto


# =============================================================================
# 3. Solo lectura
# =============================================================================


def test_la_ruta_entera_no_ejecuta_un_solo_metodo_de_escritura():
    """El argumento de venta entero. Se comprueba por lo que se INVOCO, no por
    lo que dice el docstring: una lista de llamadas no se puede racionalizar."""
    srv = _server()
    scan_input(_creds(), common=srv, models=srv)

    metodos = {m for m, _ in srv.calls}
    assert metodos and not (metodos & {"create", "write", "unlink", "copy", "action_confirm"})
    assert metodos <= {"search", "search_read", "read", "fields_get", "search_count"}


def test_el_modulo_no_importa_el_plano_de_escritura():
    """Por los imports, no por el texto: el docstring NOMBRA `writeback` para
    explicar que no lo usa, asi que un `in` sobre la fuente se acredita a si mismo."""
    import ast
    import inspect

    from src.connectors import odoo_connect

    arbol = ast.parse(inspect.getsource(odoo_connect))
    modulos = {
        n.module or "" for n in ast.walk(arbol) if isinstance(n, ast.ImportFrom)
    } | {
        a.name for n in ast.walk(arbol) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any("writeback" in m for m in modulos), modulos


# =============================================================================
# 4. La forma: el motor no se entera de donde vinieron las filas
# =============================================================================


def test_las_filas_del_odoo_traen_todo_lo_que_trae_el_camino_del_csv():
    """Si el Odoo emitiera menos claves, el motor mediria distinto segun la
    fuente -- y esa diferencia no la denuncia ningun test del motor."""
    df = pd.DataFrame([
        {"supplier": "Acme", "order_date": "2026-01-05", "receipt_date": "2026-01-25",
         "declared_lead_time_days": 7.0, "product_id": "SKU-1", "po_id": "PO-000"},
    ])
    del_csv, _ = to_deliveries(df)
    del_odoo = read_receipts(_odoo()).grouped

    claves_csv = set(del_csv["Acme"][0])
    claves_odoo = set(del_odoo["Acme"][0])
    assert claves_csv <= claves_odoo, claves_csv - claves_odoo
    # Lo que el Odoo agrega, el scorecard lo ignora por clave: no cambia numeros.
    assert claves_odoo - claves_csv <= {"picking_id", "partner_id", "approval_latency_days"}


# =============================================================================
# 5. EL GATE ENDURECIDO: el valor se mueve entre dos corridas
# =============================================================================


def _brecha(odoo) -> float:
    pull = read_receipts(odoo)
    scan = scan_from_deliveries(pull.grouped, pull.dropped, pull.qa_issues)
    assert scan.ok, scan.qa_issues
    return scan.worst.lead_time_gap


def test_dos_odoo_con_plazos_observados_distintos_dan_brechas_distintas():
    """EL TEST QUE ACREDITA QUE ESTO MIDE.

    Dos instancias, mismo declarado, comportamiento distinto. Si la brecha no se
    moviera, la ruta estaria devolviendo una constante -- que es exactamente el
    modo de falla que este proyecto ya pago caro.
    """
    lenta = _brecha(_odoo(dias=40))
    rapida = _brecha(_odoo(dias=20))

    assert lenta != rapida
    assert lenta == pytest.approx(33.0)   # 40 observados - 7 prometidos
    assert rapida == pytest.approx(13.0)  # 20 observados - 7 prometidos
    assert lenta - rapida == pytest.approx(20.0)  # exactamente el delta del insumo


def test_mover_la_PROMESA_mueve_la_brecha_en_el_sentido_contrario():
    """El segundo eje. Con solo el primero, una ruta que ignorara el declarado y
    reportara el observado a secas pasaria igual de verde."""
    prometiendo_poco = _brecha(_odoo(dias=30, declarado=7.0))
    prometiendo_mas = _brecha(_odoo(dias=30, declarado=21.0))

    assert prometiendo_poco == pytest.approx(23.0)
    assert prometiendo_mas == pytest.approx(9.0)
    assert prometiendo_poco > prometiendo_mas


def test_el_titular_que_ve_el_cliente_cambia_con_el_dato_no_solo_el_interno():
    """La brecha se mueve arriba (SupplierScore) y tambien en el numero que la
    pagina publica. Un headline congelado sobre un motor que si se mueve es el
    mismo defecto una capa mas afuera."""
    def _titular(dias):
        pull = read_receipts(_odoo(dias=dias))
        return scan_from_deliveries(pull.grouped, pull.dropped, pull.qa_issues).headline

    a, b = _titular(20), _titular(40)
    assert a["worst_observed"] != b["worst_observed"]
    assert (a["worst_observed"], b["worst_observed"]) == (20.0, 40.0)
    assert a["worst_declared"] == b["worst_declared"] == 7.0
    assert a["n_skus_to_recalculate"] == b["n_skus_to_recalculate"] == 1


def test_la_brecha_no_es_cero_que_es_el_defecto_que_esto_viene_a_mostrar():
    """Sobre una conexion viva, la brecha daba cero por construccion mientras el
    plazo saliera del campo del ERP. Este es el numero que prueba que no."""
    assert _brecha(_odoo(dias=20)) > 12


def test_la_ruta_completa_de_cuatro_campos_a_brecha_medida():
    """De `OdooCredentials` al mismo `LeadTimeScan` que produce un CSV subido."""
    srv = _server(_odoo(dias=34))
    entrada = scan_input(_creds(), common=srv, models=srv)

    assert entrada.ok, entrada.preflight.missing if entrada.preflight else entrada.check.message
    scan = scan_from_deliveries(entrada.grouped, entrada.dropped, entrada.source_notes)
    assert scan.ok, scan.qa_issues
    assert scan.worst.supplier == "Acme"
    assert scan.worst.declared_lead_time == 7.0
    assert scan.worst.avg_lead_time == pytest.approx(34.0)
    assert "SKU-1" in scan.worst.skus_to_recalculate


# =============================================================================
# 6. La regla no negociable: sin fechas no se mide, y NUNCA se cae al `delay`
# =============================================================================


def test_sin_fechas_de_recepcion_se_rechaza_nombrandolas_y_no_se_usa_el_delay():
    """REPLICA DE `test_sin_fechas_no_se_mide_aunque_venga_el_campo_del_erp`.

    El Odoo tiene el plazo declarado poblado y ni una recepcion validada. Usar
    `product.supplierinfo.delay` como observacion compararia la promesa consigo
    misma y devolveria brecha CERO sobre el defecto exacto que el escaneo viene a
    mostrar. Se rechaza, y se nombra lo que falta.
    """
    vacio = _odoo(n=0)
    assert vacio.records("product.supplierinfo")[400]["delay"] == 7.0  # la promesa SI esta

    pre = preflight(read_receipts(vacio))

    assert not pre.ok
    assert pre.n_receipts == 0
    texto = " ".join(pre.missing)
    assert "recepcion" in texto and "Odoo" in texto
    # Y en ninguna parte se ofrecio el 7 como si fuera una medicion.
    assert "7" not in texto


def test_con_las_recepciones_a_medias_se_nombra_la_causa_dominante():
    """Las cuatro causas mandan a pantallas distintas del Odoo del cliente, asi
    que un 'no se encontraron datos' unico las manda a las cuatro."""
    borrador = _odoo(dias=20)
    for po in borrador.records("purchase.order").values():
        po["state"] = "draft"

    pre = preflight(read_receipts(borrador))

    assert not pre.ok
    assert pre.dropped["po_no_confirmada"] == 3
    assert any("borrador" in m for m in pre.missing)


def test_el_preflight_no_bloquea_por_falta_de_declarado_pero_lo_nombra():
    """La compuerta de QA del scan es la UNICA autoridad sobre 'hay con que
    comparar'. Una segunda copia del guardia es como se reintroduce el defecto."""
    sin_promesa = _odoo(dias=20)
    sin_promesa.records("product.supplierinfo").clear()

    pull = read_receipts(sin_promesa)
    pre = preflight(pull)

    assert pre.ok                       # el preflight deja pasar
    assert pre.n_with_declared == 0
    assert any("Plazo de entrega" in w for w in pre.warnings)
    # ...y el scan es el que rechaza, con su propio mensaje.
    scan = scan_from_deliveries(pull.grouped, pull.dropped, pull.qa_issues)
    assert not scan.ok
    assert not scan.findings


def test_un_delay_de_uno_no_asciende_a_promesa_por_esta_ruta_tampoco():
    """Es el default de Odoo. El conector ya lo pisa; esto fija que la ruta de
    conexion no lo re-introduzca por el costado del preflight."""
    pull = read_receipts(_odoo(dias=20, declarado=1.0))
    assert preflight(pull).n_with_declared == 0


def test_sin_el_puente_de_compras_se_bloquea_en_vez_de_caer_a_la_promesa():
    """`stock.move` sin `purchase_line_id`: Compras instalada, sin el enlace de
    inventario. El conector se niega; la ruta lo convierte en bloqueante, no en
    un 500 -- y sobre todo no en un plazo sacado de la ficha."""
    roto = _odoo(dias=20)
    for m in roto.records("stock.move").values():
        m.pop("purchase_line_id")
    srv = _server(roto)

    entrada = scan_input(_creds(), common=srv, models=srv)

    assert entrada.check.ok          # la conexion estaba bien
    assert not entrada.ok            # lo que falla es la medicion
    assert any("modulo de compras" in m for m in entrada.preflight.missing)
    assert entrada.grouped == {}


def test_una_conexion_fallida_no_devuelve_filas_ni_revienta():
    entrada = scan_input(_creds(api_key="mala"), common=_server(), models=_server())

    assert not entrada.ok
    assert entrada.check.reason == CREDENTIALS
    assert entrada.grouped == {} and entrada.preflight is None


# =============================================================================
# 7. El preflight reporta, y lo que reporta se mueve con el dato
# =============================================================================


def test_el_preflight_cuenta_recepciones_proveedores_y_rango_de_fechas():
    pre = preflight(read_receipts(_odoo(dias=20, n=3)))

    assert pre.ok
    assert (pre.n_receipts, pre.n_suppliers, pre.n_with_declared) == (3, 1, 1)
    assert pre.first_receipt == "2026-01-25"
    assert pre.last_receipt == "2026-03-22"
    assert pre.span_days == 56


def test_lo_que_el_preflight_reporta_cambia_cuando_cambian_los_datos():
    """Los conteos y el rango tienen que salir del insumo, no de una plantilla."""
    corto = preflight(read_receipts(_odoo(dias=20, n=3)))
    largo = preflight(read_receipts(_odoo(dias=20, n=3, mes_extra=True)))

    assert (corto.n_receipts, largo.n_receipts) == (3, 4)
    assert corto.last_receipt != largo.last_receipt
    assert largo.last_receipt == "2026-09-21"
    assert largo.span_days > corto.span_days


def test_el_preflight_no_hace_una_sola_llamada_extra_al_erp():
    """25 minutos por cuenta al mes: una segunda pasada por el ERP se paga dos
    veces, y peor, puede contar sobre un estado distinto del que despues se mide."""
    srv = _server()
    scan_input(_creds(), common=srv, models=srv)
    antes = len(srv.calls)

    preflight(read_receipts(srv._orm))  # el preflight, aparte
    assert len(srv.calls) == antes      # no toco el servidor


# =============================================================================
# 8. La URL que pega un cliente
# =============================================================================


@pytest.mark.parametrize(
    "pegado",
    [
        "https://miempresa.odoo.com",
        "miempresa.odoo.com",
        "  https://miempresa.odoo.com/  ",
        "https://miempresa.odoo.com/odoo/inventory",
        "https://miempresa.odoo.com/web#action=42",
    ],
)
def test_las_formas_en_que_un_cliente_pega_su_direccion_dan_el_mismo_origen(pegado):
    """Exigir la forma canonica es una pregunta de soporte por cuenta. El path
    ademas no es cosmetico: `.../web/xmlrpc/2/common` es un 404."""
    assert normalize_url(pegado) == "https://miempresa.odoo.com"


def test_una_direccion_que_no_es_http_no_se_intenta():
    check = check_connection(_creds(url="ftp://miempresa.odoo.com"), common=_server(), models=_server())
    assert check.reason == UNREACHABLE


def test_los_campos_vacios_se_nombran_uno_por_uno():
    """Cuatro campos, cuatro mensajes: 'faltan datos' no dice cual."""
    faltas = {
        UNREACHABLE: _creds(url=""),
        DATABASE: _creds(db=" "),
        CREDENTIALS: _creds(api_key=""),
    }
    for esperado, cr in faltas.items():
        assert check_connection(cr, common=_server(), models=_server()).reason == esperado
