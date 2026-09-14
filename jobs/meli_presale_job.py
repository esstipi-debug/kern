"""MercadoLibre preventa / catalog-search playbook -- read-only.

Turns a list of product queries (title or GTIN) into a classified preventa
scan against MercadoLibre's official catalog API, plus a client deck. Never
publishes a listing: ``src.meli_presale`` produces drafts only, this job
does not import ``nosilo_core.writeback``, and it does not POST ``/items``.
The optional GuidedOutcome wrapper lives in ``src.meli_presale_guided``
(needs the private core) and is not on this job's import path.

``prepare()`` reads its own CSV (not ``jobs/intake.py``). Columns, any one of
which is enough per row:

* ``q`` / ``query`` / ``title`` -- keyword search
* ``gtin`` / ``ean`` / ``upc`` / ``product_identifier`` -- catalog GTIN search
* optional ``sku``, ``site_id`` (default MLA), ``manufacturing_days``, ``price``

``--demo`` (the CLI) and tests inject ``InMemoryMeliCatalog``; a live run
needs the seller's own Developers Program OAuth token on an httpx client,
wired the same way as ``src.connectors.meli_prices.MeliClient``. This job
does not start that OAuth flow and does not ask for secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.connectors.meli_catalog import MeliCatalogError, MeliCatalogRPC
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.meli_presale import (
    DEFAULT_MANUFACTURING_DAYS,
    PRESALE_READY,
    STATUSES,
    PresaleCandidate,
    PresaleQuery,
    PresaleScan,
    classify_presale,
)

_Q_COLS = ("q", "query", "title", "name", "producto", "product")
_GTIN_COLS = ("gtin", "ean", "upc", "product_identifier", "barcode")
_SKU_COLS = ("sku", "seller_sku", "product_id")
_SITE_COLS = ("site_id", "site", "marketplace")
_DAYS_COLS = ("manufacturing_days", "dias_fabricacion", "lead_time_days")
_PRICE_COLS = ("price", "precio")


@dataclass(frozen=True)
class MeliPresaleReport:
    scan: PresaleScan
    n_queries: int
    n_presale_ready: int
    n_catalog_only: int
    n_already_listed: int
    n_no_match: int
    summary: str
    demo: bool = False


def _cell_text(value: object) -> str:
    """A CSV cell as text. pandas turns a bare GTIN into a float (``779....0``)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if text.endswith(".0") and text.replace(".", "", 1).isdigit():
        return text[:-2]
    return text


def _pick(row: pd.Series, candidates: tuple[str, ...]) -> str:
    for col in candidates:
        if col in row.index:
            text = _cell_text(row[col])
            if text:
                return text
    return ""


def _queries_from_frame(df: pd.DataFrame, params: dict | None) -> list[PresaleQuery]:
    params = params or {}
    default_site = str(params.get("site_id") or "MLA").strip().upper()
    default_days = int(params.get("manufacturing_days") or DEFAULT_MANUFACTURING_DAYS)
    queries: list[PresaleQuery] = []
    for _, row in df.iterrows():
        q = _pick(row, _Q_COLS)
        gtin = _pick(row, _GTIN_COLS)
        if not q and not gtin:
            continue
        days_raw = _pick(row, _DAYS_COLS)
        price_raw = _pick(row, _PRICE_COLS)
        queries.append(
            PresaleQuery(
                q=q,
                product_identifier=gtin,
                site_id=_pick(row, _SITE_COLS) or default_site,
                sku=_pick(row, _SKU_COLS),
                manufacturing_days=int(days_raw) if days_raw else default_days,
                price=float(price_raw) if price_raw else None,
            )
        )
    return queries


def prepare(data_path: str | Path, params: dict | None = None) -> list[PresaleQuery]:
    """Read a query CSV. Raises if no usable row has q or gtin."""
    df = pd.read_csv(data_path)
    queries = _queries_from_frame(df, params)
    if not queries:
        raise ValueError(
            "meli_presale CSV needs a q/title or gtin/ean column with at least one row"
        )
    return queries


def run(
    queries: list[PresaleQuery],
    rpc: MeliCatalogRPC,
    *,
    already_listed: frozenset[str] | set[str] | None = None,
    demo: bool = False,
) -> MeliPresaleReport:
    scan = classify_presale(queries, rpc, already_listed=already_listed)
    counts = {s: 0 for s in (
        PRESALE_READY, "catalog_only", "already_listed", "no_match",
        "inactive", "no_category",
    )}
    for candidate in scan.candidates:
        counts[candidate.status] = counts.get(candidate.status, 0) + 1
    source = "offline demo catalog" if demo else "MercadoLibre official catalog"
    summary = (
        f"Preventa scan over {len(scan.candidates)} query(ies) against {source}: "
        f"{counts[PRESALE_READY]} ready for a draft, {counts['catalog_only']} catalog-only, "
        f"{counts['already_listed']} already listed, {counts['no_match']} unmatched. "
        "No listing was published."
    )
    return MeliPresaleReport(
        scan=scan,
        n_queries=len(scan.candidates),
        n_presale_ready=counts[PRESALE_READY],
        n_catalog_only=counts["catalog_only"],
        n_already_listed=counts["already_listed"],
        n_no_match=counts["no_match"],
        summary=summary,
        demo=demo,
    )


def verify(report: MeliPresaleReport) -> list[str]:
    """Refuse a deliverable that would look like a published listing."""
    problems: list[str] = []
    if report.n_queries == 0:
        problems.append("scan ran over zero queries")
    for candidate in report.scan.candidates:
        if candidate.status not in STATUSES:
            problems.append(f"unknown status {candidate.status!r}")
        if candidate.status == PRESALE_READY and candidate.draft is None:
            problems.append(f"{_label(candidate)} is presale_ready but has no draft")
        if candidate.draft and candidate.draft.get("_posted"):
            problems.append(f"{_label(candidate)} draft is marked posted -- this job never posts")
    outcome = report.scan.outcome
    if outcome is not None and getattr(outcome, "status", None) == "EXECUTED":
        problems.append("outcome is EXECUTED -- preventa must stay OPTIONS or HANDOFF")
    return problems


