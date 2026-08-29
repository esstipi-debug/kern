"""El Espejo job -- free inventory diagnostic on a prospect's sales file.

The data-prep + deck + guided-options half of the prospect_diagnostic tool.
Reads the prospect's CSV/Excel with pandas directly (deliberately *not* via
``jobs/intake.py``, which the parallel loop owns), maps headers with this
job's own alias lists, and hands canonical rows to
``src.prospect_diagnostic.diagnose``.

Socket-closed: this module never opens a network connection. The local-no-cloud
promise is the product; ``tests/test_prospect_diagnostic.py`` and
``tests/test_prospect_diagnostic_job.py`` close the socket to prove it.

The engine's ``diagnose(path)`` still uses intake for dirty Excel hygiene on
the CLI of someone who already has the file -- that path is independent of
this job. The agent tool always comes through ``prepare()`` here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from nosilo_core.guided import (
    ExecutionOption,
    GuidedOutcome,
    as_options,
    recommend,
    verify_guided,
)

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.prospect_diagnostic import (
    DEFAULT_DEAD_WINDOW_DAYS,
    DEFAULT_LEAD_TIME_DAYS,
    Diagnostic,
    diagnose,
    render_report,
)

#: Committed real-shaped prospect export (Spanish headers, no PII). Used by
#: ``examples/run_espejo.py --ejemplo`` and by the job/tool tests.
EXAMPLE_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "tests" / "fixtures" / "espejo" / "prospect_sales.csv"
)

_CSV_ENCODINGS = ("utf-8", "utf-8-sig", "cp1252", "latin-1")
_EXCEL_SUFFIXES = (".xlsx", ".xls")

# Job-owned aliases. Short lists on purpose: this prepare must not import
# the intake adapter. Spanish names first because the first real file is a LatAm export.
_DATE_COLS = (
    "fecha", "fecha_venta", "fecha_documento", "fecha_factura",
    "date", "order_date", "week", "period", "dia",
)
_PRODUCT_COLS = (
    "sku", "codigo", "codigo_producto", "producto", "articulo",
    "product_id", "item", "item_id", "stockcode",
)
_QTY_COLS = (
    "cantidad", "cantidad_vendida", "unidades", "cant", "demanda",
    "quantity", "qty", "demand", "units", "units_sold",
    "sales",  # last: sales is also an importe alias -- see _revenue_col
)
_COST_COLS = (
    "precio", "precio_unitario", "costo", "costo_unitario",
    "unit_cost", "unit_price", "price", "cost",
)
_REVENUE_COLS = (
    "importe", "importe_neto", "venta_neta",
    "revenue", "sales_amount", "line_total", "amount", "total",
    "sales",
)
_ORDERED_COLS = ("ordered_qty", "cantidad_pedida", "cantidad_solicitada")
_DELIVERED_QTY_COLS = (
    "delivered_qty", "cantidad_entregada", "cantidad_despachada", "cantidad_servida",
)
_PROMISED_COLS = ("promised_date", "fecha_prometida", "fecha_comprometida")
_DELIVERED_DATE_COLS = ("delivered_date", "fecha_entregado", "fecha_despacho")


@dataclass(frozen=True)
class EspejoReport:
    """The diagnostic plus the never-unprotected next-step menu."""

    diagnostic: Diagnostic
    outcome: GuidedOutcome
    recommended_action: str
    summary: str


def _norm_header(name: object) -> str:
    return str(name).strip().lower()


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        raw = str(override)
        return raw if raw in df.columns else None
    by_norm = {_norm_header(c): str(c) for c in df.columns}
    for alias in candidates:
        hit = by_norm.get(alias.lower())
        if hit is not None:
            return hit
    return None


def _read_table(path: Path) -> pd.DataFrame:
    """CSV with encoding fallback, or Excel via pandas. No intake.py."""
    if path.suffix.lower() in _EXCEL_SUFFIXES:
        return pd.read_excel(path)
    last: UnicodeDecodeError | None = None
    for encoding in _CSV_ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last = exc
    raise last  # type: ignore[misc]


def _as_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = pd.to_datetime(value, errors="coerce")
    if parsed is pd.NaT or pd.isna(parsed):
        return None
    return parsed.date()


def _as_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(out):
        return None
    return out


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff headers and emit canonical rows for ``diagnose``."""
    params = params or {}
    date_col = _pick_column(df, params.get("date_col"), _DATE_COLS)
    product_col = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    qty_col = _pick_column(df, params.get("quantity_col"), _QTY_COLS)
    if date_col is None or product_col is None or qty_col is None:
        cols = list(df.columns)[:12]
        missing = [
            name for name, col in (
                ("date", date_col), ("product_id", product_col), ("quantity", qty_col),
            ) if col is None
        ]
        raise ValueError(
            f"could not find required column(s) {missing}; "
            f"pass date_col/product_col/quantity_col in params (columns seen: {cols})"
        )

    cost_col = _pick_column(df, params.get("cost_col"), _COST_COLS)
    revenue_col = _pick_column(df, params.get("revenue_col"), _REVENUE_COLS)
    # Same guard as the engine: "sales" is an alias of quantity AND of revenue.
    if revenue_col is not None and revenue_col == qty_col:
        revenue_col = None
    recover = cost_col is None and revenue_col is not None and revenue_col != qty_col

    ordered_col = _pick_column(df, params.get("ordered_qty_col"), _ORDERED_COLS)
    delivered_qty_col = _pick_column(df, params.get("delivered_qty_col"), _DELIVERED_QTY_COLS)
    promised_col = _pick_column(df, params.get("promised_date_col"), _PROMISED_COLS)
    delivered_date_col = _pick_column(df, params.get("delivered_date_col"), _DELIVERED_DATE_COLS)

    rows: list[dict] = []
    for record in df.to_dict("records"):
        qty = _as_float(record.get(qty_col))
        cost = _as_float(record.get(cost_col)) if cost_col else None
        if recover:
            importe = _as_float(record.get(revenue_col))
            cost = (importe / qty) if importe is not None and qty else None
        row = {
            "date": _as_date(record.get(date_col)),
            "product_id": record.get(product_col),
            "quantity": qty,
            "unit_cost": cost,
        }
        if ordered_col:
            row["ordered_qty"] = _as_float(record.get(ordered_col))
        if delivered_qty_col:
            row["delivered_qty"] = _as_float(record.get(delivered_qty_col))
        if promised_col:
            row["promised_date"] = _as_date(record.get(promised_col))
        if delivered_date_col:
            row["delivered_date"] = _as_date(record.get(delivered_date_col))
        rows.append(row)
    return rows


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a prospect sales CSV/Excel and build canonical rows. Pandas only."""
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {data_path}")
    return prepare_records(_read_table(path), params)


def _assumed_service(params: dict) -> tuple[float, float]:
    raw = params.get("assumed_service")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return (float(raw[0]), float(raw[1]))
    return (0.80, 0.90)


def _next_steps(diag: Diagnostic) -> GuidedOutcome:
    """Ranked next steps. Default is to hand the prospect the report as-is."""
    c, m, r, s = diag.concentration, diag.dead_catalog, diag.recoverable, diag.service
    items: list[ExecutionOption] = [
        ExecutionOption(
            label="hand_the_report",
            summary=(
                "Give the prospect the ASCII diagnostic as-is: concentration, "
                "dead catalog, recoverable sales as a range, and realized service."
            ),
            score=3.0,
            action="print or send render_report() ; do not add a point estimate",
            tradeoffs="honest and local; does not yet convert them into a paid inventory run",
            recommended=True,
        ),
    ]
    if s.fill_rate is None and s.otif is None:
        items.append(ExecutionOption(
            label="ask_for_service_columns",
            summary=(
                "The file has no promised/delivered quantities or dates, so OTIF "
                "and fill rate stay 'no medido'. Ask for those four columns."
            ),
            score=2.0,
            action="request ordered_qty, delivered_qty, promised_date, delivered_date",
            tradeoffs="unlocks the one number no competitor can give; the prospect may not export it",
        ))
    else:
        items.append(ExecutionOption(
            label="walk_the_realized_service",
            summary="Walk the measured fill rate and OTIF with the prospect; those are observed, not forecast.",
            score=2.0,
            action="review the realized-service section of the report with the operator",
            tradeoffs="observed numbers sell; they also expose missed deliveries",
        ))
    if r.low is None:
        items.append(ExecutionOption(
            label="do_not_invent_recoverable",
            summary=(
                f"Recoverable sales stayed 'no medido' ({r.skus_refused} SKUs refused). "
                "Do not fill the gap with a point estimate."
            ),
            score=1.5,
            action="keep the silence; intermittent SKUs need a reorder-point conversation, not a forecast",
            tradeoffs="protects the 43-percent trap; leaves no dollar figure on the table",
        ))
    else:
        items.append(ExecutionOption(
            label="offer_inventory_run",
            summary=(
                f"If they convert, run inventory_optimization on the same file "
                f"(recoverable range {r.low:,.0f} to {r.high:,.0f} per year; "
                f"{c.skus_for_80pct} of {c.skus_total} SKUs make 80% of sales; "
                f"{m.dead_skus} dead)."
            ),
            score=1.5,
            action="route a paid inventory_optimization job on the same sales history",
            tradeoffs="the paid engine, not the free mirror; needs holding_rate and service_level",
        ))
    summary = (
        f"Espejo on {diag.rows_used} usable rows: "
        f"{c.skus_for_80pct}/{c.skus_total} SKUs make 80% of sales; "
        f"{m.dead_skus}/{m.skus_total} dead in {m.window_days} days; "
        "recoverable sales and OTIF as measured (or declared not measured)."
    )
    return as_options(summary, items, confidence=0.85)


def run(rows: list[dict], params: dict | None = None) -> EspejoReport:
    """Run the engine on already-canonical rows and attach guided next steps."""
    params = params or {}
    diag = diagnose(
        rows,
        window_days=int(params.get("window_days", DEFAULT_DEAD_WINDOW_DAYS)),
        lead_time_days=float(params.get("lead_time_days", DEFAULT_LEAD_TIME_DAYS)),
        assumed_service=_assumed_service(params),
        target_service=float(params.get("target_service", 0.95)),
        min_periods=int(params.get("min_periods", 8)),
    )
    outcome = _next_steps(diag)
    recommended = recommend(outcome.options).label
    rec = diag.recoverable
    if rec.low is None:
        rec_txt = "recoverable sales not measured"
    else:
        rec_txt = f"recoverable {rec.low:,.0f}-{rec.high:,.0f}/year"
    summary = (
        f"{diag.concentration.skus_for_80pct} of {diag.concentration.skus_total} SKUs "
        f"make 80% of sales; {diag.dead_catalog.dead_skus} dead; {rec_txt}. "
        f"Recommended: {recommended}."
    )
    return EspejoReport(
        diagnostic=diag,
        outcome=outcome,
        recommended_action=recommended,
        summary=summary,
    )


def verify(report: EspejoReport) -> list[str]:
    """QA gate: never-unprotected outcome plus a real diagnostic object."""
    issues = list(verify_guided(report.outcome))
    if report.diagnostic.rows_in < 0:
        issues.append("negative rows_in is impossible")
    if report.recommended_action != recommend(report.outcome.options).label:
        issues.append("recommended_action does not match the outcome")
    return issues


def write_operational(report: EspejoReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """Prospect-facing ASCII report plus a concentration CSV for the operator."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    informe = d / "espejo_informe.txt"
    informe.write_text(render_report(report.diagnostic), encoding="utf-8")
    rows = [
        {"product_id": pid, "value": value}
        for pid, value in report.diagnostic.concentration.top_skus
    ]
    return {
        "report": informe,
        "csv": write_summary_csv(rows, d / "espejo_concentracion.csv"),
    }


