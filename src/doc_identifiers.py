"""Check digits for logistics identifiers: container (ISO 6346), air waybill (IATA).

A field the broker will read off the pack is worth verifying, not just transcribing:
a container number carries its own check digit, so a typo is detectable from the
value alone, with no second document to compare against.

Why hand-rolled instead of ``python-stdnum``: that package sits in the
``dataquality`` extra and production boots on ``.[web,mcp]``, so a lazy import
would leave this check silently doing nothing in the one place it matters. Both
algorithms are published, fixed and short. ``tests/test_doc_identifiers.py``
cross-verifies this implementation against ``stdnum.iso6346`` over a generated
corpus, so the arithmetic answers to a battle-tested oracle in CI without the
dependency reaching prod.

Pure functions. No I/O. A fault is a REASON string; None means "nothing to say" -
either the value passes, or its field has no standard to check it against.
"""

from __future__ import annotations

import re

# ISO 6346 letter values: 10..38 assigned A..Z, skipping every multiple of 11.
_LETTER_VALUES = {}
_value = 10
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    while _value % 11 == 0:
        _value += 1
    _LETTER_VALUES[_letter] = _value
    _value += 1

_CONTAINER_RE = re.compile(r"^([A-Z]{3})([UJZ])(\d{6})(\d)$")
_AWB_RE = re.compile(r"^(\d{3})(\d{7})(\d)$")


def _compact(raw: object) -> str:
    """Strip the spaces and dashes people type into these fields."""
    return re.sub(r"[\s-]", "", str(raw)).upper()


def container_fault(raw: object) -> str | None:
    """Reason ``raw`` is not a valid ISO 6346 container number, or None.

    An empty value is not a fault: absence is the ``missing`` list's business.
    """
    if raw is None:
        return None
    text = _compact(raw)
    if not text:
        return None
    match = _CONTAINER_RE.match(text)
    if match is None:
        return (
            f"'{raw}' is not in ISO 6346 format (3 owner letters + equipment "
            "category U/J/Z + 6 digits + check digit)"
        )
    owner, category, serial, stated = match.groups()
    total = sum(
        _LETTER_VALUES[char] * (2 ** position)
        for position, char in enumerate(owner + category)
    )
    total += sum(
        int(char) * (2 ** (4 + position)) for position, char in enumerate(serial)
    )
    expected = (total % 11) % 10  # a remainder of 10 is written as 0
    if expected != int(stated):
        return (
            f"'{raw}' fails the ISO 6346 check digit: expected {expected}, "
            f"the number carries {stated}"
        )
    return None


def awb_fault(raw: object) -> str | None:
    """Reason ``raw`` is not a valid IATA air waybill number, or None.

    Format: 3-digit airline prefix + 7-digit serial + check digit = serial mod 7.
    """
    if raw is None:
        return None
    text = _compact(raw)
    if not text:
        return None
    match = _AWB_RE.match(text)
    if match is None:
        return (
            f"'{raw}' is not in IATA air waybill format (3-digit airline prefix "
            "+ 7-digit serial + check digit)"
        )
    _prefix, serial, stated = match.groups()
    expected = int(serial) % 7
    if expected != int(stated):
        return (
            f"'{raw}' fails the IATA check digit: expected {expected}, "
            f"the number carries {stated}"
        )
    return None


# Canonical pack keys that carry a self-verifying identifier. Everything absent
# from this map is deliberately unchecked: a B/L number's format is the carrier's
# own, and a SCAC would need NMFTA's licensed database.
_CHECKS = {
    "container_no": container_fault,
    "awb_no": awb_fault,
}


def identifier_fault(canonical: str, raw: object) -> str | None:
    """Reason ``raw`` fails the standard that governs ``canonical``, or None."""
    check = _CHECKS.get(canonical)
    return check(raw) if check is not None else None
