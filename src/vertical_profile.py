"""Per-vertical operating profile — the fourth level of parameter resolution.

    params  >  client_profile  >  config/verticals/<vertical>.yaml  >  engine default

The engine already carries the maths for several industries — Croston and
``src.forecastability`` for intermittent demand, ``src.lots.expiry``/``fefo``
and ``jobs.newsvendor_job`` for perishables, ``jobs.earned_value_job`` and
``src.ddmrp_decoupling`` for project-driven work. What it did not carry was a
way to *choose* between them: ``ClientProfile`` has no industry field, so every
account fell through to the generic defaults (0.95 service level, 0.25 holding
rate in ``src/batch.py``; the ABC service map in ``src/classification.py``).
Those are textbook numbers, and in food or construction they are simply wrong.

This module is that selector, as data. Same declarative pattern as
``config/markets/*.yaml`` and ``src.pricing_guardrails.load_market_rules``:
every field here is DATA a caller reads, never an ``if vertical == "..."``
branch in ``src/``. The control metric, sibling to "client-specific code ~0
lines":

    A NEW VERTICAL = 0 LINES IN THE ENGINE.

Resolution semantics, which are deliberately asymmetric:

* A client with **no** declared vertical resolves as it always did — the
  vertical layer is simply absent. Adding this module changes no existing
  behaviour.
* A client that **declares** a vertical with no profile on disk raises
  :class:`VerticalNotConfiguredError`. It does not silently fall back to the
  generic defaults, for the same reason ``load_market_rules`` refuses to run
  against an unconfigured jurisdiction: a wrong-but-plausible number delivered
  with confidence is worse than a refusal.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Reused rather than forked on purpose. This repo has already paid for the
# alternative once: three byte-identical copies of jobs.intake._norm drifted in
# jobs/pricing.py, jobs/flow_map_job.py and jobs/org_friction_job.py, all three
# carrying the same accent-folding bug, and none of them learned about the fix.
# One validator, one place. The import direction (vertical -> client) is the
# only runtime edge; client_profile imports back under TYPE_CHECKING only, so
# there is no cycle.
from src.client_profile import _require_positive_finite
from src.hats import HAT_KEYS

# config/verticals/<vertical>.yaml -- one file per industry. Overridable the
# same way DEFAULT_MARKETS_CONFIG_DIR is, so tests never touch the repo's real
# config/ directory unless they choose to.
DEFAULT_VERTICALS_CONFIG_DIR = Path(
    os.environ.get("LINCHPIN_VERTICALS_CONFIG_DIR", "").strip() or "config/verticals"
)

# Hyphenated lowercase slug ("repuestos-industriales"). Anchored and
# separator-free by construction, so a vertical name can never escape the
# config directory -- same guarantee _SAFE_MARKET gives load_market_rules.
_SAFE_VERTICAL = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# What the pack DECLARES it expects to see, not what it imposes.
# src.forecasting already picks Croston vs SES per SKU from the observed ADI
# (>= 1.32, Syntetos-Boylan-Croston 2005 via src.forecastability), and that
# choice-from-evidence beats a label written in a config file every time.
VALID_REGIMES = frozenset({"intermittent", "smooth", "mixed", "seasonal"})

# Engine params, keyed exactly as ClientProfile.as_params() emits them. Any
# other key in a pack's engine_params block would reach the tools as an
# unknown parameter and be ignored in silence -- the worst possible failure
# mode for a config file -- so it is rejected at load time instead.
ENGINE_PARAM_FIELDS = ("service_level", "holding_rate", "order_cost",
                       "lead_time_days", "objection_thresholds")


class VerticalNotConfiguredError(LookupError):
    """No ``config/verticals/<vertical>.yaml`` exists for a vertical a client
    explicitly declared. The hard gate: resolution refuses to fall back to the
    engine's generic defaults, because those defaults are exactly what the pack
    exists to correct."""

    def __init__(self, vertical: str, path: Path) -> None:
        self.vertical = vertical
        self.path = path
        super().__init__(
            f"no vertical profile for '{vertical}' at {path} -- parameter resolution "
            "refuses to fall back to the engine's generic defaults for a declared "
            "industry (a new vertical is a config/verticals/*.yaml file, not a code change)"
        )


def _require_fraction(value: object, label: str) -> None:
    """Service levels are probabilities: strictly inside (0, 1).

    1.0 is rejected as well as 0.0 -- a 100% service level makes the safety
    stock term z*sigma diverge, so it is never a real target, only a data
    entry mistake."""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number, got {value!r}")
    if not math.isfinite(value) or not 0 < value < 1:
        raise ValueError(f"{label} must be in (0, 1), got {value!r}")


@dataclass(frozen=True)
class VerticalEngineParams:
    """The pack's economics. Field names match ``ClientProfile.as_params()``
    verbatim so a reader can diff the two layers without a mapping table."""

    service_level: float | None = None
    holding_rate: float | None = None
    order_cost: float | None = None
    lead_time_days: float | None = None
    objection_thresholds: dict[str, float] | None = None

    def __post_init__(self) -> None:
        _require_fraction(self.service_level, "service_level")
        _require_positive_finite(self.holding_rate, "holding_rate")
        _require_positive_finite(self.order_cost, "order_cost")
        if self.lead_time_days is not None:
            # Deliberately impossible, not merely discouraged.
            #
            # as_params() documents lead_time_days as "the default lead time
            # when a client's CSV carries none". A per-INDUSTRY default there
            # is the exact defect this product sells against: sizing safety
            # stock against a promised lead time instead of the observed one.
            # The observed value comes from receipt_date - order_date and from
            # nowhere else, which is the same rule webapp/demo_lead_time_scan.py
            # already enforces with a dedicated test.
            #
            # A per-CLIENT default is still legal (ClientProfile.lead_time_days)
            # -- that is one operator recording one client's real number, not a
            # promise generalised across an industry.
            raise ValueError(
                "lead_time_days must be null in a vertical pack: a per-industry lead "
                "time is a promise, and sizing against promises is the defect the "
                "lead-time scan exists to expose. Record it per client instead."
            )
        if self.objection_thresholds is not None:
            for key, value in self.objection_thresholds.items():
                if key not in HAT_KEYS:
                    raise ValueError(
                        f"objection_thresholds key {key!r} is not a known area "
                        f"(expected one of {HAT_KEYS})")
                if (isinstance(value, bool)
                        or not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0)):
                    raise ValueError(
                        f"objection_thresholds[{key!r}] must be a finite number > 0, "
                        f"got {value!r}")

    def as_params(self) -> dict:
        """Non-None engine params, keyed exactly as the tools already read them.

        Same contract as ``ClientProfile.as_params()`` -- that identity is what
        makes the merge in ``merge_params`` a plain dict layering with no
        translation step."""
        return {k: v for k in ENGINE_PARAM_FIELDS if (v := getattr(self, k)) is not None}


@dataclass(frozen=True)
class DemandProfile:
    """Expected demand regime, and the models a pack forbids outright."""

    expected_regime: str = "mixed"
    # Empty for the two packs that ship today, and defined anyway: the FOOD
    # pack needs it for real -- there, sizing safety stock without a shelf-life
    # constraint has to be vetoed, because the buffer that protects against a
    # stockout is the buffer that expires, so "raise the buffer" destroys
    # value. Defining the field before it is needed is cheaper than
    # retrofitting it with live packs in the field.
    forbidden_methods: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.expected_regime not in VALID_REGIMES:
            raise ValueError(
                f"expected_regime must be one of {sorted(VALID_REGIMES)}, "
                f"got {self.expected_regime!r}")
        for method in self.forbidden_methods:
            if not isinstance(method, str) or not method.strip():
                raise ValueError(f"forbidden_methods entries must be non-empty strings, got {method!r}")


@dataclass(frozen=True)
class ClassificationRules:
    """Per-class service levels, overriding ``src.classification._SERVICE_LEVEL``
    (today a module-level constant: {A: 0.98, B: 0.95, C: 0.90})."""

    service_level_by_class: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for klass, value in self.service_level_by_class.items():
            if not isinstance(klass, str) or not klass.strip():
                raise ValueError(f"service_level_by_class key must be a non-empty string, got {klass!r}")
            _require_fraction(value, f"service_level_by_class[{klass!r}]")


@dataclass(frozen=True)
class VerticalProfile:
    """One industry's declarative operating profile, loaded from
    ``config/verticals/<vertical>.yaml`` by :func:`load_vertical_profile`."""

    vertical: str
    display_name: str
    demo_question_label: str = ""
    engine_params: VerticalEngineParams = field(default_factory=VerticalEngineParams)
    demand: DemandProfile = field(default_factory=DemandProfile)
    classification: ClassificationRules = field(default_factory=ClassificationRules)
    # Ordered: index 0 is the output the funnel leads with. Not the same across
    # industries -- in distribution, pooling moves from fifth to second because
    # it is the one industry where multi-warehouse holds by definition.
    diagnostic_outputs: tuple[str, ...] = ()
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    vocabulary: dict[str, str] = field(default_factory=dict)
    objections: tuple[dict[str, str], ...] = ()
    stockout_cost_usd: float | None = None
    stockout_prompt: str = ""

    def __post_init__(self) -> None:
        if not self.vertical or not _SAFE_VERTICAL.match(self.vertical):
            raise ValueError(
                f"vertical must be a lowercase hyphenated slug (e.g. 'distribucion-b2b'), "
                f"got {self.vertical!r}")
        if not self.display_name or not self.display_name.strip():
            raise ValueError("display_name must be a non-empty string")
        if len(set(self.diagnostic_outputs)) != len(self.diagnostic_outputs):
            raise ValueError(
                f"diagnostic_outputs must not repeat an output: {self.diagnostic_outputs!r} "
                "-- the list is an ORDER, and a duplicate makes the intended position ambiguous")
        for canon, entries in self.aliases.items():
            if not isinstance(canon, str) or not canon.strip():
                raise ValueError(f"aliases key must be a non-empty string, got {canon!r}")
            if not entries:
                raise ValueError(f"aliases[{canon!r}] is empty -- drop the key instead")
            for entry in entries:
                if not isinstance(entry, str) or not entry.strip():
                    raise ValueError(f"aliases[{canon!r}] entries must be non-empty strings, got {entry!r}")
        for key, value in self.vocabulary.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"vocabulary[{key!r}] must be a non-empty string, got {value!r}")
        for objection in self.objections:
            if set(objection) != {"claim", "response"}:
                raise ValueError(
                    f"each objection must have exactly 'claim' and 'response', got {sorted(objection)}")
        _require_positive_finite(self.stockout_cost_usd, "stockout_cost_usd")

    def as_params(self) -> dict:
        """Delegates to the engine-params block, so callers layer profiles
        uniformly: ``vertical.as_params()`` and ``profile.as_params()`` return
        the same shape."""
        return self.engine_params.as_params()

    def merged_aliases(
        self, base: Mapping[str, Sequence[str]]
    ) -> dict[str, list[str]]:
        """``base`` (normally ``jobs.intake.ALIASES``) with this pack's aliases
        PREPENDED, returned as a new dict -- ``base`` is never mutated.

        Prepended, never appended. ``detect_columns`` walks the list and the
        first alias that matches wins (jobs/intake.py:270), so appending would
        leave a generic alias shadowing the industry-specific one and the pack
        would stop doing the only thing it exists to do.

        Canonical names the pack introduces and ``base`` does not have
        (``location_id``, ``equipment_id``, ...) are CREATED here rather than
        indexed. ``detect_columns`` reads ``ALIASES[canon]`` with a bracket, so
        a caller that tried to prepend by indexing would raise ``KeyError`` on
        exactly the fields the pack adds.
        """
        merged: dict[str, list[str]] = {}
        for canon in (*self.aliases, *base):
            if canon in merged:
                continue
            entries = [*self.aliases.get(canon, ()), *base.get(canon, ())]
            merged[canon] = list(dict.fromkeys(entries))  # dedupe, first wins
        return merged


def _vertical_config_path(vertical: str, config_dir: Path | str) -> Path:
    normalized = vertical.strip().lower()
    if not _SAFE_VERTICAL.match(normalized):
        raise ValueError(
            f"vertical must be a lowercase hyphenated slug (e.g. 'distribucion-b2b'), "
            f"got {vertical!r}")
    return Path(config_dir) / f"{normalized}.yaml"


def _require_mapping(raw: object, path: Path, block: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: '{block}' must be a YAML mapping, got {type(raw).__name__}")
    return dict(raw)


def load_vertical_profile(
    vertical: str, *, config_dir: Path | str = DEFAULT_VERTICALS_CONFIG_DIR
) -> VerticalProfile:
    """Load and validate ``config/verticals/<vertical>.yaml``.

    Raises :class:`VerticalNotConfiguredError` if the file does not exist, or
    ``ValueError``/``TypeError`` from the dataclasses' ``__post_init__`` if it
    exists but fails validation -- either way, never returns a half-valid
    profile. Same contract as ``load_market_rules``.
    """
    normalized = vertical.strip().lower()
    path = _vertical_config_path(vertical, config_dir)
    if not path.exists():
        raise VerticalNotConfiguredError(normalized, path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a YAML mapping, got {type(raw).__name__}")

    data = dict(raw)
    data.setdefault("vertical", normalized)
    if data["vertical"] != normalized:
        raise ValueError(
            f"{path}: 'vertical' field {data['vertical']!r} does not match filename "
            f"vertical {normalized!r}")

    engine_raw = _require_mapping(data.pop("engine_params", None), path, "engine_params")
    unknown = set(engine_raw) - set(ENGINE_PARAM_FIELDS)
    if unknown:
        # Loud, because the alternative is silent: an unrecognised key would
        # travel all the way to a tool and be dropped without a word.
        raise ValueError(
            f"{path}: engine_params has key(s) {sorted(unknown)} that ClientProfile.as_params() "
            f"does not emit (expected a subset of {list(ENGINE_PARAM_FIELDS)}) -- they would "
            "reach the engine as unknown parameters and be ignored in silence")

    demand_raw = _require_mapping(data.pop("demand", None), path, "demand")
    if "forbidden_methods" in demand_raw:
        demand_raw["forbidden_methods"] = tuple(demand_raw["forbidden_methods"] or ())
    classification_raw = _require_mapping(data.pop("classification", None), path, "classification")

    aliases_raw = _require_mapping(data.pop("aliases", None), path, "aliases")
    data["aliases"] = {canon: tuple(entries or ()) for canon, entries in aliases_raw.items()}
    data["vocabulary"] = _require_mapping(data.pop("vocabulary", None), path, "vocabulary")
    data["diagnostic_outputs"] = tuple(data.pop("diagnostic_outputs", None) or ())
    data["objections"] = tuple(
        dict(o) for o in (data.pop("objections", None) or [])
    )

    data["engine_params"] = VerticalEngineParams(**engine_raw)
    data["demand"] = DemandProfile(**demand_raw)
    data["classification"] = ClassificationRules(**classification_raw)
    return VerticalProfile(**data)


def list_verticals(*, config_dir: Path | str = DEFAULT_VERTICALS_CONFIG_DIR) -> list[str]:
    """Vertical slugs with a profile on disk, sorted.

    This is what /demo's single question ("what do you move?") should be built
    from. Reading the directory rather than hardcoding a list is what keeps the
    control metric true: a third vertical is a file, not a code change.
    """
    directory = Path(config_dir)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml"))
