"""Barrido de neteo: cual de los emisores de cantidad a pedir descuenta lo que ya viene.

**Por que existe.** T05 (``comprobaciones.py``) sondea TRES entregables de reposicion y
puntua EL MEJOR. Eso es correcto para la vara -- la pregunta de la bateria es "¿Kern
puede?" -- y ciego para el producto, cuya pregunta es la contraria: "¿cual de los
emisores NO puede?". Por eso H4 vivio meses con la bateria en verde:
``inventory_optimization`` nunca fue candidato de T05, y las tres hermanas
(``shopify_replenishment``, ``channel_replenishment``, ``drp``) tampoco. Un tool que
nadie sondea no puede ponerse rojo.

**Que es y que no es.** Es un diagnostico AL LADO de la bateria, no una tarea nueva:
``tareas.json`` es externa y ``TOTAL_TAREAS`` sigue en 20. La vara no se toca; lo que
cambia es de donde salen las sondas. T05 pasa a CONSUMIR estas
(:func:`sondas_t05`), asi que "netea" se define UNA vez en el repo y no puede
divergir entre el corrector y el barrido.

**La disciplina** es la de ``tests/test_neteo_en_transito.py`` (kern#251):

- **que un campo exista no acredita nada**: cada sonda corre el emisor DOS veces con
  insumos distintos y afirma que el VALOR SE MUEVE (:func:`sonda_dos_corridas`);
- **la ausencia se declara**: un tool sin canal de entrada sale ``sin_canal`` con razon,
  nunca "no netea" -- y si manana le aparece un campo con nombre de en-transito, la
  sonda pasa SOLA a ``sin_sonda`` (:func:`_sin_canal`), el vector congelado de
  ``tests/test_barrido_neteo.py`` se pone rojo y alguien tiene que escribir la sonda de
  comportamiento. Un canal nuevo no entra callado.

Los cuatro estados son cuatro cosas distintas, y mezclarlos es justamente el defecto
que este modulo existe para no repetir:

============  =============================================================
``netea``     hay canal, la sonda lo movio y la cantidad bajo
``no_netea``  hay canal, la sonda lo movio y la cantidad NO bajo: la brecha
``sin_sonda`` hay canal y nadie escribio la sonda: ni se acredita ni se niega
``sin_canal`` no hay por donde entrar el en-transito: la brecha es de diseno
============  =============================================================

Clasificar es OBLIGATORIO: ``tests/test_barrido_neteo.py`` recorre
``build_default_registry()`` y falla si un tool no esta ni en :data:`EMISORES` ni en
:data:`NO_EMISORES`. Un tool nuevo tiene que decir si emite cantidad a pedir, y si la
emite, con que sonda se prueba.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache

#: Nombres con los que un canal de en-transito podria aparecer en el contrato de
#: columnas de un tool o en su entregable. Misma lista que usa T05: si un tool
#: declarado ``sin_canal`` empieza a nombrar uno de estos, la sonda lo detecta.
CAMPOS_EN_TRANSITO = ("transit", "transito", "on_order", "onorder", "incoming", "open_po",
                      "scheduled_receipt", "por_recibir", "oc_pendiente")

#: Unidades ya emitidas con las que se sondea el neteo. Es el mismo valor que usa T05.
ON_ORDER_SONDA = 80.0

#: Para la planilla el valor es chico a proposito: 42 en estante + 5 en camino = 47,
#: todavia bajo el punto de reorden de 50. Asi la sonda mide que baja la CANTIDAD y no
#: que se apago el disparador, que seria cierto pero probaria menos.
ON_ORDER_SONDA_PLANILLA = 5.0

#: Lo que el entregable pone en manos del operador. No es decoracion: una CANTIDAD se
#: pide tal cual y se compra dos veces si nadie neteo; un NIVEL lo netea quien ejecuta;
#: un VEREDICTO de cobertura manda a comprar (o a no comprar) sin decir cuanto. Los
#: tres cargan el mismo riesgo -- salen de una foto del estante -- por caminos distintos.
CANTIDAD = "cantidad a pedir"
NIVEL = "nivel objetivo"
VEREDICTO = "veredicto de cobertura"


@dataclass(frozen=True)
class SondaNeteo:
    """Lo que UNA sonda midio sobre UN emisor.

    ``descuenta`` es tri-estado a proposito y ``campo_en_transito`` lo desambigua:
    ``None`` con canal significa "hay por donde y nadie lo probo" (``sin_sonda``), y
    ``None`` sin canal significa "no hay por donde" (``sin_canal``). Colapsar los dos
    en ``False`` acusaria al entregable de algo que no se midio.
    """

    tool: str
    entregable: str
    emite: str
    #: Como entra el en-transito (columna, param o mapa). ``None`` = no hay canal.
    campo_en_transito: str | None
    descuenta: bool | None
    #: La evidencia: las dos corridas, o la razon por la que no hay canal.
    prueba: str

    @property
    def estado(self) -> str:
        if self.campo_en_transito is None:
            return "sin_canal"
        if self.descuenta is None:
            return "sin_sonda"
        return "netea" if self.descuenta else "no_netea"


def sonda_dos_corridas(
    correr: Callable[[float], Sequence[float]],
    *,
    en_camino: float,
    como: str = "",
) -> tuple[bool | None, str]:
    """La sonda generica: corre el emisor con 0 y con ``en_camino``, y compara.

    Devuelve ``(descuenta, prueba)``. ``True`` cuando TODA cantidad dimensionada baja
    -- que una sola baje no alcanza: un job que netea un SKU y se olvida del resto
    sobre-compra igual. ``None`` cuando la corrida sin en-transito no dimensiono nada:
    ahi no se midio, y un falso negativo con cara de medicion es peor que una sonda
    ausente.

    Se comparan solo las posiciones que la corrida base dimensiono en positivo. Una
    linea que no tenia nada que pedir no puede bajar, y exigirselo convertiria en
    "NO netea" a un job que netea todo lo que emitio.

    ``como`` nombra por donde entro el dato ("en la columna de la planilla", "por
    SKU"); va en la evidencia para que se pueda defender sin volver a correr nada.
    """
    qty_sin = list(correr(0.0))
    qty_con = list(correr(en_camino))
    donde = f" {como}" if como else ""
    if len(qty_sin) != len(qty_con):
        return False, (f"las dos corridas no emiten la misma cantidad de lineas "
                       f"({len(qty_sin)} -> {len(qty_con)}): la sonda no puede comparar")
    pares = [(a, b) for a, b in zip(qty_sin, qty_con, strict=True) if a > 0]
    if not pares:
        return None, ("la sonda no produjo ninguna linea dimensionada: no se midio nada, "
                      "asi que no se acredita ni se niega")
    baja = all(b < a for a, b in pares)
    return baja, (f"con {en_camino:g} unidades ya emitidas{donde} la cantidad a pedir "
                  f"{'baja' if baja else 'NO baja'}: {qty_sin} -> {qty_con}")


@lru_cache(maxsize=1)
def _registro():
    from scm_agent.tools import build_default_registry

    return build_default_registry()


def _campos_declarados(tool: str, *clases: type) -> set[str]:
    """El contrato de columnas del tool MAS los campos de sus entregables.

    Es la superficie por donde un canal de en-transito podria aparecer sin que nadie
    escriba una sonda. No pretende ser exhaustiva (los ``params`` no se declaran en
    ningun lado legible por maquina); cubre los dos caminos que si estan escritos.
    """
    t = _registro().get(tool)
    campos = set(t.required_columns) | set(t.optional_columns)
    for clase in clases:
        campos |= set(getattr(clase, "__dataclass_fields__", {}))
    return campos


def _tokens(nombre: str) -> list[str]:
    """``echelon_order_up_to`` -> ``[echelon, order, up, to]``."""
    return [t for t in re.split(r"[^a-z0-9]+", nombre.lower()) if t]


def nombra_en_transito(campo: str) -> bool:
    """¿Este nombre de campo es un canal de en-transito?

    Por TOKENS, no por substring, y esa distincion no es cosmetica: buscando
    ``"on_order"`` adentro de ``"echelon_order_up_to"`` hay una coincidencia
    (``echel-on_order-_up_to``) y ``multi_echelon`` salia acusado de tener un canal que
    no tiene. Un falso positivo aca cuesta lo mismo que un falso negativo: manda a
    escribir una sonda que no se puede escribir, y el vector congelado queda mintiendo.

    Los tokens se comparan por prefijo para que ``scheduled_receipts`` (plural) cuente
    como ``scheduled_receipt``.
    """
    campo_t = _tokens(campo)
    for aguja in CAMPOS_EN_TRANSITO:
        aguja_t = _tokens(aguja)
        if len(aguja_t) == 1:
            if any(aguja_t[0] in t for t in campo_t):
                return True
            continue
        ancho = len(aguja_t)
        for i in range(len(campo_t) - ancho + 1):
            if all(campo_t[i + j].startswith(aguja_t[j]) for j in range(ancho)):
                return True
    return False


def _sin_canal(tool: str, entregable: str, emite: str, *, campos: Iterable[str],
               razon: str) -> SondaNeteo:
    """Declara que el tool no tiene por donde recibir el en-transito -- y lo VERIFICA.

    La razon la escribe un humano; que siga siendo cierta lo comprueba la sonda. Si
    aparece un campo con nombre de en-transito, el estado pasa a ``sin_sonda`` y el
    vector congelado se pone rojo: la brecha se cierra escribiendo la sonda, no
    agregando el campo.
    """
    hallados = sorted(c for c in campos if nombra_en_transito(c))
    if hallados:
        return SondaNeteo(
            tool=tool, entregable=entregable, emite=emite,
            campo_en_transito=hallados[0], descuenta=None,
            prueba=(f"aparecio un canal de en-transito ({', '.join(hallados)}) y ninguna "
                    f"sonda lo corre: escribir la sonda antes de acreditar el neteo"),
        )
    return SondaNeteo(tool=tool, entregable=entregable, emite=emite,
                      campo_en_transito=None, descuenta=None, prueba=razon)


# -- los que SI tienen canal -------------------------------------------------------


def sonda_excel() -> SondaNeteo:
    """``excel_replenishment``: la planilla del cliente, con su columna de en-transito.

    Dos corridas sobre la MISMA planilla, cambiando solo esa columna. El valor sondeado
    se elige para que el disparador siga activo en las dos: con 42 en estante contra un
    punto de reorden de 50, una posicion de 47 sigue bajo el punto y lo que cambia es la
    CANTIDAD. Un valor mas grande apagaria el disparador y la cantidad caeria a cero por
    otra razon -- cierto, pero probaria menos.
    """
    import tempfile

    from openpyxl import Workbook

    from jobs import excel_replenishment_job as job

    def _correr_en(carpeta: str) -> Callable[[float], list[float]]:
        def _correr(en_transito: float) -> list[float]:
            wb = Workbook()
            ws = wb.active
            ws.title = "Hoja"
            for col, encabezado in enumerate(("Codigo", "Stock", "Punto Reorden", "En Transito"), 1):
                ws.cell(row=1, column=col, value=encabezado)
            for fila, (sku, stock, rop) in enumerate((("P1", 42, 50), ("P2", 30, 40)), 2):
                for col, valor in enumerate((sku, stock, rop, en_transito), 1):
                    ws.cell(row=fila, column=col, value=valor)
            ruta = f"{carpeta}/planilla-{en_transito:g}.xlsx"
            wb.save(ruta)
            return [ln.restock_qty for ln in job.run(job.prepare(ruta)).lines]

        return _correr

    with tempfile.TemporaryDirectory() as carpeta:
        descuenta, prueba = sonda_dos_corridas(
            _correr_en(carpeta), en_camino=ON_ORDER_SONDA_PLANILLA,
            como="en la columna de la planilla",
        )
    return SondaNeteo(
        tool="excel_replenishment",
        entregable="jobs.excel_replenishment_job.ReplenishmentLine",
        emite=CANTIDAD, campo_en_transito="columna En Transito de la planilla",
        descuenta=descuenta, prueba=prueba,
    )


def sonda_odoo() -> SondaNeteo:
    """``odoo_replenishment``: reposicion contra el ERP, por el mapa de OC abiertas.

    El campo existe desde que se cerro kern#251; que exista no lo acredita. Dos
    corridas sobre la MISMA fuente, cambiando solo el mapa que ``plan_replenishment``
    recibe.
    """
    from src.connectors import InventoryLevel, Order, OrderLine, Product
    from src.connectors.replenish import plan_replenishment

    class _Fuente:
        """Fuente minima y determinista, con historial suficiente para pronosticar.

        No se usa ``simulator.demo_store()`` a proposito: su catalogo no llega al
        minimo de observaciones, asi que las dos corridas salen vacias y la sonda
        reportaria "NO baja" cuando en realidad no midio nada.
        """

        def list_products(self):
            return [Product(sku="P1", title="A", price=10.0, cost=6.0)]

        def inventory_levels(self):
            return [InventoryLevel(sku="P1", available=20.0)]

        def orders(self):
            return [
                Order(order_id=f"O{i}", created_at=f"2026-01-{i:02d}",
                      lines=(OrderLine(sku="P1", quantity=10.0, price=10.0),))
                for i in range(1, 13)
            ]

    def _correr(en_camino: float) -> list[float]:
        mapa = {"P1": en_camino} if en_camino else None
        plan = plan_replenishment(_Fuente(), on_order_by_sku=mapa)
        return [ln.restock_qty for ln in plan.lines if ln.status == "ok"]

    descuenta, prueba = sonda_dos_corridas(_correr, en_camino=ON_ORDER_SONDA, como="por SKU")
    return SondaNeteo(
        tool="odoo_replenishment", entregable="src.connectors.replenish.ReplenishmentLine",
        emite=CANTIDAD, campo_en_transito="on_order_by_sku (OC abiertas del ERP)",
        descuenta=descuenta, prueba=prueba,
    )


def sonda_ddmrp() -> SondaNeteo:
    """``ddmrp``: el primero que neteo, y el unico cuyo criterio lo exigia (T05)."""
    import pandas as pd

    from jobs import ddmrp_job as job

    partes = pd.DataFrame(
        {
            "part_id": ["P1", "P2"],
            "adu": [10.0, 10.0],
            "dlt": [7.0, 7.0],
            "on_hand": [20.0, 20.0],
            "on_order": [0.0, 0.0],
            "qualified_demand": [5.0, 5.0],
            "supplier": ["S-NORTE", "S-SUR"],
        }
    )

    def _correr(en_camino: float) -> list[float]:
        reporte = job.run(job.prepare_records(partes.assign(on_order=en_camino)))
        return [p.signal.order_qty for p in reporte.parts]

    descuenta, prueba = sonda_dos_corridas(_correr, en_camino=ON_ORDER_SONDA)
    return SondaNeteo(
        tool="ddmrp", entregable="jobs.ddmrp_job.DdmrpPart", emite=CANTIDAD,
        campo_en_transito="columna on_order", descuenta=descuenta, prueba=prueba,
    )


def sonda_inventory_optimization() -> SondaNeteo:
    """``inventory_optimization``: el tool central, y el que la bateria nunca miro.

    Dimensiona ``s`` y ``Q`` desde la serie de demanda; la posicion entra por
    ``on_hand_by_sku`` / ``on_order_by_sku`` y decide CUANDO (``order_qty_now``). El
    estante se fija por debajo del punto de reorden para que el disparador siga activo
    en las dos corridas y lo que se mida sea la cantidad.
    """
    import numpy as np
    import pandas as pd

    from jobs import inventory_optimization as job

    rng = np.random.default_rng(1)
    demanda = pd.DataFrame({
        "date": pd.date_range("2025-01-06", periods=30, freq="W-MON"),
        "product_id": "P1",
        "quantity": [max(0, round(rng.normal(20.0, 4.0))) for _ in range(30)],
        "unit_cost": 5.0,
        "lead_time_days": 14,
    })
    estante = job.run(demanda).recommendations[0].reorder_point - 1.0

    def _correr(en_camino: float) -> list[float]:
        reporte = job.run(demanda, on_hand_by_sku={"P1": estante},
                          on_order_by_sku={"P1": en_camino})
        # Redondeado para que la evidencia sea legible: la politica (s,Q) emite una Q
        # con decimales largos y la comparacion no cambia por dos cifras.
        return [round(r.order_qty_now, 2) for r in reporte.recommendations
                if r.order_qty_now is not None]

    descuenta, prueba = sonda_dos_corridas(_correr, en_camino=ON_ORDER_SONDA,
                                           como="en la columna del CSV")
    return SondaNeteo(
        tool="inventory_optimization", entregable="jobs.inventory_optimization.SkuRecommendation",
        emite=CANTIDAD, campo_en_transito="columna on_order del CSV",
        descuenta=descuenta, prueba=prueba,
    )


def sonda_shopify() -> SondaNeteo:
    """``shopify_replenishment``: la cantidad ``incoming`` que la tienda ya reporta."""
    from jobs import shopify_job as job
    from src.connectors.shopify import InMemoryShopifyCommerce

    loc = "gid://shopify/Location/1"
    variante = {
        "id": "gid://shopify/ProductVariant/P1", "sku": "P1", "displayName": "P1",
        "price": "12.00",
        "inventoryItem": {"id": "gid://shopify/InventoryItem/P1",
                          "unitCost": {"amount": "5.00"}},
    }
    ordenes = [
        {"id": f"gid://shopify/Order/{d}", "createdAt": f"2026-01-0{d}T10:00:00Z",
         "lineItems": {"edges": [{"node": {
             "sku": "P1", "quantity": 5,
             "originalUnitPriceSet": {"shopMoney": {"amount": "12.00"}}}}]}}
        for d in range(1, 9)
    ]

    def _correr(en_camino: float) -> list[float]:
        rpc = InMemoryShopifyCommerce(
            variants=[variante], orders=ordenes, stock={"P1": {loc: 12}},
            incoming={"P1": {loc: int(en_camino)}},
        )
        reporte = job.run(job.prepare(None, {"shopify_rpc": rpc}))
        return [ln.restock_qty for ln in reporte.lines if ln.status == "ok"]

    descuenta, prueba = sonda_dos_corridas(_correr, en_camino=ON_ORDER_SONDA,
                                           como="en la cantidad incoming de la tienda")
    return SondaNeteo(
        tool="shopify_replenishment", entregable="src.connectors.replenish.ReplenishmentLine",
        emite=CANTIDAD, campo_en_transito="quantities(names: incoming) de Shopify",
        descuenta=descuenta, prueba=prueba,
    )


def sonda_channel() -> SondaNeteo:
    """``channel_replenishment``: canal para la demanda, ERP para el stock y las OC.

    Las OC abiertas se leen con el MISMO lector que usa ``jobs/odoo_job.py``, asi que
    la sonda tambien verifica que no haya una segunda implementacion de la regla.
    """
    from jobs import channel_replenishment_job as job
    from src.connectors.odoo import demo_odoo
    from src.connectors.shopify import demo_shopify

    def _correr(en_camino: float) -> list[float]:
        odoo = demo_odoo()
        for rec in odoo.records("product.product").values():
            rec.setdefault("uom_id", [1, "Units"])
        odoo.records("uom.uom").setdefault(1, {"name": "Units"})
        lineas = odoo.records("purchase.order.line")
        # Una OC abierta por producto: si solo se le cuelga a uno, los demas no pueden
        # bajar y la sonda diria "NO netea" sobre un job que neteo todo lo que le
        # dieron. Una linea `draft` deja el modulo de Compras presente con el mapa
        # vacio: es una lectura buena sin nada en camino, no una ausencia de lectura.
        for i, pid in enumerate(sorted(odoo.records("product.product"))):
            lineas[500 + i] = {"product_id": [pid, f"P{pid}"],
                               "product_qty": max(en_camino, 1.0), "qty_received": 0.0,
                               "product_uom": [1, "Units"],
                               "state": "purchase" if en_camino else "draft"}
        reporte = job.run(job.prepare(None, {"shopify_rpc": demo_shopify(), "odoo_rpc": odoo}))
        return [ln.restock_qty for ln in sorted(reporte.lines, key=lambda ln: ln.sku)]

    # 2 unidades: en el Odoo demo el SKU mas corto pide 4, asi que el disparador queda
    # encendido en las dos corridas y lo que se mide es la CANTIDAD. Que el disparador
    # tambien se apague cuando lo entrante alcanza ya lo prueba
    # `tests/test_channel_en_transito.py`; esta sonda mide lo que prueba mas.
    descuenta, prueba = sonda_dos_corridas(_correr, en_camino=2.0,
                                           como="en las OC abiertas del ERP")
    return SondaNeteo(
        tool="channel_replenishment", entregable="jobs.channel_replenishment_job.ChannelLine",
        emite=CANTIDAD, campo_en_transito="open_purchase_qty_by_sku del ERP",
        descuenta=descuenta, prueba=prueba,
    )


def sonda_drp() -> SondaNeteo:
    """``drp``: las entradas ya programadas entran a la grilla ANTES del neto."""
    import pandas as pd

    from jobs import drp_job as job

    def _correr(en_camino: float) -> list[float]:
        filas = [
            {"branch": sucursal, "period": periodo, "demand": 10,
             "on_hand": oh, "lead_time": 1, "safety_stock": ss,
             "en_transito": en_camino if periodo == 2 else 0.0}
            for sucursal, oh, ss in (("East", 15, 5), ("West", 0, 0))
            for periodo in range(1, 5)
        ]
        reporte = job.run(job.prepare_records(pd.DataFrame(filas)))
        return [sum(bp.releases) for bp in reporte.branch_plans]

    # 10 unidades: lo justo para tapar un periodo. Un valor grande vaciaria el plan
    # entero y probaria menos que ver bajar cada sucursal por separado.
    descuenta, prueba = sonda_dos_corridas(_correr, en_camino=10.0,
                                           como="en la columna de entradas programadas")
    return SondaNeteo(
        tool="drp", entregable="jobs.drp_job.BranchPlan", emite=CANTIDAD,
        campo_en_transito="columna de entradas programadas (scheduled receipts)",
        descuenta=descuenta, prueba=prueba,
    )


# -- los que emiten un numero de compra y NO tienen por donde netear ---------------


def sonda_newsvendor() -> SondaNeteo:
    from jobs.newsvendor_job import NewsvendorLine

    return _sin_canal(
        "newsvendor", "jobs.newsvendor_job.NewsvendorLine", CANTIDAD,
        campos=_campos_declarados("newsvendor", NewsvendorLine),
        razon=("la compra de una temporada se dimensiona desde la razon critica y nada "
               "mas: no hay estante ni OC abierta en el contrato, asi que lo ya comprado "
               "para la temporada se vuelve a comprar entero"),
    )


def sonda_sop() -> SondaNeteo:
    from src.sop import PeriodPlan

    return _sin_canal(
        "sop", "src.sop.PeriodPlan", CANTIDAD,
        campos=_campos_declarados("sop", PeriodPlan),
        razon=("la posicion entra por ``opening_inventory``, que es una foto del estante: "
               "el plan de suministro del periodo no descuenta lo que ya esta pedido y "
               "llega dentro del horizonte"),
    )


def sonda_multi_echelon() -> SondaNeteo:
    from jobs.multi_echelon_job import EchelonLine

    return _sin_canal(
        "multi_echelon", "jobs.multi_echelon_job.EchelonLine", NIVEL,
        campos=_campos_declarados("multi_echelon", EchelonLine),
        razon=("emite un NIVEL objetivo (order_up_to) por etapa, no una cantidad: el "
               "neteo contra la posicion lo hace quien ejecuta, y el entregable no trae "
               "con que hacerlo"),
    )


def sonda_simulation() -> SondaNeteo:
    from jobs.simulation_job import SkuSimResult

    return _sin_canal(
        "simulation", "jobs.simulation_job.SkuSimResult", NIVEL,
        campos=_campos_declarados("simulation", SkuSimResult),
        razon=("busca el nivel objetivo que minimiza el costo simulado; como todo nivel, "
               "se aplica contra la posicion al ejecutar, y esa posicion no entra aca"),
    )


def sonda_hat_tension() -> SondaNeteo:
    from src.hats import Candidate, HatInputs

    return _sin_canal(
        "hat_tension", "src.hats.Candidate", CANTIDAD,
        campos=_campos_declarados("hat_tension", HatInputs, Candidate),
        razon=("``HatInputs`` lleva demanda, costo, lead time, terminos del proveedor y "
               "flete, y ninguna posicion: cada sombrero propone una Q sobre un estante "
               "que no ve"),
    )


def sonda_hat_settlement() -> SondaNeteo:
    from src.hats import Candidate, HatInputs

    return _sin_canal(
        "hat_settlement", "src.hats.Candidate", CANTIDAD,
        campos=_campos_declarados("hat_settlement", HatInputs, Candidate),
        razon=("el acuerdo se arma sobre los mismos ``HatInputs`` que la tension: la Q* "
               "que sale al HANDOFF no descuenta lo que ya viene en camino"),
    )


#: Los veredictos que MANDAN A COMPRAR. `green` es el unico que no: dice
#: explicitamente "ready without a reorder".
_VEREDICTOS_QUE_COMPRAN = ("red", "yellow")


def sonda_launch_readiness() -> SondaNeteo:
    """``launch_readiness``: el unico de los catorce que netea contra una FECHA.

    Los otros trece comparan una cantidad contra una posicion y les alcanza con
    ``on_order``. Aca la pregunta es si el stock esta disponible el dia del lanzamiento,
    asi que una cantidad sin fecha de llegada no puede contestarla: el en-transito solo
    cuenta con ETA <= fecha de lanzamiento. Por eso la sonda corre TRES veces y no dos.

    Y por eso el "valor se mueve" es otro: este tool no emite una cantidad sino un
    VEREDICTO, asi que lo que tiene que moverse es que deje de mandar a comprar.
    """
    from datetime import date

    from jobs.launch_readiness_job import LaunchInput, _assess_sku

    hoy = date(2026, 1, 1)
    lanzamiento = date(2026, 1, 31)                 # 30 dias por delante
    base = dict(product_id="SKU-L", launch_date=lanzamiento, lift_pct=0.0,
                has_coverage=True, on_hand=50.0, daily_demand=10.0,
                # El plazo (45d) NO entra antes del lanzamiento (30d): sin en-transito
                # el veredicto es rojo por construccion, que es el caso que motivo esto.
                lead_time_days=45.0)

    def _veredicto(**extra) -> str:
        return _assess_sku(LaunchInput(**base, **extra),
                           service_level=0.95, as_of=hoy).verdict

    sin = _veredicto()
    a_tiempo = _veredicto(on_order=ON_ORDER_SONDA * 5, on_order_eta=date(2026, 1, 20))
    tarde = _veredicto(on_order=ON_ORDER_SONDA * 5, on_order_eta=date(2026, 2, 15))

    descuenta = (sin in _VEREDICTOS_QUE_COMPRAN
                 and a_tiempo not in _VEREDICTOS_QUE_COMPRAN
                 # La otra mitad del contrato: lo que llega TARDE no puede acreditar.
                 # Sin esto, un tool que sumara el en-transito sin mirar la fecha
                 # pasaria esta sonda y seguiria mintiendo sobre el dia del lanzamiento.
                 and tarde in _VEREDICTOS_QUE_COMPRAN)
    return SondaNeteo(
        tool="launch_readiness", entregable="jobs.launch_readiness_job.LaunchLine",
        emite=VEREDICTO, campo_en_transito="columna on_order + ETA",
        descuenta=descuenta,
        prueba=(f"con {ON_ORDER_SONDA * 5:g} unidades ya emitidas que llegan ANTES del "
                f"lanzamiento el veredicto deja de mandar a comprar: {sin} -> {a_tiempo}; "
                f"con las mismas unidades llegando DESPUES no acredita: {sin} -> {tarde}"),
    )


#: tool -> sonda. Cada una corre el emisor y devuelve la evidencia; ninguna mira campos.
EMISORES: dict[str, Callable[[], SondaNeteo]] = {
    "channel_replenishment": sonda_channel,
    "ddmrp": sonda_ddmrp,
    "drp": sonda_drp,
    "excel_replenishment": sonda_excel,
    "hat_settlement": sonda_hat_settlement,
    "hat_tension": sonda_hat_tension,
    "inventory_optimization": sonda_inventory_optimization,
    "launch_readiness": sonda_launch_readiness,
    "multi_echelon": sonda_multi_echelon,
    "newsvendor": sonda_newsvendor,
    "odoo_replenishment": sonda_odoo,
    "shopify_replenishment": sonda_shopify,
    "simulation": sonda_simulation,
    "sop": sonda_sop,
}

#: tool -> por que NO emite un numero sobre el que alguien compre. La razon es el
#: contenido: un conjunto pelado invitaria a meter ahi lo que no se quiso sondear.
NO_EMISORES: dict[str, str] = {
    "abc_xyz": "clasifica SKUs y asigna politica por celda; no dimensiona ninguna compra",
    "acceptance_sampling": "dimensiona la MUESTRA a inspeccionar de un lote que ya llego",
    "audit_evidence": "arma un papel de trabajo re-ejecutable; no propone comprar nada",
    "cost_to_serve": "asigna costo de servir por segmento; no toca cantidades",
    "cross_impact": "mide una decision YA propuesta por otra area; no emite cantidad propia",
    "cycle_count": "programa conteos ciclicos sobre el stock existente",
    "data_quality": "audita el maestro de productos (duplicados, GTIN, completitud)",
    "dea": "rankea unidades comparables en una frontera de eficiencia",
    "digital_twin": "genera datasets simulados de una red; es fuente de datos, no consejo",
    "disruption_scan": "screening de noticias de disrupcion por proveedor; senal, no cantidad",
    "earned_value": "control de costo/plazo de proyecto (SV, CV, SPI, CPI)",
    "excess_obsolete": "clasifica el stock que YA esta y recomienda darlo de baja, no comprar",
    "facility_location": "ubica una instalacion; no dimensiona inventario",
    "fefo": "ordena la salida de lotes por vencimiento; mueve stock existente",
    "financial_kpis": "rollup financiero por SKU (turns, DIO, GMROI)",
    "flow_map": "reconstruye la red y mide el transito real por tramo",
    "forecast": "segmenta y pronostica demanda; la cantidad la decide la politica, aparte",
    "import_case": "unifica documentos de una importacion YA embarcada",
    "intake_scan": "clasifica los archivos del cliente y dice que tools corren hoy",
    "iso_ops": "detecta normas aplicables y evidencia de piso",
    "landed_cost": "calcula el costo puesto en destino por SKU; es un precio, no una cantidad",
    "leadership_chain": "puntua liderazgo sobre el modelo CHAIN",
    "learning_curve": "proyecta costo unitario por volumen (curva de aprendizaje)",
    "markdown_liquidation": "pone precio de liquidacion al stock que sobra; no compra",
    "network_design": "elige que sitios abrir y a quien sirve cada uno",
    "org_friction_audit": "audita los puntos de control del cliente; toda salida es pregunta",
    "price_intelligence": "compara precios propios contra observaciones de competidores",
    "price_watch": "onboarding y monitoreo read-only de un sitio competidor",
    "pricing": "estima elasticidad y recomienda precio",
    "procedure_audit": "audita el registro de procedimientos del cliente",
    "procedure_draft": "redacta un SOP bilingue, siempre sin aprobar",
    "queuing": "dimensiona servidores por punto de servicio (colas)",
    "reconciliation": "concilia conteo fisico contra sistema; mide diferencias",
    "repricing": "pasa una lista de precios propuesta por la guarda de pricing",
    "returns": "rankea la disposicion de lotes devueltos (restock/refurbish/liquidar)",
    "risk": "puntua un registro de riesgos y rankea mitigaciones",
    "scheduling": "ordena trabajos en un recurso (SPT/EDD)",
    "scor_iso_audit": "compone mediciones existentes en un EvidenceRecord",
    "service_scan": "mide el servicio que el cliente REALMENTE recibio",
    "slotting": "asigna SKUs a zonas de picking",
    "sourcing": "rankea proveedores para un volumen ya definido; elige a quien, no cuanto",
    "supplier_management": "segmenta proveedores en la matriz de Kraljic",
    "transportation": "elige modo de transporte por embarque ya definido",
    "vehicle_routing": "agrupa y secuencia paradas de reparto",
    "warehouse_layout": "genera un deposito 3D parametrico",
    "whatif": "barre supuestos sobre el costo de la politica; mide sensibilidad",
}


def barrer(tools: Iterable[str] | None = None) -> tuple[SondaNeteo, ...]:
    """Corre todas las sondas (o las de ``tools``) y devuelve la evidencia ordenada."""
    claves = sorted(EMISORES) if tools is None else sorted(tools)
    return tuple(EMISORES[clave]() for clave in claves)


def clasificacion(sondas: Iterable[SondaNeteo] | None = None) -> dict[str, list[str]]:
    """estado -> tools, para congelar el vector en un test igual que el puntaje."""
    por: dict[str, list[str]] = {"netea": [], "no_netea": [], "sin_sonda": [], "sin_canal": []}
    for s in barrer() if sondas is None else sondas:
        por[s.estado].append(s.tool)
    return {estado: sorted(tools) for estado, tools in por.items()}


def sondas_t05() -> tuple[SondaNeteo, ...]:
    """Las TRES que T05 puntua. Se nombran aca para que el corrector y el barrido no
    puedan divergir sobre que significa "netea": es la misma sonda, corrida una vez.

    T05 sigue puntuando el MEJOR de estos tres (su criterio pide ademas una OC por
    proveedor con fechas, que solo ``ddmrp`` arma), asi que el barrido no mueve el
    puntaje. Lo que el barrido agrega es lo que T05 nunca iba a ver: los otros once.
    """
    return (sonda_excel(), sonda_odoo(), sonda_ddmrp())
