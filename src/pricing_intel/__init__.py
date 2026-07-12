"""Pricing intelligence titan -- the data spine (Linchpin 3.0 PR-10) plus the
extraction cascade (PR-11).

See ``models`` for the frozen dataclasses (``CompetitorOffer``, ``PricePoint``,
``MatchCandidate``, ``SiteConfig``) and ``ledger`` for the append-only,
SQLite-indexed, parquet/CSV-partitioned ``PriceLedger`` store built on top of
them. ``acquire.structured`` (L1 JSON-LD/microdata/OpenGraph), ``extract``
(the 5-level cascade) and ``normalize`` (the single price-string ->
Decimal/currency funnel) are PR-11. Matching (``match/``) is a later PR --
nothing in this package performs network I/O.
"""

from __future__ import annotations

from .extract import ExtractionFailed, ExtractionResult, extract_price
from .ledger import (
    AppendResult,
    BatchRecord,
    DuplicateBatchError,
    LedgerRecord,
    PriceLedger,
    default_ledger,
)
from .models import (
    ACQUISITION_TIERS,
    AVAILABILITY_VALUES,
    BASE_CURRENCY,
    MATCH_METHODS,
    MATCH_STATUSES,
    OFFER_COLUMNS,
    TOS_DECISIONS,
    CompetitorOffer,
    MatchCandidate,
    OfferFrameValidationError,
    PricePoint,
    SiteConfig,
    dataframe_to_offers,
    offers_to_dataframe,
    validate_offer_frame,
)
from .normalize import (
    NormalizedPrice,
    PriceNormalizationError,
    detect_promo,
    extract_pack_size,
    normalize_price_string,
    parse_shipping_note,
    unit_price,
)

__all__ = [
    "ACQUISITION_TIERS",
    "AVAILABILITY_VALUES",
    "BASE_CURRENCY",
    "MATCH_METHODS",
    "MATCH_STATUSES",
    "OFFER_COLUMNS",
    "TOS_DECISIONS",
    "AppendResult",
    "BatchRecord",
    "CompetitorOffer",
    "DuplicateBatchError",
    "ExtractionFailed",
    "ExtractionResult",
    "LedgerRecord",
    "MatchCandidate",
    "NormalizedPrice",
    "OfferFrameValidationError",
    "PriceLedger",
    "PriceNormalizationError",
    "PricePoint",
    "SiteConfig",
    "dataframe_to_offers",
    "default_ledger",
    "detect_promo",
    "extract_pack_size",
    "extract_price",
    "normalize_price_string",
    "offers_to_dataframe",
    "parse_shipping_note",
    "unit_price",
    "validate_offer_frame",
]
