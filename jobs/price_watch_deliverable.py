"""Deliverable writer for the discovery-assisted watch cycle (Task 11 / PR-11's
agent-tool wiring) -- kept in its own small module (matching the established
``cost_to_serve_deliverable.py`` / ``inventory_deliverable.py`` split) rather
than added to ``jobs/price_watch.py``, which is already near the repo's
800-line file cap (see that module's own docstring and the plan's
Consolidated Risk Register).

Deliberately NO ``jobs.price_watch``/``jobs.price_intelligence``/
``jobs.price_priority``/``scm_agent`` import at THIS module's top level --
this module is imported eagerly at the top of ``scm_agent/tools.py``
alongside every other ``*_deliverable`` module, and an eager import of any of
those (each of which itself imports ``scm_agent.events``/``scm_agent.
citation_gate``/``scm_agent.knowledge``) would recreate the exact circular-
import hazard ``price_intelligence_tool()`` documents. ``write_operational``
duck-types on the report's public fields, and the OPTIONAL extra deliverables
below (Finding 1 of the final whole-branch review) each use a lazy,
function-local import -- the same recipe every ``scm_agent/tools.py`` hook
already uses -- so this module's own top level stays import-hazard-free.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.export import write_summary_csv

_CYCLE_OUTCOME_COLUMNS: tuple[str, ...] = (
    "site", "competitor_sku_ref", "matched_product_id", "status", "reason",
)


def write_operational(report: object, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """One row per confirmed pair checked this cycle -- ``price_watch_cycle.csv``
    (``site, competitor_sku_ref, matched_product_id, status, reason``). Always
    written, even with zero pairs checked (a stable header, the same
    "nothing to report" idiom ``jobs.price_watch.write_homologation`` and
    ``jobs.markdown_liquidation_job`` already use) -- never a missing file
    with no explanation (golden rule 14). Every string cell passes through
    ``src.sanitize.defuse_formula`` via ``write_summary_csv`` (OWASP CSV
    injection), the same convention every other CSV deliverable in this repo
    uses.

    ``report.pending_escalations`` / ``report.scaled_watches`` (Task 9's R5
    output) are deliberately NOT written here -- they are surfaced through the
    Guided Execution Layer (``scm_agent.tool_options.price_watch_options``),
    never a second, silent channel for the same escalation.

    Finding 1 (final whole-branch review): when ``report`` also carries
    ``homologation``/``price_report``/``priority`` (a
    ``jobs.price_watch_position.PriceWatchToolReport`` bundle -- see that
    module's docstring), the FULL deliverable set is written too --
    ``homologation_table.csv``(+ unmatched), ``price_position_matrix.xlsx``
    (+ ledger export), and ``price_priority.csv`` (+ excluded) -- by calling
    the SAME already-tested writers ``examples/run_price_watch.py``'s CLI
    uses, never a second implementation. A bare report without those
    attributes (e.g. a plain ``PriceWatchCycleReport``, what every existing
    caller of this function still passes) writes ONLY the cycle CSV, exactly
    as before -- fully backward compatible.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    path = d / "price_watch_cycle.csv"
    outcomes = list(report.outcomes)
    if outcomes:
        rows = [
            {
                "site": o.site,
                "competitor_sku_ref": o.competitor_sku_ref,
                "matched_product_id": o.matched_product_id,
                "status": o.status,
                "reason": o.reason,
            }
            for o in outcomes
        ]
        written = {"csv": write_summary_csv(rows, path)}
    else:
        pd.DataFrame(columns=list(_CYCLE_OUTCOME_COLUMNS)).to_csv(path, index=False)
        written = {"csv": path}

    homologation = getattr(report, "homologation", None)
    if homologation is not None:
        from jobs.price_watch import write_homologation

        w_hom = write_homologation(homologation, d, client)
        written["homologation_table"] = w_hom["csv"]
        written["homologation_unmatched"] = w_hom["unmatched_csv"]

    price_report = getattr(report, "price_report", None)
    if price_report is not None:
        from jobs.price_intelligence import write_operational as write_price_intel_operational

        w_matrix = write_price_intel_operational(price_report, d, client)
        written["price_position_matrix"] = w_matrix["matrix"]
        written["ledger_export"] = w_matrix["ledger_csv"]

    priority = getattr(report, "priority", None)
    if priority is not None:
        from jobs.price_priority import write_operational as write_priority_operational

        w_priority = write_priority_operational(priority, d, client)
        written["price_priority"] = w_priority["csv"]
        written["price_priority_excluded"] = w_priority["excluded_csv"]

    # `client` accepted for interface symmetry with every other write_operational
    # in this repo -- this CSV has no per-client Summary sheet of its own.
    _ = client
    return written
