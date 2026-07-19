"""Unit tests for webapp/how_it_works_data.py -- the static content data
backing GET /how-it-works. No HTTP client needed; these are pure-data
invariant checks."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from webapp.how_it_works_data import (  # noqa: E402
    CERTIFICATIONS,
    DOMAIN_AREA_ORDER,
    HONEST_GAPS,
    ISO_28000_ELEMENTS,
    ISO_9001_CLAUSES,
    SCOR_BUCKET_ORDER,
    TOOLS,
    tally_by_domain_area,
    tally_by_scor_bucket,
)


def test_exactly_41_tools_with_unique_keys() -> None:
    assert len(TOOLS) == 41
    assert len({t.key for t in TOOLS}) == 41


def test_domain_area_tally_sums_to_41_and_matches_spec() -> None:
    tally = tally_by_domain_area()
    assert sum(tally.values()) == 41
    assert tally == {
        "Inventory & replenishment": 9,
        "Network & logistics": 7,
        "Inventory control & health": 6,
        "Pricing & finance": 6,
        "Demand & classification": 3,
        "Procurement & sourcing": 3,
        "Returns, risk & benchmarking": 3,
        "Planning cadence & projects": 3,
        "Leadership": 1,
    }
    assert set(tally) == set(DOMAIN_AREA_ORDER)


def test_scor_bucket_tally_sums_to_41_and_matches_spec() -> None:
    tally = tally_by_scor_bucket()
    assert sum(tally.values()) == 41
    assert tally == {
        "Plan": 15,
        "Order/Fulfill": 8,
        "Orchestrate": 8,
        "Transform": 4,
        "Source": 3,
        "Return": 2,
        "Outside SCOR scope": 1,
    }
    assert set(tally) == set(SCOR_BUCKET_ORDER)


def test_every_tool_uses_a_known_domain_area_and_scor_bucket() -> None:
    for tool in TOOLS:
        assert tool.domain_area in DOMAIN_AREA_ORDER, tool.key
        assert tool.scor_bucket in SCOR_BUCKET_ORDER, tool.key
        assert tool.label and tool.one_liner, tool.key


def test_five_certifications_with_valid_levels_and_nonempty_lists() -> None:
    assert len(CERTIFICATIONS) == 5
    assert {c.name for c in CERTIFICATIONS} == {"CPIM", "CLTD", "CSCP", "SCPro", "CPSM"}
    for cert in CERTIFICATIONS:
        assert cert.level in {"High", "Medium-high", "Partial"}
        assert cert.covered
        assert cert.gaps


def test_iso_clauses_and_gaps_are_nonempty() -> None:
    assert len(ISO_9001_CLAUSES) >= 8
    assert len(ISO_28000_ELEMENTS) >= 3
    assert len(HONEST_GAPS) >= 5
    for gap in HONEST_GAPS:
        assert gap.name and gap.current_state and gap.standard
