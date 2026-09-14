"""How sensitive a field is, and what a reader below its level sees instead.

Access control that stops at "may this principal read this record" is too coarse
for the entity store: a buyer legitimately needs a supplier's name, lead time and
performance while having no business seeing its bank account or credit limit.
Denying the whole record makes the product unusable; returning it whole makes the
product unsellable. The answer is per-field transforms applied on the read path.

Two vocabularies:

**Levels** rank how restricted a value is. The ranking is total so a reader's
clearance can be compared against a field's level with a single comparison.

**Transforms** say what a reader below the bar gets. ``DROP`` is the safe default
because it is the only one that leaks nothing at all - not even the fact that a
value exists. ``MASK`` deliberately leaks a suffix (matching an account number's
last four) and is therefore opt-in per field rather than automatic.

Applied in ``nosilo_core.entities.store`` at read time, never at write time: the
store keeps the true value, and what a caller is allowed to see is decided per
read against that caller's clearance. Redacting on write would destroy data that
a properly-cleared reader is entitled to.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

PUBLIC = "public"
INTERNAL = "internal"
CONFIDENTIAL = "confidential"
RESTRICTED = "restricted"

#: Total ordering of the levels. Injected into ``Capability.attenuate`` rather
#: than imported by it, so the access algebra stays independent of this
#: vocabulary and a domain can supply a different one.
SENSITIVITY_RANK: Mapping[str, int] = {
    PUBLIC: 0,
    INTERNAL: 1,
    CONFIDENTIAL: 2,
    RESTRICTED: 3,
}

VALID_LEVELS = frozenset(SENSITIVITY_RANK)

#: Remove the field entirely. Leaks nothing, not even existence.
DROP = "drop"
#: Replace with a fixed placeholder. Leaks that a value exists.
REDACT = "redact"
#: Keep a short suffix (account numbers, tax ids). Leaks a little, on purpose.
MASK = "mask"
#: Replace a number with the range it falls in. Useful for amounts where the
#: order of magnitude is legitimately needed and the exact figure is not.
BUCKET = "bucket"

VALID_TRANSFORMS = frozenset({DROP, REDACT, MASK, BUCKET})

REDACTED_PLACEHOLDER = "[redacted]"
MASK_CHARACTER = "*"
MASK_VISIBLE_SUFFIX = 4


class ClassificationError(ValueError):
    """Raised on an unknown level or transform."""


def rank(level: str) -> int:
    try:
        return SENSITIVITY_RANK[level]
    except KeyError:
        raise ClassificationError(
            f"unknown sensitivity level {level!r}; expected one of {sorted(VALID_LEVELS)}"
        ) from None


def permits(clearance: str | None, level: str) -> bool:
    """Whether a reader cleared to ``clearance`` may see a ``level`` value.

    ``clearance is None`` means unrestricted. That is the permissive default the
    rest of the repo uses for opt-in gates (``LINCHPIN_API_KEY`` empty disables
    the key check), and it is what keeps every existing caller working unchanged
    - but it is also why ``production_warnings()`` needs to shout about it.
    """
    if clearance is None:
        return True
    return rank(clearance) >= rank(level)


def _bucket(value: Any) -> str:
    """Order-of-magnitude band for a number; anything else falls back to REDACT."""
    try:
        amount = abs(Decimal(str(value)))
    except (ArithmeticError, TypeError, ValueError):
        return REDACTED_PLACEHOLDER
    if amount == 0:
        return "0"
    bound = Decimal(1)
    while bound <= amount:
        bound *= 10
    return f"{bound / 10}-{bound}"


def apply_transform(value: Any, transform: str) -> Any:
    """The value a reader below the bar sees instead of the real one."""
    if transform == REDACT:
        return REDACTED_PLACEHOLDER
    if transform == MASK:
        text = str(value)
        if len(text) <= MASK_VISIBLE_SUFFIX:
            return MASK_CHARACTER * len(text)
        return MASK_CHARACTER * (len(text) - MASK_VISIBLE_SUFFIX) + text[-MASK_VISIBLE_SUFFIX:]
    if transform == BUCKET:
        return _bucket(value)
    raise ClassificationError(
        f"unknown transform {transform!r}; expected one of {sorted(VALID_TRANSFORMS)} "
        f"({DROP} is handled by the caller, which removes the field entirely)"
    )


@dataclass(frozen=True)
class FieldPolicy:
    """How one attribute is classified and what happens to it below clearance."""

    level: str = INTERNAL
    below_clearance: str = DROP

    def __post_init__(self) -> None:
        if self.level not in VALID_LEVELS:
            raise ClassificationError(
                f"unknown sensitivity level {self.level!r}; expected one of {sorted(VALID_LEVELS)}"
            )
        if self.below_clearance not in VALID_TRANSFORMS:
            raise ClassificationError(
                f"unknown transform {self.below_clearance!r}; expected one of {sorted(VALID_TRANSFORMS)}"
            )


@dataclass(frozen=True)
class Classification:
    """A domain's declaration of how one entity type's fields are classified.

    Undeclared fields take ``default``, which is :data:`INTERNAL` rather than
    :data:`PUBLIC`: a field nobody has thought about yet should not be the one
    that leaks. Adding an attribute to an entity must fail closed.
    """

    fields: Mapping[str, FieldPolicy] = None  # type: ignore[assignment]
    default: FieldPolicy = FieldPolicy(level=INTERNAL, below_clearance=DROP)

    def __post_init__(self) -> None:
        if self.fields is None:
            object.__setattr__(self, "fields", {})

    def policy_for(self, name: str) -> FieldPolicy:
        return self.fields.get(name, self.default)


@dataclass(frozen=True)
class Filtered:
    """The result of applying a classification to a set of attribute values."""

    values: Mapping[str, Any]
    #: ``field -> transform`` for everything that was not returned intact. Kept
    #: so a caller can tell a reader *that* something was withheld, and so an
    #: audit entry records what a given principal actually saw.
    transformed: Mapping[str, str]

    @property
    def was_filtered(self) -> bool:
        return bool(self.transformed)


def filter_values(
    values: Mapping[str, Any], classification: Classification, clearance: str | None
) -> Filtered:
    """Apply a classification to raw attribute values for one reader."""
    kept: dict[str, Any] = {}
    transformed: dict[str, str] = {}

    for name in sorted(values):
        policy = classification.policy_for(name)
        if permits(clearance, policy.level):
            kept[name] = values[name]
            continue
        transformed[name] = policy.below_clearance
        if policy.below_clearance != DROP:
            kept[name] = apply_transform(values[name], policy.below_clearance)

    return Filtered(values=kept, transformed=transformed)
