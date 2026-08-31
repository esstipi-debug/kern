"""The header mapper must see every column the registry declares.

The trap this closes, in one sentence: `intake_scan` decides whether a client's
file carries a tool's `required_columns`, but it can only ever *see* a column
that appears in `_canonical_columns()` -- and that set was built from the four
demo profiles in `src.intake_scan.PROFILES` (15 names), not from the registry.

So the moment a tool declares something outside those 15 -- `adu`, `dlt`,
`weight_kg`, `mean_demand`, `on_time` -- `_map_headers` silently drops the
header, the column reads as absent, and the tool goes RED on a folder that
literally contains it. A permanent, invisible false red, produced by the very
act of declaring the contract correctly.

That makes this the hard prerequisite for declaring any data contract: doing the
declarations first and this second would ship a regression and then repair it.

The `no_declarado` verdict itself comes from `src/intake_scan.py`:
``if not tool.required_columns: ... UNDECLARED``.
"""
import pytest

from jobs import intake_scan_job
from jobs.intake_scan_job import _canonical_columns, _canonical_order, _map_headers
from scm_agent.tools import build_default_registry
from src.intake_scan import PROFILES

# Not in any demo profile, and the first name batch 2 will declare (ddmrp's
# average daily usage). Any name outside the profiles would do; a real one keeps
# the test honest about what it is protecting.
_OUTSIDE_THE_PROFILES = "adu"


def _profile_columns() -> set[str]:
    return {col for p in PROFILES for col in (*p.required, *p.optional)}


def _registry_declared_columns() -> set[str]:
    return {
        col
        for tool in build_default_registry().list()
        for col in (*tool.required_columns, *tool.optional_columns)
    }


def test_the_premise_this_module_exists_for():
    """If this ever fails, the demo profiles grew to cover the registry and the
    rest of this module is guarding a problem that no longer exists."""
    assert _OUTSIDE_THE_PROFILES not in _profile_columns()


def test_canonical_order_takes_registry_columns_on_top_of_the_profiles():
    order = _canonical_order(registry_columns=(_OUTSIDE_THE_PROFILES, "product_id"))
    assert _OUTSIDE_THE_PROFILES in order
    # The profiles still come first and are never dropped: their order is what
    # `_map_headers` uses to resolve a header claimed by two canonical names, so
    # reordering them would silently change existing mappings.
    assert order[: len(_canonical_order(registry_columns=()))] == _canonical_order(registry_columns=())
    assert _profile_columns() <= set(order)


def test_a_declared_column_outside_the_profiles_is_mapped_not_ignored(monkeypatch):
    """The behavioural failure, stated directly: a client file whose header IS
    the declared column must map to it."""
    monkeypatch.setattr(
        intake_scan_job,
        "_canonical_columns",
        lambda: _canonical_order(registry_columns=(_OUTSIDE_THE_PROFILES,)),
    )
    mapping = _map_headers(["product_id", _OUTSIDE_THE_PROFILES, "ruido"])
    assert mapping.get(_OUTSIDE_THE_PROFILES) == _OUTSIDE_THE_PROFILES, (
        "the header was present and the tool declares it, yet the mapper dropped it: "
        "this is the false red"
    )


def test_every_column_the_registry_declares_is_visible_to_the_mapper():
    """The standing invariant. Declaring a column the scan cannot see is worse
    than not declaring it: `no_declarado` is at least honest about not knowing,
    while a false RED sends the client to look for a column they already sent."""
    invisible = sorted(_registry_declared_columns() - set(_canonical_columns()))
    assert invisible == [], (
        f"these declared columns are invisible to jobs/intake_scan_job.py::_map_headers, "
        f"so every tool needing them is permanently RED: {invisible}"
    )


@pytest.mark.parametrize("column", sorted(_profile_columns()))
def test_no_profile_column_was_lost_when_the_registry_was_folded_in(column):
    assert column in _canonical_columns()
