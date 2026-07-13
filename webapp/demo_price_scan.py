"""Free lead-magnet: N competitor URLs in, a teaser price-position matrix
out (Linchpin 3.0 PR-13, plan section 9: "el lead magnet 'scan de posicion
de precios' gratis... replica el funnel demo-scan E2 ya desplegado").

Mirrors ``webapp/demo_scan.py``'s own shape and principles: reuses the
EXISTING, tested ``jobs.price_intelligence`` machinery as-is
(``prepare_records``/``run``/``verify_price_intel``), no new engine logic
here -- and reuses ITS lead-capture mechanism verbatim (``LEADS_FILE`` /
``demo_scan.safe_lead_dirname`` / a mini-report + a never-auto-sent
follow-up draft, wired in ``webapp/app.py``'s ``POST /api/demo-price-scan``)
rather than building a parallel one.

SSRF note (why this is safe to expose unauthenticated): every URL this scan
would fetch goes through ``jobs.price_intelligence``'s OWN acquire step,
which refuses to touch any domain without a pre-reviewed, committed
``config/sites/<domain>.yaml``
(``src.pricing_intel.acquire.base.require_approved_site`` -- "sin YAML
aprobado, el fetcher se niega a correr", plan S6.7). A visitor submitting an
internal/private/unapproved URL simply gets a "site_not_approved" skip,
never a fetch -- the compliance allowlist IS the SSRF defense, the same gate
every other acquisition path in this package goes through. This module
additionally never exposes an ``html_path`` field to the public form (that
column would let a caller read an arbitrary local file -- LFI, not just
SSRF): the teaser DataFrame built here carries ``product_id``/
``competitor_url``/``our_price`` ONLY, never a caller-supplied path.

Teaser honesty (plan rule 14, no silent caps): the teaser matrix is
EXPLICITLY partial -- quarantined/discarded observations are omitted from
the rows shown (that is the whole point: the paid diagnostic delivers the
full picture, cuarentena section included) -- but the counts of
skipped/quarantined/discarded refs are still surfaced in the response so a
visitor is never told "N competitors" when fewer than N actually resolved.
A fresh, isolated ledger is used per scan (never the production
``default_ledger()``) so a stranger's one-off teaser never mixes into a
real client's price history.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
import pandas as pd

from jobs import price_intelligence as pi
from src.pricing_intel.ledger import PriceLedger

MAX_TEASER_URLS = 5
CTA_PATH = "/paquetes/diagnostico-posicion-precios"


@dataclass(frozen=True)
class DemoPriceScanResult:
    """The report plus the composed teaser view, QA-gated the same way as
    everywhere else in Kern: no artifact is treated as "ok" for a client
    with zero confirmed observations."""

    report: object  # jobs.price_intelligence.PriceIntelReport
    n_urls_submitted: int

    @property
    def ok(self) -> bool:
        return len(self.report.offers) > 0

    @property
    def teaser_rows(self) -> list[dict]:
        """Non-quarantined, partial rows only -- the free scan's whole
        point (plan section 9's lead-magnet spec: "matriz teaser")."""
        return [
            {
                "site": o.site,
                "price_normalized": float(o.price_normalized),
                "currency": o.currency,
                "acquisition_tier": o.acquisition_tier,
                "extractor": o.extractor,
                "observed_at": o.observed_at.isoformat(),
            }
            for o in self.report.offers
        ]

    @property
    def headline(self) -> dict:
        return {
            "n_urls_submitted": self.n_urls_submitted,
            "n_confirmed": len(self.report.offers),
            "n_quarantined": len(self.report.quarantined),
            "n_discarded": len(self.report.discarded),
            "n_skipped": len(self.report.skipped),
        }


def run_demo_price_scan(
    urls: list[str],
    *,
    product_id: str = "PRODUCT",
    our_price: float | None = None,
    ledger_base_path: str | Path,
    http_client: httpx.Client | None = None,
) -> DemoPriceScanResult:
    """Run the teaser scan over up to :data:`MAX_TEASER_URLS` competitor
    URLs. ``ledger_base_path`` MUST be a fresh, isolated directory (the
    caller's responsibility, matching ``/api/demo-scan``'s own isolated-
    tempdir-per-request convention) -- never the production ledger.
    ``http_client`` is a testing-only override (never exposed via the public
    HTTP API -- see ``jobs.price_intelligence.run``'s own parameter) so tests
    can inject an ``httpx.MockTransport``-backed client instead of touching
    the real network.

    Raises ``ValueError`` when no usable URL was submitted (the endpoint
    maps that to an actionable 400, matching ``/api/demo-scan``'s pattern
    for a missing/invalid upload).
    """
    cleaned = [u.strip() for u in urls if u and u.strip()][:MAX_TEASER_URLS]
    if not cleaned:
        raise ValueError("at least one competitor URL is required")

    rows = [{"product_id": product_id, "competitor_url": u} for u in cleaned]
    if our_price is not None:
        for row in rows:
            row["our_price"] = our_price
    df = pd.DataFrame(rows)
    # base_dir is irrelevant here: the public form never supplies html_path,
    # so prepare_records never resolves a local path for these rows.
    payload = pi.prepare_records(df)

    ledger = PriceLedger(ledger_base_path)
    try:
        report = pi.run(payload, ledger=ledger, http_client=http_client)
    finally:
        ledger.close()

    return DemoPriceScanResult(report=report, n_urls_submitted=len(cleaned))


def render_mini_report(result: DemoPriceScanResult, *, email: str, product_id: str, ts: str) -> str:
    """The persisted mini-report (markdown, operator + lead facing) --
    same shape as ``webapp/demo_scan.py``'s own ``render_mini_report``."""
    h = result.headline
    lines = [
        "# Mini-reporte del escaneo de posicion de precios",
        "",
        f"- **Lead:** {email}",
        f"- **Producto:** {product_id}",
        f"- **Fecha:** {ts}",
        f"- **URLs enviadas:** {h['n_urls_submitted']}",
        "",
        "## Titular",
        "",
        f"**{h['n_confirmed']} de {h['n_urls_submitted']} competidor(es) confirmados** "
        f"({h['n_quarantined']} en cuarentena, {h['n_discarded']} descartados, "
        f"{h['n_skipped']} omitidos -- ver el Diagnostico completo para el detalle).",
        "",
        "## Precios observados (parcial -- solo confirmados)",
        "",
    ]
    for row in result.teaser_rows:
        lines.append(f"  - {row['site']}: {row['price_normalized']:.2f} {row['currency']} "
                     f"(tier {row['acquisition_tier']}, {row['extractor']})")
    lines += [
        "",
        "## Siguiente paso",
        "",
        f"Diagnostico de Posicion de Precios (sprint de 2 semanas): {CTA_PATH}",
        "",
        "*Escaneo automatico sobre las URLs enviadas. El Diagnostico completo cubre todo tu "
        "catalogo, incluye la seccion de cuarentena/descartes y la procedencia completa por "
        "dato, con compuerta de QA antes de entregarse.*",
    ]
    return "\n".join(lines) + "\n"


def render_followup_email(result: DemoPriceScanResult, *, email: str, product_id: str) -> str:
    """A ready-to-edit follow-up DRAFT for the operator. Never sent automatically."""
    h = result.headline
    return "\n".join(
        [
            f"Para: {email}",
            f"Asunto: Tu escaneo de precios para {product_id}: {h['n_confirmed']} competidor(es) confirmados",
            "",
            "Hola,",
            "",
            f"Corriste el escaneo gratuito de posicion de precios de Kern sobre {product_id} "
            f"({h['n_urls_submitted']} URL(s) enviadas, {h['n_confirmed']} confirmadas). Vista rapida:",
            "",
        ]
        + [f"- {r['site']}: {r['price_normalized']:.2f} {r['currency']}" for r in result.teaser_rows]
        + [
            "",
            "El Diagnostico de Posicion de Precios (USD 2.000-3.500, sprint de 2 semanas) corre esto "
            "sobre todo tu catalogo, con la seccion de cuarentena/descartes completa y la procedencia "
            "por dato (tier de adquisicion, extractor, confianza, fecha) citada en el reporte.",
            "",
            f"Detalle y alcance: {CTA_PATH}",
            "",
            "Si te sirve, respondeme este correo y coordinamos una llamada de 20 minutos.",
            "",
            "[FIRMA-OPERADOR]",
            "",
            "--",
            "BORRADOR generado por el escaneo del demo. Revisar y enviar a mano; Kern nunca envia "
            "correo automaticamente.",
        ]
    ) + "\n"