def _label(candidate: PresaleCandidate) -> str:
    if candidate.product:
        return candidate.product.product_id
    return candidate.query.product_identifier or candidate.query.q


def rows_for_csv(report: MeliPresaleReport) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for candidate in report.scan.candidates:
        product = candidate.product
        rows.append({
            "query": candidate.query.q or candidate.query.product_identifier,
            "sku": candidate.query.sku,
            "status": candidate.status,
            "preventa_allowed": candidate.preventa_allowed,
            "catalog_product_id": product.product_id if product else "",
            "name": product.name if product else "",
            "category_id": product.category_id if product else "",
            "gtin": product.gtin if product else candidate.query.product_identifier,
            "reason": candidate.reason,
            "draft_catalog_listing": bool(candidate.draft),
        })
    return rows


def write_report_csv(report: MeliPresaleReport, path: Path | str) -> Path:
    return write_summary_csv(rows_for_csv(report), path)


#: De donde arranca la confianza de este deck, antes de descontar. No es 1,0: el
#: catalogo oficial de MercadoLibre es la fuente, pero lo que el deck propone
#: -- publicar en preventa -- depende de terminos de categoria que MELI cambia sin
#: aviso, asi que una lectura de hoy no garantiza una publicacion de manana.
_DECK_BASE_CONFIDENCE = 0.8
#: Un deck armado sobre el catalogo de demostracion no habla de la cuenta de nadie.
#: El residual ya lo dice en palabras; esto hace que el NUMERO tampoco mienta.
_DEMO_PENALTY = 0.5
#: Consultas que no encontraron nada en el catalogo: el escaneo cubrio menos de lo
#: que el cliente pidio, y la conclusion descansa sobre esa fraccion.
_NO_MATCH_PENALTY = 0.3
#: El piso de confianza del contrato (``nosilo_core.guided.CONFIDENCE_FLOOR``),
#: repetido aca a proposito y no importado: este job y su CLI NO pueden tocar
#: ``nosilo_core`` -- es un camino de solo lectura y ahi vive la escritura, y un
#: test lo vigila (``test_job_and_cli_never_import_writeback``). Un numero
#: duplicado es mas barato que una puerta abierta hacia el plano de escritura.
_CONFIDENCE_FLOOR = 0.10


def deck_confidence(report: MeliPresaleReport) -> float:
    """La confianza del deck, derivada de lo que este escaneo pudo ver.

    Un literal en la firma decia lo mismo para una corrida de demostracion que para
    una cuenta viva, y para un escaneo que encontro todo que para uno que no
    encontro nada. Espejo de ``jobs/abc_xyz_job.py``, que deriva la suya de si el
    ABC ranquea sobre valor o se degrada a unidades.
    """
    confianza = _DECK_BASE_CONFIDENCE
    if report.demo:
        confianza -= _DEMO_PENALTY
    if report.n_queries:
        confianza -= _NO_MATCH_PENALTY * (report.n_no_match / report.n_queries)
    return max(_CONFIDENCE_FLOOR, round(confianza, 4))


def build_deliverable(
    report: MeliPresaleReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
) -> Deliverable:
    if confidence is None:
        confidence = deck_confidence(report)
    findings = [
        Finding(
            "Preventa-ready catalog products",
            f"{report.n_presale_ready} of {report.n_queries} queries can carry "
            "MANUFACTURING_TIME; drafts were prepared and not published.",
            impact="seller can pre-sell only where the category allows it",
        ),
        Finding(
            "Already listed",
            f"{report.n_already_listed} hit(s) already exist on the seller account.",
            impact="creating a second listing would duplicate and risk moderation",
        ),
        Finding(
            "Unmatched or catalog-only",
            f"{report.n_no_match} unmatched, {report.n_catalog_only} catalog-only "
            "(category has no MANUFACTURING_TIME).",
            impact="those queries need a different search or a regular listing",
        ),
    ]
    kpis = (
        Kpi("Queries", f"{report.n_queries}", rationale="Searches run against the official catalog"),
        Kpi("Preventa-ready", f"{report.n_presale_ready}", target="review drafts",
            rationale="Active catalog products whose category allows MANUFACTURING_TIME"),
        Kpi("Already listed", f"{report.n_already_listed}",
            rationale="Seller already has this catalog_product_id"),
        Kpi("Published", "0", target="0",
            rationale="This playbook never POSTs /items"),
    )
    residual = (
        "A draft is not a listing. Publishing requires the seller's own MercadoLibre "
        "app, an explicit human instruction, and a POST this job does not perform."
    )
    if report.demo:
        residual = "DEMO catalog -- numbers are fixtures, not a live seller account. " + residual
    return Deliverable(
        title="MercadoLibre preventa / catalog search",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=(
            DataSource("Catalog product search", "GET /products/search (official, authenticated)",
                       "on each scan"),
            DataSource("Preventa eligibility", "GET /categories/{id}/sale_terms MANUFACTURING_TIME",
                       "per resolved category"),
        ),
        recommendations=(
            "Review each presale_ready draft with the seller before any POST.",
            "Do not write available_quantity as if the stock were on the shelf.",
            "Register a MercadoLibre Developers Program app before pointing this at a live account.",
        ),
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared,
    )


__all__ = [
    "MeliPresaleReport",
    "MeliCatalogError",
    "build_deliverable",
    "prepare",
    "run",
    "verify",
    "write_report_csv",
]
