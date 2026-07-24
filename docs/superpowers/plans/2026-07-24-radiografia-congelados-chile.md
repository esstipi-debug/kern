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
| `examples/run_market_radiography.py` | Offline CLI: normalized CSV -> 5 reports + sector benchmark. |

**Modified files**

| Path | Change |
|---|---|
| `src/pricing_intel/normalize.py` | Add `WeightFormat`, `extract_weight_format`, `price_per_kilogram`, `VolumetricNotConvertible`. |
| `src/pricing_intel/match/probabilistic.py:103` | Extend `DECISIVE_ATTRIBUTE_KEYS` with the four es-CL keys. |
| `scm_agent/tools.py` | Import block, `_market_radiography_prepare/_run`, `market_radiography_tool()`, one `reg.register(...)`. |
| `scm_agent/citation_gate.py:54` | One `TOOL_CONCEPTS` entry. |
| `src/i18n.py:260` | One `TOOL_TITLES` es/en pair. |
| `tests/test_scm_agent.py:123` | Add `"market_radiography"` to the expected key set. |
| `tests/test_price_watch_tool.py:165` | Bump `46` -> `47`. |

**New test files:** `tests/test_normalize_weight_format.py`, `tests/test_catalog_feeds.py`, `tests/test_es_cl_lexicon.py`, `tests/test_veda_calendar.py`, `tests/test_market_radiography.py`, `tests/test_market_radiography_job.py`. Fixtures under `tests/fixtures/cl_catalog/`.

**Deliberately NOT touched:** `webapp/how_it_works_data.py` (frozen at 41 by three tests, not registry-synced), `webapp/mcp_tool_specs.py` (MCP exposure deferred -- same call `launch_readiness` made; see PR-3 follow-up note), `src/risk_period.py` (D1), `jobs/price_intelligence.py` (D3).

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
