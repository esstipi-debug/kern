"""GuidedOutcome wrapper for a MercadoLibre preventa scan.

Split from ``src.meli_presale`` on purpose: classification and drafts are
core-free (no-core CI + this Cloud Agent image without ``nosilo-core``),
while the never-unprotected OPTIONS/HANDOFF contract lives here, next to
every other ``as_options`` / ``as_handoff`` call in ``src/``.

Never EXECUTED -- publishing a listing is a human POST this package does
not perform.
"""

from __future__ import annotations

from nosilo_core.guided import (
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_handoff,
    as_options,
)

from src.meli_presale import PRESALE_READY, PresaleScan


def outcome_for(scan: PresaleScan) -> GuidedOutcome:
    """Wrap classified candidates. OPTIONS when something is draftable."""
    candidates = scan.candidates
    ready = [c for c in candidates if c.status == PRESALE_READY]
    residual = Residual(
        description="No listing is published from this scan. A draft is a suggestion.",
        risk_if_skipped="Publishing from the draft without a human review can create a "
                        "live preventa against stock the seller does not have.",
    )
    if ready:
        options = [
            ExecutionOption(
                label=c.product.product_id if c.product else c.query.q,
                summary=c.product.name if c.product else c.reason,
                score=1.0 if c.product and c.product.gtin else 0.7,
                action="draft-only: never POST /items",
                tradeoffs=c.reason,
            )
            for c in ready
        ]
        options.sort(key=lambda o: o.score, reverse=True)
        return as_options(
            f"{len(ready)} catalog product(s) are ready for a preventa draft; none were published.",
            options,
            residuals=[residual],
        )
    lines = [f"{c.status}: {c.reason}" for c in candidates] or ["no queries"]
    packet = HandoffPacket(
        title="MercadoLibre preventa: nothing ready to draft",
        steps=[
            "Confirm the search terms / GTINs against the official catalog.",
            "If a hit is catalog_only, list without MANUFACTURING_TIME or pick another category.",
            "If a hit is already_listed, edit the existing item instead of creating a new one.",
        ],
        artifact="\n".join(lines),
        data={"statuses": [c.status for c in candidates]},
        risk_if_skipped="The operator has no ranked preventa draft and may publish blind.",
    )
    return as_handoff(
        "No catalog product is ready for a preventa draft.",
        [packet],
        residuals=[residual],
    )


def scan_presale(scan: PresaleScan) -> PresaleScan:
    """Attach a GuidedOutcome to an already-classified scan."""
    return PresaleScan(
        candidates=scan.candidates,
        listed_checked=scan.listed_checked,
        outcome=outcome_for(scan),
    )


__all__ = ["outcome_for", "scan_presale"]
