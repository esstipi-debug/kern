"""Ranked, executable options on success - the Guided Execution Layer applied to the happy path.

Every tool, on a successful run, should hand the user >=2 ranked, executable choices with one
recommended default, not just a dashboard. These builders map each tool's report to that
``GuidedOutcome`` (OPTIONS); they are wired via ``Tool.options`` in tools.py. Tools whose report
already carries a ranked outcome (sourcing, sop, returns) reuse ``report.outcome`` directly.

Each builder reads only its report's public fields and returns a protected options outcome.
"""
from __future__ import annotations

from src.guided import ExecutionOption, GuidedOutcome, as_options

# Each item is (label, summary, action, tradeoffs); the first item is the recommended default.
_Item = tuple[str, str, str, str]


def _ranked(summary: str, items: list[_Item], *, confidence: float = 0.85) -> GuidedOutcome:
    """Build a protected OPTIONS outcome from ranked items (first = recommended)."""
    options = [
        ExecutionOption(
            label=label, summary=text, score=float(len(items) - i),
            action=action, tradeoffs=tradeoffs, recommended=(i == 0),
        )
        for i, (label, text, action, tradeoffs) in enumerate(items)
    ]
    return as_options(summary, options, confidence=confidence)


def inventory_options(report: object) -> GuidedOutcome:
    sl = report.params.get("service_level", 0.95)
    n_review = sum(1 for r in report.recommendations if getattr(r, "status", "ok") != "ok")
    items: list[_Item] = [
        ("Adopt the recommended policy",
         f"Stage {report.final_investment:,.0f} of inventory under the (s,Q)/(R,S) policies at {sl * 100:.0f}% service.",
         "apply the recommended per-SKU policies and budget", "balanced service vs capital"),
        ("Tighten service on A-items",
         "Raise the cycle service level on the high-value SKUs (more safety stock).",
         "raise service level on the A class", "higher availability, more capital"),
        ("Free capital - defer low-value SKUs",
         f"Trim or defer the {n_review} flagged SKU(s) to release budget.",
         "defer / review the flagged low-value SKUs", "less capital, some service risk"),
    ]
    return _ranked(f"Inventory policy for {len(report.recommendations)} SKU(s): choose how to act.", items)


def pricing_options(report: object) -> GuidedOutcome:
    apply = ("Apply the confident price moves",
             f"Roll out the {report.n_actionable} confident raise/lower move(s).",
             "apply the recommended prices", "captures the margin uplift now")
    pilot = ("Pilot on the top movers first",
             "A/B test the highest-uplift SKUs before a full roll-out.",
             "stage a price test on the top movers", "lower risk, slower")
    hold = ("Hold the inelastic SKUs",
            f"Leave the {report.n_inelastic} inelastic SKU(s) unchanged.",
            "no change where elasticity is weak", "avoids volume loss")
    items = [apply, pilot, hold] if report.n_actionable > 0 else [pilot, hold, apply]
    return _ranked(f"Pricing across {report.n_skus} SKU(s): {report.n_actionable} actionable.", items)


def leadership_options(profile: object) -> GuidedOutcome:
    items: list[_Item] = [
        (f"Act on the priority lever: {profile.lever_name}",
         f"Develop {profile.lever_name} ({profile.lever_code}) - the lowest CHAIN dimension.",
         f"run the {profile.lever_code} directives", "closes the biggest gap first"),
        (f"Reinforce the {profile.archetype} strength",
         "Double down on the dominant archetype to compound it.",
         "amplify the archetype strength", "leverages an existing strength"),
        ("Balanced CHAIN development",
         "Even uplift across all five CHAIN dimensions.",
         "run a balanced development plan", "well-rounded but slower"),
    ]
    return _ranked(f"CHAIN {profile.average:.1f}/4, archetype {profile.archetype}: choose a focus.", items)


def cost_to_serve_options(report: object) -> GuidedOutcome:
    segments = report.portfolio.segments
    losers = [s for s in segments if s.net_to_serve < 0]
    worst = segments[-1] if segments else None
    fix: _Item = (
        "Fix the loss-making segments",
        f"{len(losers)} segment(s) lose money to serve"
        + (f" (worst: {worst.segment})" if worst is not None else "")
        + "; re-price or add order minimums.",
        "re-price / add minimums on the negative net-to-serve segments", "protects margin directly",
    )
    reduce: _Item = (
        "Reduce cost-to-serve",
        "Renegotiate freight, consolidate shipments, or cut returns handling.",
        "attack the largest cost-to-serve pool", "structural, slower payoff",
    )
    cash = None
    if report.cash_release is not None:
        cash = (
            "Release working capital",
            f"Free ~{report.cash_release.total_cash_released:,.0f} by tightening the cash cycle.",
            "cut DIO/DSO to release cash", "cash now, operational effort",
        )
    if losers:
        items = [fix] + ([cash] if cash else []) + [reduce]
    elif cash:
        items = [cash, reduce, fix]
    else:
        items = [reduce, fix]
    return _ranked("Cost-to-serve portfolio: choose how to act on the losers.", items)


