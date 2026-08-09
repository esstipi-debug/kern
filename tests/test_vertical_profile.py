"""Tests for src/vertical_profile.py — the fourth parameter-resolution level."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.client_profile import ClientProfile, load_profile, merge_params, save_profile
from src.vertical_profile import (
    ENGINE_PARAM_FIELDS,
    VerticalEngineParams,
    VerticalNotConfiguredError,
    VerticalProfile,
    list_verticals,
    load_vertical_profile,
)

REPO_VERTICALS = Path(__file__).resolve().parents[1] / "config" / "verticals"


def _yaml(*, slug: str = "prueba", engine: str = "", extra: str = "",
          outputs: tuple[str, ...] = ("lead_time_gap",)) -> str:
    """Smallest valid pack, with hooks to make exactly one thing wrong.

    Built line by line rather than by concatenating onto a template string:
    appending to a template whose last block is a YAML *list* silently produces
    a different document than intended, which is how the first draft of these
    tests managed to assert the wrong failure.
    """
    lines = [f"vertical: {slug}", 'display_name: "Rubro de prueba"',
             "engine_params:", "  holding_rate: 0.30"]
    if engine:
        lines.append(engine)
    lines.append("diagnostic_outputs:")
    lines += [f"  - {name}" for name in outputs]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


# ---- the packs that actually ship ---------------------------------------------
# These are not fixtures: they assert against the real config/verticals/*.yaml,
# so a bad edit to a shipped pack fails here rather than in a client's report.


@pytest.mark.parametrize("slug", ["repuestos-industriales", "distribucion-b2b"])
def test_shipped_pack_loads_and_validates(slug):
    profile = load_vertical_profile(slug, config_dir=REPO_VERTICALS)
    assert profile.vertical == slug
    assert profile.display_name
    assert profile.diagnostic_outputs
    assert profile.aliases


@pytest.mark.parametrize("slug", ["repuestos-industriales", "distribucion-b2b"])
def test_shipped_pack_emits_only_keys_the_engine_reads(slug):
    # The failure this guards is silent: an engine_params key that as_params()
    # does not emit would travel to a tool and be dropped without a word.
    params = load_vertical_profile(slug, config_dir=REPO_VERTICALS).as_params()
    assert set(params) <= set(ENGINE_PARAM_FIELDS)
    assert set(params) <= {f.name for f in ClientProfile.__dataclass_fields__.values()}


@pytest.mark.parametrize("slug", ["repuestos-industriales", "distribucion-b2b"])
def test_shipped_pack_never_seeds_a_lead_time(slug):
    # The whole product argument: a lead time is measured from receipt dates,
    # never inherited from an industry default.
    profile = load_vertical_profile(slug, config_dir=REPO_VERTICALS)
    assert "lead_time_days" not in profile.as_params()


def test_shipped_packs_disagree_on_where_pooling_goes():
    # The behavioural claim the packs exist to make. Distribution puts pooling
    # second because multi-warehouse holds there by definition; spares leave it
    # last, where the canonical order puts it.
    spares = load_vertical_profile("repuestos-industriales", config_dir=REPO_VERTICALS)
    distri = load_vertical_profile("distribucion-b2b", config_dir=REPO_VERTICALS)

    assert spares.diagnostic_outputs[0] == "lead_time_gap"
    assert distri.diagnostic_outputs[0] == "lead_time_gap"
    assert spares.diagnostic_outputs.index("pooling_savings") == 4
    assert distri.diagnostic_outputs.index("pooling_savings") == 1


def test_shipped_packs_disagree_on_stockout_tolerance():
    # A stockout stops a machine in one industry and costs a sale in the other.
    spares = load_vertical_profile("repuestos-industriales", config_dir=REPO_VERTICALS)
    distri = load_vertical_profile("distribucion-b2b", config_dir=REPO_VERTICALS)
    assert (spares.engine_params.objection_thresholds["comercial"]
            < distri.engine_params.objection_thresholds["comercial"])


def test_list_verticals_finds_the_shipped_packs():
    assert list_verticals(config_dir=REPO_VERTICALS) == [
        "alimentos", "distribucion-b2b", "repuestos-industriales"]


def test_list_verticals_on_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert list_verticals(config_dir=tmp_path / "nope") == []


# ---- the veto ------------------------------------------------------------------


ALIMENTOS_VETOED = ("inventory_optimization", "multi_echelon")


def _alimentos() -> VerticalProfile:
    return load_vertical_profile("alimentos", config_dir=REPO_VERTICALS)


@pytest.mark.parametrize("capability", ALIMENTOS_VETOED)
def test_perishable_pack_refuses_to_size_a_buffer(capability):
    # The defect this closes: before the veto, a food client fell through to the
    # generic defaults and got "raise the buffer on these SKUs" -- which on a
    # perishable catalog turns capital into waste, delivered with full confidence.
    veto = _alimentos().forbids(capability)
    assert veto is not None
    assert "vida util" in veto.reason
    assert veto.instead  # a refusal without a next step gets routed around


@pytest.mark.parametrize("capability", ["fefo", "newsvendor", "forecast"])
def test_perishable_pack_allows_the_capabilities_that_know_about_shelf_life(capability):
    assert _alimentos().forbids(capability) is None


def test_veto_keys_are_real_registry_keys():
    # A typo here is a veto that never fires, which is worse than no veto: the
    # pack would look like protection and be decoration. Skipped where the engine
    # dependency is absent rather than silently asserting nothing.
    pytest.importorskip("nosilo_core")
    from scm_agent.tools import build_default_registry

    registry = build_default_registry()
    for capability in ALIMENTOS_VETOED:
        assert registry.get(capability) is not None


@pytest.mark.parametrize("slug", ["repuestos-industriales", "distribucion-b2b"])
def test_the_two_serving_packs_forbid_nothing(slug):
    profile = load_vertical_profile(slug, config_dir=REPO_VERTICALS)
    assert profile.demand.forbidden_methods == ()
    assert profile.forbids("inventory_optimization") is None


def test_no_capability_means_no_veto():
    assert _alimentos().forbids(None) is None
    assert _alimentos().forbids("") is None


def test_bare_capability_name_without_a_reason_is_rejected(tmp_path):
    _write(tmp_path, "prueba",
           _yaml(extra="demand:\n  forbidden_methods:\n    - inventory_optimization"))
    with pytest.raises(ValueError, match="must be mappings"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_veto_without_a_reason_is_rejected(tmp_path):
    _write(tmp_path, "prueba",
           _yaml(extra="demand:\n  forbidden_methods:\n    - capability: inventory_optimization"))
    with pytest.raises(ValueError, match="needs a 'reason'"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_two_vetoes_for_one_capability_are_rejected(tmp_path):
    _write(tmp_path, "prueba", _yaml(extra=(
        "demand:\n  forbidden_methods:\n"
        "    - capability: multi_echelon\n      reason: una\n"
        "    - capability: multi_echelon\n      reason: otra")))
    with pytest.raises(ValueError, match="repeats capability"):
        load_vertical_profile("prueba", config_dir=tmp_path)


# ---- the hard gate -------------------------------------------------------------


def test_declared_vertical_without_a_profile_refuses_to_run(tmp_path):
    # Never a silent fall-back to the generic defaults: those defaults are
    # exactly what the pack exists to correct.
    with pytest.raises(VerticalNotConfiguredError) as exc:
        load_vertical_profile("alimentos", config_dir=tmp_path)
    assert "alimentos" in str(exc.value)


@pytest.mark.parametrize("bad", ["../markets/eu", "a/b", "", "  ", "con_guion_bajo", "-lead", "a--b"])
def test_slug_that_could_escape_the_config_dir_is_rejected(tmp_path, bad):
    with pytest.raises(ValueError, match="hyphenated slug"):
        load_vertical_profile(bad, config_dir=tmp_path)


def test_slug_case_and_padding_are_normalized_not_rejected(tmp_path):
    # Same tolerance load_market_rules gives a market code: the caller may hold
    # the slug however it likes, the file on disk is canonical.
    _write(tmp_path, "prueba", _yaml())
    assert load_vertical_profile("  Prueba  ", config_dir=tmp_path).vertical == "prueba"


def test_vertical_field_must_match_the_filename(tmp_path):
    _write(tmp_path, "prueba", _yaml(slug="otro"))
    with pytest.raises(ValueError, match="does not match filename"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_vertical_field_may_be_omitted_and_defaults_to_the_filename(tmp_path):
    body = _yaml().replace("vertical: prueba\n", "")
    _write(tmp_path, "prueba", body)
    assert load_vertical_profile("prueba", config_dir=tmp_path).vertical == "prueba"


# ---- validation ----------------------------------------------------------------


def test_lead_time_days_in_a_pack_is_rejected(tmp_path):
    _write(tmp_path, "prueba", _yaml(engine="  lead_time_days: 21"))
    with pytest.raises(ValueError, match="lead_time_days must be null"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_engine_params_key_the_engine_never_reads_is_rejected_loudly(tmp_path):
    _write(tmp_path, "prueba", _yaml(engine="  backorder_cost: 50"))
    with pytest.raises(ValueError, match="ignored in silence"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_objection_threshold_for_an_unknown_area_is_rejected(tmp_path):
    _write(tmp_path, "prueba",
           _yaml(engine="  objection_thresholds:\n    bodeguero: 0.1"))
    with pytest.raises(ValueError, match="not a known area"):
        load_vertical_profile("prueba", config_dir=tmp_path)


@pytest.mark.parametrize("value", [0, 1, 1.5, -0.2])
def test_service_level_outside_the_open_unit_interval_is_rejected(tmp_path, value):
    _write(tmp_path, "prueba", _yaml(engine=f"  service_level: {value}"))
    with pytest.raises(ValueError, match=r"service_level must be in \(0, 1\)"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_repeated_diagnostic_output_is_rejected(tmp_path):
    _write(tmp_path, "prueba", _yaml(outputs=("lead_time_gap", "lead_time_gap")))
    with pytest.raises(ValueError, match="must not repeat"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_unknown_demand_regime_is_rejected(tmp_path):
    _write(tmp_path, "prueba", _yaml(extra="demand:\n  expected_regime: caotico"))
    with pytest.raises(ValueError, match="expected_regime must be one of"):
        load_vertical_profile("prueba", config_dir=tmp_path)


def test_empty_alias_list_is_rejected_rather_than_ignored(tmp_path):
    _write(tmp_path, "prueba", _yaml(extra="aliases:\n  on_hand: []"))
    with pytest.raises(ValueError, match="drop the key instead"):
        load_vertical_profile("prueba", config_dir=tmp_path)


# ---- alias merging -------------------------------------------------------------


def _pack(**aliases) -> VerticalProfile:
    return VerticalProfile(
        vertical="prueba",
        display_name="Prueba",
        aliases={k: tuple(v) for k, v in aliases.items()},
    )


def test_pack_aliases_come_before_the_global_ones():
    # detect_columns takes the FIRST alias that matches, so appending would
    # leave the generic alias shadowing the industry one and the pack would
    # stop doing the only thing it exists to do.
    base = {"on_hand": ["stock", "existencia"]}
    merged = _pack(on_hand=["stock_local"]).merged_aliases(base)
    assert merged["on_hand"] == ["stock_local", "stock", "existencia"]


def test_canonical_name_the_pack_introduces_is_created_not_indexed():
    # detect_columns reads ALIASES[canon] with a bracket, so a merge that
    # indexed instead of creating would raise KeyError on exactly the fields a
    # pack adds (location_id, equipment_id, ...).
    merged = _pack(location_id=["local", "sucursal"]).merged_aliases({"on_hand": ["stock"]})
    assert merged["location_id"] == ["local", "sucursal"]
    assert merged["on_hand"] == ["stock"]


def test_merging_does_not_mutate_the_global_table():
    base = {"on_hand": ["stock"]}
    _pack(on_hand=["stock_local"]).merged_aliases(base)
    assert base == {"on_hand": ["stock"]}


def test_duplicate_alias_across_layers_is_kept_once_in_pack_position():
    merged = _pack(on_hand=["stock"]).merged_aliases({"on_hand": ["existencia", "stock"]})
    assert merged["on_hand"] == ["stock", "existencia"]


def test_shipped_pack_merges_into_the_real_alias_table():
    from jobs.intake import ALIASES

    pack = load_vertical_profile("distribucion-b2b", config_dir=REPO_VERTICALS)
    merged = pack.merged_aliases(ALIASES)
    assert set(ALIASES) <= set(merged)          # nothing lost
    assert "location_id" in merged              # new canonical name created
    assert merged["on_hand"][0] == "stock_local"  # pack wins the first slot


# ---- the merge order itself ----------------------------------------------------


def test_three_layers_resolve_lowest_to_highest():
    pack = VerticalProfile(
        vertical="prueba",
        display_name="Prueba",
        engine_params=VerticalEngineParams(
            service_level=0.90, holding_rate=0.30, order_cost=500.0),
    )
    profile = ClientProfile(client_id="x", display_name="X", holding_rate=0.18)

    merged = merge_params({"service_level": 0.99}, profile, pack)

    assert merged["service_level"] == 0.99   # explicit per-call wins
    assert merged["holding_rate"] == 0.18    # client profile beats the industry
    assert merged["order_cost"] == 500.0     # industry fills what nobody else set


def test_merge_params_without_a_vertical_behaves_exactly_as_before():
    profile = ClientProfile(client_id="x", display_name="X", holding_rate=0.18)
    assert merge_params({"a": 1}, profile) == {"a": 1, "holding_rate": 0.18}
    assert merge_params({"a": 1}, None) == {"a": 1}


# ---- the slug on the client profile --------------------------------------------


def test_vertical_survives_a_save_load_round_trip(tmp_path):
    save_profile(
        ClientProfile(client_id="acme", display_name="Acme", vertical="distribucion-b2b"),
        root=tmp_path,
    )
    assert load_profile("acme", root=tmp_path).vertical == "distribucion-b2b"


def test_a_profile_written_before_this_field_existed_still_loads(tmp_path):
    # Backward compatibility: no schema bump, the field is simply absent.
    path = tmp_path / "acme" / "profile.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"client_id": "acme", "display_name": "Acme"}', encoding="utf-8")
    assert load_profile("acme", root=tmp_path).vertical is None


def test_the_slug_is_not_an_engine_param():
    profile = ClientProfile(client_id="x", display_name="X", vertical="distribucion-b2b")
    assert "vertical" not in profile.as_params()
