# Audit-Grade Evidence — Research Memo (Phase 1 + Phase 2)

> Task: scope an audit vertical for Linchpin — turning ABC-XYZ / inventory-policy /
> safety-stock outputs into audit-defensible workpapers. This memo covers the
> standards research (Phase 1) and the gap analysis against the current engine
> (Phase 2). The companion design proposal is
> [`AUDIT_EVIDENCE_DESIGN.md`](AUDIT_EVIDENCE_DESIGN.md). **Research only — no
> implementation in this PR.**
>
> Status: draft for review. Several factual anchors (AICPA table values, firm
> workpaper conventions) are flagged inline as **VERIFY** — they need a practicing
> auditor or a copy of the AICPA Audit Sampling guide before any code is written.

---

## 1. Audit sampling — AU-C 530 (AICPA) and AS 2315 (PCAOB)

### 1.1 What is actually mandated vs. customary practice

The single most important research finding: **neither standard mandates
statistical sampling.** Both AU-C 530 and AS 2315 explicitly allow statistical
*or* nonstatistical approaches, and state that either, properly applied, can
provide sufficient audit evidence. What they *do* mandate, regardless of
approach:

1. **Representative design.** The sample must be designed so that the sampling
   units are representative of the population, and every sampling unit must have
   a chance of selection (AU-C 530.08–.09).
2. **Sample size sufficient to reduce sampling risk to an acceptably low
   level** (AU-C 530.08). The size drivers are the same either way: tolerable
   misstatement (or tolerable rate of deviation for controls), expected
   misstatement/deviation, and the acceptable risk of incorrect acceptance
   (RIA) / risk of overreliance.
3. **Investigation of every deviation/misstatement found** — nature and cause,
   and whether it indicates fraud or a systematic problem (AU-C 530.12–.13).
4. **Projection to the population.** Misstatements found in the sample must be
   projected to the population (AU-C 530.14; AS 2315.26: divide sample
   misstatement by the sampled fraction, then add misstatements from any items
   examined 100%).
5. **Evaluation against tolerable misstatement with explicit consideration of
   sampling risk** (AS 2315.26–.27): if projected misstatement is *close to*
   tolerable misstatement, the auditor may not accept the population even
   though the point estimate is below the threshold.

A sampling method is "statistical" only if it has both (a) random selection and
(b) a statistical evaluation that *measures* sampling risk (AU-C 530.05). This
is exactly what a software engine is good at, and where Linchpin's value is:
mid-tier firms often use judgmental ("haphazard") sampling precisely because
the statistical machinery is tedious — a tool that makes the statistical route
as cheap as the judgmental one is strictly more defensible under the same
standards.

**Mandated:** the judgment inputs (TM, EM, RIA), projection, evaluation,
deviation investigation, documentation of all of it.
**Customary (not mandated):** the specific AICPA Audit Sampling guide tables
(attribute tables; MUS confidence-factor tables), the 90–95% confidence
conventions, and firm sample-size matrices. In practice these customary tables
are the de facto benchmark a PCAOB inspector or reviewing partner compares
against, so the engine should implement them *and cite them by table number*.

### 1.2 The two methods the capability needs

**Attribute sampling** (tests of controls — deviation *rates*):
inputs are confidence level (1 − risk of overreliance), tolerable deviation
rate (TDR), expected population deviation rate (EPDR), and population size
(small-population correction). Output: sample size `n` and the number of
acceptable deviations. Evaluation: achieved upper deviation limit vs. TDR.
Math is binomial/hypergeometric — the same family as
`src/acceptance_sampling.py` already implements for quality inspection.

**Monetary Unit Sampling (MUS / PPS)** (substantive tests of details —
monetary misstatement, the natural method for inventory/COGS price testing):

- Sample size: `n = Population_Value x Confidence_Factor / Tolerable_Misstatement`,
  where the confidence factor comes from the AICPA guide's MUS tables and
  rises with the ratio of expected to tolerable misstatement.
  Worked example circulating in training material (used as a candidate test
  anchor in the design doc): PV = 94,613,131; TM = 4,730,000; EM = 950,000;
  85% confidence; EM/TM = 0.20 gives CF = 2.73 and n = 55. **VERIFY against
  the AICPA guide tables (C-1/C-2) before hard-coding.**