def abc_xyz_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Tighten control on the A class",
         f"{report.n_a} A-SKU(s) hold {report.a_value_share * 100:.0f}% of value - review weekly, raise service.",
         "apply tight control + high service to the A class", "protects the value that matters"),
        ("Rationalize the CZ candidates",
         f"Cut or make-to-order the {report.n_cz} erratic low-value SKU(s).",
         "discontinue / MTO the CZ cell", "reduces complexity and frees cash"),
        ("Automate the standard cells",
         "Put the stable B/C cells on automated reorder.",
         "automate reorder for the stable cells", "operational efficiency"),
    ]
    return _ranked(f"ABC-XYZ over {report.n_skus} SKU(s): choose the policy moves.", items)


def ddmrp_options(report: object) -> GuidedOutcome:
    release: _Item = ("Release the recommended orders",
                      f"{report.total_order_qty:,.0f} units across {report.n_order} part(s) at/below buffer.",
                      "release the net-flow orders now", "restores buffer coverage")
    expedite: _Item = ("Expedite the red-zone parts",
                       f"Push the {report.n_red} part(s) in the red first.",
                       "expedite the red parts", "protects availability")
    reprofile: _Item = ("Re-profile chronic buffers",
                        "Adjust buffer profiles for parts that penetrate red often.",
                        "review and re-size buffer profiles", "structural fix")
    if report.n_order > 0:
        items = [release, expedite, reprofile]
    elif report.n_red > 0:
        items = [expedite, reprofile, release]
    else:
        items = [("Hold - buffers healthy", "No parts below buffer; monitor.",
                  "monitor; no action needed", "no cost"), reprofile]
    return _ranked(f"DDMRP over {report.n_parts} part(s): choose the execution move.", items)


def landed_cost_options(report: object) -> GuidedOutcome:
    top = report.lines[0] if report.lines else None
    leg = "freight" if report.total_freight >= report.total_duty else "duty"
    items: list[_Item] = [
        ("Cost-down the top landed-cost SKU",
         (f"{top.sku} at {top.landed.total:,.0f} landed - renegotiate or re-source." if top else
          "Renegotiate or re-source the highest landed-cost SKU."),
         "renegotiate / re-source the top landed-cost SKU", "biggest single lever"),
        (f"Attack the largest cost leg ({leg})",
         f"Freight {report.total_freight:,.0f} vs duty {report.total_duty:,.0f}.",
         f"renegotiate {leg}", "targets the biggest adder"),
        ("Review Incoterm / duty classification",
         "Shift the Incoterm or verify HS codes to cut the duty base.",
         "review Incoterm + HS classification", "commercial / compliance"),
    ]
    return _ranked(f"Landed cost over {report.n_lines} SKU(s): choose the cost-down lever.", items)


def financial_kpis_options(report: object) -> GuidedOutcome:
    worst = report.worst[0] if report.worst else None
    markdown: _Item = ("Markdown / delist the weakest GMROI SKUs",
                       f"Bottom GMROI: {worst.product_id if worst else 'n/a'} - markdown, re-buy less, or delist.",
                       "markdown / delist the bottom-GMROI SKUs", "frees cash, lifts portfolio GMROI")
    dio: _Item = ("Cut DIO to release working capital",
                  f"DIO {report.dio:.0f} days; each day cut releases cash.",
                  "reduce days inventory outstanding", "cash now")
    floors: _Item = ("Set GMROI / turns floors by ABC class",
                     "Govern with a minimum GMROI and turns per class.",
                     "set per-class KPI floors", "structural discipline")
    items = [markdown, dio, floors] if (report.gmroi < 1.0 or report.turns < 4.0) else [dio, floors, markdown]
    return _ranked(f"Inventory finance: GMROI {report.gmroi:.2f}, {report.turns:.1f} turns - choose a lever.", items)


def reconciliation_options(report: object) -> GuidedOutcome:
    worst = report.worst[0] if report.worst else None
    root: _Item = ("Root-cause + recount the worst variances",
                   "Investigate the top $ discrepancies"
                   + (f" (worst: {worst.product_id})" if worst is not None else "") + ".",
                   "root-cause and recount the top-variance SKUs", "fixes the biggest errors")
    cadence: _Item = ("Raise A-item cycle-count frequency",
                      "Count high-value SKUs more often until IRA holds.",
                      "increase cycle-count cadence on A items", "sustains accuracy")
    accept: _Item = ("Accept - IRA above target",
                     f"IRA {report.ira * 100:.0f}% meets the ~97% bar; monitor.",
                     "monitor; no corrective action", "no cost")
    items = [root, cadence] if report.ira < 0.97 else [accept, cadence, root]
    return _ranked(f"Inventory accuracy IRA {report.ira * 100:.0f}%: choose the corrective move.", items)


def whatif_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        (f"Hedge the top driver: {report.top_driver}",
         f"'{report.top_driver}' swings the outcome most - monitor and hedge it.",
         "monitor / hedge the most sensitive driver", "cuts the biggest risk"),
        ("Set the break-even trip-wire",
         (f"Alert when {report.top_driver} crosses {report.breakeven_value:,.2f}." if report.breakeven_found
          else "No break-even in band; revisit if the band widens."),
         "set a trip-wire on the top driver", "early warning"),
        ("Plan to the pessimistic corner",
         f"Size contingency to the worst case ({report.pessimistic_value:,.0f}).",
         "budget contingency to the pessimistic corner", "robust, more cost"),
    ]
    return _ranked(f"Sensitivity: '{report.top_driver}' dominates - choose how to de-risk.", items)
