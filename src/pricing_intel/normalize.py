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


# Pack-size phrases, EN + ES/PT (the plan's ICP markets -- section 6.2's
# "ICP LatAm"). Each alternative has exactly one capture group so the first
# non-None group across the whole match is the pack size.
_PACK_SIZE_RE = re.compile(
    r"""
    pack\s*(?:of)?\s*(\d+)\b        |   # "pack of 6", "pack 6"
    (\d+)\s*-?\s*pack\b             |   # "6-pack", "6 pack", "6pack"
    paquete\s*(?:de)?\s*(\d+)\b     |   # "paquete de 6"
    pacote\s*(?:de)?\s*(\d+)\b      |   # "pacote de 6" (pt-BR)
    caja\s*(?:de)?\s*(\d+)\b        |   # "caja de 12"
    (?:^|\s)(\d+)\s*x\b             |   # "6x", "6 x" (unit count prefix)
    x\s*(\d+)\b                         # "x6"
    """,
    re.IGNORECASE | re.VERBOSE,
)


def extract_pack_size(text: str) -> int | None:
    """Best-effort pack/case-size detection from a product title or
    description. Returns ``None`` (never a guessed default of 1) when no
    recognized pattern matches -- callers should treat "unknown pack size"
    as "do not unit-price this offer", not as "assume a single unit".
    """
    if not text:
        return None
    match = _PACK_SIZE_RE.search(text)
    if not match:
        return None
    for group in match.groups():
        if group:
            size = int(group)
            return size if size > 0 else None
    return None  # pragma: no cover -- unreachable: a match always has a captured group


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
