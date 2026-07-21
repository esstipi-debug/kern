# Gamified buttons & progress bars — design spec

Date: 2026-07-20
Status: implemented (see `docs/superpowers/plans/2026-07-20-gamified-buttons.md` for the
as-built plan; this spec was originally brainstormed against a stale duplicate checkout
of this repo (`C:\Users\Gamer\linchpin`) and ported here once the mixup was caught —
the design decisions below are unaffected, only the file paths in the original session
referred to that other checkout).

## Problem

Kern's web UI (`/`, `/console`, `/operator`) reads as a corporate "operations control-room" —
calm, data-dense, Linear/Vercel-dashboard precision (see `documentation/UI_DESIGN_BRIEF.md`
§5, if present in this checkout). The user wants a more game-like *feel* specifically in
buttons and progress indicators, without abandoning the credibility the control-room
aesthetic gives the platform in front of enterprise buyers. Scope is **subtle
gamification**: visual/tactile treatment of existing components only — no new
gamification concepts (achievements, streak badges, XP as a metric) are introduced. This
applies consistently across both the marketing/onboarding surface (`/operator`) and the
working dashboard/console (`/`, `/console`), per explicit user preference.

## Direction chosen

Of three directions explored (RTS command-center, RPG progress/stats, retro arcade
terminal — see brainstorm session), the user picked **B, RPG progress/stats**: warm
"reward" accent color, chunky 3D-press buttons, glossy pill progress bars. Confirmed
via the visual companion (3 clicks landing on B) and terminal follow-up.

## What stays the same

- The teal brand accent (`--accent` / `--accent-bright`, `#4fd1c5` / `#5eead4`) remains
  the identity color everywhere it already appears: logo mark, links, status chips
  (ok/warn/bad), chart colors, secondary/ghost buttons.
- Status chips (`.scm-ok`, `.scm-warn`, `.scm-bad` and friends) are untouched — no new
  badge/achievement UI is introduced.
- Overall dark theme, typography (Inter + JetBrains Mono / Space Grotesk on `/operator`),
  and layout (cards, tables, sidebar) are untouched.
- `input[type=range]` sliders are out of scope (not a button or a progress bar).

## What changes

### 1. New warm "action" accent

A gold→coral gradient reserved **only** for primary buttons and the "reserve" segment
of progress bars — the "gold = reward/action" pattern common in game HUDs, and a
genuine UX improvement: today's primary CTA is teal-on-teal (low contrast against the
rest of the UI), so this also fixes a real hierarchy weakness.

New tokens:

```css
--accent-warm: #ffb545;       /* face */
--accent-warm-2: #ff8a5c;     /* gradient partner */
--accent-warm-deep: #b5601f;  /* bevel shadow shade, ~35% darker than --accent-warm */
```

### 2. Primary button — "ledge" press bevel

