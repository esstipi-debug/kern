# Radiografia del congelado chileno - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a sector radiography of 5 Chilean frozen-food HORECA distributors from their public catalog feeds -- prices normalized to CLP/kg against a 9-source market median, plus each catalog crossed against the SUBPESCA closed-season (veda) calendar -- with zero client data.

**Architecture:** Three additive layers on top of the existing `pricing_intel` engine. (1) Feed readers turn WooCommerce Store API / Shopify `products.json` into `CatalogRecord`s, a new weight normalizer turns titles into CLP/kg, and an es-CL lexicon supplies the decisive attributes that replace the absent GTINs. (2) A hand-curated veda dataset plus a pure calendar module maps SKU titles to regulatory exposure windows. (3) A `market_radiography` job clusters comparable SKUs, computes market medians and per-subject gaps, and composes the sector deliverable. Network I/O lives only in a CLI that writes a normalized CSV; the registered job reads that CSV and is offline and deterministic.

**Tech Stack:** Python 3.11+, `httpx` (sync), `pandas` (jobs only), `PyYAML`, `Decimal` for all money, `pytest`. No new third-party dependency.

## Global Constraints

- **Python 3.11+.** Tests: `pytest tests/ -q` with `PYTHONPATH=.` (pytest config already sets `pythonpath = "."`).
- **Lint scope (matches CI):** `ruff check src tests examples`. `line-length = 120`, `select = ["E","F","I"]`, `ignore = ["E501"]`.
- **ASCII-only in console prints** (Windows cp1252). Markdown/YAML files written utf-8 are fine. No `·`, no em dashes, no accents in `print()`.
- **All money is `decimal.Decimal`, never `float`.** Tests compare `Decimal` with `==` against a `Decimal("...")` literal, never `pytest.approx`. `pytest.approx` is only for `float` similarity scores.
- **Never a module-level import of an optional extra** (`bs4`, `rapidfuzz`, `price-parser`, `python-stdnum`, `extruct`, `openpyxl`) in any module on the app boot chain (`webapp.app` -> `scm_agent` -> `tools` -> `jobs` -> `src`). Use the function-local import pattern from `src/pricing_intel/normalize.py:73-85`. Guarded by `tests/test_pricing_intel_boot_safety.py` whose `_BLOCKED = ("bs4", "price_parser", "rapidfuzz", "stdnum")`.
- **Quarantine over guessing.** An unrecognized format returns `None` and the record is reported as "sin comparar" with a reason. Never default to 1 kg. This mirrors `extract_pack_size`'s existing contract.
- **`config/**` is NOT packaged in the wheel** (no `MANIFEST.in`, no `[tool.setuptools.package-data]`; `[tool.setuptools] packages = ["src"]`). Config is read CWD-relative with an env override, following `src/pricing_intel/acquire/base.py:55-57`.
- **Frozen dataclasses** for every new record type.
- **Workflow:** feature branch -> draft PR -> CI green (py3.11/3.12/3.13) -> squash-merge to `main`. Never push straight to `main`. One PR per section below.
- **Base branch:** `origin/main`. Worktree `C:\Users\Gamer\Music\scm\.wt-radiografia-cl`, current branch `feat/radiografia-congelados-cl` at `282e0ce`.
- **Reports state exposure and documentary obligation, never infraction.** The AMERB exemption exists and compliance is not observable from outside.

---

## Spec deviations (decided, with rationale)

The spec at `docs/superpowers/specs/2026-07-24-radiografia-congelados-chile-design.md` was written before the code was read. Three of its claims do not survive contact with the codebase. Each is resolved below; the resolution is what this plan implements.

### D1. `src/risk_period.py` does NOT model risk windows

Spec line 161-162 says `veda_calendar.py` "se conecta a `src/risk_period.py`, que ya modela ventanas de riesgo". It does not. `src/risk_period.py` is 49 lines of Vandeput demand-over-lead-time math: `demand_over_risk_period(mean_demand_per_period, demand_std_per_period, mean_lead_time, lead_time_std=0.0, review_period=0.0) -> RiskPeriodStats`. Scalar floats in, scalar floats out. No dates, no intervals, no registration API. It has 6 existing callers (`src/policies.py`, `src/cost_optimization.py`, `src/powerbi_export.py`, `jobs/launch_readiness_job.py`, `examples/run_complete.py`).

**Decision:** `src/regulatory/veda_calendar.py` is self-contained with its own window model. `risk_period.py` is not touched. Widening a 6-caller numeric primitive to carry calendar windows is risk with no payoff (YAGNI).

### D2. Cross-site matching is mathematically impossible as specified

`ProductAttributes.__post_init__` (`src/pricing_intel/match/fuzzy.py:67-73`) raises `ValueError` on an empty `brand`. **None of the 9 sites declare a brand** -- the spec itself notes "Cero marca o proveedor declarado en los 96 SKUs. Sin `brand`, sin `gtin`."

Worse, the scoring arithmetic forecloses matching. From `src/pricing_intel/match/probabilistic.py:106-127`:

```
score = 0.55 * title_sim + 0.15 * brand_sim + 0.30 * attr_sim
CONFIRM_THRESHOLD = 0.96
```

If `brand` is set to each site's own name, `brand_sim = 0` across sites, so the maximum achievable score is `0.55 + 0.30 = 0.85 < 0.96`. Every cross-site pair lands `suspect`, goes to `adjudicate_pair`, and with `llm=None` returns `verdict="deferred"`. **Nothing would ever confirm and the matrix would come out empty.**

**Decision:** every `CatalogRecord` from every source carries the same brand sentinel, `NO_BRAND_DECLARED = "sin marca declarada"`. `brand_sim` then contributes a uniform `+0.15` to every pair and all discrimination comes from title (0.55) and attributes (0.30). This is the same "no evidence either way" discipline `_attribute_similarity` already applies (`probabilistic.py:150-162` returns a neutral `1.0` when no decisive key is shared) -- not a new invention, and not a number massaged upward, because it moves every pair identically and therefore changes no ranking. Task 5 pins it with a test asserting the sentinel is byte-identical across sources.

### D3. Feeding a refs CSV to `price_intelligence` re-fetches every PDP

Spec line 190 routes the scan through `price_intelligence`. But `jobs/price_intelligence.py::run` does its own acquisition (`_acquire_one`, line 210) via `acquire_l1_offer`, which calls `require_approved_site` and needs a committed `config/sites/<domain>.yaml` per domain. Feeding it ~700 refs across 9 domains means ~700 live PDP fetches to re-learn prices the feed already gave us in 9 requests, plus 9 site-config approvals. There is also **no reusable matrix module** -- the matrix math lives inside the job, coupled to acquisition (`src/pricing_intel/` has `discover, events, extract, homologate, ledger, match, models, normalize, sanity, watch_policy` and no matrix).

**Decision:** PR-3 computes clusters, medians and gaps itself in a new pure module `src/market_radiography.py` from the already-acquired `CatalogRecord`s. The refs CSV is still emitted by PR-1 as an optional by-product, for the fase-2 `price_watch` subscription -- not as the radiography's own input. This also honors the spec's own framing: "Esta es una fotografia, no una suscripcion" (line 242).

### D4. `DECISIVE_ATTRIBUTE_KEYS` does not contain the es-CL attributes (additive fix)

`DECISIVE_ATTRIBUTE_KEYS = ("pack_size", "size", "model", "capacity", "color")` (`probabilistic.py:103`). The lexicon's `especie` / `calibre` / `estado` / `proceso` are absent, so they would contribute nothing to `attr_sim`. Task 5 extends the tuple. The change is safe-by-construction: `_attribute_similarity` only compares keys **present on both sides**, and no existing offer carries these keys, so every existing path scores identically.

### D5. Catalog-feed reads need their own tier classification -- gated the same as L1, via new per-domain site configs

`ACQUISITION_TIERS = ("L0","L1","L2","L3")` (`src/pricing_intel/models.py:74`) classifies acquisition METHODS already in use: L0 official API (`meli_api.py`), L1 structured-data PDP extraction (`pdp_fetcher.fetch_pdp_html` + `l1.py::acquire_l1_offer`), L2 a third-party webhook watcher, L3 a dedicated spider crawl. None of the four is "bulk catalog/listing JSON endpoint" -- a WooCommerce Store API page or a Shopify `/products.json` page is a new fifth shape the code has never classified. The spec commits to routing catalog feeds "through the existing tiering logic, no L1-ceiling bypass invented" (design.md:88-89) without saying how a shape that doesn't exist yet should be classified, and confirmed by direct grep: **none of the 9 radiografia domains has a `config/sites/*.yaml` yet** (only 5 files exist repo-wide, all either the real, `prohibited` `api.mercadolibre.com` or synthetic `.test`-TLD fixtures).

