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


def warehouse_options(layout: object) -> GuidedOutcome:
    b = layout.building
    n_racks = len(layout.racks)
    n_slots = len(layout.slots)
    n_docks = len(layout.docks)
    items: list[_Item] = [
        ("Adopt this layout",
         f"Use the generated {n_racks}-rack / {n_slots}-slot layout as the baseline.",
         "adopt the generated layout as the baseline", "balanced storage vs access"),
        ("Densify storage",
         "Narrow the aisles and add rack modules to raise slot capacity.",
         "narrow aisles and add rack modules", "more capacity, tighter forklift access"),
        ("Boost throughput",
         f"Add dock doors and widen the main aisle for faster flow (now {n_docks} docks).",
         "add docks and widen the main aisle", "more throughput, less storage"),
    ]
    return _ranked(
        f"Warehouse {b.width_m:.0f}x{b.depth_m:.0f} m, {n_racks} racks / {n_slots} slots, "
        f"{n_docks} docks: choose how to refine.",
        items,
    )


def queuing_options(report: object) -> GuidedOutcome:
    busiest = report.busiest_station
    items: list[_Item] = [
        ("Cost-optimal staffing",
         f"Staff each of the {report.n_stations} station(s) to its min-cost server count (total {report.total_cost:,.0f}).",
         "apply the recommended per-station staffing", "best balance of wait vs labour"),
        (f"Service-first at {busiest}",
         f"Add a server at the busiest point ('{busiest}') to cut the {report.max_wait:.2f} wait.",
         "add a server where the wait is worst", "shorter wait, higher labour"),
        ("Lean staffing",
         "Run each station at the minimum stable server count.",
         "minimize servers across the network", "lowest labour, longer waits"),
    ]
    return _ranked(f"Staffing for {report.n_stations} service point(s): choose the policy.", items)


def scheduling_options(report: object) -> GuidedOutcome:
    spt = report.rule_metrics["SPT"]
    edd = report.rule_metrics["EDD"]
    fcfs = report.rule_metrics["FCFS"]
    by_rule = {
        "SPT": ("Sequence by SPT (fastest throughput)",
                f"Minimizes mean flow time ({spt.mean_flow_time:.2f}).",
                "run shortest-processing-first", "clears work fastest; may miss due dates"),
        "EDD": ("Sequence by EDD (protect due dates)",
                f"Minimizes maximum lateness ({edd.max_lateness:.2f}).",
                "run earliest-due-date-first", "best on-time; slower mean flow"),
        "FCFS": ("Sequence by FCFS (fairness)",
                 f"Process in arrival order (flow {fcfs.mean_flow_time:.2f}).",
                 "run first-come-first-served", "simple and fair; not optimal"),
    }
    rec = report.recommended_rule
    order = [rec] + [r for r in ("SPT", "EDD", "FCFS") if r != rec]
    items: list[_Item] = [by_rule[r] for r in order]
    return _ranked(f"Sequencing {report.n_jobs} job(s): choose the dispatching rule.", items)


def dea_options(report: object) -> GuidedOutcome:
    worst = report.worst_unit
    laggards = report.n_units - report.n_efficient
    items: list[_Item] = [
        (f"Improve the laggards (start with {worst})",
         f"Bring the {laggards} below-frontier unit(s) toward the best peers, starting with '{worst}'.",
         "run improvement plans on the lowest-efficiency units", "biggest efficiency gain"),
        ("Replicate the frontier units",
         f"Standardize the {report.n_efficient} efficient unit(s)' practices across the network.",
         "roll out the frontier playbook", "lifts the whole network"),
        ("Reallocate volume to the efficient units",
         "Shift work toward the units already on the frontier.",
         "reallocate volume to the efficient units", "fast win; capacity-limited"),
    ]
    return _ranked(f"DEA over {report.n_units} unit(s): choose how to close the gap.", items)


def acceptance_sampling_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Adopt the per-part sampling plans",
         f"Inspect {report.total_sample} units across {report.n_parts} part(s) at the recommended (n, c).",
         "apply the receiving inspection plans", "balances risk vs inspection cost"),
        ("Reduce inspection on reliable suppliers",
         "Move parts whose suppliers consistently hold AQL to skip-lot / reduced inspection.",
         "switch proven suppliers to skip-lot", "less inspection; needs supplier history"),
        (f"Tighten on critical parts (e.g. {report.strictest_part})",
         "Lower AQL on safety-critical parts to raise the inspection bar.",
         "tighten AQL on critical parts", "more inspection; lower escape risk"),
    ]
    return _ranked(f"Receiving inspection for {report.n_parts} part(s): choose the posture.", items)


