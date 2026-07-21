# Gamified Buttons & Progress Bars Implementation Plan

> **Provenance:** this plan was authored during a brainstorm session against a stale
> duplicate checkout (`C:\Users\Gamer\linchpin`) before the mixup was caught; it was
> executed there (6 tasks, subagent-implemented + independently reviewed, all clean)
> then ported to this repo once the correct checkout was identified. The touched files
> matched byte-for-byte in every region this plan edits, so the diffs reapplied without
> adaptation. See `docs/superpowers/specs/2026-07-20-gamified-buttons-design.md` for
> the as-built deviations from the original design (token-architecture revision, two
> real bugs a final review caught and fixed).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Kern's primary action buttons and progress bars a "game-like" tactile
treatment (warm gold/coral ledge-press bevel on buttons, glossy pill progress bars)
across `/`, `/console`, and `/operator`, without touching brand teal, status chips,
charts, or layout.

**Architecture:** A new small additive stylesheet (`webapp/static/theme.css`) holds
the new warm design tokens plus the handful of genuinely shared component classes
consumed by `index.html`'s app.js-rendered content. Every other restyled element
(`.scm-run`, `.scm-chip`, `.btn-primary`, `.btn-ghost`, the floating console link)
already has its own dedicated selector in the file that defines it — those get their
rule bodies edited in place rather than migrated to shared classes, since each has
exactly one owner file and forcing a shared abstraction there would be speculative,
not DRY.

**Tech Stack:** Vanilla CSS + hand-rolled JS template strings (no build step, no
framework, no existing test runner for the frontend).

## Global Constraints

- New tokens are strictly additive: `--accent-warm:#ffb545`, `--accent-warm-2:#ff8a5c`,
  `--accent-warm-deep:#b5601f`. Never redefine an existing token name or value.
- The existing teal accent (`--accent`/`--accent-bright` on `/` and `/console`,
  `--accent`/`--accent-2`/`--accent-deep` on `/operator`), status chips
  (`.scm-ok`/`.scm-warn`/`.scm-bad`), chart colors, and `input[type=range]` sliders are
  never touched.
- No new gamification *content* (badges, achievements, streaks) — visual/tactile
  restyle of existing components only.
- Any looping animation (the bar shimmer) must be gated behind
  `@media (prefers-reduced-motion: reduce)`.
- Every restyled interactive element keeps (or gains) a visible `:focus-visible`
  outline — keyboard accessibility must not regress.
