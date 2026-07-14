"""Price-position priority playbook (Kern PR-10, R6 value-based
prioritization): crosses TWO already-existing engine outputs into ONE
per-SKU pricing action -- igualar_precio / oportunidad_subir / vigilar /
ignorar_bajo_valor. Mirrors ``jobs.seo_priority``'s shape almost
line-for-line (same ``ExcludedSku`` pattern, same deterministic
``_assign_action`` rule, same prepare/run/verify/write_operational shape)
because this is the same kind of pure cross-reference, just against a
different pair of signals:

  - ``src.classification.classify_portfolio`` (via ``jobs.abc_xyz_job``) --
    ABC (importance) / XYZ (predictability).
  - ``jobs.price_intelligence``'s ``position_index = our_price / avg
    competitor price`` signal -- ``jobs.price_intelligence._position_index``
    is reused verbatim (never reimplemented, golden rule 1), computed from a
    ``PriceIntelReport`` (``params['price_report']``, when the caller already
    ran ``jobs.price_intelligence`` earlier in the same pipeline) OR a
    lightweight price-position CSV (``params['price_position_path']``, for a
    client who already knows its own price + a competitor read without
    running the full acquisition cascade).

This module invents NO new demand or price-position computation. The only
new logic here is a business RULE that maps two already-computed numbers to
an action (mirrors ``seo_priority``'s own docstring framing):

  - C-class SKU -> **ignorar_bajo_valor**: not worth a price action this
    cycle, regardless of its price position (checked FIRST, exactly like
    ``seo_priority``'s E&O ``DEAD`` check being unconditional on ABC/trend).
  - A/B-class AND ``position_index > 1 + band`` (materially pricier than the
    competitor average) -> **igualar_precio**: match down to defend volume.
  - A/B-class AND ``position_index < 1 - band`` (materially cheaper) ->
    **oportunidad_subir**: margin headroom, raise toward market.
  - A/B-class AND ``position_index`` within ``[1-band, 1+band]`` (at
    market), OR no confirmed competitor read this cycle
    (``competitor_read == "insufficient_signal"``) -> **vigilar**: keep
    watching. A SKU with no confirmed competitor read NEVER gets a
    fabricated igualar_precio/oportunidad_subir from a guessed position --
    see
    ``test_sku_without_confirmed_read_gets_vigilar_insufficient_signal_not_a_guess``
    in this module's test file.

A SKU present in only ONE of the two required inputs (ABC-XYZ classification
vs. the price-position source) is EXCLUDED and reported via ``ExcludedSku``
with a reason (golden rule 14) -- never silently dropped, never silently
included with a fabricated action.

NON-GOAL 4: every action here is a RECOMMENDATION only; no price is ever
written anywhere by this module (no writeback connector is touched) -- the
``igualar_precio``/``oportunidad_subir`` reasons say so explicitly, mirroring
``seo_priority``'s ``requires_human_signoff`` framing in prose rather than a
separate field (this module's ``SkuPriceAction`` intentionally stays as small
as the task brief's own interface specifies).

QA (``verify``/``price_priority_passed``, matching ``jobs/qa.py``'s
``verify_*``/``*_passed`` naming convention): every SKU missing from one of
the two inputs is reported, not silently dropped; every action string and
every ``competitor_read`` is one of the honest enumerated labels (never a
bare guess); a price-changing action never fires without a confirmed
competitor read; every action/excluded row carries a non-empty, citable
reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

from jobs import abc_xyz_job
from jobs.price_intelligence import PriceIntelReport, _position_index
from src.classification import SkuClassification
from src.export import write_summary_csv

IGUALAR_PRECIO = "igualar_precio"
OPORTUNIDAD_SUBIR = "oportunidad_subir"
VIGILAR = "vigilar"
IGNORAR_BAJO_VALOR = "ignorar_bajo_valor"

CONFIRMED = "confirmed"
INSUFFICIENT_SIGNAL = "insufficient_signal"

# +/-5% around parity (position_index == 1.0) is the "at market" neutral
# band; outside it the position reads as a clear pricing gap. Same "honest
# neutral band" idea as seo_priority's _DEFAULT_TREND_THRESHOLD_PCT.
_DEFAULT_BAND = 0.05

_PRICE_PRODUCT_COLS = ("product_id", "ProductID", "sku", "SKU", "Product", "item")
_OUR_PRICE_COLS = ("our_price", "client_price", "current_price", "list_price")
_COMPETITOR_PRICE_COLS = ("competitor_price", "competitor_price_normalized", "price")

_ACTION_CSV_COLUMNS = (
    "product_id", "action", "abc", "xyz", "position_index", "competitor_read", "reason",
)
_EXCLUDED_CSV_COLUMNS = ("product_id", "reason")


@dataclass(frozen=True)
class SkuPricePosition:
    """One SKU's already-computed price-position read -- never re-derived
    here, only carried. ``position_index`` is ``None`` exactly when there is
    no confirmed competitor observation this cycle (``competitor_read ==
    "insufficient_signal"``)."""

    product_id: str
    position_index: float | None
    competitor_read: str  # "confirmed" | "insufficient_signal"


@dataclass(frozen=True)
class SkuPriceAction:
    """One SKU's pricing action + the citable signal behind it."""

    product_id: str
    action: str  # igualar_precio | oportunidad_subir | vigilar | ignorar_bajo_valor
    abc: str
    xyz: str
    position_index: float | None
    competitor_read: str  # confirmed | insufficient_signal
    reason: str  # citable basis (golden rule 7)


