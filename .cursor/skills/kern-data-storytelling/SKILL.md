---
name: kern-data-storytelling
description: >-
  Compone el relato de decision (storytelling) de un entregable de Kern que YA
  existe y paso el gate de QA: el Deliverable con su summary, findings, kpis,
  recommendations, options, data_sources, citations, readings, confidence y
  residual. Ordena y conecta lo que el motor ya calculo; NO calcula, no inventa
  una cifra y no esconde el residual. Para producir los numeros corre un tool de
  Kern, no esta skill. Para el angulo de liderazgo/CHAIN, ver liderazgo-chain.
  Trigger - "el storytelling de esto", "contame la historia de esta corrida",
  "narrativa ejecutiva", "algo que el CFO lea", "guion para el deck", "como se
  lo cuento al directorio", "pasame el reporte a relato".
---

# Kern data storytelling

Repo: **`C:\Users\Gamer\Music\scm\kern\`** — el clon canonico (rama `main`). El
clon `supply-chain-optimization/` esta **retirado**: solo archivo, no se trabaja
ahi, y las skills `kern-*` que aun apuntan alli estan desactualizadas en ese
punto. Esta skill no necesita escribir en el repo para funcionar: lee el
entregable y produce el relato.

**The story is COMPOSED, not generated.** Every claim in the output must be
traceable to a field of the artifact you were handed. If a sentence needs a
number that is not in the artifact, the fix is a different sentence -- or
another tool run -- never an estimate.

This is the one thing that separates this skill from every generic
"data storytelling" skill: those turn a raw dataset into a narrative, so they
must produce findings, and the finding is the model's opinion. Kern has already
produced the findings, QA-gated them (`QA fails => no deliverable`), and
attached the citation for each. Your job is the arc between them.

## What this skill is NOT

- **Not a tool.** It registers nothing, touches no engine module, and does not
  run `scm_agent`/`jobs/`. Adding a 61st tool means the eight fixed places in
  `.specify/memory/constitution.md` §I; this skill is deliberately on the other
  side of that line.
- **Not a figure producer.** No arithmetic beyond restating what the artifact
  says. No percentage of a percentage, no annualization, no "roughly 3x".
- **Not a substitute for the artifact.** The story is a *reading* of the
  deliverable. `report.md` still ships, and the story never replaces it.
- **Not a marketing voice.** See "Voice" below. Hype in a Kern narrative reads
  as a claim the engine did not make.

## Input: the artifact contract

Take the `Deliverable` (`src/deliverable.py`) -- either the object or its
`to_markdown()` render. These are the fields you may draw on, and nothing else:

| Field | What it gives the story |
|---|---|
| `title`, `client`, `prepared` | The frame: whose decision, dated when |
| `summary` | The engine's own one-paragraph claim -- the spine's anchor |
| `findings` | `Finding(title, detail, impact)` -- the tension. `impact` is the cost |
| `recommendations` | Ordered actions the engine already ranked |
| `options` | `GuidedOutcome`s: `label`, `summary`, `action`, `tradeoffs`, `recommended` |
| `kpis` | `Kpi(name, value, target, rationale)` -- the state of play |
| `data_sources` | `DataSource(field, source, cadence)` -- where each number came from |
| `citations` | L3-grounded theory behind the method |
| `readings` | Weaker, **ungated** grounding. Never present as an extension of citations |
| `confidence` | `float \| None`. `None` is a fact: it was not measured |
| `residual` | The human action that remains. **Non-negotiable in the output** |

If you were handed only prose and no artifact, stop and ask for the artifact.
Composing from memory is how a dropped figure ships.

## Workflow

1. **Read the whole artifact before writing a word.** All of it, including
   `residual` and `readings`.
2. **Pick the spine** (below) -- which 2-3 findings actually carry the decision.
   Not all of them. A story with eleven findings is a table.
3. **Draft the movements**, keeping every figure verbatim.
4. **Run the figure check** (see "Verify before it ships").
5. **Close with the human step**, quoting `residual` and `confidence`.

## The spine: four movements

The arc is latent in the artifact's own structure. You are revealing it, not
adding it:

1. **Dónde estamos** -- `summary` + the `kpis` that matter. Short. The reader
   already suspects the problem; do not build suspense.
2. **Qué está mal y cuánto cuesta** -- the chosen `findings`, with their
   `impact`. This is the only place the narrative is allowed to be vivid, and
   it is vivid because the cost is real, not because the adjectives are.
3. **Qué se puede hacer** -- `options` with their `tradeoffs`, naming which one
   is `recommended`. Ranked choices, or a prepared human step (`HANDOFF`), or a
   routing with SLA (`ESCALATED`). **Do not collapse a ranked menu into a single
   announcement** -- that erases the human's decision, which is the product.
4. **Qué no sabemos y qué falta que decidas** -- `confidence`, `residual`, and
   any absence declared in the artifact.

## Which arc fits

Match the shape to what the artifact actually shows. Forcing an arc is
inventing a claim.

| Situation in the artifact | Arc that fits |
|---|---|
| One big cost, clear cause | **Cost of inaction**: state -> bleeding -> stop it -> what it takes |
| A ranked menu of real options | **Fork in the road**: the constraint -> why each option exists -> what each costs |
| A refusal, `needs_data`, or a thin history | **What we could not measure and why** -- never dress a refusal as a number |
| Lead-time/disruption framing | **Before and after the shock**: normal -> what broke -> recovery time |
| Getting leadership to fund the work | **The case**: what supply chain really is for the business, then the numbers |

The last one is the only arc that leans on theory rather than artifact alone,
and the L3 graph already holds it: `knowledge::chain_storytelling` ("storytelling
as a leadership skill"), `knowledge::chain_narrative` ("no one is inspired by
functions; what inspires us are stories"), `knowledge::chain_influential`,
`knowledge::storytelling_influence`,
`knowledge::storytelling_and_simple_communication`,
`knowledge::purpose_driven_narrative`.

**Cite those only if the artifact's `citations` already reach them.** Whether a
citation survives to the client is decided by `scm_agent/citation_gate.py`
(anchor concepts within `MAX_HOPS`, `MIN_CITATIONS` or the whole batch is
dropped). The gate **removes and never invents**; if you add a citation the gate
never approved, you have manufactured provenance -- the exact defect
`tests/test_l3_source_inventory.py` exists to catch.

## Five hard rules

Each of these is a bug the repo already paid for. They are not style.

**1. Every figure appears exactly as the artifact states it.** No re-rounding,
no recomputation, no derived percentages. `scm_agent/narrative_critic.py` exists
because `narrative_rewrite` replaced a whole summary with prose and nothing
verified the numbers survived (gap 8: "ninguna de las funciones verify toma
prosa"). A human reading a story is *less* likely to check the arithmetic than a
human reading a table.

**2. Absence is declared, never zero-filled.** `src/deliverable.py::not_measured`
exists for this, and the repo's rule is `None != 0` throughout: an absent
`on_order`, a missing `forecast`, an unmeasured `confidence` are all *unknown*,
and a zero would assert "measured, and it was nothing". Write what could not be
measured **and which column or field would make it measurable**.

**3. The residual and the confidence ship every time.** `to_markdown` renders
the Coverage & handoff section unconditionally, "so no deliverable ever reads as
'fully autonomous' when it isn't". A story that drops it is the one defect this
skill could introduce that the engine cannot catch.

**4. Never invent causality.** The artifact gives association and cost, not
mechanism, unless a tool actually measured the mechanism. "Lead time rose and
stockouts followed" is defensible. "Lead time caused the stockouts" is a claim
no engine run in this repo made.

**5. Facts keep their numbers; judgment stays visibly a judgment.** Label
inference as inference. A recommendation is the engine's, an interpretation is
yours, and the reader must be able to tell which is which.

## Voice and language

- **Language:** compose in the language the operator asks for. The artifact is
  **mixed by design** -- `src/i18n.py` translates the package scaffolding
  (`Deliverable.lang` defaults to `"en"`; `DEFAULT_LANG` is `"es"` for
  packages), while each tool's own `Finding`/`summary` prose stays in its
  engine-native English. So an `"es"` story carries English findings: quote them
  faithfully, translate around them, and never quietly rewrite a quoted finding
  into the target language as if it were the artifact's text.
- **Persona:** `nosilo_core.llm.narrative_rewrite` takes a `persona` and threads
  it into the prompt. Keep the same voice the deliverable was written in --
  switching voice between `report.md` and its story makes two documents about
  one run read as two runs.
- **Tone:** a CFO is not moved by "revolutionary" or "guaranteed savings", and
  both are anti-claims against `KERN_IDENTIDAD_Y_FILOSOFIA.md`. State the cost,
  the option, and the residual.
- **Never claim autonomy.** The identity doc is explicit: *"Kern es un socio
  operativo, no un plugin"*, and the anti-claim is **Kern prepares, the human
  signs**. `EXECUTED` is the only one of the four `GuidedOutcome` statuses the
  agent did alone -- `OPTIONS`, `HANDOFF` and `ESCALATED` all route to a human,
  and the story must say so.

## Verify before it ships

The artifact's figures are the test fixture:

1. **Figure diff.** Collect the numeric tokens from the artifact and from your
   draft; every artifact figure must appear in the draft unless you can state
   why it was irrelevant to the decision. `scm_agent/narrative_critic.py::figures`
   is the existing extractor -- reuse its idea rather than eyeballing.
2. **Residual present.** The draft names the human step, verbatim from
   `residual` (or says explicitly that the artifact declared none).
3. **Absences named.** Every `None`/unmeasured value you touched is described as
   not measured, with the field that would measure it.
4. **Citations match.** The citations in the draft are a subset of the
   artifact's. Nothing added.
5. **Traceability.** Each movement's claims map to a named field. If a sentence
   cannot be traced, delete it -- do not soften it.

## Anti-patterns

| The generic storyteller writes | Kern writes |
|---|---|
| "Sales data reveals a shocking trend" | "Lead time rose from 14 to 21 days between W12 and W18 _(impact: 3 stockout weeks)_" |
| "We estimate ~$40k in savings" | the artifact's figure, or nothing |
| "This metric wasn't tracked" | "Not measured: `on_order`. Add the in-transit column to net the trigger." |
| "Revolutionary AI-driven optimization" | "The engine will not size this SKU: 4 periods of history, 6 required." |
| "The system will reorder automatically" | "Prepared, not applied: this changeset is staged and needs your approval." |
| A tidy resolution | "Residual: your call on the service level, which is not in the file." |

## Related skills

- **`vandeput-inventory-*`** explain the models behind the numbers. Use them to
  answer *why this policy* -- not to restate the story.
- **`liderazgo-chain`** owns the leadership diagnosis: the CHAIN model, its
  rubrics, archetypes and the five lenses. This skill **borrows** the Narrative
  dimension's framing for the "fund the work" arc and hands the leadership
  assessment itself to that skill. Composing a story is not scoring a leader.
- For the numbers themselves, run the tool. For the story of the run, this skill.
