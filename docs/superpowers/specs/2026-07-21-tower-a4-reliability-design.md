# Tower A4 reliability wiring — design

Status: approved by user, pending spec review sign-off.
Scope: close the standing TODO in `webapp/tower_page.py`'s "Confiabilidad por
tool (A4)" section. Point-in-time only, no new persistence, no scheduled-job
wiring. This is a small, tightly-bounded slice of the broader "SC health
monitor" idea explored in conversation — the SCOR 5-axis radar and any
persisted trend history are explicitly deferred (see Non-goals).

## Why

`webapp/app.py::tower_page()` and `webapp/tower_page.py::render_tower_html()`
both carry a docstring/placeholder saying A4 (`src/verify/backtest.py` +
`src/verify/reliability.py`) "already exist as of PR-8, but nothing here
reads them yet." Both modules are fully implemented and already load-bearing
elsewhere (`scm_agent/autonomy_promotion.py` consumes
`ToolReliabilityReport` for T1/T2 promotion and degradation decisions) — the
Tower's own display of that same evidence was simply never wired. This is
also, independently, the single highest-leverage finding from a 2026-07-21
SCM-KPI gap audit of this repo (see project memory
`kern-scm-kpi-gap-audit-2026-07-21`): none of Kern's inventory/forecast
recommendations are ever checked against what actually happened, and this is
the first concrete step of connecting that check to something visible.

## Non-goals (explicit — do not let scope grow during implementation)

- Do **not** wire `scm_agent.autonomy_promotion.evaluate_tier_transition` /
  `evaluate_promotion` into any scheduled job. That is a separate, larger
  decision (who runs autonomy promotion, on what cadence) outside this
  slice.
- Do **not** persist a history of `ToolReliabilityReport`. The user
  explicitly chose point-in-time-only over a sparkline/trend to avoid a new
  persistence layer for this slice.
- Do **not** add a configurable backtest window. Use the full available
  `history()` (i.e. `window=None`) for both `forecast` and `outcomes`.
- Do **not** implement the `decisions`-domain backtest path
  (`match_decision_actuals`, which needs a per-tool
  `decision_value_column` mapping that doesn't exist yet). Only the
  `forecast`/`outcomes` path (`match_forecast_actuals`), which already has a
  tested convenience precedent (`run_forecast_backtest` — note: NOT reused
  directly, see Architecture).
- Do **not** build the SCOR 5-attribute health radar discussed earlier in
  the conversation. That remains a separate, larger design if picked up
  later.

## Architecture

Everything needed already exists and is a pure function over already-stored
`src.state` snapshots. No new files, no new persistence, no new config.

```
tower_page() [webapp/app.py]
  store = _get_state_store()                                  # existing helper (line ~196)
  reports = _compute_reliability_reports(store)                # NEW small helper, same file
  ...
  render_tower_html(..., reliability_reports=reports)

_compute_reliability_reports(store) -> list[ToolReliabilityReport]:
  forecast_snaps = history("forecast", store=store)            # src/state/system_state.py, window=None (default)
  outcome_snaps  = history("outcomes", store=store)             # same
  observations   = match_forecast_actuals(forecast_snaps, outcome_snaps)  # src/verify/backtest.py
  return build_all_reliability_reports(observations)            # src/verify/reliability.py, default tolerance=0.10/threshold=0.85
```

`_compute_reliability_reports` is added in `webapp/app.py` next to the other
`_get_*`/`_compute_*` helpers (not in `tower_page.py`, which stays a pure
HTML string builder with no I/O — the file's own established convention:
route handlers in `app.py` fetch data, `tower_page.py` only renders
already-fetched plain arguments). It is a plain function (not an
`_get_*`-style ledger getter) so it's trivially unit-testable with a
hand-built `StateStore` and no HTTP layer.

Explicitly **not** `run_forecast_backtest()` (the existing convenience
wrapper in `src/verify/backtest.py`): that function returns
`list[SkuBacktestResult]` (grouped by `product_id`, no `tool` field —
`per_sku_backtest` drops tool grouping entirely), which does not fit
`build_all_reliability_reports`'s required input
(`list[MatchedObservation]`, tool-tagged). The correct call chain wires
`history()` → `match_forecast_actuals()` → `build_all_reliability_reports()`
directly, skipping `run_forecast_backtest`/`per_sku_backtest` altogether.

`store` follows the exact `try/finally: store.close()` pattern already used
at `webapp/app.py:1051-1057` for the scheduled-jobs endpoint's
`run_all_monitors()` call.

## Rendering

`webapp/tower_page.py` gains:

- `_reliability_rows(reports: list[ToolReliabilityReport]) -> str` — a new
  helper matching the exact style of the existing `_t1_rows` / `_t2_rows` /
  `_promotion_rows` (plain string-building, `html.escape` on any text,
  chip spans reusing the page's existing `chip-t1` / `chip-sev-high` CSS
  classes rather than inventing new ones).
- `render_tower_html(...)` gains a new required keyword parameter
  `reliability_reports: list[ToolReliabilityReport]`.
- The existing `<div class="placeholder">TODO (PR-8, ...)</div>` block in
  the "Confiabilidad por tool (A4)" section is replaced by a call to
  `_reliability_rows(reliability_reports)`.

Table columns: **Tool · Decisiones (n) · SKUs (n) · Precision · WAPE · Bias
· Estado**.

All new Spanish UI strings in this file stay accent/tilde-free ASCII
("todavia", "precision", "senal", "automatico", ...), matching this file's
own existing convention throughout (`todavia`, `aprobacion`,
`automaticamente`, `confiabilidad` already appear unaccented in the
rendered HTML today) — do not "correct" these to proper Spanish
orthography during implementation.

### Edge cases (all already resolved by `ToolReliabilityReport` itself —
this is purely about rendering them honestly, never fabricating a number):

