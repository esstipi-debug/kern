"""Siembra en el Odoo 17 del laboratorio un distribuidor ficticio creible.

Es el sembrador de la prueba del recorrido del cliente del 2026-09-19, traido al
repo para que esa prueba se pueda repetir y para que
``tests/integracion/test_recorrido_cliente.py`` tenga sobre que correr.

**Carga los datos como los carga un usuario desde la pantalla.** El proveedor va
en la ficha del producto (``product_tmpl_id``) y SIN variante (``product_id``
vacio): es lo que deja el formulario de Odoo 17 cuando el producto no tiene
variantes (``purchase/views/product_views.xml:45``), y Odoo elige al proveedor
por la plantilla. La suite de integracion sembraba con variante -- el caso poco
comun -- y por eso no vio que Kern no leia ningun plazo (hallazgo F1).

Que siembra (``--hoy`` es la fecha de referencia; por defecto, hoy):

- 3 proveedores con plazo declarado de 21, 7 y 45 dias (el real, 30, 9 y 52, se
  mide con las recepciones) y 5 clientes.
- 20 productos almacenables. ``SKU-015`` (transpaleta) es intermitente: 6 ventas
  de 1 unidad en 26 semanas. ``SKU-017`` no vende. ``SKU-018`` tiene el plazo sin
  configurar (``delay=1``, el default de Odoo). ``SKU-019`` no tiene proveedor.
- 26 semanas de ventas a 3 pedidos por semana, confirmadas y despachadas.
- 4 compras recibidas (para medir el plazo real) y 2 confirmadas en camino.

**No es idempotente, a proposito: se niega a sembrar si la base ya tiene el
catalogo.** Sembrar dos veces duplicaria 26 semanas de ventas y ningun numero de
la prueba seria el mismo. Para empezar de cero, desde la raiz del repo:

    docker compose --env-file .env.odoo -f docker-compose.odoo.yml down -v
    python scripts/odoo_test_setup.py
    python scripts/odoo_sembrar_distribuidor.py

Con ``--hoy 2026-09-19`` reproduce los pedidos, las cantidades y el stock del
laboratorio del informe (la semilla es fija). Dos diferencias declaradas con ese
laboratorio, ninguna mueve un numero del plan: el precio de lista del proveedor
es el 92% del costo estandar del producto (alla eran iguales, y un test no podia
distinguir "precio del proveedor" de "costo del producto"), y las compras
historicas llevan ese precio en vez de 1.0.

Credenciales: ``--url/--db/--usuario/--clave``, o las mismas variables que usan
los tests de integracion (``ODOO_URL``, ``ODOO_DB``, ``ODOO_USERNAME``,
``ODOO_API_KEY``); sin ninguna, el laboratorio de ``scripts/odoo_test_setup.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import xmlrpc.client
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

SEMILLA = 42
DIAS_HISTORIA = 182
DIAS_DE_PEDIDO = (0, 2, 4)            # 3 pedidos por semana: dias desde el inicio de la semana
DIAS_HASTA_DESPACHO = (1, 1, 2, 3)
DISPERSION_SEMANAL = 0.35             # desvio / media de la venta semanal
#: El costo estandar incluye flete e internacion; la lista del proveedor, no.
FACTOR_PRECIO_PROVEEDOR = 0.92
MODULOS_REQUERIDOS = ("purchase", "sale_management", "stock")

SKU_INTERMITENTE = "SKU-015"
VENTAS_INTERMITENTE = 6
SKU_PLAZO_SIN_CONFIGURAR = "SKU-018"
SKU_SIN_PROVEEDOR = "SKU-019"


@dataclass(frozen=True)
class Proveedor:
    nombre: str
    plazo_declarado: int
    plazo_real: int


@dataclass(frozen=True)
class Sku:
    codigo: str
    nombre: str
    venta_semanal: float
    proveedor: int | None          # indice en PROVEEDORES; None = sin proveedor
    plazo: int | None              # None = el plazo declarado del proveedor
    moq: int
    costo: int
    precio_venta: int
    semanas_stock_final: float


@dataclass(frozen=True)
class Compra:
    sku: str
    proveedor: int
    dias: int                      # historica: hace cuantos dias; abierta: en cuantos llega
    cantidad: int


@dataclass(frozen=True)
class Pedido:
    fecha: datetime
    cliente: int                   # indice en CLIENTES
    lineas: tuple[tuple[str, int], ...]
    dias_hasta_despacho: int


@dataclass(frozen=True)
class PlanDeDatos:
    hoy: datetime
    ventas: dict[str, list[int]]   # sku -> unidades por semana
    pedidos: tuple[Pedido, ...]
    stock_final: dict[str, int]
    stock_inicial: dict[str, int]


PROVEEDORES = (
    Proveedor("Importadora Andes Ltda", 21, 30),
    Proveedor("Distribuidora Sur SpA", 7, 9),
    Proveedor("Fabrica Norte SA", 45, 52),
)
CLIENTES = ("Municipalidad Norte", "Constructora Delta", "Ferreteria Central",
            "Hospital Regional", "Colegio Andino")
CATALOGO = (
    Sku("SKU-001", "Guante nitrilo palma (par)", 60, 1, None, 0, 700, 1590, 1.0),
    Sku("SKU-002", "Cinta embalaje 48mm x 100m", 80, 1, None, 72, 560, 980, 0.5),
    Sku("SKU-003", "Casco seguridad con arnes", 12, 0, None, 50, 9000, 21000, 2.0),
    Sku("SKU-004", "Lente de seguridad claro", 30, 0, None, 0, 1800, 3700, 6.0),
    Sku("SKU-005", "Chaleco geologo reflectante", 20, 0, None, 0, 8500, 24750, 1.0),
    Sku("SKU-006", "Zapato seguridad punta acero", 6, 2, None, 0, 31000, 57000, 4.0),
    Sku("SKU-007", "Cono vial 70 cm", 8, 2, None, 0, 6500, 11900, 20.0),
    Sku("SKU-008", "Extintor PQS 6 kg", 4, 1, None, 0, 26000, 33600, 3.0),
    Sku("SKU-009", "Tubo LED T8 18W", 50, 0, None, 0, 1960, 4750, 3.0),
    Sku("SKU-010", "Panel LED 60x60 48W", 15, 0, None, 0, 11350, 12050, 2.0),
    Sku("SKU-011", "Zapatilla electrica 6 tomas", 10, 1, None, 0, 6990, 7700, 5.0),
    Sku("SKU-012", "Caja organizadora 60 L", 7, 1, None, 0, 9300, 23500, 2.0),
    Sku("SKU-013", "Toldo plegable 3x3", 2, 2, None, 0, 40000, 70200, 6.0),
    Sku("SKU-014", "Escalera tijera aluminio 6 peldanos", 1.5, 2, None, 0, 70000, 150000, 4.0),
    Sku("SKU-015", "Transpaleta manual 2500 kg", 0, 2, None, 0, 244000, 324000, 0.0),
    Sku("SKU-016", "Film stretch 50cm x 300m", 25, 1, None, 0, 6300, 6480, 1.0),
    Sku("SKU-017", "Carro de carga 3 en 1", 0, 0, None, 0, 180000, 178000, 0.0),
    Sku("SKU-018", "Cinta masking 48mm x 40m", 40, 1, 1, 0, 1370, 1250, 1.0),
    Sku("SKU-019", "Candado 50 mm", 5, None, None, 0, 25600, 26800, 1.0),
    Sku("SKU-020", "Cinta de peligro 300 m", 6, 1, None, 0, 5400, 8900, 8.0),
)
#: Stock final fijado a mano donde la venta semanal media no sirve para derivarlo.
STOCK_FINAL_FIJO = {"SKU-015": 1, "SKU-017": 15}
COMPRAS_HISTORICAS = (
    Compra("SKU-003", 0, 150, 30),
    Compra("SKU-009", 0, 90, 300),
    Compra("SKU-001", 1, 120, 400),
    Compra("SKU-006", 2, 160, 20),
)
COMPRAS_ABIERTAS = (Compra("SKU-005", 0, 10, 100), Compra("SKU-001", 1, 4, 150))


def precio_proveedor(item: Sku) -> int:
    """El precio de lista del proveedor: distinto del costo estandar a proposito."""
    return round(item.costo * FACTOR_PRECIO_PROVEEDOR)


def linea_proveedor(item: Sku, tmpl_id: int, proveedor_ids: list[int]) -> dict:
    """La fila de ``product.supplierinfo`` como la deja el formulario: sin variante."""
    if item.proveedor is None:
        raise ValueError(f"{item.codigo} no tiene proveedor")
    proveedor = PROVEEDORES[item.proveedor]
    return {
        "partner_id": proveedor_ids[item.proveedor],
        "product_tmpl_id": tmpl_id,
        "delay": item.plazo if item.plazo is not None else proveedor.plazo_declarado,
        "min_qty": item.moq,
        "price": precio_proveedor(item),
    }


def _ventas_semanales(rng: random.Random, semanas: int) -> dict[str, list[int]]:
    ventas = {item.codigo: [0] * semanas for item in CATALOGO}
    for item in CATALOGO:
        if item.codigo == SKU_INTERMITENTE or item.venta_semanal <= 0:
            continue
        for semana in range(semanas):
            sorteo = rng.gauss(item.venta_semanal, item.venta_semanal * DISPERSION_SEMANAL)
            ventas[item.codigo][semana] = max(0, round(sorteo))
    for semana in rng.sample(range(semanas), VENTAS_INTERMITENTE):
        ventas[SKU_INTERMITENTE][semana] = 1
    return ventas


def _pedidos(rng: random.Random, ventas: dict[str, list[int]], hoy: datetime, semanas: int) -> tuple[Pedido, ...]:
    """Reparte cada semana en 3 pedidos. El orden de los sorteos fija los datos: no tocar."""
    crudos: list[tuple[datetime, int, tuple[tuple[str, int], ...]]] = []
    for semana in range(semanas):
        base = hoy - timedelta(days=DIAS_HISTORIA - 7 * semana)
        reparto = {sku: [0] * len(DIAS_DE_PEDIDO) for sku in ventas}
        for sku, serie in ventas.items():
            for _ in range(serie[semana]):
                reparto[sku][rng.randrange(len(DIAS_DE_PEDIDO))] += 1
        for k, desfase in enumerate(DIAS_DE_PEDIDO):
            lineas = tuple((sku, q[k]) for sku, q in reparto.items() if q[k] > 0)
            if lineas:
                fecha = base + timedelta(days=desfase, hours=rng.randrange(8))
                crudos.append((fecha, rng.choice(range(len(CLIENTES))), lineas))
    despachos = [rng.choice(DIAS_HASTA_DESPACHO) for _ in crudos]
    return tuple(Pedido(f, c, lineas, d) for (f, c, lineas), d in zip(crudos, despachos))


def plan_de_datos(hoy: datetime, semilla: int = SEMILLA) -> PlanDeDatos:
    """Todo lo que se va a sembrar, calculado sin tocar Odoo."""
    rng = random.Random(semilla)
    semanas = DIAS_HISTORIA // 7
    ventas = _ventas_semanales(rng, semanas)
    pedidos = _pedidos(rng, ventas, hoy, semanas)
    stock_final = {
        item.codigo: STOCK_FINAL_FIJO.get(item.codigo, round(item.venta_semanal * item.semanas_stock_final))
        for item in CATALOGO
    }
    recibido = {item.codigo: 0 for item in CATALOGO}
    for compra in COMPRAS_HISTORICAS:
        recibido[compra.sku] += compra.cantidad
    inicial = {sku: sum(ventas[sku]) + stock_final[sku] - recibido[sku] for sku in ventas}
    negativos = sorted(sku for sku, q in inicial.items() if q < 0)
    if negativos:
        raise ValueError(f"stock inicial negativo en {negativos}: revisar COMPRAS_HISTORICAS")
    return PlanDeDatos(hoy, ventas, pedidos, stock_final, inicial)


class Odoo:
    """XML-RPC minimo, sin pasar por el conector de Kern: el sembrador no usa el
    codigo que se va a probar para armar el dato con que se lo prueba."""

    def __init__(self, url: str, db: str, usuario: str, clave: str) -> None:
        uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, usuario, clave, {})
        if not uid:
            raise ValueError(f"Odoo rechazo al usuario {usuario!r} en la base {db!r}")
        self._db, self._uid, self._clave = db, uid, clave
        self._modelos = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)

    def x(self, modelo: str, metodo: str, *args, **kw):
        try:
            return self._modelos.execute_kw(self._db, self._uid, self._clave, modelo, metodo, list(args), kw)
        except xmlrpc.client.Fault as falla:
            # Los metodos de accion que devuelven None (action_apply_inventory, ...):
            # Odoo ya hizo commit y solo falla al serializar la respuesta
            # (odoo/addons/base/controllers/rpc.py:128, allow_none=False).
            if "cannot marshal None" in falla.faultString:
                return None
            raise


def modulos_faltantes(odoo: Odoo) -> list[str]:
    filas = odoo.x("ir.module.module", "search_read",
                   [("name", "in", list(MODULOS_REQUERIDOS)), ("state", "=", "installed")], fields=["name"])
    instalados = {f["name"] for f in filas}
    return [m for m in MODULOS_REQUERIDOS if m not in instalados]


def skus_ya_cargados(odoo: Odoo) -> list[str]:
    """Los codigos del catalogo que la base ya tiene, archivados incluidos."""
    filas = odoo.x("product.product", "search_read", [("default_code", "in", [s.codigo for s in CATALOGO])],
                   fields=["default_code"], context={"active_test": False})
    return sorted({f["default_code"] for f in filas})


def _ts(fecha: datetime) -> str:
    return fecha.strftime("%Y-%m-%d %H:%M:%S")


def _crear_socios(odoo: Odoo) -> tuple[list[int], list[int]]:
    proveedores = [odoo.x("res.partner", "create", {"name": p.nombre, "is_company": True, "supplier_rank": 1})
                   for p in PROVEEDORES]
    clientes = [odoo.x("res.partner", "create", {"name": f"Cliente {c}", "is_company": True, "customer_rank": 1})
                for c in CLIENTES]
    return proveedores, clientes


def _crear_catalogo(odoo: Odoo, proveedor_ids: list[int]) -> dict[str, int]:
    """Crea los productos y su proveedor en la PLANTILLA. Devuelve sku -> product.product id."""
    productos: dict[str, int] = {}
    for item in CATALOGO:
        tmpl = odoo.x("product.template", "create", {
            "name": item.nombre, "default_code": item.codigo, "type": "product",
            "list_price": item.precio_venta, "standard_price": item.costo,
            "sale_ok": True, "purchase_ok": True,
        })
        productos[item.codigo] = odoo.x("product.product", "search", [("product_tmpl_id", "=", tmpl)])[0]
        if item.proveedor is not None:
            odoo.x("product.supplierinfo", "create", linea_proveedor(item, tmpl, proveedor_ids))
    return productos


def _cargar_stock(odoo: Odoo, productos: dict[str, int], inicial: dict[str, int], ubicacion: int) -> None:
    for sku, cantidad in inicial.items():
        if cantidad <= 0:
            continue
        quant = odoo.x("stock.quant", "create",
                       {"product_id": productos[sku], "location_id": ubicacion, "inventory_quantity": cantidad},
                       context={"inventory_mode": True})
        odoo.x("stock.quant", "action_apply_inventory", [quant])


def _validar(odoo: Odoo, picking_ids: list[int]) -> None:
    for pid in picking_ids:
        odoo.x("stock.picking", "action_assign", [pid])
        for mov in odoo.x("stock.move", "search_read", [("picking_id", "=", pid)], fields=["product_uom_qty"]):
            odoo.x("stock.move", "write", [mov["id"]], {"quantity": mov["product_uom_qty"], "picked": True})
        respuesta = odoo.x("stock.picking", "button_validate", [pid])
        if isinstance(respuesta, dict) and respuesta.get("res_model"):
            raise RuntimeError(f"la validacion del picking {pid} pidio el asistente {respuesta['res_model']}")


def _fechar(odoo: Odoo, picking_ids: list[int], fecha: datetime) -> None:
    for pid in picking_ids:
        odoo.x("stock.picking", "write", [pid], {"date_done": _ts(fecha)})
        odoo.x("stock.move", "write", odoo.x("stock.move", "search", [("picking_id", "=", pid)]), {"date": _ts(fecha)})


def _orden_de_compra(odoo: Odoo, compra: Compra, productos: dict[str, int], proveedor_ids: list[int],
                     llegada: datetime) -> int:
    item = next(s for s in CATALOGO if s.codigo == compra.sku)
    orden = odoo.x("purchase.order", "create", {"partner_id": proveedor_ids[compra.proveedor], "order_line": [
        (0, 0, {"product_id": productos[compra.sku], "product_qty": compra.cantidad,
                "price_unit": precio_proveedor(item), "date_planned": _ts(llegada)})]})
    odoo.x("purchase.order", "button_confirm", [orden])
    return orden


def _compras_historicas(odoo: Odoo, plan: PlanDeDatos, productos: dict[str, int], proveedor_ids: list[int]) -> None:
    """Confirmadas, recibidas y fechadas con el plazo REAL: de aca se mide."""
    for compra in COMPRAS_HISTORICAS:
        proveedor = PROVEEDORES[compra.proveedor]
        emitida = plan.hoy - timedelta(days=compra.dias)
        orden = _orden_de_compra(odoo, compra, productos, proveedor_ids,
                                 emitida + timedelta(days=proveedor.plazo_declarado))
        pickings = odoo.x("purchase.order", "read", [orden], fields=["picking_ids"])[0]["picking_ids"]
        _validar(odoo, pickings)
        odoo.x("purchase.order", "write", [orden], {"date_order": _ts(emitida), "date_approve": _ts(emitida)})
        _fechar(odoo, pickings, emitida + timedelta(days=proveedor.plazo_real))


def _ventas(odoo: Odoo, plan: PlanDeDatos, productos: dict[str, int], cliente_ids: list[int]) -> None:
    for n, pedido in enumerate(plan.pedidos, 1):
        orden = odoo.x("sale.order", "create", {"partner_id": cliente_ids[pedido.cliente], "order_line": [
            (0, 0, {"product_id": productos[sku], "product_uom_qty": q}) for sku, q in pedido.lineas]})
        odoo.x("sale.order", "action_confirm", [orden])
        odoo.x("sale.order", "write", [orden], {"date_order": _ts(pedido.fecha)})
        pickings = odoo.x("sale.order", "read", [orden], fields=["picking_ids"])[0]["picking_ids"]
        _validar(odoo, pickings)
        _fechar(odoo, pickings, pedido.fecha + timedelta(days=pedido.dias_hasta_despacho))
        if n % 20 == 0:
            print(f"  {n}/{len(plan.pedidos)} pedidos de venta")


def _diseno(odoo: Odoo, plan: PlanDeDatos, productos: dict[str, int], ubicacion: int) -> dict:
    """Lo que se sembro, leido de vuelta de Odoo: el stock y las lineas de proveedor reales."""
    por_producto = {v: k for k, v in productos.items()}
    en_odoo: dict[str, float] = {}
    for fila in odoo.x("stock.quant", "search_read", [("location_id", "=", ubicacion)],
                       fields=["product_id", "quantity"]):
        sku = por_producto.get(fila["product_id"][0])
        if sku:
            en_odoo[sku] = en_odoo.get(sku, 0.0) + fila["quantity"]
    lineas = odoo.x("product.supplierinfo", "search_read",
                    [("product_tmpl_id.default_code", "in", [s.codigo for s in CATALOGO])], fields=["product_id"])
    return {
        "fecha_referencia": _ts(plan.hoy), "semanas": DIAS_HISTORIA // 7, "pedidos_venta": len(plan.pedidos),
        "skus": [{"sku": s.codigo, "venta_6m": sum(plan.ventas[s.codigo]),
                  "stock_final_disenado": plan.stock_final[s.codigo], "stock_en_odoo": en_odoo.get(s.codigo, 0.0),
                  "proveedor": None if s.proveedor is None else PROVEEDORES[s.proveedor].nombre}
                 for s in CATALOGO],
        "supplierinfo_con_product_id": sum(1 for f in lineas if f["product_id"]),
        "supplierinfo_total": len(lineas),
    }


def sembrar(odoo: Odoo, plan: PlanDeDatos) -> dict:
    proveedor_ids, cliente_ids = _crear_socios(odoo)
    productos = _crear_catalogo(odoo, proveedor_ids)
    ubicacion = odoo.x("stock.warehouse", "search_read", [], fields=["lot_stock_id"], limit=1)[0]["lot_stock_id"][0]
    _cargar_stock(odoo, productos, plan.stock_inicial, ubicacion)
    _compras_historicas(odoo, plan, productos, proveedor_ids)
    _ventas(odoo, plan, productos, cliente_ids)
    for compra in COMPRAS_ABIERTAS:     # confirmadas y NO recibidas: en camino
        _orden_de_compra(odoo, compra, productos, proveedor_ids, plan.hoy + timedelta(days=compra.dias))
    return _diseno(odoo, plan, productos, ubicacion)


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("ODOO_URL", "http://localhost:8069"))
    p.add_argument("--db", default=os.environ.get("ODOO_DB", "kern_test"))
    p.add_argument("--usuario", default=os.environ.get("ODOO_USERNAME", "admin"))
    p.add_argument("--clave", default=os.environ.get("ODOO_API_KEY", "admin"))
    p.add_argument("--hoy", type=lambda s: datetime.strptime(s, "%Y-%m-%d").replace(hour=12),
                   default=datetime.now().replace(hour=12, minute=0, second=0, microsecond=0),
                   help="fecha de referencia AAAA-MM-DD (2026-09-19 reproduce el laboratorio del informe)")
    p.add_argument("--salida", type=Path, default=None, help="carpeta donde escribir diseno-datos.json")
    return p.parse_args(argv)


def _base_lista(odoo: Odoo, db: str) -> bool:
    faltan = modulos_faltantes(odoo)
    if faltan:
        print(f"faltan modulos en {db}: {', '.join(faltan)}. Corre: python scripts/odoo_test_setup.py")
        return False
    cargados = skus_ya_cargados(odoo)
    if cargados:
        print(f"la base {db} ya tiene {len(cargados)} de los {len(CATALOGO)} SKU del catalogo "
              f"({', '.join(cargados[:3])}...). No se siembra dos veces: duplicaria 26 semanas de ventas.")
        print("Base limpia: docker compose --env-file .env.odoo -f docker-compose.odoo.yml down -v "
              "&& python scripts/odoo_test_setup.py")
        return False
    return True


def _ultima_linea(exc: BaseException) -> str:
    """La ultima linea del error, en ASCII: un fault de Odoo trae el traceback entero."""
    lineas = str(exc).strip().splitlines() or [repr(exc)]
    return lineas[-1].encode("ascii", "replace").decode("ascii")


def main(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    try:
        odoo = Odoo(args.url, args.db, args.usuario, args.clave)
    except (OSError, xmlrpc.client.Error, ValueError) as exc:
        print(f"no se pudo entrar a {args.url} (base {args.db}): {_ultima_linea(exc)}")
        print("Levanta el laboratorio con: python scripts/odoo_test_setup.py")
        return 2
    if not _base_lista(odoo, args.db):
        return 2
    diseno = sembrar(odoo, plan_de_datos(args.hoy))
    if args.salida is not None:
        args.salida.mkdir(parents=True, exist_ok=True)
        (args.salida / "diseno-datos.json").write_text(json.dumps(diseno, ensure_ascii=False, indent=1),
                                                       encoding="utf-8")
    print(f"pedidos de venta: {diseno['pedidos_venta']} | supplierinfo con variante: "
          f"{diseno['supplierinfo_con_product_id']}/{diseno['supplierinfo_total']}")
    for fila in diseno["skus"]:
        print(f"  {fila['sku']} venta6m={fila['venta_6m']:>5} final_disenado={fila['stock_final_disenado']:>4} "
              f"en_odoo={fila['stock_en_odoo']:>6.0f}  {fila['proveedor']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
