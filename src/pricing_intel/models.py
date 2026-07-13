"""Data spine for the pricing titan (Linchpin 3.0 PR-10, ``src/pricing_intel``).

Frozen dataclasses for every externally-observed or derived pricing-intel
record, plus the pure conversion helpers that turn a batch of
``CompetitorOffer`` into the flat, engine-agnostic ``pandas.DataFrame`` shape
``ledger.py`` actually persists (and back). No I/O happens in this module --
only ``ledger.py`` touches disk.

Scope note (Linchpin 3.0 plan S6.1 file tree): this PR owns the *shape* of
``CompetitorOffer``, ``PricePoint``, ``MatchCandidate`` and ``SiteConfig``.
The cascades that populate them are later PRs: ``extract.py``/``normalize.py``
(PR-11) fill in ``CompetitorOffer``, ``match/`` (PR-14) fills in
``MatchCandidate``, and ``config/sites/*.yaml`` loading (PR-12) is what
actually enforces ``SiteConfig`` against a real registry.

Normalization convention (plan S6.3: ``price_normalized`` is "a moneda base,
unit price"): this PR fixes the convention so later PRs have one thing to
target -- base currency is USD (``BASE_CURRENCY``) and "unit" means price per
smallest sellable unit (a single item, not a pack/case; pack-size normalization
is PR-11's ``normalize.py`` concern). Worked-by-hand example: a competitor
offer priced at MXN 1,234.56 with a stated FX rate of 0.058 USD/MXN normalizes
to ``Decimal('1234.56') * Decimal('0.058') = Decimal('71.60448')`` USD -- see
``tests/test_pricing_intel_models.py::test_price_normalized_hand_verified_fx_example``
for the reference computation this PR ships. PR-11's ``normalize.py`` is what
actually computes this at scale from live FX feeds; this PR only carries the
field, its meaning, and one proof that the arithmetic behind it is exact.

Storage dtype design (why every column ``offers_to_dataframe`` produces is a
plain Python ``str``, never a native ``Decimal``/``Timestamp``/``bool``):
measured directly against this repo's pinned pandas/pyarrow (see PR notes),
writing genuine ``Decimal`` objects to parquet round-trips correctly *only*
when every value in a column shares the same scale -- pyarrow infers one
``decimal128(precision, scale)`` per column from whatever it sees, and pads
lower-scale values with trailing zeros to match the widest scale in the
column (``Decimal('19.99')`` next to ``Decimal('123.456')`` comes back as
``Decimal('19.990')``). That is numerically equal (``==`` still holds) but not
*byte*-identical, which is what this PR's QA bar (golden round-trip) demands.
Encoding every field -- including ``observed_at`` and ``promo_flag`` -- as an
explicit canonical string sidesteps the whole class of engine-inference
surprises and makes the parquet and CSV (no-pyarrow-fallback) paths behave
identically, which a mixed-dtype frame does not.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd

try:  # optional strict schema validation -- same graceful-degradation idiom as
    # src/state/system_state.py and src/sku_dedup.py (rapidfuzz -> difflib).
    import pandera.pandas as pa

    _HAS_PANDERA = True
except ImportError:
    try:
        import pandera as pa  # older pandera versions expose this at the top level
        _HAS_PANDERA = True
    except ImportError:
        pa = None
        _HAS_PANDERA = False

# Base currency every CompetitorOffer.price_normalized is denominated in (see
# module docstring's "Normalization convention"). PR-11's normalize.py is the
# only place this constant should be consumed for real FX math; PR-10 only
# fixes the convention.
BASE_CURRENCY = "USD"

AVAILABILITY_VALUES = ("InStock", "OutOfStock", "Preorder")
ACQUISITION_TIERS = ("L0", "L1", "L2", "L3")
TOS_DECISIONS = ("allowed", "limited", "prohibited")
MATCH_METHODS = ("gtin", "fuzzy", "probabilistic", "llm", "human")
MATCH_STATUSES = ("confirmed", "suspect", "rejected")

# Canonical column order for the flat storage frame -- exactly CompetitorOffer's
# field order (plan S6.3). ledger.py appends its own bookkeeping columns
# (e.g. "is_correction") after these; dataframe_to_offers() tolerates and
# ignores any extra columns beyond this set (same "strict=False" convention as
# src/state/system_state.py's DOMAIN_COLUMNS).
OFFER_COLUMNS: tuple[str, ...] = (
    "observed_at",
    "site",
    "competitor_sku_ref",
    "matched_product_id",
    "match_confidence",
    "price",
    "currency",
    "price_normalized",
    "shipping",
    "availability",
    "promo_flag",
    "list_price",
    "acquisition_tier",
    "extractor",
    "extractor_version",
    "extraction_confidence",
)


def _require_nonempty(field: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string, got {value!r}")


@dataclass(frozen=True)
class CompetitorOffer:
    """One externally-observed competitor price, with total provenance (plan
    rule 7: acquisition tier, extractor + version, confidence and timestamp
    travel with every observed data point, never optional plumbing).

    Field list and order are verbatim from plan S6.3. Validated eagerly in
    ``__post_init__`` -- a bad value never makes it into a ``CompetitorOffer``
    in the first place, matching this repo's "fail fast at the boundary"
    convention (coding-style.md) rather than deferring the check to whatever
    downstream code happens to read the field.
    """

    observed_at: datetime  # UTC
    site: str  # normalized domain
    competitor_sku_ref: str  # URL or external id (ASIN/MLA/...)
    matched_product_id: str | None  # our SKU, via match/ (PR-14)
    match_confidence: float  # 0-1; <threshold => match/ keeps it out of the
    # main ledger (PR-14's concern -- this PR stores the field, not the gate)
    price: Decimal
    currency: str
    price_normalized: Decimal  # base currency (BASE_CURRENCY), unit price
    shipping: Decimal | None
    availability: str  # InStock/OutOfStock/Preorder
    promo_flag: bool
    list_price: Decimal | None
    acquisition_tier: str  # L0/L1/L2/L3 (procedence, plan rule 7)
    extractor: str
    extractor_version: str
    extraction_confidence: float

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError(
                f"observed_at must be timezone-aware UTC, got {self.observed_at!r}"
            )
        _require_nonempty("site", self.site)
        if "://" in self.site or " " in self.site or self.site != self.site.strip():
            raise ValueError(f"site must be a bare normalized domain, got {self.site!r}")
        _require_nonempty("competitor_sku_ref", self.competitor_sku_ref)
        if self.matched_product_id is not None and not self.matched_product_id.strip():
            raise ValueError("matched_product_id must be None or a non-empty string, got ''")
        if not (0.0 <= self.match_confidence <= 1.0):
            raise ValueError(f"match_confidence must be within [0, 1], got {self.match_confidence!r}")
        if not isinstance(self.price, Decimal):
            raise TypeError(f"price must be a decimal.Decimal, got {type(self.price).__name__}")
        if self.price <= 0:
            raise ValueError(f"price must be > 0, got {self.price!r}")
        _require_nonempty("currency", self.currency)
        if self.currency != self.currency.upper():
            raise ValueError(f"currency must be an uppercase code, got {self.currency!r}")
        if not isinstance(self.price_normalized, Decimal):
            raise TypeError(
                f"price_normalized must be a decimal.Decimal, got {type(self.price_normalized).__name__}"
            )
        if self.price_normalized <= 0:
            raise ValueError(f"price_normalized must be > 0, got {self.price_normalized!r}")
        if self.shipping is not None:
            if not isinstance(self.shipping, Decimal):
                raise TypeError(f"shipping must be None or a decimal.Decimal, got {type(self.shipping).__name__}")
            if self.shipping < 0:
                raise ValueError(f"shipping must be >= 0 when present, got {self.shipping!r}")
        if self.availability not in AVAILABILITY_VALUES:
            raise ValueError(f"availability must be one of {AVAILABILITY_VALUES}, got {self.availability!r}")
        if self.list_price is not None:
            if not isinstance(self.list_price, Decimal):
                raise TypeError(
                    f"list_price must be None or a decimal.Decimal, got {type(self.list_price).__name__}"
                )
            if self.list_price <= 0:
                raise ValueError(f"list_price must be > 0 when present, got {self.list_price!r}")
        if self.acquisition_tier not in ACQUISITION_TIERS:
            raise ValueError(
                f"acquisition_tier must be one of {ACQUISITION_TIERS}, got {self.acquisition_tier!r}"
            )
        _require_nonempty("extractor", self.extractor)
        _require_nonempty("extractor_version", self.extractor_version)
        if not (0.0 <= self.extraction_confidence <= 1.0):
            raise ValueError(
                f"extraction_confidence must be within [0, 1], got {self.extraction_confidence!r}"
            )


@dataclass(frozen=True)
class PricePoint:
    """A single (product, site, time) normalized price reading -- the light
    read-shape used by price-history views and the ``price_position_matrix``
    deliverable (PR-13), stripped of the acquisition provenance a
    ``CompetitorOffer`` carries. Shape only; PR-13 is what actually builds the
    matrix. ``currency`` is always ``BASE_CURRENCY`` here since
    ``price_normalized`` is defined as base-currency (see module docstring).
    """

    matched_product_id: str | None
    site: str
    observed_at: datetime
    price_normalized: Decimal
    currency: str = BASE_CURRENCY

    def __post_init__(self) -> None:
        _require_nonempty("site", self.site)
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() != timedelta(0):
            raise ValueError(f"observed_at must be timezone-aware UTC, got {self.observed_at!r}")
        if not isinstance(self.price_normalized, Decimal):
            raise TypeError(
                f"price_normalized must be a decimal.Decimal, got {type(self.price_normalized).__name__}"
            )
        if self.price_normalized <= 0:
            raise ValueError(f"price_normalized must be > 0, got {self.price_normalized!r}")
        _require_nonempty("currency", self.currency)

    @staticmethod
    def from_offer(offer: CompetitorOffer) -> PricePoint:
        """Project a full ``CompetitorOffer`` down to its read-shape."""
        return PricePoint(
            matched_product_id=offer.matched_product_id,
            site=offer.site,
            observed_at=offer.observed_at,
            price_normalized=offer.price_normalized,
        )


@dataclass(frozen=True)
class MatchCandidate:
    """One step's output in the product-matching pipeline (plan S6.5) -- shape
    only. PR-14 implements the actual GTIN/fuzzy/probabilistic/LLM cascade and
    the ``sku_map``'s human-review workflow this record feeds; this PR just
    fixes what a candidate looks like so PR-14 has a stable target.
    """

    our_product_id: str
    competitor_sku_ref: str
    site: str
    method: str  # "gtin" | "fuzzy" | "probabilistic" | "llm" | "human"
    score: float  # 0-1
    status: str  # "confirmed" | "suspect" | "rejected"
    reason: str = ""
    confirmed_by: str | None = None  # who/what confirmed (plan S6.5 point 5)
    confirmed_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_nonempty("our_product_id", self.our_product_id)
        _require_nonempty("competitor_sku_ref", self.competitor_sku_ref)
        _require_nonempty("site", self.site)
        if self.method not in MATCH_METHODS:
            raise ValueError(f"method must be one of {MATCH_METHODS}, got {self.method!r}")
        if self.status not in MATCH_STATUSES:
            raise ValueError(f"status must be one of {MATCH_STATUSES}, got {self.status!r}")
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score must be within [0, 1], got {self.score!r}")
        if self.confirmed_at is not None and (
            self.confirmed_at.tzinfo is None or self.confirmed_at.utcoffset() != timedelta(0)
        ):
            raise ValueError(f"confirmed_at must be None or timezone-aware UTC, got {self.confirmed_at!r}")


@dataclass(frozen=True)
class SiteConfig:
    """Per-domain compliance + acquisition envelope (plan S6.7): robots.txt,
    ToS decision, rate limit, max acquisition tier allowed, PII posture.
    Shape only -- PR-12 is what actually loads and enforces this against
    ``config/sites/*.yaml``; ``is_approved`` below is the derived check PR-12
    will call, not a substitute for it.
    """

    domain: str
    robots_txt_respected: bool
    robots_checked_at: str  # ISO date the robots.txt decision was last verified
    tos_summary: str
    tos_decision: str  # "allowed" | "limited" | "prohibited"
    rate_limit_seconds: float
    max_tier_allowed: str  # "L0".."L3"
    pii_policy: str = "none"  # always "none" for this product (plan S6.0 #2)
    selectors_version: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty("domain", self.domain)
        _require_nonempty("robots_checked_at", self.robots_checked_at)
        _require_nonempty("tos_summary", self.tos_summary)
        if self.tos_decision not in TOS_DECISIONS:
            raise ValueError(f"tos_decision must be one of {TOS_DECISIONS}, got {self.tos_decision!r}")
        if self.rate_limit_seconds < 0:
            raise ValueError(f"rate_limit_seconds must be >= 0, got {self.rate_limit_seconds!r}")
        if self.max_tier_allowed not in ACQUISITION_TIERS:
            raise ValueError(
                f"max_tier_allowed must be one of {ACQUISITION_TIERS}, got {self.max_tier_allowed!r}"
            )
        if self.pii_policy != "none":
            raise ValueError(
                "pii_policy must be 'none' -- plan S6.0 principle 2 (zero PII) has no exceptions"
            )

    @property
    def is_approved(self) -> bool:
        """Whether a fetcher may run at all against this domain (plan S6.7:
        "Sin YAML aprobado el fetcher no corre")."""
        return self.tos_decision != "prohibited" and self.robots_txt_respected


# -- storage-frame conversion (models <-> the flat frame ledger.py persists) --


def _none_if_missing(value: object) -> str | None:
    """Collapse every "missing" sentinel a round trip can produce -- ``None``,
    ``""``, or a stray float NaN from a pandas "str" column -- to ``None``."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value)
    return None if text == "" else text


def _decimal_or_none(value: object) -> Decimal | None:
    text = _none_if_missing(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"cannot parse {value!r} as a Decimal") from exc


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1"):
        return True
    if text in ("false", "0"):
        return False
    raise ValueError(f"cannot interpret {value!r} as a bool")


def offers_to_dataframe(offers: Sequence[CompetitorOffer]) -> pd.DataFrame:
    """Flatten ``offers`` into the canonical all-``str`` storage frame (see
    module docstring's "Storage dtype design"). Every value is either a plain
    string or the empty string ``""`` standing in for a ``None`` optional
    field -- never a native ``Decimal``/``Timestamp``/``bool``/NaN, so the
    frame round-trips byte-identically through both parquet and the CSV
    fallback with no engine-specific dtype inference involved.
    """
    if not offers:
        raise ValueError("offers must be non-empty")
    rows = []
    for o in offers:
        rows.append(
            {
                "observed_at": o.observed_at.astimezone(timezone.utc).isoformat(),
                "site": o.site,
                "competitor_sku_ref": o.competitor_sku_ref,
                "matched_product_id": "" if o.matched_product_id is None else o.matched_product_id,
                "match_confidence": repr(float(o.match_confidence)),
                "price": str(o.price),
                "currency": o.currency,
                "price_normalized": str(o.price_normalized),
                "shipping": "" if o.shipping is None else str(o.shipping),
                "availability": o.availability,
                "promo_flag": "True" if o.promo_flag else "False",
                "list_price": "" if o.list_price is None else str(o.list_price),
                "acquisition_tier": o.acquisition_tier,
                "extractor": o.extractor,
                "extractor_version": o.extractor_version,
                "extraction_confidence": repr(float(o.extraction_confidence)),
            }
        )
    return pd.DataFrame(rows, columns=list(OFFER_COLUMNS))


def dataframe_to_offers(df: pd.DataFrame) -> list[CompetitorOffer]:
    """Inverse of ``offers_to_dataframe``. Extra columns beyond
    ``OFFER_COLUMNS`` (e.g. ledger.py's ``is_correction`` bookkeeping column)
    are ignored, matching ``src/state/system_state.py``'s "strict=False"
    convention for schema contracts."""
    missing = [c for c in OFFER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"dataframe is missing required columns: {missing}")
    offers = []
    for _, row in df.iterrows():
        offers.append(
            CompetitorOffer(
                observed_at=datetime.fromisoformat(str(row["observed_at"])),
                site=str(row["site"]),
                competitor_sku_ref=str(row["competitor_sku_ref"]),
                matched_product_id=_none_if_missing(row["matched_product_id"]),
                match_confidence=float(row["match_confidence"]),
                price=Decimal(str(row["price"])),
                currency=str(row["currency"]),
                price_normalized=Decimal(str(row["price_normalized"])),
                shipping=_decimal_or_none(row["shipping"]),
                availability=str(row["availability"]),
                promo_flag=_to_bool(row["promo_flag"]),
                list_price=_decimal_or_none(row["list_price"]),
                acquisition_tier=str(row["acquisition_tier"]),
                extractor=str(row["extractor"]),
                extractor_version=str(row["extractor_version"]),
                extraction_confidence=float(row["extraction_confidence"]),
            )
        )
    return offers


class OfferFrameValidationError(ValueError):
    """A storage frame handed to the ledger failed its column contract. Raised
    by ``validate_offer_frame`` before anything is written to disk -- defense
    in depth for a frame built directly (bulk path) rather than via
    ``offers_to_dataframe``/individual ``CompetitorOffer`` construction."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        super().__init__("offer frame rejected: " + "; ".join(issues))


def _validate_fallback(df: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    required_nonempty = (
        "site",
        "competitor_sku_ref",
        "currency",
        "availability",
        "acquisition_tier",
        "extractor",
        "extractor_version",
    )
    for col in required_nonempty:
        if col not in df.columns:
            issues.append(f"{col}: missing required column")
            continue
        if (df[col].astype(str) == "").any() or df[col].isna().any():
            issues.append(f"{col}: contains an empty/missing value")

    if "availability" in df.columns:
        bad = ~df["availability"].isin(AVAILABILITY_VALUES)
        if bad.any():
            issues.append(f"availability: values must be one of {AVAILABILITY_VALUES}")
    if "acquisition_tier" in df.columns:
        bad = ~df["acquisition_tier"].isin(ACQUISITION_TIERS)
        if bad.any():
            issues.append(f"acquisition_tier: values must be one of {ACQUISITION_TIERS}")
    if "promo_flag" in df.columns:
        bad = ~df["promo_flag"].astype(str).isin(("True", "False"))
        if bad.any():
            issues.append("promo_flag: values must be 'True' or 'False'")
    for col in ("price", "price_normalized"):
        if col not in df.columns:
            continue
        for value in df[col]:
            try:
                if Decimal(str(value)) <= 0:
                    issues.append(f"{col}: values must be > 0")
                    break
            except InvalidOperation:
                issues.append(f"{col}: {value!r} does not parse as a Decimal")
                break
    for col in ("match_confidence", "extraction_confidence"):
        if col not in df.columns:
            continue
        for value in df[col]:
            try:
                if not (0.0 <= float(value) <= 1.0):
                    issues.append(f"{col}: values must be within [0, 1]")
                    break
            except (TypeError, ValueError):
                issues.append(f"{col}: {value!r} does not parse as a float")
                break
    return issues


def validate_offer_frame(df: pd.DataFrame) -> None:
    """Raise ``OfferFrameValidationError`` if ``df`` does not satisfy the
    storage-frame contract; returns ``None`` (no side effects) otherwise.
    Uses ``pandera`` when the optional ``state`` extra is installed, else the
    equivalent hand-rolled check above (same degrade pattern as
    ``src/state/system_state.py``)."""
    if _HAS_PANDERA:
        checks_by_col = {
            "site": pa.Column(str, checks=pa.Check.str_length(min_value=1), nullable=False),
            "competitor_sku_ref": pa.Column(str, checks=pa.Check.str_length(min_value=1), nullable=False),
            "currency": pa.Column(str, checks=pa.Check.str_length(min_value=1), nullable=False),
            "extractor": pa.Column(str, checks=pa.Check.str_length(min_value=1), nullable=False),
            "extractor_version": pa.Column(str, checks=pa.Check.str_length(min_value=1), nullable=False),
            "availability": pa.Column(str, checks=pa.Check.isin(AVAILABILITY_VALUES), nullable=False),
            "acquisition_tier": pa.Column(str, checks=pa.Check.isin(ACQUISITION_TIERS), nullable=False),
            "promo_flag": pa.Column(str, checks=pa.Check.isin(("True", "False")), nullable=False),
        }
        schema = pa.DataFrameSchema(checks_by_col, strict=False)
        try:
            schema.validate(df, lazy=True)
        except pa.errors.SchemaErrors as exc:
            records = exc.failure_cases[["column", "check", "failure_case"]].to_dict("records")
            issues = [f"{r['column']}: {r['check']} failed on {r['failure_case']!r}" for r in records]
            raise OfferFrameValidationError(issues or [str(exc)]) from exc
        except pa.errors.SchemaError as exc:
            raise OfferFrameValidationError([str(exc)]) from exc
        # pandera's Column checks above don't express "parses as a positive
        # Decimal/bounded float" cleanly -- reuse the fallback for those.
        issues = [
            i
            for i in _validate_fallback(df)
            if not i.startswith(("site:", "competitor_sku_ref:", "currency:", "extractor:", "extractor_version:", "availability:", "acquisition_tier:", "promo_flag:"))
        ]
        if issues:
            raise OfferFrameValidationError(issues)
    else:
        issues = _validate_fallback(df)
        if issues:
            raise OfferFrameValidationError(issues)