def earned_value_options(report: object) -> GuidedOutcome:
    p = report.portfolio
    worst = report.tasks[0].task if report.tasks else "n/a"
    recover_cost = (f"Recover cost (start with {worst})",
                    f"CPI {p.cpi:.2f}; act on the over-budget tasks first.",
                    "re-scope / re-resource the worst-CPI tasks", "protects budget")
    recover_sched = ("Recover schedule",
                     f"SPI {p.spi:.2f}; fast-track or add resource to the late tasks.",
                     "fast-track the behind-schedule tasks", "protects the date; may cost more")
    hold = ("Hold - on track",
            "SPI and CPI are at/above 1.0; keep executing and monitor.",
            "monitor; no corrective action", "no cost")
    if p.behind_schedule and not p.over_budget:
        items = [recover_sched, recover_cost, hold]
    elif p.over_budget or p.behind_schedule:
        items = [recover_cost, recover_sched, hold]
    else:
        items = [hold, recover_sched, recover_cost]
    return _ranked(f"Project SPI {p.spi:.2f} / CPI {p.cpi:.2f}: choose the recovery move.", items)


def learning_curve_options(report: object) -> GuidedOutcome:
    top = report.products[0].product if report.products else "n/a"
    items: list[_Item] = [
        (f"Commit volume to capture the cost-down (top: {top})",
         f"Lock in the volumes that realize the {report.total_savings:,.0f} learning savings.",
         "commit the high-savings volume", "captures cost-down; volume risk"),
        ("Quote at the projected unit cost",
         "Price using the at-volume unit cost, not the first-unit cost.",
         "quote on the projected unit cost", "wins on price; thinner early margin"),
        ("Negotiate a steeper learning rate",
         "Push process improvement to lower the curve on the high-savings products.",
         "invest in process improvement on the top products", "more cost-down; needs investment"),
    ]
    return _ranked(f"Cost-down across {report.n_products} product(s): choose the lever.", items)


def newsvendor_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Order the recommended quantities",
         f"Commit {report.total_order_qty:,.0f} unit(s) across {report.n_skus} SKU(s) at the "
         f"critical-ratio optimum.",
         "place the single-period order at the recommended quantities",
         "maximizes expected profit for one-shot demand"),
        (f"Protect availability on the scarce SKUs (e.g. {report.scarcest_product})",
         "Round up where a stock-out costs more than overstock (highest critical ratio).",
         "raise the order on the high-critical-ratio SKUs", "fewer stock-outs, more overage risk"),
        (f"Cut overstock risk on thin-margin SKUs (e.g. {report.thinnest_product})",
         "Order below the optimum where overage dominates or salvage is low.",
         "trim the order on the low-critical-ratio SKUs", "less write-off, some stock-out risk"),
    ]
    return _ranked(f"Single-period order across {report.n_skus} SKU(s): choose the stocking posture.", items)


def cycle_count_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Adopt the cycle-count program",
         f"Run the balanced A/B/C schedule: {report.total_counts} counts/year, "
         f"peak {report.peak_daily_load}/day.",
         "stand up the recommended cycle-count schedule", "steady accuracy without an annual shutdown"),
        ("Front-load A-item accuracy",
         f"Count the {report.by_class.get('A', 0)} A-SKU(s) first / more often until IRA holds.",
         "raise the count frequency on the A class", "protects the highest-value stock first"),
        ("Lighten the daily load",
         f"Spread counts over more working days (peak now {report.peak_daily_load}/day) or trim C cadence.",
         "rebalance the schedule to cut the daily peak", "easier to staff, slower full coverage"),
    ]
    return _ranked(f"Cycle-count program for {report.n_items} SKU(s): choose how to run it.", items)


def multi_echelon_options(report: object) -> GuidedOutcome:
    placement = ", ".join(report.stocking_stage_names) if report.stocking_stage_names else "none"
    items: list[_Item] = [
        ("Adopt the cost-optimal placement",
         f"Hold safety stock where the model places it ({placement}) for "
         f"{report.total_holding_cost:,.0f} holding cost.",
         "set each stage to its recommended base-stock level", "minimum network holding cost"),
        ("Centralize safety stock upstream",
         "Pool stock at a central / upstream echelon (risk pooling) rather than at every stage.",
         "consolidate safety stock at the upstream echelon", "less stock, more downstream lead-time risk"),
        ("Push stock to the customer-facing stage",
         "Hold more at the demand node to protect responsiveness and availability.",
         "raise base stock at the customer-facing stage", "better service, higher holding cost"),
    ]
    return _ranked(
        f"Multi-echelon placement over {report.n_stages} stage(s): choose the stocking strategy.",
        items,
    )
