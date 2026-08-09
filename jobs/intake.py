"""Intake adapter — map arbitrary client demand data to the canonical schema.

Real client files (ERP exports, transaction logs, Kaggle-style sets) arrive in
their own shapes. This detects the date / product / quantity (and optional unit
cost / lead time) columns by header aliases, then normalizes to one row per
product per period (weekly by default) ready for the engine.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

CANONICAL_REQUIRED = ("date", "product_id", "quantity")
# ``revenue`` is detected but never emitted: it exists only to recover a real
# unit cost (revenue / quantity) from files that carry money but no cost column.
CANONICAL_OPTIONAL = ("unit_cost", "lead_time_days", "lead_time_std", "revenue")

ALIASES: dict[str, list[str]] = {
    "date": ["date", "order_date", "orderdate", "invoicedate", "invoice_date", "week", "period", "ds", "day", "timestamp", "datetime", "fecha", "fecha_documento", "fecha_venta", "fecha_factura", "data", "data_emissao"],
    "product_id": ["product_id", "productid", "sku", "item", "item_id", "itemid", "stockcode", "stock_code", "product", "material", "article", "part_number", "upc", "codigo", "codigo_producto", "cod_producto", "articulo", "codigo_articulo", "referencia", "clave_producto", "produto", "codigo_produto"],
    "quantity": ["quantity", "qty", "sales", "demand", "units", "unit_sales", "unitsales", "order_qty", "orderqty", "volume", "sold", "units_sold", "cantidad", "cant", "cantidad_vendida", "unidades", "quantidade"],
    "unit_cost": ["unit_cost", "unitcost", "price", "unitprice", "unit_price", "cost", "sell_price", "sellprice", "selling_price", "costo", "costo_unitario", "precio", "precio_unitario", "custo", "preco"],
    "lead_time_days": ["lead_time_days", "leadtimedays", "lead_time", "leadtime", "lead", "lt_days", "supplier_lead_time", "declared_lead_time_days", "promised_lead_time_days", "planned_lead_time", "plazo", "plazo_dias", "plazo_entrega", "plazo_declarado", "dias_entrega", "prazo", "prazo_entrega"],
    # Lead-time dispersion (sigma_LT). Same alias set jobs/launch_readiness_job.py
    # already accepts, so a file that works there works here too.
    "lead_time_std": ["lead_time_std", "leadtimestd", "lead_std", "sigma_lead", "sigma_l"],
    "revenue": ["revenue", "sales", "sales_amount", "amount", "net_sales", "line_total", "total"],
    # Below: added for jobs/intake_scan_job.py (the intake_scan folder-audit job),
    # which maps headers against a wider canonical set (src.intake_scan.PROFILES)
    # than the demand-normalization set above. "sku" is NOT one of these entries:
    # it is not a distinct canonical name anywhere in this codebase, it is a
    # header alias for "product_id" (already listed above) -- a client's "sku"
    # column maps straight to canonical product_id, same as "item_id"/"upc"/etc.
    "name": ["name", "product_name", "item_name", "description", "desc", "nombre", "nombre_producto", "descripcion", "detalle", "glosa", "nome", "descricao"],
    "gtin": ["gtin", "ean", "upc_code", "barcode", "gtin_code"],
    "on_hand": ["on_hand", "onhand", "stock_on_hand", "qty_on_hand", "inventory_on_hand", "current_stock", "stock_qty", "stock_quantity", "stock", "stock_actual", "existencia", "existencias", "saldo", "disponible", "estoque"],
    "daily_demand": ["daily_demand", "avg_daily_demand", "demand_daily", "mean_daily_demand"],
    "days_since_last_sale": ["days_since_last_sale", "days_since_sale", "last_sale_days", "dsls"],
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
}

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
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> ColumnMapping:
    """Heuristically map canonical fields to the client's column names.

    ``required``/``optional`` default al juego de demanda, que es lo que pedian
    todos los llamadores previos. Se parametrizan porque el mismo mapeo por alias
    sirve a otros formularios de entrada -- recepciones, por ejemplo -- y
    duplicar la tabla de alias por cada uno es como se termina con cinco
    normalizadores que divergen (`RECEIPT_REQUIRED` arriba es el segundo juego).

    ``aliases`` reemplaza la tabla global. Su caso de uso es el pack de rubro:
    ``VerticalProfile.merged_aliases(ALIASES)`` devuelve esta misma tabla con los
    alias del rubro ADELANTE -- el export de una constructora trae `partida` y
    `faena`, el de un distribuidor trae `local` y `guia de despacho`, y ninguno
    de esos encabezados esta ni puede estar en una tabla generica. Como el
    recorrido de abajo toma el PRIMER alias que pega, el orden de la tabla es
    parte de su significado: quien la construya tiene que prependear, no anexar.
    """
    overrides = overrides or {}
    table = ALIASES if aliases is None else aliases
    # Un canonico pedido que la tabla no conoce se dice, no se ignora. Antes esto
    # era un `ALIASES[canon]` pelado, cuyo KeyError decia el nombre y nada mas; y
    # con tablas de rubro el error deja de ser siempre un typo -- tambien puede
    # ser un pack al que le falta un campo que el llamador da por hecho.
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
        for alias in table[canon]:
            hit = by_norm.get(_norm(alias))
            if hit is not None:
                mapping[canon] = hit
                break
    unmatched = [c for c in required if c not in mapping]
    return ColumnMapping(mapping=mapping, unmatched_required=unmatched)


def load_table(path: str | Path) -> pd.DataFrame:
    """Read a client file (CSV or Excel) into a DataFrame."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(p)
    return pd.read_csv(p)


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
    return _normalize_core(
        df, mapping, period=period, default_lead_days=default_lead_days, default_unit_cost=default_unit_cost
    )


def prepare(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
) -> pd.DataFrame:
    """Load a client file and return canonical, period-aggregated demand.

    ``default_lead_days`` / ``default_unit_cost`` fill lead time and unit cost only
    where the file carries none (missing column or blank cells) — per-SKU CSV values
    always win, and a unit cost derived from revenue/quantity wins over the default.
    """
    raw = load_table(path)
    mapping = detect_columns(raw, overrides)
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
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Same as ``prepare``, plus the ``IntakeQuality`` signal for a caller that
    needs to decide whether the result is trustworthy enough to hand back with
    unqualified confidence — see ``normalize_tracked``."""
    raw = load_table(path)
    mapping = detect_columns(raw, overrides)
    return normalize_tracked(
        raw, mapping, period=period, default_lead_days=default_lead_days,
        default_unit_cost=default_unit_cost,
    )
