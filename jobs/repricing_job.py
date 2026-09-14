"""Agent-tool wrapper around ``jobs/repricing.py`` -- the multichannel
repricing playbook (Linchpin 3.0 PR-18, plan section 7 P3).

The playbook itself was fully engineered and fully tested and, until this
module, reachable by nobody but a test and ``examples/run_repricing.py``
(gap audit finding #6, 2026-07-13, still open when re-verified 2026-09-12).

**This wrapper STAGES. It never applies, and it never touches a live
channel.** That is the whole design, not a first cut:

* ``jobs.repricing.apply_repricing`` and ``run_channel_repricing`` are NOT
  imported here. There is no code path from a plain-language brief to a
  real price write. Golden Rule 11 -- autonomy over a brand-new external
  write surface is earned with A4 evidence, and a tool that did not exist
  until this PR has none.
* The changeset is staged against an in-memory store seeded from the
  client's OWN stated ``current_price`` column, so the diff is real and the
  guardrail gate runs on real before/after values, without this process
  ever holding a Shopify / MercadoLibre / Odoo credential.
* The outcome is ALWAYS ``HANDOFF``: a reviewed, gate-approved diff plus the
  exact primitives a human runs to apply it. Never OPTIONS, never EXECUTED --
  see :func:`_handoff`.

So this tool answers "what price change would pass our guardrails, and why",
which is the decision layer. Executing it stays in the operator's hands, via
``examples/run_repricing.py``.

Deliberately NOT on the MCP surface: ``webapp/mcp_tool_specs.py`` is opt-in,
and a tool whose whole subject is changing a client's live prices does not
belong on an anonymously reachable read surface. Declared in
``docs/MCP_SERVER.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs.intake import load_table
from nosilo_core import writeback
from nosilo_core.guided import GuidedOutcome, HandoffPacket, Residual, as_handoff
from src.export import write_summary_csv

# ``jobs.repricing`` is imported LAZILY, inside run(), and that is load-bearing.
# It imports ``scm_agent.knowledge``, which executes ``scm_agent/__init__`` ->
# ``orchestrator`` -> ``tools`` -> this module. At module level that closes a
# cycle and a plain ``import jobs.repricing`` dies with a partially-initialized
# module -- which would break examples/run_repricing.py and every existing test
# that imports the playbook directly. Guarded by
# tests/test_repricing_job.py::test_importing_the_playbook_directly_still_works.

# Canonical column names, with the aliases a real client export actually
# carries. ``product_id`` matches the repo's canonical vocabulary (see
# jobs/intake.py); ``sku`` is its long-standing alias.
_SKU_COLUMNS = ("product_id", "sku", "codigo", "código")
_CURRENT_COLUMNS = ("current_price", "price", "precio_actual", "precio")
_PROPOSED_COLUMNS = ("proposed_price", "new_price", "precio_propuesto", "precio_nuevo")
_COST_COLUMNS = ("landed_cost", "unit_cost", "costo")

REQUIRED_COLUMNS = ("product_id", "current_price", "proposed_price")
OPTIONAL_COLUMNS = ("landed_cost",)


def _pick(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lowered = {str(c).strip().lower(): c for c in frame.columns}
    return next((lowered[c] for c in candidates if c in lowered), None)


@dataclass(frozen=True)
class RepricingPlan:
    """What ``prepare`` resolved from the client file plus params."""

    channel: str
    reason: str
    current_prices: dict[str, float]
    proposed_prices: dict[str, float]
    landed_costs: dict[str, float]
    skipped_rows: int
    idempotency_key: str


@dataclass(frozen=True)
class RepricingReport:
    """One staged (or blocked) channel diff, plus everything the operator
    needs to decide. ``changeset`` is ``None`` exactly when ``blocked_reason``
    is set -- the guardrail gate refused, which is a real answer, not a
    failure to report."""

    plan: RepricingPlan
    changeset: writeback.Changeset | None
    blocked_reason: str | None
    summary: str

    @property
    def outcome(self) -> GuidedOutcome:
        return _handoff(self)


def prepare(data_path: str | Path, params: dict) -> RepricingPlan:
    """Resolve the price list and the two REQUIRED params.

    ``channel`` and ``reason`` have no defaults on purpose. ``reason`` is
    what ``stage_repricing`` refuses to fabricate (a blank reason is exactly
    what the gate blocks), and guessing a ``channel`` would mean guessing
    which of a client's storefronts to reprice. Missing either is a
    ``needs_data`` at the boundary, not a generic gate error three layers in.
    """
    channel = str(params.get("channel") or "").strip()
    if not channel:
        raise ValueError("params['channel'] is required (e.g. 'shopify', 'meli', 'odoo')")
    reason = str(params.get("reason") or "").strip()
    if not reason:
        raise ValueError(
            "params['reason'] is required: the pricing guardrail gate blocks a changeset "
            "with a blank reason, so there is no point staging one"
        )

    frame = load_table(data_path)
    sku_col = _pick(frame, _SKU_COLUMNS)
    current_col = _pick(frame, _CURRENT_COLUMNS)
    proposed_col = _pick(frame, _PROPOSED_COLUMNS)
    missing = [
        name for name, col in (
            ("product_id", sku_col), ("current_price", current_col), ("proposed_price", proposed_col),
        ) if col is None
    ]
    if missing:
        raise ValueError(
            f"missing required column(s): {', '.join(missing)} "
            f"(columns present: {', '.join(str(c) for c in frame.columns)})"
        )
    cost_col = _pick(frame, _COST_COLUMNS)

    current: dict[str, float] = {}
    proposed: dict[str, float] = {}
    costs: dict[str, float] = {}
    skipped = 0
    for _, row in frame.iterrows():
        raw_sku = row[sku_col]
        # pd.isna FIRST: a blank sku cell reads back as NaN, and str(nan) is the
        # string "nan" -- truthy, non-empty, and it would sail through a bare
        # `if not sku` straight into a staged price change for a SKU called
        # "nan". Caught by test_unpriceable_rows_are_counted_not_coerced.
        sku = "" if pd.isna(raw_sku) else str(raw_sku).strip()
        cur = pd.to_numeric(row[current_col], errors="coerce")
        new = pd.to_numeric(row[proposed_col], errors="coerce")
        # A row we cannot price is COUNTED and reported, never silently
        # dropped and never coerced to 0.0 -- golden rule 14.
        if not sku or pd.isna(cur) or pd.isna(new) or float(cur) <= 0 or float(new) <= 0:
            skipped += 1
            continue
        current[sku] = float(cur)
        proposed[sku] = float(new)
        if cost_col is not None:
            cost = pd.to_numeric(row[cost_col], errors="coerce")
            if not pd.isna(cost) and float(cost) > 0:
                costs[sku] = float(cost)

    if not proposed:
        raise ValueError("no priceable rows: every row was missing a sku, a current price or a proposed price")

    return RepricingPlan(
        channel=channel, reason=reason, current_prices=current, proposed_prices=proposed,
        landed_costs=costs, skipped_rows=skipped,
        idempotency_key=str(params.get("idempotency_key") or f"repricing-{channel}-1"),
    )


def run(plan: RepricingPlan, *, max_move_pct: float | None = None, kb: object | None = None) -> RepricingReport:
    """Stage the diff through the central pricing guardrail.

    The store is an in-memory one seeded with the client's own stated current
    prices: real before/after for the gate, zero live-channel credentials in
    this process. A gate refusal is CAUGHT and reported as
    ``blocked_reason`` -- the operator needs to see why their proposal was
    refused, which a propagating exception would turn into a generic error.
    """
    from jobs.repricing import RepricingGuardrailBlocked, gated_citations, stage_repricing

    store = writeback.InMemoryStore({sku: {"price": price} for sku, price in plan.current_prices.items()})
    kwargs: dict = {
        "idempotency_key": plan.idempotency_key,
        "reason": plan.reason,
        # `or {}`, no `or None`: un plan sin costos es "no hay datos de costo para
        # estos SKU", una decision explicita y en el registro. `None` significaba
        # otra cosa -- desarmaba el chequeo de precio bajo costo en silencio, y
        # cualquier precio, por debajo de costo que estuviera, pasaba el gate.
        "landed_costs": plan.landed_costs or {},
        # Ground the citations on the playbook's FIXED keyword set with an EMPTY
        # brief, instead of letting stage_repricing default to grounding on the
        # operator's free-text `reason` -- the citation trilogy's fix
        # (#160/#162/#164) applied to the one gate whose zero HARD-BLOCKS the
        # whole changeset (src/pricing_guardrails.py: "no citations that survive
        # the citation gate").
        #
        # Measured on this graph, 2026-09-12, NOT assumed: the free-text path is
        # not fragile today -- five realistic reason lines ("Q4 margin recovery
        # on slow movers", "ajuste por tipo de cambio", ...) each resolved 3-4
        # citations, so nothing blocks right now. The fixed set is chosen for
        # DETERMINISM, not rescue: the same four citations regardless of how the
        # operator words the reason, so a price change can never be refused --
        # or cited differently -- because of phrasing. That property is what the
        # trilogy bought; it is worth having before a reason line that does
        # degrade shows up, not after.
        "candidate_citations": gated_citations("", kb=kb) if kb is not None else gated_citations(""),
    }
    if max_move_pct is not None:
        kwargs["max_move_pct"] = max_move_pct
    if kb is not None:
        kwargs["kb"] = kb
    try:
        changeset = stage_repricing(store, plan.channel, plan.proposed_prices, **kwargs)
    except RepricingGuardrailBlocked as exc:
        return RepricingReport(
            plan=plan, changeset=None, blocked_reason=str(exc),
            summary=f"{plan.channel}: the pricing guardrail refused the proposal -- nothing staged",
        )
    moves = sum(1 for c in changeset.changes if not c.is_noop)
    return RepricingReport(
        plan=plan, changeset=changeset, blocked_reason=None,
        summary=(
            f"{plan.channel}: {moves} price change(s) staged and gate-approved, "
            f"awaiting a human approval before anything is written"
        ),
    )


def verify(report: RepricingReport) -> list[str]:
    """QA issues. A blocked gate is NOT an issue -- it is the gate doing its
    job, and the handoff says so."""
    issues: list[str] = []
    if report.changeset is not None and not any(not c.is_noop for c in report.changeset.changes):
        issues.append("every proposed price equals the current price: there is nothing to approve")
    if report.plan.skipped_rows and not report.plan.proposed_prices:
        issues.append("no priceable rows survived parsing")
    return issues


def _rows(report: RepricingReport) -> list[dict]:
    if report.changeset is None:
        return []
    rows = []
    for change in report.changeset.changes:
        # Change is field-level: before/after are the scalar price, not a dict.
        before, after = change.before, change.after
        move_pct = None
        if isinstance(before, (int, float)) and before and isinstance(after, (int, float)):
            move_pct = round((after - before) / before * 100.0, 2)
        rows.append({
            "product_id": change.entity_id,
            "current_price": before,
            "proposed_price": after,
            "move_pct": move_pct,
            "landed_cost": report.plan.landed_costs.get(change.entity_id),
            "is_noop": change.is_noop,
        })
    return rows


def write_operational(report: RepricingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The staged diff, one row per SKU, for the human who has to approve it."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    return {"csv": write_summary_csv(_rows(report), d / "repricing_staged_changes.csv")}


def _handoff(report: RepricingReport) -> GuidedOutcome:
    """ALWAYS a handoff. Never options, never executed.

    A tool reachable from a plain-language brief must not be able to report
    that it changed a client's live prices, because it cannot: applying is
    ``jobs.repricing.apply_repricing`` under a real signed ``Approval``, and
    this module never calls it. The packet carries the exact primitives so
    the operator is handed a next step rather than a dead end.
    """
    plan = report.plan
    residuals = [Residual(
        description="Applying the change to the live channel, under a signed Approval",
        risk_if_skipped="the proposal stays staged and no price moves -- the safe default",
    )]
    if plan.skipped_rows:
        residuals.append(Residual(
            description=f"{plan.skipped_rows} row(s) had no usable sku/current/proposed price and were not staged",
            risk_if_skipped="those SKUs keep their current price; fix the export and re-run to include them",
        ))

    if report.changeset is None:
        return as_handoff(
            f"{plan.channel}: proposal refused by the pricing guardrail, nothing staged",
            [HandoffPacket(
                title=f"Revise the {plan.channel} price proposal before staging it again",
                steps=[
                    "Read the gate's reason below -- it names the specific check that refused.",
                    "Fix the proposal (a smaller move, a price above landed cost, a real reason line).",
                    "Re-run this tool; the gate runs again on the corrected proposal.",
                ],
                artifact=report.blocked_reason or "",
                data={"channel": plan.channel, "skus_proposed": len(plan.proposed_prices)},
                risk_if_skipped="applying a proposal the guardrail refused is exactly the incident the gate exists to prevent",
            )],
            residuals=residuals,
        )

    moves = [c for c in report.changeset.changes if not c.is_noop]
    return as_handoff(
        report.summary,
        [HandoffPacket(
            title=f"Approve and apply {len(moves)} staged price change(s) on {plan.channel}",
            steps=[
                "Review repricing_staged_changes.csv -- one row per SKU, with the % move.",
                "If you approve, run examples/run_repricing.py against the real channel store.",
                "It calls jobs.repricing.stage_repricing, then writeback.approve(changeset, <your name>),"
                " then apply_repricing, then verify_applied -- which reads the channel back and fails"
                " loudly if a write silently did not land.",
                "This tool does none of those: it stages against an in-memory copy of your own"
                " current prices and stops.",
            ],
            data={
                "channel": plan.channel,
                "idempotency_key": plan.idempotency_key,
                "reason": plan.reason,
                "changes": len(moves),
                "noops": len(report.changeset.changes) - len(moves),
            },
            risk_if_skipped="prices stay as they are; no revenue change, no risk taken",
        )],
        residuals=residuals,
    )
