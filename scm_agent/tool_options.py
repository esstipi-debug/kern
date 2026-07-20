"""Ranked, executable options on success - the Guided Execution Layer applied to the happy path.

Every tool, on a successful run, should hand the user >=2 ranked, executable choices with one
recommended default, not just a dashboard. These builders map each tool's report to that
``GuidedOutcome`` (OPTIONS); they are wired via ``Tool.options`` in tools.py. Tools whose report
already carries a ranked outcome (sourcing, sop, returns) reuse ``report.outcome`` directly.

Each builder reads only its report's public fields and returns a protected options outcome.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from src.escalation import OPERATIONAL, escalate, maybe_escalate_data_quality
from src.guided import ExecutionOption, GuidedOutcome, Residual, as_executed, as_handoff, as_options

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


def _intake_quality_detail(quality: object) -> str:
    """'X bad date, Y missing quantity, Z negative quantity' - only the reasons
    that actually dropped a row, in the order intake.py's IntakeQuality tracks them."""
    parts = []
    if quality.n_dropped_bad_date:
        parts.append(f"{quality.n_dropped_bad_date} bad date")
    if quality.n_dropped_bad_quantity:
        parts.append(f"{quality.n_dropped_bad_quantity} missing quantity")
    if quality.n_dropped_negative_quantity:
        parts.append(f"{quality.n_dropped_negative_quantity} negative quantity")
    return ", ".join(parts)


def _apply_intake_quality(outcome: GuidedOutcome, report: object) -> GuidedOutcome:
    """Shared by every intake-fed tool's options builder: state a residual
    whenever ANY source rows were dropped during intake (transparency first,
    regardless of severity), and escalate (src.escalation.maybe_escalate_data_quality)
    when the dropped share exceeds the report's own intake_quality_threshold - the
    never-unprotected contract applied to the intake step, the same failure class
    jobs/forecast_job.py's MASE=inf handling addresses for unvalidated SKUs.

    A no-op when the report carries no ``intake_quality`` (untracked intake path,
    e.g. examples/run_inventory_job.py) or intake dropped nothing.
    """
    quality = getattr(report, "intake_quality", None)
    if quality is None or quality.n_dropped == 0:
        return outcome
    detail = _intake_quality_detail(quality)
    residual = Residual(
        description=f"{quality.n_dropped} of {quality.n_raw} source row(s) were dropped during intake ({detail}).",
        risk_if_skipped="the analysis is built on a smaller, possibly unrepresentative slice of the real "
                        "demand history - verify the source file's date/quantity columns.",
    )
    outcome = replace(outcome, residuals=[*outcome.residuals, residual])
    threshold = getattr(report, "intake_quality_threshold", 0.20)
    return maybe_escalate_data_quality(outcome, quality.dropped_fraction, threshold, detail=detail)


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
    outcome = _ranked(f"Inventory policy for {len(report.recommendations)} SKU(s): choose how to act.", items)
    return _apply_intake_quality(outcome, report)


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


def transportation_options(report: object) -> GuidedOutcome:
    worst = report.worst_lane.lane if report.worst_lane is not None else "the densest lane"
    items: list[_Item] = [
        ("Adopt the recommended mode per shipment",
         f"Route each shipment to its cheapest feasible mode - saves {report.total_savings:,.0f} "
         f"vs all-LTL across {report.n_shipments} shipment(s).",
         "book each shipment on its recommended mode", "lowest freight at the current service"),
        (f"Consolidate small shipments to FTL (e.g. {worst})",
         f"Pool LTL volume on dense lanes past the ~{report.breakeven_kg:,.0f} kg FTL breakeven.",
         "consolidate LTL shipments into full truckloads", "cheaper per kg, needs volume + scheduling"),
        ("Set service-mode rules",
         "Cap transit time so only time-sensitive lanes pay for the faster (pricier) modes.",
         "apply transit-time rules per lane", "protects service where it matters, trims it elsewhere"),
    ]
    return _ranked(f"Transport plan over {report.n_shipments} shipment(s): choose the freight strategy.", items)


