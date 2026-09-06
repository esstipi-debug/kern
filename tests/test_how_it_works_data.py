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
    ISO_9001_CLAUSES,
    ISO_28000_ELEMENTS,
    SCOR_BUCKET_ORDER,
    TOOLS,
    tally_by_domain_area,
    tally_by_scor_bucket,
)


def test_tools_table_matches_the_registry_exactly() -> None:
    """THE drift guard -- the one that would have caught this PR's bug.

    TOOLS is hand-maintained and feeds a client-facing page that gets quoted
    in sales conversations, so a stale entry is a wrong claim to a prospect,
    not just a cosmetic count. It silently fell SEVEN tools behind the
    registry between the launch_readiness PR and PR #199 (supplier_management,
    network_design, disruption_scan, hat_tension, hat_settlement, flow_map,
    cross_impact) because a `register()` call with no matching ToolInfo broke
    nothing.

    Set equality in BOTH directions, like tests/test_scm_agent.py's registry
    test: registering a tool without listing it fails here, and so does
    listing a tool that no longer exists. The exact-count assertions below
    stay as a second, cheaper tripwire -- they are what make the intended
    number an explicit, reviewable diff instead of a silently moving target.
    """
    from scm_agent.tools import build_default_registry

    registered = {t.key for t in build_default_registry().list()}
    listed = [t.key for t in TOOLS]

    assert len(listed) == len(set(listed)), "duplicate ToolInfo key"
    assert set(listed) == registered


def test_exactly_53_tools_with_unique_keys() -> None:
    assert len(TOOLS) == 53
    assert len({t.key for t in TOOLS}) == 53


def test_domain_area_tally_sums_to_53_and_matches_spec() -> None:
    tally = tally_by_domain_area()
    assert sum(tally.values()) == 53
    assert tally == {
        "Inventory & replenishment": 9,
        "Network & logistics": 9,
        "Inventory control & health": 8,
        "Pricing & finance": 6,
        "Demand & classification": 3,
        "Procurement & sourcing": 4,
        "Returns, risk & benchmarking": 4,
        "Planning cadence & projects": 3,
        "Decision alignment": 3,
        "Procedures & standard work": 2,
        "Leadership": 2,
    }
    assert set(tally) == set(DOMAIN_AREA_ORDER)


def test_scor_bucket_tally_sums_to_53_and_matches_spec() -> None:
    tally = tally_by_scor_bucket()
    assert sum(tally.values()) == 53
    assert tally == {
        "Plan": 17,
        "Order/Fulfill": 9,
        "Orchestrate": 16,
        "Transform": 4,
        "Source": 4,
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
