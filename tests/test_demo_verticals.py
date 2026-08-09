"""Tests for webapp/demo_verticals.py — the funnel's single question."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs.intake import ALIASES, detect_columns
from src.classification import classify_portfolio
from src.vertical_profile import VerticalProfile, load_vertical_profile
from webapp import demo_verticals

REPO_VERTICALS = Path(__file__).resolve().parents[1] / "config" / "verticals"


def _pack(*outputs: str) -> VerticalProfile:
    return VerticalProfile(
        vertical="prueba", display_name="Prueba", diagnostic_outputs=outputs)


# ---- which output leads --------------------------------------------------------


def test_anonymous_visitor_gets_the_canonical_order():
    # The default case, and the one most visitors will hit: no declared industry
    # means lead time first, exactly as §5 has it.
    assert demo_verticals.demo_modes_for(None) == ("lead", "stock")


def test_pack_order_decides_which_tab_leads():
    assert demo_verticals.demo_modes_for(
        _pack("overstock_valued", "lead_time_gap")) == ("stock", "lead")


def test_two_outputs_served_by_the_same_mode_collapse_to_one_tab():
    # overstock_valued and dead_stock_12m are both the stock scan; the tab must
    # appear once, in the position of whichever the pack ranked higher.
    assert demo_verticals.demo_modes_for(
        _pack("overstock_valued", "lead_time_gap", "dead_stock_12m")) == ("stock", "lead")


def test_output_with_no_engine_in_the_funnel_is_skipped_not_faked():
    # pooling_savings is the one no competitor can compute -- and it has no demo
    # mode. A pack that leads with it gets the next implemented output, not an
    # empty tab.
    assert demo_verticals.demo_modes_for(
        _pack("pooling_savings", "below_reorder_point", "lead_time_gap")) == ("lead",)


def test_pack_whose_outputs_are_all_unimplemented_yields_nothing():
    # Deliberately empty rather than falling back: serving this visitor the
    # canonical order would hand them another industry's output under their own
    # industry's label.
    assert demo_verticals.demo_modes_for(_pack("pooling_savings")) == ()


# ---- the payload the page is built from ----------------------------------------


def test_payload_offers_both_shipped_verticals():
    payload = demo_verticals.verticals_payload(config_dir=REPO_VERTICALS)
    slugs = [o["slug"] for o in payload["options"]]
    assert slugs == ["distribucion-b2b", "repuestos-industriales"]
    assert payload["question"] == demo_verticals.QUESTION
    assert all(o["label"] and o["modes"] for o in payload["options"])


def test_payload_carries_each_packs_own_order():
    payload = demo_verticals.verticals_payload(config_dir=REPO_VERTICALS)
    by_slug = {o["slug"]: o["modes"] for o in payload["options"]}
    # Both lead with the lead-time gap; the shipped packs only diverge after it,
    # and that divergence (pooling second in distribution) has no demo mode yet.
    assert by_slug["repuestos-industriales"][0] == "lead"
    assert by_slug["distribucion-b2b"][0] == "lead"


def test_payload_on_an_empty_directory_is_empty_not_an_error(tmp_path):
    assert demo_verticals.verticals_payload(config_dir=tmp_path)["options"] == []


def test_the_perishable_pack_exists_but_is_never_offered_in_the_funnel():
    # alimentos ships as a GUARD, not as a capability: it exists so a food client
    # gets a refusal with a reason instead of a wrong buffer. Offering it in the
    # funnel would hand that visitor another industry's output under their own
    # industry's label, so its diagnostic_outputs are empty and it drops out here.
    assert "alimentos" in [p.stem for p in REPO_VERTICALS.glob("*.yaml")]
    pack = load_vertical_profile("alimentos", config_dir=REPO_VERTICALS)
    assert demo_verticals.demo_modes_for(pack) == ()

    offered = [o["slug"] for o in
               demo_verticals.verticals_payload(config_dir=REPO_VERTICALS)["options"]]
    assert "alimentos" not in offered


def test_the_perishable_packs_aliases_still_load_even_though_it_is_not_offered():
    # Not being sellable does not make the pack inert: its columns are what fefo
    # needs, and `fecha_vencimiento` is not in the generic table.
    aliases = demo_verticals.aliases_for("alimentos", config_dir=REPO_VERTICALS)
    assert aliases is not None
    assert "fecha_vencimiento" in aliases["expiry_date"]
    assert set(ALIASES) <= set(aliases)


def test_a_broken_pack_is_not_swallowed(tmp_path):
    # A malformed pack is a repo defect, not a runtime condition. Silently
    # dropping it would leave the funnel quietly serving generic numbers.
    (tmp_path / "roto.yaml").write_text(
        "vertical: roto\ndisplay_name: Roto\nengine_params:\n  service_level: 5\n",
        encoding="utf-8")
    with pytest.raises(ValueError):
        demo_verticals.verticals_payload(config_dir=tmp_path)


# ---- the alias table the answer selects ----------------------------------------


def test_no_declared_vertical_means_the_generic_table():
    assert demo_verticals.aliases_for("", config_dir=REPO_VERTICALS) is None
    assert demo_verticals.aliases_for(None, config_dir=REPO_VERTICALS) is None


@pytest.mark.parametrize("hostile", ["../markets/eu", "no-existe", "a/b"])
def test_unknown_or_tampered_slug_falls_back_instead_of_500ing(hostile):
    # This value arrives from a public form. The worst case of ignoring it is
    # mapping with the generic table -- which is what happened before packs
    # existed. A 500 would be an attack surface, not a correctness guarantee.
    assert demo_verticals.aliases_for(hostile, config_dir=REPO_VERTICALS) is None


def test_declared_vertical_maps_a_column_the_generic_table_cannot():
    # The practical point of the whole pack: "Casa Matriz" is a real supplier
    # header in a Chilean distributor's export and it is not, and cannot be, in
    # a generic table.
    df = pd.DataFrame({
        "Casa Matriz": ["ACME"], "Fecha OC": ["2026-01-02"], "Fecha Recepción": ["2026-01-20"]})

    generic = detect_columns(df, required=("supplier",), optional=())
    assert generic.unmatched_required == ["supplier"]

    aliases = demo_verticals.aliases_for("distribucion-b2b", config_dir=REPO_VERTICALS)
    with_pack = detect_columns(df, required=("supplier",), optional=(), aliases=aliases)
    assert with_pack.mapping["supplier"] == "Casa Matriz"


def test_pack_table_still_maps_everything_the_generic_one_did():
    aliases = demo_verticals.aliases_for("repuestos-industriales", config_dir=REPO_VERTICALS)
    assert set(ALIASES) <= set(aliases)
    df = pd.DataFrame({"Código": ["A1"], "Fecha": ["2026-01-02"], "Cantidad": [3]})
    detected = detect_columns(df, aliases=aliases)
    assert detected.unmatched_required == []


def test_a_canonical_the_table_does_not_know_is_named_not_a_bare_keyerror():
    df = pd.DataFrame({"x": [1]})
    with pytest.raises(KeyError, match="no tiene entrada"):
        detect_columns(df, required=("no_existe_este_campo",), optional=())


# ---- the other half of step 5: service level by class --------------------------


def _portfolio() -> list[dict]:
    return [
        {"product_id": "alto", "unit_cost": 100.0, "demand": [10, 12, 11, 9]},
        {"product_id": "bajo", "unit_cost": 0.5, "demand": [1, 0, 2, 0]},
    ]


def test_service_level_by_class_defaults_are_unchanged():
    levels = {c.abc: c.service_level for c in classify_portfolio(_portfolio())}
    assert levels["A"] == 0.98


def test_pack_raises_the_a_class_service_level():
    # In spares an A is "what stops the machine", not "what bills most".
    pack = load_vertical_profile("repuestos-industriales", config_dir=REPO_VERTICALS)
    classified = classify_portfolio(
        _portfolio(), service_level_by_class=pack.classification.service_level_by_class)
    assert {c.abc: c.service_level for c in classified}["A"] == 0.99


def test_partial_map_leaves_the_classes_it_does_not_mention():
    classified = classify_portfolio(_portfolio(), service_level_by_class={"A": 0.995})
    levels = {c.abc: c.service_level for c in classified}
    assert levels["A"] == 0.995
    for klass, value in levels.items():
        if klass != "A":
            assert value == {"B": 0.95, "C": 0.90}[klass]
