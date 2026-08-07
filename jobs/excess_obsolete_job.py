"""Excess & Obsolete (E&O) agent job: a stock CSV -> dead / excess classification + cash at risk.

The data-prep + deck half of the E&O tool. Reads on-hand stock (quantity, daily demand,
optional unit cost + days since last sale) with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns) and classifies each SKU healthy / excess / dead
via ``src.excess_obsolete``, sizing the cash tied up in slow and non-moving stock.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.excess_obsolete import DEAD, EXCESS, SkuEO, SkuStock, classify_excess_obsolete
from src.export import write_summary_csv
from src.i18n import DEFAULT_LANG, Lang, tool_title

# `classification` es un token de maquina (dead/excess/healthy) y viaja tal cual
# en el CSV operativo; solo se traduce donde lo lee un cliente.
_CLASS_ES = {DEAD: "muerto", EXCESS: "excedido", "healthy": "sano"}

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ONHAND_COLS = ("on_hand", "quantity", "qty", "stock", "units", "On Hand")
_DEMAND_COLS = ("daily_demand", "demand", "demand_rate", "daily_sales", "run_rate")
_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")
_LASTSALE_COLS = ("days_since_last_sale", "last_sale_days", "days_no_sale", "since_last_sale", "idle_days")

_DEFAULT_TARGET_COVER_DAYS = 90.0
_DEFAULT_DEAD_THRESHOLD_DAYS = 180.0


@dataclass(frozen=True)
class EOReport:
    n_skus: int
    n_dead: int
    n_excess: int
    n_healthy: int
    total_on_hand_value: float
    dead_value: float
    excess_value: float
    eo_value: float                 # dead_value + excess_value (cash at risk)
    eo_pct_of_value: float          # eo_value / total_on_hand_value
    lines: tuple[SkuEO, ...]        # ranked by at-risk value desc
    target_cover_days: float
    dead_threshold_days: float
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the stock columns and build the per-SKU stock records."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    on_hand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    missing = [n for n, c in (("product_id", product), ("on_hand", on_hand), ("daily_demand", demand)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    if df[on_hand].isna().any():
        bad = df.loc[df[on_hand].isna(), product].astype(str).tolist()
        raise ValueError(f"missing/non-numeric on_hand for: {', '.join(bad[:10])}")

    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    last_sale = _pick_column(df, params.get("last_sale_col"), _LASTSALE_COLS)
    stocks = [
        SkuStock(
            product_id=str(row[product]),
            on_hand=float(row[on_hand]),
            daily_demand=float(row[demand]) if pd.notna(row[demand]) else 0.0,
            unit_cost=float(row[cost]) if cost and pd.notna(row[cost]) else 0.0,
            days_since_last_sale=float(row[last_sale]) if last_sale and pd.notna(row[last_sale]) else 0.0,
        )
        for _, row in df.iterrows()
    ]
    return {
        "stocks": stocks,
        "target_cover_days": float(params.get("target_cover_days", _DEFAULT_TARGET_COVER_DAYS)),
        "dead_threshold_days": float(params.get("dead_threshold_days", _DEFAULT_DEAD_THRESHOLD_DAYS)),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a stock CSV and build the E&O payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> EOReport:
    """Classify each SKU and roll up the cash tied up in dead + excess stock."""
    lines = classify_excess_obsolete(
        payload["stocks"], target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"],
    )
    dead_value = sum(e.excess_value for e in lines if e.classification == DEAD)
    excess_value = sum(e.excess_value for e in lines if e.classification == EXCESS)
    total_value = sum(e.on_hand_value for e in lines)
    eo_value = dead_value + excess_value
    n_dead = sum(1 for e in lines if e.classification == DEAD)
    n_excess = sum(1 for e in lines if e.classification == EXCESS)
    summary = (
        f"E&O sobre {len(lines)} SKU(s): {n_dead} muertos + {n_excess} excedidos retienen "
        f"{eo_value:,.0f} en riesgo "
        f"({(eo_value / total_value * 100) if total_value > 0 else 0:.0f}% de "
        f"{total_value:,.0f} de valor en mano)."
    )
    return EOReport(
        n_skus=len(lines), n_dead=n_dead, n_excess=n_excess,
        n_healthy=len(lines) - n_dead - n_excess,
        total_on_hand_value=total_value, dead_value=dead_value, excess_value=excess_value,
        eo_value=eo_value, eo_pct_of_value=(eo_value / total_value) if total_value > 0 else 0.0,
        lines=tuple(lines), target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"], summary=summary,
    )


def verify(report: EOReport) -> list[str]:
    """QA gate: SKUs present, finite non-negative values, E&O share a valid fraction."""
    import math

    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to assess")
    if not math.isfinite(report.eo_value) or report.eo_value < 0:
        issues.append("E&O value is negative or non-finite")
    if not 0.0 <= report.eo_pct_of_value <= 1.0:
        issues.append(f"E&O share out of [0,1]: {report.eo_pct_of_value}")
    return issues


def write_operational(report: EOReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the at-risk SKUs (dead/excess) by cash exposure."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": e.product_id,
            "classification": e.classification,
            "on_hand": round(e.on_hand, 1),
            "days_of_cover": ("inf" if e.days_of_cover == float("inf") else round(e.days_of_cover, 1)),
            "excess_units": round(e.excess_units, 1),
            "at_risk_value": round(e.excess_value, 2),
            "recommended_action": e.recommended_action,
        }
        for e in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "excess_obsolete.csv")}


def build_deck(
    report: EOReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
    lang: Lang = DEFAULT_LANG,
) -> Deliverable:
    """Compose the E&O study: how much cash is stuck in dead and excess stock, and what to do.

    La prosa narrativa de este deck (summary, findings, recomendaciones, residual)
    es **nativa en castellano**: es un entregable cliente-facing y el idioma por
    defecto del repo es ``es``. Esto invierte, solo para este tool, la convencion
    que documenta ``src/i18n.py`` (prosa de tool nativa en ingles).

    ``lang`` gobierna el titulo y el andamiaje estructural del ``Deliverable``
    (encabezados de seccion, columnas de tabla, hojas de Excel) -- no traduce la
    narrativa.
    """
    summary = (
        f"Excedente y obsoleto sobre {report.n_skus} SKU(s): {report.n_dead} muertos "
        f"({report.dead_value:,.0f}) + {report.n_excess} excedidos ({report.excess_value:,.0f}) = "
        f"{report.eo_value:,.0f} en riesgo, {report.eo_pct_of_value * 100:.0f}% del "
        f"valor en mano de {report.total_on_hand_value:,.0f}."
    )

    worst = report.lines[0] if report.lines else None
    findings = [
        Finding(
            "Stock muerto (sin movimiento)",
            f"{report.n_dead} SKU(s) sin venta en {report.dead_threshold_days:.0f}+ dias retienen "
            f"{report.dead_value:,.0f}.",
            impact="cash y espacio inmovilizados - liquidar, devolver al proveedor o dar de baja",
        ),
        Finding(
            "Stock excedido (sobre la cobertura)",
            f"{report.n_excess} SKU(s) por encima del objetivo de "
            f"{report.target_cover_days:.0f} dias de cobertura retienen "
            f"{report.excess_value:,.0f} de excedente.",
            impact="dejar de comprar y bajar el stock antes de que envejezca a muerto",
        ),
    ]
    if worst is not None and worst.excess_value > 0:
        findings.append(Finding(
            f"Mayor exposicion individual: {worst.product_id}",
            f"{_CLASS_ES.get(worst.classification, worst.classification)}, "
            f"{worst.excess_value:,.0f} en riesgo ({worst.on_hand:,.0f} en mano).",
            impact="actuar primero sobre este SKU",
        ))

    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="Lineas de stock evaluadas"),
        Kpi("SKUs muertos", f"{report.n_dead}", target="minimize",
            rationale="Sin movimiento pasado el umbral"),
        Kpi("SKUs excedidos", f"{report.n_excess}", target="minimize",
            rationale="Por encima del objetivo de cobertura"),
        Kpi("Cash en riesgo (E&O)", f"{report.eo_value:,.0f}", target="minimize",
            rationale="Valor muerto + valor excedido"),
        Kpi("Participacion E&O del valor", f"{report.eo_pct_of_value * 100:.0f}%", target="minimize",
            rationale="Cash en riesgo como porcion del valor en mano"),
    )

    data_sources = (
        DataSource("Stock en mano, demanda diaria, dias desde la ultima venta",
                   "WMS / historial de ventas", "semanal"),
        DataSource("Costo unitario", "Maestro de costos", "por revision de costos"),
    )

    recommendations = (
        "Liquidar / devolver / dar de baja el stock muerto para liberar el cash y el espacio "
        "inmovilizados.",
        "Dejar de comprar los SKUs excedidos y redistribuirlos o promocionarlos antes de que "
        "envejezcan a muertos.",
        "Fijar un techo de dias de cobertura por clase ABC para que el excedente no se reconstruya.",
    )

    return Deliverable(
        title=tool_title("excess_obsolete", lang, fallback="Excess & Obsolete (E&O) Stock"),
        lang=lang,
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="La disposicion es una decision humana / comercial: el agente entrega la lista "
                 "priorizada de stock en riesgo y las acciones recomendadas para aprobar - no "
                 "liquida, devuelve ni da de baja stock. Confirmar el objetivo de cobertura, el "
                 "umbral de stock muerto y los canales de recupero antes de actuar.",
        prepared=prepared,
    )