- Selection: fixed-interval (sampling interval = PV / n) with a random start;
  every *dollar* is the sampling unit, so large items are proportionally more
  likely; any logical unit >= the interval is automatically selected (top
  stratum, examined 100%, no projection).
- Known limitations that must be documented in the plan itself: zero and
  negative balances have no chance of selection (AU-C 530 application
  material) and need a separate population or different design; MUS is weak
  at detecting understatement.
- Evaluation: basic precision (zero-error confidence factor x interval),
  projected misstatement (tainting % x interval for items below the interval;
  actual misstatement for top-stratum items), incremental allowance for
  sampling risk from the ranked taintings, summing to an **upper misstatement
  limit (UML)** that is compared to TM. UML > TM means the population is not
  accepted — in Linchpin terms, that is an escalation, not a footnote.

### 1.3 Standards churn to design around (2025–2026)

- **Technology-assisted analysis amendments** (PCAOB, adopted June 2024,
  SEC-approved August 2024, effective for audits of fiscal years beginning on
  or after **Dec 15, 2025** — i.e. current audits): amendments to AS 1105 and
  AS 2301 addressing procedures that analyze *entire populations* of
  electronic information with technology tools. Two consequences for Linchpin:
  (a) when the engine tests 100% of a GL extract, that is **not sampling** and
  AS 2315 does not apply — but the reliability of the electronic information
  and the appropriateness of the tool's design must be evaluated and
  documented; (b) the amendments sharpen the auditor's duty to test the
  **completeness and accuracy of company-produced electronic data** before
  relying on tool output over it. This is the strongest regulatory tailwind
  for the capability: the standards now explicitly contemplate tools like
  this, and explicitly demand the lineage/reliability documentation this
  design adds.
- **AS 2315 amendment effective Dec 15, 2026** — a conforming amendment to
  paragraph .11 (nonsampling risk), part of the broader AS 1000-series
  updates. Not a substantive rewrite of the sampling math; cite-check the
  final text when implementing.
- **AS 1215 amendment effective Dec 15, 2026**: the audit-documentation
  assembly window shrinks from **45 days to 14 days** after report release.
  Anything Linchpin produces must be final-form fast; a workpaper that needs
  manual reassembly after the fact is a liability under the new deadline.

## 2. Audit evidence — AS 1105 (what "defensible" means beyond "correct")

AS 1105 splits the requirement into **sufficiency** (quantity of evidence,
driven by risk) and **appropriateness** (relevance + **reliability**).
Reliability has a well-known hierarchy (AS 1105.08): evidence from independent
external sources > company-internal evidence; evidence the auditor obtains
directly > indirectly; documentary > oral; originals > copies. Two clauses
matter most for this capability:

- **AS 1105.10 — information produced by the company (IPE).** When the auditor
  uses company-produced information (a GL export, a perpetual inventory
  listing) as evidence, the auditor must (1) test its **accuracy and
  completeness** (or test controls over its accuracy and completeness), and
  (2) evaluate whether it is **sufficiently precise and detailed** for the
  purpose. Every Linchpin analytical output today is computed *from* IPE, so a
  workpaper built on it is only as defensible as the documented IPE testing
  around it. Concretely: row counts, control totals tied to the trial balance,
  the extraction query/parameters, the file hash, and who pulled it, all
  recorded on the face of the workpaper.
- **Amended AS 1105 (effective FY beginning on/after Dec 15, 2025)** extends
  this to external information received in electronic form and to
  technology-assisted analysis (see 1.3).

**Correct vs. defensible.** A number is *correct* if the math is right. It is
*defensible* if an independent reviewer can establish, from the documentation
alone, that the math was right, applied to reliable data, using an appropriate
method, by an identified person, at a known time — and could **re-perform** it.
Defensibility is a property of the *documentation*, which is why AS 1215 is as
load-bearing for this capability as AS 2315.

## 3. Audit documentation — AS 1215 (the actual bar for workpapers)

- **The experienced-auditor standard (AS 1215.06):** documentation must let an
  experienced auditor *with no previous connection to the engagement*
  understand the nature, timing, extent, and **results** of procedures,
  the evidence obtained, the conclusions reached, **who performed the work and
  the date**, and **who reviewed it and the date**. This is the acceptance
  test for every artifact the capability emits.
