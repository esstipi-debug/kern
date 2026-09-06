---
pair: qa-gate
lang: en
slug: inventory-ai-qa-gate
title: "Inventory AI quality gate: how a run ends with no file"
description: "Two runners call the same qa() check, and neither writes a deliverable when it comes back with an issue. What arrives instead of a number."
date: 2026-08-25
updated: 2026-08-25
query: "inventory AI quality gate"
answer: >
  A quality gate is the verification step between the calculation and the file.
  In Kern it is not owned by the capability doing the arithmetic: two runners
  call qa() themselves, and both return an empty deliverables map the moment it
  reports a single issue. A chatbot always produces prose. This path can produce
  a list of defects and no number at all.
cta: diagnostico
offer: diagnostico-arranque
sources:
  - kind: code
    ref: scm_agent/orchestrator.py
  - kind: code
    ref: scm_agent/packages.py
  - kind: code
    ref: scm_agent/guided_bridge.py
  - kind: code
    ref: jobs/qa.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: src/financial_kpis.py
  - kind: code
    ref: webapp/demo_scan.py
faq:
  - q: "Can a wrong number still get through the gate?"
    a: "Yes. It checks internal consistency, ranges and evidence: that investment adds up, that a buffer has an estimable dispersion behind it, that history clears the six-period minimum. It does not know whether your unit cost is right or whether you exported the right channel. Consistent garbage passes; QA is not omniscience."
  - q: "The scan came back with no report. What do I do with that?"
    a: "You get a qa_failed status, an empty deliverables map, and an escalation packet: the reason, the route (data owner or analyst), the recommendation to fix the flagged inputs and re-run, and every issue found, each naming the SKU and the invariant it broke. It reads as a work order, not an error message."
  - q: "Can I override the gate and get the file anyway?"
    a: "No, and there is no flag for it. The decision to write lives in the runner, not the capability, so nothing a tool or caller sets changes it: fix the input, re-run the job. What a human controls is the other side — of the four possible endings, three stop on a human by design, and anything irreversible on the writeback path needs an explicit, expiring approval."
  - q: "Does my sales file go to a model provider?"
    a: "The demo scan does not touch one: webapp/demo_scan.py imports the three jobs and pandas, no provider. In the full engine what can reach a provider is the already-computed summary sentence and the free-text brief, never the CSV rows, and only with an API key configured. A data-processing annex draft (documentation/legal/dpa-lite.md) sits in the repository pending legal review; the binding version is agreed in writing, not improvised here."
  - q: "Is the autonomous control tower shipping today?"
    a: "No. What runs in production is the engine plus a human operator: a runner that routes, verifies, and withholds output that does not pass. A tower deciding on its own is a plan, not a product."
noindex: false
---

# Inventory AI quality gate: how a run ends with no file

A quality gate is the verification step between the calculation and the file. In Kern it is not owned by the capability doing the arithmetic: two runners call qa() themselves, and both return an empty deliverables map the moment it reports a single issue. A chatbot always produces prose. This path can produce a list of defects and no number at all.

## Where the veto lives

Outside the tests, `tool.qa(` appears in exactly two places, both runners and neither a capability: `scm_agent/orchestrator.py` for a single job, `scm_agent/packages.py` for a multi-step package. A capability supplies `prepare`, `run`, `qa` and `deliver`, but never chooses when `deliver` is called — its caller does, after the gate. The check is not a step a tool can decline, because the tool is not the one running it.

In the single-job path the ordering is literal (`scm_agent/orchestrator.py`, lines 204-211):

```
produced = tool.run(prepared.payload, params)
issues = tool.qa(produced.report)
if issues:
    return JobResult(
        status=STATUS_QA_FAILED, tool=tool.key, confidence=intent.confidence,
        deliverables={}, summary=f"{tool.title}: QA failed; no deliverables written.",
        qa_issues=issues,
    )
```

`tool.deliver(...)` sits below that return, so one issue ends the function before anything touches disk. Of the five statuses — `ok`, `needs_clarification`, `needs_data`, `qa_failed`, `error` — only `ok` reaches the writing code. There is no "delivered with warnings" state to land in.

The package runner is stricter: it computes every step against a temporary directory, and a second phase writes to the output root only once no executed step has come back `qa_failed`.

## What the checks look for

`jobs/qa.py` returns a list of strings, and an empty list means passed. On an inventory-policy report it asks three kinds of question.

**Does the arithmetic close?** Per SKU, `investment` must equal `cycle_investment + ss_investment` within tolerance. The SKU investments must sum to the requested investment when no budget is set, and the final investment may never exceed it.

**Is anything out of range or self-contradictory?** Negative safety stock or reorder point, a `safety_stock_scale` outside [0, 1], an `(s, Q)` line with no positive order quantity, an `(R, S)` line with no positive order-up-to level or a reorder point above it, a report flagged feasible whose investment exceeds the budget, or one flagged infeasible while the cycle-stock floor fits inside it.

