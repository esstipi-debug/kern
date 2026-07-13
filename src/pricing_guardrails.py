"""Jurisdiction-aware pricing compliance guardrails (Linchpin 3.0 PR-17, plan
section 7 P5 ``pricing_guardrails``) -- the core compliance differentiator of
the 3.0 plan (Golden Rule 12: "jurisdiccion es configuracion declarativa").

Three legally distinct guardrails, one shared evidence primitive:

1. **EU/UK Omnibus -- HARD gate.** Article 6a of Directive 98/6/EC (as
   amended by the "Omnibus" Directive (EU) 2019/2161) requires that a price
   reduction be announced against the trader's own lowest price in the 30
   days before the reduction; CJEU C-330/23 *Aldi Sud* (2025) confirmed the
   claimed percentage must be **calculated** against that 30-day-lowest
   price, not merely displayed alongside a different reference.
   :func:`validate_discount_reference` BLOCKS (raises
   :class:`DiscountComplianceError`) a proposed discount whose stated
   percentage does not match that calculation within a documented
   tolerance, for any market whose profile declares
   ``discount_reference_gate: hard`` (``config/markets/eu.yaml``,
   ``uk.yaml``).
2. **CL/MX/CO -- SOFT gate.** SERNAC (Ley 19.496 arts. 28/35), PROFECO
   (LFPC -- "precios inflados antes del Buen Fin"), and the SIC (Ley 1480)
   all require a real, non-deceptive reference price, but none of them
   codifies a fixed window the way EU Omnibus does. These markets'
   profiles declare ``discount_reference_gate: soft``:
   :func:`validate_discount_reference` never raises for them, it returns a
   ``passed=False`` :class:`DiscountValidation` (a warning) carrying the
   SAME prior-price evidence trail -- which doubles as the audit-defense
   file in all four jurisdictions (plan section 7's own framing).
   :func:`detect_inflate_then_discount` flags the classic "raise the price,
   then discount it" pattern these regulators pursue.
3. **MAP (US) -- observe-and-alert only, never a gate.** MAP (Minimum
   Advertised Price) is lawful in the US only as a UNILATERAL policy the
   manufacturer sets and monitors itself (the Colgate doctrine, *Colgate v.
   United States*, 250 U.S. 300 (1919)). :func:`detect_map_alert` therefore
   NEVER blocks anything and this module NEVER builds a retailer-facing
   communication (a violation letter, an acknowledgment workflow, a
   negotiation) -- doing either would risk turning a unilateral policy into
   an unlawful vertical price-fixing agreement. In the EU/UK, the identical
   below-floor-price signal is RPM (a hardcore restriction) when framed as
   a retailer obligation, so the exact same alert must never use
   "violation" language there -- ``MarketRules.map_alert_label`` is a
   jurisdiction-keyed lookup (Golden Rule 12), not a single hardcoded
   string; see ``config/markets/*.yaml``.

**Shared evidence primitive: :func:`prior_price_30d_lowest`.** This is
NOT a parallel own-price ledger -- it queries ``src.state``'s existing,
already-shipped, append-only ``prices_own`` domain (PR-1) via
``src.state.history()``. Extra columns beyond ``prices_own``'s
``DOMAIN_COLUMNS`` contract (``product_id``/``price``/``currency``) are
already allowed (``strict=False``, the same convention PR-8 used for
``outcomes``) -- this module reads an OPTIONAL ``channel`` column from that
payload for per-store/per-channel filtering, without touching
``DOMAIN_COLUMNS`` itself.

**Design decision -- ``store`` vs ``channel``.** ``src.state``'s own
functions (``history``, ``latest``, ``snapshot``) already use the keyword
``store`` for the injectable :class:`~src.state.store.StateStore` (see
``src/verify/backtest.py``'s identical ``store: StateStore | None = None``
convention). This module's public functions keep that same meaning for
``store`` -- forwarded to ``src.state.history()`` unchanged -- rather than
overloading the word for a physical retail store/channel. The plan's
"SKU x mercado x canal x ts" evidence granularity is instead the separate
``channel`` keyword (optional; ``None`` means "don't filter by channel").

**Golden Rule 7 (total provenance) / Rule 8 (append-only) / Rule 14 (no
silent caps):** every :class:`DiscountValidation` and
:class:`InflateThenDiscountWarning` carries an ``evidence_trail`` of
human-legible lines recording the prior 30-day-low price, the calculation
basis, and the window used -- this module never silently drops that
context, and it never writes to ``prices_own`` itself (read-only over
already-append-only history).

**Central gate (:func:`gate_price_changeset`).** No proposed price
changeset -- from PR-16's ``price_optimizer``, this module's own discount
validation, or anywhere else -- ships without BOTH a human-legible
explanation and citations. Reuses
``src.writeback.Changeset`` (already has a ``reason`` field -- no parallel
changeset type is built here) and
``scm_agent.citation_gate.filter_citations`` (the same gate
Fase B PR-13 established for ``price_intelligence``; ``"pricing"`` is
already a registered ``TOOL_CONCEPTS`` key) -- no second citation
mechanism.

Pure/deterministic except for the read-only ``src.state`` query (the same
carve-out ``src/verify/backtest.py`` and ``src/pricing_intel/ledger.py``
already use); no writeback, no network I/O.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from scm_agent.citation_gate import filter_citations
from scm_agent.knowledge import GroundedCitation, KnowledgeBase
from src.state.store import StateStore
from src.state.system_state import history as state_history
from src.writeback import Changeset

# -- market profile loading (Golden Rule 12: declarative config, not `if`) ---

# config/markets/<market>.yaml -- one file per jurisdiction. Overridable the
# same way acquire/base.py's DEFAULT_SITES_CONFIG_DIR is, so tests never
# touch the repo's real config/ directory unless they choose to.
DEFAULT_MARKETS_CONFIG_DIR = Path(
    os.environ.get("LINCHPIN_MARKETS_CONFIG_DIR", "").strip() or "config/markets"
)

GATE_HARD = "hard"
GATE_SOFT = "soft"
GATE_NONE = "none"
_VALID_GATES = (GATE_HARD, GATE_SOFT, GATE_NONE)

# Default tolerance (percentage POINTS) between a stated discount % and the
# % computed against prior_price_30d_lowest() before it counts as a
# mismatch, when a market's own profile does not declare one. 0.5 absorbs
# ordinary display rounding (a computed 19.6% shown as "20% off") without
# opening a loophole for a materially different reference price.
DEFAULT_DISCOUNT_TOLERANCE_PCT = Decimal("0.5")

# EU Omnibus's own reference window (Art. 6a Directive 98/6/EC) -- the
# primitive's name (prior_price_30d_lowest) bakes this in; kept as an
# overridable parameter only for testability, not as a per-market config
# knob (plan section 7: "el mismo log sirve de expediente de defensa en las
# 4 jurisdicciones" -- ONE 30-day evidence primitive, shared by all
# profiles regardless of that market's own gate severity).
DEFAULT_DISCOUNT_WINDOW_DAYS = 30

# "Shortly before a discount announcement" lookback for the CL/MX/CO
# inflate-then-discount pattern (plan section 7's PROFECO "Buen Fin"
# framing) -- deliberately shorter than the 30-day reference window: this
# flags a RECENT raise, not any price move somewhere in the last month.
DEFAULT_INFLATE_LOOKBACK_DAYS = 14

_PCT_QUANT = Decimal("0.01")
_SAFE_MARKET = re.compile(r"^[a-z]{2,8}$")


@dataclass(frozen=True)
class MarketRules:
    """One jurisdiction's declarative pricing-guardrail profile (plan
    section 7 P5, Golden Rule 12) -- loaded from
    ``config/markets/<market>.yaml`` by :func:`load_market_rules`. Every
    field here is DATA a guardrail function reads, never an ``if market ==
    ...`` branch in this module's code."""

    market: str
    discount_reference_gate: str  # "hard" | "soft" | "none"
    discount_tolerance_pct: Decimal
    map_alert_label: str
    legal_basis: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.market or not self.market.strip():
            raise ValueError("market must be a non-empty string")
        if self.discount_reference_gate not in _VALID_GATES:
            raise ValueError(
                f"discount_reference_gate must be one of {_VALID_GATES}, got {self.discount_reference_gate!r}"
            )
        if self.discount_tolerance_pct < 0:
            raise ValueError("discount_tolerance_pct must be >= 0")
        if not self.map_alert_label or not self.map_alert_label.strip():
            raise ValueError("map_alert_label must be a non-empty string")