def fefo_options(report: object) -> GuidedOutcome:
    disp = report.disposition
    fefo = ("Issue stock FEFO",
            "Ship the soonest-to-expire lots first so nothing ages out behind fresher stock.",
            "pick lots First-Expired-First-Out", "no cost; prevents avoidable waste")
    markdown = ("Mark down the at-risk lots",
                f"Clear the {report.at_risk_units:,.0f} at-risk unit(s) at a discount - "
                f"recovers {disp.markdown_recovery:,.0f} vs {disp.scrap_recovery:,.0f} scrap.",
                "stage a markdown on the at-risk lots", "recovers cash, dilutes margin")
    scrap = ("Scrap / write off the unsellable",
             f"Write off the at-risk units where markdown can't beat the {disp.scrap_recovery:,.0f} scrap value.",
             "scrap the unsellable at-risk lots", "stops further holding cost; lost value")
    if report.at_risk_units > 0 and disp.recommended == "markdown":
        items: list[_Item] = [markdown, fefo, scrap]
    elif report.at_risk_units > 0:
        items = [scrap, fefo, markdown]
    else:
        items = [fefo, markdown, scrap]
    return _ranked(
        f"Lot expiry over {report.n_lots} lot(s): {report.at_risk_units:,.0f} unit(s) at risk - choose the move.",
        items,
    )


def slotting_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Adopt the COI slot map",
         f"Place the {report.n_a} fast/dense SKU(s) in zone A (closest), {report.n_b} in B, "
         f"{report.n_c} in C, by cube-per-order index.",
         "re-slot SKUs to their recommended zones", "cuts the most pick travel for the effort"),
        (f"Co-locate the affinity clusters ({len(report.co_location_groups)})",
         "Keep SKUs frequently ordered together adjacent to shorten multi-line picks.",
         "co-locate the affinity groups", "fewer steps per order; needs adjacent space"),
        ("Re-slot the A zone only",
         "Move just the highest-impact zone-A SKUs first to limit disruption.",
         "re-slot the zone-A SKUs first", "fast partial win, less churn"),
    ]
    return _ranked(f"Slotting over {report.n_skus} SKU(s): choose how to re-slot.", items)


def simulation_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Adopt the simulation-optimized policy",
         f"Set each SKU's (R,S) to the simulated optimum - saves {report.total_saving:,.0f} "
         f"vs the analytical policy across {report.n_skus} SKU(s).",
         "apply the simulation-optimized order-up-to levels", "lowest simulated total cost"),
        ("Validate with a longer run first",
         "Re-simulate the high-value SKUs with more periods / new seeds before committing.",
         "re-run the simulation with more periods", "more confidence, more compute"),
        ("Apply only the material gains",
         "Adopt the new policy where the cost saving is significant; keep the analytical one elsewhere.",
         "adopt only where the saving is material", "less churn, captures most of the gain"),
    ]
    return _ranked(
        f"Simulation-optimized policy over {report.n_skus} SKU(s): choose how to roll it out.",
        items,
    )


def digital_twin_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Feed the generated datasets to the analysis suite",
         f"Run forecasting / safety stock / policy tools on the twin's demand history "
         f"({report.n_products} product(s), {report.periods} periods) to benchmark them "
         f"against known ground truth.",
         "run the analysis tools on the generated CSVs",
         "turns the scenario into validated recommendations"),
        ("Harden the weakest node first",
         f"{report.weakest_store} fills only {report.weakest_store_fill * 100:.1f}% - raise its "
         f"order-up-to level or shorten its lead time and re-simulate.",
         "re-run the twin with a stronger policy at the weakest store",
         "targets the service floor directly"),
        ("Stress the network harder",
         "Re-run with a longer outage / bigger surge to find the breaking point before reality does.",
         "re-run the twin with a harsher disruption",
         "maps resilience limits; costs another run"),
    ]
    return _ranked(
        f"Digital twin scenario complete (network fill {report.network_fill_rate * 100:.1f}%): "
        f"choose what to do with it.",
        items,
    )


def excess_obsolete_options(report: object) -> GuidedOutcome:
    clear_dead = ("Clear the dead stock",
                  f"Liquidate / return / write off the {report.n_dead} dead SKU(s) holding "
                  f"{report.dead_value:,.0f}.",
                  "dispose of the dead stock to release cash and space", "recovers cash now; some loss on value")
    draw_down = ("Draw down the excess",
                 f"Stop buying and redistribute / promote the {report.n_excess} excess SKU(s) "
                 f"({report.excess_value:,.0f}).",
                 "stop replenishing and draw down the excess", "frees cash before it ages to dead")
    caps = ("Set days-of-cover caps",
            "Govern with a max days-of-cover per ABC class so excess does not rebuild.",
            "apply per-class cover ceilings", "structural prevention, slower payoff")
    if report.dead_value > 0:
        items: list[_Item] = [clear_dead, draw_down, caps]
    elif report.excess_value > 0:
        items = [draw_down, caps, clear_dead]
    else:
        items = [caps, draw_down, clear_dead]
    return _ranked(
        f"E&O over {report.n_skus} SKU(s): {report.eo_value:,.0f} at risk - choose how to release it.",
        items,
    )


