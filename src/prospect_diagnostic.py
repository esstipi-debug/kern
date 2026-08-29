"""El espejo: el diagnostico gratuito sobre el archivo de alguien que no compro.

Todo lo demas de este repo mejora el producto para quien YA es cliente. Esto es
lo unico que se le muestra a un desconocido, y por eso las reglas son otras.

**Que hace.** Toma el archivo del prospecto -- su CSV, su Excel, con la mugre que
tenga -- y le devuelve cuatro cosas: en cuantos SKU se le concentra la venta,
cuanto del catalogo esta muerto, cuanta venta se le escapa por quiebre (en
RANGO), y -- lo unico que ningun competidor puede dar -- **su OTIF y su fill rate
reales**, cuando el dato alcanza.

**Por que gana.** Netstock exige estar en uno de sus ERP soportados; Inventory
Planner exige una plataforma de comercio; ForesightAI exige ser cliente de Cin7.
Los tres proyectan HACIA ADELANTE. Esto lee un archivo cualquiera y mide lo que
YA se incumplio. No le pedimos al prospecto que este en nada.

**Corre local.** La objecion "no subo mis datos a tu nube" es real, y esquivarla
es parte del producto, no una concesion. Nada en este modulo abre un socket, y
``tests/test_prospect_diagnostic.py`` lo clausura para probarlo en vez de
declararlo.

---

## La trampa ya pagada: el 43%

Una version anterior se valido el 2026-07-18 contra cuatro vendedores de Olist
(Brasil, R$) y Superstore (EE.UU., 1.862 SKUs); el patron aguanto cruzando
paises y monedas. Pero el baseline ingenuo ``z=0`` -- suponer que el prospecto
tiene inventario igual a su demanda media, o sea **50% de servicio** -- daba 43%
de la venta como recuperable. Ese numero no es creible y quema la conversacion
en el primer contacto.

La aritmetica, medida con la propia ``src.fill_rate``:

    servicio actual supuesto   z        L_N(z)    exceso vs. 95%   veces
    50% (el ingenuo)           0.0000   0.3989    0.3780           14,3x
    80% (nuestro techo)        0.8416   0.1116    0.0907            3,4x
    90% (nuestro piso)         1.2816   0.0473    0.0265            1,0x

Catorce veces. El arreglo no es afinar el numero: es **suponer un servicio
actual de 80-90% y devolver un rango**, nunca un punto. Un rango ancho dice la
verdad sobre lo que no sabemos; un punto miente con precision.

## La regla que gobierna el modulo

**Decir ``no medido`` cuando no se puede medir.** Ese silencio no es una falla
del informe: es el argumento de venta -- *"tu sistema no sabe si cumpliste"*. Se
hereda de ``src/order_fulfillment.py``, que ya distingue lo observado de lo que
un modelo implica, y de ``src/classification.py``, que ya declara si su ranking
ABC es de valor o apenas de unidades.

Nada aca reimplementa motor: la concentracion sale de ``classification``, la
funcion de perdida de ``fill_rate``, el servicio realizado de
``order_fulfillment``, y el archivo entra por ``jobs/intake.py`` con la higiene
de ``src/excel_hygiene.py`` ya aplicada.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from scipy.stats import norm

from src import order_fulfillment
from src.classification import value_basis
from src.fill_rate import normal_loss_standard
from src.forecastability import classify_series
from src.order_fulfillment import RealizedService

#: Periodo de agregacion. Semanal: un archivo de retail chico no tiene densidad
#: diaria suficiente para un sigma estable, y mensual deja menos de 24 puntos en
#: dos anios de historia.
_PERIOD = "W"
_DAYS_PER_PERIOD = 7.0

#: Cuantos periodos necesita un SKU antes de que su sigma signifique algo. Con
#: tres puntos el desvio es ruido, y el ruido multiplicado por la funcion de
#: perdida produce una cifra enorme y falsa -- exactamente el tipo de numero que
#: un operador con veinte anios de deposito detecta en dos segundos.
DEFAULT_MIN_PERIODS = 8

#: Plazo de reposicion cuando el archivo no trae ninguno. Catorce dias es el
#: mismo default que ``jobs/intake.py``, para que las dos rutas no discrepen.
DEFAULT_LEAD_TIME_DAYS = 14.0

#: Servicio que se le supone HOY al prospecto. El primero produce el techo del
#: rango y el segundo el piso. Nunca es un solo numero: ver la trampa del 43%.
DEFAULT_ASSUMED_SERVICE = (0.80, 0.90)

#: A donde se lo quiere llevar. 95% es el default del motor.
DEFAULT_TARGET_SERVICE = 0.95

#: Sin venta en seis meses. Se mide contra la ultima fecha DEL ARCHIVO.
DEFAULT_DEAD_WINDOW_DAYS = 180

REFUSED_FEW_PERIODS = "pocos_periodos"
#: La demanda del SKU no es aproximadamente normal, asi que la funcion de
#: perdida no aplica. Es la SEGUNDA via al artefacto del 43%: un SKU de cola
#: larga que vende cinco veces en tres anios tiene un sigma enorme contra una
#: media minuscula, y `normal_loss` le atribuye una recuperable que puede pasar
#: el 30% de lo que ese SKU factura. Con mil SKU asi -- que es como se ve un
#: catalogo real -- se reconstruye el 43% por otro camino.
REFUSED_INTERMITTENT = "demanda_intermitente"
REFUSED_NO_DEMAND = "sin_demanda"
REFUSED_NO_PRICE = "sin_precio"

#: Los cuatro campos de servicio. NO estan en el juego canonico de demanda de
#: ``jobs/intake.py`` porque ``normalize`` no los emite -- se pasan como
#: ``optional`` a ``detect_columns``, que ya esta parametrizado justo para esto
#: (mismo camino que usan las recepciones en ``webapp/demo_lead_time_scan.py``).
SERVICE_FIELDS = ("ordered_qty", "delivered_qty", "promised_date", "delivered_date")

#: Los cuadrantes de Syntetos-Boylan donde un stock de seguridad normal no es
#: legitimo. `erratic` entra igual: su timing es regular (ADI < 1.32) y solo
#: varia el tamanio, que es exactamente lo que un sigma sobre la serie captura.
_NO_NORMAL_QUADRANTS = frozenset({"intermittent", "lumpy"})


# --------------------------------------------------------------------------
# Resultados
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Concentration:
    """En cuantos SKU se le concentra la venta al prospecto."""

    skus_total: int
    skus_for_80pct: int
    share_of_skus_for_80pct: float
    revenue_total: float
    #: ``"value"`` si los precios discriminan; ``"units"`` si son todos iguales,
    #: en cuyo caso el ABC es un ranking de unidades y el informe debe decirlo.
    #: Es aritmetica correcta para un catalogo de precio uniforme y una mentira
    #: silenciosa cuando la columna de precio era un relleno.
    value_basis: str
    top_skus: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class DeadCatalog:
    """Cuanto del catalogo no se movio dentro de la ventana."""

    skus_total: int
    dead_skus: int
    share: float
    window_days: int
    #: La ultima fecha DEL ARCHIVO, no ``today``. Un export de tres anios atras
    #: anclado a hoy marca el catalogo entero como muerto, y el informe se
    #: vuelve una acusacion absurda que el prospecto descarta entera.
    anchor_date: date | None
    dead_skus_sample: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecoverableSales:
    """Venta que se recupera subiendo el servicio, SIEMPRE en rango.

    ``low``/``high`` son ``None`` -- no ``0.0`` -- cuando ningun SKU tenia
    evidencia suficiente. Un cero ahi seria la afirmacion de que no hay nada que
    recuperar, que es un hallazgo; ``None`` dice que no se midio, que es otro.
    """

    low: float | None
    high: float | None
    #: El supuesto de servicio actual detras de cada punta del rango. El nombre
    #: refiere a QUE PUNTA produce: peor servicio supuesto => mas recuperable.
    assumed_service_high: float
    assumed_service_low: float
    target_service: float
    lead_time_days: float
    skus_measured: int
    skus_refused: int
    refused_reasons: dict = field(default_factory=dict)
    method: str = ""


@dataclass(frozen=True)
class Diagnostic:
    """El espejo completo sobre un archivo."""

    concentration: Concentration
    dead_catalog: DeadCatalog
    recoverable: RecoverableSales
    #: Servicio realizado. ``provenance == "not_measured"`` cuando el archivo no
    #: trae con que -- y ese silencio es el hallazgo, no un hueco del informe.
    service: RealizedService
    #: Lo que se le saco a la planilla, cuando entro por Excel. ``None`` si el
    #: archivo era CSV o si no habia columna de producto reconocible.
    hygiene: object | None = None
    rows_in: int = 0
    rows_used: int = 0
    currency_hint: str = ""


# --------------------------------------------------------------------------
# Normalizacion de filas
# --------------------------------------------------------------------------

def _as_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if parsed is pd.NaT or pd.isna(parsed):
        return None
    return parsed.date()


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _clean_rows(rows: Sequence[dict]) -> list[dict]:
    """Filas utilizables: con fecha, producto y cantidad no negativa."""
    salida: list[dict] = []
    for row in rows:
        fecha = _as_date(row.get("date"))
        pid = row.get("product_id")
        qty = _as_float(row.get("quantity"))
        if fecha is None or pid is None or qty is None or qty < 0:
            continue
        if isinstance(pid, float) and math.isnan(pid):
            continue
        nueva = dict(row)
        nueva["date"] = fecha
        nueva["product_id"] = str(pid)
        nueva["quantity"] = qty
        nueva["unit_cost"] = _as_float(row.get("unit_cost"))
        salida.append(nueva)
    return salida


def _weekly_series(rows: Sequence[dict]) -> dict[str, list[float]]:
    """Demanda semanal por SKU, sobre el tramo que el ARCHIVO cubre para ese SKU.

    El tramo va de la primera a la ultima fecha en que ese SKU aparece, y los
    huecos se rellenan con cero. Anclar al rango del archivo entero en vez de al
    del SKU castigaria a un producto lanzado el mes pasado con cuarenta semanas
    de ceros que nunca existieron, e inflaria su sigma hasta una cifra de venta
    recuperable que no tiene ningun sustento.
    """
    if not rows:
        return {}
    por_sku: dict[str, dict[pd.Period, float]] = {}
    for row in rows:
        periodo = pd.Period(row["date"], freq=_PERIOD)
        balde = por_sku.setdefault(row["product_id"], {})
        balde[periodo] = balde.get(periodo, 0.0) + row["quantity"]

    series: dict[str, list[float]] = {}
    for pid, baldes in por_sku.items():
        primero, ultimo = min(baldes), max(baldes)
        rango = pd.period_range(primero, ultimo, freq=_PERIOD)
        series[pid] = [baldes.get(p, 0.0) for p in rango]
    return series


def _unit_price(rows: Sequence[dict]) -> dict[str, float | None]:
    """Precio representativo por SKU: media de los precios que el archivo trae."""
    acumulado: dict[str, list[float]] = {}
    for row in rows:
        precio = row.get("unit_cost")
        if precio is not None and precio > 0:
            acumulado.setdefault(row["product_id"], []).append(precio)
    return {
        pid: (sum(vs) / len(vs) if vs else None)
        for pid, vs in acumulado.items()
    }


# --------------------------------------------------------------------------
# 1. Concentracion
# --------------------------------------------------------------------------

def concentration(rows: Sequence[dict], *, top: int = 10) -> Concentration:
    """Concentracion de venta por SKU (la curva de Pareto del prospecto)."""
    limpias = _clean_rows(rows)
    if not limpias:
        return Concentration(0, 0, 0.0, 0.0, "units", ())

    precios = _unit_price(limpias)
    unidades: dict[str, float] = {}
    for row in limpias:
        unidades[row["product_id"]] = unidades.get(row["product_id"], 0.0) + row["quantity"]

    # ``value_basis`` decide si el ranking es de valor o apenas de unidades. Se
    # lo alimenta con el precio que el archivo trajo; donde no trajo ninguno se
    # usa 1.0, que es justamente el caso que hace que NO discrimine.
    items = [
        {"product_id": pid, "unit_cost": (precios.get(pid) or 1.0), "demand": [q]}
        for pid, q in unidades.items()
    ]
    basis = value_basis(items)

    valores = sorted(
        ((pid, q * (precios.get(pid) or 1.0)) for pid, q in unidades.items()),
        key=lambda par: par[1],
        reverse=True,
    )
    total = sum(v for _, v in valores)

    # Se cuenta sobre la acumulada ANTES del SKU, igual que ``classify_portfolio``:
    # el SKU que cruza el umbral se queda del lado importante.
    cuantos, acumulada = 0, 0.0
    if total > 0:
        for _pid, v in valores:
            if acumulada >= 0.80:
                break
            cuantos += 1
            acumulada += v / total

    return Concentration(
        skus_total=len(valores),
        skus_for_80pct=cuantos,
        share_of_skus_for_80pct=(cuantos / len(valores)) if valores else 0.0,
        revenue_total=total,
        value_basis="value" if basis.discriminates else "units",
        top_skus=tuple(valores[:top]),
    )


# --------------------------------------------------------------------------
# 2. Catalogo muerto
# --------------------------------------------------------------------------

def dead_catalog(
    rows: Sequence[dict],
    *,
    window_days: int = DEFAULT_DEAD_WINDOW_DAYS,
    sample: int = 10,
) -> DeadCatalog:
    """SKUs sin venta dentro de la ventana, anclada a la ultima fecha del archivo."""
    limpias = _clean_rows(rows)
    if not limpias:
        return DeadCatalog(0, 0, 0.0, window_days, None, ())

    ancla = max(row["date"] for row in limpias)
    ultima: dict[str, date] = {}
    for row in limpias:
        # Una fila con cantidad cero es presencia en el catalogo, no una venta.
        if row["quantity"] <= 0:
            continue
        pid = row["product_id"]
        if pid not in ultima or row["date"] > ultima[pid]:
            ultima[pid] = row["date"]

    todos = {row["product_id"] for row in limpias}
    muertos = sorted(
        pid for pid in todos
        if pid not in ultima or (ancla - ultima[pid]).days > window_days
    )
    return DeadCatalog(
        skus_total=len(todos),
        dead_skus=len(muertos),
        share=(len(muertos) / len(todos)) if todos else 0.0,
        window_days=window_days,
        anchor_date=ancla,
        dead_skus_sample=tuple(muertos[:sample]),
    )


# --------------------------------------------------------------------------
# 3. Venta recuperable -- el rango
# --------------------------------------------------------------------------

def _sigma(serie: Sequence[float]) -> float:
    """Desvio muestral (ddof=1). Con menos de dos puntos no hay dispersion."""
    n = len(serie)
    if n < 2:
        return 0.0
    media = sum(serie) / n
    return math.sqrt(sum((v - media) ** 2 for v in serie) / (n - 1))


def _shortfall_units(sigma_lt: float, z_actual: float, z_objetivo: float, ciclos: float) -> float:
    """Unidades anuales que se dejan de servir de mas, contra el objetivo.

    ``sigma_LT * [L_N(z_actual) - L_N(z_objetivo)]`` son las unidades que faltan
    por ciclo de mas que las que faltarian al servicio objetivo; por los ciclos
    del anio da el total anual. Es la identidad de la funcion de perdida normal
    de ``src/fill_rate.py``, no una formula nueva.
    """
    delta = float(normal_loss_standard(z_actual)) - float(normal_loss_standard(z_objetivo))
    return max(delta, 0.0) * sigma_lt * ciclos


def recoverable_sales(
    rows: Sequence[dict],
    *,
    assumed_service: tuple[float, float] = DEFAULT_ASSUMED_SERVICE,
    target_service: float = DEFAULT_TARGET_SERVICE,
    lead_time_days: float = DEFAULT_LEAD_TIME_DAYS,
    min_periods: int = DEFAULT_MIN_PERIODS,
) -> RecoverableSales:
    """Venta recuperable subiendo el servicio, en RANGO y nunca en un punto.

    ``assumed_service`` es ``(pesimista, optimista)``: el pesimista produce el
    techo del rango y el optimista el piso. Suponer 50% -- que el prospecto tiene
    exactamente su demanda media en gondola -- multiplica el resultado por 14 y
    es el origen del artefacto del 43%. Ver el docstring del modulo.
    """
    peor, mejor = assumed_service
    limpias = _clean_rows(rows)
    series = _weekly_series(limpias)
    precios = _unit_price(limpias)

    z_peor, z_mejor = float(norm.ppf(peor)), float(norm.ppf(mejor))
    z_objetivo = float(norm.ppf(target_service))
    periodos_lt = max(lead_time_days / _DAYS_PER_PERIOD, 1e-9)
    ciclos = 365.0 / max(lead_time_days, 1e-9)

    alto = bajo = 0.0
    medidos = 0
    rechazos = {
        REFUSED_FEW_PERIODS: 0,
        REFUSED_NO_DEMAND: 0,
        REFUSED_NO_PRICE: 0,
        REFUSED_INTERMITTENT: 0,
    }

    for pid, serie in series.items():
        if len(serie) < min_periods:
            rechazos[REFUSED_FEW_PERIODS] += 1
            continue
        if sum(serie) <= 0:
            rechazos[REFUSED_NO_DEMAND] += 1
            continue
        precio = precios.get(pid)
        if precio is None or precio <= 0:
            rechazos[REFUSED_NO_PRICE] += 1
            continue
        # Syntetos-Boylan: con el timing irregular (ADI >= 1.32) la demanda
        # durante el plazo deja de ser aproximadamente normal, que es justo lo
        # que `normal_loss` supone. Esos SKU necesitan Croston/TSB y una
        # distribucion discreta; aca se los declara en vez de contestarles mal.
        if classify_series(pid, serie).quadrant in _NO_NORMAL_QUADRANTS:
            rechazos[REFUSED_INTERMITTENT] += 1
            continue

        sigma_lt = _sigma(serie) * math.sqrt(periodos_lt)
        # Techo por SKU: nadie recupera mas de lo que ese SKU factura en el
        # tramo que el archivo cubre. Sin este tope, un SKU con dos picos y
        # cincuenta ceros produce un sigma que supera su propia venta anual.
        facturado = sum(serie) * precio
        alto += min(_shortfall_units(sigma_lt, z_peor, z_objetivo, ciclos) * precio, facturado)
        bajo += min(_shortfall_units(sigma_lt, z_mejor, z_objetivo, ciclos) * precio, facturado)
        medidos += 1

    hay = medidos > 0
    return RecoverableSales(
        low=(bajo if hay else None),
        high=(alto if hay else None),
        assumed_service_high=peor,
        assumed_service_low=mejor,
        target_service=target_service,
        lead_time_days=lead_time_days,
        skus_measured=medidos,
        skus_refused=sum(rechazos.values()),
        refused_reasons=rechazos,
        method=(
            f"funcion de perdida normal sobre demanda semanal; servicio actual "
            f"supuesto {mejor:.0%}-{peor:.0%}, objetivo {target_service:.0%}, "
            f"plazo {lead_time_days:g} dias"
        ),
    )


# --------------------------------------------------------------------------
# 4. Servicio realizado
# --------------------------------------------------------------------------

def _service(rows: Sequence[dict]) -> RealizedService:
    """OTIF y fill rate reales, o ``not_measured`` si el archivo no trae con que."""
    por_sku: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("ordered_qty") is None and row.get("delivered_qty") is None:
            continue
        por_sku.setdefault(str(row["product_id"]), []).append(row)

    if not por_sku:
        return order_fulfillment.not_measured("PORTFOLIO", order_lines=len(rows))

    registros = [
        order_fulfillment.measure_from_order_lines(pid, lineas)
        for pid, lineas in por_sku.items()
    ]
    return order_fulfillment.aggregate(registros)


# --------------------------------------------------------------------------
# 5. El diagnostico entero
# --------------------------------------------------------------------------

def _rows_from_path(path: str | Path) -> tuple[list[dict], object | None]:
    """Lee el archivo del prospecto y lo lleva al esquema canonico.

    Pasa por ``jobs/intake.py`` -- que detecta hoja, encabezado y separadores de
    miles LATAM y anglosajones, y aplica la higiene de Excel -- y le agrega los
    cuatro campos de servicio como ``optional``, que es para lo que
    ``detect_columns`` esta parametrizado.
    """
    from jobs import intake

    frame = intake.load_table(path)
    informe = frame.attrs.get(intake.HYGIENE_ATTR)
    mapping = intake.detect_columns(
        frame,
        required=(),
        optional=intake.CANONICAL_REQUIRED + intake.CANONICAL_OPTIONAL + SERVICE_FIELDS,
    )
    columnas = mapping.mapping
    if "date" not in columnas or "product_id" not in columnas or "quantity" not in columnas:
        return [], informe

    # Sin columna de costo pero con plata en el archivo, se recupera el precio
    # unitario real (importe / cantidad) en vez de dejarlo en None y rechazar
    # cada SKU por "sin precio". Es la misma recuperacion que hace
    # `intake._normalize_core`, con su mismo guard: "sales" es alias de
    # `quantity` Y de `revenue`, asi que si las dos apuntan a la misma columna
    # no hay importe que dividir -- solo la cantidad, dos veces.
    recuperar = (
        "unit_cost" not in columnas
        and "revenue" in columnas
        and columnas["revenue"] != columnas.get("quantity")
    )

    filas: list[dict] = []
    for registro in frame.to_dict("records"):
        fila = {canon: registro.get(origen) for canon, origen in columnas.items()}
        if recuperar:
            importe, cantidad = _as_float(fila.get("revenue")), _as_float(fila.get("quantity"))
            fila["unit_cost"] = (
                importe / cantidad if importe is not None and cantidad else None
            )
        filas.append(fila)
    return filas, informe


#: Lo que se imprime donde no hay con que medir. Es una frase y no un cero, y la
#: diferencia es el producto entero: un cero afirma que cumpliste el 0% de las
#: veces, y esto dice que tu sistema no lo sabe.
NO_MEDIDO = "no medido"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def render_report(diag: Diagnostic) -> str:
    """El informe que el prospecto lee, en ASCII puro.

    ASCII a proposito: esto se imprime por consola en la maquina del prospecto,
    y en Windows un guion largo revienta con ``UnicodeEncodeError`` (ver la
    seccion de gotchas de CLAUDE.md). El unico lugar donde no se puede permitir
    ese error es justo este.
    """
    c, m, r, s = diag.concentration, diag.dead_catalog, diag.recoverable, diag.service
    out: list[str] = []
    add = out.append

    add("=" * 68)
    add("  DIAGNOSTICO DE INVENTARIO")
    add("=" * 68)
    add(f"  Filas leidas: {diag.rows_in:,}   |   utilizables: {diag.rows_used:,}")
    if diag.hygiene is not None and getattr(diag.hygiene, "rows_dropped", 0):
        detalle = ", ".join(
            f"{motivo.replace('_', ' ')}: {cuantas}"
            for motivo, cuantas in getattr(diag.hygiene, "dropped", {}).items()
            if cuantas
        )
        add(f"  De tu planilla se descartaron {diag.hygiene.rows_dropped} filas ({detalle}).")
        add("  Se declaran aca en vez de restarlas en silencio de tu demanda.")
    add("")

    add("-- 1. Concentracion ------------------------------------------------")
    if c.skus_total == 0:
        add(f"  {NO_MEDIDO}: el archivo no trajo filas utilizables.")
    else:
        add(f"  {c.skus_for_80pct} de {c.skus_total} SKU "
            f"({_pct(c.share_of_skus_for_80pct)}) explican el 80% de la venta.")
        if c.value_basis == "units":
            add("  AVISO: todos los precios del archivo son iguales, asi que este")
            add("  ranking es por UNIDADES, no por valor. Con la columna de precio")
            add("  real el orden puede cambiar bastante.")
    add("")

    add("-- 2. Catalogo muerto ----------------------------------------------")
    if m.skus_total == 0:
        add(f"  {NO_MEDIDO}.")
    else:
        add(f"  {m.dead_skus} de {m.skus_total} SKU ({_pct(m.share)}) "
            f"sin una sola venta en {m.window_days} dias.")
        add(f"  Contado desde {m.anchor_date} -- la ultima fecha de TU archivo,")
        add("  no la de hoy: un export viejo no vuelve muerto a todo el catalogo.")
    add("")

    add("-- 3. Venta recuperable --------------------------------------------")
    if r.low is None or r.high is None:
        irregulares = r.refused_reasons.get(REFUSED_INTERMITTENT, 0)
        add(f"  {NO_MEDIDO}, y el motivo es el hallazgo.")
        add("")
        if irregulares:
            add(f"  {irregulares} de tus SKU venden de forma demasiado irregular")
            add("  -- semanas en cero y despues un pico -- como para que un stock")
            add("  de seguridad clasico signifique algo. Poner un numero ahi seria")
            add("  inventarlo. Esa cola larga es donde suele estar tu capital")
            add("  inmovilizado, y se trabaja distinto: por punto de pedido, no")
            add("  por pronostico.")
        else:
            add(f"  Ningun SKU tuvo evidencia suficiente ({r.skus_refused} rechazados).")
    else:
        add(f"  Entre {r.low:,.0f} y {r.high:,.0f} al anio.")
        add("")
        add("  Es un RANGO y no un numero porque no sabemos tu servicio actual:")
        add(f"  el piso supone que hoy cumplis {_pct(r.assumed_service_low)}, el techo que cumplis "
            f"{_pct(r.assumed_service_high)}.")
        add(f"  En los dos casos el objetivo es llevarte a {_pct(r.target_service)}.")
        add(f"  Medido sobre {r.skus_measured} SKU"
            + (f"; {r.skus_refused} no se pudo medir." if r.skus_refused else "."))
        if r.skus_refused:
            motivos = ", ".join(
                f"{k.replace('_', ' ')}: {v}" for k, v in r.refused_reasons.items() if v
            )
            add(f"  Motivos: {motivos}.")
            irregulares = r.refused_reasons.get(REFUSED_INTERMITTENT, 0)
            if irregulares:
                add(f"  Los {irregulares} irregulares no entran en la cifra de arriba:")
                add("  venden a los saltos y se trabajan por punto de pedido, no por")
                add("  pronostico. Contarlos habria inflado el numero.")
    add("")

    add("-- 4. Servicio realizado (OTIF y fill rate) ------------------------")
    if s.fill_rate is None and s.otif is None:
        add(f"  Fill rate: {NO_MEDIDO}")
        add(f"  OTIF:      {NO_MEDIDO}")
        add("")
        add("  Tu archivo no trae que cantidad se entrego ni en que fecha, asi")
        add("  que nadie -- ni tu sistema -- sabe si cumpliste. No es un hueco de")
        add("  este informe: es el hallazgo. Con esas dos columnas esto se mide.")
    else:
        add(f"  Fill rate: {_pct(s.fill_rate) if s.fill_rate is not None else NO_MEDIDO}")
        add(f"  OTIF:      {_pct(s.otif) if s.otif is not None else NO_MEDIDO}")
        add(f"  Sobre {s.lines_scored} lineas con datos, de {s.order_lines} leidas.")
        add("  Esto es lo que YA paso, medido -- no un pronostico.")
    add("")
    add("=" * 68)
    add("  Todo esto se calculo en esta maquina. El archivo no salio de aca.")
    add("=" * 68)
    return "\n".join(out)


def diagnose(
    source: str | Path | Sequence[dict],
    *,
    window_days: int = DEFAULT_DEAD_WINDOW_DAYS,
    assumed_service: tuple[float, float] = DEFAULT_ASSUMED_SERVICE,
    target_service: float = DEFAULT_TARGET_SERVICE,
    lead_time_days: float = DEFAULT_LEAD_TIME_DAYS,
    min_periods: int = DEFAULT_MIN_PERIODS,
) -> Diagnostic:
    """El espejo entero sobre un archivo (CSV o Excel) o sobre filas ya canonicas."""
    if isinstance(source, (str, Path)):
        filas, informe = _rows_from_path(source)
    else:
        filas, informe = list(source), None

    limpias = _clean_rows(filas)
    return Diagnostic(
        concentration=concentration(limpias),
        dead_catalog=dead_catalog(limpias, window_days=window_days),
        recoverable=recoverable_sales(
            limpias,
            assumed_service=assumed_service,
            target_service=target_service,
            lead_time_days=lead_time_days,
            min_periods=min_periods,
        ),
        service=_service(limpias),
        hygiene=informe,
        rows_in=len(filas),
        rows_used=len(limpias),
    )
