# Brief 05 — pair `qa-gate`

**Claude writes:** `documentation/blog/es/compuerta-qa.md` and
`documentation/blog/en/inventory-ai-qa-gate.md`.

This is the trust / anti-chatbot article (calendar E1/E2, bible §2.5).

## Queries

| | ES | EN |
|---|---|---|
| Primary | IA inventario cifras sin fuente | inventory AI quality gate |
| Secondary | por qué no fiarse de ChatGPT para reorden | chatbot reorder quantity hallucination |
| Not this article | full autonomy ladder T1–T3 as a shipped Tower | Control Tower as if it were live |

## Persona

Skeptic who already tried a generic LLM on a CSV. Ops lead who must defend a
number to a CFO.

## Facts to lock (read code / docs, do not inflate)

Never-unprotected outcomes (`nosilo_core.guided`): `EXECUTED`, `OPTIONS`,
`HANDOFF`, `ESCALATED`. **Three of four require a human by design.** Allowed
wording is that sentence. Forbidden: unmeasured frequencies ("3 of 4 runs").

Orchestrator contract (CLAUDE.md / `scm_agent` README): **QA fails ⇒ no
deliverable**. Statuses include `ok`, `needs_clarification`, `needs_data`,
`qa_failed`, `error`. If you name them, read `scm_agent` types so you do not
invent a sixth.

Writeback: dry-run changeset, risk tiers `read` / `reversible` / `irreversible`,
approval TTL 900s, `rollback()`. Irreversible always needs explicit approval.
Name this once. This article is about **not shipping a bad number**, not a
writeback tutorial.

Citations: deliverables cite L3 sources when the citation gate allows. Do not
claim "every cell in Excel has a footnote".

If you use `documentation/KERN_AUTOPSIA_FALLAS.md`, only facts that are in that
file (a real bug found, measured, tested, shipped). No extra heroics.

## Required sections

1. The failure mode: plausible number, no σ definition, no QA, no refusal.
2. What the gate actually vetoes (no deliverable). Contrast with a chatbot
   that always answers.
3. Four endings; three need a human.
4. Compared-to: generic AI tools (bible ⑤); SaaS that makes the correct method
   an optional toggle (bible ②).
5. What Kern does not do: fully autonomous purchasing; "trust us, it's AI".
6. FAQ:
   - Can I still get a wrong number? (Yes — garbage in; QA is not omniscience.
     Be explicit.)
   - What happens when QA fails?
   - Does a human still look at it?
   - Are you using my CSV to train a public model? (Do not invent legal copy.
     Point at existing public legal pages if they exist under `documentation/legal/`;
     if unsure, say the demo processes the file to produce the scan and do not
     freelance a DPA.)
   - Is this the Control Tower? (**No.** Production engine + human operator today.)

## Sources

- `kind: code` — orchestrator / QA path you actually opened, e.g.
  `scm_agent/orchestrator.py` (only cite symbols you saw)
- `kind: code` optional `nosilo_core/guided.py` if present in this tree
- Book: optional; method citations matter less here than the contract. A
  forecasting book does not prove the QA-gate.

## CTA

`cta: diagnostico` · `offer: diagnostico-arranque`
Invite a CSV + a call, not "turn on autonomy". `/demo` as a lighter proof that
a scan can refuse to emit a number when QA fails (only if that is true of the
demo path — **verify** `webapp/static/demo/index.html` / demo API: the demo
already documents QA-fail ⇒ no figure. You may say that if you keep it scoped
to the demo).

## Landmines

- Entire §10 list, especially autonomy % and "no human".
- Do not claim SOC2/ISO unless a legal doc says so (they are not a blog claim).
- Do not name Nosilo in the title. One sentence max in the body.

## Transcreation notes

ES: "otra IA que inventa el número". EN: "ChatGPT on a sales CSV". Same gate.