def launch_readiness_options(report: object) -> GuidedOutcome:
    """Aggregate the per-SKU launch verdicts into one run-level outcome.

    Any red SKU -> ESCALATED (routed to the campaign owner), bundling every red into one
    packet and carrying those options at the top level too (mirrors src.escalation._maybe_
    escalate's "nothing silently vanishes"). Else any yellow -> the worst-margin yellow SKU's
    own OPTIONS outcome. Else all green -> EXECUTED.
    """
    reds = [line for line in report.lines if line.verdict == "red"]
    if reds:
        reason = f"{len(reds)} SKU(s) at launch risk: " + "; ".join(
            f"{line.product_id} - {line.reason}" for line in reds)
        options = [
            ExecutionOption(
                label="Route the red SKUs to the campaign owner", score=2.0, recommended=True,
                summary=f"{len(reds)} SKU(s) cannot be ready for their launch date as planned.",
                action="send the red-SKU handoff to the marketing campaign owner",
                tradeoffs="protects day-one availability; needs a calendar/allocation decision"),
            ExecutionOption(
                label="Proceed only with the launch-ready SKUs", score=1.0,
                summary=f"launch the {report.n_green} green SKU(s) on schedule; hold the rest.",
                action="launch green SKUs only; defer yellow/red",
                tradeoffs="keeps the date for what is ready; narrows the launch"),
        ]
        outcome = escalate(report.summary, OPERATIONAL, reason, route_to="marketing campaign owner",
                           sla="before the campaign go/no-go", options=options, confidence=0.7)
        return replace(outcome, options=list(outcome.escalation.options))
    yellows = [line for line in report.lines if line.verdict == "yellow"]
    if yellows:
        return min(yellows, key=lambda line: (line.days_of_cover or 0.0) - line.days_until_launch).outcome
    return as_executed(f"All {len(report.lines)} SKU(s) are launch-ready.", confidence=0.9)


def markdown_liquidation_options(report: object) -> GuidedOutcome:
    priced = report.n_elasticity + report.n_default_discount
    execute = ("Execute the clearance plan",
               f"Clear the {priced} priced SKU(s) at the recommended markdowns over "
               f"{report.horizon_weeks:.0f} weeks, recovering ~{report.total_recovered:,.0f}.",
               "apply the per-SKU clearance prices and horizon", "recovers cash now; margin hit on markdowns")
    salvage = ("Salvage the non-moving lines",
               f"Route the {report.n_salvage} salvage SKU(s) (dead / no price) to return-to-vendor, "
               f"jobbers, or a write-down.",
               "dispose of the salvage lines off-price", "recovers a fraction of cost; clears space fast")
    reprice = ("Refine prices before committing",
               "Gather more price/quantity history so more lines can be elasticity-priced, not defaulted.",
               "hold and collect price-response data", "better prices later; cash stays locked meanwhile")
    if priced > 0:
        items: list[_Item] = [execute, salvage, reprice]
    elif report.n_salvage > 0:
        items = [salvage, reprice, execute]
    else:
        items = [reprice, execute, salvage]
    return _ranked(
        f"Liquidation plan for {report.n_assessed} at-risk SKU(s): recover ~{report.total_recovered:,.0f} "
        f"of {report.total_at_risk:,.0f} - choose how to act.",
        items,
    )


def facility_location_options(report: object) -> GuidedOutcome:
    save_txt = (f" (saves {report.saving_vs_current:,.0f} vs current)"
                if report.saving_vs_current is not None else "")
    optimum = (f"Site at the optimum near '{report.nearest_point}'",
               f"Locate at ({report.optimum.x:,.2f}, {report.optimum.y:,.2f}) - minimum total "
               f"weighted travel{save_txt}.",
               "site the facility at the Weiszfeld optimum", "lowest load x distance")
    cog = ("Use the center of gravity",
           f"Locate at the load centroid ({report.cog.x:,.2f}, {report.cog.y:,.2f}) - simpler to justify.",
           "site at the center of gravity", "near-optimal, easy to explain")
    keep = ("Keep the current site",
            "Stay put if the relocation saving doesn't beat the move and lease cost.",
            "keep the current site", "no move cost; forgoes the travel saving")
    items: list[_Item] = [optimum, cog, keep] if report.current is not None else [optimum, cog]
    return _ranked(
        f"Facility location over {report.n_points} demand point(s): choose the site.",
        items,
    )


