"""The vocabulary for printing a number that was never measured.

PR #193 turned a dozen "flattering fallback" values into ``None``. Every caller that
renders one now needs the same two things, and they were being re-invented privately
per module: words for "not measured", and a way to keep an unscoreable entity out of a
ranking instead of letting it sort/compare as if it had scored.

``min``/``max``/``sort``/TOPSIS on a ``None`` raise TypeError - loud, and better than
the old lie, but still not an answer. The answer is to drop what could not be scored
and say which ones were dropped.
"""

import pytest

from src.deliverable import days, money, not_measured, pct, ppm
from src.supplier_scorecard import Evaluable, SupplierScore, rankable

_MEASURED_ALL = Evaluable(on_time=4, in_full=4, otif=4, lead_time=4, ppm=4)
_MEASURED_NONE = Evaluable(on_time=0, in_full=0, otif=0, lead_time=0, ppm=0)


def _card(name: str, *, otif=0.9, lead=3.0, defects_ppm=120.0) -> SupplierScore:
    return SupplierScore(
        supplier=name, deliveries=4, on_time_rate=otif, in_full_rate=otif, otif=otif,
        avg_lead_time=lead, ppm=defects_ppm,
        evaluable=_MEASURED_ALL if otif is not None else _MEASURED_NONE,
    )


# ---- formatters -------------------------------------------------------------


def test_a_measured_rate_prints_as_a_percentage():
    assert pct(0.9312) == "93%"


def test_an_unmeasured_rate_prints_the_words_not_the_number_zero():
    assert pct(None) == "not measured"
    assert pct(None) != "0%"


def test_the_formatters_speak_the_deck_language():
    assert pct(None, "es") == "no medido"
    assert days(None, "es") == "no medido"
    assert ppm(None, "es") == "no medido"


def test_a_genuine_zero_is_still_a_measurement_and_prints_as_one():
    """The whole point: 'we looked and it was zero' must not read as 'we never looked'."""
    assert pct(0.0) == "0%"
    assert days(0.0) == "0 days"
    assert ppm(0.0) == "0 PPM"


def test_measured_quantities_carry_their_units():
    assert days(3.4) == "3 days"
    assert ppm(1234.6) == "1,235 PPM"    # thousands separator, no decimals


def test_an_unmeasured_amount_of_money_is_not_free():
    """`landed_cost(...).per_unit` is None over zero units; $0.00/unit reads as free."""
    assert money(None) == "not measured"
    assert money(None, "es") == "no medido"


def test_a_measured_amount_of_money_prints_with_its_symbol_and_cents():
    assert money(12.5) == "$12.50"
    assert money(0.0) == "$0.00"
    assert money(1234.5, symbol="R$") == "R$1,234.50"


def test_the_formatters_agree_with_the_shared_i18n_token():
    assert pct(None) == not_measured("en")
    assert days(None, "es") == not_measured("es")


# ---- rankable ---------------------------------------------------------------


def test_a_panel_where_everyone_scored_ranks_whole():
    panel, dropped = rankable([_card("A"), _card("B")], ("otif", "avg_lead_time"))

    assert [c.supplier for c in panel] == ["A", "B"]
    assert dropped == ()


def test_a_supplier_that_never_measured_a_criterion_leaves_the_panel_by_name():
    """It must not be ranked at 0: that awards it the worst score as if measured."""
    blind = _card("B", otif=None)

    panel, dropped = rankable([_card("A"), blind], ("otif",))

    assert [c.supplier for c in panel] == ["A"]
    assert dropped == ("B",)


def test_only_the_criteria_actually_being_ranked_on_can_drop_a_supplier():
    """A missing PPM is irrelevant to a ranking that does not weigh quality."""
    no_quality = _card("B", defects_ppm=None)

    panel, dropped = rankable([_card("A"), no_quality], ("otif", "avg_lead_time"))

    assert [c.supplier for c in panel] == ["A", "B"]
    assert dropped == ()


def test_a_panel_where_nobody_scored_ranks_nobody():
    panel, dropped = rankable([_card("A", otif=None), _card("B", otif=None)], ("otif",))

    assert panel == []
    assert dropped == ("A", "B")


def test_an_unknown_criterion_is_refused_instead_of_dropping_the_whole_panel():
    """A typo'd field name would otherwise silently empty the panel."""
    with pytest.raises(AttributeError, match="on_time_pct"):
        rankable([_card("A")], ("on_time_pct",))