**Does the number rest on anything?** A priced policy line built on fewer than six periods of demand history fails — six is `MIN_PERIODS_FOR_POLICY` in `src/forecasting.py`, which its own comment calls a refusal threshold, not a tuning knob. So does any positive safety stock whose `sigma_source` reads `unavailable`: that field names which dispersion reached the formula (`forecast_error_oos`, `forecast_error`, `demand_std_fallback`, `unavailable`), and the last means none was estimable. A buffer is z times sigma times the square root of the risk period; with no sigma, it is manufactured.

## Absence is not zero

The figure an operator is likeliest to trust is the one saying nothing went wrong. Kern's own logged defects (`documentation/KERN_AUTOPSIA_FALLAS.md`) repeat one mistake: an absence encoded as the most favourable value.

`stockout_rate` returned `0.0` when it had no periods to divide by, printing "we never stocked out" out of an absence of observation; five `float("inf")` defaults on the same row did it in the other direction. A missing on-time-in-full column became `False`, then `0.0`, so a deliverable printed "OTIF 0% against a 95% target" — zero observations is not zero percent, it is not measured. Both rows are closed: `src/financial_kpis.py` now declares `stockout_rate(...) -> float | None`, and the OTIF path got explicit true-value tokens plus a readable error when the column is missing.

## What arrives instead of the file

`scm_agent/guided_bridge.py` maps every status onto an outcome that still carries something executable. `qa_failed` becomes `ESCALATED` with a packet whose fields are fixed in code: the reason (gate failed, output withheld), the route (`data owner / analyst`), the recommendation to fix the flagged inputs and re-run, and every QA issue as its citations. `error` escalates to support instead; `needs_data` becomes a handoff naming the missing file; `needs_clarification`, ranked options.

Of the four possible endings, three stop on a human by design. That sentence is the entire claim: no frequency behind it has been measured.

The write side follows the same rule. A writeback is staged as a field-level dry-run changeset, tiered `read` / `reversible` / `irreversible`, approved for 900 seconds before that approval expires, and recorded so it can be rolled back; irreversible always needs a human. Nosilo is the control plane under those endings and that writeback.

## Compared with what

**Your planning spreadsheet.** It computes whatever it is asked to, including what should not be computed: a cell built on years of history and one built on a couple of weeks look identical, and the minimum lives in the head of whoever built the file.

**A Stocky- or Netstock-class planning SaaS.** The correct method is usually in there as a setting — sizing a buffer from forecast error rather than raw demand dispersion is an option nobody switches on, and nothing refuses to ship while it is off. The gap is not the algorithm; it is that the right step is skippable.

**Min/max reorder points in the ERP or the 3PL.** A static threshold never fails because it always fires, which is why it never refuses. A badly set minimum reorders with the confidence of a well-set one.

**A chatbot over the sales export.** It writes a clean paragraph, every time. It has no state in which it declines, and no minimum-history constant to compare your SKU against. The question is not who writes better prose; it is which can hand back no number.

## What Kern does not do

The gate is not omniscience: it verifies internal consistency, ranges, finiteness and evidence, not whether your landed unit cost was loaded correctly or you exported the right sales channel. What it guarantees is that an internally incoherent result never reaches you wearing the face of a report.

There is no autonomous purchasing: Kern prepares the work, a person signs anything irreversible. And there is no "trust us, it is AI" — a sentence that cannot be pointed at a file in this repository does not get written.

## FAQ

### Can a wrong number still get through the gate?

Yes. It checks internal consistency, ranges and evidence: that investment adds up, that a buffer has an estimable dispersion behind it, that history clears the six-period minimum. It does not know whether your unit cost is right or whether you exported the right channel. Consistent garbage passes; QA is not omniscience.

### The scan came back with no report. What do I do with that?

You get a `qa_failed` status, an empty deliverables map, and an escalation packet: the reason, the route (data owner or analyst), the recommendation to fix the flagged inputs and re-run, and every issue found, each naming the SKU and the invariant it broke. It reads as a work order, not an error message.

### Can I override the gate and get the file anyway?

No, and there is no flag for it. The decision to write lives in the runner, not the capability, so nothing a tool or caller sets changes it: fix the input, re-run the job. What a human controls is the other side — of the four possible endings, three stop on a human by design, and anything irreversible on the writeback path needs an explicit, expiring approval.

### Does my sales file go to a model provider?

The demo scan does not touch one: `webapp/demo_scan.py` imports the three jobs and pandas, no provider. In the full engine what can reach a provider is the already-computed summary sentence and the free-text brief, never the CSV rows, and only with an API key configured. A data-processing annex draft (`documentation/legal/dpa-lite.md`) sits in the repository pending legal review; the binding version is agreed in writing, not improvised here.

### Is the autonomous control tower shipping today?

No. What runs in production is the engine plus a human operator: a runner that routes, verifies, and withholds output that does not pass. A tower deciding on its own is a plan, not a product.

## Next step

Bring the stock CSV you already export. The [Start-up Diagnostic](/paquetes/diagnostico-arranque) starts from what your data can support, before any quantity is proposed — and the free scan behind it reuses these same jobs' `verify()` calls: when one flags something, no figure is issued.