class MarketNotConfiguredError(LookupError):
    """No ``config/markets/<market>.yaml`` exists for ``market`` at all --
    the hard gate. A guardrail function must never run against an
    unconfigured jurisdiction (same enforcement pattern as
    ``src.pricing_intel.acquire.base.require_approved_site``)."""

    def __init__(self, market: str, path: Path) -> None:
        self.market = market
        self.path = path
        super().__init__(
            f"no market profile for '{market}' at {path} -- pricing guardrails refuse to run "
            "without a declarative config/markets/*.yaml profile (plan Golden Rule 12)"
        )


def _market_config_path(market: str, config_dir: Path | str) -> Path:
    normalized = market.strip().lower()
    if not _SAFE_MARKET.match(normalized):
        raise ValueError(f"market must be a bare lowercase code (e.g. 'eu', 'us'), got {market!r}")
    return Path(config_dir) / f"{normalized}.yaml"


def load_market_rules(market: str, *, config_dir: Path | str = DEFAULT_MARKETS_CONFIG_DIR) -> MarketRules:
    """Load and validate ``config/markets/<market>.yaml`` into a
    :class:`MarketRules`. Raises :class:`MarketNotConfiguredError` if the
    file does not exist, or ``ValueError``/``TypeError`` (from
    ``MarketRules.__post_init__``) if it exists but fails validation --
    either way, never returns a half-valid profile."""
    normalized = market.strip().lower()
    path = _market_config_path(market, config_dir)
    if not path.exists():
        raise MarketNotConfiguredError(normalized, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(raw).__name__}")
    data = dict(raw)
    data.setdefault("market", normalized)
    if data["market"] != normalized:
        raise ValueError(f"{path}: 'market' field {data['market']!r} does not match filename market {normalized!r}")
    data["discount_tolerance_pct"] = (
        Decimal(str(data["discount_tolerance_pct"]))
        if "discount_tolerance_pct" in data
        else DEFAULT_DISCOUNT_TOLERANCE_PCT
    )
    return MarketRules(**data)


