"""Intake adapter — map arbitrary client demand data to the canonical schema.

Real client files (ERP exports, transaction logs, Kaggle-style sets) arrive in
their own shapes. This detects the date / product / quantity (and optional unit
cost / lead time) columns by header aliases, then normalizes to one row per
product per period (weekly by default) ready for the engine.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from src import excel_hygiene

CANONICAL_REQUIRED = ("date", "product_id", "quantity")
# ``revenue`` is detected but never emitted: it exists only to recover a real
# unit cost (revenue / quantity) from files that carry money but no cost column.
CANONICAL_OPTIONAL = ("unit_cost", "lead_time_days", "lead_time_std", "revenue")

# Cuatro alias (`dia`, `producto`, `demanda`, `plazo_de_entrega`) vienen de
# jobs/column_mapping.py, un segundo normalizador muerto -- sin un solo
# importador fuera de su propio test -- que se borro en este commit. Eran los
# unicos que tenia y esta tabla no: encabezados en espanol pelado, que es
# justo lo que exporta un ERP de LatAm. Dos de los suyos NO se portaron:
# `entrega` es ambiguo (puede ser una fecha de entrega, no un plazo en dias, y
# confundirlas es el error que RECEIPT_REQUIRED existe para evitar), y
# `lead_time_std` estaba listado alli bajo lead_time_days, que es un defecto:
# aca la dispersion es su propio canonico.
ALIASES: dict[str, list[str]] = {
    "date": ["date", "order_date", "orderdate", "invoicedate", "invoice_date", "week", "period", "ds", "day", "timestamp", "datetime", "fecha", "fecha_documento", "fecha_venta", "fecha_factura", "data", "data_emissao", "dia"],
    "product_id": ["product_id", "productid", "sku", "item", "item_id", "itemid", "stockcode", "stock_code", "product", "material", "article", "part_number", "upc", "codigo", "codigo_producto", "cod_producto", "articulo", "codigo_articulo", "referencia", "clave_producto", "produto", "codigo_produto", "producto"],
    "quantity": ["quantity", "qty", "sales", "demand", "units", "unit_sales", "unitsales", "order_qty", "orderqty", "volume", "sold", "units_sold", "cantidad", "cant", "cantidad_vendida", "unidades", "quantidade", "demanda"],
    "unit_cost": ["unit_cost", "unitcost", "price", "unitprice", "unit_price", "cost", "sell_price", "sellprice", "selling_price", "costo", "costo_unitario", "precio", "precio_unitario", "custo", "preco"],
    "lead_time_days": ["lead_time_days", "leadtimedays", "lead_time", "leadtime", "lead", "lt_days", "supplier_lead_time", "declared_lead_time_days", "promised_lead_time_days", "planned_lead_time", "plazo", "plazo_dias", "plazo_entrega", "plazo_de_entrega", "plazo_declarado", "dias_entrega", "prazo", "prazo_entrega"],
    # Lead-time dispersion (sigma_LT). Same alias set jobs/launch_readiness_job.py
    # already accepts, so a file that works there works here too.
    "lead_time_std": ["lead_time_std", "leadtimestd", "lead_std", "sigma_lead", "sigma_l"],
    "revenue": ["revenue", "sales", "sales_amount", "amount", "net_sales", "line_total", "total"],
    # Precio de VENTA, canonico propio desde que `pricing` declaro su contrato.
    # La lista es corta a proposito: `unit_cost` de arriba ya reclama "price",
    # "unit_price", "sell_price", "precio" y "preco", y en `_map_headers` gana
    # el canonico que aparece primero en el juego (los perfiles van antes que el
    # registry). Repetir esos alias aca no se los quitaria, solo pondria dos
    # duenos para el mismo encabezado. Quedan entonces los terminos que NO son
    # ambiguos entre costo y precio; un archivo cuya unica columna de dinero se
    # llama `precio` sigue resolviendo a unit_cost, y `pricing` sale rojo. Eso es
    # una deuda del par costo/precio en la tabla de arriba, no de esta entrada.
    "price": ["price", "sale_price", "saleprice", "sales_price", "retail_price", "list_price", "precio_venta", "preco_venda", "pvp"],
    # Below: added for jobs/intake_scan_job.py (the intake_scan folder-audit job),
    # which maps headers against a wider canonical set (src.intake_scan.PROFILES)
    # than the demand-normalization set above. "sku" is NOT one of these entries:
    # it is not a distinct canonical name anywhere in this codebase, it is a
    # header alias for "product_id" (already listed above) -- a client's "sku"
    # column maps straight to canonical product_id, same as "item_id"/"upc"/etc.
    "name": ["name", "product_name", "item_name", "description", "desc", "nombre", "nombre_producto", "descripcion", "detalle", "glosa", "nome", "descricao"],
    "gtin": ["gtin", "ean", "upc_code", "barcode", "gtin_code"],
    "on_hand": ["on_hand", "onhand", "stock_on_hand", "qty_on_hand", "inventory_on_hand", "current_stock", "stock_qty", "stock_quantity", "stock", "stock_actual", "existencia", "existencias", "saldo", "disponible", "estoque"],
    # Spanish added 2026-08-21: a LatAm export ("demanda_diaria") reached
    # detect_columns and matched product_id/on_hand/unit_cost, then died on this
    # one field. The table was rich in Spanish everywhere except the two below.
    "daily_demand": ["daily_demand", "avg_daily_demand", "demand_daily", "mean_daily_demand", "demanda_diaria", "demanda_media_diaria", "consumo_diario", "consumo_medio_diario"],
    "days_since_last_sale": ["days_since_last_sale", "days_since_sale", "last_sale_days", "dsls", "dias_sin_venta", "dias_sin_movimiento", "dias_sin_rotacion"],
    "cogs": ["cogs", "cost_of_goods_sold", "cost_of_goods"],
    "avg_inventory_value": ["avg_inventory_value", "average_inventory_value", "avg_inv_value", "inventory_value_avg"],
    "gross_margin": ["gross_margin", "grossmargin", "margin"],
    "units_sold": ["units_sold", "unitssold", "qty_sold"],
    "units_on_hand": ["units_on_hand", "unitsonhand"],
    "net_sales": ["net_sales", "netsales", "net_revenue", "venta_neta", "importe_neto"],
    # Abajo: el export de RECEPCIONES (webapp/demo_lead_time_scan.py). Son campos
    # canonicos propios y no alias de los de arriba porque la distincion es justo
    # la que da valor: `date` acepta un "date" pelado, y en un export de compras
    # esa columna tanto puede ser la fecha de la orden como la de la recepcion.
    # Confundirlas invierte el signo del plazo medido, asi que ninguna de las dos
    # acepta un encabezado ambiguo: o el archivo las nombra, o no se mide.
    "supplier": ["supplier", "supplier_name", "supplier_id", "vendor", "vendor_name", "partner_id", "proveedor", "nombre_proveedor", "razon_social_proveedor", "fornecedor"],
    "order_date": ["order_date", "orderdate", "po_date", "podate", "issue_date", "date_order", "ordered_at", "fecha_orden", "fecha_pedido", "fecha_oc", "fecha_emision_oc", "data_pedido"],
    "receipt_date": ["receipt_date", "received_date", "receipt_dt", "delivery_date", "actual_delivery_date", "date_done", "fecha_recepcion", "fecha_recibido", "fecha_entrega", "fecha_llegada", "data_recebimento"],
    "po_id": ["po_id", "po", "po_number", "order_id", "purchase_order", "purchase_order_id", "numero_oc", "num_oc", "orden_compra", "folio", "pedido"],
    # -- Lote 2: inventario y politica de reposicion --------------------------
    # Cada alias de aca sale de la tupla `_*_COLS` del job que declara la columna,
    # nunca inventado. Tres reglas al portarlas, porque una tabla de alias mal
    # armada produce el falso VERDE que el scan existe para evitar:
    #
    #  1. Un termino que ya pertenece a un canonico de arriba no se repite: en
    #     `_map_headers` gana el canonico que aparece primero, asi que repetirlo
    #     no se lo quita, solo pone dos duenos para el mismo encabezado. Por eso
    #     `lead_time`/`lead` quedan con `lead_time_days`, `period`/`week` con
    #     `date`, y `demand` con `quantity`. El nombre canonico exacto igual
    #     resuelve: los nombres exactos se reclaman antes que los alias.
    #  2. Nada de alias de una o dos letras (`h`, `R`, `lt`, `ss`, `vf`). Una
    #     columna llamada "h" en un archivo de cliente es casi seguro otra cosa.
    #     El sniffer del job los sigue aceptando cuando la tool corre de verdad;
    #     esta tabla solo gobierna el scan.
    #  3. Los terminos que se disputan DOS canonicos nuevos se arbitran aca, no
    #     por orden de iteracion: `lot` es de `lot_id` y `moq` es de `moq`. Y
    #     `location`/`node` no van a ninguno -- los reclamaban `stage` y `branch`
    #     por igual, y elegir uno haria que una tool salga verde con el archivo
    #     de la otra.
    "mean_demand": ["mean_demand", "mean", "avg_demand", "demand_mean", "expected_demand", "forecast"],
    "std_demand": ["std_demand", "std", "sigma", "demand_std", "stdev", "demand_sigma"],
    "salvage_value": ["salvage_value", "salvage"],
    "goodwill": ["goodwill", "shortage_penalty", "stockout_penalty"],
    # Simulation measures its lead time in PERIODS, not days, so it gets its own
    # canonical rather than sharing `lead_time_days`: the two are different
    # quantities and merging them would silently reinterpret a client's days as
    # periods. It is deliberately NOT called `lead_time`: a canonical by that name
    # exact-matches the generic header "Lead Time" and takes it away from
    # `lead_time_days`, which is what almost every other tool needs -- a regression
    # this table shipped for exactly one commit.
    "lead_time_periods": ["lead_time_periods"],
    "holding_cost": ["holding_cost", "holding_cost_per_period", "holding"],
    "order_cost": ["order_cost", "fixed_order_cost", "ordering_cost", "setup_cost"],
    "backorder_cost": ["backorder_cost", "shortage_cost", "stockout_cost", "penalty"],
    "review_period": ["review_period", "review"],
    "stage": ["stage", "echelon"],
    "branch": ["branch", "warehouse", "site"],
    "period": ["period", "month", "bucket"],
    "safety_stock": ["safety_stock", "safety"],
    "lot_size": ["lot_size"],
    "adu": ["adu", "avg_daily_usage", "average_daily_usage", "daily_usage"],
    "dlt": ["dlt", "decoupled_lead_time"],
    "ltf": ["ltf", "lead_time_factor"],
    "vf": ["vf", "variability_factor"],
    "moq": ["moq", "min_order_qty"],
    "order_cycle_days": ["order_cycle_days", "order_cycle", "order_cycle_period"],
    "on_order": ["on_order", "open_po", "open_orders"],
    "qualified_demand": ["qualified_demand", "qualified", "open_demand", "sales_orders"],
    "lot_id": ["lot_id", "lot", "batch", "batch_id"],
    # "days" a secas queda afuera a proposito: es de la tupla de fefo, pero un
    # encabezado llamado "days" en una carpeta cualquiera no es evidencia de nada.
    "days_to_expiry": ["days_to_expiry", "shelf_life_days", "days_left", "ttl_days"],
    # -- Lote 3: deposito, conteo y auditoria ---------------------------------
    # Mismas tres reglas del lote 2. Ademas se podan los terminos genericos de una
    # palabra que en un archivo de cliente pueden ser cualquier cosa: "count" (puede
    # ser un conteo de filas), "actual", "system", "book", "order". Y "category"
    # queda AFUERA de `reason` aunque este en la tupla de returns: en un maestro de
    # productos una columna `category` es la categoria del producto, no el motivo de
    # una devolucion, y aceptarla es exactamente el falso verde que el scan evita.
    "system_qty": ["system_qty", "book_qty", "on_hand_system"],
    "physical_qty": ["physical_qty", "physical", "counted"],
    "returned_units": ["returned_units", "returns"],
    "reason": ["reason", "return_reason"],
    "resale_value": ["resale_value", "resale"],
    "sellable": ["sellable", "resellable"],
    "order_id": ["order_id", "invoice", "basket"],
    "unit_volume": ["unit_volume", "required_space", "cube"],
    # -- Lote 4: red, geometria y flujo ---------------------------------------
    # Un matiz de la regla 1 que aparecio aca: un canonico que figura en esta tabla
    # pero que NINGUNA tool declara no participa de `_map_headers` -- el juego
    # canonico del scan son los perfiles mas lo declarado por el registry, y hoy
    # `receipt_date`, `order_date` y `po_id` quedan afuera (los usa
    # `detect_columns` con una lista explicita, otro camino). Por eso `fecha_llegada`
    # puede darse a `arrival_date` sin conflicto vivo, aunque `receipt_date` tambien
    # lo liste. Si alguna vez una tool declara `receipt_date`, ESTE es el par a
    # resolver: en un export de recepciones esa fecha es la del PO, y en uno de
    # movimientos es la llegada de la pierna. Queda anotado, no escondido.
    "longitude": ["longitude", "lon", "x_coord", "easting"],
    "latitude": ["latitude", "lat", "y_coord", "northing"],
    "role": ["role", "node_type"],
    "fixed_cost": ["fixed_cost", "open_cost", "annual_cost"],
    "capacity": ["capacity", "throughput", "max_load"],
    "weight_kg": ["weight_kg", "weight", "gross_weight"],
    "distance_km": ["distance_km", "distance", "lane_km"],
    "shipment_id": ["shipment_id", "shipment"],
    # `destination` se lo lleva el canonico de flow_map, no el `lane` de
    # transportation: un destino es un lugar y una lane es un par origen-destino.
    "lane": ["lane", "route", "origin_dest"],
    "order_value": ["order_value"],
    "departure_date": ["departure_date", "fecha_salida", "fecha_despacho", "dispatch_date", "ship_date"],
    "arrival_date": ["arrival_date", "fecha_llegada"],
    "origin": ["origin", "origen", "desde", "from_location"],
    "destination": ["destination", "destino", "hacia", "to_location"],
    # -- Lote 5: proveedores y abastecimiento ---------------------------------
    # `origin` queda con el canonico de flow_map (un origen es un punto de una
    # pierna, no el pais de un proveedor), y de `annual_value` se poda "value" y
    # "spend" pelados: en un maestro cualquiera una columna `value` puede ser
    # cualquier plata.
    "country": ["country", "pais", "hq_country", "region"],
    "annual_value": ["annual_value", "annual_spend", "spend_usd", "annual_value_usd", "purchase_value", "gasto_anual"],
    "on_time": ["on_time", "on_time_delivery", "ontime"],
    "in_full": ["in_full", "infull"],
    "defects": ["defects", "rejects", "ppm_defects", "defective_units"],
    "aql": ["aql", "acceptable_quality"],
    "ltpd": ["ltpd", "rejectable_quality", "rql"],
    "freight": ["freight", "shipping", "shipping_cost"],
    "insurance": ["insurance"],
    "duty_rate": ["duty_rate", "duty", "tariff_rate"],
    "handling": ["handling"],
    "broker_fee": ["broker_fee", "broker", "customs_fee"],
    "incoterm": ["incoterm"],
    "single_source": ["single_source", "sole_source", "single_sourced", "sole_sourced"],
    "defect_ppm": ["defect_ppm", "defect_rate", "reject_rate"],
    "financial_risk": ["financial_risk", "credit_risk", "supplier_financial_risk"],
    "geo_risk": ["geo_risk", "country_risk", "geopolitical_risk"],
    # -- Lote 6: ops, proyecto, riesgo y analisis -----------------------------
    # Este lote es el que mas terminos genericos trae en sus tuplas, y casi
    # ninguno sobrevive: "value" lo querian `impact_value` y `base`, "rate" lo
    # querian `learning_rate` y `service_rate`, "time" lo queria
    # `processing_time`, y "min"/"max" los querian `low`/`high`. Un encabezado
    # llamado `value` o `rate` en la carpeta de un cliente no es evidencia de
    # nada, y darselo a uno de los dos seria decidirlo por orden de iteracion.
    # "task" queda con `task` y no con `job`, y "name" no va a ninguno: lo
    # reclamaban cuatro tuplas distintas y ya significa nombre de producto.
    "task": ["task", "work_package", "activity"],
    "planned_value": ["planned_value", "planned", "bcws", "budget"],
    "earned_value": ["earned_value", "earned", "bcwp"],
    "actual_cost": ["actual_cost", "actual", "acwp", "spent"],
    "first_unit_cost": ["first_unit_cost", "first_unit", "first_cost"],
    "learning_rate": ["learning_rate", "curve"],
    "planned_volume": ["planned_volume"],
    "station": ["station", "resource"],
    "arrival_rate": ["arrival_rate", "arrivals", "lambda", "demand_rate"],
    "service_rate": ["service_rate", "service"],
    "wait_cost": ["wait_cost", "waiting_cost", "downtime_cost"],
    "server_cost": ["server_cost", "labor_cost", "staffing_cost"],
    "job": ["job", "job_id"],
    "processing_time": ["processing_time", "processing", "duration"],
    "due_date": ["due_date", "due", "deadline", "due_in"],
    "risk_name": ["risk_name", "risk", "factor"],
    "likelihood": ["likelihood", "probability", "prob", "annual_probability"],
    "impact_value": ["impact_value", "impact", "impact_usd", "loss"],
    "risk_category": ["risk_category"],
    "exposure": ["exposure", "exposed_value"],
    "velocity_days": ["velocity_days", "velocity"],
    "detectability_days": ["detectability_days", "detectability", "detection_days"],
    "time_to_recover": ["time_to_recover", "ttr", "recovery_days"],
    "driver": ["driver", "parameter", "assumption"],
    "low": ["low", "lower"],
    "high": ["high", "upper"],
    "base": ["base", "baseline", "expected"],
    "unit": ["unit"],
    "segment": ["segment", "customer_segment", "channel"],
    # -- Lote 7: precios de competencia ---------------------------------------
    # "url" y "link" pelados quedan afuera: en un maestro de productos una columna
    # `url` es la ficha propia, no la del competidor, y confundirlas haria que
    # price_intelligence salga verde sobre el catalogo del propio cliente.
    "competitor_url": ["competitor_url", "competitor_ref", "competitor_link"],
    "competitor_site": ["competitor_site", "domain"],
    "our_price": ["our_price", "client_price", "current_price"],
}

#: La tabla de alias que un mapeo usa. Normalmente ``ALIASES``, o
#: ``VerticalProfile.merged_aliases(ALIASES)`` cuando el cliente declaro un rubro.
#: ``None`` en cada firma de abajo significa "usa la del modulo".
AliasTable = Mapping[str, Sequence[str]]

#: Los campos que `webapp/demo_lead_time_scan.py` le pide a `detect_columns`.
#: `lead_time_days` entra como OPCIONAL y significa el plazo DECLARADO: en un
#: export de recepciones esa columna es lo que el sistema promete, nunca lo que
#: se observo -- lo observado se calcula de las dos fechas y de ningun otro lado.
RECEIPT_REQUIRED = ("supplier", "order_date", "receipt_date")
RECEIPT_OPTIONAL = ("lead_time_days", "product_id", "po_id")


def _norm(value: object) -> str:
    """Header -> comparison key: acentos plegados, todo lo demas fuera.

    El plegado NFKD importa porque sin el ``[^a-z0-9]`` **borra** la vocal
    acentuada en vez de reducirla: ``Descripcion`` con tilde caia en
    ``descripcin``, que no coincide con ningun alias, y una cabecera
    ``Codigo`` con tilde caia en ``cdigo``. Un export de Defontana, Softland o
    Contpaqi no mapeaba esa columna y el job la trataba como ausente -- en un
    producto cuyo primer mercado es LatAm, ese era el modo de falla por
    defecto, no un caso borde. Con el plegado, ``Descripcion`` acentuada y
    ``descripcion`` son la misma clave.
    """
    folded = unicodedata.normalize("NFKD", str(value).lower())
    return re.sub(r"[^a-z0-9]", "", folded)


def _parse_dates(series: pd.Series) -> pd.Series:
    """Parse a date column, tolerating a genuinely mixed-format column.

    ``pd.to_datetime`` without ``format="mixed"`` locks onto whichever format
    it infers from the column and silently turns every row in a DIFFERENT
    format into NaT -- a real failure mode for messy client exports that mix
    ISO and slash-delimited dates in the same column, not a hypothetical.
    ``format="mixed"`` infers the format PER VALUE instead of once for the
    whole column, so it does not drop rows just because an earlier row set a
    different format.

    Known, accepted limitation: a slash-delimited date where BOTH components
    are <= 12 (e.g. "06/01/2025") is genuinely ambiguous without locale
    context, and this defaults to month-first (the international/US
    convention) absent one. That is a narrow, honest ambiguity -- it never
    drops a row and it never corrupts an already-unambiguous date (ISO, or
    any slash date with a component > 12) the way an earlier, reverted
    ``dayfirst=True`` retry was found to do (it silently swapped month/day on
    ~39% of ISO dates whenever a mixed column happened to lock onto a slash
    format first).

    A separate failure mode: a column mixing timezone-naive values (e.g.
    "2025-01-06") with timezone-aware ones at different offsets (e.g.
    "2025-01-07T10:00:00-05:00") cannot be homogenized into one dtype, and
    pandas treats that as a structural failure rather than a per-value parse
    failure that ``errors="coerce"`` can turn into NaT. On pandas>=3 this
    raises ``ValueError: Mixed timezones detected`` before coercion ever
    runs; on pandas 2.x (the repo's declared floor) it instead silently
    returns an object-dtype Series with the aware value un-coerced, which
    crashes the very next ``.dt`` access downstream. Both are recovered the
    same way: re-parse with ``utc=True`` (which lets per-value coercion run
    normally, converting every value -- naive or aware -- to its UTC
    instant) and drop the tz so the result matches the naive dtype the rest
    of this module assumes.
    """
    try:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    except ValueError as exc:
        if "mixed timezone" not in str(exc).lower():
            raise
        parsed = None
    if parsed is None or parsed.dtype == object:
        parsed = pd.to_datetime(series, errors="coerce", format="mixed", utc=True).dt.tz_localize(None)
    return parsed


_LATAM_NUMBER_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})+,\d+$|^-?\d{1,3},(?:\d{1,2}|\d{4,})$")
_US_THOUSANDS_NUMBER_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})+\.\d+$")


def _parse_quantities(series: pd.Series) -> pd.Series:
    """Parsea la columna de cantidad, tolerando dos convenciones de separador
    de miles/decimal ademas del punto decimal simple que ``pd.to_numeric``
    ya entiende por su cuenta.

    ``pd.to_numeric`` sin mas no reconoce NINGUNA convencion real de
    separador de miles -- ni la europea/LATAM (punto de miles, coma decimal:
    ``"1.234,56"``) ni la anglosajona (coma de miles, punto decimal:
    ``"1,234.56"``) -- y las descarta en silencio como cantidad invalida.
    Real, no hipotetico: un archivo con cantidades en formato LATAM (un
    patron de suciedad "cosmetico" del arnes de pruebas) perdio el 31.67% de
    sus filas en una corrida real, con cero perdida de fecha.

    Primera pasada: el comportamiento actual, sin cambios -- riesgo cero de
    regresion para cualquier valor que ya se parseaba bien. Segunda pasada:
    reintenta SOLO los valores que fallaron, usando dos patrones mutuamente
    excluyentes.

    El patron LATAM tiene DOS ramas deliberadas: (a) al menos un grupo de
    miles real (``\\.\\d{3}`` una o mas veces) seguido de coma decimal -- sin
    ambiguedad posible; (b) sin ningun punto, una coma seguida de 1-2 o 4+
    digitos -- tambien sin ambiguedad, porque un separador de miles
    anglosajon real SIEMPRE trae grupos de EXACTAMENTE 3 digitos. La rama
    (b) excluye a proposito el shape ``coma + exactamente 3 digitos``
    (ej. ``"1,234"``): ese es el unico caso genuinamente ambiguo sin contexto
    de locale (miles anglosajon sin parte decimal, o decimal LATAM de 3
    cifras) y se deja caer como cantidad invalida en vez de adivinar -- la
    misma disciplina que ``_parse_dates`` aplica a sus propias fechas
    ambiguas. (Una version anterior de este patron con ``(?:\\.\\d{3})*``
    -- cero o mas grupos -- degeneraba con cero grupos a aceptar
    ``"1,234"`` como LATAM, precisamente el caso que se queria excluir; el
    ``+`` en la rama (a) y la rama (b) separada cierran ese hueco.)
    """
    parsed = pd.to_numeric(series, errors="coerce")
    unresolved = parsed.isna() & series.notna()
    if not unresolved.any():
        return parsed

    candidates = series[unresolved].astype(str)

    is_latam = candidates.str.match(_LATAM_NUMBER_RE)
    if is_latam.any():
        fixed = (
            candidates[is_latam]
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        parsed.loc[fixed.index] = pd.to_numeric(fixed, errors="coerce")

    still_unresolved = parsed.isna() & series.notna()
    remaining = series[still_unresolved].astype(str)
    is_us_thousands = remaining.str.match(_US_THOUSANDS_NUMBER_RE)
    if is_us_thousands.any():
        fixed2 = remaining[is_us_thousands].str.replace(",", "", regex=False)
        parsed.loc[fixed2.index] = pd.to_numeric(fixed2, errors="coerce")

    return parsed


@dataclass(frozen=True)
class IntakeQuality:
    """How much of the raw file survived cleaning, and why the rest didn't.

    ``normalize()`` silently drops unusable rows (bad date, missing quantity,
    negative quantity) so the engine never chokes on them — but "silently" means
    a client-facing deliverable could be built on a shrunken, unrepresentative
    slice of the real data with no signal anywhere that it happened. This is
    that signal, tracked alongside (not instead of) the plain cleaned frame —
    see ``normalize_tracked``/``prepare_tracked``.
    """

    n_raw: int
    n_dropped_bad_date: int
    n_dropped_bad_quantity: int
    n_dropped_negative_quantity: int
    # Canonical column -> how many cells were filled from a default rather than read
    # from the file. Dropped rows were never the only way a deliverable could rest on
    # data that is not the client's: a column absent from the source is synthesized
    # wholesale, and counting rows alone reports that as perfect quality.
    synthesized: tuple[tuple[str, int], ...] = ()
    n_canonical_rows: int = 0
    # What Kern took the file to MEAN (mapping, sheet, hygiene). Distinct from
    # the drop counts above: those are how much survived. This is the decisions
    # that happened before a row could even be counted. None on the untracked
    # ``normalize()`` path and on hand-built records; ``normalize_tracked``
    # always fills it.
    reading: IntakeReading | None = None

    @property
    def n_dropped(self) -> int:
        return self.n_dropped_bad_date + self.n_dropped_bad_quantity + self.n_dropped_negative_quantity

    @property
    def dropped_fraction(self) -> float:
        return self.n_dropped / self.n_raw if self.n_raw else 0.0

    @property
    def fully_synthesized_columns(self) -> tuple[str, ...]:
        """Columns where every single cell came from a default."""
        if not self.n_canonical_rows:
            return ()
        return tuple(
            name for name, count in self.synthesized if count >= self.n_canonical_rows
        )


@dataclass(frozen=True)
class IntakeReading:
    """What Kern took the client's file to MEAN.

    Distinct from :class:`IntakeQuality`: quality is how much survived
    cleaning; this is the mapping, the sheet, and the hygiene -- the
    decisions that happened before a row could even be counted as survived
    or dropped. It exists so the deliverable can tell the client how their
    own file was read, the same reason :class:`excel_hygiene.HygieneReport`
    is not allowed to be silent.

    English on purpose: this text lands in the inventory deck, which is
    English by default. The Spanish demo workbook writes its own labels
    from the same fields.
    """

    mapping: tuple[tuple[str, str], ...] = ()
    sheet: str | None = None
    header_row: int | None = None
    source_kind: str = "csv"
    hygiene: excel_hygiene.HygieneReport | None = None

    def mapping_text(self) -> str:
        """``date <- 'Fecha'; product_id <- 'SKU'`` -- auditable against the file."""
        return "; ".join(f"{canon} <- '{src}'" for canon, src in self.mapping)

    def location_text(self) -> str:
        """Where in the file the header was found, in the client's own terms.

        Excel rows are 1-based here: the client opens the workbook, not pandas.
        """
        if self.source_kind != "excel":
            return "CSV, first row as header"
        sheet = self.sheet if self.sheet is not None else "(first sheet)"
        excel_row = (self.header_row + 1) if self.header_row is not None else 1
        return f"Excel sheet {sheet!s}, header on row {excel_row}"

    def hygiene_text(self) -> str:
        """What was taken off the spreadsheet, or that nothing was."""
        h = self.hygiene
        if h is None:
            return "spreadsheet hygiene did not run (CSV, or no product column recognised)"
        parts: list[str] = []
        n_total = int(h.dropped.get("fila_de_total") or 0)
        n_blank = int(h.dropped.get("fila_vacia") or 0)
        if n_total:
            parts.append(f"{n_total} total/summary row(s) excluded")
        if n_blank:
            parts.append(f"{n_blank} blank separator row(s) excluded")
        if h.dropped_columns:
            parts.append(
                f"{len(h.dropped_columns)} empty unnamed column(s) dropped"
            )
        n_filled = sum(h.filled.values()) if h.filled else 0
        if n_filled:
            cols = ", ".join(f"'{c}'" for c in h.filled)
            parts.append(
                f"{n_filled} cell(s) filled from merged cells ({cols})"
            )
        if not parts:
            return (
                f"no rows or columns removed; {h.rows_in} row(s) in the sheet after the header"
            )
        return "; ".join(parts)

    def noteworthy_hygiene(self) -> bool:
        """Whether the deliverable should spend a row on hygiene.

        A silent cleaner is the defect this report exists to prevent, but a
        row that always says "nothing happened" is how the map of sources
        stops being read. Merged-cell fills count even when ``HygieneReport.clean``
        is True -- that flag ignores fills.
        """
        h = self.hygiene
        if h is None:
            return False
        return (not h.clean) or bool(h.filled)


def reading_from(raw: pd.DataFrame, mapping: ColumnMapping) -> IntakeReading:
    """Build the reading off the frame ``load_table`` returned and the mapping it produced."""
    kind = "excel" if SHEET_ATTR in raw.attrs else "csv"
    sheet = raw.attrs.get(SHEET_ATTR)
    header = raw.attrs.get(HEADER_ATTR)
    return IntakeReading(
        mapping=tuple(sorted(mapping.mapping.items())),
        sheet=None if sheet is None else str(sheet),
        header_row=None if header is None else int(header),
        source_kind=kind,
        hygiene=raw.attrs.get(HYGIENE_ATTR),
    )


@dataclass(frozen=True)
class ColumnMapping:
    """Canonical-field -> source-column, plus any required fields not found."""

    mapping: dict[str, str]
    unmatched_required: list[str]

    @property
    def ok(self) -> bool:
        return not self.unmatched_required


def detect_columns(
    df: pd.DataFrame,
    overrides: dict[str, str] | None = None,
    *,
    required: tuple[str, ...] = CANONICAL_REQUIRED,
    optional: tuple[str, ...] = CANONICAL_OPTIONAL,
    aliases: AliasTable | None = None,
) -> ColumnMapping:
    """Heuristically map canonical fields to the client's column names.

    ``required``/``optional`` default al juego de demanda, que es lo que pedian
    todos los llamadores previos. Se parametrizan porque el mismo mapeo por alias
    sirve a otros formularios de entrada -- recepciones, por ejemplo -- y
    duplicar la tabla de alias por cada uno es como se termina con cinco
    normalizadores que divergen (`RECEIPT_REQUIRED` arriba es el segundo juego).

    ``aliases`` es la tabla a usar; ``None`` significa la del modulo. El pack por
    vertical llega por aqui y por ningun otro lado -- ver :data:`AliasTable`.
    ``VerticalProfile.merged_aliases(ALIASES)`` devuelve esta misma tabla con los
    alias del rubro ADELANTE -- el export de una constructora trae `partida` y
    `faena`, el de un distribuidor trae `local` y `guia de despacho`, y ninguno
    de esos encabezados esta ni puede estar en una tabla generica. Como el
    recorrido de abajo toma el PRIMER alias que pega, el orden de la tabla es
    parte de su significado: quien la construya tiene que prependear, no anexar.

    **Precedencia**, por campo canonico: cuanto mas estrecha la poblacion sobre
    la que la afirmacion habla, mas arriba queda. Un ``overrides`` que nombra una
    columna que existe en ESTE archivo gana; despues los alias del rubro (una
    afirmacion sobre una industria); despues los genericos (sobre todos los
    archivos). Entre campos canonicos no arbitra nadie: sigue mandando el orden
    de ``(*required, *optional)``, que este cambio no toca.
    """
    if aliases is not None and not aliases:
        raise ValueError(
            "aliases={} esta vacia: pasa None para la tabla del modulo, o "
            "vertical.merged_aliases(jobs.intake.ALIASES) para un pack. Pasar "
            "VerticalProfile.aliases crudo es el error que esto atrapa: tiene la "
            "forma correcta y no fallaria, solo dejaria de detectar casi todo."
        )
    table = ALIASES if aliases is None else aliases
    overrides = overrides or {}
    # El branch de overrides vive DENTRO del bucle, asi que una clave fuera de
    # required|optional se evapora en silencio -- la capa de mayor precedencia
    # fallando de la forma mas callada. Se valida antes de empezar.
    #
    # Va ANTES del chequeo de la tabla a proposito: quien escribe
    # `overrides={"equipment_id": ...}` con los juegos por defecto tiene un error
    # de llamada, y el mensaje util nombra el override, no la tabla de alias.
    desconocidas = sorted(set(overrides) - set(required) - set(optional))
    if desconocidas:
        raise ValueError(
            f"overrides nombra campos canonicos que esta llamada no detecta: {desconocidas}; "
            f"required={list(required)} optional={list(optional)}"
        )
    # Un canonico pedido que la tabla no conoce se dice, no se ignora. Antes esto
    # era un `ALIASES[canon]` pelado, cuyo KeyError decia el nombre y nada mas; y
    # con tablas de rubro el error deja de ser siempre un typo -- tambien puede
    # ser un pack al que le falta un campo que el llamador da por hecho.
    #
    # Se rechaza aqui y no con un `table.get(canon, (canon,))` mas abajo. Ese
    # default INVENTA un alias -- el propio nombre canonico -- y convierte un
    # pack incompleto en "columna no encontrada", que es la respuesta correcta a
    # otra pregunta. `required` y `optional` los fija el codigo, nunca el
    # visitante, asi que esto solo se dispara por un bug o por un pack mal
    # armado, y en los dos casos hay que verlo. Si algun dia un endpoint pasa una
    # canonica de pack en `required`, este KeyError sale por el manejador de 500:
    # ahi se envuelve en la capa que llama, no se ablanda la guarda.
    missing = [c for c in (*required, *optional) if c not in table]
    if missing:
        raise KeyError(
            f"detect_columns: la tabla de alias no tiene entrada para {missing} "
            f"(pedidos: required={list(required)}, optional={list(optional)})")
    by_norm = {_norm(c): c for c in df.columns}
    mapping: dict[str, str] = {}
    for canon in (*required, *optional):
        if canon in overrides and overrides[canon] in df.columns:
            mapping[canon] = overrides[canon]
            continue
        # Corchete y no `.get`: la guarda de `missing` de arriba ya garantiza la
        # clave, y un default aqui volveria a inventar el alias que aquella
        # rechaza.
        for alias in table[canon]:
            hit = by_norm.get(_norm(alias))
            if hit is not None:
                mapping[canon] = hit
                break
    unmatched = [c for c in required if c not in mapping]
    return ColumnMapping(mapping=mapping, unmatched_required=unmatched)


#: Cuantas filas de arriba se miran buscando el encabezado real. Doce cubre el
#: titulo + logo + filtros + linea en blanco de un export de ERP sin volverse un
#: escaneo del archivo entero.
HEADER_SCAN_ROWS = 12

#: Sufijos que van por ``pd.read_excel``. El resto se lee como CSV.
_EXCEL_SUFFIXES = (".xlsx", ".xlsm", ".xls")


#: Largo minimo de un alias para poder votar por la fila de encabezado. `ot`
#: (orden de trabajo, del pack de repuestos) tiene dos caracteres y aparece como
#: VALOR en cualquier export con una columna "Tipo Documento": cada fila de datos
#: se llevaria un punto gratis en `_header_score`, y los empates van a la fila mas
#: arriba, asi que la primera fila de datos le ganaria al encabezado real. Medido,
#: el piso descarta exactamente `ds` y `po` de la tabla base y `ot` del pack.
_MIN_ALIAS_KEY = 3


def _alias_keys(aliases: AliasTable | None = None) -> frozenset[str]:
    """Todo alias conocido, normalizado, para puntuar una fila candidata.

    ``ALIASES`` **no se muta nunca**: el pack por vertical llega por el parametro
    y por ningun otro camino. La alternativa -- un `ALIASES.update(...)` global --
    seria un bug de concurrencia, no un atajo: webapp/app.py corre los jobs en
    `asyncio.to_thread` sobre un unico orquestador y con el servidor MCP montado
    encima, asi que la tabla mutada para un cliente de mineria re-mapearia la
    subida simultanea del siguiente cliente de distribucion. Daria un numero
    equivocado, no un error, y no seria reproducible.
    """
    table = ALIASES if aliases is None else aliases
    return frozenset(
        k
        for entries in table.values()
        for alias in entries
        if len(k := _norm(alias)) >= _MIN_ALIAS_KEY
    )


def _header_score(row: pd.Series, keys: frozenset[str]) -> int:
    """Cuantos encabezados CANONICOS DISTINTOS reconoce esta fila.

    Distintos, no totales: una fila de relleno con la misma palabra repetida no
    puede ganarle a un encabezado de verdad.
    """
    return len({k for cell in row if (k := _norm(cell)) and k in keys})


def detect_sheet_and_header(
    path: str | Path, *, aliases: AliasTable | None = None
) -> tuple[object, int]:
    """(hoja, fila de encabezado) del libro: la combinacion que reconoce mas columnas.

    ``pd.read_excel(p)`` a secas asume PRIMERA HOJA y FILA 0, y las dos son
    falsas en la planilla que manda un cliente: un titulo arriba ("Reporte de
    Recepciones 2026") desplaza el encabezado, y media docena de ERPs exportan
    una portada con el logo y dejan los datos en la segunda hoja. Medido, esos
    dos casos mapeaban CERO columnas y salian por el 400 -- friccion en el paso
    que el embudo self-serve necesita que sea de cero contacto.

    Se puntua por evidencia: la fila que mas alias conocidos reconoce gana. No
    se adivina por posicion ni por nombre de hoja ("Datos", "Hoja1") porque eso
    es una heuristica sobre la convencion de quien exporto, no sobre el archivo.

    Empates -> la hoja mas a la izquierda y la fila mas arriba, para que dos
    corridas sobre el mismo archivo den lo mismo. Si NINGUNA fila de ninguna
    hoja reconoce un solo alias, devuelve ``(0, 0)`` -- el comportamiento previo
    exacto, para que un archivo que hoy es un 400 explicito lo siga siendo en
    vez de convertirse en un mapeo inventado.
    """
    probe = pd.read_excel(path, sheet_name=None, header=None, nrows=HEADER_SCAN_ROWS)
    keys = _alias_keys(aliases)
    best: tuple[int, object, int] | None = None
    for name, frame in probe.items():
        for row_idx in range(len(frame)):
            score = _header_score(frame.iloc[row_idx], keys)
            if score and (best is None or score > best[0]):
                best = (score, name, row_idx)
    if best is None:
        return 0, 0
    return best[1], best[2]


#: Donde ``load_table`` deja el informe de higiene de un Excel, para que el
#: entregable pueda decirle al cliente que le sacaron a su planilla.
HYGIENE_ATTR = "excel_hygiene"
#: Hoja y fila de encabezado (0-based) que gano ``detect_sheet_and_header``.
#: Viajan en ``attrs`` por el mismo motivo que la higiene: ``load_table`` no
#: puede cambiar de firma, y el entregable tiene que poder decir "lei la hoja
#: X, fila Y" en vez de dejar que el cliente adivine.
SHEET_ATTR = "excel_sheet"
HEADER_ATTR = "excel_header_row"


def _key_column(frame: pd.DataFrame, aliases: AliasTable | None) -> str | None:
    """La columna de producto del archivo, o ``None`` si no se reconoce ninguna."""
    tabla = aliases if aliases is not None else ALIASES
    conocidos = {_norm(a) for a in tabla.get("product_id", ())}
    for columna in frame.columns:
        if _norm(columna) in conocidos:
            return str(columna)
    return None


def load_table(path: str | Path, *, aliases: AliasTable | None = None) -> pd.DataFrame:
    """Read a client file (CSV or Excel) into a DataFrame.

    Excel pasa por :func:`detect_sheet_and_header` en vez de asumir la primera
    hoja y la fila 0. El camino CSV queda igual: ahi un titulo arriba rompe el
    parseo con un error de columnas desparejas en vez de mapear en silencio, y
    es otra clase de arreglo.
    """
    p = Path(path)
    if p.suffix.lower() not in _EXCEL_SUFFIXES:
        return pd.read_csv(p)

    sheet, header = detect_sheet_and_header(p, aliases=aliases)
    crudo = pd.read_excel(p, sheet_name=sheet, header=header)
    # Hoja y fila viajan siempre, incluso cuando no hay columna de producto:
    # el entregable tiene que poder decir donde miro, no solo que no limpio.
    crudo.attrs[SHEET_ATTR] = sheet
    crudo.attrs[HEADER_ATTR] = header
    clave = _key_column(crudo, aliases)
    if clave is None:
        # Sin una columna de producto reconocible no hay donde buscar la etiqueta
        # de total ni que fila es un separador. Se devuelve el crudo: el mapeo va
        # a fallar mas adelante con un mensaje que nombra las columnas, que es
        # mejor diagnostico que una limpieza adivinada sobre un archivo ajeno.
        return crudo

    limpio = excel_hygiene.read_clean_table(p, key_column=clave, sheet=sheet, header_row=header)
    frame = limpio.frame
    # El informe viaja en `attrs` y no en el tipo de retorno: `load_table` lo
    # llaman el intake, pricing y la subida del webapp, y cambiarle la firma
    # obligaria a tocar los tres. `attrs` lo ignora quien no lo mira y lo
    # encuentra quien tiene donde mostrarlo -- que es lo que impide que esto sea
    # una limpieza silenciosa.
    frame.attrs[HYGIENE_ATTR] = limpio.report
    frame.attrs[SHEET_ATTR] = sheet
    frame.attrs[HEADER_ATTR] = header
    return frame


def _normalize_core(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    period: str,
    default_lead_days: float,
    default_unit_cost: float,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Shared engine behind ``normalize``/``normalize_tracked`` — identical
    cleaning + aggregation, plus a per-reason count of every row dropped along
    the way. Each row is attributed to the FIRST filter that removes it (bad
    date -> bad quantity -> negative quantity), matching the actual left-to-right
    elimination order below, so counts never double-count a single bad row.
    """
    if not mapping.ok:
        raise ValueError(f"could not detect required columns: {mapping.unmatched_required}")

    m = mapping.mapping
    out = pd.DataFrame()
    out["date"] = _parse_dates(df[m["date"]])
    out["product_id"] = df[m["product_id"]].astype(str).str.strip()
    out["quantity"] = _parse_quantities(df[m["quantity"]])
    if "unit_cost" in m:
        out["unit_cost"] = pd.to_numeric(df[m["unit_cost"]], errors="coerce")
    elif "revenue" in m and m["revenue"] != m["quantity"]:
        # No cost column, but the file carries money: recover the real unit cost
        # rather than falling back to a flat default (which makes any value-based
        # ranking collapse onto units). Guarded against revenue *being* the
        # quantity column -- 'sales' is an alias of both.
        revenue = pd.to_numeric(df[m["revenue"]], errors="coerce")
        qty = out["quantity"]
        out["unit_cost"] = (revenue / qty.where(qty != 0)).replace(
            [float("inf"), float("-inf")], None
        )
    if "lead_time_days" in m:
        out["lead_time_days"] = pd.to_numeric(df[m["lead_time_days"]], errors="coerce")
    if "lead_time_std" in m:
        out["lead_time_std"] = pd.to_numeric(df[m["lead_time_std"]], errors="coerce")

    n_raw = len(out)
    after_date = out.dropna(subset=["date"])
    n_dropped_bad_date = n_raw - len(after_date)
    after_quantity = after_date.dropna(subset=["quantity"])
    n_dropped_bad_quantity = len(after_date) - len(after_quantity)
    out = after_quantity[after_quantity["quantity"] >= 0]
    n_dropped_negative_quantity = len(after_quantity) - len(out)
    quality = IntakeQuality(
        n_raw=n_raw,
        n_dropped_bad_date=n_dropped_bad_date,
        n_dropped_bad_quantity=n_dropped_bad_quantity,
        n_dropped_negative_quantity=n_dropped_negative_quantity,
    )
    if out.empty:
        raise ValueError("no usable rows after cleaning (check date/quantity columns)")

    out["bucket"] = out["date"].dt.to_period(period).dt.start_time
    agg: dict[str, str] = {"quantity": "sum"}
    if "unit_cost" in out.columns:
        agg["unit_cost"] = "mean"
    if "lead_time_days" in out.columns:
        agg["lead_time_days"] = "median"
    if "lead_time_std" in out.columns:
        agg["lead_time_std"] = "median"

    grouped = out.groupby(["product_id", "bucket"], as_index=False).agg(agg).rename(columns={"bucket": "date"})
    synthesized: dict[str, int] = {}

    def _fill(column: str, default: float) -> None:
        """Fill a missing/blank optional column and record how many cells we invented."""
        if column not in grouped.columns:
            synthesized[column] = len(grouped)
            grouped[column] = default
            return
        blank = int(grouped[column].isna().sum())
        if blank:
            synthesized[column] = blank
            grouped[column] = grouped[column].fillna(default)

    _fill("unit_cost", default_unit_cost)
    _fill("lead_time_days", default_lead_days)
    # 0.0 = "no dispersion known", which is what the engine assumed before this
    # column existed — so a file without it behaves exactly as it did.
    _fill("lead_time_std", 0.0)

    quality = replace(
        quality,
        synthesized=tuple(sorted(synthesized.items())),
        n_canonical_rows=len(grouped),
    )

    grouped = grouped.sort_values(["product_id", "date"]).reset_index(drop=True)
    return grouped[["date", "product_id", "quantity", "unit_cost", "lead_time_days", "lead_time_std"]], quality


def normalize(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
) -> pd.DataFrame:
    """
    Normalize raw client data to the canonical schema, aggregated per period.

    Returns columns: date, product_id, quantity, unit_cost, lead_time_days —
    one row per (product_id, period). ``period`` is a pandas offset alias
    ('W' weekly, 'D' daily, 'MS' month-start). Drops unusable rows (bad date,
    missing/negative quantity) silently — use ``normalize_tracked`` for a
    caller that needs to know how much was dropped and why.
    """
    frame, _quality = _normalize_core(
        df, mapping, period=period, default_lead_days=default_lead_days, default_unit_cost=default_unit_cost
    )
    return frame


def normalize_tracked(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Same cleaning + aggregation as ``normalize``, plus the ``IntakeQuality``
    signal (rows dropped, and why) a caller needs to decide whether the result
    is trustworthy enough to hand back with unqualified confidence."""
    frame, quality = _normalize_core(
        df, mapping, period=period, default_lead_days=default_lead_days, default_unit_cost=default_unit_cost
    )
    return frame, replace(quality, reading=reading_from(df, mapping))


def prepare(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
    aliases: AliasTable | None = None,
) -> pd.DataFrame:
    """Load a client file and return canonical, period-aggregated demand.

    ``default_lead_days`` / ``default_unit_cost`` fill lead time and unit cost only
    where the file carries none (missing column or blank cells) — per-SKU CSV values
    always win, and a unit cost derived from revenue/quantity wins over the default.
    """
    raw = load_table(path, aliases=aliases)
    mapping = detect_columns(raw, overrides, aliases=aliases)
    return normalize(
        raw, mapping, period=period, default_lead_days=default_lead_days,
        default_unit_cost=default_unit_cost,
    )


def prepare_tracked(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
    aliases: AliasTable | None = None,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Same as ``prepare``, plus the ``IntakeQuality`` signal for a caller that
    needs to decide whether the result is trustworthy enough to hand back with
    unqualified confidence — see ``normalize_tracked``."""
    raw = load_table(path, aliases=aliases)
    mapping = detect_columns(raw, overrides, aliases=aliases)
    return normalize_tracked(
        raw, mapping, period=period, default_lead_days=default_lead_days,
        default_unit_cost=default_unit_cost,
    )


# -- servicio realizado desde el archivo del cliente ----------------------------

#: Las tres columnas de servicio, que NO estan en ``CANONICAL_OPTIONAL``.
#:
#: Y no lo estan a proposito: ``normalize_tracked`` agrega la demanda a periodos, y el
#: servicio no se agrega -- dos pedidos del mismo SKU en la misma semana son dos resultados
#: distintos, y sumarlos borra el que fallo. Viven en su propio canonico, los detecta
#: ``service_lines`` y no tocan el camino de la demanda.
SERVICE_OPTIONAL = ("delivered_qty", "promised_date", "delivered_date")

#: Alias de las tres. Ricos en espanol y portugues por el mismo motivo que el resto de la
#: tabla: es lo que exporta un ERP de LatAm.
#:
#: ``entrega`` pelado NO esta, y sigue sin estar: el comentario de arriba explica que es
#: ambiguo entre una fecha y un plazo en dias. ``fecha_entrega`` si, porque el prefijo lo
#: desambigua.
SERVICE_ALIASES: dict[str, list[str]] = {
    "delivered_qty": [
        "delivered_qty", "qty_delivered", "delivered_quantity", "quantity_delivered",
        "shipped_qty", "qty_shipped", "shipped_quantity", "fulfilled_qty", "served_qty",
        "cantidad_entregada", "cant_entregada", "unidades_entregadas", "entregado",
        "entregadas", "cantidad_despachada", "cant_despachada", "unidades_despachadas",
        "despachado", "despachadas", "cantidad_servida", "servido",
        "quantidade_entregue", "entregue",
    ],
    "promised_date": [
        "promised_date", "promise_date", "due_date", "required_date", "requested_date",
        "commitment_date", "committed_date", "requested_delivery_date", "eta",
        "expected_delivery_date", "fecha_prometida", "fecha_comprometida",
        "fecha_requerida", "fecha_pactada", "fecha_compromiso", "fecha_solicitada",
        "fecha_pedida", "data_prometida", "data_prevista",
    ],
    "delivered_date": [
        "delivered_date", "delivery_date", "actual_delivery_date", "date_delivered",
        "ship_date", "shipped_date", "dispatch_date", "date_shipped", "fulfilled_date",
        "fecha_entrega", "fecha_de_entrega", "fecha_real_entrega", "fecha_entregado",
        "fecha_despacho", "fecha_de_despacho", "fecha_envio", "fecha_embarque",
        "data_entrega", "data_envio",
    ],
}

#: Las columnas que ``jobs.inventory_optimization.run(order_lines=...)`` lee. Se escriben
#: aca con el mismo orden y los mismos nombres que ``src.connectors._SERVICE_FRAME_COLUMNS``:
#: un renombre no rompe, hace que el job descarte la linea en silencio.
_SERVICE_FRAME_COLUMNS = (
    "product_id", "ordered_qty", "delivered_qty",
    "promised_date", "delivered_date", "unit_price",
)

#: En INGLES, y no por gusto: esta frase viaja al ``summary`` del agente y al mapa de
#: fuentes del entregable, que son en ingles por defecto. El deck tiene i18n
#: (``src/i18n.py``) para producir una version en espanol ENTERA; mezclar los dos idiomas
#: dentro de un mismo entregable no es eso. La primera version de esta constante estaba en
#: espanol e inyectaba castellano en un deck ingles.
_NOTA_SERVICIO = (
    "Service measured from the file's own columns: {columnas}. IMPORTANT about "
    "'{entrega}': from a file there is no way to know whether that date is the DISPATCH "
    "from the warehouse or the ARRIVAL at the customer, and they are not the same thing -- "
    "measured over 98,666 real orders the two readings differ by 6.2 points of OTIF. "
    "Confirm with the client what that column records."
)
_NOTA_SIN_ENTREGA = (
    "Service measured from the file's own columns: {columnas}. Without a delivery date "
    "OTIF cannot be measured; fill rate can."
)


def _texto_o_none(valor: object) -> str | None:
    """Una fecha ISO como texto, o ``None`` cuando la celda estaba vacia.

    ``None`` y nunca cadena vacia ni ``NaT``: el motor distingue una ausencia de un cero, y
    una cadena vacia que llega como fecha se descarta igual pero sin decir por que.
    """
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() in ("nan", "nat", "none"):
        return None
    return texto[:10]


def _numero_o_none(valor: object) -> float | None:
    """Una cantidad reportada, o ``None``. Una celda vacia NO es cero unidades servidas."""
    if valor is None:
        return None
    if isinstance(valor, str) and not valor.strip():
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def service_lines(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    aliases: AliasTable | None = None,
):
    """Las lineas de servicio del archivo del cliente, o ``None`` si no trae ninguna.

    El productor que le faltaba a ``jobs.inventory_optimization.run(order_lines=...)``. Ese
    argumento acepta un frame de lineas salientes desde que aterrizo el trabajo de servicio
    realizado, y ningun llamador lo pasaba -- asi que cada corrida reportaba ``not_measured``
    sobre archivos que a veces SI traian con que medirse.

    ``None`` y no un frame vacio: "este cliente no manda servicio" y "manda servicio y esta
    todo en cero" son cosas distintas, y el job las trata distinto.

    NO agrega por periodo. La demanda se agrega a semanas y el servicio no: dos pedidos del
    mismo SKU en la misma semana son dos resultados distintos -- uno pudo servirse entero y
    el otro no -- y sumarlos borra justamente el que fallo.

    El frame trae dos cosas en ``.attrs`` que el entregable necesita:

    ``service_headers``
        Que columna del cliente matcheo cada campo. Es lo que hace auditable al numero: de
        un adaptador sabemos que significa ``delivered_date`` porque leimos la doc del
        vendor, y de un archivo no.

    ``service_note``
        La advertencia de que una fecha de entrega en un archivo puede ser un despacho o una
        llegada. No es un detalle: ninguno de los tres ERP del canal registra la llegada, y
        entre las dos lecturas hay 6,2 puntos de OTIF medidos.
    """
    raw = load_table(path, aliases=aliases)
    mapeo = detect_columns(
        raw, overrides, optional=CANONICAL_OPTIONAL + SERVICE_OPTIONAL,
        aliases={**(aliases or ALIASES), **SERVICE_ALIASES},
    )
    if not mapeo.ok:
        # Sin las columnas obligatorias de demanda no hay a que SKU atribuirle el servicio.
        return None
    presentes = {c: mapeo.mapping[c] for c in SERVICE_OPTIONAL if c in mapeo.mapping}
    if not presentes:
        return None

    import pandas as pd

    col_sku = mapeo.mapping["product_id"]
    col_qty = mapeo.mapping["quantity"]
    col_precio = mapeo.mapping.get("unit_cost")
    filas = []
    for fila in raw.to_dict("records"):
        sku = _texto_o_none(fila.get(col_sku))
        pedidas = _numero_o_none(fila.get(col_qty))
        if sku is None or pedidas is None:
            continue
        filas.append(
            {
                "product_id": sku,
                "ordered_qty": pedidas,
                "delivered_qty": (
                    _numero_o_none(fila.get(presentes["delivered_qty"]))
                    if "delivered_qty" in presentes else None
                ),
                "promised_date": (
                    _texto_o_none(fila.get(presentes["promised_date"]))
                    if "promised_date" in presentes else None
                ),
                "delivered_date": (
                    _texto_o_none(fila.get(presentes["delivered_date"]))
                    if "delivered_date" in presentes else None
                ),
                "unit_price": _numero_o_none(fila.get(col_precio)) if col_precio else None,
            }
        )
    frame = pd.DataFrame(filas, columns=list(_SERVICE_FRAME_COLUMNS))
    columnas = ", ".join(f"{k} <- '{v}'" for k, v in sorted(presentes.items()))
    frame.attrs["service_headers"] = dict(presentes)
    frame.attrs["service_note"] = (
        _NOTA_SERVICIO.format(columnas=columnas, entrega=presentes["delivered_date"])
        if "delivered_date" in presentes
        else _NOTA_SIN_ENTREGA.format(columnas=columnas)
    )
    return frame
