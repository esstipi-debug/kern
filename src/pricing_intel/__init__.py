"""Pricing intelligence titan -- the data spine (Linchpin 3.0 PR-10), the
extraction cascade (PR-11), and product matching (``match/``, PR-14).

See ``models`` for the frozen dataclasses (``CompetitorOffer``, ``PricePoint``,
``MatchCandidate``, ``SiteConfig``) and ``ledger`` for the append-only,
SQLite-indexed, parquet/CSV-partitioned ``PriceLedger`` store built on top of
them. ``acquire.structured`` (L1 JSON-LD/microdata/OpenGraph), ``extract``
(the 5-level cascade) and ``normalize`` (the single price-string ->
Decimal/currency funnel) are PR-11. ``match/`` (PR-14) is the GTIN/fuzzy/
probabilistic/LLM-adjudication matching pipeline plus the versioned
``sku_map`` store -- see ``match/__init__.py`` for the full pipeline
writeup. Nothing in this package performs network I/O except ``sku_map.py``
and ``ledger.py`` (their own on-disk stores, by design).
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
from .match import (
    AdjudicationRequest,
    AdjudicationResult,
    BlockingCandidate,
    LlmAdjudicationResponse,
    ProbabilisticScore,
    ProductAttributes,
    SkuMap,
    SkuMapEntry,
    adjudicate_pair,
    block_candidates,
    blocking_score,
    classify_score,
    default_sku_map,
    match_by_gtin,
    normalize_gtin,
    score_pair,
    score_to_match_candidate,
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
    STATIC_FX_TO_USD,
    NormalizedPrice,
    PriceNormalizationError,
    convert_to_base_currency,
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
    "STATIC_FX_TO_USD",
    "TOS_DECISIONS",
    "AdjudicationRequest",
    "AdjudicationResult",
    "AppendResult",
    "BatchRecord",
    "BlockingCandidate",
    "CompetitorOffer",
    "DuplicateBatchError",
    "ExtractionFailed",
    "ExtractionResult",
    "LedgerRecord",
    "LlmAdjudicationResponse",
    "MatchCandidate",
    "NormalizedPrice",
    "OfferFrameValidationError",
    "PriceLedger",
    "PriceNormalizationError",
    "PricePoint",
    "ProbabilisticScore",
    "ProductAttributes",
    "SiteConfig",
    "SkuMap",
    "SkuMapEntry",
    "adjudicate_pair",
    "block_candidates",
    "blocking_score",
    "classify_score",
    "convert_to_base_currency",
    "dataframe_to_offers",
    "default_ledger",
    "default_sku_map",
    "detect_promo",
    "extract_pack_size",
    "extract_price",
    "match_by_gtin",
    "normalize_gtin",
    "normalize_price_string",
    "offers_to_dataframe",
    "parse_shipping_note",
    "score_pair",
    "score_to_match_candidate",
    "unit_price",
    "validate_offer_frame",
]
