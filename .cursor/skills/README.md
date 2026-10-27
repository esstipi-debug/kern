# Agent skills

Skills for Cursor and Claude Code. Copy to `~/.claude/skills/` for global Claude use.

Two families. The `vandeput-*` ones explain the **models** behind the numbers;
`kern-data-storytelling` composes the **narrative** over a deliverable that
already exists.

| Skill | Use when |
|-------|----------|
| [vandeput-inventory-optimization](vandeput-inventory-optimization/SKILL.md) | Any task in this repo; start here |
| [vandeput-inventory-eoq-policies](vandeput-inventory-eoq-policies/SKILL.md) | EOQ, safety stock, (s,Q), (R,S), simulation |
| [vandeput-inventory-service-cost](vandeput-inventory-service-cost/SKILL.md) | Fill rate, cost optimization, lead time |
| [vandeput-inventory-advanced](vandeput-inventory-advanced/SKILL.md) | Gamma, GSM, newsvendor, KDE, sim-opt |
| [kern-data-storytelling](kern-data-storytelling/SKILL.md) | The story of a QA-gated `Deliverable`: executive narrative, board read, deck script |

`kern-data-storytelling` composes and orders the artifact's own fields
(`findings`, `kpis`, `options`, `citations`, `residual`). It runs no engine code,
registers no tool, and never invents a figure — see its "What this skill is NOT"
section for why it is a skill and not tool #61.

Reference files: `vandeput-inventory-optimization/modules.md`, `formulas.md`.

**Claude Code:** skills live at `C:\Users\Gamer\.claude\skills\vandeput-inventory-*`
