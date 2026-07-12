"""Demo funnel scan: one stock CSV -> the Diagnostico's teaser numbers.

The /demo page's engine (E2 of the 2.0 plan). From a single stock snapshot
(product_id, on_hand, daily_demand [+ unit_cost, days_since_last_sale]) it runs
the same three analyses the paid Diagnostico de Arranque opens with -- Excess &
Obsolete, ABC-XYZ and financial KPIs -- by REUSING the existing jobs' prepare/run/
verify as-is (no delivery phase, no new engine logic), and composes the
mini-report the visitor sees: "$X trapped in dead/excess stock - A-items hold Y%
of value - DIO Z days" plus three executive findings and the CTA to the
Diagnostico one-pager.

Derivation honesty: ABC gets one annualized demand point per SKU (so XYZ is
degenerate on a snapshot -- zero-demand SKUs land in Z, the rest in X; the
mini-report only quotes the ABC axis). Finance derives COGS from the demand run
rate and inventory value from on_hand x unit_cost; DIO/turns follow. The full
multi-file, time-series treatment is exactly what the paid package sells.

QA gate: same contract as everywhere else in Kern -- if any of the three
jobs' verify() flags an issue (or a headline number is non-finite), the scan is
qa_failed and NO artifact is written. Pure module: no I/O beyond the callers'.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

import pandas as pd

from jobs import abc_xyz_job, excess_obsolete_job, financial_kpis_job
from jobs.abc_xyz_job import AbcXyzReport
from jobs.excess_obsolete_job import EOReport
from jobs.financial_kpis_job import FinancialReport
from src.excess_obsolete import SkuStock

DAYS_PER_YEAR = 365.0
CTA_PATH = "/paquetes/diagnostico-arranque"
REQUIRED_COLUMNS_HINT = (
    "product_id, on_hand, daily_demand (y opcionales: unit_cost, days_since_last_sale)"
)


@dataclass(frozen=True)
class DemoScanResult:
    """The three reports + the composed teaser, QA-gated as one unit."""

    eo: EOReport
    abc: AbcXyzReport
    fin: FinancialReport
    qa_issues: tuple[str, ...]
    findings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.qa_issues

    @property
    def headline(self) -> dict:
        """The JSON-safe numbers the UI renders. Only meaningful when ok."""
        return {
            "eo_value": round(self.eo.eo_value, 2),
            "eo_pct_of_value": round(self.eo.eo_pct_of_value, 4),
            "n_dead": self.eo.n_dead,
            "n_excess": self.eo.n_excess,
            "n_skus": self.eo.n_skus,
            "a_value_share": round(self.abc.a_value_share, 4),
            "n_a": self.abc.n_a,
            "dio": round(self.fin.dio, 1),
            "turns": round(self.fin.turns, 2),
        }


def derive_abc_items(stocks: list[SkuStock]) -> list[dict]:
    """One classifier item per SKU with a single annualized demand point.

    ABC (annual usage value = unit_cost x annual demand) is exact; XYZ needs a
    time series a snapshot doesn't carry, so it degenerates (documented above).
    """
    return [
        {
            "product_id": s.product_id,
            "unit_cost": s.unit_cost,
            "demand": [s.daily_demand * DAYS_PER_YEAR],
        }
        for s in stocks
    ]


def derive_finance_records(stocks: list[SkuStock]) -> list[dict]:
    """Per-SKU finance rows from the run rate: COGS at cost, inventory at cost."""
    return [
        {
            "product_id": s.product_id,
            "cogs": s.daily_demand * DAYS_PER_YEAR * s.unit_cost,
            "avg_inventory_value": s.on_hand * s.unit_cost,
            "gross_margin": 0.0,
            "units_sold": s.daily_demand * DAYS_PER_YEAR,
            "units_on_hand": s.on_hand,
            "net_sales": 0.0,
        }
        for s in stocks
    ]


def _md_safe(text: str, max_len: int = 60) -> str:
    """Neutralize a CSV-supplied string before it lands in a persisted .md file.

    product_id is fully attacker-controlled (an arbitrary CSV cell) and the
    mini-report/e-mail draft are read by the operator, potentially through a
    markdown/HTML renderer -- so an unescaped `<img src=x onerror=...>` or
    `[text](url)` product_id would be a stored injection sink (the repo's
    existing `defuse_formula()` only covers CSV/Excel formula injection, not
    this). Collapse to a conservative charset instead of trying to escape
    every markdown/HTML special case.
    """
    collapsed = re.sub(r"[^A-Za-z0-9 ._/-]+", "_", text).strip()
    collapsed = re.sub(r"\s+", " ", collapsed)[:max_len]
    return collapsed or "SKU"


def _scan_issues(eo: EOReport, fin: FinancialReport) -> list[str]:
    """Headline-specific finiteness gate on top of the jobs' own verify()."""
    issues: list[str] = []
    if not math.isfinite(fin.dio) or fin.dio < 0:
        issues.append(
            "DIO no computable (sin demanda o sin costos): el escaneo no puede "
            "estimar dias de inventario"
        )
    if eo.total_on_hand_value <= 0:
        issues.append(
            "sin valor de inventario (falta unit_cost o esta en cero): no hay "
            "cifra de dinero que reportar"
        )
    return issues