Research-backed pattern (Duolingo-style solid offset shadow, the most widely replicated
and cheapest-to-animate technique found across sources — see brainstorm session
research pass): `box-shadow: 0 <offset>px 0 <deep-shade>`, `:active` moves
`translateY(<offset>px)` and collapses the shadow to `0 0 0`. As-built, this uses a 6px
offset for large hero CTAs (`.scm-run`, `.btn-primary`) and a 4px offset for compact
ones (the floating console pill, `app.js`'s inline action buttons), `border-radius`
14px for hero buttons / `999px` (pill) for the floating link.

Applies to (existing selectors get their rule bodies updated in place — no new class
names introduced into markup where a selector already exists cleanly):

- `.scm-run` (`/console` main CTA)
- `.btn-primary` (`/operator` hero CTA)
- The floating "◈ Agent console →" pill on `/` — was inline-styled with
  `onmouseover`/`onmouseout` JS handlers; converted to a CSS class (`.console-pill`,
  defined in `index.html`'s own `<style>` block since it has exactly one consumer) so
  the same `:hover`/`:active` rules apply without duplicating logic in JS.
- The "Exportar"/"Reintentar" buttons in `app.js` — converted to `class="btn-warm-action"`
  (defined in the new `webapp/static/theme.css`), since inline styles cannot express
  `:hover`/`:active`.

Secondary/ghost buttons (`.scm-chip`, `.btn-ghost`) keep their current neutral teal
look; only gain the same `:active` press affordance (small `translateY`) for tactile
consistency with the primary button family.

### 3. Progress bars — pill + gloss

Research-backed "classic" CSS-Tricks gloss recipe (pill track + inset-shadow gloss on
the fill, zero JS), defined in `webapp/static/theme.css` as `.bar-track` (pill shape,
`border-radius: var(--r-chip)`, owns the shared `background: var(--track)`) and
`.bar-fill-gloss` (the inset-shadow shine, color-agnostic — color stays inline per
usage so the semantic budget-utilization gauge keeps its dynamic ok/warn/bad color).
`.bar-fill-safety` supplies the warm gradient for the SKU coverage bar's safety-stock
segment; `.bar-fill-shimmer` adds an optional diagonal stripe animation, gated behind
`prefers-reduced-motion`.

As-built scope ended up narrower than the original two-bars description once the real
`app.js` was read closely: there are exactly **two** genuine fill-percentage progress
bars in this codebase — the budget-utilization gauge (single fill, semantic color) and
the two-segment SKU coverage bar (cycle-stock teal + safety-stock, now warm). A third
`var(--track)` usage in `app.js` (a bias-deviation marker with a positioned dot) is
**not** a fill bar and was correctly left untouched.

Side effect (intentional, flagged to the user as a fix, not scope creep): the cycle vs.
safety-stock segments were previously two near-identical teal shades with weak
contrast; making the safety segment warm fixes that readability issue for free. The
"Allocation by SKU" legend swatch color was updated to match (a real bug caught by
final code review — the legend still said teal for "safety" after the bar itself
turned gold).

### 4. Token architecture (revised from the original brainstorm)

The original brainstorm assumed `index.html` and `webapp/static/prototype/index.html`
duplicated an identical `:root { ... }` token block byte-for-byte (per an existing code
comment claiming this). Closer reading during plan-writing found this comment is
**stale** — the two files' token blocks actually diverge in several values (e.g.
`--r-card` 14px vs 16px, `--shadow`'s alpha, prototype defines `--ease` which
`index.html` doesn't). Consolidating them would have silently changed unrelated visual
output, so the plan was revised:

- `webapp/static/theme.css` is purely **additive** — it adds the 3 new warm tokens plus
  the new shared component classes (`.btn-warm-action`, `.bar-track`, `.bar-fill-gloss`,
  `.bar-fill-safety`, `.bar-fill-shimmer`). It does not redeclare or touch any existing
  token.
- `index.html` and `prototype/index.html` both link `theme.css` (for the new tokens/
  classes) but keep their own pre-existing `:root` blocks exactly as they were.
- `operator/index.html` is **not** linked to `theme.css` at all — it has its own
  distinct token vocabulary and a structurally different template (sidebar + hero
  layout, different font stack). It keeps its own `:root` block, with the 3 warm tokens
  added directly using its existing naming convention.
- `app.js` keeps generating markup via string templates (no framework introduced);
  only the `class="..."` values on button/bar templates changed. The two-segment bar's
  safety segment moved its color from an inline `var(--accent2-bar)` to the
  `.bar-fill-safety` class; `--accent2-bar` itself was removed from `index.html`'s
  `:root` as dead code once nothing referenced it anymore.

## Out of scope

- No new badges, achievements, streaks, or other net-new gamification *content* —
  visual/tactile restyle only, per explicit scope decision during brainstorm.
- No design-token consolidation for `/operator`, or between `index.html`/`prototype`,
  beyond adding the 3 warm tokens — see §4 above for why.
- No changes to status chips, charts, sliders, or non-button/non-progress-bar
  components.
- No automated visual-regression tooling is introduced (none exists in the repo today
  — out of scope to build one for a CSS restyle).

## Verification plan

No frontend test suite exists for this vanilla JS/CSS UI. Verification was manual, via
the running dev server and direct DOM/computed-style inspection:

1. Confirmed primary buttons on `/`, `/console`, `/operator` render the warm gradient +
   ledge box-shadow, and text/data-action content is unchanged.
2. Confirmed the SKU coverage bar's two segments are visually distinguishable (teal
   cycle vs. warm gold safety) with the pill/gloss treatment, and the shimmer animation
   actually renders (this required a follow-up fix — see below).
3. Confirmed no console errors on any of the three pages after the restyle.
4. `:focus-visible` and `prefers-reduced-motion` gating were verified by direct
   inspection of the shipped CSS (the rules are present and correctly scoped) rather
   than by simulating real keyboard focus in an automated browser, which proved
   unreliable for triggering `:focus-visible` from scripted `.focus()` calls.

A final whole-branch code review (opus-tier, adversarial) caught two real defects
fixed before merge: an inline `background` shorthand on the coverage bar's cycle
segment was clobbering `.bar-fill-shimmer`'s `background-image`, silently making the
shimmer non-functional (fixed by using the `background-color` longhand inline instead);
and the "safety" legend swatch was left teal after the bar itself turned warm gold.

## Risks / trade-offs

- **Brand dilution risk**: introducing a second prominent accent color could read as
  inconsistent if it leaks beyond buttons/progress-bars. Mitigated by keeping the warm
  tokens strictly scoped to those two component families in this change.
- **`operator/index.html` and `index.html`/`prototype` token drift**: keeping these on
  separate token systems (rather than forcing consolidation) means future palette
  tweaks must be applied in multiple places instead of one. Accepted trade-off — see
  §4 for why forcing a merge was rejected.