- `webapp/static/index.html` and `webapp/static/prototype/index.html` do **not**
  share a byte-identical token block despite an existing code comment claiming they
  do (`--r-card` is `14px` vs `16px`, `--shadow`'s first layer alpha is `.7` vs `.8`,
  prototype also defines `--ease` which index.html doesn't). Do not "fix" or
  consolidate this pre-existing divergence as part of this work — it's out of scope
  and touching it would change unrelated visual output. `theme.css` must only add
  brand-new token names, never redeclare an existing one.
- `webapp/static/operator/index.html` has its own fully separate token system and
  template (sidebar+hero layout, different font stack) — it is not linked to
  `theme.css`; its warm tokens are added directly to its own `:root` block.
- No automated frontend test suite exists in this repo. Every task's verification
  step is manual, via the running dev server (`uvicorn webapp.app:app` /
  the project's existing run command) — confirmed pattern: no Jest/Playwright config
  under `webapp/`.

---

### Task 1: Create `webapp/static/theme.css`

**Files:**
- Create: `webapp/static/theme.css`

**Interfaces:**
- Produces: CSS custom properties `--accent-warm`, `--accent-warm-2`,
  `--accent-warm-deep`; classes `.btn-warm-action`, `.bar-track`, `.bar-fill-gloss`,
  `.bar-fill-safety`, `.bar-fill-shimmer` — consumed by Task 4 (`app.js`) and linked
  from Task 2 (`index.html`) and Task 3 (`prototype/index.html`, tokens only).

- [ ] **Step 1: Write the file**

```css
/* webapp/static/theme.css
   Additive tokens + component classes for the gamified button/progress-bar
   treatment. See docs/superpowers/specs/2026-07-20-gamified-buttons-design.md.
   Linked by index.html and prototype/index.html. operator/index.html keeps its
   own standalone token system (different template/layout) and is not linked here.
*/

:root {
  --accent-warm: #ffb545;
  --accent-warm-2: #ff8a5c;
  --accent-warm-deep: #b5601f;
}

/* compact "game" action buttons — app.js "export"/"retry" CTAs */
.btn-warm-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--sans);
  font-size: 13px;
  font-weight: 600;
  color: #2a1200;
  cursor: pointer;
  border: none;
  border-radius: var(--r-field);
  padding: 10px 16px;
  background: linear-gradient(150deg, var(--accent-warm), var(--accent-warm-2));
  box-shadow: 0 4px 0 var(--accent-warm-deep);
  transform: translateY(0);
  transition: transform 80ms ease-out, box-shadow 80ms ease-out, filter 120ms ease-out;
}
.btn-warm-action:hover { filter: saturate(1.1); }
.btn-warm-action:active { transform: translateY(4px); box-shadow: 0 0 0 var(--accent-warm-deep); }
.btn-warm-action:focus-visible { outline: 2px solid var(--accent-warm); outline-offset: 2px; }

/* progress bars — pill shape + glossy fill */
.bar-track {
  border-radius: var(--r-chip);
  background: var(--track);
  overflow: hidden;
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, .3);
}
.bar-fill-gloss {
  border-radius: var(--r-chip);
  box-shadow: inset 0 2px 5px rgba(255, 255, 255, .22), inset 0 -2px 4px rgba(0, 0, 0, .22);
}
.bar-fill-safety {
  background: linear-gradient(to bottom, var(--accent-warm), var(--accent-warm-2));
}
.bar-fill-shimmer {
  background-image: linear-gradient(135deg, rgba(255, 255, 255, .15) 25%, transparent 25%,
    transparent 50%, rgba(255, 255, 255, .15) 50%, rgba(255, 255, 255, .15) 75%, transparent 75%);
  background-size: 20px 20px;
  animation: bar-stripes 3s linear infinite;
}
@keyframes bar-stripes {
  to { background-position: 40px 0; }
}
@media (prefers-reduced-motion: reduce) {
  .bar-fill-shimmer { animation: none; background-image: none; }
}
```

- [ ] **Step 2: Verify the file exists and is well-formed**

This is a Python repo with no Node/CSS toolchain installed — don't assume `node` is
on PATH. Run: `python -c "s=open('webapp/static/theme.css').read(); assert s.count('{')==s.count('}'); print('OK', len(s), 'bytes')"`
Expected: `OK <n> bytes` (confirms the file exists and braces balance; real visual
validation happens once it's linked in Task 2/3 — there is no CSS linter configured
in this repo to run standalone).

- [ ] **Step 3: Commit**

```bash
git add webapp/static/theme.css
git commit -m "feat(webapp): add gamified button/bar theme tokens and classes"
```

---

### Task 2: Wire `index.html` to `theme.css` + restyle the floating console pill

**Files:**
- Modify: `webapp/static/index.html:10` (add stylesheet link)
- Modify: `webapp/static/index.html:62-63` (add `.console-pill` class to the existing `<style>` block)
- Modify: `webapp/static/index.html:67-70` (simplify the floating link markup)

**Interfaces:**
- Consumes: `webapp/static/theme.css` tokens (`--accent-warm`, `--accent-warm-2`,
  `--accent-warm-deep`) from Task 1.
- Produces: `.console-pill` class available on this page only.

- [ ] **Step 1: Add the stylesheet link**

In `webapp/static/index.html`, after line 10 (the Google Fonts `<link>`) and before
the `<style>` tag on line 11, insert:

```html
<link rel="stylesheet" href="/static/theme.css">
```

- [ ] **Step 2: Add the `.console-pill` class**

In `webapp/static/index.html`, the existing `<style>` block ends with this rule
(current lines 62-63):

```css
  [role="tab"][aria-selected="false"]:hover{color:var(--txt-2)}
</style>
```

Change it to:

```css
  [role="tab"][aria-selected="false"]:hover{color:var(--txt-2)}
  .console-pill{
    position:fixed; bottom:18px; right:18px; z-index:50;
    display:flex; align-items:center; gap:7px;
    font:600 12.5px/1 'JetBrains Mono',ui-monospace,monospace;
    color:#2a1200; text-decoration:none;
    background:linear-gradient(150deg,var(--accent-warm),var(--accent-warm-2));
    padding:11px 16px; border-radius:999px;
    box-shadow:0 4px 0 var(--accent-warm-deep);
    transform:translateY(0);
    transition:transform 80ms ease-out, box-shadow 80ms ease-out, filter 120ms ease-out;
  }
  .console-pill:hover{filter:saturate(1.1)}
  .console-pill:active{transform:translateY(4px); box-shadow:0 0 0 var(--accent-warm-deep)}
  .console-pill:focus-visible{outline:2px solid var(--accent-warm); outline-offset:2px}
</style>
```

- [ ] **Step 3: Simplify the floating link markup**

Current (lines 67-70):

```html
<a href="/console" aria-label="Open the agent console"
   style="position:fixed;bottom:18px;right:18px;z-index:50;display:flex;align-items:center;gap:7px;font:600 12.5px/1 'JetBrains Mono',ui-monospace,monospace;color:var(--ink);text-decoration:none;background:linear-gradient(150deg,var(--accent-bright),var(--accent));padding:11px 16px;border-radius:999px;box-shadow:0 10px 26px -10px rgba(79,209,197,.6);transition:transform .15s,box-shadow .15s"
   onmouseover="this.style.transform='translateY(-2px)';this.style.boxShadow='0 16px 34px -10px rgba(79,209,197,.85)'"
   onmouseout="this.style.transform='none';this.style.boxShadow='0 10px 26px -10px rgba(79,209,197,.6)'">◈ Agent console →</a>
```

Replace with:

```html
<a href="/console" aria-label="Open the agent console" class="console-pill">◈ Agent console →</a>
```

- [ ] **Step 4: Visual check**

Start the dev server (see Task 6 for the exact command) and open `/`. Confirm:
- The bottom-right pill is now gold/coral instead of teal.
- Clicking and holding it (mousedown) visibly presses it down; releasing restores it.
- Tab-focusing it shows a visible gold outline.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/index.html
git commit -m "feat(webapp): restyle floating console link with gamified bevel"
```

---

### Task 3: Wire `prototype/index.html` to `theme.css` + restyle `.scm-run` / `.scm-chip`

**Files:**
- Modify: `webapp/static/prototype/index.html:10` (add stylesheet link)
- Modify: `webapp/static/prototype/index.html:99-107` (`.scm-run` rule body)
- Modify: `webapp/static/prototype/index.html:92-97` (`.scm-chip` — add `:active`)

**Interfaces:**
- Consumes: `webapp/static/theme.css` tokens from Task 1.

- [ ] **Step 1: Add the stylesheet link**

In `webapp/static/prototype/index.html`, after line 10 (Google Fonts `<link>`) and
before the `<style>` tag on line 11, insert:

```html
<link rel="stylesheet" href="/static/theme.css">
```

- [ ] **Step 2: Restyle `.scm-run`**

Current (lines 99-107):

```css
  .scm-run{
    width:100%; margin-top:20px; font-family:var(--sans); font-size:15px; font-weight:700; letter-spacing:.01em;
    color:var(--ink); cursor:pointer; border:0; border-radius:12px; padding:13px 18px;
    background:linear-gradient(150deg,var(--accent-bright),var(--accent));
    box-shadow:0 12px 26px -12px rgba(79,209,197,.65); transition:transform .15s var(--ease), box-shadow .15s, filter .15s;
    display:flex; align-items:center; justify-content:center; gap:9px;
  }
  .scm-run:hover{transform:translateY(-1px); box-shadow:0 16px 32px -12px rgba(79,209,197,.8); filter:saturate(1.08);}
  .scm-run:active{transform:translateY(0);}
```

Replace with:

```css
  .scm-run{
    width:100%; margin-top:20px; font-family:var(--sans); font-size:15px; font-weight:700; letter-spacing:.01em;
    color:#2a1200; cursor:pointer; border:0; border-radius:14px; padding:13px 18px;
    background:linear-gradient(150deg,var(--accent-warm),var(--accent-warm-2));
    box-shadow:0 6px 0 var(--accent-warm-deep); transition:transform 80ms ease-out, box-shadow 80ms ease-out, filter 120ms ease-out;
    display:flex; align-items:center; justify-content:center; gap:9px;
  }
  .scm-run:hover{filter:saturate(1.08);}
  .scm-run:active{transform:translateY(6px); box-shadow:0 0 0 var(--accent-warm-deep);}
  .scm-run:focus-visible{outline:2px solid var(--accent-warm); outline-offset:2px;}
```

- [ ] **Step 3: Add a press state to `.scm-chip`**

Current (lines 92-97):

```css
  .scm-chip{
    font-family:var(--mono); font-size:12px; color:var(--muted); cursor:pointer;
    background:var(--ink-2); border:1px solid var(--line-2); border-radius:var(--r-chip);
    padding:6px 12px; transition:all .16s var(--ease);
  }
  .scm-chip:hover{color:var(--accent-bright); border-color:var(--accent-dim); background:rgba(79,209,197,.07); transform:translateY(-1px);}
```

Add immediately after the `:hover` rule:

```css
  .scm-chip:active{transform:translateY(0) scale(.97);}
```

- [ ] **Step 4: Visual check**

Open `/console`. Confirm:
- "Ejecutar ▸" is now a gold/coral pill with a solid bottom ledge shadow.
- Clicking it (mousedown) presses it flush; the spinner state (`isRunning`) still
  renders correctly inside the button (unaffected — only color/shadow/radius changed).
- The example chips ("Reorder points" etc.) still look the same at rest/hover, and
  now visibly compress slightly on click.
- Tab-focusing "Ejecutar ▸" shows a visible gold outline.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/prototype/index.html
git commit -m "feat(webapp): restyle console CTA and chips with gamified bevel"
```

---

### Task 4: Restyle `app.js` action buttons (export / retry)

**Files:**
- Modify: `webapp/static/app.js:332` (export button)
- Modify: `webapp/static/app.js:396` (retry button)

**Interfaces:**
- Consumes: `.btn-warm-action` class from Task 1 (via `index.html`'s link to
  `theme.css`, Task 2).

- [ ] **Step 1: Update the export button**

Current (line 332):

```js
      '<button data-action="export" style="margin-top:2px;border:1px solid var(--accent-bd);background:var(--accent-soft);color:var(--accent-bright);font-size:13px;font-weight:600;padding:10px;border-radius:var(--r-field);cursor:pointer;transition:background .15s">' +
```

Replace with:

```js
      '<button data-action="export" class="btn-warm-action" style="margin-top:2px">' +
```

- [ ] **Step 2: Update the retry button**

Current (line 396):

```js
        '<button data-action="retry" style="margin-top:18px;border:1px solid var(--accent-bd);background:var(--accent-soft);color:var(--accent-bright);font-size:13px;font-weight:600;padding:9px 16px;border-radius:var(--r-field);cursor:pointer">Retry</button></div>');
```

Replace with:

```js
        '<button data-action="retry" class="btn-warm-action" style="margin-top:18px">Retry</button></div>');
```

- [ ] **Step 3: Visual check**

Open `/`, go to the Budget Planner tab, confirm "Commit & export plan (CSV)" renders
as a gold/coral bevel button and the click/press still triggers the existing
`data-action="export"` handler (check the button text still toggles to
"✓ plan exported (CSV)" after clicking — this confirms the JS event wiring, which
reads `data-action`, is untouched).

To see the retry button, temporarily stop the backend (or point `fetch` at a bad URL
in devtools) to force the error state, confirm "Retry" renders with the same gold
bevel and still re-triggers the fetch on click. Restore normal operation after.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/app.js
git commit -m "feat(webapp): restyle export/retry buttons with gamified bevel"
```

---

### Task 5: Restyle `app.js` progress bars (budget gauge + SKU coverage)

**Files:**
- Modify: `webapp/static/app.js:187-188` (budget utilization gauge — single fill)
- Modify: `webapp/static/app.js:295-297` (SKU coverage bar — two-segment fill)

**Interfaces:**
- Consumes: `.bar-track`, `.bar-fill-gloss`, `.bar-fill-safety`, `.bar-fill-shimmer`
  classes from Task 1.

Note: a third `var(--track)` usage exists at `app.js:359` — that element is a bias
*marker* (a fixed track with a positioned dot showing forecast bias), not a
fill-percentage progress bar. It is explicitly **out of scope**: applying a
pill/gloss fill treatment to it wouldn't make sense since it has no fill segment.
Leave it untouched.

- [ ] **Step 1: Update the budget utilization gauge**

Current (lines 187-188):

```js
      '<div style="height:10px;border-radius:6px;background:var(--track);overflow:hidden;position:relative">' +
      '<div style="height:100%;border-radius:6px;width:' + Math.min(100, pct).toFixed(1) + "%;background:" + gColor + ';transition:width .5s cubic-bezier(0.16,1,0.3,1),background .3s"></div></div>' +
```

Replace with:

```js
      '<div class="bar-track" style="height:10px;position:relative">' +
      '<div class="bar-fill-gloss" style="height:100%;width:' + Math.min(100, pct).toFixed(1) + "%;background:" + gColor + ';transition:width .5s cubic-bezier(0.16,1,0.3,1),background .3s"></div></div>' +
```

(`gColor` stays inline and unchanged — this bar's color is semantic (ok/warn/bad
depending on budget headroom), so it keeps its existing dynamic color. Only the pill
shape and gloss shine are new.)

- [ ] **Step 2: Update the SKU coverage bar**

Current (lines 295-297):

```js
        '<div style="height:18px;border-radius:5px;background:var(--track);overflow:hidden;display:flex">' +
        '<div style="height:100%;width:' + cycleW + '%;background:var(--accent-bar);transition:width .55s cubic-bezier(0.16,1,0.3,1)"></div>' +
        '<div style="height:100%;width:' + ssW + '%;background:var(--accent2-bar);transition:width .55s cubic-bezier(0.16,1,0.3,1)"></div></div>' +
```

Replace with:

```js
        '<div class="bar-track" style="height:18px;display:flex">' +
        '<div class="bar-fill-gloss bar-fill-shimmer" style="height:100%;width:' + cycleW + '%;background:var(--accent-bar);transition:width .55s cubic-bezier(0.16,1,0.3,1)"></div>' +
        '<div class="bar-fill-gloss bar-fill-safety" style="height:100%;width:' + ssW + '%;transition:width .55s cubic-bezier(0.16,1,0.3,1)"></div></div>' +
```

(The cycle-stock segment keeps its existing teal `--accent-bar` color inline and gets
the shimmer — it represents the "active" portion. The safety-stock segment drops its
`--accent2-bar` inline color entirely in favor of the `.bar-fill-safety` class, which
is the actual teal→warm color change called for in the design spec, and also fixes
the pre-existing low-contrast-between-segments issue since teal-on-teal is now
teal-on-gold.)

- [ ] **Step 3: Visual check**

Open `/`, Portfolio tab: confirm the top "Budget utilization" bar is now a pill with a
visible glossy highlight band, and its color still switches between teal/amber/red as
you drag the budget slider (semantic color logic unaffected).

Go to the Budget Planner tab: confirm each SKU row's bar is now a pill with two
clearly distinct colors (teal cycle-stock, gold safety-stock) and the teal segment has
a faint diagonal shimmer.

- [ ] **Step 4: Confirm `prefers-reduced-motion` is respected**

In Chrome DevTools, open the Rendering tab → "Emulate CSS media feature
prefers-reduced-motion" → set to "reduce". Reload `/`, go to Budget Planner. Confirm
the shimmer stops (the diagonal stripe pattern disappears, bar becomes a static solid
fill) while the pill shape and colors are unchanged. Set emulation back to "no
preference" afterward.

- [ ] **Step 5: Commit**

```bash
git add webapp/static/app.js
git commit -m "feat(webapp): restyle budget gauge and SKU coverage bars with pill/gloss"
```

---

### Task 6: Restyle `operator/index.html` primary/ghost buttons

**Files:**
- Modify: `webapp/static/operator/index.html:16-19` (add warm tokens to `:root`)
- Modify: `webapp/static/operator/index.html:107-117` (`.btn`/`.btn-primary`/`.btn-ghost`)

**Interfaces:**
- None (this file is fully standalone — no dependency on `theme.css`).

- [ ] **Step 1: Add warm tokens to this page's own `:root`**

Current (lines 16-19):

```css
    --accent:#5eead4; --accent-2:#34d3bd; --accent-deep:#0fb39b;
    --accent-soft:rgba(94,234,212,.12); --accent-bd:rgba(94,234,212,.40);
    --glow:rgba(94,234,212,.22);
    --ok:#3fb950; --warn:#e3b341; --bad:#f0564a; --info:#6ea8fe;
```

Replace with:

```css
    --accent:#5eead4; --accent-2:#34d3bd; --accent-deep:#0fb39b;
    --accent-soft:rgba(94,234,212,.12); --accent-bd:rgba(94,234,212,.40);
    --glow:rgba(94,234,212,.22);
    --accent-warm:#ffb545; --accent-warm-2:#ff8a5c; --accent-warm-deep:#b5601f;
    --ok:#3fb950; --warn:#e3b341; --bad:#f0564a; --info:#6ea8fe;
```

- [ ] **Step 2: Restyle `.btn` / `.btn-primary` / `.btn-ghost`**

Current (lines 107-117):

```css
  .btn{
    display:inline-flex; align-items:center; gap:9px; font-weight:600; font-size:14.5px; padding:12px 22px;
    border-radius:12px; border:1px solid transparent; cursor:pointer; transition:.18s;
  }
  .btn-primary{
    color:#04221d; background:linear-gradient(135deg, var(--accent), var(--accent-deep));
    box-shadow:0 14px 34px -14px var(--glow), inset 0 1px 0 rgba(255,255,255,.25);
  }
  .btn-primary:hover{transform:translateY(-2px); box-shadow:0 20px 44px -14px var(--glow)}
  .btn-ghost{color:var(--txt-2); border-color:var(--line-2); background:rgba(255,255,255,.02)}
  .btn-ghost:hover{border-color:var(--accent-bd); color:var(--txt)}
```

Replace with:

```css
  .btn{
    display:inline-flex; align-items:center; gap:9px; font-weight:600; font-size:14.5px; padding:12px 22px;
    border-radius:14px; border:1px solid transparent; cursor:pointer;
    transition:transform 80ms ease-out, box-shadow 80ms ease-out, filter 120ms ease-out;
  }
  .btn-primary{
    color:#2a1200; background:linear-gradient(135deg, var(--accent-warm), var(--accent-warm-2));
    box-shadow:0 6px 0 var(--accent-warm-deep);
  }
  .btn-primary:hover{filter:saturate(1.08)}
  .btn-primary:active{transform:translateY(6px); box-shadow:0 0 0 var(--accent-warm-deep)}
  .btn-primary:focus-visible{outline:2px solid var(--accent-warm); outline-offset:2px}
  .btn-ghost{color:var(--txt-2); border-color:var(--line-2); background:rgba(255,255,255,.02)}
  .btn-ghost:hover{border-color:var(--accent-bd); color:var(--txt)}
  .btn-ghost:active{transform:translateY(2px)}
```

- [ ] **Step 3: Visual check**

Open `/operator`. Confirm the hero "Empezar →" button is now gold/coral with a bevel
press, "Ver en GitHub" (ghost) is visually unchanged except for a subtle press on
click, and both are keyboard-focusable with a visible gold/teal outline respectively.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/operator/index.html
git commit -m "feat(webapp): restyle operator hero buttons with gamified bevel"
```

---

### Task 7: Full manual verification pass

**Files:** none (verification only)

- [ ] **Step 1: Start the dev server**

`webapp/app.py`'s own header comment documents the exact launch command. Run (from
the repo root, `C:\Users\Gamer\linchpin`): `py -m uvicorn webapp.app:app --reload`

Expected: server starts, no import errors, log line indicating it's listening on
`127.0.0.1:8000` (uvicorn's default port).

- [ ] **Step 2: Re-walk the full spec verification checklist**

Using the Browser pane against `http://localhost:8000`:
1. `/`, `/console`, `/operator` all load with no console errors (check via
   `read_console_messages`).
2. Primary buttons on all three pages show the gold/coral bevel and press correctly
   on mousedown (check via `computer` click + `zoom` screenshot before/after).
3. Secondary/ghost buttons (`.scm-chip`, `.btn-ghost`) are visually unchanged except
   for the new press affordance.
4. The SKU coverage bar shows two distinguishable colors with the gloss/pill
   treatment; the budget gauge bar is a gloss pill with unchanged semantic coloring.
5. `prefers-reduced-motion: reduce` stops the shimmer (re-verify via `resize_window`
   or DevTools emulation as in Task 5 Step 4) with colors/shape intact.
6. Tab-navigate to every restyled button (`read_page` for focus order, `computer` key
   `Tab`) and confirm `:focus-visible` outlines are visible.
7. `resize_window` to ~768px and ~1440px on all three pages — confirm no overflow or
   layout shift introduced by the new shadow offsets (the shadow adds ~4-6px of
   visual footprint below each button; padding/margins were not changed, so no
   layout shift is expected, but confirm visually).

- [ ] **Step 3: Fix any issues found**

If any check fails, fix the specific file/rule (do not add new files or classes not
already defined in Tasks 1-6), re-run the affected verification step, and commit the
fix with `git commit -m "fix(webapp): <specific issue>"`.

---

### Task 8: Code review pass

**Files:** none (review only)

- [ ] **Step 1: Run the code-reviewer agent**

Dispatch the `code-reviewer` agent against the diff on this branch
(`feat/gamified-buttons-ui` vs `main`). Ask it to focus on: CSS specificity
conflicts, accessibility (focus states, reduced-motion), and whether any inline
style/class split introduced inconsistent behavior between the export and retry
buttons (their pre-existing padding differed slightly — `10px` vs `9px 16px` — and
both now share `.btn-warm-action`'s `10px 16px`; confirm this intentional
harmonization doesn't visually break either button's layout).

- [ ] **Step 2: Address findings**

Fix any CRITICAL or HIGH findings inline; note MEDIUM/LOW findings in the PR
description if deferred. Re-commit fixes with `git commit -m "fix(webapp): address
review feedback — <summary>"`.

---

### Task 9: Push branch and open PR

**Files:** none

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/gamified-buttons-ui
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Gamified button/progress-bar restyle" --body "$(cat <<'EOF'
## Summary
- Adds a warm gold/coral "action" accent scoped to primary buttons and progress-bar
  reserve segments (docs/superpowers/specs/2026-07-20-gamified-buttons-design.md)
- 3D ledge-press bevel on every primary CTA across /, /console, /operator
- Pill-shaped, glossy progress bars (budget gauge + SKU coverage), with a
  reduced-motion-gated shimmer on the active segment
- No new gamification content (badges/achievements) — visual/tactile restyle only,
  brand teal and status-chip semantics untouched

## Test plan
- [x] Manual walkthrough of /, /console, /operator per the spec's verification
      checklist (button press states, bar colors/gloss, reduced-motion, focus-visible,
      768px/1440px)
- [x] code-reviewer agent pass, findings addressed
- [ ] Human spot-check in the deployed preview before merge

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Report the PR URL back to the user**

Do not merge and do not deploy — those are separate, explicitly-confirmed actions.