**Decision:** every catalog-feed read is gated exactly like an L1 read. `catalog_feeds.py`'s two readers call `base.require_approved_site(domain, config_dir=...)` internally -- never let a caller skip the gate -- and refuse with `CatalogFeedError` if `ACQUISITION_TIERS.index("L1") > ACQUISITION_TIERS.index(config.max_tier_allowed)`, mirroring `l1.py:157-161`'s own ceiling check verbatim. Each of the 9 domains needs a **hand-authored** `config/sites/<domain>.yaml` -- never `auto_approve_site` (`acquire/auto_approve.py`), which exists for a domain `price_watch` discovers live and has never seen before. These 9 were manually researched in a prior session (robots.txt already read, licensing table already resolved in the spec's "Restricciones legales" section) -- hand-authoring is the honest provenance, the same shape `config/sites/example-retailer.test.yaml` demonstrates for a human-reviewed domain. `tos_decision` is `"limited"`, **never** `"allowed"` -- nobody has done a legal ToS review, only a robots.txt read, the same discipline `auto_approve.py` enforces for its own auto-onboarded configs (`AUTO_TOS_DECISION = "limited"`, never raised automatically). Unit tests never depend on the real 9 configs -- they use synthetic `.test`-TLD fixture domains under a test-local `config_dir`, the same convention `example-retailer.test.yaml` / `discovered-retailer.test.yaml` already establish. Authoring the real 9 files against the real domains is an operator step at the top of Task 6 (network-dependent, cannot be scripted into a plan document); Tasks 3-5 are fully offline against fixtures.

### D6. JSON-LD reader (IceFish, Mardeli) is out of scope for PR-1

The spec lists 9 sources in its Alcance section but only designs two readers, Woo + Shopify (design.md:72-84, "[1] Lectores de catalogo"). IceFish is Jumpseller/JSON-LD and Mardeli is JSON-LD on an undetermined platform -- neither fits either designed reader. All **5 required sujetos** are Woo (Rapa-Nui, Pulpo Chile, TodoFrio, Del Mar A Tu Hogar) or Shopify (Mariscos De La Concha) -- zero sujetos need a JSON-LD reader. Of the 4 reference-only sources (mass for the market median, no report of their own), 2 are Shopify (Frozt, South Wind) and 2 are JSON-LD (IceFish, Mardeli).

**Decision:** PR-1 ships only the two readers the spec actually designed. IceFish and Mardeli are dropped from v1's reference set. 7 of 9 sources is still enough mass for a market median, and every sujeto's own report is unaffected -- this is the same "cobertura, no adivinar" discipline applied to source selection instead of a single SKU. A third `read_json_ld_products` reader is a clean, additive follow-up (same `CatalogRecord` return shape) whenever the reference set needs to widen; nothing here blocks on it.

### D7. N-way peer clustering does not call `homologate.homologate()`

`homologate.homologate(discovered: Sequence[DiscoveredProduct], our_catalog: Sequence[ProductAttributes], ...)` (`src/pricing_intel/homologate.py:280-287`) is shaped for a **bilateral** "our catalog vs. one discovered competitor" flow -- the same shape `price_watch` uses, and it takes two *different* types on its two sides (`DiscoveredProduct` vs `ProductAttributes`). The radiografia has no privileged "our" side: it is 9 equal peer sources that need pairwise comparison across all `C(9,2) = 36` source pairs, unioned into N-way clusters. Forcing 9 peers through a bilateral, differently-typed API would mean inventing a fake "our" side five separate times (once per sujeto) and manually deduping the resulting pairwise verdicts back into clusters -- more code, not less, and it would still not natively produce one N-way cluster.

**Decision:** `market_radiography.cluster_offers` calls the lower-level cascade directly, once per source pair: `fuzzy.block_candidates` (blocking) -> `probabilistic.score_pair` (scoring) -> `adjudicate.adjudicate_pair` only when the score lands inside `ADJUDICATION_BAND = (0.5, 0.85)`, confirming immediately above the band without spending an adjudication call. Confirmed pairs are merged with a plain union-find local to `market_radiography.py`. This still reuses every piece of `match/` completely unmodified (D3's own promise); it only supplies new orchestration logic that the two-sided `homologate()` cannot express for a peer scenario. Same-source pairs are skipped unconditionally (two SKUs from Rapa-Nui's own catalog are never "the same offer" for this comparison, no matter how similar their titles are).

---

## File Structure

**New files**

| Path | Responsibility |
|---|---|
| `src/pricing_intel/acquire/catalog_feeds.py` | `CatalogRecord` + the two feed readers. The only new module doing network I/O. |
| `src/pricing_intel/lexicon.py` | es-CL lexicon loader, `attributes_from_title`, the `ProductAttributes` bridge, the two-signal gate. Pure except for one YAML read. |
| `config/lexicon/es_cl_congelados.yaml` | Versioned es-CL vocabulary: especie, calibre, estado, proceso, origen. |
| `src/regulatory/__init__.py` | Package marker. |
| `src/regulatory/veda_calendar.py` | `VedaWindow`, `VedaExposure`, loader with staleness check, exposure computation. Pure except one YAML read. |
| `config/regulatory/cl_vedas.yaml` | Hand-curated decree dataset, one row per veda, each with `verificado_el`. |
| `src/market_radiography.py` | Pure cluster / median / gap math. No I/O, no pandas. |
| `jobs/market_radiography_job.py` | `prepare` / `run` / `verify` / `write_operational` / `build_deck`. Reads its own CSV with pandas. Offline. |
| `examples/run_cl_catalog_scan.py` | Network CLI: 9 feeds -> normalized CSV + refs CSV + coverage report. |
| `examples/run_market_radiography.py` | Offline CLI: normalized CSV + veda calendar -> 5 subject reports + 1 sector benchmark. |
| `config/sites/<domain>.yaml` (x9, hand-authored, Task 6) | Per-domain acquisition approval (D5) -- `tos_decision: limited`, `max_tier_allowed: L1`. Real domains, filled in at execution time; not fabricated in this plan. |

**Modified files**

| Path | Change |
|---|---|
| `src/pricing_intel/normalize.py` | Add `WeightFormat`, `extract_weight_format`, `price_per_kilogram`, `VolumetricNotConvertible`. (Tasks 1-2, done.) |
| `src/pricing_intel/match/probabilistic.py:103` | Extend `DECISIVE_ATTRIBUTE_KEYS` with the four es-CL keys (D4). |
| `scm_agent/tools.py` | Import block (insert alphabetically between `markdown_liquidation_job` and `multi_echelon_job`), `_market_radiography_prepare/_run`, `market_radiography_tool()`, one `reg.register(...)` (placed near `price_intelligence_tool()`/`price_watch_tool()` -- this codebase's registration order is thematic, not alphabetical). |
| `scm_agent/citation_gate.py:54` | One `TOOL_CONCEPTS` entry: `("competition_oriented_pricing", "price_positioning", "regulated_supply_chain_environment")` -- BFS-verified 2026-07-24, 54-node closure, zero magnets, no `EXCLUDED_CONCEPTS` entry needed. |
| `src/i18n.py` | One `TOOL_TITLES` es/en pair, inserted alphabetically. |
| `tests/test_scm_agent.py:123` | Add `"market_radiography"` to the `assert keys == {...}` set literal. |
| `tests/test_price_watch_tool.py:165` | Bump `46` -> `47` inside `test_registry_now_has_45_tools` (the function name is already one bump stale from the `disruption_scan` merge -- leave it, matching established practice, don't rename it in this PR). |

**Line-number caveat:** every `scm_agent/tools.py` / `src/i18n.py` line number above was verified directly against the file on 2026-07-24 (`disruption_scan`'s own entries: import at line 19, `reg.register(disruption_scan_tool())` at line 2546, `i18n.py` entry at lines 223-224 -- **not** the 32 / 2571 / 260 a prior handoff note guessed by extrapolation, which actually point at `launch_readiness`'s entries one tool later). `grep`/re-read the anchor before editing regardless -- PR-1 and PR-2 don't touch this file, but confirm nothing else landed on `main` first.

**New test files:** `tests/test_normalize_weight_format.py` (done), `tests/test_catalog_feeds.py`, `tests/test_es_cl_lexicon.py`, `tests/test_veda_calendar.py`, `tests/test_market_radiography.py`, `tests/test_market_radiography_job.py`, `tests/test_market_radiography_tool.py`. Fixtures under `tests/fixtures/cl_catalog/` (feed JSON) and `tests/fixtures/cl_catalog/sites/` (synthetic `.test`-TLD site configs, so unit tests never depend on the real 9 hand-authored configs -- D5).

**Deliberately NOT touched:** `webapp/how_it_works_data.py` (frozen at 41 by three tests, not registry-synced -- already 5 tools stale as of this writing, that's established practice not a new gap), `webapp/mcp_tool_specs.py` (MCP exposure deferred -- same call `launch_readiness` made), `src/risk_period.py` (D1), `jobs/price_intelligence.py` (D3), `homologate.py` (D7 -- reused only for its `ProductAttributes`-adjacent primitives, not its own orchestrator), `CLAUDE.md`/`README.md` tool counts (already stale by one from `disruption_scan`; not part of this codebase's actual enforced convention despite what an idealized checklist would say).

---

# PR-1: feed readers + CLP/kg normalizer + es-CL lexicon

**Branch:** `feat/radiografia-cl-catalog-scan` off `origin/main`.

**Deliverable:** `python examples/run_cl_catalog_scan.py --config config/lexicon/es_cl_congelados.yaml --out data/cl_scan/` reads the 9 feeds and writes a normalized CSV with a CLP/kg column, a refs CSV, and prints a coverage line.

- [ ] **Task 0: Branch setup**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git fetch origin
git checkout -b feat/radiografia-cl-catalog-scan origin/main
```

Expected: `Switched to a new branch 'feat/radiografia-cl-catalog-scan'`. Confirm the spec is reachable -- it lives on `feat/radiografia-congelados-cl`, not on this branch, so read it from the other branch if needed:

```bash
git show feat/radiografia-congelados-cl:docs/superpowers/specs/2026-07-24-radiografia-congelados-chile-design.md | head -60
```

---

### Task 1: `extract_weight_format` -- the single point of failure

This is the highest-risk component in the whole design. A misread format inverts a conclusion: TodoFrio's reineta at $10.090 looks cheaper than Rapa-Nui's at $11.768, but it is a 500 g pack, i.e. $20.180/kg. Write the test table first and make it exhaustive.

**Files:**
- Modify: `src/pricing_intel/normalize.py` (append after `extract_pack_size`, which ends at line 248)
- Test: `tests/test_normalize_weight_format.py` (create)

**Interfaces:**
- Consumes: nothing (leaf function).
- Produces: `WeightFormat(quantity: Decimal, unit: Literal["kg","g","lt","cc"])`, `extract_weight_format(text: str) -> WeightFormat | None`. Task 2 consumes both. Task 3 calls `extract_weight_format` on `CatalogRecord.raw_format_text`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_normalize_weight_format.py`:

```python
"""Weight-format extraction from es-CL frozen-food product titles.

The titles are real, captured 2026-07-24 from the nine public catalog feeds
in the radiografia scope. This is the highest-risk unit in the design: a
misread format inverts a price conclusion (a 500 g reineta at $10.090 is
$20.180/kg, not "cheaper than" a 1 kg one at $11.768), so the table below is
deliberately exhaustive and every unrecognized shape must return None rather
than a guessed 1 kg.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.pricing_intel.normalize import WeightFormat, extract_weight_format


@pytest.mark.parametrize(
    "text,quantity,unit",
    [
        # bare kg, three casings
        ("Camaron 36/40 crudo pelado 1kg", Decimal("1"), "kg"),
        ("Camaron 36/40 crudo pelado 1 kg", Decimal("1"), "kg"),
        ("Camaron 36/40 crudo pelado 1Kg", Decimal("1"), "kg"),
        # grams, abbreviated and spelled
        ("Filete de reineta 500g", Decimal("500"), "g"),
        ("Filete de reineta 500 grs", Decimal("500"), "g"),
        ("Filete de reineta 500 gramos", Decimal("500"), "g"),
        ("Filete de reineta 800 gr", Decimal("800"), "g"),
        # decimal separators: es-CL comma AND dot
        ("Pulpo entero 2,5kg", Decimal("2.5"), "kg"),
        ("Pulpo entero 2.5 kg", Decimal("2.5"), "kg"),
        ("Salmon porcionado 1.36 kilos", Decimal("1.36"), "kg"),
        ("Salmon porcionado 1,36 kilo", Decimal("1.36"), "kg"),
        # container prefix does not shadow the weight
        ("Chorito bolsa 1kg", Decimal("1"), "kg"),
        ("Merluza austral caja 10kg", Decimal("10"), "kg"),
        # volumetric: recognized as a format, NOT convertible to mass
        ("Locos al natural 1lt", Decimal("1"), "lt"),
        ("Pulpo en conserva 1 litro", Decimal("1"), "lt"),
        ("Salsa de ostion 500 cc", Decimal("500"), "cc"),
        ("Salsa de ostion 500ml", Decimal("500"), "cc"),
    ],
)
def test_extract_weight_format_recognizes_real_titles(text: str, quantity: Decimal, unit: str) -> None:
    result = extract_weight_format(text)
    assert result == WeightFormat(quantity=quantity, unit=unit)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Camaron 36/40 crudo pelado",          # calibre is NOT a weight
        "Camaron 21/25 cocido",                # nor is this one
        "Reineta filete",                      # no format at all
        "Surtido de mariscos",                 # no format at all
        "Congrio dorado 1000",                 # a number with no unit is not a weight
        "Merluza x 12",                        # a count is not a weight
        "Ostion del norte calibre 100/200",    # explicit calibre wording
    ],
)
def test_extract_weight_format_returns_none_rather_than_guessing(text: str) -> None:
    assert extract_weight_format(text) is None


def test_calibre_is_never_read_as_a_weight_even_next_to_one() -> None:
    # The calibre appears BEFORE the weight in the real Rapa-Nui titles; the
    # weight must still win, and the calibre digits must never leak in.
    assert extract_weight_format("Camaron 36/40 crudo pelado 1kg") == WeightFormat(Decimal("1"), "kg")
    assert extract_weight_format("Ostion 100/200 IQF 500 grs") == WeightFormat(Decimal("500"), "g")


def test_first_weight_wins_when_a_case_states_both_case_and_unit_size() -> None:
    # "caja 10kg (10 x 1kg)": the PRICE is for the case, so the case weight is
    # the correct denominator. Leftmost match wins by construction.
    assert extract_weight_format("Camaron caja 10kg (10 x 1kg)") == WeightFormat(Decimal("10"), "kg")


def test_weight_format_is_frozen() -> None:
    fmt = WeightFormat(Decimal("1"), "kg")
    with pytest.raises(Exception):
        fmt.quantity = Decimal("2")  # type: ignore[misc]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_normalize_weight_format.py -q
```

Expected: collection error, `ImportError: cannot import name 'WeightFormat' from 'src.pricing_intel.normalize'`.

- [ ] **Step 3: Write the implementation**

Append to `src/pricing_intel/normalize.py`, immediately after `extract_pack_size` (after line 248) and before `unit_price`:

```python
# --- Weight / volume format, es-CL frozen-food titles ------------------------
# Sibling of extract_pack_size: that one answers "how many units", this one
# answers "how much mass or volume", which is what a CLP/kg comparison needs.
# Order inside the alternation matters: the longest unit spellings come first
# so "kilos" is not consumed as "kilo" + stray "s". A bare number with no unit
# never matches -- a calibre like "36/40" and a count like "x 12" must not be
# mistaken for a weight.
_WEIGHT_FORMAT_RE = re.compile(
    r"""
    (?<![\d/])                      # not preceded by a digit or a slash (blocks "36/40")
    (\d{1,4}(?:[.,]\d{1,3})?)       # 1) quantity, es-CL comma or dot decimal
    \s*
    (                               # 2) unit
        kilogramos | kilogramo | kilos | kilo | kgs | kg
      | gramos | gramo | grs | gr | g
      | litros | litro | lts | lt | l
      | cc | ml
    )
    (?![a-z])                       # unit must not be a prefix of a longer word
    """,
    re.IGNORECASE | re.VERBOSE,
)

_WEIGHT_UNIT_CANON: dict[str, str] = {
    "kilogramos": "kg", "kilogramo": "kg", "kilos": "kg", "kilo": "kg", "kgs": "kg", "kg": "kg",
    "gramos": "g", "gramo": "g", "grs": "g", "gr": "g", "g": "g",
    "litros": "lt", "litro": "lt", "lts": "lt", "lt": "lt", "l": "lt",
    "cc": "cc", "ml": "cc",
}


@dataclass(frozen=True)
class WeightFormat:
    """A recognized mass or volume statement from a product title.

    ``unit`` is canonical: "kg" | "g" | "lt" | "cc". Volume units are kept
    (they are real, published formats) but are NOT mass -- see
    ``price_per_kilogram``, which refuses to convert them.
    """

    quantity: Decimal
    unit: Literal["kg", "g", "lt", "cc"]


def extract_weight_format(text: str) -> WeightFormat | None:
    """Best-effort mass/volume detection from an es-CL product title.

    Returns ``None`` -- never a guessed default of 1 kg -- when no recognized
    format is present, so the caller quarantines the SKU as "sin comparar"
    instead of publishing an invented CLP/kg. Same contract as
    ``extract_pack_size``.

    The leftmost match wins. For a case that states both its case weight and
    its unit weight ("caja 10kg (10 x 1kg)") that is the correct choice: the
    published price is the case price, so the case weight is the denominator.
    """
    if not text:
        return None
    match = _WEIGHT_FORMAT_RE.search(text)
    if not match:
        return None
    raw_quantity, raw_unit = match.group(1), match.group(2)
    try:
        quantity = Decimal(raw_quantity.replace(",", "."))
    except InvalidOperation:  # pragma: no cover -- the regex only admits parseable numbers
        return None
    if quantity <= 0:
        return None
    unit = _WEIGHT_UNIT_CANON[raw_unit.lower()]
    return WeightFormat(quantity=quantity, unit=unit)  # type: ignore[arg-type]
```

Extend the imports at the top of `normalize.py`: the module already imports `re`, `Decimal` and `dataclass`. Add `InvalidOperation` to the existing `decimal` import and `Literal` to the `typing` import:

```python
from decimal import Decimal, InvalidOperation
from typing import Literal
```

If `normalize.py` has no `typing` import yet, add the line after the `decimal` import.

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_normalize_weight_format.py -q
```

Expected: `31 passed`.

- [ ] **Step 5: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/test_pricing_intel_normalize.py tests/test_pricing_intel_boot_safety.py -q
ruff check src tests examples
```

Expected: all pass, `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add src/pricing_intel/normalize.py tests/test_normalize_weight_format.py
git commit -m "feat(pricing-intel): extract mass/volume format from es-CL titles

The CLP/kg comparison needs a mass denominator that no feed publishes as a
field. extract_weight_format reads it from the title, returning None instead
of a guessed 1kg so an unreadable format is quarantined rather than inverted
into a wrong conclusion. Table-driven against real titles from the nine
catalog feeds, including the calibre-is-not-a-weight cases."
```

---

### Task 2: `price_per_kilogram` -- and the refusal to convert volume

**Files:**
- Modify: `src/pricing_intel/normalize.py` (append after `extract_weight_format`)
- Test: `tests/test_normalize_weight_format.py` (append)

**Interfaces:**
- Consumes: `WeightFormat` from Task 1.
- Produces: `price_per_kilogram(price: Decimal, fmt: WeightFormat) -> Decimal`, raising `VolumetricNotConvertible` for `lt`/`cc`. Task 3 and Task 10 consume it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_normalize_weight_format.py`:

```python
from src.pricing_intel.normalize import VolumetricNotConvertible, price_per_kilogram


def test_price_per_kilogram_the_conclusion_inverting_case() -> None:
    # The case that motivates this whole module. Nominally TodoFrio is cheaper.
    todofrio = price_per_kilogram(Decimal("10090"), WeightFormat(Decimal("500"), "g"))
    rapanui = price_per_kilogram(Decimal("11768"), WeightFormat(Decimal("1"), "kg"))
    assert todofrio == Decimal("20180")
    assert rapanui == Decimal("11768")
    assert todofrio > rapanui  # per kilo, TodoFrio is 71% MORE expensive


@pytest.mark.parametrize(
    "price,quantity,unit,expected",
    [
        (Decimal("11768"), Decimal("1"), "kg", Decimal("11768")),
        (Decimal("10090"), Decimal("500"), "g", Decimal("20180")),
        (Decimal("1000"), Decimal("2.5"), "kg", Decimal("400")),
        (Decimal("89900"), Decimal("10"), "kg", Decimal("8990")),
        (Decimal("8990"), Decimal("800"), "g", Decimal("11237.5")),
    ],
)
def test_price_per_kilogram_converts_mass_formats(
    price: Decimal, quantity: Decimal, unit: str, expected: Decimal
) -> None:
    assert price_per_kilogram(price, WeightFormat(quantity, unit)) == expected


@pytest.mark.parametrize("unit", ["lt", "cc"])
def test_price_per_kilogram_refuses_volume(unit: str) -> None:
    # "Locos al natural 1lt" has no honest conversion to CLP/kg. Refuse loudly
    # so the caller clusters it separately instead of comparing it to mass.
    with pytest.raises(VolumetricNotConvertible) as exc:
        price_per_kilogram(Decimal("9990"), WeightFormat(Decimal("1"), unit))
    assert unit in str(exc.value)


def test_price_per_kilogram_rejects_nonpositive_price() -> None:
    with pytest.raises(ValueError):
        price_per_kilogram(Decimal("0"), WeightFormat(Decimal("1"), "kg"))


def test_volumetric_not_convertible_is_a_value_error() -> None:
    # Callers that only care about "could not compare" catch ValueError.
    assert issubclass(VolumetricNotConvertible, ValueError)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_normalize_weight_format.py -q
```

Expected: `ImportError: cannot import name 'VolumetricNotConvertible'`.

- [ ] **Step 3: Write the implementation**

Append to `src/pricing_intel/normalize.py`, after `extract_weight_format`:

```python
_GRAMS_PER_KILOGRAM = Decimal("1000")

_VOLUMETRIC_UNITS: frozenset[str] = frozenset({"lt", "cc"})


class VolumetricNotConvertible(ValueError):
    """Raised when a CLP/kg conversion is asked of a volume format.

    A conserve sold as "Locos al natural 1lt" has no honest mass equivalent
    without a declared density nobody publishes. Volume SKUs are compared only
    against other SKUs of the same volumetric format, in their own cluster.
    """


def price_per_kilogram(price: Decimal, fmt: WeightFormat) -> Decimal:
    """Convert a published price and a recognized mass format into CLP/kg.

    Raises ``VolumetricNotConvertible`` for "lt"/"cc" and ``ValueError`` for a
    non-positive price. The quotient is returned unquantized: rounding is a
    presentation decision and belongs to the report layer, not here.
    """
    if price <= 0:
        raise ValueError(f"price must be positive to unit-price it, got {price}")
    if fmt.unit in _VOLUMETRIC_UNITS:
        raise VolumetricNotConvertible(
            f"format unit {fmt.unit!r} is volume, not mass; there is no honest CLP/kg "
            "conversion without a declared density -- compare within the volumetric cluster"
        )
    kilograms = fmt.quantity if fmt.unit == "kg" else fmt.quantity / _GRAMS_PER_KILOGRAM
    return price / kilograms
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_normalize_weight_format.py -q
```

Expected: `43 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/pricing_intel/normalize.py tests/test_normalize_weight_format.py
git commit -m "feat(pricing-intel): price_per_kilogram, refusing volume formats

Mass formats convert; lt/cc raise VolumetricNotConvertible so a conserve is
never silently compared against a frozen fillet. The quotient stays
unquantized -- rounding belongs to the report layer."
```

---

### Task 3: `CatalogRecord` + `read_woo_store_api` -- the first network reader, gated

**Files:**
- Create: `src/pricing_intel/acquire/catalog_feeds.py`
- Create: `tests/test_catalog_feeds.py`
- Create fixtures: `tests/fixtures/cl_catalog/woo_page1.json`, `tests/fixtures/cl_catalog/woo_page2.json`
- Create fixture config: `tests/fixtures/cl_catalog/sites/rapa-nui-catalog.test.yaml` (approved), `tests/fixtures/cl_catalog/sites/tier-too-low.test.yaml` (approved but `max_tier_allowed: L0`)

**Interfaces:**
- Consumes: `src.pricing_intel.acquire.base` (`require_approved_site`, `normalize_domain`, `DEFAULT_SITES_CONFIG_DIR`, `SiteNotConfiguredError`, `SiteNotApprovedError`) and `src.pricing_intel.models.ACQUISITION_TIERS`.
- Produces: `CatalogRecord` (frozen dataclass), `CatalogFeedError`, `read_woo_store_api(base_url, *, client, config_dir=...) -> tuple[CatalogRecord, ...]`. Task 4 reuses `CatalogRecord`/`CatalogFeedError`. Task 5 consumes `CatalogRecord.title`/`raw_format_text` for attribute extraction. Task 6's CLI is the only caller with a real `httpx.Client`.

Per D5, gating lives **inside** the reader, not in the caller -- a script cannot accidentally call `read_woo_store_api` against an unapproved domain, the same safe-by-construction shape `l1.py::acquire_l1_offer` already uses.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/cl_catalog/sites/rapa-nui-catalog.test.yaml`:

```yaml
domain: rapa-nui-catalog.test
robots_txt_respected: true
robots_checked_at: "2026-07-24"
tos_summary: >
  Synthetic fixture for the radiografia catalog-scan test suite. Mirrors the
  real approval shape a hand-authored production config carries (D5): robots.txt
  read, no legal ToS review performed, so tos_decision stays LIMITED.
tos_decision: limited
rate_limit_seconds: 3.0
max_tier_allowed: L1
pii_policy: none
```

Create `tests/fixtures/cl_catalog/sites/tier-too-low.test.yaml`:

```yaml
domain: tier-too-low.test
robots_txt_respected: true
robots_checked_at: "2026-07-24"
tos_summary: "Synthetic fixture: approved, but only up to L0 -- a catalog feed read (L1) must be refused."
tos_decision: limited
rate_limit_seconds: 3.0
max_tier_allowed: L0
pii_policy: none
```

Create `tests/fixtures/cl_catalog/woo_page1.json` (real captured Rapa-Nui SKUs and prices from the 2026-07-24 research pass; note the inconsistent SKU casing, `MAR1401` vs `Mar1004`, is itself a real, previously-verified gotcha -- not a typo to fix):

```json
[
  {
    "id": 1401,
    "name": "Filete de Reineta 1kg",
    "slug": "filete-de-reineta-1kg",
    "permalink": "https://rapa-nui-catalog.test/producto/filete-de-reineta-1kg/",
    "sku": "MAR1401",
    "prices": {
      "price": "11768",
      "regular_price": "11768",
      "currency_code": "CLP",
      "currency_minor_unit": 0
    },
    "is_in_stock": true
  },
  {
    "id": 1004,
    "name": "Camaron 36/40 crudo pelado 1kg",
    "slug": "camaron-36-40-crudo-pelado",
    "permalink": "https://rapa-nui-catalog.test/producto/camaron-36-40-crudo-pelado/",
    "sku": "Mar1004",
    "prices": {
      "price": "15990",
      "regular_price": "15990",
      "currency_code": "CLP",
      "currency_minor_unit": 0
    },
    "is_in_stock": true
  }
]
```

Create `tests/fixtures/cl_catalog/woo_page2.json` (page 2 of 2; item 4 is deliberately malformed -- no `prices` block at all, the Woo-side analog of Jumbo's `"price": "undefined"` soft-404 gotcha -- and must be dropped, not crash the run):

```json
[
  {
    "id": 1903,
    "name": "Congrio Dorado Filete 800g",
    "slug": "congrio-dorado-filete",
    "permalink": "https://rapa-nui-catalog.test/producto/congrio-dorado-filete/",
    "sku": "pes1903",
    "prices": {
      "price": "12500",
      "regular_price": "12500",
      "currency_code": "CLP",
      "currency_minor_unit": 0
    },
    "is_in_stock": false
  },
  {
    "id": 9999,
    "name": "Producto sin precio publicado",
    "slug": "producto-sin-precio",
    "permalink": "https://rapa-nui-catalog.test/producto/producto-sin-precio/",
    "sku": "SIN-PRECIO",
    "is_in_stock": true
  }
]
```

Create `tests/test_catalog_feeds.py`:

```python
"""Catalog feed readers -- WooCommerce Store API and (Task 4) Shopify products.json.

Every test is offline: httpx.MockTransport stands in for the network, and the
site-config gate (D5) is pointed at a fixture config_dir so these tests never
touch the real config/sites/ directory. No fixture domain here resolves on a
real network -- ".test" is a reserved TLD (RFC 2606) chosen for exactly this.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from src.pricing_intel.acquire import base
from src.pricing_intel.acquire.catalog_feeds import CatalogFeedError, CatalogRecord, read_woo_store_api

FIXTURES = Path(__file__).parent / "fixtures" / "cl_catalog"
SITES_CONFIG_DIR = FIXTURES / "sites"

_PAGE1 = (FIXTURES / "woo_page1.json").read_text(encoding="utf-8")
_PAGE2 = (FIXTURES / "woo_page2.json").read_text(encoding="utf-8")


def _woo_handler(request: httpx.Request) -> httpx.Response:
    page = request.url.params.get("page", "1")
    if page == "1":
        return httpx.Response(200, content=_PAGE1, headers={"X-WP-TotalPages": "2"})
    if page == "2":
        return httpx.Response(200, content=_PAGE2, headers={"X-WP-TotalPages": "2"})
    return httpx.Response(200, content="[]", headers={"X-WP-TotalPages": "2"})


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_read_woo_store_api_paginates_and_returns_catalog_records() -> None:
    client = _client(_woo_handler)
    records = read_woo_store_api(
        "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    # 4 raw items across 2 pages, 1 malformed (no prices block) -> 3 records.
    assert len(records) == 3
    assert all(isinstance(r, CatalogRecord) for r in records)


def test_read_woo_store_api_maps_fields_correctly() -> None:
    client = _client(_woo_handler)
    records = read_woo_store_api(
        "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    reineta = next(r for r in records if r.sku == "MAR1401")
    assert reineta.title == "Filete de Reineta 1kg"
    assert reineta.price == Decimal("11768")
    assert reineta.currency == "CLP"
    assert reineta.in_stock is True
    assert reineta.source == "rapa-nui-catalog.test"
    assert reineta.raw_format_text == reineta.title  # never the slug (D6's sibling gotcha, Frozt)


def test_read_woo_store_api_preserves_inconsistent_sku_casing() -> None:
    # MAR1401 vs Mar1004 vs pes1903 is a real, previously-verified gotcha in
    # Rapa-Nui's own catalog -- the reader must not "fix" it by upper/lowercasing.
    client = _client(_woo_handler)
    records = read_woo_store_api(
        "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    skus = {r.sku for r in records}
    assert skus == {"MAR1401", "Mar1004", "pes1903"}


def test_read_woo_store_api_drops_items_with_no_price_block() -> None:
    client = _client(_woo_handler)
    records = read_woo_store_api(
        "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    assert "SIN-PRECIO" not in {r.sku for r in records}


def test_read_woo_store_api_preserves_out_of_stock_flag() -> None:
    client = _client(_woo_handler)
    records = read_woo_store_api(
        "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    congrio = next(r for r in records if r.sku == "pes1903")
    assert congrio.in_stock is False


def test_read_woo_store_api_refuses_an_unconfigured_domain() -> None:
    client = _client(_woo_handler)
    with pytest.raises(CatalogFeedError, match="never-configured-domain.test"):
        read_woo_store_api(
            "https://never-configured-domain.test", client=client, config_dir=SITES_CONFIG_DIR
        )


def test_read_woo_store_api_refuses_a_domain_below_the_l1_ceiling() -> None:
    # D5: catalog feed reads are gated exactly like L1. A domain approved only
    # to L0 must be refused even though tos_decision is otherwise "limited".
    client = _client(_woo_handler)
    with pytest.raises(CatalogFeedError, match="L0"):
        read_woo_store_api(
            "https://tier-too-low.test", client=client, config_dir=SITES_CONFIG_DIR
        )


def test_read_woo_store_api_raises_on_zero_products() -> None:
    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content="[]", headers={"X-WP-TotalPages": "1"})

    client = _client(empty_handler)
    with pytest.raises(CatalogFeedError, match="zero products"):
        read_woo_store_api(
            "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
        )


def test_read_woo_store_api_wraps_http_errors() -> None:
    def failing_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(failing_handler)
    with pytest.raises(CatalogFeedError, match="fetch failed"):
        read_woo_store_api(
            "https://rapa-nui-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
        )
```

Add the missing `Decimal` import at the top of the test file: `from decimal import Decimal`.

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_catalog_feeds.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'src.pricing_intel.acquire.catalog_feeds'`.

- [ ] **Step 3: Write the implementation**

Create `src/pricing_intel/acquire/catalog_feeds.py`:

```python
"""Public catalog-feed readers: WooCommerce Store API and (Task 4) Shopify.

Both endpoints are the same JSON a site's own storefront JS consumes to render
its product list -- not scraped HTML, no rendering cost imposed on the server
(design.md's own justification for this over scraping). Gated exactly like an
L1 acquisition (D5 in the plan): every reader calls require_approved_site
internally before making a single request, so this module cannot be misused
to read an unapproved or under-tiered domain -- the same safe-by-construction
shape src/pricing_intel/acquire/l1.py already uses.

Never parse a slug/handle for product identity -- Frozt's handles are known to
be stale relative to their title (see Task 4). Always read the title field.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx

from . import base

USER_AGENT = "LinchpinPricingIntel/1.0 (+https://kern.example/pricing-intel-bot)"
DEFAULT_TIMEOUT_SECONDS = 10.0
_CATALOG_FEED_TIER = "L1"  # D5: no existing tier models "bulk catalog JSON"; gated as L1.


@dataclass(frozen=True)
class CatalogRecord:
    """One normalized product row from a public catalog feed.

    ``raw_format_text`` is always the feed's own title/name field -- never a
    slug or handle, which can be stale (Task 4's Frozt gotcha). Task 5's
    extract_weight_format reads this field to recover a CLP/kg denominator.
    """

    source: str  # normalized domain, e.g. "rapa-nui-catalog.test"
    sku: str
    title: str
    price: Decimal
    currency: str
    in_stock: bool
    url: str
    raw_format_text: str


class CatalogFeedError(ValueError):
    """A feed domain could not be read at all -- gate refusal, network failure,
    or an empty/entirely-malformed response. The caller (Task 6's CLI)
    quarantines this ORIGIN and moves on to the next one; it never aborts the
    whole scan (spec: "Feed caido o con rate limit: falla ese origen, no la
    corrida")."""


def _require_catalog_tier(domain: str, *, config_dir: Path | str) -> base.SiteConfig:
    try:
        config = base.require_approved_site(domain, config_dir=config_dir)
    except (base.SiteNotConfiguredError, base.SiteNotApprovedError) as exc:
        raise CatalogFeedError(f"{domain}: site gate refused: {exc}") from exc
    # base.py imports ACQUISITION_TIERS directly (`from ..models import ACQUISITION_TIERS, ...`,
    # acquire/base.py:49) -- base.ACQUISITION_TIERS is the same tuple object as
    # models.ACQUISITION_TIERS, confirmed verbatim; no need to re-import from models.
    if base.ACQUISITION_TIERS.index(_CATALOG_FEED_TIER) > base.ACQUISITION_TIERS.index(config.max_tier_allowed):
        raise CatalogFeedError(
            f"{domain}: approved only to {config.max_tier_allowed}, catalog feed reads need "
            f"{_CATALOG_FEED_TIER} (D5)"
        )
    return config


def read_woo_store_api(
    base_url: str,
    *,
    client: httpx.Client,
    config_dir: Path | str = base.DEFAULT_SITES_CONFIG_DIR,
    per_page: int = 100,
) -> tuple[CatalogRecord, ...]:
    """Read every page of a WooCommerce Store API product list.

    Paginates on ``X-WP-TotalPages``. A single malformed item (no ``prices``
    block, non-numeric price, non-positive price) is dropped silently -- this
    is catalog-ingestion hygiene, not the spec's "cuarentena con motivo"
    discipline, which applies to the CLP/kg normalization stage (Task 5/6)
    where a wrong guess would invert a conclusion. Raises CatalogFeedError if
    the domain is not approved, not tiered for L1, unreachable, or returns
    zero usable products across every page.
    """
    domain = base.normalize_domain(base_url)
    if domain is None:
        raise CatalogFeedError(f"could not extract a domain from {base_url!r}")
    _require_catalog_tier(domain, config_dir=config_dir)

    records: list[CatalogRecord] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        url = f"{base_url.rstrip('/')}/wp-json/wc/store/v1/products"
        try:
            response = client.get(
                url,
                params={"per_page": per_page, "page": page},
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise CatalogFeedError(f"{domain}: fetch failed on page {page}: {exc}") from exc
        if response.status_code != 200:
            raise CatalogFeedError(f"{domain}: HTTP {response.status_code} on page {page}")
        total_pages = int(response.headers.get("X-WP-TotalPages", "1"))
        try:
            items = response.json()
        except ValueError as exc:
            raise CatalogFeedError(f"{domain}: page {page} was not valid JSON: {exc}") from exc
        for item in items:
            record = _woo_item_to_record(item, domain=domain, base_url=base_url)
            if record is not None:
                records.append(record)
        page += 1

    if not records:
        raise CatalogFeedError(f"{domain}: feed returned zero products across {total_pages} page(s)")
    return tuple(records)


def _woo_item_to_record(item: dict, *, domain: str, base_url: str) -> CatalogRecord | None:
    prices = item.get("prices") or {}
    raw_price = prices.get("price")
    currency = prices.get("currency_code")
    title = item.get("name", "")
    if not raw_price or not currency or not title:
        return None
    minor_unit = prices.get("currency_minor_unit", 2)
    try:
        amount = Decimal(str(raw_price)) / (Decimal(10) ** int(minor_unit))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if amount <= 0:
        return None
    sku = item.get("sku") or str(item.get("id", ""))
    return CatalogRecord(
        source=domain,
        sku=str(sku),
        title=title,
        price=amount,
        currency=currency,
        in_stock=bool(item.get("is_in_stock", True)),
        url=item.get("permalink", base_url),
        raw_format_text=title,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_catalog_feeds.py -q
```

Expected: `9 passed`.

- [ ] **Step 5: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/test_pricing_intel_acquire_base.py tests/test_pricing_intel_boot_safety.py -q
ruff check src tests examples
```

Expected: all pass. If `test_pricing_intel_acquire_base.py` isn't the real filename, find it first: `PYTHONPATH=. python -m pytest tests/ -q -k "acquire and base"`.

- [ ] **Step 6: Commit**

```bash
git add src/pricing_intel/acquire/catalog_feeds.py tests/test_catalog_feeds.py tests/fixtures/cl_catalog/
git commit -m "feat(pricing-intel): CatalogRecord + WooCommerce Store API reader

Gated exactly like an L1 acquisition (D5): require_approved_site runs inside
the reader, never left to the caller, so an unapproved or under-tiered domain
cannot be read. A malformed item (no prices block, non-numeric price) is
dropped at ingestion; a whole-origin failure raises CatalogFeedError so the
CLI can quarantine that source and keep going."
```

---

### Task 4: `read_shopify_products_json` -- variants, and the slug that must never be read

**Files:**
- Modify: `src/pricing_intel/acquire/catalog_feeds.py` (append)
- Modify: `tests/test_catalog_feeds.py` (append)
- Create fixture: `tests/fixtures/cl_catalog/shopify_page1.json`
- Create fixture config: `tests/fixtures/cl_catalog/sites/frozt-catalog.test.yaml`

**Interfaces:**
- Consumes: `CatalogRecord`, `CatalogFeedError`, `_require_catalog_tier` from Task 3.
- Produces: `read_shopify_products_json(base_url, *, client, config_dir=...) -> tuple[CatalogRecord, ...]`. Task 5 and Task 6 consume it identically to `read_woo_store_api`.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/cl_catalog/sites/frozt-catalog.test.yaml` (same shape as Task 3's `rapa-nui-catalog.test.yaml`, different domain):

```yaml
domain: frozt-catalog.test
robots_txt_respected: true
robots_checked_at: "2026-07-24"
tos_summary: "Synthetic fixture for the radiografia catalog-scan test suite (D5)."
tos_decision: limited
rate_limit_seconds: 3.0
max_tier_allowed: L1
pii_policy: none
```

Create `tests/fixtures/cl_catalog/shopify_page1.json` (the handle is real-shape-corrupted on purpose -- this is the actual Frozt gotcha the handoff recorded: the handle reads "26-30" while the title says "36/40". If the reader ever derived the SKU text from `handle` instead of `title`, this fixture catches it):

```json
{
  "products": [
    {
      "id": 55001,
      "title": "Camaron 36/40 cocido pelado desvenado",
      "handle": "camaron-26-30-crudo-pelada-desvenada-fish-box-copia",
      "variants": [
        {
          "id": 990001,
          "sku": "FRZ-CAM-3640",
          "title": "Default Title",
          "price": "13990.00",
          "available": true
        }
      ]
    },
    {
      "id": 55002,
      "title": "Pulpo entero",
      "handle": "pulpo-entero",
      "variants": [
        {
          "id": 990002,
          "sku": "FRZ-PUL-1KG",
          "title": "1kg",
          "price": "18990.00",
          "available": true
        },
        {
          "id": 990003,
          "sku": "FRZ-PUL-2KG",
          "title": "2kg",
          "price": "34990.00",
          "available": false
        }
      ]
    },
    {
      "id": 55003,
      "title": "Producto sin variantes de precio",
      "handle": "producto-sin-precio",
      "variants": [
        {
          "id": 990004,
          "sku": "FRZ-NOPRICE",
          "title": "Default Title",
          "price": null,
          "available": true
        }
      ]
    }
  ]
}
```

Append to `tests/test_catalog_feeds.py`:

```python
from src.pricing_intel.acquire.catalog_feeds import read_shopify_products_json

_SHOPIFY_PAGE1 = (FIXTURES / "shopify_page1.json").read_text(encoding="utf-8")


def _shopify_handler(request: httpx.Request) -> httpx.Response:
    page = request.url.params.get("page", "1")
    if page == "1":
        return httpx.Response(200, content=_SHOPIFY_PAGE1)
    return httpx.Response(200, content=json.dumps({"products": []}))


def test_read_shopify_products_json_expands_variants_into_records() -> None:
    client = _client(_shopify_handler)
    records = read_shopify_products_json(
        "https://frozt-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    # 3 products, 4 variants total, 1 variant has price=null -> 3 records.
    assert len(records) == 3
    assert {r.sku for r in records} == {"FRZ-CAM-3640", "FRZ-PUL-1KG", "FRZ-PUL-2KG"}


def test_read_shopify_products_json_never_reads_the_handle_for_title() -> None:
    # The real Frozt gotcha: handle says "26-30", title says "36/40". If the
    # reader ever fell back to the handle, this title would silently read
    # "26/30" instead of "36/40" -- a wrong calibre, not just a wrong SKU.
    client = _client(_shopify_handler)
    records = read_shopify_products_json(
        "https://frozt-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    camaron = next(r for r in records if r.sku == "FRZ-CAM-3640")
    assert "36/40" in camaron.title
    assert "26-30" not in camaron.title
    assert "26/30" not in camaron.title


def test_read_shopify_products_json_appends_variant_title_when_meaningful() -> None:
    client = _client(_shopify_handler)
    records = read_shopify_products_json(
        "https://frozt-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    pulpo_1kg = next(r for r in records if r.sku == "FRZ-PUL-1KG")
    pulpo_2kg = next(r for r in records if r.sku == "FRZ-PUL-2KG")
    assert pulpo_1kg.title == "Pulpo entero 1kg"
    assert pulpo_2kg.title == "Pulpo entero 2kg"
    assert pulpo_1kg.price == Decimal("18990.00")
    assert pulpo_2kg.in_stock is False


def test_read_shopify_products_json_does_not_append_default_title() -> None:
    client = _client(_shopify_handler)
    records = read_shopify_products_json(
        "https://frozt-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    camaron = next(r for r in records if r.sku == "FRZ-CAM-3640")
    assert camaron.title == "Camaron 36/40 cocido pelado desvenado"  # no " Default Title" suffix


def test_read_shopify_products_json_drops_null_price_variants() -> None:
    client = _client(_shopify_handler)
    records = read_shopify_products_json(
        "https://frozt-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    assert "FRZ-NOPRICE" not in {r.sku for r in records}


def test_read_shopify_products_json_assumes_clp_currency() -> None:
    # Shopify's public /products.json has no per-item currency field -- every
    # radiografia source is a Chilean store, so this is a deliberate, scoped
    # assumption for this module, not a general-purpose Shopify reader.
    client = _client(_shopify_handler)
    records = read_shopify_products_json(
        "https://frozt-catalog.test", client=client, config_dir=SITES_CONFIG_DIR
    )
    assert all(r.currency == "CLP" for r in records)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_catalog_feeds.py -q
```

Expected: `ImportError: cannot import name 'read_shopify_products_json'`.

- [ ] **Step 3: Write the implementation**

Append to `src/pricing_intel/acquire/catalog_feeds.py`:

```python
def read_shopify_products_json(
    base_url: str,
    *,
    client: httpx.Client,
    config_dir: Path | str = base.DEFAULT_SITES_CONFIG_DIR,
    limit: int = 250,
) -> tuple[CatalogRecord, ...]:
    """Read every page of a Shopify storefront's public /products.json.

    Every variant of every product becomes its own CatalogRecord (a variant IS
    a separately-priced, separately-stocked SKU). ``title`` and
    ``raw_format_text`` are ALWAYS built from the product's ``title`` field,
    never its ``handle`` -- Frozt's handles are confirmed stale relative to
    their title. Currency is hardcoded "CLP": the endpoint carries no
    per-item currency field and every radiografia source is a Chilean store.
    """
    domain = base.normalize_domain(base_url)
    if domain is None:
        raise CatalogFeedError(f"could not extract a domain from {base_url!r}")
    _require_catalog_tier(domain, config_dir=config_dir)

    records: list[CatalogRecord] = []
    page = 1
    while True:
        url = f"{base_url.rstrip('/')}/products.json"
        try:
            response = client.get(
                url,
                params={"limit": limit, "page": page},
                headers={"User-Agent": USER_AGENT},
                timeout=DEFAULT_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise CatalogFeedError(f"{domain}: fetch failed on page {page}: {exc}") from exc
        if response.status_code != 200:
            raise CatalogFeedError(f"{domain}: HTTP {response.status_code} on page {page}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CatalogFeedError(f"{domain}: page {page} was not valid JSON: {exc}") from exc
        products = payload.get("products", [])
        if not products:
            break
        for product in products:
            records.extend(_shopify_product_to_records(product, domain=domain))
        if len(products) < limit:
            break
        page += 1

    if not records:
        raise CatalogFeedError(f"{domain}: feed returned zero products")
    return tuple(records)


def _shopify_product_to_records(product: dict, *, domain: str) -> list[CatalogRecord]:
    title = product.get("title", "")
    handle = product.get("handle", "")
    if not title:
        return []
    out: list[CatalogRecord] = []
    for variant in product.get("variants", []):
        raw_price = variant.get("price")
        if raw_price is None:
            continue
        try:
            amount = Decimal(str(raw_price))
        except InvalidOperation:
            continue
        if amount <= 0:
            continue
        variant_title = variant.get("title")
        full_title = title if (not variant_title or variant_title == "Default Title") else f"{title} {variant_title}"
        sku = variant.get("sku") or f"shopify-{variant.get('id')}"
        out.append(
            CatalogRecord(
                source=domain,
                sku=str(sku),
                title=full_title,
                price=amount,
                currency="CLP",
                in_stock=bool(variant.get("available", True)),
                url=f"https://{domain}/products/{handle}",
                raw_format_text=full_title,
            )
        )
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_catalog_feeds.py -q
```

Expected: `15 passed`.

- [ ] **Step 5: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/test_pricing_intel_boot_safety.py -q
ruff check src tests examples
```

Expected: all pass. `test_pricing_intel_boot_safety.py` matters here specifically: `catalog_feeds.py` imports only `httpx`, `dataclasses`, `decimal`, `pathlib` and `.base` at module level -- none of the boot-safety guard's blocked optional extras -- confirm this stays true.

- [ ] **Step 6: Commit**

```bash
git add src/pricing_intel/acquire/catalog_feeds.py tests/test_catalog_feeds.py tests/fixtures/cl_catalog/
git commit -m "feat(pricing-intel): Shopify products.json reader

Every variant becomes its own CatalogRecord. title/raw_format_text always
come from the product's title field, never its handle -- Frozt's handles are
confirmed stale relative to title (handle says 26-30, title says 36/40), and
a fixture pins this so a future refactor can't reintroduce the bug."
```

---

### Task 5: es-CL lexicon, the `NO_BRAND_DECLARED` sentinel (D2), and `DECISIVE_ATTRIBUTE_KEYS` (D4)

This is where D2 and D4 stop being decisions on paper and become code: without this task, `probabilistic.score_pair` maxes out at `0.85 < CONFIRM_THRESHOLD (0.96)` on every cross-site pair (D2's math), and `attr_sim` is always the neutral `1.0` because none of the four new keys exist yet (D4). This task's golden test is the first point in the whole plan where a real cross-site pair actually reaches `CONFIRM_THRESHOLD` -- if it doesn't, PR-1's entire reason for existing (a market comparison) is not yet real.

**Files:**
- Create: `config/lexicon/es_cl_congelados.yaml`
- Create: `src/pricing_intel/lexicon.py`
- Modify: `src/pricing_intel/match/probabilistic.py:103` (D4)
- Create: `tests/test_es_cl_lexicon.py`

**Interfaces:**
- Consumes: `CatalogRecord` (Task 3/4), `ProductAttributes` (`match/fuzzy.py`), `score_pair`/`CONFIRM_THRESHOLD` (`match/probabilistic.py`).
- Produces: `NO_BRAND_DECLARED: str`, `attributes_from_title(title, vocab) -> dict[str, str]`, `count_concordant_signals(a, b, *, keys=...) -> int` (the spec's "two senales concordantes" gate), `to_product_attributes(record, *, vocab, product_id=None) -> ProductAttributes`. PR-3's `market_radiography.cluster_offers` (D7) consumes all four.

- [ ] **Step 1: Write the lexicon YAML**

Create `config/lexicon/es_cl_congelados.yaml`. **Key order inside `especie` is load-bearing**: compound forms (`merluza austral`, `merluza comun`, `merluza hubbsi`, `ostion del norte`, `camaron nailon`) are tried before their generic parent (`merluza`, `ostion`, `camaron`) because `attributes_from_title` matches in file order and stops at the first hit -- PyYAML's `safe_load` preserves mapping insertion order (Python 3.7+ dict semantics). This ordering is exactly why `veda_calendar.py` (PR-2) will be able to tell `merluza austral`'s veda (biologica, agosto) apart from `merluza comun`'s (biologica, septiembre, -15% cuota) apart from `merluza hubbsi`'s (no veda registered) -- get the order wrong here and PR-2 silently under- or over-reports exposure two PRs later.

```yaml
# es-CL frozen-food vocabulary for attribute extraction (no GTIN, no brand
# market -- D2/D4). Regex patterns are Spanish, case-insensitive, applied
# after lowercasing. Order inside `especie` is load-bearing: see plan Task 5.
especie:
  "merluza austral":
    patterns: ["merluza\\s+austral(es)?"]
  "merluza comun":
    patterns: ["merluza\\s+com[uú]n(es)?"]
  "merluza hubbsi":
    patterns: ["merluza\\s+hubbsi"]
  "ostion del norte":
    patterns: ["ostio?n\\s+del\\s+norte"]
  "camaron nailon":
    patterns: ["camaron(es)?\\s+nail[oó]n"]
  merluza:
    patterns: ["merluza(s)?"]
  ostion:
    patterns: ["ostio?n(es)?"]
  camaron:
    patterns: ["camaron(es)?"]
  langostino:
    patterns: ["langostino(s)?"]
  reineta:
    patterns: ["reineta(s)?"]
  congrio:
    patterns: ["congrio(s)?"]
  salmon:
    patterns: ["salm[oó]n(es)?"]
  trucha:
    patterns: ["trucha(s)?"]
  albacora:
    patterns: ["albacora(s)?"]
  atun:
    patterns: ["at[uú]n(es)?"]
  pangasius:
    patterns: ["pangasius"]
  tilapia:
    patterns: ["tilapia(s)?"]
  calamar:
    patterns: ["calamar(es)?"]
  pulpo:
    patterns: ["pulpo(s)?"]
  chorito:
    patterns: ["chorito(s)?"]
  almeja:
    patterns: ["almeja(s)?"]
  macha:
    patterns: ["macha(s)?"]
  navajuela:
    patterns: ["navajuela(s)?"]
  loco:
    patterns: ["loco(s)?"]
  centollon:
    patterns: ["centollon(es)?"]
  centolla:
    patterns: ["centolla(s)?"]
estado:
  crudo:
    patterns: ["crud[oa]s?"]
  cocido:
    patterns: ["cocid[oa]s?"]
proceso:
  pelado:
    patterns: ["pelad[oa]s?"]
  desvenado:
    patterns: ["desvenad[oa]s?"]
  con_caparazon:
    patterns: ["con\\s+caparaz[oó]n"]
  apanado:
    patterns: ["apanad[oa]s?"]
  rebosado:
    patterns: ["reboz?ad[oa]s?"]
  iqf:
    patterns: ["\\biqf\\b"]
  porcionado:
    patterns: ["porcionad[oa]s?"]
  entero:
    patterns: ["enter[oa]s?"]
  filete:
    patterns: ["filete(s)?"]
  trozos:
    patterns: ["trozo(s)?"]
  anillos:
    patterns: ["anillo(s)?"]
  medallon:
    patterns: ["medall[oó]n(es)?"]
origen:
  ecuatoriano:
    patterns: ["ecuatorian[oa]s?"]
  nacional:
    patterns: ["nacional(es)?"]
  importado:
    patterns: ["importad[oa]s?"]
  del_norte:
    patterns: ["del\\s+norte"]
  austral:
    patterns: ["austral(es)?"]
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_es_cl_lexicon.py`:

```python
"""es-CL frozen-food attribute extraction, the NO_BRAND_DECLARED sentinel
(D2), and the two-concordant-signal gate the spec requires for a market with
no GTIN and no brand declared anywhere (design.md: "Un match exige al menos
dos senales concordantes ... nunca solo similitud de string")."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.pricing_intel.acquire.catalog_feeds import CatalogRecord
from src.pricing_intel.lexicon import (
    NO_BRAND_DECLARED,
    attributes_from_title,
    count_concordant_signals,
    load_lexicon,
    to_product_attributes,
)

VOCAB = load_lexicon(Path("config/lexicon/es_cl_congelados.yaml"))


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Camaron 36/40 crudo pelado desvenado 1kg", {"especie": "camaron", "calibre": "36/40", "estado": "crudo", "proceso": "pelado"}),
        ("Camaron 36/40 cocido pelado desvenado", {"especie": "camaron", "calibre": "36/40", "estado": "cocido", "proceso": "pelado"}),
        ("Filete de Reineta 500g", {"especie": "reineta", "proceso": "filete"}),
        ("Pulpo entero 2,5kg", {"especie": "pulpo", "proceso": "entero"}),
        ("Ostion 100/200 IQF 500 grs", {"especie": "ostion", "calibre": "100/200", "proceso": "iqf"}),
        ("Merluza Austral Filete 1kg", {"especie": "merluza austral", "proceso": "filete"}),
        ("Merluza Comun trozos", {"especie": "merluza comun", "proceso": "trozos"}),
        ("Merluza Hubbsi entera", {"especie": "merluza hubbsi", "proceso": "entero"}),
        ("Filete de Merluza 500g", {"especie": "merluza", "proceso": "filete"}),  # no compound match -> generic
        ("Ostion del Norte calibre 100/200", {"especie": "ostion del norte", "calibre": "100/200"}),
        ("Camaron Nailon cocido", {"especie": "camaron nailon", "estado": "cocido"}),
        ("Langostinos importados", {"especie": "langostino", "origen": "importado"}),
        ("Salmon porcionado nacional", {"especie": "salmon", "proceso": "porcionado", "origen": "nacional"}),
    ],
)
def test_attributes_from_title_extracts_the_right_categories(title: str, expected: dict) -> None:
    attrs = attributes_from_title(title, VOCAB)
    for key, value in expected.items():
        assert attrs.get(key) == value, f"{key}: expected {value!r}, got {attrs.get(key)!r} for {title!r}"


def test_compound_especie_wins_over_generic_parent() -> None:
    # The exact D2/PR-2 forward-dependency this task exists to protect: if
    # this regresses to "merluza", veda_calendar (PR-2) would silently miss
    # merluza austral's veda and misreport merluza hubbsi as exposed when it
    # has none registered.
    assert attributes_from_title("Merluza Austral 1kg", VOCAB)["especie"] == "merluza austral"
    assert attributes_from_title("Merluza Comun 1kg", VOCAB)["especie"] == "merluza comun"
    assert attributes_from_title("Merluza Hubbsi 1kg", VOCAB)["especie"] == "merluza hubbsi"
    assert attributes_from_title("Merluza sin especificar 1kg", VOCAB)["especie"] == "merluza"


def test_attributes_from_title_returns_empty_dict_for_no_matches() -> None:
    assert attributes_from_title("Surtido de mariscos", VOCAB) == {}


def test_calibre_normalizes_dash_to_slash() -> None:
    assert attributes_from_title("Camaron 26-30 crudo", VOCAB)["calibre"] == "26/30"


def test_no_brand_declared_is_a_stable_string_constant() -> None:
    assert NO_BRAND_DECLARED == "sin marca declarada"


def test_to_product_attributes_uses_the_sentinel_identically_across_sources() -> None:
    rapa_nui = CatalogRecord(
        source="rapa-nui-catalog.test", sku="MAR1401", title="Camaron 36/40 crudo pelado 1kg",
        price=15990, currency="CLP", in_stock=True, url="https://rapa-nui-catalog.test/x",
        raw_format_text="Camaron 36/40 crudo pelado 1kg",
    )
    frozt = CatalogRecord(
        source="frozt-catalog.test", sku="FRZ-CAM-3640", title="Camaron 36/40 crudo pelado desvenado",
        price=13990, currency="CLP", in_stock=True, url="https://frozt-catalog.test/x",
        raw_format_text="Camaron 36/40 crudo pelado desvenado",
    )
    a = to_product_attributes(rapa_nui, vocab=VOCAB)
    b = to_product_attributes(frozt, vocab=VOCAB)
    assert a.brand == b.brand == NO_BRAND_DECLARED  # D2: identical, byte-for-byte, across every source


def test_to_product_attributes_product_id_defaults_to_source_and_sku() -> None:
    record = CatalogRecord(
        source="rapa-nui-catalog.test", sku="MAR1401", title="Filete de Reineta 1kg",
        price=11768, currency="CLP", in_stock=True, url="https://rapa-nui-catalog.test/x",
        raw_format_text="Filete de Reineta 1kg",
    )
    attrs = to_product_attributes(record, vocab=VOCAB)
    assert attrs.product_id == "rapa-nui-catalog.test:MAR1401"


@pytest.mark.parametrize(
    "a,b,expected_count",
    [
        ({"especie": "camaron", "calibre": "36/40"}, {"especie": "camaron", "calibre": "36/40"}, 2),
        ({"especie": "camaron", "calibre": "36/40"}, {"especie": "camaron", "calibre": "26/30"}, 1),  # calibre disagrees
        ({"especie": "camaron"}, {"especie": "camaron"}, 1),  # only one shared key at all
        ({"especie": "camaron", "calibre": "36/40", "estado": "crudo"}, {"especie": "camaron", "calibre": "36/40", "estado": "cocido"}, 2),
        ({}, {"especie": "camaron"}, 0),  # nothing shared
    ],
)
def test_count_concordant_signals(a: dict, b: dict, expected_count: int) -> None:
    assert count_concordant_signals(a, b) == expected_count


def test_golden_camaron_3640_reaches_confirm_threshold_across_sources() -> None:
    # The spec's own acceptance bar (design.md Testing section): a cluster
    # anchor (camaron 36/40 crudo) must actually confirm across sources once
    # D2 (sentinel) + D4 (decisive keys) are both wired in -- before this
    # task, the math ceilings at 0.85 and this test would fail.
    from src.pricing_intel.match.probabilistic import CONFIRM_THRESHOLD, score_pair

    rapa_nui = CatalogRecord(
        source="rapa-nui-catalog.test", sku="MAR1004", title="Camaron 36/40 crudo pelado 1kg",
        price=15990, currency="CLP", in_stock=True, url="https://rapa-nui-catalog.test/x",
        raw_format_text="Camaron 36/40 crudo pelado 1kg",
    )
    frozt = CatalogRecord(
        source="frozt-catalog.test", sku="FRZ-CAM-3640", title="Camaron 36/40 crudo pelado",
        price=13990, currency="CLP", in_stock=True, url="https://frozt-catalog.test/x",
        raw_format_text="Camaron 36/40 crudo pelado",
    )
    a = to_product_attributes(rapa_nui, vocab=VOCAB)
    b = to_product_attributes(frozt, vocab=VOCAB)
    result = score_pair(a, b)
    assert result.score >= CONFIRM_THRESHOLD
    assert count_concordant_signals(a.attributes, b.attributes) >= 2
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_es_cl_lexicon.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'src.pricing_intel.lexicon'`.

- [ ] **Step 4: Write `src/pricing_intel/lexicon.py`**

```python
"""es-CL frozen-food attribute extraction -- the vocabulary that stands in
for GTIN/brand in a market where neither exists (D2, D4).

NO_BRAND_DECLARED is the sentinel every CatalogRecord's ProductAttributes.brand
carries: identical across all 9 sources, so brand_sim contributes a uniform
+0.15 to every pair and all discrimination comes from title (0.55) and
attributes (0.30) -- see plan D2 for the full arithmetic. Never invent a
per-site fake brand name; that would make brand_sim noise instead of a
constant, corrupting the ranking D2 depends on staying untouched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .acquire.catalog_feeds import CatalogRecord
from .match.fuzzy import ProductAttributes

NO_BRAND_DECLARED = "sin marca declarada"

DEFAULT_LEXICON_PATH = Path("config/lexicon/es_cl_congelados.yaml")

# The es-CL decisive keys DECISIVE_ATTRIBUTE_KEYS gains in this task (D4).
# origen is deliberately excluded -- the spec's own worked example is
# "especie + calibre", and origen is inconsistently declared enough across
# the 9 sources that requiring it to agree would fragment true matches.
ES_CL_DECISIVE_KEYS: tuple[str, ...] = ("especie", "calibre", "estado", "proceso")

_CALIBRE_RE = re.compile(r"\b(\d{1,3})[/-](\d{1,3})\b")

LexiconVocab = dict[str, list[tuple[str, list[re.Pattern]]]]


def load_lexicon(path: str | Path = DEFAULT_LEXICON_PATH) -> LexiconVocab:
    """Load and compile the es-CL vocabulary. Category and key order inside
    the YAML is preserved (PyYAML safe_load keeps mapping insertion order) --
    this is load-bearing for compound especie forms, see Task 5."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    vocab: LexiconVocab = {}
    for category, entries in raw.items():
        compiled = []
        for canonical, spec in entries.items():
            patterns = [re.compile(p, re.IGNORECASE) for p in spec["patterns"]]
            compiled.append((canonical, patterns))
        vocab[category] = compiled
    return vocab


def attributes_from_title(title: str, vocab: LexiconVocab) -> dict[str, str]:
    """First-match-wins per category, in the vocabulary's own key order.
    calibre is matched separately by a generic numeric-range regex, not
    listed per-value in the YAML (there are too many valid calibres to
    enumerate, and the format itself -- N/M -- is what identifies it, not a
    fixed vocabulary)."""
    attrs: dict[str, str] = {}
    for category, entries in vocab.items():
        for canonical, patterns in entries:
            if any(p.search(title) for p in patterns):
                attrs[category] = canonical
                break
    calibre_match = _CALIBRE_RE.search(title)
    if calibre_match:
        attrs["calibre"] = f"{calibre_match.group(1)}/{calibre_match.group(2)}"
    return attrs


def count_concordant_signals(
    a: dict[str, str], b: dict[str, str], *, keys: tuple[str, ...] = ES_CL_DECISIVE_KEYS
) -> int:
    """How many of `keys` are present on both sides AND agree. The spec's own
    match-quality floor (design.md: "al menos dos senales concordantes,
    nunca solo similitud de string") -- PR-3's clustering (D7) requires this
    to be >= 2 in addition to the probabilistic score threshold, so a single
    coincidentally-similar title can never confirm a match alone."""
    return sum(1 for k in keys if k in a and k in b and a[k] == b[k])


def to_product_attributes(
    record: CatalogRecord, *, vocab: LexiconVocab, product_id: str | None = None
) -> ProductAttributes:
    """Bridge a CatalogRecord into the match/ pipeline's own input shape.
    brand is always NO_BRAND_DECLARED (D2) -- never derived from `record.source`
    or anything else that would vary per site."""
    return ProductAttributes(
        product_id=product_id or f"{record.source}:{record.sku}",
        title=record.title,
        brand=NO_BRAND_DECLARED,
        attributes=attributes_from_title(record.raw_format_text, vocab),
    )
```

- [ ] **Step 5: Extend `DECISIVE_ATTRIBUTE_KEYS` (D4)**

In `src/pricing_intel/match/probabilistic.py`, replace line 103:

```python
DECISIVE_ATTRIBUTE_KEYS: tuple[str, ...] = ("pack_size", "size", "model", "capacity", "color")
```

with:

```python
# es-CL frozen-food keys appended for the radiografia (D4, plan
# 2026-07-24-radiografia-congelados-chile.md): additive and safe by
# construction -- _attribute_similarity only compares keys present on BOTH
# sides, and no existing offer in this codebase carries especie/calibre/
# estado/proceso, so every pre-existing matching path scores identically.
DECISIVE_ATTRIBUTE_KEYS: tuple[str, ...] = (
    "pack_size", "size", "model", "capacity", "color",
    "especie", "calibre", "estado", "proceso",
)
```

- [ ] **Step 6: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_es_cl_lexicon.py -q
```

Expected: `21 passed`. If the golden test fails short of `CONFIRM_THRESHOLD`, print `score_pair`'s full `ProbabilisticScore` (title_sim/brand_sim/attr_sim breakdown) before touching the vocabulary -- the two likely causes are (a) `especie`/`calibre` not both extracted from one of the two titles (check `attributes_from_title` output directly), or (b) `_normalize_text`'s own whitespace/case handling interacting oddly with the regex; it is NOT a reason to lower `CONFIRM_THRESHOLD` or hand-tune the score, which is existing, unmodified code (D2's whole premise is that the sentinel makes the EXISTING formula work, not that the formula needs changing).

- [ ] **Step 7: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/test_pricing_intel_match_probabilistic.py tests/test_pricing_intel_match_fuzzy.py tests/test_pricing_intel_match_adjudicate.py tests/test_pricing_intel_boot_safety.py -q
ruff check src tests examples
```

Expected: all pass -- D4's extension must not change a single existing assertion in the probabilistic/fuzzy/adjudicate suites (find the real filenames first if these guesses are wrong: `PYTHONPATH=. python -m pytest tests/ -q -k "probabilistic or fuzzy or adjudicate"`). `lexicon.py` imports `yaml` at module level, which is a existing direct dependency (`PyYAML`), not on the boot-safety guard's blocked-extras list -- confirm `tests/test_pricing_intel_boot_safety.py::_BLOCKED` still doesn't need updating.

- [ ] **Step 8: Commit**

```bash
git add config/lexicon/es_cl_congelados.yaml src/pricing_intel/lexicon.py src/pricing_intel/match/probabilistic.py tests/test_es_cl_lexicon.py
git commit -m "feat(pricing-intel): es-CL lexicon, NO_BRAND_DECLARED sentinel, decisive keys

D2 + D4 made real: DECISIVE_ATTRIBUTE_KEYS gains especie/calibre/estado/
proceso, and every CatalogRecord bridges into ProductAttributes with the
identical NO_BRAND_DECLARED sentinel, so brand_sim stops being a hard
ValueError and starts being a uniform, ranking-neutral +0.15. The golden
camaron-36-40 cross-source pair now reaches CONFIRM_THRESHOLD -- before this
commit the math ceilinged at 0.85 (D2's own derivation) and no cross-site
match was possible at all. count_concordant_signals adds the spec's own
two-signal floor as a second, independent gate PR-3 will apply alongside the
probabilistic score."
```

---

### Task 6: `examples/run_cl_catalog_scan.py` -- the network CLI, PR-1's deliverable

**Files:**
- Create: `config/cl_catalog_sources.yaml` (the 9-source manifest -- placeholders, see Step 0)
- Create: `examples/run_cl_catalog_scan.py`
- Create: `tests/test_run_cl_catalog_scan.py`

**Interfaces:**
- Consumes: `read_woo_store_api`/`read_shopify_products_json`/`CatalogFeedError` (Task 3/4), `extract_weight_format`/`price_per_kilogram`/`VolumetricNotConvertible` (Task 1/2), `attributes_from_title`/`load_lexicon` (Task 5), `normalize_price_string` (existing, for currency confirmation only -- `CatalogRecord.price` is already `Decimal` from the reader).
- Produces: a normalized CSV whose row shape PR-3's `market_radiography_job.py::prepare` reads back verbatim (columns: `source, sku, title, price_clp, currency, in_stock, url, price_per_kg, quarantine_reason, especie, calibre, estado, proceso, origen`), plus a `refs.csv` by-product (`product_id, site, url` -- D3: unused by this plan, a fase-2 `price_watch` seed) and a printed coverage report.

- [ ] **Step 0: Operator step -- confirm the 9 real base URLs and author their site configs (network-dependent, not scriptable into this plan)**

This step needs a live connection and cannot be pre-written here without fabricating domains. Whoever executes this task:

1. Confirms the real base URL for each of the 5 sujetos and the 2 remaining reference sources (D6 drops IceFish/Mardeli): Rapa-Nui, Pulpo Chile, TodoFrio, Del Mar A Tu Hogar, Mariscos De La Concha, Frozt, South Wind.
2. Re-reads each domain's `/robots.txt` and confirms it still permits the relevant path (`/wp-json/wc/store/v1/products` or `/products.json`) -- the 2026-07-24 research pass already did this once; re-verify at execution time since robots.txt can change.
3. For each domain, creates `config/sites/<domain>.yaml` by hand, following `config/sites/example-retailer.test.yaml`'s shape exactly, with `tos_decision: limited` and `max_tier_allowed: L1` (D5 -- never `"allowed"`, never a higher tier).
4. Fills in `config/cl_catalog_sources.yaml` (Step 1 below) with the real `base_url` per domain.

Skipping this step is safe -- Tasks 1-5 and the rest of Task 6 are fully testable against fixtures without it -- but the CLI cannot run against real feeds until it's done.

- [ ] **Step 1: Write the source manifest template**

Create `config/cl_catalog_sources.yaml` with placeholder URLs the operator fills in (Step 0):

```yaml
# Radiografia del congelado chileno -- 9-source manifest (D6: 7 of the
# original 9, IceFish/Mardeli's JSON-LD reader deferred). base_url is a
# placeholder until Step 0 is done by whoever runs this against live feeds.
- domain: REPLACE_ME_rapa_nui
  base_url: "https://REPLACE_ME.cl"
  platform: woo
  role: sujeto
- domain: REPLACE_ME_pulpo_chile
  base_url: "https://REPLACE_ME.cl"
  platform: woo
  role: sujeto
- domain: REPLACE_ME_todofrio
  base_url: "https://REPLACE_ME.cl"
  platform: woo
  role: sujeto
- domain: REPLACE_ME_del_mar_a_tu_hogar
  base_url: "https://REPLACE_ME.cl"
  platform: woo
  role: sujeto
- domain: REPLACE_ME_mariscos_de_la_concha
  base_url: "https://REPLACE_ME.cl"
  platform: shopify
  role: sujeto
- domain: REPLACE_ME_frozt
  base_url: "https://REPLACE_ME.cl"
  platform: shopify
  role: referencia
- domain: REPLACE_ME_south_wind
  base_url: "https://REPLACE_ME.cl"
  platform: shopify
  role: referencia
```

- [ ] **Step 2: Write the failing test for the pure row-building logic**

The CLI's network glue (`main`/`argparse`/a real `httpx.Client`) is deliberately thin and untested by pytest -- every other example CLI in this repo follows that convention. The part worth unit-testing is `build_normalized_rows`, pure given a list of already-fetched `CatalogRecord`s.

Create `tests/test_run_cl_catalog_scan.py`:

```python
"""Pure row-building logic for run_cl_catalog_scan.py. The network/argparse
glue in main() is intentionally untested here -- see the module docstring."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from examples.run_cl_catalog_scan import build_normalized_rows
from src.pricing_intel.acquire.catalog_feeds import CatalogRecord
from src.pricing_intel.lexicon import load_lexicon

VOCAB = load_lexicon(Path("config/lexicon/es_cl_congelados.yaml"))


def _record(sku: str, title: str, price: str) -> CatalogRecord:
    return CatalogRecord(
        source="rapa-nui-catalog.test", sku=sku, title=title, price=Decimal(price),
        currency="CLP", in_stock=True, url=f"https://rapa-nui-catalog.test/{sku}",
        raw_format_text=title,
    )


def test_build_normalized_rows_computes_price_per_kg_for_a_readable_format() -> None:
    records = [_record("MAR1401", "Filete de Reineta 1kg", "11768")]
    rows, coverage = build_normalized_rows(records, vocab=VOCAB)
    assert rows[0]["price_per_kg"] == Decimal("11768")
    assert rows[0]["quarantine_reason"] == ""
    assert coverage.comparable == 1
    assert coverage.quarantined == 0


def test_build_normalized_rows_is_the_conclusion_inverting_case() -> None:
    # The exact TodoFrio-vs-Rapa-Nui case Task 1/2 exist for, run end-to-end
    # through the CLI's own row-building for the first time in this plan.
    records = [
        _record("TDF-REI-500", "Filete de Reineta 500g", "10090"),
        _record("MAR1401", "Filete de Reineta 1kg", "11768"),
    ]
    rows, _ = build_normalized_rows(records, vocab=VOCAB)
    todofrio = next(r for r in rows if r["sku"] == "TDF-REI-500")
    rapa_nui = next(r for r in rows if r["sku"] == "MAR1401")
    assert todofrio["price_per_kg"] == Decimal("20180")
    assert rapa_nui["price_per_kg"] == Decimal("11768")
    assert todofrio["price_per_kg"] > rapa_nui["price_per_kg"]


def test_build_normalized_rows_quarantines_an_unreadable_format() -> None:
    records = [_record("X1", "Surtido de mariscos", "9990")]
    rows, coverage = build_normalized_rows(records, vocab=VOCAB)
    assert rows[0]["price_per_kg"] is None
    assert rows[0]["quarantine_reason"] != ""
    assert coverage.comparable == 0
    assert coverage.quarantined == 1


def test_build_normalized_rows_quarantines_a_volumetric_format_separately() -> None:
    records = [_record("X2", "Locos al natural 1lt", "9990")]
    rows, coverage = build_normalized_rows(records, vocab=VOCAB)
    assert rows[0]["price_per_kg"] is None
    assert "volum" in rows[0]["quarantine_reason"]
    assert coverage.quarantined == 1


def test_build_normalized_rows_attaches_lexicon_attributes() -> None:
    records = [_record("MAR1004", "Camaron 36/40 crudo pelado 1kg", "15990")]
    rows, _ = build_normalized_rows(records, vocab=VOCAB)
    row = rows[0]
    assert row["especie"] == "camaron"
    assert row["calibre"] == "36/40"
    assert row["estado"] == "crudo"
    assert row["proceso"] == "pelado"


def test_build_normalized_rows_coverage_totals_are_consistent() -> None:
    records = [
        _record("A", "Filete de Reineta 1kg", "11768"),
        _record("B", "Surtido de mariscos", "9990"),
        _record("C", "Locos al natural 1lt", "9990"),
    ]
    rows, coverage = build_normalized_rows(records, vocab=VOCAB)
    assert coverage.total == 3
    assert coverage.comparable + coverage.quarantined == coverage.total
    assert len(rows) == 3
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_run_cl_catalog_scan.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'examples.run_cl_catalog_scan'`.

- [ ] **Step 4: Write `examples/run_cl_catalog_scan.py`**

```python
"""Network CLI: read the 9 (7, D6) public catalog feeds, normalize each SKU
to CLP/kg, extract es-CL attributes, write a normalized CSV + a refs.csv
by-product + a coverage report.

Usage:
    python examples/run_cl_catalog_scan.py --sources config/cl_catalog_sources.yaml \
        --lexicon config/lexicon/es_cl_congelados.yaml --site-config-dir config/sites \
        --out data/cl_scan/

Only build_normalized_rows is unit-tested (pure, no network). main()'s
argparse/httpx.Client wiring is deliberately thin glue, matching every other
examples/run_*.py CLI in this repo.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Sequence

import httpx
import yaml

from src.pricing_intel.acquire.catalog_feeds import (
    CatalogFeedError,
    CatalogRecord,
    read_shopify_products_json,
    read_woo_store_api,
)
from src.pricing_intel.lexicon import attributes_from_title, load_lexicon
from src.pricing_intel.normalize import VolumetricNotConvertible, extract_weight_format, price_per_kilogram

_READERS = {"woo": read_woo_store_api, "shopify": read_shopify_products_json}

CSV_COLUMNS = (
    "source", "sku", "title", "price_clp", "currency", "in_stock", "url",
    "price_per_kg", "quarantine_reason", "especie", "calibre", "estado", "proceso", "origen",
)


@dataclass(frozen=True)
class CoverageRow:
    total: int
    comparable: int
    quarantined: int
    reasons: dict[str, int]


def build_normalized_rows(
    records: Sequence[CatalogRecord], *, vocab
) -> tuple[list[dict], CoverageRow]:
    """Pure: CatalogRecord[] -> normalized row dicts + a coverage summary.
    Never guesses a price_per_kg -- an unreadable or volumetric format leaves
    it None with a quarantine_reason, per the plan's quarantine-over-guessing
    Global Constraint."""
    rows: list[dict] = []
    reasons: Counter[str] = Counter()
    comparable = 0
    for record in records:
        attrs = attributes_from_title(record.raw_format_text, vocab)
        weight_fmt = extract_weight_format(record.raw_format_text)
        price_per_kg: Decimal | None = None
        reason = ""
        if weight_fmt is None:
            reason = "formato de peso no reconocido"
        else:
            try:
                price_per_kg = price_per_kilogram(record.price, weight_fmt)
                comparable += 1
            except VolumetricNotConvertible:
                reason = "formato volumetrico (lt/cc), no convertible a masa"
        if reason:
            reasons[reason] += 1
        rows.append(
            {
                "source": record.source, "sku": record.sku, "title": record.title,
                "price_clp": record.price, "currency": record.currency, "in_stock": record.in_stock,
                "url": record.url, "price_per_kg": price_per_kg, "quarantine_reason": reason,
                "especie": attrs.get("especie", ""), "calibre": attrs.get("calibre", ""),
                "estado": attrs.get("estado", ""), "proceso": attrs.get("proceso", ""),
                "origen": attrs.get("origen", ""),
            }
        )
    coverage = CoverageRow(
        total=len(records), comparable=comparable, quarantined=len(records) - comparable, reasons=dict(reasons)
    )
    return rows, coverage


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _write_refs_csv(records: Sequence[CatalogRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=("product_id", "site", "url"))
        writer.writeheader()
        for r in records:
            writer.writerow({"product_id": f"{r.source}:{r.sku}", "site": r.source, "url": r.url})


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan the CL frozen-food catalog feeds")
    parser.add_argument("--sources", default="config/cl_catalog_sources.yaml")
    parser.add_argument("--lexicon", default="config/lexicon/es_cl_congelados.yaml")
    parser.add_argument("--site-config-dir", default="config/sites")
    parser.add_argument("--out", default="data/cl_scan")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    vocab = load_lexicon(Path(args.lexicon))
    with open(args.sources, encoding="utf-8") as f:
        sources = yaml.safe_load(f)

    all_records: list[CatalogRecord] = []
    all_rows: list[dict] = []
    print("source,status,raw_items,comparable,quarantined,reason")
    for src in sources:
        reader = _READERS[src["platform"]]
        try:
            records = reader(src["base_url"], client=httpx.Client(), config_dir=Path(args.site_config_dir))
        except CatalogFeedError as exc:
            print(f"{src['domain']},failed,0,0,0,{exc}")
            continue
        rows, coverage = build_normalized_rows(records, vocab=vocab)
        all_records.extend(records)
        all_rows.extend(rows)
        print(f"{src['domain']},ok,{coverage.total},{coverage.comparable},{coverage.quarantined},")

    out_dir = Path(args.out)
    _write_csv(all_rows, out_dir / "normalized.csv")
    _write_refs_csv(all_records, out_dir / "refs.csv")
    print(f"wrote {len(all_rows)} rows to {out_dir / 'normalized.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_run_cl_catalog_scan.py -q
```

Expected: `6 passed`.

- [ ] **Step 6: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/ -q
ruff check src tests examples
```

Expected: full suite green (this is PR-1's last task -- run the whole suite, not a scoped subset, before opening the PR).

- [ ] **Step 7: Commit**

```bash
git add config/cl_catalog_sources.yaml examples/run_cl_catalog_scan.py tests/test_run_cl_catalog_scan.py
git commit -m "feat(pricing-intel): run_cl_catalog_scan CLI -- PR-1's deliverable

Wires the two readers (Task 3/4), the CLP/kg normalizer (Task 1/2), and the
es-CL lexicon (Task 5) into a normalized CSV + refs.csv by-product + a
per-origin coverage report. build_normalized_rows is the pure, unit-tested
core; main()'s argparse/httpx wiring is deliberately thin and untested,
matching every other examples/run_*.py CLI. The 9 real site configs and
base URLs are an operator step (Task 6, Step 0) this plan cannot fabricate."
```

- [ ] **Step 8: Open the PR**

```bash
git push -u origin feat/radiografia-cl-catalog-scan
gh pr create --draft --base main --title "feat(pricing-intel): CL frozen-food catalog scan (radiografia PR-1)" --body "$(cat <<'EOF'
## Summary
- CatalogRecord + WooCommerce Store API + Shopify products.json readers, gated exactly like an L1 acquisition (D5)
- extract_weight_format / price_per_kilogram: the CLP/kg normalizer, the single point of failure the design calls out
- es-CL lexicon + NO_BRAND_DECLARED sentinel (D2) + DECISIVE_ATTRIBUTE_KEYS extension (D4) -- makes cross-site matching possible at all in a market with no GTIN and no brand
- run_cl_catalog_scan.py: the network CLI, PR-1's deliverable

## Spec deviations
See docs/superpowers/plans/2026-07-24-radiografia-congelados-chile.md, D1-D7.

## Test plan
- [ ] `PYTHONPATH=. pytest tests/ -q` green on py3.11/3.12/3.13 (CI)
- [ ] `ruff check src tests examples` clean
- [ ] Operator: author the 9 real config/sites/*.yaml (Task 6 Step 0) and run against live feeds before PR-3 depends on this data
EOF
)"
```

---

# PR-2: veda dataset + `veda_calendar.py`

**Branch:** `feat/radiografia-cl-veda-calendar` off `origin/main` (not off PR-1's branch -- PR-2 has zero code dependency on PR-1; it only shares the sector's domain vocabulary, not any Python symbol).

**Deliverable:** `config/regulatory/cl_vedas.yaml` (hand-curated) + `src/regulatory/veda_calendar.py`, importable and fully tested standalone. PR-3 is the first consumer.

- [ ] **Task 0: Branch setup**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git fetch origin
git checkout -b feat/radiografia-cl-veda-calendar origin/main
```

Expected: `Switched to a new branch 'feat/radiografia-cl-veda-calendar'`.

---

### Task 1: the veda dataset + `VedaWindow`/`VedaExposure` + `load_veda_calendar`

**Files:**
- Create: `config/regulatory/cl_vedas.yaml`
- Create: `src/regulatory/__init__.py`
- Create: `src/regulatory/veda_calendar.py`
- Create: `tests/test_veda_calendar.py`

**Interfaces:**
- Consumes: nothing from PR-1 or PR-3 -- fully self-contained (D1: this module does **not** touch `src/risk_period.py`, which has no date/window concept at all, confirmed by re-reading the full 49-line file).
- Produces: `VedaWindow` (frozen dataclass), `VedaExposure` (frozen dataclass), `load_veda_calendar(path=...) -> tuple[VedaWindow, ...]`. Task 2 consumes all three.

**No recurrence engine, by design.** A biological veda like "merluza austral, 1-31 agosto" recurs every year, but `VedaWindow.desde`/`.hasta` are plain absolute dates for the row's *current* validity window, not a month-day recurrence rule. The spec's own freshness mechanism -- `verificado_el` + a 90-day staleness warning (Task 3) -- is what forces a human to re-date a recurring row before it silently goes stale; building month/day recurrence math would be solving the same problem twice, with more ways to get a year boundary wrong (YAGNI, same spirit as D1).

- [ ] **Step 1: Write the veda dataset**

Create `config/regulatory/cl_vedas.yaml`. Every date below is either directly stated in the 2026-07-24 research pass or marked `nota:` as an approximation the operator should confirm against the decree text -- **never presented as more certain than it is**:

```yaml
# Curated SUBPESCA closed-season (veda) dataset for the radiografia sector
# scope. One row per decree/window. verificado_el drives the 90-day
# staleness warning (Task 3) -- update it whenever a row is re-checked
# against the source, even if nothing else changed.

- especie: loco
  tipo: extractiva
  desde: "2026-01-01"
  hasta: "2028-12-31"
  ambito: "Arica y Parinacota a Aysen (excluye Magallanes)"
  decreto: DEXE202600007
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura, comercializacion, transporte, procesamiento, almacenamiento]
  exencion: "AMERB donde el loco es recurso principal (art. 3); acreditar origen legal (arts. 63/65 LGPA)"
  nota: "fecha 'desde' aproximada -- el 2026-07-24 research pass confirmo la fecha 'hasta' (dic-2028) contra el decreto; confirmar 'desde' exacto antes de citar la fecha de inicio en un informe."

- especie: "ostion del norte"
  tipo: extractiva
  desde: "2022-08-01"
  hasta: "2027-08-31"
  ambito: "Regiones III y IV"
  decreto: DEXE202200104
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura, comercializacion, transporte, procesamiento, almacenamiento]
  exencion: "AMERB donde el ostion del norte es recurso principal; acreditar origen legal (arts. 63/65 LGPA)"
  nota: "fecha 'desde' aproximada a partir del numero de decreto (2022); confirmar contra el texto del decreto."

- especie: "ostion del norte"
  tipo: sanitaria
  desde: "2026-01-01"
  hasta: "2026-01-31"
  ambito: "Tongoy"
  decreto: "sin numero de decreto registrado en el research pass del 2026-07-24"
  fuente: "https://www.sernapesca.cl/"
  verificado_el: "2026-07-24"
  prohibe: [captura, comercializacion]
  exencion: ""
  nota: "cierre sanitario por marea roja, distinto de la veda extractiva de la misma especie (fila anterior); fechas exactas del decreto sanitario aun no confirmadas."

- especie: "merluza austral"
  tipo: biologica
  desde: "2026-08-01"
  hasta: "2026-08-31"
  ambito: "nacional"
  decreto: "no registrado en el research pass del 2026-07-24"
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura]
  exencion: ""
  nota: "veda biologica anual recurrente; esta fila representa la ventana de 2026, debe renovarse cada ano."

- especie: "merluza comun"
  tipo: biologica
  desde: "2026-09-01"
  hasta: "2026-09-30"
  ambito: "nacional"
  decreto: DEXE202500260
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura]
  exencion: ""
  nota: "el mismo decreto fija una reduccion de cuota 2026 de -15% respecto a 2025; la reduccion de cuota no es en si una veda y se documenta aqui solo como contexto, no como una ventana adicional."

- especie: "camaron nailon"
  tipo: biologica
  desde: "2026-09-01"
  hasta: "2026-09-30"
  ambito: "nacional"
  decreto: DEXE202500128
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura]
  exencion: ""
  nota: "veda permanente anual establecida por DEXE202500128 desde jul-2025; esta fila representa la ventana de 2026, debe renovarse cada ano."

- especie: langostino
  tipo: biologica
  desde: "2026-01-01"
  hasta: "2026-02-28"
  ambito: "nacional"
  decreto: "no registrado en el research pass del 2026-07-24"
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura]
  exencion: ""
  nota: "primera de dos ventanas anuales (ene-feb); ver tambien la fila de septiembre."

- especie: langostino
  tipo: biologica
  desde: "2026-09-01"
  hasta: "2026-09-30"
  ambito: "nacional"
  decreto: "no registrado en el research pass del 2026-07-24"
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura]
  exencion: ""
  nota: "segunda de dos ventanas anuales (sep); ver tambien la fila de enero-febrero."

- especie: chorito
  tipo: biologica
  desde: "2026-11-01"
  hasta: "2026-12-31"
  ambito: "nacional"
  decreto: "no registrado en el research pass del 2026-07-24"
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura]
  exencion: ""
  nota: ""

- especie: macha
  tipo: extractiva
  desde: "2025-08-03"
  hasta: "2026-08-02"
  ambito: "Valparaiso, O'Higgins, Maule"
  decreto: "por confirmar -- sin decreto de renovacion registrado en el research pass del 2026-07-24"
  fuente: "https://www.subpesca.cl/portal/615/articles-"
  verificado_el: "2026-07-24"
  prohibe: [captura, comercializacion, transporte, procesamiento, almacenamiento]
  exencion: "AMERB donde la macha es recurso principal; acreditar origen legal (arts. 63/65 LGPA)"
  nota: "LIVE: vence 2026-08-02 y no hay decreto de renovacion en el registro a la fecha de verificacion. Si no se renueva, tras esa fecha la especie queda sin veda vigente registrada -- el informe debe decir exactamente eso, nunca asumir que la veda continua ni que termino limpiamente."
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_veda_calendar.py`:

```python
"""VedaWindow/VedaExposure -- self-contained (D1: no risk_period.py dependency).
Test dates use `on = date(2026, 8, 15)` as the reference "today" unless a test
is specifically about staleness (which needs to be far from verificado_el)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.regulatory.veda_calendar import VedaExposure, VedaWindow, load_veda_calendar

DATASET_PATH = Path("config/regulatory/cl_vedas.yaml")


def test_load_veda_calendar_parses_every_row() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    assert len(calendar) == 10
    assert all(isinstance(w, VedaWindow) for w in calendar)


def test_load_veda_calendar_parses_dates_as_date_objects() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    loco = next(w for w in calendar if w.especie == "loco")
    assert loco.desde == date(2026, 1, 1)
    assert loco.hasta == date(2028, 12, 31)
    assert loco.verificado_el == date(2026, 7, 24)


def test_load_veda_calendar_preserves_two_rows_for_the_same_especie() -> None:
    # ostion del norte has two distinct rows: an extractiva (multi-year) and
    # a sanitaria marea roja closure (Jan 2026 only) -- both must survive.
    calendar = load_veda_calendar(DATASET_PATH)
    ostion_rows = [w for w in calendar if w.especie == "ostion del norte"]
    assert len(ostion_rows) == 2
    assert {w.tipo for w in ostion_rows} == {"extractiva", "sanitaria"}


def test_load_veda_calendar_preserves_two_langostino_windows() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    langostino_rows = [w for w in calendar if w.especie == "langostino"]
    assert len(langostino_rows) == 2
    assert {(w.desde.month, w.hasta.month) for w in langostino_rows} == {(1, 2), (9, 9)}


def test_veda_window_covers_is_inclusive_on_both_ends() -> None:
    window = VedaWindow(
        especie="merluza austral", tipo="biologica", desde=date(2026, 8, 1), hasta=date(2026, 8, 31),
        ambito="nacional", decreto="X", fuente="https://x", verificado_el=date(2026, 7, 24),
        prohibe=("captura",),
    )
    assert window.covers(date(2026, 8, 1)) is True
    assert window.covers(date(2026, 8, 31)) is True
    assert window.covers(date(2026, 8, 15)) is True
    assert window.covers(date(2026, 7, 31)) is False
    assert window.covers(date(2026, 9, 1)) is False
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'src.regulatory'`.

- [ ] **Step 4: Write the implementation**

Create `src/regulatory/__init__.py` (empty, package marker).

Create `src/regulatory/veda_calendar.py`:

```python
"""SUBPESCA closed-season (veda) calendar -- self-contained (D1).

src/risk_period.py has no date/calendar concept at all (confirmed: 49 lines
of pure Vandeput demand-over-lead-time scalar math, zero datetime imports) --
this module does not import it and does not extend it. A veda window is
modeled with plain absolute dates, not a month-day recurrence rule; see the
plan's Task 1 for why recurrence math is deliberately not built.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Sequence

import yaml

DEFAULT_VEDA_CONFIG_PATH = Path(
    os.environ.get("LINCHPIN_VEDA_CONFIG_PATH", "").strip() or "config/regulatory/cl_vedas.yaml"
)


@dataclass(frozen=True)
class VedaWindow:
    especie: str
    tipo: str  # "extractiva" | "biologica" | "sanitaria"
    desde: date
    hasta: date
    ambito: str
    decreto: str
    fuente: str
    verificado_el: date
    prohibe: tuple[str, ...]
    exencion: str = ""
    nota: str = ""

    def covers(self, on: date) -> bool:
        """Inclusive on both ends -- the closure applies on its first and last day."""
        return self.desde <= on <= self.hasta

    def is_stale(self, *, on: date, max_age_days: int = 90) -> bool:
        return (on - self.verificado_el).days > max_age_days


@dataclass(frozen=True)
class VedaExposure:
    especie: str
    window: VedaWindow
    exposed: bool
    stale: bool


def load_veda_calendar(path: str | Path = DEFAULT_VEDA_CONFIG_PATH) -> tuple[VedaWindow, ...]:
    with open(path, encoding="utf-8") as f:
        rows = yaml.safe_load(f)
    windows = []
    for row in rows:
        windows.append(
            VedaWindow(
                especie=row["especie"],
                tipo=row["tipo"],
                desde=date.fromisoformat(row["desde"]),
                hasta=date.fromisoformat(row["hasta"]),
                ambito=row["ambito"],
                decreto=row["decreto"],
                fuente=row["fuente"],
                verificado_el=date.fromisoformat(row["verificado_el"]),
                prohibe=tuple(row["prohibe"]),
                exencion=row.get("exencion", ""),
                nota=row.get("nota", ""),
            )
        )
    return tuple(windows)
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py -q
```

Expected: `5 passed`.

- [ ] **Step 6: Confirm lint is clean**

```bash
ruff check src tests
```

Expected: `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add config/regulatory/cl_vedas.yaml src/regulatory/ tests/test_veda_calendar.py
git commit -m "feat(regulatory): veda dataset + VedaWindow, self-contained (D1)

10 hand-curated rows covering the species that touch the radiografia's 5
sujetos: loco, ostion del norte (2 rows -- extractiva + marea roja sanitaria),
merluza austral, merluza comun, camaron nailon, langostino (2 rows), chorito,
macha. Every approximated date is marked with a nota, never presented as more
certain than the 2026-07-24 research pass actually confirmed. Absolute dates,
no recurrence engine -- verificado_el + the coming staleness check (Task 3)
is what forces a human to re-date a recurring closure, not month-day math."
```

---

### Task 2: `exposure_for_especie` -- the four canonical cases from the spec's own testing section

**Files:**
- Modify: `src/regulatory/veda_calendar.py` (append)
- Modify: `tests/test_veda_calendar.py` (append)

**Interfaces:**
- Consumes: `VedaWindow`/`VedaExposure` (Task 1).
- Produces: `exposure_for_especie(especie, *, calendar, on) -> tuple[VedaExposure, ...]`. PR-3's job calls this once per offer, passing the `especie` string already extracted by PR-1's lexicon (Task 5) -- **not** a raw title. This is a deliberate decoupling from the spec's own wording ("recibe titulos de SKU"): species detection is the lexicon's job (it already has to solve this problem for matching), and re-solving it here with a second vocabulary would let the two drift apart. `veda_calendar.py` stays import-free of `lexicon.py` either way -- the caller does the one function call in between.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_veda_calendar.py`:

```python
from src.regulatory.veda_calendar import exposure_for_especie

ON = date(2026, 8, 15)


def test_exposure_for_especie_sku_inside_window() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    exposures = exposure_for_especie("merluza austral", calendar=calendar, on=ON)
    assert len(exposures) == 1
    assert exposures[0].exposed is True
    assert exposures[0].stale is False


def test_exposure_for_especie_sku_outside_window() -> None:
    # merluza comun's window is September; ON is August 15.
    calendar = load_veda_calendar(DATASET_PATH)
    exposures = exposure_for_especie("merluza comun", calendar=calendar, on=ON)
    assert len(exposures) == 1
    assert exposures[0].exposed is False


def test_exposure_for_especie_no_veda_registered() -> None:
    # merluza hubbsi has no row in the dataset at all -- distinct from "outside window".
    calendar = load_veda_calendar(DATASET_PATH)
    exposures = exposure_for_especie("merluza hubbsi", calendar=calendar, on=ON)
    assert exposures == ()


def test_exposure_for_especie_none_returns_empty() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    assert exposure_for_especie(None, calendar=calendar, on=ON) == ()


def test_exposure_for_especie_returns_one_row_per_matching_especie_entry() -> None:
    # ostion del norte has 2 rows (extractiva + sanitaria) -- both come back,
    # regardless of whether ON currently falls inside either window. A "no
    # aplica hoy, pero esta especie SI tiene veda vigente en el ano" signal
    # is real regulatory context a documentary-obligation report should show.
    calendar = load_veda_calendar(DATASET_PATH)
    exposures = exposure_for_especie("ostion del norte", calendar=calendar, on=ON)
    assert len(exposures) == 2
    assert {e.window.tipo for e in exposures} == {"extractiva", "sanitaria"}
    # extractiva (2022-08-01 .. 2027-08-31) covers ON; sanitaria (Jan 2026) does not.
    extractiva = next(e for e in exposures if e.window.tipo == "extractiva")
    sanitaria = next(e for e in exposures if e.window.tipo == "sanitaria")
    assert extractiva.exposed is True
    assert sanitaria.exposed is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py -q
```

Expected: `ImportError: cannot import name 'exposure_for_especie'`.

- [ ] **Step 3: Write the implementation**

Append to `src/regulatory/veda_calendar.py`:

```python
def exposure_for_especie(
    especie: str | None, *, calendar: Sequence[VedaWindow], on: date
) -> tuple[VedaExposure, ...]:
    """Every VedaWindow whose especie matches exactly (case/whitespace-normalized),
    regardless of whether `on` currently falls inside it -- an especie with a
    veda that isn't active today is still relevant regulatory context, not a
    non-match. Empty tuple means this especie has no row in the calendar at
    all, which is a materially different signal from "has a row, not active
    today" -- never conflate the two in a report."""
    if not especie:
        return ()
    normalized = especie.strip().lower()
    matches = [w for w in calendar if w.especie.strip().lower() == normalized]
    return tuple(
        VedaExposure(especie=w.especie, window=w, exposed=w.covers(on), stale=w.is_stale(on=on))
        for w in matches
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py -q
```

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/regulatory/veda_calendar.py tests/test_veda_calendar.py
git commit -m "feat(regulatory): exposure_for_especie -- the 4 spec-mandated cases

Inside window, outside window, no veda registered for this especie, and
(Task 3) stale verificado_el -- exposure_for_especie takes an already-
extracted especie string, not a raw title: species detection stays the
lexicon's single responsibility (D-adjacent decoupling, keeps veda_calendar.py
free of any dependency on pricing_intel)."
```

---

### Task 3: staleness -- the live macha case

**Files:**
- Modify: `tests/test_veda_calendar.py` (append only -- `is_stale`/`stale_windows`-shaped functionality already exists on `VedaWindow` from Task 1; this task adds the batch helper and pins the real dataset's live expiring row.)
- Modify: `src/regulatory/veda_calendar.py` (append `stale_windows`)

**Interfaces:**
- Consumes: `VedaWindow.is_stale` (Task 1).
- Produces: `stale_windows(calendar, *, on, max_age_days=90) -> tuple[VedaWindow, ...]`. PR-3's job (Task 4) surfaces this as a report-level warning list, separate from any single offer's exposure.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_veda_calendar.py`:

```python
from src.regulatory.veda_calendar import stale_windows


def test_stale_windows_empty_when_everything_was_verified_recently() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    # Every row's verificado_el is 2026-07-24; 3 weeks later is nowhere near 90 days.
    assert stale_windows(calendar, on=date(2026, 8, 15)) == ()


def test_stale_windows_flags_rows_past_90_days() -> None:
    calendar = load_veda_calendar(DATASET_PATH)
    # 2026-07-24 -> 2026-11-01 is 100 days; every row in the dataset shares
    # the same verificado_el, so all 10 come back.
    stale = stale_windows(calendar, on=date(2026, 11, 1))
    assert len(stale) == 10


def test_macha_window_no_longer_covers_the_day_after_it_expires() -> None:
    # The live case the handoff calls out by name: macha vence 2026-08-02,
    # sin decreto de renovacion registrado. This is NOT a staleness check --
    # it's the window's own hasta date, which the report must respect exactly
    # (never silently extend a closed window past its stated end).
    calendar = load_veda_calendar(DATASET_PATH)
    macha = next(w for w in calendar if w.especie == "macha")
    assert macha.covers(date(2026, 8, 2)) is True
    assert macha.covers(date(2026, 8, 3)) is False
    assert "sin decreto de renovacion" in macha.nota
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py -q
```

Expected: `ImportError: cannot import name 'stale_windows'`.

- [ ] **Step 3: Write the implementation**

Append to `src/regulatory/veda_calendar.py`:

```python
def stale_windows(
    calendar: Sequence[VedaWindow], *, on: date, max_age_days: int = 90
) -> tuple[VedaWindow, ...]:
    """Rows whose verificado_el is more than max_age_days old as of `on`.
    A report-level warning, independent of any single offer's exposure --
    this dataset is hand-curated (design.md's own framing) and needs a human
    to re-check it periodically, exactly the freshness discipline the spec
    calls for instead of month-day recurrence math (Task 1)."""
    return tuple(w for w in calendar if w.is_stale(on=on, max_age_days=max_age_days))
```

- [ ] **Step 4: Run the test to verify it passes, then the full veda suite**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py -q
```

Expected: `13 passed`.

- [ ] **Step 5: Confirm lint is clean and commit**

```bash
ruff check src tests
git add src/regulatory/veda_calendar.py tests/test_veda_calendar.py
git commit -m "feat(regulatory): stale_windows -- batch freshness check

A report-level warning list, independent of any single SKU's exposure. Pins
the macha row's own hasta boundary (2026-08-02, no renewal decree on record
as of the 2026-07-24 verification) as a hard covers()-boundary test, distinct
from the staleness check itself."
```

- [ ] **Step 6: Open the PR**

```bash
git push -u origin feat/radiografia-cl-veda-calendar
gh pr create --draft --base main --title "feat(regulatory): SUBPESCA veda calendar (radiografia PR-2)" --body "$(cat <<'EOF'
## Summary
- config/regulatory/cl_vedas.yaml: 10 hand-curated rows, every approximated date flagged with a nota
- src/regulatory/veda_calendar.py: VedaWindow/VedaExposure, exposure_for_especie, stale_windows -- fully self-contained (D1), zero dependency on src/risk_period.py or pricing_intel

## Test plan
- [ ] `PYTHONPATH=. pytest tests/test_veda_calendar.py -q` -- 13 passed
- [ ] `ruff check src tests` clean
- [ ] No dependency on PR-1 -- mergeable independently, in either order
EOF
)"
```

---

# PR-3: `market_radiography` -- the deliverable, and the 8th place a new tool touches

**Branch:** `feat/radiografia-cl-market-radiography` off `origin/main`, opened **only after PR-1 and PR-2 are both merged** -- this is a hard dependency, not a convenience. `market_radiography.py` imports `NO_BRAND_DECLARED`/`count_concordant_signals`/`ES_CL_DECISIVE_KEYS` from PR-1's `lexicon.py` and calls PR-2's `veda_calendar.exposure_for_especie`; there is no way to write or test this PR against `origin/main` before both land.

**Deliverable:** `python examples/run_market_radiography.py --normalized data/cl_scan/normalized.csv --out data/cl_radiography/` reads PR-1's normalized CSV + PR-2's veda calendar, clusters comparable offers across sources, computes each sujeto's price-per-kg gap against the market median, overlays veda exposure per offer, and writes 5 per-subject reports + 1 sector benchmark. Registered as the `market_radiography` agent tool.

- [ ] **Task 0: Branch setup**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git fetch origin
git log origin/main --oneline -5   # confirm PR-1 and PR-2's commits are present on main before branching
git checkout -b feat/radiografia-cl-market-radiography origin/main
```

Expected: PR-1's `run_cl_catalog_scan.py` commit and PR-2's `stale_windows` commit both appear in the `git log` output. If either is missing, stop -- this branch cannot be written or tested yet.

---

### Task 1: `src/market_radiography.py` -- `RadiographyOffer`, N-way clustering (D7), median/gap math

**Files:**
- Create: `src/market_radiography.py`
- Create: `tests/test_market_radiography.py`

**Interfaces:**
- Consumes: `NO_BRAND_DECLARED`, `ES_CL_DECISIVE_KEYS`, `count_concordant_signals` (PR-1's `lexicon.py`); `ProductAttributes`, `block_candidates`, `BLOCKING_THRESHOLD` (`match/fuzzy.py`); `score_pair`, `CONFIRM_THRESHOLD`, `is_in_adjudication_band` (`match/probabilistic.py`); `AdjudicationRequest`, `adjudicate_pair` (`match/adjudicate.py`).
- Produces: `RadiographyOffer`, `MarketCluster`, `SubjectGap`, `CoverageStats`, `cluster_offers(offers, *, llm=None) -> tuple[MarketCluster, ...]`, `subject_gaps(clusters, *, subjects) -> tuple[SubjectGap, ...]`. Task 2 (the job's `prepare`) produces `RadiographyOffer`s from the CSV; Task 4 (`run`) calls `cluster_offers`/`subject_gaps`.

`ProbabilisticScore` (`probabilistic.py:134-147`) was re-read directly and confirmed verbatim: `our`, `competitor`, `title_similarity`, `brand_similarity`, `attribute_similarity`, `attribute_conflict`, `conflicting_attributes`, `score` -- `result.score` below is correct as written, not a guess.

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_radiography.py`:

```python
"""market_radiography.py -- N-way peer clustering (D7) and gap math.

llm=None throughout (the production default): a pair only clusters if it
reaches CONFIRM_THRESHOLD outright, or if an LLM adjudicator is wired in and
returns "same" for a pair in the adjudication band. Without an LLM, an
ambiguous pair is left unclustered, never guessed -- the same
quarantine-over-guessing discipline the CLP/kg normalizer applies, extended
to matching itself. Wiring a real LLM adjudicator is an explicit,
Fuera-de-alcance follow-up (see the plan's Self-Review), not a gap in this
task.
"""
from __future__ import annotations

from decimal import Decimal

from src.market_radiography import CoverageStats, RadiographyOffer, cluster_offers, coverage_stats, subject_gaps

SUBJECTS = frozenset({"rapa-nui-catalog.test", "todofrio-catalog.test"})


def _offer(source: str, sku: str, title: str, price_per_kg, especie=None, calibre=None, estado=None, proceso=None, reason: str = "") -> RadiographyOffer:
    return RadiographyOffer(
        source=source, sku=sku, title=title, price_clp=Decimal(price_per_kg) if price_per_kg else Decimal("0"),
        price_per_kg=Decimal(price_per_kg) if price_per_kg is not None else None, quarantine_reason=reason,
        especie=especie, calibre=calibre, estado=estado, proceso=proceso, origen=None,
        url=f"https://{source}/{sku}",
    )


def test_cluster_offers_unions_a_confident_cross_source_pair() -> None:
    offers = [
        _offer("rapa-nui-catalog.test", "MAR1004", "Camaron 36/40 crudo pelado 1kg", "15990",
               especie="camaron", calibre="36/40", estado="crudo", proceso="pelado"),
        _offer("frozt-catalog.test", "FRZ-CAM-3640", "Camaron 36/40 crudo pelado", "13990",
               especie="camaron", calibre="36/40", estado="crudo", proceso="pelado"),
    ]
    clusters = cluster_offers(offers)
    assert len(clusters) == 1
    assert len(clusters[0].members) == 2


def test_cluster_offers_never_unions_two_offers_from_the_same_source() -> None:
    offers = [
        _offer("rapa-nui-catalog.test", "A", "Camaron 36/40 crudo pelado 1kg", "15990",
               especie="camaron", calibre="36/40", estado="crudo", proceso="pelado"),
        _offer("rapa-nui-catalog.test", "B", "Camaron 36/40 crudo pelado 1kg", "15990",
               especie="camaron", calibre="36/40", estado="crudo", proceso="pelado"),
    ]
    assert cluster_offers(offers) == ()  # identical titles, same source -- never a "market" of one seller


def test_cluster_offers_excludes_quarantined_offers() -> None:
    offers = [
        _offer("rapa-nui-catalog.test", "A", "Camaron 36/40 crudo pelado 1kg", "15990",
               especie="camaron", calibre="36/40", estado="crudo", proceso="pelado"),
        _offer("frozt-catalog.test", "B", "Camaron 36/40 crudo pelado", None,
               especie="camaron", calibre="36/40", estado="crudo", proceso="pelado", reason="formato de peso no reconocido"),
    ]
    assert cluster_offers(offers) == ()  # the second offer has no price_per_kg -- can never anchor a cluster


def test_cluster_offers_requires_two_concordant_signals_even_with_a_similar_title() -> None:
    # Same especie only (1 signal) -- calibre disagrees. The spec's own floor:
    # never confirm on string similarity alone.
    offers = [
        _offer("rapa-nui-catalog.test", "A", "Camaron 36/40 crudo pelado 1kg", "15990", especie="camaron", calibre="36/40"),
        _offer("frozt-catalog.test", "B", "Camaron 26/30 crudo pelado 1kg", "14990", especie="camaron", calibre="26/30"),
    ]
    assert cluster_offers(offers) == ()


def test_cluster_offers_median_price_per_kg_is_decimal_safe() -> None:
    offers = [
        _offer("a-catalog.test", "1", "Reineta filete 1kg", "11768", especie="reineta", proceso="filete"),
        _offer("b-catalog.test", "2", "Reineta filete 1kg", "12000", especie="reineta", proceso="filete"),
        _offer("c-catalog.test", "3", "Reineta filete 1kg", "10500", especie="reineta", proceso="filete"),
    ]
    clusters = cluster_offers(offers)
    assert len(clusters) == 1
    assert clusters[0].median_price_per_kg == Decimal("11768")  # middle of 10500, 11768, 12000


def test_subject_gaps_computes_a_positive_and_negative_gap() -> None:
    offers = [
        _offer("rapa-nui-catalog.test", "A", "Reineta filete 1kg", "11768", especie="reineta", proceso="filete"),  # subject
        _offer("frozt-catalog.test", "B", "Reineta filete 1kg", "10000", especie="reineta", proceso="filete"),
        _offer("southwind-catalog.test", "C", "Reineta filete 1kg", "10000", especie="reineta", proceso="filete"),
    ]
    clusters = cluster_offers(offers)
    gaps = subject_gaps(clusters, subjects=frozenset({"rapa-nui-catalog.test"}))
    assert len(gaps) == 1
    assert gaps[0].subject == "rapa-nui-catalog.test"
    assert gaps[0].market_median_price_per_kg == Decimal("10000")
    assert gaps[0].gap_pct == Decimal("17.68")  # (11768 - 10000) / 10000 * 100, quantized to 2dp in the report layer


def test_subject_gaps_skips_clusters_with_no_subject_member() -> None:
    offers = [
        _offer("frozt-catalog.test", "A", "Reineta filete 1kg", "10000", especie="reineta", proceso="filete"),
        _offer("southwind-catalog.test", "B", "Reineta filete 1kg", "10500", especie="reineta", proceso="filete"),
    ]
    clusters = cluster_offers(offers)
    assert subject_gaps(clusters, subjects=SUBJECTS) == ()


def test_coverage_stats_totals() -> None:
    offers = [
        _offer("rapa-nui-catalog.test", "A", "Reineta filete 1kg", "11768", especie="reineta", proceso="filete"),
        _offer("frozt-catalog.test", "B", "Surtido de mariscos", None, reason="formato de peso no reconocido"),
    ]
    stats = coverage_stats(offers)
    assert stats.total_offers == 2
    assert stats.comparable_offers == 1
    assert stats.quarantined_offers == 1
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography.py -q
```

Expected: `ModuleNotFoundError: No module named 'src.market_radiography'`.

- [ ] **Step 3: Write the implementation**

Create `src/market_radiography.py`:

```python
"""Pure cluster / median / gap math for the CL frozen-food radiografia.

N-way peer clustering across up to 9 equal sources -- not the bilateral "our
catalog vs one discovered competitor" shape homologate.homologate() provides
(D7). Every pairwise confirmation additionally requires the spec's own
two-concordant-signal floor (count_concordant_signals >= MIN_CONCORDANT_SIGNALS)
on top of the probabilistic score threshold -- a coincidentally-similar title
alone never confirms a match in a market with no GTIN and no brand.

No I/O, no pandas -- jobs/market_radiography_job.py (Task 2-4) is the only
caller and owns all file access.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations
from typing import Sequence

from src.pricing_intel.lexicon import NO_BRAND_DECLARED, count_concordant_signals
from src.pricing_intel.match.adjudicate import AdjudicationRequest, adjudicate_pair
from src.pricing_intel.match.fuzzy import BLOCKING_THRESHOLD, ProductAttributes, block_candidates
from src.pricing_intel.match.probabilistic import CONFIRM_THRESHOLD, is_in_adjudication_band, score_pair

MIN_CONCORDANT_SIGNALS = 2  # design.md: "al menos dos senales concordantes, nunca solo similitud de string"


@dataclass(frozen=True)
class RadiographyOffer:
    """One row of PR-1's normalized CSV, read back by the job (Task 2)."""

    source: str
    sku: str
    title: str
    price_clp: Decimal
    price_per_kg: Decimal | None  # None => quarantined; see quarantine_reason
    quarantine_reason: str
    especie: str | None
    calibre: str | None
    estado: str | None
    proceso: str | None
    origen: str | None
    url: str


@dataclass(frozen=True)
class MarketCluster:
    cluster_id: str
    members: tuple[RadiographyOffer, ...]
    median_price_per_kg: Decimal


@dataclass(frozen=True)
class SubjectGap:
    subject: str
    sku: str
    cluster_id: str
    subject_price_per_kg: Decimal
    market_median_price_per_kg: Decimal
    gap_pct: Decimal
    cluster_size: int


@dataclass(frozen=True)
class CoverageStats:
    total_offers: int
    comparable_offers: int
    quarantined_offers: int


def _decimal_median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / Decimal(2)


def _to_product_attributes(offer: RadiographyOffer) -> ProductAttributes:
    attrs: dict[str, str] = {}
    if offer.especie:
        attrs["especie"] = offer.especie
    if offer.calibre:
        attrs["calibre"] = offer.calibre
    if offer.estado:
        attrs["estado"] = offer.estado
    if offer.proceso:
        attrs["proceso"] = offer.proceso
    return ProductAttributes(
        product_id=f"{offer.source}:{offer.sku}", title=offer.title, brand=NO_BRAND_DECLARED, attributes=attrs
    )


def cluster_offers(offers: Sequence[RadiographyOffer], *, llm=None) -> tuple[MarketCluster, ...]:
    """Union-find over pairwise adjudication verdicts across every source pair.

    Only comparable offers (price_per_kg is not None) enter clustering -- a
    quarantined offer can neither anchor nor join a market comparison. Two
    offers from the SAME source are never compared to each other (this is a
    cross-source comparison by definition). A cluster of size 1 has no market
    to compare against and is dropped, not reported as a degenerate cluster.
    """
    comparable = [o for o in offers if o.price_per_kg is not None]
    attrs = [_to_product_attributes(o) for o in comparable]
    id_to_idx = {a.product_id: i for i, a in enumerate(attrs)}
    parent = list(range(len(comparable)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    by_source: dict[str, list[int]] = {}
    for idx, offer in enumerate(comparable):
        by_source.setdefault(offer.source, []).append(idx)

    for source_a, source_b in combinations(sorted(by_source), 2):
        our = [attrs[i] for i in by_source[source_a]]
        competitor = [attrs[i] for i in by_source[source_b]]
        for candidate in block_candidates(our, competitor, threshold=BLOCKING_THRESHOLD):
            i = id_to_idx[candidate.our.product_id]
            j = id_to_idx[candidate.competitor.product_id]
            if count_concordant_signals(attrs[i].attributes, attrs[j].attributes) < MIN_CONCORDANT_SIGNALS:
                continue
            result = score_pair(attrs[i], attrs[j])
            if result.score >= CONFIRM_THRESHOLD:
                union(i, j)
            elif is_in_adjudication_band(result.score):
                request = AdjudicationRequest(our=attrs[i], competitor=attrs[j], probabilistic_score=result.score)
                verdict = adjudicate_pair(request, llm=llm)
                if verdict.verdict == "same":
                    union(i, j)

    groups: dict[int, list[int]] = {}
    for idx in range(len(comparable)):
        groups.setdefault(find(idx), []).append(idx)

    clusters = []
    for root, idxs in groups.items():
        if len(idxs) < 2:
            continue
        members = tuple(comparable[i] for i in idxs)
        median = _decimal_median(tuple(m.price_per_kg for m in members))
        clusters.append(MarketCluster(cluster_id=f"cluster-{root}", members=members, median_price_per_kg=median))
    return tuple(clusters)


def subject_gaps(clusters: Sequence[MarketCluster], *, subjects: frozenset[str]) -> tuple[SubjectGap, ...]:
    """One SubjectGap per (cluster, member) pair where the member's source is
    one of the 5 sujetos. The median is the whole cluster's median (subject
    included) -- this is a market position, not a "everyone but you" figure."""
    gaps = []
    for cluster in clusters:
        for member in cluster.members:
            if member.source not in subjects:
                continue
            gap_pct = (member.price_per_kg - cluster.median_price_per_kg) / cluster.median_price_per_kg * 100
            gaps.append(
                SubjectGap(
                    subject=member.source, sku=member.sku, cluster_id=cluster.cluster_id,
                    subject_price_per_kg=member.price_per_kg, market_median_price_per_kg=cluster.median_price_per_kg,
                    gap_pct=gap_pct, cluster_size=len(cluster.members),
                )
            )
    return tuple(gaps)


def coverage_stats(offers: Sequence[RadiographyOffer]) -> CoverageStats:
    comparable = sum(1 for o in offers if o.price_per_kg is not None)
    return CoverageStats(
        total_offers=len(offers), comparable_offers=comparable, quarantined_offers=len(offers) - comparable
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography.py -q
```

Expected: `8 passed`. If `test_subject_gaps_computes_a_positive_and_negative_gap` fails on the exact `Decimal("17.68")` value, the gap math itself is right but unquantized -- either quantize with `.quantize(Decimal("0.01"))` in `subject_gaps` (report-layer rounding, same principle `price_per_kilogram` already established: only quantize where a human reads it) or adjust the test's expected value to the unquantized quotient; do not change the formula.

- [ ] **Step 5: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/test_pricing_intel_match_fuzzy.py tests/test_pricing_intel_match_probabilistic.py tests/test_pricing_intel_match_adjudicate.py tests/test_es_cl_lexicon.py -q
ruff check src tests
```

- [ ] **Step 6: Commit**

```bash
git add src/market_radiography.py tests/test_market_radiography.py
git commit -m "feat(market-radiography): N-way clustering + gap math (D7)

Pairwise cascade (block_candidates -> score_pair -> adjudicate_pair) across
every source pair, unioned with a local union-find -- not homologate()'s
bilateral orchestration, which cannot express 9 equal peers (D7). Every
confirmation requires >= 2 concordant es-CL signals in addition to the
probabilistic score threshold, the spec's own floor against false positives
in a market with no GTIN and no brand."
```

---

### Task 2: `jobs/market_radiography_job.py` -- `prepare` / `run` / `verify`

**Files:**
- Create: `jobs/market_radiography_job.py`
- Create: `tests/test_market_radiography_job.py`

**Interfaces:**
- Consumes: `RadiographyOffer`/`cluster_offers`/`subject_gaps`/`coverage_stats` (Task 1); `load_veda_calendar`/`exposure_for_especie`/`stale_windows` (PR-2).
- Produces: `MarketRadiographyReport`, `SubjectVedaRow`, `prepare(data_path, params=None) -> list[RadiographyOffer]`, `run(offers, *, subjects=..., veda_calendar_path=None, on=None, llm=None) -> MarketRadiographyReport`, `verify(report) -> list[str]`. Task 3 consumes `MarketRadiographyReport`; Task 4's tool wiring consumes all three functions, matching `disruption_scan_job.py`'s own `prepare`/`run`/`verify` shape exactly (the established template).

`prepare` reads its own CSV with pandas directly, **not** via `jobs/intake.py` -- same discipline `disruption_scan_job.py`/`launch_readiness_job.py` both already follow. Nothing in this file makes a network call (D3).

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_radiography_job.py`:

```python
"""jobs/market_radiography_job.py -- offline, deterministic (D3: no network,
no re-fetch of any PDP). prepare() reads PR-1's normalized CSV; run() clusters
+ overlays PR-2's veda calendar; verify() is the QA gate."""
from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from jobs.market_radiography_job import MarketRadiographyReport, prepare, run, verify
from src.market_radiography import RadiographyOffer

NORMALIZED_COLUMNS = (
    "source", "sku", "title", "price_clp", "currency", "in_stock", "url",
    "price_per_kg", "quarantine_reason", "especie", "calibre", "estado", "proceso", "origen",
)


def _write_normalized_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _row(source, sku, title, price_per_kg, especie="", calibre="", estado="", proceso="", reason=""):
    return {
        "source": source, "sku": sku, "title": title, "price_clp": price_per_kg or "0",
        "currency": "CLP", "in_stock": "True", "url": f"https://{source}/{sku}",
        "price_per_kg": price_per_kg or "", "quarantine_reason": reason,
        "especie": especie, "calibre": calibre, "estado": estado, "proceso": proceso, "origen": "",
    }


def test_prepare_reads_the_normalized_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(csv_path, [_row("rapa-nui-catalog.test", "A", "Reineta filete 1kg", "11768", especie="reineta", proceso="filete")])
    offers = prepare(csv_path)
    assert len(offers) == 1
    assert isinstance(offers[0], RadiographyOffer)
    assert offers[0].price_per_kg == Decimal("11768")


def test_prepare_treats_blank_price_per_kg_as_quarantined(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(csv_path, [_row("rapa-nui-catalog.test", "A", "Surtido de mariscos", "", reason="formato de peso no reconocido")])
    offers = prepare(csv_path)
    assert offers[0].price_per_kg is None
    assert offers[0].quarantine_reason == "formato de peso no reconocido"


def test_prepare_raises_on_an_empty_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(csv_path, [])
    with pytest.raises(ValueError, match="no rows"):
        prepare(csv_path)


def test_run_produces_subject_gaps_and_veda_exposure(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(
        csv_path,
        [
            _row("subject-catalog.test", "A", "Merluza Austral Filete 1kg", "9000", especie="merluza austral", proceso="filete"),
            _row("ref1-catalog.test", "B", "Merluza Austral Filete 1kg", "8500", especie="merluza austral", proceso="filete"),
            _row("ref2-catalog.test", "C", "Merluza Austral Filete 1kg", "8700", especie="merluza austral", proceso="filete"),
        ],
    )
    offers = prepare(csv_path)
    report = run(offers, subjects=frozenset({"subject-catalog.test"}), on=date(2026, 8, 15))
    assert isinstance(report, MarketRadiographyReport)
    assert len(report.gaps) == 1
    assert report.gaps[0].subject == "subject-catalog.test"
    assert len(report.veda_rows) == 1
    assert report.veda_rows[0].exposure.exposed is True  # merluza austral, Aug 15 falls inside Aug 1-31


def test_run_is_deterministic_given_the_same_inputs(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(
        csv_path,
        [
            _row("subject-catalog.test", "A", "Reineta filete 1kg", "11768", especie="reineta", proceso="filete"),
            _row("ref-catalog.test", "B", "Reineta filete 1kg", "10000", especie="reineta", proceso="filete"),
        ],
    )
    offers = prepare(csv_path)
    report_a = run(offers, subjects=frozenset({"subject-catalog.test"}), on=date(2026, 8, 15))
    report_b = run(offers, subjects=frozenset({"subject-catalog.test"}), on=date(2026, 8, 15))
    assert report_a.gaps == report_b.gaps


def test_verify_flags_zero_offers() -> None:
    empty_report = run([], subjects=frozenset(), on=date(2026, 8, 15))
    assert any("no offers" in issue for issue in verify(empty_report))


def test_verify_flags_clusters_with_zero_subject_gaps() -> None:
    # Every offer is a "referencia" source, no subject present -- clusters
    # form, but subject_gaps comes back empty. This is the exact symptom of
    # SUBJECT_DOMAINS still holding the config/cl_catalog_sources.yaml
    # REPLACE_ME_* placeholders (PR-1 Task 6, Step 0 not done).
    offers = [
        RadiographyOffer(source="ref1.test", sku="A", title="Reineta filete 1kg", price_clp=Decimal("10000"),
                          price_per_kg=Decimal("10000"), quarantine_reason="", especie="reineta", calibre=None,
                          estado=None, proceso="filete", origen=None, url="https://ref1.test/A"),
        RadiographyOffer(source="ref2.test", sku="B", title="Reineta filete 1kg", price_clp=Decimal("10500"),
                          price_per_kg=Decimal("10500"), quarantine_reason="", especie="reineta", calibre=None,
                          estado=None, proceso="filete", origen=None, url="https://ref2.test/B"),
    ]
    report = run(offers, subjects=frozenset({"a-subject-domain-not-present.test"}), on=date(2026, 8, 15))
    assert report.clusters  # the two references DID cluster with each other
    assert not report.gaps  # but no subject is among them
    assert any("REPLACE_ME" in issue or "SUBJECT_DOMAINS" in issue for issue in verify(report))
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_job.py -q
```

Expected: `ModuleNotFoundError: No module named 'jobs.market_radiography_job'`.

- [ ] **Step 3: Write the implementation**

Create `jobs/market_radiography_job.py`:

```python
"""CL frozen-food sector radiography -- Kern tool #47.

prepare() reads PR-1's normalized CSV directly with pandas (deliberately NOT
via jobs/intake.py -- same convention disruption_scan_job.py and
launch_readiness_job.py both follow). run() clusters comparable offers
(src.market_radiography, D7), computes each sujeto's price-per-kg gap against
the market median, and overlays PR-2's veda calendar per subject SKU. Fully
offline and deterministic: no network call happens anywhere in this file, and
feeding the same normalized CSV + veda calendar always produces the same
report (D3).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from src.market_radiography import (
    CoverageStats,
    MarketCluster,
    RadiographyOffer,
    SubjectGap,
    cluster_offers,
    coverage_stats,
    subject_gaps,
)
from src.regulatory.veda_calendar import VedaExposure, VedaWindow, exposure_for_especie, load_veda_calendar, stale_windows

# Filled in once PR-1 Task 6 Step 0 confirms the 5 real sujeto domains --
# placeholder keys mirror config/cl_catalog_sources.yaml's REPLACE_ME_* rows.
# verify() below flags the symptom if this is still unfilled at execution time.
SUBJECT_DOMAINS: frozenset[str] = frozenset(
    {
        "REPLACE_ME_rapa_nui",
        "REPLACE_ME_pulpo_chile",
        "REPLACE_ME_todofrio",
        "REPLACE_ME_del_mar_a_tu_hogar",
        "REPLACE_ME_mariscos_de_la_concha",
    }
)


@dataclass(frozen=True)
class SubjectVedaRow:
    subject: str
    sku: str
    especie: str
    exposure: VedaExposure


@dataclass(frozen=True)
class MarketRadiographyReport:
    coverage: CoverageStats
    clusters: tuple[MarketCluster, ...]
    gaps: tuple[SubjectGap, ...]
    veda_rows: tuple[SubjectVedaRow, ...]
    stale_veda_windows: tuple[VedaWindow, ...]
    summary: str


def _parse_decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def prepare(data_path: str | Path, params: dict | None = None) -> list[RadiographyOffer]:
    """Reads PR-1's normalized CSV. Every column is read as `str` (dtype=str)
    so a blank price_per_kg cell round-trips as an empty string, not NaN --
    _parse_decimal treats both as None (quarantined), never a guessed 0."""
    df = pd.read_csv(data_path, dtype=str, keep_default_na=False)
    offers = [
        RadiographyOffer(
            source=row["source"], sku=row["sku"], title=row["title"],
            price_clp=_parse_decimal(row["price_clp"]) or Decimal("0"),
            price_per_kg=_parse_decimal(row["price_per_kg"]),
            quarantine_reason=row["quarantine_reason"],
            especie=row["especie"] or None, calibre=row["calibre"] or None,
            estado=row["estado"] or None, proceso=row["proceso"] or None,
            origen=row["origen"] or None, url=row["url"],
        )
        for _, row in df.iterrows()
    ]
    if not offers:
        raise ValueError("normalized CSV has no rows")
    return offers


def run(
    offers: list[RadiographyOffer],
    *,
    subjects: frozenset[str] = SUBJECT_DOMAINS,
    veda_calendar_path: str | Path | None = None,
    on: date | None = None,
    llm=None,
) -> MarketRadiographyReport:
    on = on or date.today()
    calendar = load_veda_calendar(veda_calendar_path) if veda_calendar_path else load_veda_calendar()
    clusters = cluster_offers(offers, llm=llm)
    gaps = subject_gaps(clusters, subjects=subjects)
    coverage = coverage_stats(offers)

    veda_rows = [
        SubjectVedaRow(subject=offer.source, sku=offer.sku, especie=offer.especie, exposure=exposure)
        for offer in offers
        if offer.source in subjects and offer.especie
        for exposure in exposure_for_especie(offer.especie, calendar=calendar, on=on)
    ]
    stale = stale_windows(calendar, on=on)
    summary = (
        f"{len(gaps)} subject offers compared across {len(clusters)} market clusters "
        f"({coverage.comparable_offers}/{coverage.total_offers} offers comparable); "
        f"{len(veda_rows)} subject SKUs carry veda exposure."
    )
    return MarketRadiographyReport(
        coverage=coverage, clusters=clusters, gaps=gaps, veda_rows=tuple(veda_rows),
        stale_veda_windows=stale, summary=summary,
    )


def verify(report: MarketRadiographyReport) -> list[str]:
    """QA gate. A thin market (few clusters) is a real finding, never a
    failure by itself -- but an internal inconsistency, or clusters existing
    with zero subject gaps (the SUBJECT_DOMAINS-still-a-placeholder symptom),
    both fail."""
    issues: list[str] = []
    if report.coverage.total_offers <= 0:
        issues.append("no offers to compare")
    if report.coverage.comparable_offers + report.coverage.quarantined_offers != report.coverage.total_offers:
        issues.append("coverage totals are inconsistent")
    for cluster in report.clusters:
        if len(cluster.members) < 2:
            issues.append(f"{cluster.cluster_id}: a cluster with fewer than 2 members should never exist")
    if report.coverage.comparable_offers > 0 and report.clusters and not report.gaps:
        issues.append(
            "clusters exist but zero subject gaps -- SUBJECT_DOMAINS likely still holds "
            "config/cl_catalog_sources.yaml's REPLACE_ME_* placeholders (PR-1 Task 6, Step 0 not done)"
        )
    return issues
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_job.py -q
```

Expected: `7 passed`.

- [ ] **Step 5: Confirm no existing test regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/test_veda_calendar.py tests/test_market_radiography.py -q
ruff check src tests jobs
```

- [ ] **Step 6: Commit**

```bash
git add jobs/market_radiography_job.py tests/test_market_radiography_job.py
git commit -m "feat(market-radiography): prepare/run/verify job -- offline, D3

Mirrors disruption_scan_job.py's own prepare/run/verify shape. prepare()
reads PR-1's normalized CSV with pandas directly, not via intake.py. run()
clusters, computes subject gaps, and overlays PR-2's veda calendar per
subject SKU -- zero network calls anywhere (D3). verify() flags the exact
symptom of SUBJECT_DOMAINS still holding config/cl_catalog_sources.yaml's
REPLACE_ME_* placeholders: clusters form but zero subject gaps come out."
```

---

### Task 3: `write_operational` + the N-per-subject + 1-sector-benchmark deck

**Files:**
- Modify: `jobs/market_radiography_job.py` (append)
- Modify: `tests/test_market_radiography_job.py` (append)

**Interfaces:**
- Consumes: `MarketRadiographyReport` (Task 2); `Deliverable`, `Finding`, `Kpi` (`src/deliverable.py`); `write_summary_csv` (`src/export.py`).
- Produces: `write_operational(report, out_dir, client="Client") -> dict[str, Path]`, `build_subject_deck(report, subject, *, client, prepared, citations, confidence) -> Deliverable`, `build_sector_deck(report, *, client, prepared, citations, confidence) -> Deliverable`, `write_all_decks(report, out_dir, *, client, prepared, citations, confidence, subjects) -> dict[str, Path]`. Task 4's `Tool.deliver`/`Tool.deck` lambdas consume `write_operational`/`write_all_decks` respectively.

**No existing job in this codebase produces N per-subject files + 1 aggregate in one run** (verified: the closest precedents are `jobs/package_deliverable.py`'s many-outcomes-to-one-deck aggregation and `src/seo/pdp_writer.py`'s one-file-per-SKU loop) -- `write_all_decks` below is a new composition of those two half-precedents, not a copy of an existing job.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_market_radiography_job.py`:

```python
from jobs.market_radiography_job import build_sector_deck, build_subject_deck, write_all_decks, write_operational
from src.deliverable import Deliverable


def _sample_report(tmp_path: Path) -> MarketRadiographyReport:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(
        csv_path,
        [
            _row("subject-catalog.test", "A", "Merluza Austral Filete 1kg", "9000", especie="merluza austral", proceso="filete"),
            _row("ref-catalog.test", "B", "Merluza Austral Filete 1kg", "8500", especie="merluza austral", proceso="filete"),
        ],
    )
    offers = prepare(csv_path)
    return run(offers, subjects=frozenset({"subject-catalog.test"}), on=date(2026, 8, 15))


def test_write_operational_writes_one_csv_with_every_gap_row(tmp_path: Path) -> None:
    report = _sample_report(tmp_path)
    written = write_operational(report, tmp_path / "out")
    assert "csv" in written
    assert written["csv"].exists()


def test_build_subject_deck_includes_the_veda_finding(tmp_path: Path) -> None:
    report = _sample_report(tmp_path)
    deck = build_subject_deck(report, "subject-catalog.test")
    assert isinstance(deck, Deliverable)
    assert any("veda" in f.title.lower() or "exposicion" in f.title.lower() for f in deck.findings)
    assert "infrac" not in deck.to_markdown().lower()  # never assert infraction, only exposure (spec doctrine)


def test_build_sector_deck_reports_every_cluster(tmp_path: Path) -> None:
    report = _sample_report(tmp_path)
    deck = build_sector_deck(report)
    assert len(deck.findings) == len(report.clusters)


def test_write_all_decks_writes_one_subdirectory_per_present_subject_plus_sector(tmp_path: Path) -> None:
    report = _sample_report(tmp_path)
    written = write_all_decks(report, tmp_path / "decks", subjects=frozenset({"subject-catalog.test"}))
    assert any("sector" in k for k in written)
    assert any("subject-catalog.test" in k for k in written)
    assert all(p.exists() for p in written.values())


def test_write_all_decks_skips_subjects_with_no_gaps() -> None:
    # A sujeto with zero comparable offers this run produces no subdirectory --
    # never an empty/placeholder report for a subject with nothing to say.
    report = MarketRadiographyReport(
        coverage=CoverageStats(total_offers=0, comparable_offers=0, quarantined_offers=0),
        clusters=(), gaps=(), veda_rows=(), stale_veda_windows=(), summary="no offers",
    )
    written = write_all_decks(report, Path("/tmp/unused"), subjects=frozenset({"never-present.test"}))
    assert not any("never-present.test" in k for k in written)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_job.py -q
```

Expected: `ImportError: cannot import name 'write_operational'`.

- [ ] **Step 3: Write the implementation**

Append to `jobs/market_radiography_job.py`:

```python
from src.deliverable import Deliverable, Finding, Kpi
from src.export import write_summary_csv


def write_operational(report: MarketRadiographyReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "subject": g.subject, "sku": g.sku, "cluster_id": g.cluster_id,
            "subject_price_per_kg": str(g.subject_price_per_kg),
            "market_median_price_per_kg": str(g.market_median_price_per_kg),
            "gap_pct": str(g.gap_pct), "cluster_size": g.cluster_size,
        }
        for g in report.gaps
    ]
    if not rows:
        rows = [{"note": "no subject gaps this run -- see report summary for why"}]
    return {"csv": write_summary_csv(rows, d / "market_radiography.csv")}


def build_subject_deck(
    report: MarketRadiographyReport, subject: str, *, client: str = "Client",
    prepared: str = "", citations: tuple[str, ...] = (), confidence: float = 0.6,
) -> Deliverable:
    subject_gap_rows = [g for g in report.gaps if g.subject == subject]
    subject_veda_rows = [v for v in report.veda_rows if v.subject == subject]

    findings = [
        Finding(
            title=f"{g.sku}: {g.gap_pct:+.1f}% vs mediana de mercado",
            detail=(
                f"Precio propio {g.subject_price_per_kg} CLP/kg vs mediana de mercado "
                f"{g.market_median_price_per_kg} CLP/kg ({g.cluster_size} ofertas cruzadas, {g.cluster_id})."
            ),
        )
        for g in subject_gap_rows
    ]
    for v in subject_veda_rows:
        if not v.exposure.exposed:
            continue
        findings.append(
            Finding(
                title=f"{v.sku}: exposicion a veda -- {v.especie}",
                detail=(
                    f"Veda {v.exposure.window.tipo} vigente ({v.exposure.window.desde} a {v.exposure.window.hasta}), "
                    f"decreto {v.exposure.window.decreto}. Exposicion y obligacion documental -- no se afirma infraccion; "
                    f"la exencion AMERB puede aplicar y su cumplimiento no es observable desde afuera."
                ),
                impact="Revisar acreditacion de origen legal (arts. 63/65 LGPA) si corresponde." if v.exposure.window.tipo == "extractiva" else "",
            )
        )

    kpis = [
        Kpi(name="SKUs comparados", value=str(len(subject_gap_rows))),
        Kpi(name="SKUs con exposicion a veda vigente", value=str(sum(1 for v in subject_veda_rows if v.exposure.exposed))),
    ]
    return Deliverable(
        title=f"Radiografia de mercado -- {subject}", client=client,
        summary=f"{len(subject_gap_rows)} SKUs comparados contra la mediana de mercado; {sum(1 for v in subject_veda_rows if v.exposure.exposed)} con exposicion a veda vigente.",
        findings=tuple(findings), kpis=tuple(kpis), citations=tuple(citations), confidence=confidence,
        prepared=prepared, lang="es",
        residual="Comparacion basada en catalogos publicos de hasta 7 fuentes; no incluye costos, margenes ni volumen real del sujeto.",
    )


def build_sector_deck(
    report: MarketRadiographyReport, *, client: str = "Client",
    prepared: str = "", citations: tuple[str, ...] = (), confidence: float = 0.6,
) -> Deliverable:
    findings = [
        Finding(
            title=f"{cluster.cluster_id}: mediana {cluster.median_price_per_kg} CLP/kg ({len(cluster.members)} ofertas)",
            detail=", ".join(f"{m.source}={m.price_per_kg} CLP/kg" for m in cluster.members),
        )
        for cluster in report.clusters
    ]
    kpis = [
        Kpi(name="Ofertas totales", value=str(report.coverage.total_offers)),
        Kpi(name="Ofertas comparables", value=str(report.coverage.comparable_offers)),
        Kpi(name="Clusters de mercado", value=str(len(report.clusters))),
    ]
    if report.stale_veda_windows:
        kpis.append(Kpi(name="Filas de veda desactualizadas (>90 dias)", value=str(len(report.stale_veda_windows))))
    return Deliverable(
        title="Radiografia sectorial -- congelados HORECA Chile", client=client, summary=report.summary,
        findings=tuple(findings), kpis=tuple(kpis), citations=tuple(citations), confidence=confidence,
        prepared=prepared, lang="es",
        residual="Fotografia puntual sobre catalogos publicos, no monitoreo continuo (price_watch existe para eso). No recomienda precio.",
    )


def write_all_decks(
    report: MarketRadiographyReport, out_dir: str | Path, *, client: str = "Client",
    prepared: str = "", citations: tuple[str, ...] = (), confidence: float = 0.6,
    subjects: frozenset[str] = SUBJECT_DOMAINS,
) -> dict[str, Path]:
    """N per-subject Deliverables + 1 sector Deliverable, each in its own
    subdirectory. A subject with zero gaps this run gets no subdirectory at
    all -- never an empty placeholder report for a subject with nothing to
    compare (this can legitimately happen: a sujeto's whole feed failed, or
    none of its SKUs cleared the match gate this run)."""
    d = Path(out_dir)
    written: dict[str, Path] = {}
    present_subjects = sorted({g.subject for g in report.gaps} & subjects)
    for subject in present_subjects:
        deck = build_subject_deck(report, subject, client=client, prepared=prepared, citations=citations, confidence=confidence)
        for key, path in deck.write_all(d / subject).items():
            written[f"{key}_{subject}"] = path
    sector = build_sector_deck(report, client=client, prepared=prepared, citations=citations, confidence=confidence)
    for key, path in sector.write_all(d / "sector").items():
        written[f"{key}_sector"] = path
    return written
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_job.py -q
```

Expected: `12 passed`.

- [ ] **Step 5: Confirm lint is clean and commit**

```bash
ruff check src tests jobs
git add jobs/market_radiography_job.py tests/test_market_radiography_job.py
git commit -m "feat(market-radiography): N-per-subject + 1-sector-benchmark deck

No existing job in this codebase produces N per-subject files + 1 aggregate
in one run -- this composes package_deliverable.py's many-to-one aggregation
with pdp_writer.py's one-file-per-item loop, neither copied wholesale. A
subject with zero gaps this run gets no subdirectory at all, never an empty
placeholder. Veda findings state exposure and documentary obligation only,
never infraction (spec doctrine) -- pinned by a markdown-text assertion."
```

---

### Task 4: tool registration -- the 8 fixed places (not 7 -- see File Structure's line-number caveat)

**Files:**
- Modify: `scm_agent/tools.py` (import block, `_market_radiography_prepare`/`_run`, `market_radiography_tool()`, one `reg.register(...)`)
- Modify: `scm_agent/citation_gate.py` (one `TOOL_CONCEPTS` entry)
- Modify: `src/i18n.py` (one `TOOL_TITLES` entry)
- Modify: `tests/test_scm_agent.py` (add `"market_radiography"` to the expected key set)
- Modify: `tests/test_price_watch_tool.py` (bump `46` -> `47`)
- Create: `tests/test_market_radiography_tool.py`

**Interfaces:**
- Consumes: `jobs.market_radiography_job` (Task 2/3), `Tool`/`Prepared`/`Produced`/`ToolRegistry` (`scm_agent/registry.py`), `JobRequest`/`LLMProvider` (`scm_agent/types.py`/`scm_agent/llm.py`).
- Produces: the registered `market_radiography` tool, reachable via `build_default_registry()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_market_radiography_tool.py` (mirrors `tests/test_disruption_scan_tool.py`'s own shape):

```python
"""market_radiography tool wiring into scm_agent -- the 8th fixed place a new
tool touches (File Structure's line-number caveat corrects a prior handoff's
miscounted "7 lugares fijados")."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from scm_agent import tools
from scm_agent.types import JobRequest

NORMALIZED_COLUMNS = (
    "source", "sku", "title", "price_clp", "currency", "in_stock", "url",
    "price_per_kg", "quarantine_reason", "especie", "calibre", "estado", "proceso", "origen",
)


def _write_normalized_csv(path: Path) -> None:
    rows = [
        {
            "source": "subject-catalog.test", "sku": "A", "title": "Merluza Austral Filete 1kg",
            "price_clp": "9000", "currency": "CLP", "in_stock": "True", "url": "https://subject-catalog.test/A",
            "price_per_kg": "9000", "quarantine_reason": "", "especie": "merluza austral", "calibre": "",
            "estado": "", "proceso": "filete", "origen": "",
        },
        {
            "source": "ref-catalog.test", "sku": "B", "title": "Merluza Austral Filete 1kg",
            "price_clp": "8500", "currency": "CLP", "in_stock": "True", "url": "https://ref-catalog.test/B",
            "price_per_kg": "8500", "quarantine_reason": "", "especie": "merluza austral", "calibre": "",
            "estado": "", "proceso": "filete", "origen": "",
        },
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=NORMALIZED_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_market_radiography_is_registered() -> None:
    reg = tools.build_default_registry()
    tool = reg.get("market_radiography")
    assert tool.key == "market_radiography"
    assert tool.requires_data is True


def test_market_radiography_prepare_needs_data_without_a_path() -> None:
    reg = tools.build_default_registry()
    tool = reg.get("market_radiography")
    prepared = tool.prepare(JobRequest(data_path=None, params={}), provider=None)
    assert prepared.status == "needs_data"


def test_market_radiography_prepare_and_run_end_to_end(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(csv_path)
    reg = tools.build_default_registry()
    tool = reg.get("market_radiography")
    prepared = tool.prepare(
        JobRequest(data_path=str(csv_path), params={"subjects": frozenset({"subject-catalog.test"}), "on": date(2026, 8, 15)}),
        provider=None,
    )
    assert prepared.status == "ok"
    produced = tool.run(prepared.payload, {"subjects": frozenset({"subject-catalog.test"}), "on": date(2026, 8, 15)})
    assert produced.summary
    issues = tool.qa(produced.report)
    assert issues == []


def test_market_radiography_deck_writes_subject_and_sector_files(tmp_path: Path) -> None:
    csv_path = tmp_path / "normalized.csv"
    _write_normalized_csv(csv_path)
    reg = tools.build_default_registry()
    tool = reg.get("market_radiography")
    params = {"subjects": frozenset({"subject-catalog.test"}), "on": date(2026, 8, 15)}
    prepared = tool.prepare(JobRequest(data_path=str(csv_path), params=params), provider=None)
    produced = tool.run(prepared.payload, params)
    written = tool.deck(produced.report, tmp_path / "out", "Client", [], 0.6, [])
    assert any("sector" in k for k in written)


def test_registry_still_has_no_duplicate_keys() -> None:
    reg = tools.build_default_registry()
    keys = [t.key for t in reg.list()]
    assert len(keys) == len(set(keys))
```

Modify `tests/test_scm_agent.py` -- add `"market_radiography"` to the `assert keys == {...}` set literal (verify the exact current line with `grep -n "assert keys ==" tests/test_scm_agent.py` before editing; it was line 123 as of this plan's own research pass).

Modify `tests/test_price_watch_tool.py` -- bump the bare integer inside `test_registry_now_has_45_tools`:

```python
def test_registry_now_has_45_tools():
    # supplier_management_tool() + network_design_tool() (41 -> 43, origin/main) merged with
    # hat_tension_tool() + hat_settlement_tool() (41 -> 43, this branch) -> 45.
    # disruption_scan_tool() (45 -> 46). market_radiography_tool() (46 -> 47).
    # bump this count again the next time a tool is registered, same as every prior update.
    reg = tools.build_default_registry()
    assert len(reg.list()) == 47
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_tool.py -q
```

Expected: `AttributeError` or `KeyError` -- `reg.get("market_radiography")` doesn't exist yet.

- [ ] **Step 3: Wire the tool -- all 8 places**

**Place 1/2 -- `scm_agent/tools.py` import block.** Insert alphabetically inside the `from jobs import (...)` block (between `markdown_liquidation_job,` and `multi_echelon_job,` -- grep-confirm the exact neighbors first, PR-1/PR-2 don't touch this file but re-verify nothing else landed on `main`):

```python
    markdown_liquidation_job,
    market_radiography_job,
    multi_echelon_job,
```

**Place 3 -- `_market_radiography_prepare`/`_market_radiography_run`/`market_radiography_tool()`.** Add near `price_intelligence_tool()`/`price_watch_tool()` (this codebase's registration is thematic, not alphabetical -- put the new pricing/competitive tool with its siblings):

```python
# ---- market_radiography (CL frozen-food sector radiography) -----------------

def _market_radiography_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a normalized CL catalog-scan CSV (examples/run_cl_catalog_scan.py's output) is required"],
        )
    try:
        offers = market_radiography_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=offers)


def _market_radiography_run(payload: object, params: dict) -> Produced:
    report = market_radiography_job.run(
        payload,
        subjects=params.get("subjects", market_radiography_job.SUBJECT_DOMAINS),
        veda_calendar_path=params.get("veda_calendar_path"),
        on=params.get("on"),
        llm=params.get("llm"),
    )
    return Produced(report=report, summary=report.summary)


def market_radiography_tool() -> Tool:
    return Tool(
        key="market_radiography",
        title="CL Frozen-Food Sector Radiography",
        description="Compare up to 5 Chilean HORECA frozen-food distributors' public catalog prices "
                    "(normalized to CLP/kg) against a market median from up to 7 public sources, and "
                    "overlay each catalog against the SUBPESCA closed-season (veda) calendar. Zero "
                    "client data -- built entirely from public WooCommerce/Shopify catalog feeds and "
                    "hand-curated regulatory decrees. Read-only; reports exposure and documentary "
                    "obligation, never infraction.",
        intent_keywords=(
            "radiografia de mercado", "radiografia sectorial", "comparacion de precios congelados",
            "benchmark de precios chile", "calendario de vedas", "exposicion a veda",
            "frozen food market radiography", "chilean seafood price benchmark", "veda calendar exposure",
        ),
        requires_data=True,
        prepare=_market_radiography_prepare,
        run=_market_radiography_run,
        qa=lambda report: market_radiography_job.verify(report),
        deliver=lambda report, out_dir, client: market_radiography_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: market_radiography_job.write_all_decks(
            report, out_dir, client=client, citations=tuple(citations), confidence=confidence
        ),
    )
```

**Place 4 -- `build_default_registry()`.** Add near `price_intelligence_tool()`/`price_watch_tool()`:

```python
    reg.register(price_intelligence_tool())
    reg.register(price_watch_tool())
    reg.register(market_radiography_tool())
```

**Place 5 -- `scm_agent/citation_gate.py`, inside `TOOL_CONCEPTS`.** Anchors BFS-verified against the committed books graph on 2026-07-24 (54-node closure, zero degree->50 magnets, `price_transparency` deliberately rejected -- it pulls in `book_nagle` as a magnet):

```python
    "market_radiography": ("competition_oriented_pricing", "price_positioning", "regulated_supply_chain_environment"),
```

No `EXCLUDED_CONCEPTS` entry needed (same as `disruption_scan`).

**Place 6 -- `src/i18n.py`, inside `TOOL_TITLES`**, inserted alphabetically:

```python
    "market_radiography": {"es": "Radiografia de Mercado -- Congelados Chile", "en": "Market Radiography -- Chilean Frozen Food"},
```

**Place 7 -- `tests/test_scm_agent.py:123`**, add `"market_radiography"` to the set literal (already staged above in Step 1).

**Place 8 -- `tests/test_price_watch_tool.py:165`**, bump to `47` (already staged above in Step 1).

- [ ] **Step 4: Run the test to verify it passes**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_tool.py tests/test_scm_agent.py tests/test_price_watch_tool.py -q
```

Expected: all pass. If `test_every_anchor_concept_exists` (in `tests/test_citation_gate.py`) fails, one of the three anchor ids doesn't exist in `knowledge/scm-books/graph.json` under this exact spelling -- re-run the BFS verification the handoff already did once (`graphify query`/`python examples/query_knowledge.py --search "..."`) rather than picking a different id blind.

- [ ] **Step 5: Confirm nothing regressed and lint is clean**

```bash
PYTHONPATH=. python -m pytest tests/ -q
ruff check src tests scm_agent jobs examples
```

Expected: full suite green -- this is the last code task in the plan.

- [ ] **Step 6: Commit**

```bash
git add scm_agent/tools.py scm_agent/citation_gate.py src/i18n.py tests/test_scm_agent.py tests/test_price_watch_tool.py tests/test_market_radiography_tool.py
git commit -m "feat(market-radiography): register as Kern tool #47

All 8 fixed places (not 7 -- test_scm_agent.py and test_price_watch_tool.py
are two separate files under one miscounted handoff bullet): tools.py import
+ prepare/run/tool()/register, citation_gate.py TOOL_CONCEPTS (BFS-verified
anchors, zero magnets, no EXCLUDED_CONCEPTS entry needed), i18n.py
TOOL_TITLES, and both registry-count tests bumped 46 -> 47."
```

---

### Task 5: `examples/run_market_radiography.py`, the golden acceptance test, and the PR

**Files:**
- Create: `examples/run_market_radiography.py`
- Create: `tests/test_market_radiography_golden.py`

**Interfaces:**
- Consumes: `market_radiography_job.prepare`/`run`/`write_operational`/`write_all_decks` (Task 2/3).
- Produces: the offline CLI, and the plan's own acceptance bar from the spec's Testing section: "Golden: un cluster completo (camaron 36/40 crudo, 4 origenes) de feed crudo a fila de matriz, con numeros verificados a mano."

- [ ] **Step 1: Write the golden test first**

Create `tests/test_market_radiography_golden.py`:

```python
"""The spec's own acceptance bar (design.md Testing section): a full cluster
-- camaron 36/40 crudo across 4 sources -- from raw normalized rows to a
matrix row, with numbers verified by hand. This is the one test in the whole
plan that exercises Task 1 (clustering) + Task 2 (the job) together end to
end, standing in for a live feed-to-report run without a network call."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from jobs.market_radiography_job import run
from src.market_radiography import RadiographyOffer

# Hand-verified: 4 sources, camaron 36/40 crudo pelado, prices from the
# 2026-07-24 research pass (Rapa-Nui, Frozt) plus two synthetic reference
# points at plausible market levels for TodoFrio and Pulpo Chile (the exact
# live prices for those two were not part of the captured research trail --
# marked here, not presented as independently verified like the other two).
_OFFERS = [
    RadiographyOffer(
        source="rapa-nui-catalog.test", sku="MAR1004", title="Camaron 36/40 crudo pelado 1kg",
        price_clp=Decimal("15990"), price_per_kg=Decimal("15990"), quarantine_reason="",
        especie="camaron", calibre="36/40", estado="crudo", proceso="pelado", origen=None,
        url="https://rapa-nui-catalog.test/MAR1004",
    ),
    RadiographyOffer(
        source="frozt-catalog.test", sku="FRZ-CAM-3640", title="Camaron 36/40 crudo pelado desvenado",
        price_clp=Decimal("13990"), price_per_kg=Decimal("13990"), quarantine_reason="",
        especie="camaron", calibre="36/40", estado="crudo", proceso="pelado", origen=None,
        url="https://frozt-catalog.test/FRZ-CAM-3640",
    ),
    RadiographyOffer(
        # synthetic reference point -- see module docstring
        source="todofrio-catalog.test", sku="TDF-CAM-3640", title="Camaron 36/40 crudo pelado 1kg",
        price_clp=Decimal("14990"), price_per_kg=Decimal("14990"), quarantine_reason="",
        especie="camaron", calibre="36/40", estado="crudo", proceso="pelado", origen=None,
        url="https://todofrio-catalog.test/TDF-CAM-3640",
    ),
    RadiographyOffer(
        # synthetic reference point -- see module docstring
        source="pulpochile-catalog.test", sku="PLP-CAM-3640", title="Camaron 36/40 crudo pelado",
        price_clp=Decimal("14500"), price_per_kg=Decimal("14500"), quarantine_reason="",
        especie="camaron", calibre="36/40", estado="crudo", proceso="pelado", origen=None,
        url="https://pulpochile-catalog.test/PLP-CAM-3640",
    ),
]


def test_golden_camaron_3640_cluster_from_offers_to_matrix_row() -> None:
    report = run(
        _OFFERS, subjects=frozenset({"rapa-nui-catalog.test"}), veda_calendar_path=None, on=date(2026, 8, 15)
    )
    assert len(report.clusters) == 1
    cluster = report.clusters[0]
    assert len(cluster.members) == 4
    # median of 13990, 14500, 14990, 15990 -- average of the two middle values
    assert cluster.median_price_per_kg == Decimal("14745")

    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert gap.subject == "rapa-nui-catalog.test"
    assert gap.subject_price_per_kg == Decimal("15990")
    assert gap.market_median_price_per_kg == Decimal("14745")
    # (15990 - 14745) / 14745 * 100 -- Rapa-Nui sits above the cluster median here
    expected_gap = (Decimal("15990") - Decimal("14745")) / Decimal("14745") * 100
    assert gap.gap_pct == expected_gap
    assert gap.gap_pct > 0
```

- [ ] **Step 2: Run the test to verify it fails, then passes**

```bash
PYTHONPATH=. python -m pytest tests/test_market_radiography_golden.py -q
```

If clustering does not confirm all 4 into one cluster, this is diagnostic, not a reason to weaken the match gate -- print each pair's `score_pair` breakdown and `count_concordant_signals` before touching `CONFIRM_THRESHOLD`, `MIN_CONCORDANT_SIGNALS`, or the lexicon. Expected once correct: `1 passed`.

- [ ] **Step 3: Write the CLI**

Create `examples/run_market_radiography.py`:

```python
"""Offline CLI: PR-1's normalized CSV + PR-2's veda calendar -> 5 subject
reports + 1 sector benchmark. No network call anywhere in this file (D3).

Usage:
    python examples/run_market_radiography.py --normalized data/cl_scan/normalized.csv \
        --out data/cl_radiography/ [--on 2026-08-15]
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from jobs.market_radiography_job import prepare, run, write_all_decks, write_operational


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the CL frozen-food sector radiography")
    parser.add_argument("--normalized", required=True)
    parser.add_argument("--out", default="data/cl_radiography")
    parser.add_argument("--on", default=None, help="ISO date, defaults to today")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    on = date.fromisoformat(args.on) if args.on else None
    offers = prepare(args.normalized)
    report = run(offers, on=on)
    write_operational(report, Path(args.out) / "operational")
    written = write_all_decks(report, Path(args.out) / "decks")
    print(report.summary)
    for key, path in sorted(written.items()):
        print(f"{key}: {path}")
    if report.stale_veda_windows:
        print(f"WARNING: {len(report.stale_veda_windows)} veda row(s) verified more than 90 days ago")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the full suite**

```bash
PYTHONPATH=. python -m pytest tests/ -q
ruff check src tests scm_agent jobs examples
```

Expected: full suite green on all three PRs' combined work.

- [ ] **Step 5: Commit**

```bash
git add examples/run_market_radiography.py tests/test_market_radiography_golden.py
git commit -m "feat(market-radiography): offline CLI + the spec's golden acceptance test

camaron 36/40 crudo, 4 sources, offers-to-matrix-row with hand-verified
numbers -- the spec's own Testing-section acceptance bar (design.md). Two of
the four price points are real (Rapa-Nui, Frozt, from the 2026-07-24 research
pass); the other two are marked synthetic reference points, not presented as
independently verified."
```

- [ ] **Step 6: Open the PR**

```bash
git push -u origin feat/radiografia-cl-market-radiography
gh pr create --draft --base main --title "feat(market-radiography): sector radiography deliverable (radiografia PR-3)" --body "$(cat <<'EOF'
## Summary
- src/market_radiography.py: N-way peer clustering across up to 7 sources (D7), not homologate()'s bilateral shape; two-concordant-signal gate on top of the probabilistic threshold
- jobs/market_radiography_job.py: prepare/run/verify (mirrors disruption_scan_job.py), fully offline (D3)
- N-per-subject + 1-sector-benchmark deck -- a new composition, no existing job in this codebase does this shape
- Registered as Kern tool #47, all 8 fixed places touched (citation-gate anchors BFS-verified, zero magnets)

## Depends on
- PR-1 (feed readers + normalizer + lexicon) -- merged first
- PR-2 (veda calendar) -- merged first

## Test plan
- [ ] `PYTHONPATH=. pytest tests/ -q` green on py3.11/3.12/3.13 (CI)
- [ ] `ruff check` clean across src/tests/scm_agent/jobs/examples
- [ ] Golden test: camaron 36/40 crudo, 4 sources, hand-verified median/gap
- [ ] Operator: run examples/run_cl_catalog_scan.py against the 9 real hand-authored site configs (PR-1 Task 6, Step 0) before trusting this report's numbers in a real deliverable
EOF
)"
```

---

## Self-Review

- **TDD discipline held throughout.** Every task writes a failing test before its implementation, across all three PRs -- no task skips straight to code.
- **The single highest-risk component (CLP/kg normalization, Task 1-2 of PR-1) got the most exhaustive test table** (18 cases, including the exact conclusion-inverting real numbers). Lower-risk tasks (e.g. the CLI's thin `main()` glue) are deliberately left untested, matching the rest of this codebase's own convention for `examples/run_*.py` files -- this is a calibration, not an oversight.
- **Every spec claim that didn't survive contact with the actual code got a numbered, reasoned deviation** (D1-D7), not a silent workaround. Four were found before any code was written (D1-D4, prior session); three more (D5-D7) surfaced while writing PR-1 Task 3 and PR-3 Task 1 in this session -- catalog-feed tier classification, the JSON-LD reader's actual scope, and N-way clustering's real shape. All seven are cross-referenced from the File Structure and task text that depend on them, not just listed once and forgotten.
- **Every code-level claim in this plan (function signatures, dataclass fields, line numbers) was verified against the actual file on 2026-07-24**, not inferred from the spec or a prior handoff's paraphrase -- two paraphrased claims from the prior handoff turned out to be wrong (`launch_readiness_job.py` does NOT demonstrate the `config/**`-relative-to-CWD pattern; three of the "7 fijados" line numbers actually point at `launch_readiness`'s entries, not `disruption_scan`'s) and are corrected in place rather than silently repeated forward.
- **Two things this plan explicitly cannot do, and says so instead of faking it:** (1) the real base URLs and hand-authored `config/sites/*.yaml` for the 9 (7, D6) domains -- a network-dependent operator step, isolated to PR-1 Task 6 Step 0, that nothing else in the plan depends on being done early; (2) two of the four golden-test price points (Task 5, PR-3) are synthetic reference values, not independently re-verified live numbers, and are labeled as such rather than presented with false confidence.
- **`verify()`'s "SUBJECT_DOMAINS still holds placeholders" check (PR-3 Task 2) is the plan's own safety net against its own known gap** -- if Task 6 Step 0 is skipped or the placeholder domains are never replaced, the job doesn't silently produce an empty-looking-successful report; QA fails with a message that points directly at the missed step.
- **Known limitation, deliberately out of scope, not silently absorbed:** with `llm=None` (the production default), `cluster_offers` only confirms pairs that reach `CONFIRM_THRESHOLD` outright -- an ambiguous pair in the adjudication band is left unclustered rather than guessed, same discipline as the CLP/kg quarantine. Wiring a real LLM adjudicator would recover more matches; it is a clean, additive follow-up this plan does not attempt, matching the spec's own "Fuera de alcance" framing for anything beyond the radiography itself.
- **What this plan does NOT re-litigate:** whether to build this at all (spec, approved), the B+C pricing offer (handoff, decided), or whether to contact the 5 sujetos (explicitly out of scope in both documents -- producing the report and sending it are different decisions, and only the user makes the second one).
- **Residual risk carried forward, not resolved here:** two of the original 9 sources (IceFish, Mardeli, D6) are out of scope for v1; the market median runs on 7 sources instead of 9. This thins the reference set but does not affect any sujeto's own report, since all 5 sujetos are Woo/Shopify. If reference-set mass ever matters more than it does today, the JSON-LD reader is the named, scoped follow-up (D6) -- not a surprise to rediscover later.