@dataclass(frozen=True)
class ExcludedSku:
    """A SKU missing from one of the two required inputs -- reported, never dropped."""

    product_id: str
    reason: str


@dataclass(frozen=True)
class PricePriorityReport:
    actions: tuple[SkuPriceAction, ...]
    excluded: tuple[ExcludedSku, ...]
    n_igualar_precio: int
    n_oportunidad_subir: int
    n_vigilar: int
    n_ignorar_bajo_valor: int
    n_excluded: int
    band: float
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _price_positions_from_report(report: PriceIntelReport) -> list[SkuPricePosition]:
    """Group ``report.offers``/``report.our_prices`` by product and reuse
    ``jobs.price_intelligence._position_index`` verbatim -- the SAME
    function ``price_intelligence.write_operational`` uses to fill its own
    ``price_position_matrix.xlsx`` -- so this module never reimplements the
    our_price / avg(competitor_price) math (golden rule 1). Every product_id
    price_intelligence attempted this run (via ``report.rows``, one row per
    ref, plus any product only visible through ``offers``/``our_prices``)
    gets an entry, even with ``position_index=None``, so a SKU that was only
    skipped/quarantined/discarded still reads ``insufficient_signal`` rather
    than being silently absent from the join."""
    offers_by_pid: dict[str, list[Decimal]] = {}
    for offer in report.offers:
        if offer.matched_product_id:
            offers_by_pid.setdefault(offer.matched_product_id, []).append(offer.price_normalized)

    attempted_pids = {row.product_id for row in report.rows} | set(offers_by_pid) | set(report.our_prices)
    positions: list[SkuPricePosition] = []
    for pid in sorted(attempted_pids):
        idx = _position_index(report.our_prices.get(pid), offers_by_pid.get(pid, []))
        positions.append(SkuPricePosition(
            product_id=pid,
            position_index=float(idx) if idx is not None else None,
            competitor_read=CONFIRMED if idx is not None else INSUFFICIENT_SIGNAL,
        ))
    return positions