def build_deck(
    report: EspejoReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Operator deck: what the mirror measured, and the ranked next step."""
    diag = report.diagnostic
    c, m, r, s = diag.concentration, diag.dead_catalog, diag.recoverable, diag.service
    rec_value = (
        "not measured"
        if r.low is None
        else f"{r.low:,.0f} to {r.high:,.0f} / year"
    )
    fill = "not measured" if s.fill_rate is None else f"{s.fill_rate * 100:.0f}%"
    otif = "not measured" if s.otif is None else f"{s.otif * 100:.0f}%"

    findings = (
        Finding(
            "Sales concentration",
            f"{c.skus_for_80pct} of {c.skus_total} SKUs "
            f"({c.share_of_skus_for_80pct * 100:.0f}%) make 80% of sales "
            f"(ranking on {c.value_basis}).",
            impact="the conversation starts on the few SKUs that move the money",
        ),
        Finding(
            "Dead catalog",
            f"{m.dead_skus} of {m.skus_total} SKUs with no sale in {m.window_days} days, "
            f"anchored at {m.anchor_date} (last date in THEIR file, not today).",
            impact="capital sitting in SKUs that have not moved",
        ),
        Finding(
            "Recoverable sales (range, never a point)",
            rec_value + (
                ""
                if r.low is None
                else f"; assumed current service {r.assumed_service_low:.0%}-{r.assumed_service_high:.0%}, "
                     f"target {r.target_service:.0%}; {r.skus_measured} SKUs measured, "
                     f"{r.skus_refused} refused"
            ),
            impact="a point estimate here is the 43-percent trap; the range is the product",
        ),
        Finding(
            "Realized service (OTIF and fill rate)",
            f"fill rate {fill}; OTIF {otif}. "
            "Silence is the finding when the file has no promised/delivered columns.",
            impact="the number no ERP-locked competitor can give from a spreadsheet",
        ),
    )
    kpis = (
        Kpi("SKUs for 80% of sales", f"{c.skus_for_80pct}/{c.skus_total}",
            target="declare, do not 'fix'", rationale="Pareto of the prospect's own file"),
        Kpi("Dead SKUs", f"{m.dead_skus}/{m.skus_total}",
            target="minimize after they convert", rationale=f"{m.window_days}-day window"),
        Kpi("Recoverable sales", rec_value,
            target="a range, never a point", rationale="loss function at 80-90% assumed service"),
        Kpi("Fill rate", fill, target="observed or not measured",
            rationale="never print 0% for a missing column"),
        Kpi("OTIF", otif, target="observed or not measured",
            rationale="same silence rule as fill rate"),
    )
    return Deliverable(
        title="El Espejo -- Prospect Inventory Diagnostic",
        client=client,
        summary=report.summary,
        findings=findings,
        kpis=kpis,
        data_sources=(
            DataSource("Prospect sales file (date / sku / qty / price)", "their export", "once"),
            DataSource("Concentration / dead catalog / recoverable range / OTIF",
                       "src.prospect_diagnostic", "deterministic"),
        ),
        recommendations=(
            "Hand them the ASCII report. Do not add a single recoverable-sales number.",
            "If OTIF is not measured, the ask is two quantity columns and two dates.",
            "A paid inventory_optimization run is the conversion step, not this free mirror.",
        ),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual="this is a catalog diagnostic on THEIR history, not a demand-first "
                 "search for what to import. Assumed current service is 80-90%, not 50%.",
        prepared=prepared,
    )