def network_design_options(report: object) -> GuidedOutcome:
    sites = ", ".join(report.open_sites)
    open_all = (
        f"Open the {report.p} optimal site(s)",
        f"Open [{sites}] - {report.total_weighted_distance:,.0f} total weighted travel, "
        f"{report.saving_vs_baseline:,.0f} less than the best single facility "
        f"({report.saving_pct * 100:.0f}%).",
        f"open sites {sites} and assign each demand to its nearest open site",
        "minimum load x distance across the network",
    )
    phase = (
        "Phase the rollout",
        f"Stand up the {report.p} site(s) in waves, busiest cluster first, to spread the capex.",
        "open the busiest site first, then the rest on a schedule",
        "slower to the full saving; smooths the investment",
    )
    single = (
        "Keep a single facility",
        f"Stay at one DC ({report.baseline_distance:,.0f} weighted travel) if the "
        f"{report.saving_pct * 100:.0f}% saving doesn't beat the cost of running more sites.",
        "keep one facility",
        f"forgoes {report.saving_vs_baseline:,.0f} weighted travel; avoids multi-site overhead",
    )
    validate = (
        "Validate the chosen site",
        "Field-check the selected site against real road distance, land and labour before opening.",
        "confirm the geometric optimum on the ground before committing",
        "de-risks the straight-line model",
    )
    items: list[_Item] = (
        [open_all, phase, single] if report.p > 1 else [open_all, validate]
    )
    return _ranked(
        f"Network design over {report.n_demand} demand point(s): choose how many DCs and which.",
        items,
    )


def drp_options(report: object) -> GuidedOutcome:
    items: list[_Item] = [
        ("Release the planned orders",
         f"Launch the time-phased branch releases ({report.total_branch_releases:,.0f} unit(s)) and "
         f"the DC releases ({report.dc_release_total:,.0f}) on their offset periods.",
         "release the planned branch + DC orders on schedule", "keeps every branch ahead of demand"),
        (f"Pre-position the DC for the peak (period {report.peak_period})",
         f"Stage DC inbound for the {report.peak_qty:,.0f}-unit peak so branch releases are covered.",
         "size DC inbound + capacity to the peak period", "protects service at the bottleneck period"),
        ("Smooth the lumpy releases",
         "Add safety stock or lot sizing where releases spike to level the load.",
         "apply lot sizing / safety stock to smooth releases", "steadier flow, a little more stock"),
    ]
    return _ranked(
        f"DRP over {report.n_branches} branch(es) x {report.n_periods} period(s): choose how to execute.",
        items,
    )


def vehicle_routing_options(report: object) -> GuidedOutcome:
    other = report.sweep_plan if report.recommended_method == "savings" else report.savings_plan
    adopt = (f"Adopt the {report.recommended_method} plan",
             f"{len(report.recommended_routes)} route(s), {report.recommended_distance:,.1f} total "
             f"distance - {report.savings_vs_naive:,.1f} less than one truck per stop.",
             f"dispatch the {report.recommended_method} route plan", "lowest distance found")
    alt = (f"Use the {other.method} plan instead",
           f"{other.n_vehicles} route(s), {other.total_distance:,.1f} total distance - "
           "simpler angular sequencing, easier to explain to drivers.",
           f"dispatch the {other.method} route plan", "may be easier to communicate; usually a bit longer")
    fix_windows = ("Resolve the late stops first",
                   f"{len(report.late_stops)} stop(s) miss their time window on the recommended plan.",
                   "re-sequence, add a vehicle, or renegotiate the window for the late stops",
                   "protects service commitments before dispatch")
    items: list[_Item] = [adopt, alt, fix_windows] if report.late_stops else [adopt, alt]
    return _ranked(
        f"Vehicle routing over {report.n_stops} stop(s): choose the route plan.",
        items,
    )


def price_intelligence_options(report: object) -> GuidedOutcome:
    n_flagged = len(report.quarantined) + len(report.discarded)
    act_now = (
        "Act on the confirmed rows",
        f"{report.n_products_covered} of {report.n_products} product(s) have a confirmed competitor "
        f"read ({report.coverage_pct * 100:.0f}% coverage) - review the price-position matrix and "
        "decide where to move.",
        "review price_position_matrix.xlsx and act on the non-quarantined rows",
        "the fastest path to a decision; skipped/quarantined refs stay unresolved",
    )
    expand_coverage = (
        "Expand coverage first",
        f"{len(report.skipped)} ref(s) produced no observation this run (site not approved, blocked, "
        "or extraction failed) - resolving those raises coverage before acting.",
        "get the skipped domains approved (config/sites/*.yaml) or fix the refs, then re-run",
        "better-grounded decision; delays action by one more cycle",
    )
    investigate_flags = (
        "Investigate the flagged rows",
        f"{n_flagged} observation(s) were quarantined or discarded by the sanity gate - a confirmatory "
        "re-read (or a manual check) resolves whether the jump is real.",
        "re-run once the confirmation window passes, or check the flagged URLs by hand",
        "protects against acting on a bad read; costs a second cycle",
    )
    if n_flagged > 0:
        items: list[_Item] = [act_now, investigate_flags, expand_coverage]
    elif report.skipped:
        items = [act_now, expand_coverage, investigate_flags]
    else:
        items = [act_now, expand_coverage, investigate_flags]
    return _ranked(
        f"Price position across {report.n_products} product(s): {report.coverage_pct * 100:.0f}% "
        "coverage - choose how to proceed.",
        items,
    )


