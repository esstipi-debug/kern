"""Reprice decision bridge — the missing step between the price-watch chain
and the staged repricing playbook.

The competitor chain (``jobs/price_watch.py`` -> ``PriceLedger`` ->
``jobs/price_priority.py``) ends in per-SKU ACTION LABELS
(igualar_precio / oportunidad_subir) that carry no target price, and the
elasticity optimizer (``src/price_optimizer.py``) proposes prices that ignore
the competitor read entirely (its ``CompetitorPriceContext`` is display-only
by design). This module JOINS the two into one NUMERIC proposed price per
SKU, then hands the result to ``jobs/repricing.py``'s already-built staged,
guardrail-gated, approval-TTL'd changeset path
(:func:`stage_from_recommendations`).

Decision rule per SKU (all reuse, no new pricing math — golden rule 1):

- **Elastic + confirmed competitor read**: ``optimize_sku_price`` with
  ``max_price = competitor_avg * (1 + premium_cap)`` — the elasticity optimum
  bounded at (slightly above) market. This is ``igualar_precio`` (p* above
  market gets pulled down to the cap) and ``oportunidad_subir`` (a raise stops
  at market) in one rule.
- **Elastic + no read**: the plain elasticity optimum (no cap to apply); the
  reason says so explicitly.
- **No elasticity signal + confirmed read**: a match-to-market proposal
  bounded by the margin floor, labeled ``basis="competitor_rule"`` — NEVER
  presented as elasticity math (``elasticity_used`` stays ``None``) and NEVER
  staged unless the caller opts in (``prices_for_staging(...,
  include_rule_based=True)``).
- **Cap below the margin floor**: an honest ``conflict_floor_above_market``
  result with NO price — the competitor sells below our floor; matching them
  would breach margin, so a human decides (matches the repo's
  "needs_data, never a fabricated number" posture).
- **No cost / no signal / no read**: ``needs_data`` with a reason. A SKU seen
  in either input is always accounted for (golden rule 14), never dropped.

Boot-chain note: this module is imported LAZILY by ``scm_agent/tools.py``
(inside the tool's hook functions), like every other pricing-intel-adjacent
jobs module — see ``jobs/price_watch_position.py``'s docstring for why.
``jobs.repricing`` (which pulls ``scm_agent.knowledge``) is itself imported
lazily inside :func:`stage_from_recommendations`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs.price_priority import (
    _COMPETITOR_PRICE_COLS,
    _OUR_PRICE_COLS,
    _PRICE_PRODUCT_COLS,
    _pick_column,
)
from jobs.pricing import prepare_pricing
from src.elasticity_batch import estimate_portfolio_elasticities
from src.export import write_summary_csv
from src.price_optimizer import (
    STATUS_NEEDS_DATA as _OPT_NEEDS_DATA,
)
from src.price_optimizer import (
    STATUS_OK as _OPT_OK,
)
from src.price_optimizer import (
    optimize_sku_price,
)

BASIS_ELASTICITY = "elasticity"
BASIS_COMPETITOR_RULE = "competitor_rule"

STATUS_PROPOSED = "proposed"
STATUS_NEEDS_DATA = "needs_data"
STATUS_CONFLICT = "conflict_floor_above_market"

# +5% above the competitor average is the default ceiling for any proposal
# with a confirmed read — same neutral-band width jobs/price_priority.py uses
# around parity (its _DEFAULT_BAND), so "at market" means the same thing on
# both sides of the chain.
_DEFAULT_PREMIUM_CAP = 0.05

_CSV_COLUMNS = (
    "product_id", "status", "basis", "current_price", "proposed_price",
    "landed_cost", "floor_price", "elasticity_used", "competitor_avg",
    "competitor_cap", "position_index", "price_capped", "stageable", "reason",
)

_EPS = 1e-9


@dataclass(frozen=True)
class CompetitorRead:
    """One SKU's already-aggregated competitor price read — carried, never
    re-derived (the position math lives in jobs/price_intelligence.py)."""

    product_id: str
    our_price: float | None
    competitor_avg: float | None
    competitor_min: float | None
    n_obs: int

    @property
    def confirmed(self) -> bool:
        return self.competitor_avg is not None and self.n_obs > 0


@dataclass(frozen=True)
class RepriceProposal:
    """One SKU's outcome: a numeric proposal with its basis, or an honest
    needs_data / conflict result with no price."""

    product_id: str
    status: str  # proposed | needs_data | conflict_floor_above_market
    basis: str | None  # elasticity | competitor_rule | None (no price)
    current_price: float | None
    proposed_price: float | None
    landed_cost: float | None
    elasticity_used: float | None
    position_index: float | None
    competitor_avg: float | None
    competitor_cap: float | None  # the ceiling actually applied, when any
    floor_price: float | None
    floor_applied: bool
    price_capped: bool
    stageable: bool  # True only for elasticity-basis proposals
    reason: str


@dataclass(frozen=True)
class RepriceRecommendReport:
    proposals: tuple[RepriceProposal, ...]
    n_proposed: int
    n_rule_based: int
    n_needs_data: int
    n_conflict: int
    premium_cap: float
    summary: str


def _reads_from_csv(df: pd.DataFrame, params: dict) -> dict[str, CompetitorRead]:
    """Aggregate a lightweight price-position CSV (one row per competitor
    observation) into one read per SKU. Column sniffing reuses
    jobs/price_priority.py's alias tuples verbatim so both consumers accept
    the same client file."""
    product = _pick_column(df, params.get("price_product_col"), _PRICE_PRODUCT_COLS)
    our_col = _pick_column(df, params.get("our_price_col"), _OUR_PRICE_COLS)
    competitor_col = _pick_column(df, params.get("competitor_price_col"), _COMPETITOR_PRICE_COLS)
    missing = [
        name for name, col in (
            ("price_product_col", product), ("our_price_col", our_col),
            ("competitor_price_col", competitor_col),
        ) if col is None
    ]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)} in the price-position CSV; "
            f"pass them in params (columns seen: {cols})"
        )

    our_price: dict[str, float] = {}
    competitor: dict[str, list[float]] = {}
    for _, row in df.iterrows():
        pid = str(row[product]).strip()
        if pd.notna(row[our_col]):
            our_price.setdefault(pid, float(row[our_col]))
        if pd.notna(row[competitor_col]):
            competitor.setdefault(pid, []).append(float(row[competitor_col]))
        else:
            competitor.setdefault(pid, [])

    reads: dict[str, CompetitorRead] = {}
    for pid in sorted(set(our_price) | set(competitor)):
        obs = competitor.get(pid, [])
        reads[pid] = CompetitorRead(
            product_id=pid,
            our_price=our_price.get(pid),
            competitor_avg=(sum(obs) / len(obs)) if obs else None,
            competitor_min=min(obs) if obs else None,
            n_obs=len(obs),
        )
    return reads


def _reads_from_price_report(report: object) -> dict[str, CompetitorRead]:
    """Aggregate a jobs/price_intelligence.py PriceIntelReport (e.g. the one
    a watch cycle assembles via
    jobs/price_watch_position.py::price_report_from_confirmed_pairs) into one
    read per SKU. Duck-typed on .offers / .our_prices / .rows — the same
    public shape jobs/price_priority.py already consumes."""
    offers_by_pid: dict[str, list[float]] = {}
    for offer in report.offers:  # type: ignore[attr-defined]
        if offer.matched_product_id:
            offers_by_pid.setdefault(str(offer.matched_product_id), []).append(float(offer.price_normalized))

    our_prices = {str(k): float(v) for k, v in report.our_prices.items()}  # type: ignore[attr-defined]
    attempted = {str(row.product_id) for row in report.rows}  # type: ignore[attr-defined]
    reads: dict[str, CompetitorRead] = {}
    for pid in sorted(attempted | set(offers_by_pid) | set(our_prices)):
        obs = offers_by_pid.get(pid, [])
        reads[pid] = CompetitorRead(
            product_id=pid,
            our_price=our_prices.get(pid),
            competitor_avg=(sum(obs) / len(obs)) if obs else None,
            competitor_min=min(obs) if obs else None,
            n_obs=len(obs),
        )
    return reads


def prepare(data_path: str | Path, params: dict | None = None) -> dict:
    """Load the two inputs of the bridge: canonical price/quantity history
    (via jobs/pricing.py's own prepare — never re-sniffed here) and the
    competitor position side, supplied either as an already-run
    ``params['price_report']`` (a PriceIntelReport) or a
    ``params['price_position_path']`` CSV — the exact same two-source
    convention jobs/price_priority.py established."""
    params = params or {}
    demand = prepare_pricing(data_path, period=params.get("period", "W"))

    price_report = params.get("price_report")
    if price_report is not None:
        reads = _reads_from_price_report(price_report)
    else:
        position_path = params.get("price_position_path")
        if not position_path:
            raise ValueError(
                "params['price_report'] (a PriceIntelReport) or params['price_position_path'] "
                "(a CSV of product_id/our_price/competitor_price) is required"
            )
        reads = _reads_from_csv(pd.read_csv(position_path), params)

    return {
        "demand": demand,
        "reads": reads,
        "min_margin_pct": float(params.get("min_margin_pct", 0.0)),
        "price_increment": float(params.get("price_increment", 0.0)),
        "premium_cap": float(params.get("premium_cap", _DEFAULT_PREMIUM_CAP)),
    }


def _landed_costs(demand: pd.DataFrame) -> dict[str, float]:
    """Mean real cost per SKU when the client file carries one — the bridge
    NEVER imputes a cost (unlike jobs/pricing.py's cost_ratio fallback):
    an imputed cost under a price that might be staged into a live channel
    is exactly the fabricated-number failure the QA rules exist to stop."""
    if "cost" not in demand.columns:
        return {}
    costs: dict[str, float] = {}
    for pid, grp in demand.groupby("product_id"):
        col = grp["cost"].dropna()
        if not col.empty and float(col.mean()) > 0:
            costs[str(pid)] = float(col.mean())
    return costs


def _current_prices(demand: pd.DataFrame) -> dict[str, float]:
    """LATEST bucket price per SKU — deliberately not the all-history median
    (a stale baseline once a price has already moved)."""
    latest: dict[str, float] = {}
    for pid, grp in demand.groupby("product_id"):
        ordered = grp.sort_values("date")
        latest[str(pid)] = float(ordered["price"].iloc[-1])
    return latest


def _position_index(read: CompetitorRead) -> float | None:
    if read.our_price is None or read.competitor_avg in (None, 0):
        return None
    return read.our_price / read.competitor_avg


def _needs_data(pid: str, reason: str, *, current_price: float | None,
                landed_cost: float | None, read: CompetitorRead | None) -> RepriceProposal:
    return RepriceProposal(
        product_id=pid, status=STATUS_NEEDS_DATA, basis=None,
        current_price=current_price, proposed_price=None, landed_cost=landed_cost,
        elasticity_used=None,
        position_index=_position_index(read) if read else None,
        competitor_avg=read.competitor_avg if read else None,
        competitor_cap=None,
        floor_price=None, floor_applied=False, price_capped=False,
        stageable=False, reason=reason,
    )


def run(payload: dict) -> RepriceRecommendReport:
    """Join elasticity fits with competitor reads into one proposal per SKU
    (see the module docstring's decision rule)."""
    demand: pd.DataFrame = payload["demand"]
    reads: dict[str, CompetitorRead] = payload["reads"]
    min_margin_pct: float = float(payload.get("min_margin_pct", 0.0))
    price_increment: float = float(payload.get("price_increment", 0.0))
    premium_cap: float = float(payload.get("premium_cap", _DEFAULT_PREMIUM_CAP))

    fits = estimate_portfolio_elasticities(demand)
    costs = _landed_costs(demand)
    currents = _current_prices(demand)

    proposals: list[RepriceProposal] = []
    for pid in sorted(set(fits) | set(reads)):
        read = reads.get(pid)
        current = currents.get(pid)
        cost = costs.get(pid)

        if pid not in fits:
            proposals.append(_needs_data(
                pid, "no price/quantity history for this SKU in the demand file "
                     "(present only in the competitor-position input)",
                current_price=current, landed_cost=cost, read=read,
            ))
            continue
        if cost is None:
            proposals.append(_needs_data(
                pid, "no real landed cost in the client file for this SKU -- the bridge never "
                     "imputes a cost under a price that could be staged",
                current_price=current, landed_cost=None, read=read,
            ))
            continue

        floor_price = cost * (1.0 + min_margin_pct)
        confirmed = read is not None and read.confirmed
        cap = read.competitor_avg * (1.0 + premium_cap) if confirmed else None

        if cap is not None and cap < floor_price - _EPS:
            proposals.append(RepriceProposal(
                product_id=pid, status=STATUS_CONFLICT, basis=None,
                current_price=current, proposed_price=None, landed_cost=cost,
                elasticity_used=None, position_index=_position_index(read),
                competitor_avg=read.competitor_avg, competitor_cap=cap,
                floor_price=floor_price, floor_applied=False, price_capped=False,
                stageable=False,
                reason=(
                    f"competitor average {read.competitor_avg:.2f} (+{premium_cap:.0%} cap "
                    f"{cap:.2f}) sits below the margin floor {floor_price:.2f} "
                    f"(landed cost {cost:.2f}, min margin {min_margin_pct:.0%}) -- matching "
                    "the market would breach margin; a human decides (match, exit, or renegotiate cost)"
                ),
            ))
            continue

        opt = optimize_sku_price(
            fits[pid], landed_cost=cost, current_price=current,
            min_margin_pct=min_margin_pct, price_increment=price_increment,
            max_price=cap,
        )

        if opt.status == _OPT_OK:
            capped_note = (
                f"; capped at competitor avg {read.competitor_avg:.2f} +{premium_cap:.0%}"
                if opt.price_capped and confirmed else ""
            )
            read_note = "" if confirmed else "; no confirmed competitor read this cycle (uncapped)"
            proposals.append(RepriceProposal(
                product_id=pid, status=STATUS_PROPOSED, basis=BASIS_ELASTICITY,
                current_price=current, proposed_price=opt.proposed_price, landed_cost=cost,
                elasticity_used=opt.elasticity_used,
                position_index=_position_index(read) if read else None,
                competitor_avg=read.competitor_avg if read else None,
                competitor_cap=cap,
                floor_price=floor_price, floor_applied=opt.floor_applied,
                price_capped=opt.price_capped, stageable=True,
                reason=(
                    f"elasticity optimum (eps={opt.elasticity_used:.3f}) over landed cost "
                    f"{cost:.2f}{capped_note}{read_note}"
                ),
            ))
            continue

        assert opt.status == _OPT_NEEDS_DATA
        if confirmed:
            # Match-to-market rule: explicitly labeled, never staged by default.
            target = max(read.competitor_avg, floor_price)
            if price_increment > 0:
                from src.constraints import apply_order_rules
                target = float(apply_order_rules(
                    target, minimum_order_quantity=floor_price, order_multiple=price_increment,
                ))
            proposals.append(RepriceProposal(
                product_id=pid, status=STATUS_PROPOSED, basis=BASIS_COMPETITOR_RULE,
                current_price=current, proposed_price=float(target), landed_cost=cost,
                elasticity_used=None, position_index=_position_index(read),
                competitor_avg=read.competitor_avg,
                competitor_cap=cap,
                floor_price=floor_price,
                floor_applied=target > read.competitor_avg + _EPS,
                price_capped=False, stageable=False,
                reason=(
                    f"no elasticity signal ({opt.reason}); RULE-BASED match to the confirmed "
                    f"competitor average {read.competitor_avg:.2f} over {read.n_obs} observation(s), "
                    f"bounded by the margin floor {floor_price:.2f} -- explicitly not elasticity "
                    "math, excluded from staging unless opted in"
                ),
            ))
        else:
            proposals.append(_needs_data(
                pid, f"no elasticity signal ({opt.reason}) and no confirmed competitor read "
                     "this cycle -- no honest basis for a price",
                current_price=current, landed_cost=cost, read=read,
            ))

    n_proposed = sum(1 for p in proposals if p.status == STATUS_PROPOSED)
    n_rule = sum(1 for p in proposals if p.basis == BASIS_COMPETITOR_RULE)
    n_needs = sum(1 for p in proposals if p.status == STATUS_NEEDS_DATA)
    n_conflict = sum(1 for p in proposals if p.status == STATUS_CONFLICT)
    summary = (
        f"Reprice bridge over {len(proposals)} SKU(s): {n_proposed} proposed "
        f"({n_proposed - n_rule} elasticity-based stageable, {n_rule} rule-based labeled), "
        f"{n_needs} needs_data, {n_conflict} floor-vs-market conflict(s)."
    )
    return RepriceRecommendReport(
        proposals=tuple(proposals), n_proposed=n_proposed, n_rule_based=n_rule,
        n_needs_data=n_needs, n_conflict=n_conflict, premium_cap=premium_cap,
        summary=summary,
    )


def verify(report: RepriceRecommendReport) -> list[str]:
    """QA gate (jobs/qa.py's verify_* naming). Empty list = passed.

    Invariants: enumerated statuses/bases; a proposal always respects its own
    floor and cap; conflict/needs_data never carry a price; rule-based is
    never stageable and never fires without a confirmed read; every row has
    a citable reason; counts reconcile."""
    issues: list[str] = []
    valid_status = {STATUS_PROPOSED, STATUS_NEEDS_DATA, STATUS_CONFLICT}
    valid_basis = {BASIS_ELASTICITY, BASIS_COMPETITOR_RULE, None}

    for p in report.proposals:
        if p.status not in valid_status:
            issues.append(f"{p.product_id}: invalid status {p.status!r}")
        if p.basis not in valid_basis:
            issues.append(f"{p.product_id}: invalid basis {p.basis!r}")
        if not p.reason:
            issues.append(f"{p.product_id}: no citable reason")

        if p.status == STATUS_PROPOSED:
            if p.proposed_price is None:
                issues.append(f"{p.product_id}: proposed without a price")
                continue
            if p.floor_price is not None and p.proposed_price < p.floor_price - _EPS:
                issues.append(
                    f"{p.product_id}: proposed price {p.proposed_price:.2f} below the margin "
                    f"floor {p.floor_price:.2f}"
                )
            if p.competitor_cap is not None and p.proposed_price > p.competitor_cap + _EPS:
                issues.append(
                    f"{p.product_id}: proposed price {p.proposed_price:.2f} above the competitor "
                    f"cap {p.competitor_cap:.2f}"
                )
            if p.basis == BASIS_COMPETITOR_RULE:
                if p.stageable:
                    issues.append(f"{p.product_id}: rule-based proposal marked stageable")
                if p.elasticity_used is not None:
                    issues.append(f"{p.product_id}: rule-based proposal dressed as elasticity math")
                if p.competitor_avg is None:
                    issues.append(
                        f"{p.product_id}: rule-based proposal without a confirmed competitor read"
                    )
            if p.basis == BASIS_ELASTICITY and not p.stageable:
                issues.append(f"{p.product_id}: elasticity proposal not marked stageable")
            if p.basis is None:
                issues.append(f"{p.product_id}: proposed without a basis")
        else:
            if p.proposed_price is not None:
                label = "conflict" if p.status == STATUS_CONFLICT else "needs_data"
                issues.append(f"{p.product_id}: {label} result still carries a price")
            if p.stageable:
                issues.append(f"{p.product_id}: non-proposed row marked stageable")

    n_proposed = sum(1 for p in report.proposals if p.status == STATUS_PROPOSED)
    if n_proposed != report.n_proposed:
        issues.append("n_proposed does not match the proposal rows")
    return issues


def reprice_recommend_passed(report: RepriceRecommendReport) -> bool:
    return not verify(report)


def prices_for_staging(
    report: RepriceRecommendReport, *, include_rule_based: bool = False,
) -> dict[str, float]:
    """The {sku: price} map to hand to the staged repricing path. Default:
    ONLY elasticity-basis proposals (stageable). ``include_rule_based=True``
    is the caller's explicit opt-in to also stage the labeled match-to-market
    rows -- mirroring jobs/repricing.py's posture that autonomy is opted
    into, never defaulted."""
    out: dict[str, float] = {}
    for p in report.proposals:
        if p.status != STATUS_PROPOSED or p.proposed_price is None:
            continue
        if p.stageable or (include_rule_based and p.basis == BASIS_COMPETITOR_RULE):
            out[p.product_id] = float(p.proposed_price)
    return out


def stage_from_recommendations(
    store: object,
    channel: str,
    report: RepriceRecommendReport,
    *,
    idempotency_key: str,
    reason: str,
    include_rule_based: bool = False,
    kb: object | None = None,
    candidate_citations: list | None = None,
):
    """Glue into jobs/repricing.py: select the stageable prices and stage ONE
    channel's dry-run, guardrail-gated Changeset (never applied here --
    apply/verify stay with jobs/repricing.py's approval-TTL'd path).
    Raises ValueError when nothing is stageable rather than staging an empty
    changeset a human would approve for no effect."""
    prices = prices_for_staging(report, include_rule_based=include_rule_based)
    if not prices:
        raise ValueError(
            "no stageable prices in this report (elasticity-basis proposals only by default; "
            "pass include_rule_based=True to opt the labeled match-to-market rows in)"
        )
    from jobs.repricing import stage_repricing  # lazy: pulls scm_agent.knowledge
    return stage_repricing(
        store, channel, prices,
        idempotency_key=idempotency_key, reason=reason,
        kb=kb, candidate_citations=candidate_citations,
    )


def write_operational(
    report: RepriceRecommendReport, out_dir: str | Path, client: str = "Client",
) -> dict[str, Path]:
    """One row per SKU -- proposed, needs_data and conflict alike (golden
    rule 14: nothing drops silently). An empty report still writes a
    header-only CSV (mirrors jobs/price_priority.py)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "reprice_recommendations.csv"
    if not report.proposals:
        pd.DataFrame(columns=list(_CSV_COLUMNS)).to_csv(path, index=False)
        return {"csv": path}
    rows = [
        {
            "product_id": p.product_id,
            "status": p.status,
            "basis": p.basis,
            "current_price": p.current_price,
            "proposed_price": p.proposed_price,
            "landed_cost": p.landed_cost,
            "floor_price": p.floor_price,
            "elasticity_used": p.elasticity_used,
            "competitor_avg": p.competitor_avg,
            "competitor_cap": p.competitor_cap,
            "position_index": p.position_index,
            "price_capped": p.price_capped,
            "stageable": p.stageable,
            "reason": p.reason,
        }
        for p in report.proposals
    ]
    return {"csv": write_summary_csv(rows, path)}