def _price_positions_from_csv(df: pd.DataFrame, params: dict) -> list[SkuPricePosition]:
    """A lightweight, already-known price-position CSV (``product_id``,
    ``our_price``, ``competitor_price`` -- one row per competitor
    observation) for a client who has not run the full
    ``jobs.price_intelligence`` acquisition cascade. Still reuses
    ``_position_index`` verbatim -- this function only sniffs columns and
    aggregates per SKU, it never computes the ratio itself."""
    product = _pick_column(df, params.get("price_product_col"), _PRICE_PRODUCT_COLS)
    our_price_col = _pick_column(df, params.get("our_price_col"), _OUR_PRICE_COLS)
    competitor_col = _pick_column(df, params.get("competitor_price_col"), _COMPETITOR_PRICE_COLS)
    missing = [
        name for name, col in (
            ("price_product_col", product), ("our_price_col", our_price_col),
            ("competitor_price_col", competitor_col),
        ) if col is None
    ]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)} in the price-position CSV; "
            f"pass them in params (columns seen: {cols})"
        )

    our_price_by_pid: dict[str, Decimal] = {}
    competitor_prices_by_pid: dict[str, list[Decimal]] = {}
    all_pids: set[str] = set()
    for _, row in df.iterrows():
        pid = str(row[product]).strip()
        all_pids.add(pid)
        if pd.notna(row[our_price_col]):
            our_price_by_pid.setdefault(pid, Decimal(str(row[our_price_col])))
        if pd.notna(row[competitor_col]):
            competitor_prices_by_pid.setdefault(pid, []).append(Decimal(str(row[competitor_col])))

    positions: list[SkuPricePosition] = []
    for pid in sorted(all_pids):
        idx = _position_index(our_price_by_pid.get(pid), competitor_prices_by_pid.get(pid, []))
        positions.append(SkuPricePosition(
            product_id=pid,
            position_index=float(idx) if idx is not None else None,
            competitor_read=CONFIRMED if idx is not None else INSUFFICIENT_SIGNAL,
        ))
    return positions


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the demand-history CSV (feeds ABC-XYZ, via ``jobs.abc_xyz_job``'s
    own ``prepare``/``run`` -- never re-derived here) and cross it against
    the competitor price-position side, supplied either as an already-run
    ``params['price_report']`` (a :class:`~jobs.price_intelligence.PriceIntelReport`)
    or a ``params['price_position_path']`` CSV. Mirrors ``jobs.seo_priority``'s
    multi-input ``params`` convention (see module docstring)."""
    params = params or {}

    abc_items = abc_xyz_job.prepare(data_path, params)
    abc_report = abc_xyz_job.run(
        abc_items,
        abc_thresholds=params.get("abc_thresholds", (0.80, 0.95)),
        cv_cuts=params.get("cv_cuts", (0.5, 1.0)),
    )

    price_report = params.get("price_report")
    if price_report is not None:
        price_positions = _price_positions_from_report(price_report)
    else:
        price_position_path = params.get("price_position_path")
        if not price_position_path:
            raise ValueError(
                "params['price_report'] (a PriceIntelReport) or params['price_position_path'] "
                "(a CSV of product_id/our_price/competitor_price) is required"
            )
        price_positions = _price_positions_from_csv(pd.read_csv(price_position_path), params)

    return {
        "classifications": list(abc_report.classifications),
        "price_positions": price_positions,
        "band": float(params.get("band", _DEFAULT_BAND)),
    }


def _assign_action(classification: SkuClassification, position: SkuPricePosition, band: float) -> str:
    """The R6 business rule -- C-class is checked FIRST (unconditional,
    exactly like seo_priority's E&O DEAD check), then a missing confirmed
    read always reads vigilar/insufficient_signal, never a guessed
    igualar_precio/oportunidad_subir."""
    if classification.abc == "C":
        return IGNORAR_BAJO_VALOR
    if position.position_index is None:
        return VIGILAR
    if position.position_index > 1 + band:
        return IGUALAR_PRECIO
    if position.position_index < 1 - band:
        return OPORTUNIDAD_SUBIR
    return VIGILAR


def _reason(action: str, classification: SkuClassification, position: SkuPricePosition, band: float) -> str:
    """Golden rule 7: cite the exact numbers behind the action, and flag the
    price-changing actions as recommendations only -- this module never
    writes a price anywhere (NON-GOAL 4)."""
    if action == IGNORAR_BAJO_VALOR:
        return (
            f"ABC class=C (low importance, {classification.cell} cell); not worth a price "
            "action this cycle regardless of price position."
        )
    if position.position_index is None:
        return (
            f"ABC class={classification.abc}; no confirmed competitor price read this cycle "
            f"(competitor_read={position.competitor_read}) -- watching, never a guessed position."
        )
    idx = position.position_index
    if action == IGUALAR_PRECIO:
        return (
            f"ABC class={classification.abc}; position_index={idx:.3f} (our_price / avg competitor "
            f"price) > 1+band ({1 + band:.2f}) -- materially pricier than the competitor average. "
            "RECOMMENDATION to match down and defend volume; no price is applied automatically."
        )
    if action == OPORTUNIDAD_SUBIR:
        return (
            f"ABC class={classification.abc}; position_index={idx:.3f} < 1-band ({1 - band:.2f}) "
            "-- materially cheaper than the competitor average. RECOMMENDATION: margin headroom to "
            "raise toward market; no price is applied automatically."
        )
    return (
        f"ABC class={classification.abc}; position_index={idx:.3f} within [{1 - band:.2f}, "
        f"{1 + band:.2f}] -- at market; no change this cycle."
    )


def run(payload: dict) -> PricePriorityReport:
    """Join ABC-XYZ classification with the price-position signal on
    product_id and assign one pricing action per joined SKU.

    ``payload`` (as built by :func:`prepare`, or hand-built for tests):
      - ``classifications``: ``list[SkuClassification]`` (required)
      - ``price_positions``: ``list[SkuPricePosition]`` (required)
      - ``band``: optional override, default ``_DEFAULT_BAND`` (0.05)
    """
    classifications: list[SkuClassification] = payload["classifications"]
    price_positions: list[SkuPricePosition] = payload["price_positions"]
    band = float(payload.get("band", _DEFAULT_BAND))

    # Duplicate product_id in an input resolves to its LAST occurrence
    # (matches jobs.seo_priority / jobs.markdown_liquidation_job precedent).
    class_by_pid = {c.product_id: c for c in classifications}
    position_by_pid = {p.product_id: p for p in price_positions}

    class_pids = set(class_by_pid)
    position_pids = set(position_by_pid)
    joined_pids = sorted(class_pids & position_pids)

    excluded: list[ExcludedSku] = [
        ExcludedSku(pid, "present in ABC-XYZ classification but missing from the price-position input")
        for pid in sorted(class_pids - position_pids)
    ] + [
        ExcludedSku(pid, "present in the price-position input but missing from ABC-XYZ classification")
        for pid in sorted(position_pids - class_pids)
    ]
    excluded.sort(key=lambda e: e.product_id)

    actions: list[SkuPriceAction] = []
    for pid in joined_pids:
        classification = class_by_pid[pid]
        position = position_by_pid[pid]
        action = _assign_action(classification, position, band)
        reason = _reason(action, classification, position, band)
        actions.append(SkuPriceAction(
            product_id=pid, action=action, abc=classification.abc, xyz=classification.xyz,
            position_index=position.position_index, competitor_read=position.competitor_read,
            reason=reason,
        ))

    n_igualar = sum(1 for a in actions if a.action == IGUALAR_PRECIO)
    n_subir = sum(1 for a in actions if a.action == OPORTUNIDAD_SUBIR)
    n_vigilar = sum(1 for a in actions if a.action == VIGILAR)
    n_ignorar = sum(1 for a in actions if a.action == IGNORAR_BAJO_VALOR)

    summary = (
        f"Price priority plan over {len(actions)} SKU(s) (ABC-XYZ x price-position join): "
        f"{n_igualar} igualar_precio, {n_subir} oportunidad_subir, {n_vigilar} vigilar, "
        f"{n_ignorar} ignorar_bajo_valor."
    )
    if excluded:
        shown = ", ".join(e.product_id for e in excluded[:5]) + ("..." if len(excluded) > 5 else "")
        summary += f" {len(excluded)} SKU(s) excluded for missing input: {shown}."

    return PricePriorityReport(
        actions=tuple(actions), excluded=tuple(excluded),
        n_igualar_precio=n_igualar, n_oportunidad_subir=n_subir, n_vigilar=n_vigilar,
        n_ignorar_bajo_valor=n_ignorar, n_excluded=len(excluded), band=band, summary=summary,
    )


def verify(report: PricePriorityReport) -> list[str]:
    """QA gate (matches ``jobs/qa.py``'s ``verify_*`` naming). Empty list = passed.

    Checks: SKUs are accounted for (never silently dropped), every action and
    every ``competitor_read`` is one of the honest enumerated labels, a
    price-changing action (igualar_precio/oportunidad_subir) never fires
    without a confirmed competitor read, and every action/excluded row
    carries a non-empty, citable reason.
    """
    issues: list[str] = []
    if not report.actions and not report.excluded:
        issues.append("no SKUs assessed (empty ABC-XYZ and price-position inputs)")
    total = report.n_igualar_precio + report.n_oportunidad_subir + report.n_vigilar + report.n_ignorar_bajo_valor
    if total != len(report.actions):
        issues.append("action counts do not match len(actions)")

    valid_actions = {IGUALAR_PRECIO, OPORTUNIDAD_SUBIR, VIGILAR, IGNORAR_BAJO_VALOR}
    valid_reads = {CONFIRMED, INSUFFICIENT_SIGNAL}
    price_changing_actions = {IGUALAR_PRECIO, OPORTUNIDAD_SUBIR}
    for a in report.actions:
        if a.action not in valid_actions:
            issues.append(f"{a.product_id}: invalid action {a.action!r}")
        if a.competitor_read not in valid_reads:
            issues.append(f"{a.product_id}: invalid competitor_read {a.competitor_read!r}")
        if not a.reason:
            issues.append(f"{a.product_id}: action has no citable reason")
        if a.competitor_read == INSUFFICIENT_SIGNAL and a.action in price_changing_actions:
            issues.append(
                f"{a.product_id}: {a.action} fired without a confirmed competitor read "
                "(fabricated price position)"
            )
        if a.position_index is None and a.competitor_read != INSUFFICIENT_SIGNAL:
            issues.append(f"{a.product_id}: missing position_index but competitor_read={a.competitor_read!r}")
        if a.position_index is not None and a.competitor_read == INSUFFICIENT_SIGNAL:
            issues.append(f"{a.product_id}: has a position_index but competitor_read=insufficient_signal")

    for e in report.excluded:
        if not e.reason:
            issues.append(f"{e.product_id}: excluded without a reason")

    return issues


def price_priority_passed(report: PricePriorityReport) -> bool:
    return not verify(report)


def write_operational(report: PricePriorityReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per joined SKU (action +
    citable reason) plus the excluded SKUs, so nothing drops silently
    (golden rule 14). An empty action list still writes a header-only CSV
    (mirrors ``jobs.seo_priority.write_operational``)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    action_path = d / "price_priority.csv"
    if not report.actions:
        pd.DataFrame(columns=list(_ACTION_CSV_COLUMNS)).to_csv(action_path, index=False)
        out: dict[str, Path] = {"csv": action_path}
    else:
        action_rows = [
            {
                "product_id": a.product_id,
                "action": a.action,
                "abc": a.abc,
                "xyz": a.xyz,
                "position_index": a.position_index,
                "competitor_read": a.competitor_read,
                "reason": a.reason,
            }
            for a in report.actions
        ]
        out = {"csv": write_summary_csv(action_rows, action_path)}

    excluded_path = d / "price_priority_excluded.csv"
    if report.excluded:
        excluded_rows = [{"product_id": e.product_id, "reason": e.reason} for e in report.excluded]
        out["excluded_csv"] = write_summary_csv(excluded_rows, excluded_path)
    else:
        pd.DataFrame(columns=list(_EXCLUDED_CSV_COLUMNS)).to_csv(excluded_path, index=False)
        out["excluded_csv"] = excluded_path

    return out