def price_watch_options(report: object) -> GuidedOutcome:
    """The discovery-assisted watch cycle's options hook (Task 11 / PR-11).

    Happy path: ranked next steps over this cycle's outcomes (accepted /
    quarantined+discarded / skipped), same ``_ranked`` shape every other
    tool's options hook uses.

    R5 (SAFETY-CRITICAL): when the cycle's scaling step (Task 9) produced one
    or more ``pending_escalations`` -- a SKU wanting a higher acquisition tier
    than its site's approved ceiling -- this hook NEVER reports the happy-path
    ranked options alone and NEVER reports EXECUTED. It surfaces a HANDOFF
    outcome instead, carrying every pending escalation's own prepared
    ``HandoffPacket``(s)/residual(s) verbatim (``watch_policy._ceiling_raise_
    outcome`` already built them -- never rebuilt here), so the ceiling-raise
    request is genuinely visible to the operator, not flattened into the
    ranked-options list or silently dropped. The happy-path's own ranked
    options travel along at the outcome's top level too (mirrors
    ``src.escalation._maybe_escalate``'s "nothing silently vanishes from the
    rendered deliverable" contract) so the routine next steps stay visible
    alongside the pending approval.
    """
    outcomes = list(report.outcomes)
    by_status = Counter(o.status for o in outcomes)
    n_accepted = by_status.get("accepted", 0)
    n_flagged = by_status.get("quarantined", 0) + by_status.get("discarded", 0)
    n_skipped = by_status.get("skipped", 0)

    review_accepted = (
        "Review the accepted price reads",
        f"{n_accepted} of {len(outcomes)} confirmed pair(s) produced a fresh accepted observation "
        "this cycle - review the price-position matrix and decide where to move.",
        "review the accepted observations and act on the non-flagged pairs",
        "the fastest path to a decision; flagged/skipped pairs stay unresolved",
    )
    investigate_flags = (
        "Investigate the flagged reads",
        f"{n_flagged} observation(s) were quarantined or discarded by the sanity gate this cycle - "
        "a confirmatory re-read (or a manual check) resolves whether the jump is real.",
        "re-run once the confirmation window passes, or check the flagged pairs by hand",
        "protects against acting on a bad read; costs a second cycle",
    )
    resolve_skipped = (
        "Resolve the skipped pairs",
        f"{n_skipped} confirmed pair(s) were skipped this cycle (blocked, circuit open, tier not "
        "approved, or a fetch/extraction failure) - resolving those raises next-cycle coverage.",
        "check the skip reasons and fix the underlying site/tier issue, or wait for the breaker to reopen",
        "better-grounded next cycle; delays action by one more cycle",
    )
    if n_flagged > 0:
        items: list[_Item] = [review_accepted, investigate_flags, resolve_skipped]
    elif n_skipped > 0:
        items = [review_accepted, resolve_skipped, investigate_flags]
    else:
        items = [review_accepted, investigate_flags, resolve_skipped]
    happy_path = _ranked(
        f"Price watch cycle over {report.pairs_checked} confirmed pair(s): {n_accepted} accepted, "
        f"{n_flagged} flagged, {n_skipped} skipped - choose how to proceed.",
        items,
    )

    pending = list(report.pending_escalations)
    if not pending:
        return happy_path

    handoffs = [packet for guided in pending for packet in guided.handoffs]
    residuals = [residual for guided in pending for residual in guided.residuals]
    summary = (
        f"{happy_path.summary} {len(pending)} SKU(s) also want a higher acquisition tier than their "
        "site's approved ceiling - a human must review and raise the ceiling before that tier is ever "
        "used; nothing was applied automatically."
    )
    outcome = as_handoff(
        summary, handoffs, confidence=min([happy_path.confidence, *(g.confidence for g in pending)]),
        residuals=residuals,
    )
    return replace(outcome, options=list(happy_path.options))
