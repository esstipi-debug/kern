"""El puente entre los jobs que leen stock y la memoria del Tower (N1c).

**Por que existe.** ``scm_agent/monitors.py::run_all_monitors`` lee tres dominios de
``src.state`` -- ``stock``, ``forecast``, ``outcomes`` -- y el UNICO escritor de ``stock``
era el puente del marcador (``examples/run_world_sim.py``). En produccion nadie escribia:
el bucle de sensado del Control Tower corria sobre vacio y no podia emitir un evento
aunque el quiebre estuviera pasando. Es el hallazgo H1 de
``documentation/KERN_DISRUPCIONES_Y_DESAJUSTES.md``.

Los cinco jobs que YA leen una posicion de inventario (``inventory_optimization`` desde
``ead1243``, ``odoo_replenishment``, ``shopify_replenishment``, ``channel_replenishment``
y ``excel_replenishment``) tienen las cuatro cosas que el dominio ``stock`` exige. Este
modulo es el unico lugar donde se dice COMO se mapean, y esa es toda su razon de ser: un
mapeo copiado en cinco jobs son cinco convenciones que divergen en silencio.

**Identidad: la misma regla que ``jobs/forecast_cycle.py::store_from_params``.** Sin
``params["state_client_id"]`` no se escribe NADA, y nunca la particion compartida. Un id
invalido levanta ``ClientIdError`` en vez de degradar: dos clientes no pueden terminar
sanitizados en el mismo directorio. Cada llamador recibe una NOTA -- que se escribio y con
que ciclo, o por que no -- y la pone en el resumen que lee el operador.

**La ausencia se declara.** Una linea sin los cuatro datos no se escribe con ceros: se
omite y la nota dice cuantas y por que. Un ``avg_daily_demand`` de 0 inventado haria que
``rop_breach_monitor`` calcule dias de cobertura infinitos sobre un SKU que se esta
quedando sin stock.

**Limite declarado.** El dominio ``stock`` no tiene ``on_order`` y ``ColumnRule`` solo
modela columnas obligatorias: agregarla exige un ciclo de esquema en
``src/state/system_state.py`` y romperia al puente del marcador. Consecuencia honesta:
``rop_breach`` dispara sobre el ESTANTE, no sobre la posicion. El evento se rutea a
``inventory_optimization``, que SI netea cuando el archivo trae ``on_order`` -- pero el
CSV sintetico del constructor no lo trae. Queda como seguimiento: columnas opcionales en
``DOMAIN_COLUMNS``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from jobs.forecast_cycle import now_cycle_id
from src.state.record import record_outcome, record_stock
from src.state.store import StateStore

#: Dias por periodo cuando el job razona en semanas. Nombrado y no tipeado en cada
#: llamador para que "el periodo era semanal" sea una afirmacion de un solo lugar.
DAYS_PER_WEEK = 7.0

#: Dias del anio con los que se convierte ``periods_per_year`` a dias por periodo.
#: 365, no 365,25: el job de inventario tambien usa el anio civil.
DAYS_PER_YEAR = 365.0

#: Lo que se escribe en la columna ``tool`` de cada fila. El dominio ``stock`` no la
#: exige (``strict=False`` en el esquema), pero sin ella un snapshot no dice quien lo
#: produjo, y dos jobs escribiendo la misma particion se vuelven indistinguibles.
COLUMN_TOOL = "tool"


@dataclass(frozen=True)
class StockLine:
    """Una fila del dominio ``stock``, con los cuatro campos que el esquema exige.

    Existe para que el mapeo de cada job se valide en Python antes de llegar a pandera:
    un ``None`` que se cuela hasta el validador aparece como "columna con nulos" y no
    dice de que SKU ni de que job vino.
    """

    product_id: str
    on_hand: float
    #: El disparador que el job USO, en unidades. No "un punto de reorden" generico:
    #: cada job declara cual es el suyo en su propio builder, abajo.
    reorder_point: float
    #: Unidades por dia CALENDARIO. Es con lo que el Tower calcula dias de cobertura.
    avg_daily_demand: float
    #: Lo ya comprado que todavia no llego, o ``None`` si el job NO PUDO MEDIRLO.
    #:
    #: Es el unico campo opcional de la fila, y el unico donde ``None`` y ``0.0``
    #: significan cosas distintas: cero es "medido, y no viene nada" y manda a no
    #: restar; ausente es "no sabemos". Por eso no entra en ``_usable``: exigirlo
    #: dejaria sin escribir a todo job sin en-transito y el Tower volveria a sensar
    #: sobre vacio, que es el agujero que D vino a cerrar.
    #:
    #: Con el dato, ``src/alerting.py::detect_events`` netea el DISPARADOR contra la
    #: posicion y deja de emitir quiebres falsos sobre stock que ya esta comprado.
    on_order: float | None = None

    def as_row(self, tool: str) -> dict:
        fila = {
            "product_id": str(self.product_id),
            "on_hand": float(self.on_hand),
            "reorder_point": float(self.reorder_point),
            "avg_daily_demand": float(self.avg_daily_demand),
            COLUMN_TOOL: tool,
        }
        # La clave solo viaja cuando hay numero. Escribir `None` la volveria NaN al
        # pasar por parquet, que es lo mismo que no escribirla, pero ademas haria
        # creer que el job la midio.
        if self.on_order is not None:
            fila["on_order"] = float(self.on_order)
        return fila


def _en_camino(linea: object, campo: str) -> float | None:
    """Lo ya comprado que no llego, leido de la linea del job, o ``None``.

    Los cinco productores ya traen el dato con la semantica correcta -- ``None``
    cuando no se pudo leer el canal, nunca 0,0 -- asi que aca no se decide nada: se
    reenvia. Lo que se evita es que un ``0`` inventado por este puente llegue al Tower
    diciendo "medido, y no viene nada".

    Un valor negativo se descarta como ausente: no existe un en-transito negativo, y
    dejarlo pasar inflaria la posicion hacia abajo y volveria a disparar de mas.
    """
    valor = getattr(linea, campo, None)
    if valor is None:
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if numero != numero or numero < 0:      # NaN o negativo
        return None
    return numero


def _usable(product_id: object, on_hand: object, trigger: object, daily: object) -> bool:
    """Los cuatro datos estan y son numeros no negativos.

    Un ``None`` en cualquiera de los tres numeros descalifica la fila entera. No se
    rellena: ``on_hand`` ausente no es cero (nadie conto el estante), un disparador
    ausente no es cero (pediria siempre) y una demanda diaria ausente no es cero (daria
    cobertura infinita justo sobre el SKU del que no se sabe nada).
    """
    if not product_id:
        return False
    for value in (on_hand, trigger, daily):
        if value is None:
            return False
        try:
            if float(value) < 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


# -- un builder por job: el mapeo, dicho una sola vez -----------------------------


def stock_lines_from_inventory(report, *, periods_per_year: float | None = None) -> list[StockLine]:
    """``inventory_optimization``: solo los SKU que trajeron posicion en el archivo.

    ``on_hand`` = ``rec.on_hand`` (``None`` en un SKU sin columna de stock: se omite);
    ``reorder_point`` = ``rec.reorder_point``, el disparador que el job calculo;
    ``avg_daily_demand`` = ``rec.forecast`` / dias del periodo, que salen de
    ``periods_per_year`` (52 -> 7,02 dias). Un ``(R,S)`` tambien trae ``reorder_point``,
    asi que no hay rama especial.
    """
    per_year = float(periods_per_year or report.params.get("periods_per_year") or 52.0)
    period_days = DAYS_PER_YEAR / per_year if per_year > 0 else DAYS_PER_WEEK
    lines = []
    for rec in report.recommendations:
        daily = rec.forecast / period_days if period_days > 0 else None
        if not _usable(rec.product_id, rec.on_hand, rec.reorder_point, daily):
            continue
        lines.append(StockLine(rec.product_id, float(rec.on_hand), float(rec.reorder_point),
                               float(daily), _en_camino(rec, "on_order")))
    return lines


def stock_lines_from_replenishment(lines: Iterable) -> list[StockLine]:
    """``odoo_replenishment`` / ``shopify_replenishment``: la linea compartida
    ``src.connectors.replenish.ReplenishmentLine``.

    ``reorder_point`` = ``line.target``, y no es una aproximacion: este plan pide cuando
    la posicion cae por debajo del objetivo de cobertura, asi que el objetivo ES el
    disparador. ``avg_daily_demand`` = ``line.avg_daily_demand``, la tasa por dia
    calendario que el conector mide (NO ``forecast_per_period``, cuyo periodo es un
    dia-con-pedidos). Una linea ``insufficient_history`` sale con ``target`` 0 y se
    omite: escribirla diria que su punto de reorden es cero.
    """
    out = []
    for line in lines:
        if getattr(line, "status", "ok") != "ok":
            continue
        if not _usable(line.sku, line.on_hand, line.target, line.avg_daily_demand):
            continue
        out.append(StockLine(line.sku, float(line.on_hand), float(line.target),
                             float(line.avg_daily_demand), _en_camino(line, "on_order")))
    return out


def stock_lines_from_channel(report) -> list[StockLine]:
    """``channel_replenishment``: el estante del ERP, el objetivo del canal.

    ``on_hand`` = ``line.erp_on_hand`` (el ERP es el sistema de registro del stock; el
    canal es de donde sale la demanda, y donde los dos discrepan el job ya emite una
    ``StockDivergence`` aparte). ``reorder_point`` = ``line.target``, mismo argumento que
    arriba. ``avg_daily_demand`` = la tasa diaria del canal.
    """
    out = []
    for line in report.lines:
        if not _usable(line.sku, line.erp_on_hand, line.target, line.avg_daily_demand):
            continue
        out.append(StockLine(line.sku, float(line.erp_on_hand), float(line.target),
                             float(line.avg_daily_demand), _en_camino(line, "on_order")))
    return out


def stock_lines_from_excel(report, rows: Iterable) -> list[StockLine]:
    """``excel_replenishment``: la planilla del cliente, y su limite.

    ``on_hand`` = ``line.on_hand``; ``reorder_point`` = ``line.trigger`` cuando lo hay
    (modo punto-de-reorden, o un objetivo optimizado por Kern), y ``line.target`` cuando
    no (modo cobertura, donde no existe el concepto de disparador).

    ``avg_daily_demand`` sale de ``row.demand_per_period`` de la planilla, dividido por
    los dias del periodo. **En modo punto-de-reorden la planilla no tiene columna de
    demanda, y entonces NO se escribe nada**: el dominio ``stock`` exige una tasa diaria
    y derivarla del punto de reorden seria inventarla. La nota lo dice; es la unica de
    las cinco fuentes que puede quedarse sin escribir por diseno.
    """
    demand_by_sku = {
        row.sku: row.demand_per_period
        for row in rows
        if getattr(row, "demand_per_period", None) is not None
    }
    out = []
    for line in report.lines:
        per_period = demand_by_sku.get(line.sku)
        daily = None if per_period is None else float(per_period) / DAYS_PER_WEEK
        trigger = line.trigger if line.trigger is not None else line.target
        if not _usable(line.sku, line.on_hand, trigger, daily):
            continue
        out.append(StockLine(line.sku, float(line.on_hand), float(trigger), float(daily),
                             _en_camino(line, "on_order")))
    return out


# -- la escritura, con la identidad y la nota -------------------------------------


@dataclass(frozen=True)
class StateNote:
    """Que paso con la escritura, en una frase y en tres banderas.

    ``text`` es la frase para el operador. Las banderas existen para que un llamador
    no tenga que olfatear esa frase: ``store_named`` separa "la memoria esta apagada
    por configuracion" (el caso normal, que no merece una linea en cada resumen) de
    "habia donde escribir"; ``recorded`` dice si efectivamente se escribio.
    """

    text: str
    store_named: bool
    recorded: bool
    cycle_id: str = ""

    def __str__(self) -> str:  # para interpolar la nota sin pensar
        return self.text


def _no_client(que: str) -> StateNote:
    return StateNote(
        f"no state_client_id: {que} NOT recorded (the Tower senses on nothing until a "
        "run names whose state this is)",
        store_named=False,
        recorded=False,
    )


def record_stock_snapshot(
    store: StateStore | None,
    *,
    tool: str,
    lines: Sequence[StockLine],
    n_candidates: int | None = None,
    cycle_id: str | None = None,
) -> StateNote:
    """Escribe UN snapshot ``stock`` y devuelve la nota para el resumen del job.

    ``store=None`` es el caso normal, no un error: la memoria es opt-in por
    ``params["state_client_id"]`` y sin ella no se escribe (ni en la particion
    compartida). Sin lineas usables tampoco se escribe -- un snapshot vacio se leeria
    como "no hay stock", que es una afirmacion distinta de "no se pudo medir".

    ``n_candidates`` (cuantas lineas trajo el reporte) solo sirve para que la nota pueda
    decir cuantas se omitieron. El ``cycle_id`` es ISO-8601 UTC
    (``forecast_cycle.now_cycle_id``), la misma convencion que ya usa la memoria de
    ciclos: ordena cronologicamente y convive con los ciclos enteros del marcador.
    """
    total = len(lines) if n_candidates is None else int(n_candidates)
    omitted = max(0, total - len(lines))
    if store is None:
        return _no_client("stock snapshot")
    if not lines:
        return StateNote(
            f"stock snapshot NOT recorded: none of the {total} line(s) carried on-hand, a "
            "trigger and a daily demand rate (the three the state domain requires)",
            store_named=True, recorded=False,
        )
    cycle = cycle_id or now_cycle_id()
    record_stock(store, cycle, [line.as_row(tool) for line in lines])
    text = f"stock snapshot recorded as cycle {cycle} ({len(lines)} SKU(s))"
    if omitted:
        text += f"; {omitted} line(s) omitted for missing on-hand / trigger / daily demand"
    return StateNote(text, store_named=True, recorded=True, cycle_id=cycle)


# -- D2: el insumo real de los dos monitores huerfanos ----------------------------


#: La metrica con la que el dominio ``outcomes`` guarda demanda realizada. La fija
#: ``scm_agent/monitors.py::forecast_error_out_of_band_monitor`` y la escribe hoy el
#: puente del marcador; este modulo la escribe desde produccion con el mismo nombre.
METRIC_ACTUAL_QTY = "actual_qty"

#: Plazo OBSERVADO de una recepcion, en dias. Lo lee
#: ``scm_agent/monitors.py::lead_time_drift_monitor``.
METRIC_LEAD_TIME_DAYS = "lead_time_days"


def _demand_rows(tool: str, points_by_sku: Mapping[str, Sequence[tuple[str, float]]]) -> list[dict]:
    rows = []
    for sku, points in sorted(points_by_sku.items()):
        for when, value in points:
            if not str(when).strip():
                continue
            rows.append({"product_id": str(sku), "tool": tool, "metric": METRIC_ACTUAL_QTY,
                         "value": float(value), "period": str(when)})
    return rows


def _lead_time_rows(tool: str, days_by_sku: Mapping[str, Sequence[float]]) -> list[dict]:
    return [
        {"product_id": str(sku), "tool": tool, "metric": METRIC_LEAD_TIME_DAYS,
         "value": float(days)}
        for sku, observations in sorted(days_by_sku.items())
        for days in observations
        if days is not None and float(days) >= 0
    ]


def record_outcomes(
    store: StateStore | None,
    *,
    tool: str,
    points_by_sku: Mapping[str, Sequence[tuple[str, float]]] | None = None,
    lead_times_by_sku: Mapping[str, Sequence[float]] | None = None,
    cycle_id: str | None = None,
) -> StateNote:
    """Escribe UN ciclo de ``outcomes`` con lo que esta corrida midio.

    **Las dos metricas van en el MISMO ciclo, y no es cosmetico.**
    ``lead_time_drift_monitor`` compara los DOS snapshots mas recientes de ``outcomes``;
    si la demanda y los plazos se escribieran en ciclos separados, el snapshot de arriba
    seria el de demanda, su mapa de plazos saldria vacio y el monitor no podria disparar
    nunca -- con los plazos medidos, escritos y ahi. Es la misma forma que ya usa el
    puente del marcador (``examples/run_world_sim.py``), y ahora por la misma razon.

    - ``points_by_sku``: demanda REALIZADA por (SKU, fecha), de
      ``demand_points_by_sku(orders)``. Es lo que a ``forecast_error_out_of_band`` le
      faltaba en produccion: ``forecast_cycle.freeze`` escribe el pronostico y el MAPE,
      nunca lo realizado. La columna ``period`` lleva la FECHA del pedido, no un ordinal,
      para que quien reconstruya la serie no tenga que inventarle un dia a cada punto
      (regla 7, procedencia total).
    - ``lead_times_by_sku``: plazos OBSERVADOS, uno por recepcion. El medido, no el
      declarado: una media sobre ``supplierinfo.delay`` no se mueve nunca y el monitor
      compara justamente medias.
    """
    rows = _demand_rows(tool, points_by_sku or {}) + _lead_time_rows(tool, lead_times_by_sku or {})
    que = " and ".join(
        parte for parte, activo in (("demand outcomes", points_by_sku is not None),
                                    ("lead time outcomes", lead_times_by_sku is not None))
        if activo
    ) or "outcomes"
    if store is None:
        return _no_client(que)
    if not rows:
        return StateNote(f"{que} NOT recorded: nothing measurable in this run",
                         store_named=True, recorded=False)
    cycle = cycle_id or now_cycle_id()
    record_outcome(store, cycle, rows)
    n_sku = len({r["product_id"] for r in rows})
    return StateNote(f"{que} recorded as cycle {cycle} ({len(rows)} row(s) over {n_sku} SKU(s))",
                     store_named=True, recorded=True, cycle_id=cycle)