def _findings(eo: EOReport, abc: AbcXyzReport, fin: FinancialReport) -> tuple[str, ...]:
    """The 3 executive findings, Spanish neutral, numbers already formatted."""
    worst = next((e for e in eo.lines if e.excess_value > 0), None)
    f1 = (
        f"${eo.eo_value:,.0f} atrapados en stock muerto o excedente: "
        f"{eo.n_dead} SKU(s) sin venta hace {eo.dead_threshold_days:.0f}+ dias y "
        f"{eo.n_excess} por encima de {eo.target_cover_days:.0f} dias de cobertura "
        f"({eo.eo_pct_of_value * 100:.0f}% del valor en stock)."
    )
    if worst is not None:
        f1 += (
            f" La mayor exposicion individual es {_md_safe(worst.product_id)} "
            f"(${worst.excess_value:,.0f} en riesgo)."
        )
    f2 = (
        f"{abc.n_a} SKU(s) clase A concentran {abc.a_value_share * 100:.0f}% del "
        f"valor anual: merecen politica de servicio e inventario diferenciada del "
        f"resto del catalogo."
    )
    if abc.n_cz:
        f2 += f" {abc.n_cz} SKU(s) CZ son candidatos a descontinuar."
    f3 = (
        f"El inventario tarda {fin.dio:.0f} dias en convertirse en caja (DIO), "
        f"una rotacion de {fin.turns:.1f}x al anio: cada punto de mejora libera "
        f"capital de trabajo."
    )
    return (f1, f2, f3)


def run_demo_scan(df: pd.DataFrame, params: dict | None = None) -> DemoScanResult:
    """The whole scan: sniff columns, run the 3 jobs, QA-gate, compose findings.

    Raises ValueError (from the E&O prepare) when the required columns are
    missing -- the endpoint maps that to an actionable 400.
    """
    payload = excess_obsolete_job.prepare_records(df, params)
    stocks: list[SkuStock] = payload["stocks"]

    eo = excess_obsolete_job.run(payload)
    abc = abc_xyz_job.run(derive_abc_items(stocks))
    fin = financial_kpis_job.run(derive_finance_records(stocks))

    qa_issues = (
        excess_obsolete_job.verify(eo)
        + abc_xyz_job.verify(abc)
        + financial_kpis_job.verify(fin)
        + _scan_issues(eo, fin)
    )
    findings = _findings(eo, abc, fin) if not qa_issues else ()
    return DemoScanResult(
        eo=eo, abc=abc, fin=fin, qa_issues=tuple(qa_issues), findings=findings
    )


# ---- lead artifacts (operator-facing, written by the endpoint only on ok) ----