# -- shared helpers ------------------------------------------------------------


def _to_decimal(value: Decimal | float | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _as_naive_datetime(value: date | datetime) -> datetime:
    """Normalize ``date``/``datetime`` (aware or naive) to a naive UTC
    ``datetime`` -- comparisons in this module are all against
    ``StateSnapshot.created_at`` (itself normalized the same way), so
    mixing naive/aware datetimes here would otherwise raise ``TypeError``."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    return datetime.combine(value, datetime.min.time())


def _parse_created_at(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _product_price_series(
    product_id: str,
    *,
    store: StateStore | None = None,
    channel: str | None = None,
) -> list[tuple[datetime, Decimal]]:
    """Every observed ``(timestamp, price)`` point for ``product_id`` across
    ALL stored ``prices_own`` snapshots (``src.state.history``, PR-1),
    oldest first. Each snapshot is one full price capture (potentially many
    products, potentially many channels) taken at its own ``created_at`` --
    this flattens that history down to one product's points.

    ``channel``, when given, filters to a payload's optional ``channel``
    extra column (allowed by ``prices_own``'s ``strict=False`` schema --
    see module docstring); a snapshot payload that does not track
    ``channel`` at all contributes no points once a specific channel is
    requested, rather than ambiguously matching every row.
    """
    points: list[tuple[datetime, Decimal]] = []
    for snap in state_history("prices_own", store=store):
        payload = snap.payload
        if "product_id" not in payload.columns or "price" not in payload.columns:
            continue
        rows = payload[payload["product_id"] == product_id]
        if channel is not None:
            if "channel" not in payload.columns:
                continue
            rows = rows[rows["channel"] == channel]
        if rows.empty:
            continue
        ts = _parse_created_at(snap.created_at)
        points.extend((ts, Decimal(str(price))) for price in rows["price"])
    points.sort(key=lambda pair: pair[0])
    return points


# -- shared evidence primitive: 30-day-lowest own price ------------------------


def prior_price_30d_lowest(
    product_id: str,
    as_of: date | datetime,
    *,
    store: StateStore | None = None,
    channel: str | None = None,
    window_days: int = DEFAULT_DISCOUNT_WINDOW_DAYS,
) -> Decimal | None:
    """The lowest own price observed for ``product_id`` in the
    ``window_days`` days (default 30 -- the EU Omnibus reference window) up
    to and including ``as_of``, read from ``src.state``'s append-only
    ``prices_own`` domain history (PR-1). Returns ``None`` when there is no
    own-price history at all in that window (never a fabricated number --
    plan Golden Rule 14).

    ``store`` is the injectable ``src.state.store.StateStore`` (forwarded
    to ``src.state.history()`` unchanged, matching
    ``src/verify/backtest.py``'s own convention) -- pass an isolated
    ``StateStore(tmp_path / "state")`` in tests, never the process-wide
    default. ``channel`` is an OPTIONAL per-store/per-channel filter (see
    module docstring's design-decision note on why these are two separate
    keywords).

    Reference example (see tests/test_pricing_guardrails.py): own-price
    snapshots at 100 (60 days before ``as_of``, outside the window), 80 (22
    days before), 95 (7 days before) -> ``prior_price_30d_lowest`` = 80 (the
    100 snapshot falls outside the 30-day window and is excluded).
    """
    if not product_id or not product_id.strip():
        raise ValueError("product_id must be a non-empty string")
    if window_days <= 0:
        raise ValueError("window_days must be > 0")

    as_of_dt = _as_naive_datetime(as_of)
    window_start = as_of_dt - timedelta(days=window_days)

    prices = [
        price
        for ts, price in _product_price_series(product_id, store=store, channel=channel)
        if window_start <= ts <= as_of_dt
    ]
    return min(prices) if prices else None


# -- EU/UK Omnibus hard gate + CL/MX/CO soft gate -------------------------------


@dataclass(frozen=True)
class DiscountValidation:
    """Outcome of validating one proposed discount announcement against its
    market's reference-price rule. ``passed=False`` under a ``"hard"`` gate
    never reaches the caller as a return value -- it is raised as
    :class:`DiscountComplianceError` instead; under ``"soft"``/``"none"`` it
    is returned normally (a warning, never a block)."""

    product_id: str
    market: str
    gate: str  # "hard" | "soft" | "none"
    passed: bool
    stated_discount_pct: Decimal
    computed_discount_pct: Decimal | None
    prior_price_30d_low: Decimal | None
    reason: str | None
    evidence_trail: tuple[str, ...]


class DiscountComplianceError(ValueError):
    """Raised by :func:`validate_discount_reference` when a HARD-gated
    market's proposed discount does not survive validation -- the changeset
    must not ship (plan section 7 P5: EU Omnibus Art. 6a / CJEU C-330/23
    *Aldi Sud*). ``self.validation`` carries the full evidence trail for
    whatever caught this exception to log or surface."""

    def __init__(self, validation: DiscountValidation) -> None:
        self.validation = validation
        super().__init__(validation.reason)


def _discount_pct(new_price: Decimal, reference_price: Decimal) -> Decimal:
    pct = (Decimal(1) - (new_price / reference_price)) * Decimal(100)
    return pct.quantize(_PCT_QUANT, rounding=ROUND_HALF_UP)


def validate_discount_reference(
    *,
    product_id: str,
    new_price: Decimal | float,
    stated_discount_pct: Decimal | float,
    market: str,
    as_of: date | datetime,
    store: StateStore | None = None,
    channel: str | None = None,
    markets_dir: Path | str = DEFAULT_MARKETS_CONFIG_DIR,
) -> DiscountValidation:
    """Validate a proposed discount's STATED percentage against the
    percentage actually computed from ``prior_price_30d_lowest()`` --
    ``round(100 * (1 - new_price / prior_price_30d_lowest), 2)`` -- per
    ``market``'s declarative profile (:func:`load_market_rules`).

    - ``discount_reference_gate: hard`` (EU, UK): a mismatch beyond the
      market's ``discount_tolerance_pct`` raises
      :class:`DiscountComplianceError` -- the changeset does not ship.
      Showing the correct 30-day-lowest number elsewhere in the same
      deliverable does NOT cure a mismatched STATED percentage (the exact
      *Aldi Sud* fact pattern -- see
      tests/test_pricing_guardrails.py::test_aldi_sud_mismatched_percentage_is_blocked_in_eu).
    - ``discount_reference_gate: soft`` (CL, MX, CO): never raises; returns
      a ``passed=False`` warning with the same evidence trail attached.
    - ``discount_reference_gate: none`` (US, by default): never gated at
      all -- ``passed=True`` unconditionally, but ``prior_price_30d_lowest``
      is still computed and recorded in ``evidence_trail`` (Golden Rule 7:
      total provenance, even where nothing is enforced on it).

    Missing own-price history (``prior_price_30d_lowest`` returns ``None``)
    is itself treated as a failed validation under ``hard``/``soft`` gates
    -- a discount cannot be verified without evidence, and Golden Rule 14
    forbids silently assuming compliance.
    """
    if new_price <= 0:
        raise ValueError("new_price must be > 0")
    rules = load_market_rules(market, config_dir=markets_dir)
    new_price_d = _to_decimal(new_price)
    stated_d = _to_decimal(stated_discount_pct)
    as_of_dt = _as_naive_datetime(as_of)

    prior_low = prior_price_30d_lowest(product_id, as_of, store=store, channel=channel)
    evidence: tuple[str, ...] = (
        f"product_id={product_id} market={rules.market} as_of={as_of_dt.isoformat()}",
        f"prior_price_{DEFAULT_DISCOUNT_WINDOW_DAYS}d_lowest="
        f"{prior_low if prior_low is not None else 'unavailable (no own-price history in window)'}",
        f"new_price={new_price_d} stated_discount_pct={stated_d}",
    )

    if rules.discount_reference_gate == GATE_NONE:
        return DiscountValidation(
            product_id=product_id, market=rules.market, gate=rules.discount_reference_gate, passed=True,
            stated_discount_pct=stated_d, computed_discount_pct=None, prior_price_30d_low=prior_low,
            reason=None, evidence_trail=evidence,
        )

    if prior_low is None:
        reason = (
            f"no own-price history in the {DEFAULT_DISCOUNT_WINDOW_DAYS}-day reference window for "
            f"'{product_id}' -- cannot verify the discount reference"
        )
        result = DiscountValidation(
            product_id=product_id, market=rules.market, gate=rules.discount_reference_gate, passed=False,
            stated_discount_pct=stated_d, computed_discount_pct=None, prior_price_30d_low=None,
            reason=reason, evidence_trail=evidence,
        )
        if rules.discount_reference_gate == GATE_HARD:
            raise DiscountComplianceError(result)
        return result

    computed = _discount_pct(new_price_d, prior_low)
    evidence = evidence + (f"computed_discount_pct_vs_{DEFAULT_DISCOUNT_WINDOW_DAYS}d_low={computed}",)
    matches = abs(computed - stated_d) <= rules.discount_tolerance_pct

    if matches:
        return DiscountValidation(
            product_id=product_id, market=rules.market, gate=rules.discount_reference_gate, passed=True,
            stated_discount_pct=stated_d, computed_discount_pct=computed, prior_price_30d_low=prior_low,
            reason=None, evidence_trail=evidence,
        )

    reason = (
        f"stated discount {stated_d}% does not match the {computed}% computed against the "
        f"{DEFAULT_DISCOUNT_WINDOW_DAYS}-day lowest price {prior_low} (tolerance "
        f"{rules.discount_tolerance_pct} pct points) -- {rules.legal_basis.strip() or 'market reference-price rule'}"
    )
    result = DiscountValidation(
        product_id=product_id, market=rules.market, gate=rules.discount_reference_gate, passed=False,
        stated_discount_pct=stated_d, computed_discount_pct=computed, prior_price_30d_low=prior_low,
        reason=reason, evidence_trail=evidence,
    )
    if rules.discount_reference_gate == GATE_HARD:
        raise DiscountComplianceError(result)
    return result


# -- CL/MX/CO inflate-then-discount pattern (soft-gate signal) -----------------


@dataclass(frozen=True)
class InflateThenDiscountWarning:
    """A price RAISE observed shortly before a proposed discount -- the
    "inflar para despues descontar" / "precios inflados antes del Buen Fin"
    pattern SERNAC/PROFECO/SIC pursue (plan section 7). Always a WARNING:
    this dataclass is never raised as an exception, only returned."""

    product_id: str
    as_of: str  # ISO date
    raised_from: Decimal
    raised_to: Decimal
    raised_at: str  # ISO timestamp of the observed peak price
    new_price: Decimal
    lookback_days: int
    evidence_trail: tuple[str, ...]


def detect_inflate_then_discount(
    product_id: str,
    as_of: date | datetime,
    new_price: Decimal | float,
    *,
    lookback_days: int = DEFAULT_INFLATE_LOOKBACK_DAYS,
    store: StateStore | None = None,
    channel: str | None = None,
) -> InflateThenDiscountWarning | None:
    """Flag (never block) an own-price RAISE within ``lookback_days`` before
    ``as_of`` that ``new_price`` now discounts off of -- the pattern is:
    price goes up, then a "discount" brings it back down, off the
    ARTIFICIALLY raised peak rather than the SKU's genuine baseline.

    Returns ``None`` when there are fewer than two own-price observations
    in the lookback window, when the window shows no raise (the price
    trended flat or down), or when ``new_price`` is not actually below the
    observed peak (not a discount off of it).

    Reference example (see tests/test_pricing_guardrails.py): own-price
    points at 50 (12 days before ``as_of``) then 65 (7 days before,
    a +30% raise), proposed ``new_price``=55 -> flagged: raised_from=50,
    raised_to=65, even though 55 is still above the original 50 baseline.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be > 0")
    new_price_d = _to_decimal(new_price)
    as_of_dt = _as_naive_datetime(as_of)
    window_start = as_of_dt - timedelta(days=lookback_days)

    points = [
        (ts, price)
        for ts, price in _product_price_series(product_id, store=store, channel=channel)
        if window_start <= ts <= as_of_dt
    ]
    if len(points) < 2:
        return None

    first_ts, first_price = points[0]
    peak_ts, peak_price = max(points, key=lambda pair: pair[1])
    if peak_price <= first_price:
        return None  # no raise observed in the lookback window
    if new_price_d >= peak_price:
        return None  # not actually a discount off the raised peak

    evidence = (
        f"product_id={product_id} as_of={as_of_dt.isoformat()} lookback_days={lookback_days}",
        f"price_raised_from={first_price} (at {first_ts.isoformat()}) to={peak_price} (at {peak_ts.isoformat()})",
        f"proposed_new_price={new_price_d}",
    )
    return InflateThenDiscountWarning(
        product_id=product_id, as_of=as_of_dt.date().isoformat(), raised_from=first_price,
        raised_to=peak_price, raised_at=peak_ts.isoformat(), new_price=new_price_d,
        lookback_days=lookback_days, evidence_trail=evidence,
    )


# -- MAP: observe-and-alert only, jurisdiction-keyed label ----------------------


@dataclass(frozen=True)
class MapAlert:
    """A price observed below a client's declared MAP -- ALWAYS an alert,
    NEVER a blocking gate and NEVER the seed of a retailer-facing
    communication (see module docstring's Colgate-doctrine note).
    ``label``/``message`` text is jurisdiction-keyed via
    ``MarketRules.map_alert_label`` -- the identical underlying signal
    reads "MAP violation" in the US and "dispersion de precios /
    inteligencia de canal" in the EU/UK (Golden Rule 12: a label lookup,
    never a single hardcoded string)."""

    product_id: str
    market: str
    observed_price: Decimal
    map_price: Decimal
    shortfall_pct: Decimal
    label: str
    message: str
    severity: str = "alert"  # ALWAYS "alert" -- see class docstring


def detect_map_alert(
    product_id: str,
    observed_price: Decimal | float,
    map_price: Decimal | float,
    *,
    market: str,
    markets_dir: Path | str = DEFAULT_MARKETS_CONFIG_DIR,
) -> MapAlert | None:
    """Returns a :class:`MapAlert` when ``observed_price`` is below
    ``map_price``, ``None`` otherwise. Never raises for a below-MAP price
    (this is an alert, not a gate) and never produces anything resembling a
    retailer-facing violation letter or acknowledgment request -- callers
    must not build one either (see module docstring).

    Reference example (see tests/test_pricing_guardrails.py): observed=45,
    map_price=50 -> shortfall_pct=10.00, market="us" ->
    label="MAP violation"; the SAME 45-vs-50 pair with market="eu" ->
    label="dispersion de precios / inteligencia de canal" -- same numbers,
    different rendered text, by jurisdiction.
    """
    if map_price <= 0:
        raise ValueError("map_price must be > 0")
    rules = load_market_rules(market, config_dir=markets_dir)
    observed_d = _to_decimal(observed_price)
    map_d = _to_decimal(map_price)
    if observed_d >= map_d:
        return None

    shortfall = ((map_d - observed_d) / map_d * Decimal(100)).quantize(_PCT_QUANT, rounding=ROUND_HALF_UP)
    message = (
        f"{product_id}: observed price {observed_d} is {shortfall}% below the declared MAP "
        f"{map_d} in market '{rules.market}' ({rules.map_alert_label})"
    )
    return MapAlert(
        product_id=product_id, market=rules.market, observed_price=observed_d, map_price=map_d,
        shortfall_pct=shortfall, label=rules.map_alert_label, message=message, severity="alert",
    )


# -- central gate: explanation + citations, or the changeset does not ship -----


@dataclass(frozen=True)
class GuardrailGateResult:
    """Verdict from :func:`gate_price_changeset` -- ``approved=False``
    means the changeset does not ship, full stop (plan section 7 P5's QA
    row: "Gate central: changeset sin explicacion legible + citas => no
    sale")."""

    approved: bool
    reason: str | None  # populated only when approved is False
    citations: tuple[str, ...]


def gate_price_changeset(
    changeset: Changeset,
    *,
    kb: KnowledgeBase,
    candidate_citations: Sequence[GroundedCitation],
    tool_key: str = "pricing",
) -> GuardrailGateResult:
    """The central pre-ship gate for ANY proposed price changeset --
    PR-16's ``price_optimizer`` output, this module's own discount
    validation, or any future caller (plan section 7 P5's central gate row).

    Reuses ``src.writeback.Changeset`` verbatim (it already carries the
    human-legible ``reason`` field -- no second changeset type is built
    here) and ``scm_agent.citation_gate.filter_citations`` (the same
    convention Fase B PR-13 established for ``price_intelligence``;
    ``"pricing"`` is already a registered ``TOOL_CONCEPTS`` key -- no
    second citation mechanism is built here either).

    ``approved=False`` when ``changeset.reason`` is empty/blank, OR when
    ``candidate_citations`` does not survive ``filter_citations`` (fewer
    than its ``MIN_CITATIONS`` resolve) -- either failure alone is enough
    to block.
    """
    if not changeset.reason or not changeset.reason.strip():
        return GuardrailGateResult(
            approved=False,
            reason="changeset is missing a human-legible explanation (writeback.Changeset.reason)",
            citations=(),
        )

    gate = filter_citations(kb, tool_key, list(candidate_citations))
    if not gate.kept:
        return GuardrailGateResult(
            approved=False,
            reason="changeset has no citations that survive the citation gate (scm_agent.citation_gate)",
            citations=(),
        )

    return GuardrailGateResult(approved=True, reason=None, citations=gate.kept)
