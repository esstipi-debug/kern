"""Check digits for logistics identifiers, cross-verified against python-stdnum.

The implementation under test is hand-rolled on purpose (see the module docstring):
``python-stdnum`` lives in the ``dataquality`` extra and prod boots on
``.[web,mcp]``, so a lazy import would leave the check doing nothing in the one
place it matters. These tests use stdnum as an ORACLE - it is present in dev and
CI - so the arithmetic is held to a battle-tested reference without shipping it.
"""

import random

import pytest

from src.doc_identifiers import awb_fault, container_fault, identifier_fault

# ISO 6346's own worked example, and a real MSC box.
VALID_CONTAINERS = ("CSQU3054383", "MSCU5285725")


def test_a_valid_container_has_no_fault():
    for number in VALID_CONTAINERS:
        assert container_fault(number) is None


def test_a_wrong_check_digit_is_named_as_such():
    fault = container_fault("MSKU1234567")

    assert fault is not None
    assert "check digit" in fault
    assert "MSKU1234567" in fault


def test_a_value_that_is_not_a_container_number_is_a_format_fault():
    fault = container_fault("CONTAINER 4")

    assert fault is not None
    assert "format" in fault
    assert "fails" not in fault  # do not blame arithmetic for a shape problem


def test_container_separators_and_case_are_tolerated():
    assert container_fault("  csqu 305438 3 ") is None


def test_container_check_digit_matches_stdnum_over_a_random_corpus():
    """The oracle test: agree with python-stdnum on 500 generated numbers."""
    iso6346 = pytest.importorskip("stdnum.iso6346")
    rng = random.Random(20260911)
    owners = ("MSCU", "CSQU", "TEMU", "MAEU", "HLXU", "TGHU")

    disagreements = []
    for _ in range(500):
        candidate = rng.choice(owners) + f"{rng.randrange(10_000_000):07d}"
        ours_ok = container_fault(candidate) is None
        theirs_ok = iso6346.is_valid(candidate)
        if ours_ok != theirs_ok:
            disagreements.append((candidate, ours_ok, theirs_ok))

    assert not disagreements, f"disagreed with stdnum on: {disagreements[:5]}"


def test_the_corpus_actually_contains_both_verdicts():
    """Guard the guard: a corpus of all-invalid numbers would pass vacuously."""
    iso6346 = pytest.importorskip("stdnum.iso6346")
    rng = random.Random(20260911)
    owners = ("MSCU", "CSQU", "TEMU", "MAEU", "HLXU", "TGHU")
    verdicts = {
        iso6346.is_valid(rng.choice(owners) + f"{rng.randrange(10_000_000):07d}")
        for _ in range(500)
    }

    assert verdicts == {True, False}


# --- air waybill -------------------------------------------------------------
# IATA: 3-digit airline prefix + 7-digit serial + 1 check digit = serial mod 7.
# The rule is documented in this repo's own schema note for air_waybill.


def test_a_valid_awb_has_no_fault():
    assert awb_fault("020-12345675") is None  # 1234567 % 7 == 5


def test_an_awb_with_a_wrong_check_digit_is_flagged():
    fault = awb_fault("020-12345670")

    assert fault is not None
    assert "check digit" in fault


def test_awb_separators_are_tolerated():
    assert awb_fault("020 1234567 5") is None
    assert awb_fault("02012345675") is None


def test_an_awb_of_the_wrong_length_is_a_format_fault():
    fault = awb_fault("123456")

    assert fault is not None
    assert "format" in fault


# --- the dispatcher ----------------------------------------------------------


def test_only_the_canonical_keys_with_a_standard_are_checked():
    assert identifier_fault("container_no", "MSKU1234567") is not None
    assert identifier_fault("awb_no", "020-12345670") is not None
    # bl_no has no check digit: carrier-specific format, nothing to verify.
    assert identifier_fault("bl_no", "MSKU1234567") is None
    assert identifier_fault("shipper", "anything at all") is None


def test_an_absent_value_is_not_a_fault():
    for empty in (None, "", "   "):
        assert identifier_fault("container_no", empty) is None