def safe_lead_dirname(email: str) -> str:
    """A lead email -> a single, traversal-proof, COLLISION-FREE path segment.

    The readable part alone collides: 'user+test@x.com' and 'user_test@x.com'
    both sanitize to 'user_test_at_x.com' ('+' and '_' both fall outside
    [a-z0-9._-]), and distinct emails could truncate identically past 80
    chars. A second lead colliding with a first would silently overwrite that
    lead's mini-report/follow-up draft with no signal to the operator. Append
    a short hash of the FULL normalized email (not the truncated/sanitized
    prefix) so two different email addresses cannot collide, while keeping
    the prefix human-readable for the operator browsing deliverables/leads/.
    """
    normalized = email.strip().lower()
    name = normalized.replace("@", "_at_")
    name = re.sub(r"[^a-z0-9._-]", "_", name).strip(".")[:60]
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    prefix = name if name and (set(name) - {".", "_", "-"}) else "lead"
    return f"{prefix}-{digest}"


def render_mini_report(
    result: DemoScanResult, *, email: str, dataset_label: str, ts: str
) -> str:
    """The persisted mini-report (markdown, operator + lead facing)."""
    h = result.headline
    lines = [
        "# Mini-reporte del escaneo de inventario",
        "",
        f"- **Lead:** {email}",
        f"- **Dataset:** {dataset_label}",
        f"- **Fecha:** {ts}",
        f"- **SKUs analizados:** {h['n_skus']}",
        "",
        "## Titular",
        "",
        f"**${h['eo_value']:,.0f} atrapados en stock muerto/excedente** "
        f"({h['eo_pct_of_value'] * 100:.0f}% del valor en stock) - "
        f"los A-items concentran {h['a_value_share'] * 100:.0f}% del valor - "
        f"DIO {h['dio']:.0f} dias.",
        "",
        "## Hallazgos",
        "",
    ]
    lines += [f"{i}. {f}" for i, f in enumerate(result.findings, start=1)]
    lines += [
        "",
        "## Siguiente paso",
        "",
        f"Diagnostico de Arranque (sprint de 2 semanas): {CTA_PATH}",
        "",
        "*Escaneo automatico sobre un snapshot de stock. El Diagnostico completo "
        "audita calidad de datos, clasifica ABC-XYZ sobre la serie temporal real "
        "y entrega el plan priorizado con compuerta de QA.*",
    ]
    return "\n".join(lines) + "\n"


def render_followup_email(
    result: DemoScanResult, *, email: str, dataset_label: str
) -> str:
    """A ready-to-edit follow-up DRAFT for the operator. Never sent automatically."""
    h = result.headline
    return "\n".join(
        [
            f"Para: {email}",
            "Asunto: Tu escaneo de inventario: "
            f"${h['eo_value']:,.0f} atrapados en stock muerto/excedente",
            "",
            "Hola,",
            "",
            f"Corriste el escaneo gratuito de Kern sobre {dataset_label} "
            f"({h['n_skus']} SKUs). El resultado en una linea:",
            "",
            f"- ${h['eo_value']:,.0f} atrapados en stock muerto o excedente "
            f"({h['eo_pct_of_value'] * 100:.0f}% del valor en stock)",
            f"- Los {h['n_a']} productos clase A concentran "
            f"{h['a_value_share'] * 100:.0f}% del valor anual",
            f"- El inventario tarda {h['dio']:.0f} dias en volverse caja (DIO)",
            "",
            "El Diagnostico de Arranque (USD 1.500-2.500, sprint de 2 semanas) "
            "convierte ese numero en un plan de recuperacion priorizado: auditoria "
            "de calidad de datos, ABC-XYZ sobre la serie real y KPIs financieros, "
            "cada numero trazable y con compuerta de QA antes de entregarse.",
            "",
            f"Detalle y alcance: {CTA_PATH}",
            "",
            "Si te sirve, respondeme este correo y coordinamos una llamada de 20 "
            "minutos.",
            "",
            "[FIRMA-OPERADOR]",
            "",
            "--",
            "BORRADOR generado por el escaneo del demo. Revisar y enviar a mano; "
            "Kern nunca envia correo automaticamente.",
        ]
    ) + "\n"
