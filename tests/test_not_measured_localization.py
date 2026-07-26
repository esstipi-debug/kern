"""The "not measured" token is a label, so it lives in the i18n table.

`src/i18n.py` scopes each tool deck's narrative prose as English-only by design
(`Deliverable.lang` defaults to "en"), so a hardcoded Spanish "no medido" leaked a
foreign phrase into decks that are otherwise entirely English. It is not prose though -
it is a label, the same category i18n already owns (section headers, column names), so
it belongs in the table with both translations rather than pinned to one language.
"""

import pandas as pd
import pytest

from jobs import financial_kpis_job as fkj
from jobs import landed_cost_job as lcj
from jobs import sourcing_job as srcj
from src import i18n
from src.deliverable import not_measured

_FIN = pd.DataFrame({
    "product_id": ["A", "B"],
    "cogs": [1000.0, 2000.0],
    "avg_inventory_value": [250.0, 500.0],
})
_LEAD_ONLY = pd.DataFrame({
    "supplier": ["Alpha"] * 3 + ["Beta"] * 3,
    "lead_time_days": [7, 8, 7, 12, 11, 13],
})


def test_the_token_is_in_the_i18n_table_with_both_translations():
    assert i18n.label("not_measured", "en") == "not measured"
    assert i18n.label("not_measured", "es") == "no medido"


def test_the_default_is_english_because_tool_decks_are_english():
    assert not_measured() == "not measured"
    assert not_measured("en") == "not measured"
    assert not_measured("es") == "no medido"


def test_an_unknown_language_degrades_to_readable_text_rather_than_crashing():
    assert not_measured("pt")  # i18n.label falls back, never raises


@pytest.mark.parametrize("lang,expected", [("en", "not measured"), ("es", "no medido")])
def test_the_financial_deck_speaks_the_language_it_was_asked_for(lang, expected):
    report = fkj.run(fkj.prepare_records(_FIN))

    md = fkj.build_deck(report, client="Acme", lang=lang).to_markdown()

    assert expected in md


@pytest.mark.parametrize("lang,expected", [("en", "not measured"), ("es", "no medido")])
def test_the_sourcing_deck_speaks_the_language_it_was_asked_for(lang, expected):
    cards, _ = srcj.score_suppliers(_LEAD_ONLY, supplier_col="supplier",
                                    require_all_criteria=False, lead_col="lead_time_days")
    report = srcj.run(cards)

    md = srcj.build_deck(report, client="Acme", lang=lang).to_markdown()

    assert expected in md


def test_an_english_deck_no_longer_carries_a_spanish_phrase():
    report = fkj.run(fkj.prepare_records(_FIN))

    md = fkj.build_deck(report, client="Acme").to_markdown()

    assert "no medido" not in md
    assert "not measured" in md


def test_the_landed_cost_deck_localizes_too(tmp_path):
    csv = tmp_path / "s.csv"
    pd.DataFrame({"sku": ["A"], "unit_cost": [10.0], "qty": [0.0], "freight": [5000.0]}).to_csv(
        csv, index=False)
    report = lcj.run(lcj.prepare(str(csv), {}))

    assert "no medido" in lcj.build_deck(report, client="Acme", lang="es").to_markdown()
    assert "not measured" in lcj.build_deck(report, client="Acme").to_markdown()