1. **No snapshots yet** (`reports == []`): render an empty state, e.g.
   `<p class="empty">Sin evidencia de confiabilidad todavia — corre un
   ciclo con forecast + outcomes para verla aca.</p>`. No table, no "TODO"
   text (the placeholder's TODO language goes away entirely — the feature
   is either honestly empty or honestly populated, never "not implemented").
2. **`headline_precision is None`** (every matched row for that tool has
   `actual == 0`; ratio undefined — see `reliability.py`'s own
   `_hit()` docstring): render the Precision cell as `sin senal` (ASCII —
   see repo-wide console-print convention; this is server-rendered HTML,
   not a console print, but keep the string ASCII-only to match the
   repo's existing text conventions), never `0%` and never a bare dash
   that could read as "zero". The Estado chip for this row is neutral
   (reuse the page's `.muted` styling, not a red/green chip) — failing to
   meet a threshold is not the same claim as "we have no signal to
   evaluate at all."
3. **`meets_threshold` true/false**: green "OK" chip (`chip-t1`'s color) /
   red "Bajo umbral" chip (`chip-sev-high`'s color), matching the page's
   existing severity-chip visual language.

## Testing

### Unit (no HTTP, no FastAPI TestClient)

Direct calls to `_reliability_rows()` and `render_tower_html()` with
hand-built `ToolReliabilityReport` instances:
- empty list -> empty-state text, no table markup.
- one report with `headline_precision=None` -> "sin senal", neutral chip,
  never `0%`.
- one report with `meets_threshold=True` -> green/OK chip.
- one report with `meets_threshold=False` -> red/"Bajo umbral" chip.

### Integration (`tests/test_webapp_tower.py`)

Extend the existing `isolated_ledgers` fixture to also monkeypatch
`appmod.STATE_STORE_PATH` to a `tmp_path`-based file, so these tests never
touch the real (gitignored) `data/state` — same isolation principle the
fixture already applies to `EVENTS_LEDGER_PATH` / `AUTONOMY_LEDGER_PATH` /
`EVENT_ROUTING_PATH`.

- **Replace** `test_tower_page_shows_a4_placeholder_not_fabricated_numbers`
  (currently asserts the literal string `"PR-8"` appears — that text is
  being removed by this change) with
  `test_tower_page_shows_honest_empty_state_when_no_reliability_evidence`:
  no state snapshots written, `GET /tower` shows the new empty-state
  string, no fabricated table, no stale "PR-8"/TODO text.
- **New**:
  `test_tower_page_shows_real_reliability_table_from_state_snapshots`:
  write a `forecast` snapshot (`product_id`, `period`, `forecast_qty`) and
  an `outcomes` snapshot (`product_id`, `tool`, `metric="actual_qty"`,
  `value`, plus the `period` extra column) via
  `src.state.system_state.snapshot()` against the isolated store, then
  `GET /tower` and assert the tool name, a real (non-zero, non-fabricated)
  precision percentage, and the correct OK/Bajo-umbral chip all appear.
- **New**: `test_tower_page_shows_sin_senal_when_precision_unverifiable`:
  seed matched observations where every `outcomes.value == 0` for one
  tool (undefined ratio), assert `"sin senal"` appears and no `0%` does,
  and the chip is neutral rather than red.

## Open questions

None — every decision point in this slice was resolved during
brainstorming (scope, trend-vs-point-in-time, which backtest path). If
implementation surfaces a genuine ambiguity not covered here, treat it as
a signal to stop and ask rather than guess.
