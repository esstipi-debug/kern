"""Price-string normalization -- the single funnel every extraction tier's
raw price text passes through on its way to a ``Decimal`` + ISO 4217
currency (Linchpin 3.0 PR-11, ``src/pricing_intel``).

Plan section 6.1/6.4: "TODO precio (incluso de API/JSON-LD) pasa por
price-parser >=0.5.1 hacia Decimal" -- ``extract.py``'s five cascade tiers
all fetch their raw price text/number from a different place (a JSON-LD
``Offer.price``, a microdata property, a CSS-selector node's text, a
free-text candidate near the title) but every single one calls
``normalize_price_string`` here rather than doing its own ``Decimal(...)``
parsing. One normalizer, one set of locale edge cases, instead of N
slightly-different ad hoc parsers drifting apart over time.

Currency resolution (why this module never guesses a bare "$"): price-parser
detects a *symbol* from the text ("$", "R$", "kr", ...), not an ISO 4217
code -- and several of those symbols are shared by multiple real currencies
(US/MX/AR/CL all use "$"). This module accepts an explicit ``currency``
(ISO 4217, known from context -- a JSON-LD ``priceCurrency``, a future
SiteConfig's declared market) that both disambiguates price-parser's own
decimal-separator heuristics (its ``currency_hint`` mechanism) AND becomes
the returned currency code outright. Without one, only a small table of
genuinely unambiguous symbols (EUR's euro sign, GBP's pound sign, BRL's
"R$", JPY's yen sign) resolves; anything else raises
``PriceNormalizationError`` rather than silently defaulting to USD -- plan
section 6.4's own words: "un precio dudoso es peor que ningun precio".

Hand-verified reference values (plan section 6.10's required locale set --
see ``tests/test_pricing_intel_normalize.py`` for the executable proof):
  USD "$1,234.56"        -> Decimal("1234.56"), "USD"
  EUR "1.234,56" + euro sign -> Decimal("1234.56"), "EUR"
  MXN "1.234,56"          -> Decimal("1234.56"), "MXN"  (currency="MXN" explicit)
  BRL "R$ 1.234,56"       -> Decimal("1234.56"), "BRL"
  CLP "12.345" (no cents) -> Decimal("12345"),   "CLP"  (currency="CLP" explicit)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# importlib.metadata lookup with a pinned-version fallback -- same idea as
# src/state/store.py's _HAS_PARQUET_ENGINE degrade flag, except here the
# package is always present (pricing-intel extra) and we only need its
# *version string* for extractor_version provenance (plan rule 7), never a
# behavioral branch.
try:
    PRICE_PARSER_VERSION = _pkg_version("price-parser")
except PackageNotFoundError:  # pragma: no cover -- defensive; extra installs cleanly in CI
    PRICE_PARSER_VERSION = "0.5.1"  # pyproject's pinned floor


class PriceNormalizationError(ValueError):
    """Raw price text could not be resolved to a valid (Decimal, ISO 4217
    currency) pair -- either price-parser found no numeric amount at all, or
    the currency is ambiguous and no explicit ISO hint was supplied. Never
    guessed (plan section 6.4: "un precio dudoso es peor que ningun
    precio")."""


class PriceParserUnavailable(RuntimeError):
    """``normalize_price_string`` needs the ``price-parser`` package, which only
    the optional ``pricing-intel`` extra installs -- the base ``.[web,mcp]``
    production install does not. Deliberately NOT a subclass of
    ``PriceNormalizationError`` (a ValueError callers routinely catch and treat
    as "this candidate had no valid price"), so a missing-dependency
    environment error propagates loudly with an actionable "install the extra"
    message instead of being silently swallowed as "no price found"."""


def _load_price_class():
    """Import price-parser's ``Price`` on demand, keeping this module (on the
    app's boot chain via ``pricing_intel/__init__`` -> ``extract`` ->
    ``normalize``) import-safe when the ``pricing-intel`` extra is absent.
    Behavior is unchanged when the package is installed (dev/CI/tests)."""
    try:
        from price_parser import Price
    except ImportError as exc:  # pragma: no cover - exercised only in a prod-like env without the extra
        raise PriceParserUnavailable(
            "price normalization requires price-parser; install the pricing-intel "
            "extra: pip install '.[pricing-intel]'"
        ) from exc
    return Price


# Symbols that map unambiguously to exactly one ISO 4217 code. Deliberately
# small: "$" alone (USD/MXN/ARS/CLP/...), "kr" (SEK/NOK/DKK), and similar
# multi-country symbols are NOT here on purpose -- callers must pass an
# explicit ``currency`` for those (see module docstring).
_UNAMBIGUOUS_SYMBOLS: dict[str, str] = {
    "€": "EUR",  # e;
    "£": "GBP",  # a3
    "¥": "JPY",  # a5
    "R$": "BRL",
}


@dataclass(frozen=True)
class NormalizedPrice:
    """Result of normalizing one raw price string."""

    amount: Decimal
    currency: str  # ISO 4217, uppercase, always 3 letters


def normalize_price_string(raw: str, *, currency: str | None = None) -> NormalizedPrice:
    """Parse ``raw`` (any locale's price text -- with or without a currency
    symbol) into a ``NormalizedPrice``.

    ``currency`` should be the ISO 4217 code already known from context (a
    JSON-LD ``priceCurrency``, a site's declared market, ...). Passing it
    does two things at once: it resolves the returned currency
    deterministically, AND it feeds price-parser's own ``currency_hint``
    mechanism, which is what correctly disambiguates "1.234,56" as
    thousands-dot/decimal-comma (European/Latin American convention) versus
    "1,234.56" as thousands-comma/decimal-dot (US convention) -- price
    strings with no symbol at all are otherwise genuinely ambiguous.

    When ``currency`` is omitted, the ISO code is recovered from whatever
    symbol/code price-parser detected in ``raw`` itself, through a small
    unambiguous-symbol table or a bare 3-letter code already present in the
    text (e.g. "USD 19.99"). Raises ``PriceNormalizationError`` if no amount
    parses, or if the currency cannot be resolved either way.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PriceNormalizationError(f"raw price text must be a non-empty string, got {raw!r}")

    explicit_currency = _validate_iso_currency(currency) if currency is not None else None
    parsed = _load_price_class().fromstring(raw, currency_hint=explicit_currency)
    if parsed.amount is None:
        raise PriceNormalizationError(f"could not parse a price amount out of {raw!r}")

    resolved_currency = explicit_currency or _resolve_currency_from_symbol(parsed.currency, raw)
    return NormalizedPrice(amount=parsed.amount, currency=resolved_currency)


def _validate_iso_currency(currency: str) -> str:
    code = currency.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise PriceNormalizationError(f"currency must be a 3-letter ISO 4217 code, got {currency!r}")
    return code


def _resolve_currency_from_symbol(detected: str | None, raw: str) -> str:
    if detected:
        mapped = _UNAMBIGUOUS_SYMBOLS.get(detected.strip())
        if mapped:
            return mapped
        candidate = detected.strip().upper()
        if len(candidate) == 3 and candidate.isalpha():
            return candidate  # price-parser already recovered a bare ISO code, e.g. "USD 19.99"
    raise PriceNormalizationError(
        f"currency is ambiguous for {raw!r} (price-parser detected {detected!r}); "
        "pass an explicit ISO 4217 currency"
    )


# Static FX-to-USD table (Linchpin 3.0 PR-13): a small, hand-verified,
# illustrative set of rates covering the majors + the plan's ICP LatAm
# markets (section 6.2) -- NOT a live feed. The plan's own models.py
# docstring anticipated "PR-11's normalize.py is what actually computes
# this at scale from live FX feeds"; no live source is wired up as of this
# PR, so this constant is the documented placeholder until one is (a future
# PR swapping this for a real feed only has to change this one lookup, the
# same "isolate behind one funnel" idea as the rest of this module).
# Never guessed per-call -- an unmapped currency raises rather than being
# silently treated as 1:1 with USD.
STATIC_FX_TO_USD: dict[str, Decimal] = {
    "USD": Decimal("1"),
    "EUR": Decimal("1.08"),
    "GBP": Decimal("1.27"),
    "MXN": Decimal("0.058"),
    "BRL": Decimal("0.18"),
    "ARS": Decimal("0.0011"),
    "CLP": Decimal("0.0011"),
    "COP": Decimal("0.00025"),
    "PEN": Decimal("0.27"),
}


def convert_to_base_currency(amount: Decimal, currency: str) -> Decimal:
    """Convert ``amount`` (in ``currency``) to ``models.BASE_CURRENCY`` (USD)
    via :data:`STATIC_FX_TO_USD`.

    Raises ``PriceNormalizationError`` for a currency this PR's static table
    does not cover -- an unconverted price must never enter the ledger's
    ``price_normalized`` field silently mislabeled as USD (plan rule 14).

    Hand-verified reference: ``convert_to_base_currency(Decimal("1234.56"),
    "MXN") == Decimal("1234.56") * Decimal("0.058") == Decimal("71.60448")``
    -- the exact example worked by ``models.py``'s own module docstring.
    """
    code = currency.strip().upper()
    rate = STATIC_FX_TO_USD.get(code)
    if rate is None:
        raise PriceNormalizationError(
            f"no static FX rate available for currency {currency!r} -- "
            f"supported: {sorted(STATIC_FX_TO_USD)}"
        )
    return amount * rate


def detect_promo(price: Decimal, list_price: Decimal | None) -> bool:
    """True when ``list_price`` is present and strictly greater than
    ``price`` -- the plan's "list_price vs price divergence" promo signal
    (section 6.3). A missing ``list_price`` is not a promo (nothing to
    compare); an equal ``list_price`` is not a promo either (no discount).
    """
    if list_price is None:
        return False
    return list_price > price


# Units of MEASURE (mass/volume/length), as opposed to units of COUNT. A
# number followed by one of these is a quantity of stuff, never a number of
# sellable units -- see the three false positives documented on
# ``extract_pack_size`` below.
_MEASURE_UNIT = r"(?:kgs?|kilos?|kilogramos?|grs?|gramos?|g|lts?|litros?|l|ml|cc|mts?|metros?|cm|mm|oz|lbs?)"
_UNIT_SUFFIX_RE = re.compile(rf"\s*{_MEASURE_UNIT}\b", re.IGNORECASE)
_MEASURE_WORD_RE = re.compile(rf"^{_MEASURE_UNIT}$", re.IGNORECASE)

# Pack-size phrases, EN + ES/PT (the plan's ICP markets -- section 6.2's
# "ICP LatAm"). Named groups so each candidate can be guarded on its own
# terms; scanned left-to-right, first survivor wins.
_PACK_SIZE_RE = re.compile(
    r"""
    (?:pack\s*(?:of)?|paquete\s*(?:de)?|pacote\s*(?:de)?|caja\s*(?:de)?)
        \s*(?P<phrase>\d+)\b                        |  # "pack of 6", "caja de 12"
    (?P<npack>\d+)\s*-?\s*pack\b                    |  # "6-pack", "6 pack"
    (?P<a>\d+)\s*x\s*(?P<b>\d+(?:[.,]\d+)?)\s*(?P<bunit>[a-z]+)?  |  # "4x2,5kg", "11x11"
    (?<![\dx])x\s*(?P<xn>\d+)\b                     |  # "x6"
    (?<!\d)(?P<nx>\d+)\s*x(?![\dx])                    # "6x"
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _pack_size_candidate(match: re.Match[str], text: str) -> int | None:
    """One regex hit -> a pack size, or None when a guard rejects it."""
    groups = match.groupdict()

    # The "A x B" shape is ambiguous and the two readings are both common in
    # real catalogs, so it is decided by whether B carries a unit:
    #   "caja 4x2,5kg"  -> B has a unit: A boxes of 2,5 kg each -> pack is A
    #   "papas 11x11"   -> B is bare:    a CUT DIMENSION, not a count at all
    if groups["a"] is not None:
        unit = groups["bunit"]
        if unit and _MEASURE_WORD_RE.match(unit):
            return int(groups["a"])
        return None

    for name in ("phrase", "npack", "xn", "nx"):
        raw = groups[name]
        if raw is None:
            continue
        # A count is never followed by a unit of measure: "caja x 12 kilos"
        # is twelve KILOS in one box, not twelve boxes.
        if _UNIT_SUFFIX_RE.match(text, match.end(name)):
            return None
        return int(raw)
    return None  # pragma: no cover -- every alternative has a named group


def extract_pack_size(text: str) -> int | None:
    """Best-effort pack/case-size detection from a product title or
    description. Returns ``None`` (never a guessed default of 1) when no
    recognized pattern matches -- callers should treat "unknown pack size"
    as "do not unit-price this offer", not as "assume a single unit".

    Three false positives found against real Chilean frozen-food catalogs
    (puntomarinochile.cl, rapa-nui.cl), each of which silently produced a
    wrong ``unit_price`` rather than an honest ``None``:

    1. ``"Langostino entero u15 2kg. (caja x 12 kilos)"`` returned 12. Those
       twelve are KILOS. The box holds 12 kg in 2 kg units, i.e. 6 units, so
       ``unit_price`` divided by double the right number.
    2. ``"Papas prefritas 11X11 1kg"`` returned 11. ``11x11`` is the cut
       dimension of the fry in millimetres -- it is not a count of anything.
    3. ``"Papas prefritas corte 13x13 caja 4x2,5kg"`` returned 13, shadowing
       the real pack size (4) that appears later in the same title.

    The guards are therefore: a candidate followed by a unit of measure is
    rejected, and an ``A x B`` pair is a pack size only when ``B`` carries a
    unit -- otherwise it is a dimension. A bare ``"x6"``/``"6x"`` keeps its
    original meaning.
    """
    if not text:
        return None
    for match in _PACK_SIZE_RE.finditer(text):
        size = _pack_size_candidate(match, text)
        if size is not None and size > 0:
            return size
    return None


def unit_price(price: Decimal, pack_size: int) -> Decimal:
    """Price per sellable unit given a pack/case size. ``pack_size`` must be
    a known positive integer (e.g. from ``extract_pack_size`` or a catalog
    field) -- never guessed."""
    if not isinstance(pack_size, int) or pack_size <= 0:
        raise ValueError(f"pack_size must be a positive integer, got {pack_size!r}")
    return price / Decimal(pack_size)


_FREE_SHIPPING_PHRASES = (
    "free shipping",
    "shipping included",
    "envio gratis",
    "envío gratis",
    "frete gratis",
    "frete grátis",
    "envio gratuito",
    "envío gratuito",
)


def parse_shipping_note(text: str | None, *, currency: str | None = None) -> Decimal | None:
    """Parse a shipping/tax note into a ``Decimal`` amount.

    Returns ``None`` when ``text`` is ``None`` or blank -- "no shipping note
    was supplied" (distinct from "shipping is free", see below). A
    recognized free-shipping phrase (EN/ES/PT) resolves to ``Decimal("0")``
    -- a *known* cost, not missing data. Anything else funnels through
    ``normalize_price_string`` so shipping amounts stay on the exact same
    normalizer as prices; a note that mentions shipping but carries no
    parseable amount and isn't a known free-shipping phrase raises
    ``PriceNormalizationError`` rather than silently returning ``None`` --
    that would be indistinguishable from "no note at all" (plan rule 14: no
    silent caps/drops).
    """
    if text is None or not text.strip():
        return None
    lowered = text.strip().lower()
    if any(phrase in lowered for phrase in _FREE_SHIPPING_PHRASES):
        return Decimal("0")
    return normalize_price_string(text, currency=currency).amount