- **Re-performability:** documentation must enable an experienced auditor to
  determine who performed the work and to re-perform/re-derive it. For a
  calculation engine this literally means: inputs + parameters + formula
  version on the record must reproduce the output bit-for-bit.
- **Lockdown and immutability:** complete and final documentation must be
  assembled within 45 days of report release (14 days from Dec 15, 2026);
  nothing may be deleted after the documentation completion date; anything
  added later must be documented *as an addition* (who added it, when, why).
  Retention: **7 years** from report release.
- Implication for design: evidence records must be **append-only, timestamped,
  attributable, and hash-verifiable** — the write-path already solved this
  shape in `src/writeback_store.py` (SQLite ledger with `applied_at`) and
  `src/writeback.py` (SHA-256 content hashes, signed approvals). The read/
  analysis path has nothing equivalent today (see gap table).

## 4. SOX 404 control testing — AS 2201 and mid-tier practice

### 4.1 What the standard requires

- **Walkthroughs** (AS 2201.37–.38): trace a transaction from initiation
  through the process to the financial statements, confirming understanding of
  flow, control points, and whether controls are designed effectively.
- **Test of design (TOD)** (AS 2201.42–.43): would the control, operating as
  designed by a person with the necessary authority and competence, prevent or
  detect material misstatement?
- **Test of operating effectiveness (TOE)** (AS 2201.44–.46): did the control
  operate as designed, consistently, over the period, by the right person?
  Evidence needed rises with the risk associated with the control.
- **Deficiency taxonomy** (AS 2201.62–.70, definitions in .A3/.A7/.A11):
  - *Control deficiency* — design or operation does not allow timely
    prevention/detection of misstatements.
  - *Significant deficiency* — less severe than a material weakness but
    important enough to merit attention of those charged with governance.
  - *Material weakness* — a reasonable possibility that a **material**
    misstatement will not be prevented or detected timely.
  Severity is a function of **likelihood** (probability of misstatement) and
  **magnitude** (potential size), evaluated with compensating controls
  considered — a two-axis classification an engine can *propose* but a human
  must *conclude* on (the qualitative judgment, e.g. the "prudent official"
  test, is not automatable).

### 4.2 Mid-tier documentation format (customary, not mandated)

The working artifact set at mid-tier firms is consistent:

1. **Risk-Control Matrix (RCM)** — one row per control: control ID, process /
   sub-process, related assertion(s) and risk ("what could go wrong"), control
   description, owner, frequency, type (preventive/detective), nature
   (manual/automated/ITDM), key/non-key, TOD conclusion, TOE sample size,
   exceptions found, deficiency classification, remediation reference.
2. **Walkthrough memo** per significant process.
3. **TOE testing sheet** per control: population definition, sample selection
   method, attributes tested, per-item results with tickmarks, exceptions,
   conclusion, preparer/reviewer sign-off.
4. **Deficiency evaluation summary** aggregating deficiencies (individually
   and in combination) to the significant-deficiency / material-weakness line.

Customary **sample sizes by control frequency** (firm matrices differ but
cluster): annual = 1, quarterly = 2, monthly = 2–5, weekly = 5–15,
daily = 20–40, many-times-daily = 25/40/60 by risk tier. These trace back to
the AICPA attribute tables at ~90% confidence. **VERIFY: the pilot should ship
the AICPA-table math plus a configurable frequency matrix, not hard-coded firm
numbers.**

### 4.3 What an inspector/reviewing partner checks first (inventory)

From PCAOB inspection-findings analyses: inventory is a perennial top-two
deficiency area (one 2024-cycle analysis puts inventory-related deficiencies
at 68% of inspected audits with the top specific failures being inadequate
observation/sampling coverage (~42%) and insufficient valuation testing
(~31%)). Recurring first-look items:

- Was the **sample size justified** and the method documented (not just "we
  selected 25 items")? Were high-risk / top-stratum items covered?
- **IPE completeness & accuracy** — was the listing the sample was drawn from
  tied to the GL / trial balance before sampling?
- Were **all exceptions evaluated** (nature, cause, projection), or waved off?
- Does the workpaper stand alone (experienced-auditor test), with preparer /
  reviewer sign-offs and dates?

These four items are exactly the four requirement areas of this task, which is
a good sign the task is scoped on the real pain.

## 5. What "traceability" means here — and how it maps to Linchpin

Regulatory traceability = **re-performability plus provenance**: an
independent reviewer must be able to walk output -> formula (identified,
versioned) -> parameters -> input data (identified, hash-verifiable, with
documented completeness/accuracy testing) -> source system, with a timestamp
and a responsible human at each hop.

Against Linchpin today (see gap table): the closest existing pattern is
`src/writeback.py` + `src/writeback_store.py` — SHA-256 content hashes, HMAC-
signed approvals with approver identity and TTL, a persistent timestamped
SQLite ledger, and exact reversibility. That is the right *shape*, but it is
scoped to **mutations** of a system of record. Audit evidence needs the same
primitives on the **read/analysis path** (a calculation run), where there is no
Changeset because nothing is mutated. Conclusion: **it is a new sibling of the
writeback pattern, reusing its primitives (sha256 hashing, ledger, identity),
not an extension of Changeset/Approval** — plus one thing writeback does not
have: a *positive attestation* record (what was checked and passed, not just
what failed).

---

## 6. Phase 2 — Gap analysis: current engine vs. the four requirements

Grounding: engine modules are pure, frozen, and deliberately metadata-free
(no timestamps, versions, hashes, or source references on any result — the
textbook citation lives only in docstrings). `PolicyResult` nesting
(`policies.py:13` embeds full `SafetyStockResult` + `EOQResult`) is the one
existing structural-lineage precedent. `JobResult` (`scm_agent/types.py:33`)
carries `confidence`, `citations`, `qa_issues` — but no run id, no timestamp,
no input hash, no record of the params actually used (the merged
profile+overrides dict is computed in `orchestrator.py:114` and discarded).
QA (`tool.qa` -> `list[str]`, gate at `orchestrator.py:148`) records only
*failures*, never a positive attestation, and persists nothing.

| Requirement | What exists today | Gap (what must be built) |
|---|---|---|
| **1. Statistically sound sampling plans** | `src/acceptance_sampling.py`: binomial OC-curve single-sampling design (n, c) balancing producer/consumer risk — same math family as attribute sampling. Quality-inspection framing (AQL/LTPD), not audit framing (TDR/RIA/TM). | Audit-vocabulary attribute sampling (confidence, TDR, EPDR -> n, acceptable deviations, upper-limit evaluation) validated against AICPA tables; **MUS end-to-end** (CF tables, interval selection w/ top stratum, tainting/basic-precision/UML evaluation) — nothing like MUS exists. Deterministic, seedable selection (engine is clock-free by convention). |
| **2. GL reconciliation of engine outputs** | `src/reconciliation.py`: system-vs-physical count reconciliation, IRA %, variance $, tolerance bands; `jobs/reconciliation_job.py` already sniffs "book vs physical" columns. | GL/trial-balance tie-out: match engine-input inventory listing (and valuation) to GL control accounts; unmatched-both-directions; reconciling-item taxonomy (timing vs. unexplained); tie-out identity checks (GL − subledger = sum of reconciling items); IPE completeness/accuracy attestation per AS 1105.10 (row counts, control totals, hash). Existing module is inventory-units-specific and metadata-free. |
| **3. SOX 404 control-testing documentation** | Nothing. Guided layer (`src/guided.py`) has routing + SLA (`EscalationPacket.route_to/.sla`) and residual-risk language; `writeback.py` has approver identity — but no TOD/TOE concepts, no RCM, no deficiency taxonomy anywhere. | RCM builder, TOE sample-size-by-frequency logic (attribute-table-backed), test-sheet generation with attributes/exceptions, deficiency classification *proposal* (likelihood x magnitude, AS 2201.62–.70) with mandatory human conclusion, aggregation view. |
| **4. Calculation traceability / lineage** | Write path only: `Changeset.content_hash` (SHA-256, `writeback.py:59`), HMAC `Approval` w/ identity+TTL, `SqliteAuditLedger.applied_at`, byte-exact backups named by content hash (`connectors/excel.py`). Read path: `Deliverable.data_sources` + `prepared` are **hand-typed strings**; nothing auto-derived; QA failures not persisted; params-used not returned. | An `EvidenceRecord` for the read path: input-file SHA-256 + row/total fingerprints, params-as-used echo, formula/version identifier per calculation, caller-supplied timestamp + preparer identity, output hash, **positive QA attestation** (checks run, values compared, pass/fail), serialized alongside every deliverable and rendered on the workpaper face. Append-only evidence ledger is optional/open (see design doc §7). |

Cross-cutting gap: **sign-off.** The four-status guided contract
(`EXECUTED/OPTIONS/HANDOFF/ESCALATED`) expresses *that* a human must act, with
routing and SLA — but captures no reviewer identity or approval timestamp on
the analytical path. AS 1215's who-prepared/who-reviewed-and-when requirement
means the workpaper artifact itself must carry preparer/reviewer fields, with
Linchpin filling **preparer = the engine (identified tool + version)** and
leaving reviewer fields as the prepared human step (HANDOFF).

### GuidedOutcome status decision (required by the task)

**HANDOFF by default; ESCALATED on adverse findings; never EXECUTED.**

- Audit evidence is definitionally a *prepared human step*: only the auditor
  of record can conclude, sign, and file a workpaper. `HandoffPacket` is a
  precise fit (`title`, `steps`, `artifact` = the workpaper file, `deadline`
  = e.g. documentation-assembly window, `risk_if_skipped`).
- ESCALATED (`EscalationPacket`, financial trigger, route_to = engagement
  partner / concurring reviewer) when the engine's evaluation is adverse:
  UML > tolerable misstatement, attribute deviations exceed acceptable number,
  GL tie-out fails outside tolerance, or a proposed deficiency classification
  reaches significant-deficiency/material-weakness territory.
- EXECUTED is contractually wrong here even for "safe" cases — emitting a
  signed-off workpaper autonomously would place the engine in the auditor's
  chair. `jobs/qa.py::coverage_gate` should be extended so an audit-evidence
  outcome with status EXECUTED is itself a QA failure ("QA fails => no
  deliverable" then enforces the never-unprotected contract mechanically).

---

## 7. Open questions for a practicing auditor (blocking, before code)

1. **Table fidelity.** Exact AICPA Audit Sampling guide edition + table values
   (attribute tables; MUS confidence factors / sample-size factors) to pin the
   validation tests to. The worked numbers in this memo are candidates, not
   anchors, until checked against the guide.
2. **Who is Linchpin's user in the engagement?** (a) the audit firm using it
   as a firm tool, vs. (b) management/a consultant preparing IPE-style support
   the auditor then tests. The workpaper wording, independence framing, and
   who appears in preparer fields differ materially. Current positioning
   ("the human sells & decides") suggests (b) for mid-tier clients, but (a)
   is the higher-margin product.
3. **Workpaper conventions.** Indexing scheme (e.g. C-series for inventory),
   tickmark legend expectations, and whether output must import into CaseWare
   / TeamMate / Datasnipper-style e-workpaper systems (affects the Excel
   layout contract).
4. **MUS design choices.** Preferred handling of zero/negative balances,
   understatement evaluation, and whether the firm would rather have
   stratified classical variables sampling for inventory price testing.
5. **Materiality inputs.** Confirm the engine must *accept* TM / performance
   materiality as inputs and never derive materiality itself (that is the
   auditor's judgment; deriving it would overstep). Should
   `required_client_params` force these under `strict_params`?
6. **Deficiency classification.** Confirm the engine proposes likelihood /
   magnitude and a *candidate* classification only, with the human conclusion
   mandatory (compensating controls, prudent-official test).
7. **Evidence ledger scope.** Is an append-only local evidence ledger (7-year
   retention semantics) wanted, or is retention the firm's document-management
   system's job and Linchpin should only emit hash-verifiable artifacts?
8. **14-day lockdown (Dec 2026).** Does the target workflow need a "finalize"
   step that freezes and hashes the workpaper package for assembly?

---

## Sources

Primary standards (paywalled/anti-bot on direct fetch; content corroborated via the secondary sources below):

- [PCAOB AS 2315: Audit Sampling](https://pcaobus.org/oversight/standards/auditing-standards/details/AS2315) and [AS 2315 as amended, effective 12/15/2026](https://pcaobus.org/oversight/standards/auditing-standards/details/as-2315--audit-sampling-(effective-on-12-15-2026))
- [PCAOB AS 1105: Audit Evidence](https://pcaobus.org/oversight/standards/auditing-standards/details/AS1105) and [AS 1105 as amended (technology-assisted analysis)](https://pcaobus.org/oversight/standards/auditing-standards/details/as-1105-audit-evidence-(amended-for-fye-on-or-after-6-15-2025))
- [PCAOB AS 1215: Audit Documentation](https://pcaobus.org/oversight/standards/auditing-standards/details/AS1215) and [AS 1215 effective 12/15/2026 (14-day assembly)](https://pcaobus.org/oversight/standards/auditing-standards/details/as-1215--audit-documentation-(effective-on-12-15-2026))
- [PCAOB implementation page: technology-assisted analysis amendments](https://pcaobus.org/oversight/standards/implementation-resources-PCAOB-standards-rules/amendments-related-to-aspects-of-designing-and-performing-audit-procedures-that-involve-technology-assisted-analysis-of-information-in-electronic-form) · [PCAOB press release](https://pcaobus.org/news-events/news-releases/news-release-detail/pcaob-updates-its-standards-to-clarify-auditor-responsibilities-when-using-technology-assisted-analysis) · [SEC approval, Aug 2024](https://www.sec.gov/newsroom/press-releases/2024-100)
- [AU-C 530 Audit Sampling (text mirror)](https://apiproxy.utc.wa.gov/cases/GetDocument?docID=8&year=2019&docketNumber=190531) · [Wiley Practitioner's Guide to GAAS, AU-C 530 chapter](https://onlinelibrary.wiley.com/doi/10.1002/9781119789673.ch20)

Secondary / practice sources:

- [AICPA Audit Sampling guide (2025 ed.)](https://www.aicpa-cima.com/cpe-learning/publication/audit-sampling-audit-guide) · [AICPA guide key concepts summary](https://legalclarity.org/aicpa-aicpa-audit-guide-key-concepts-in-audit-sampling/)
- MUS mechanics and worked example: [MUS sample size](https://learnauditsampling.com/monetary-unit-sampling-sample-size-a-quick-guide/) · [MUS worked example](https://learnauditsampling.com/monetary-unit-sampling-example/) · [MUS confidence levels](https://learnauditsampling.com/confidence-level-in-monetary-unit-sampling/) · [Wiley Audit Guide Appendix C, MUS tables](https://onlinelibrary.wiley.com/doi/pdf/10.1002/9781119448617.app3)
- SOX 404 practice: [SOX 404 testing guide (Fieldguide)](https://www.fieldguide.io/resource-articles/sox-risk-assessment-guide) · [sample-size-by-frequency discussion (SOX forum)](https://www.sarbanes-oxley-forum.com/topic/6949/control-frequency-sample-size-1640) · [attribute sampling for SOC/SOX (Linford & Co)](https://linfordco.com/blog/audit-sampling/)
- Inspection findings: [PCAOB 2024 inspection activities spotlight](https://pcaobus.org/documents/staff-update-2024-inspection-activities-spotlight.pdf) · [inventory deficiency analysis (CPCON)](https://cpcongroup.com/insights/article/pcaob-inspection-findings-inventory-2024/) · [common findings & remediation (Ankura)](https://angle.ankura.com/post/102imwl/common-pcaob-inspection-findings-and-actions-firms-can-take-to-improve-audit-qual)
- Documentation practice: [AS 1215 14-day rule explainer](https://www.finrep.ai/blog/pcaobs-new-14-day-audit-documentation-rule-explained) · [documentation failures in enforcement (JGA)](https://www.jgacpa.com/back-to-basics-audit-documentation-failures-have-become-dangerous-low-hanging-fruit)
- Workpaper structure: [inventory observation case study (AABRI)](https://www.aabri.com/manuscripts/172760.pdf) · [inventory audit workpaper examples (Scribd)](https://www.scribd.com/document/856083055/Audit-Working-Papers-Inventory)
