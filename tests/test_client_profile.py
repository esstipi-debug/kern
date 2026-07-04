"""Tests for src/client_profile.py — per-client persisted parameter profile."""

from __future__ import annotations

import pytest

from src.client_profile import (
    ClientProfile,
    WarehouseCapacity,
    is_generic_client_label,
    load_profile,
    merge_params,
    save_profile,
    slugify_client_id,
    upsert_profile,
)

# ---- slugify_client_id -------------------------------------------------------

def test_slugify_basic():
    assert slugify_client_id("Acme Corp") == "acme-corp"


def test_slugify_transliterates_accents_instead_of_dropping_letters():
    # Spanish client names are the norm here — accents must not vanish (that
    # would collide distinct clients, e.g. "Café" and "Caf" both -> "caf").
    assert slugify_client_id("  Café - Cliente!!  ") == "cafe-cliente"
    assert slugify_client_id("Ñandú SA") == "nandu-sa"
    assert slugify_client_id("López y García") == "lopez-y-garcia"


def test_slugify_rejects_empty_result():
    with pytest.raises(ValueError):
        slugify_client_id("   !!!   ")


# ---- validation ---------------------------------------------------------------

def test_service_level_out_of_range_raises():
    with pytest.raises(ValueError):
        ClientProfile(client_id="x", display_name="X", service_level=1.5)


def test_service_level_zero_raises():
    with pytest.raises(ValueError):
        ClientProfile(client_id="x", display_name="X", service_level=0.0)


def test_holding_rate_must_be_positive():
    with pytest.raises(ValueError):
        ClientProfile(client_id="x", display_name="X", holding_rate=-0.1)


def test_order_cost_must_be_positive():
    with pytest.raises(ValueError):
        ClientProfile(client_id="x", display_name="X", order_cost=0.0)


def test_lead_time_days_must_be_positive():
    with pytest.raises(ValueError):
        ClientProfile(client_id="x", display_name="X", lead_time_days=-1.0)


def test_warehouse_capacity_value_must_be_positive():
    with pytest.raises(ValueError):
        WarehouseCapacity(value=0, unit="m3")


def test_warehouse_capacity_unit_required():
    with pytest.raises(ValueError):
        WarehouseCapacity(value=100.0, unit="  ")


def test_valid_profile_constructs_cleanly():
    profile = ClientProfile(
        client_id="acme", display_name="Acme", service_level=0.95,
        holding_rate=0.25, order_cost=75.0, lead_time_days=10.0,
        warehouse_capacity=WarehouseCapacity(value=850.0, unit="m3"),
    )
    assert profile.client_id == "acme"


# ---- save/load round trip ------------------------------------------------------

def test_save_and_load_round_trip(tmp_path):
    profile = ClientProfile(
        client_id="acme", display_name="Acme Corp", holding_rate=0.3,
        service_level=0.97, order_cost=50.0, lead_time_days=12.0,
        warehouse_capacity=WarehouseCapacity(value=850.0, unit="m3"),
        source="elicited", updated_at="2026-07-04",
    )
    save_profile(profile, root=tmp_path)
    loaded = load_profile("acme", root=tmp_path)
    assert loaded == profile


def test_save_and_load_round_trip_without_capacity(tmp_path):
    profile = ClientProfile(client_id="acme", display_name="Acme", holding_rate=0.25)
    save_profile(profile, root=tmp_path)
    loaded = load_profile("acme", root=tmp_path)
    assert loaded == profile
    assert loaded.warehouse_capacity is None


def test_load_profile_missing_file_returns_none(tmp_path):
    assert load_profile("ghost", root=tmp_path) is None


# ---- merge_params (resolution priority) ----------------------------------------

def test_merge_params_profile_fills_gaps_only():
    profile = ClientProfile(client_id="acme", display_name="Acme", holding_rate=0.3, service_level=0.97)
    merged = merge_params({"service_level": 0.90}, profile)
    assert merged == {"holding_rate": 0.3, "service_level": 0.90}


def test_merge_params_with_no_profile_returns_params_unchanged():
    assert merge_params({"a": 1}, None) == {"a": 1}


def test_merge_params_profile_never_overrides_explicit_value():
    profile = ClientProfile(client_id="acme", display_name="Acme", order_cost=99.0)
    merged = merge_params({"order_cost": 10.0}, profile)
    assert merged["order_cost"] == 10.0


# ---- upsert_profile -------------------------------------------------------------

def test_upsert_profile_creates_then_updates_preserving_other_fields(tmp_path):
    p1 = upsert_profile("acme", "Acme", root=tmp_path, holding_rate=0.25)
    assert p1.holding_rate == 0.25

    p2 = upsert_profile("acme", "Acme", root=tmp_path, service_level=0.95)
    assert p2.holding_rate == 0.25
    assert p2.service_level == 0.95


def test_upsert_profile_persists_to_disk(tmp_path):
    upsert_profile("acme", "Acme", root=tmp_path, holding_rate=0.4, source="elicited")
    reloaded = load_profile("acme", root=tmp_path)
    assert reloaded.holding_rate == 0.4
    assert reloaded.source == "elicited"


# ---- generic client label guard (no cross-tenant profile bleed) ---------------

def test_is_generic_client_label_matches_default_and_variants():
    assert is_generic_client_label("Client")
    assert is_generic_client_label("client")
    assert is_generic_client_label("  CLIENT  ")


def test_is_generic_client_label_rejects_real_names():
    assert not is_generic_client_label("Acme Corp")
    assert not is_generic_client_label("")


def test_save_profile_refuses_generic_client_label(tmp_path):
    profile = ClientProfile(client_id="client", display_name="Client")
    with pytest.raises(ValueError):
        save_profile(profile, root=tmp_path)


def test_upsert_profile_refuses_generic_client_label(tmp_path):
    with pytest.raises(ValueError):
        upsert_profile("client", "Client", root=tmp_path, holding_rate=0.3)


def test_load_profile_ignores_generic_client_label_even_if_file_exists(tmp_path):
    # Simulate a stray file at the generic slug (e.g. hand-created before this guard
    # existed) — load_profile must still refuse it, not just refuse writing it.
    stray = tmp_path / "client"
    stray.mkdir(parents=True)
    (stray / "profile.json").write_text(
        '{"client_id": "client", "display_name": "Client", "holding_rate": 0.9}',
        encoding="utf-8",
    )
    assert load_profile("client", root=tmp_path) is None
    assert load_profile("Client", root=tmp_path) is None


# ---- corrupt profile.json --------------------------------------------------------

def test_load_profile_raises_clear_error_on_corrupt_json(tmp_path):
    bad = tmp_path / "acme"
    bad.mkdir(parents=True)
    (bad / "profile.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt client profile"):
        load_profile("acme", root=tmp_path)


def test_load_profile_raises_clear_error_on_missing_required_key(tmp_path):
    bad = tmp_path / "acme"
    bad.mkdir(parents=True)
    (bad / "profile.json").write_text('{"display_name": "Acme"}', encoding="utf-8")
    with pytest.raises(ValueError, match="corrupt client profile"):
        load_profile("acme", root=tmp_path)


# ---- atomic save (no partial file left behind on success) ------------------------

def test_save_profile_leaves_no_leftover_temp_files(tmp_path):
    profile = ClientProfile(client_id="acme", display_name="Acme", holding_rate=0.3)
    save_profile(profile, root=tmp_path)
    leftovers = list((tmp_path / "acme").glob("*.tmp"))
    assert leftovers == []


# ---- canonical client_id enforcement (save/load key symmetry + no traversal) ------

def test_save_profile_rejects_non_canonical_client_id(tmp_path):
    # A display name saved verbatim would land at a key the orchestrator's
    # slugified lookup never reads — silent "profile exists but never applies".
    with pytest.raises(ValueError, match="canonical slug"):
        save_profile(ClientProfile(client_id="Acme Corp", display_name="Acme Corp"), root=tmp_path)


def test_save_profile_rejects_path_traversal_client_id(tmp_path):
    with pytest.raises(ValueError):
        save_profile(ClientProfile(client_id="../escaped", display_name="Evil"), root=tmp_path / "clients")
    assert not (tmp_path / "escaped").exists()


def test_upsert_profile_canonicalizes_display_name(tmp_path):
    upsert_profile("Acme Corp", "Acme Corp", root=tmp_path, holding_rate=0.3)
    assert (tmp_path / "acme-corp" / "profile.json").exists()
    assert load_profile("acme-corp", root=tmp_path).holding_rate == 0.3


# ---- upsert preserves metadata on partial updates ---------------------------------

def test_upsert_profile_preserves_source_and_updated_at_when_omitted(tmp_path):
    upsert_profile("acme", "Acme", root=tmp_path, holding_rate=0.3,
                   source="elicited", updated_at="2026-07-01")
    p2 = upsert_profile("acme", "Acme", root=tmp_path, service_level=0.95)
    assert p2.source == "elicited"
    assert p2.updated_at == "2026-07-01"
    assert p2.holding_rate == 0.3


# ---- non-finite numbers are rejected ------------------------------------------------

@pytest.mark.parametrize("field", ["holding_rate", "order_cost", "lead_time_days"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_numeric_fields_are_rejected(field, bad):
    with pytest.raises(ValueError):
        ClientProfile(client_id="x", display_name="X", **{field: bad})


def test_non_finite_capacity_rejected():
    with pytest.raises(ValueError):
        WarehouseCapacity(value=float("nan"), unit="m3")


# ---- source / schema_version validation ---------------------------------------------

def test_invalid_source_rejected():
    with pytest.raises(ValueError, match="source"):
        ClientProfile(client_id="x", display_name="X", source="guessed")


def test_load_refuses_newer_schema_version(tmp_path):
    d = tmp_path / "acme"
    d.mkdir(parents=True)
    (d / "profile.json").write_text(
        '{"client_id": "acme", "display_name": "Acme", "schema_version": 99}', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="corrupt client profile"):
        load_profile("acme", root=tmp_path)


def test_load_wraps_out_of_range_values_with_path_context(tmp_path):
    d = tmp_path / "acme"
    d.mkdir(parents=True)
    (d / "profile.json").write_text(
        '{"client_id": "acme", "display_name": "Acme", "service_level": 1.5}', encoding="utf-8",
    )
    with pytest.raises(ValueError, match="corrupt client profile"):
        load_profile("acme", root=tmp_path)


# ---- as_params excludes non-engine fields -------------------------------------------

def test_as_params_excludes_warehouse_capacity():
    # No engine consumes it yet, and a dataclass object inside params would
    # poison JSON serialization of the merged dict downstream.
    profile = ClientProfile(
        client_id="acme", display_name="Acme", holding_rate=0.3,
        warehouse_capacity=WarehouseCapacity(value=100.0, unit="m3"),
    )
    assert "warehouse_capacity" not in profile.as_params()
    assert profile.as_params() == {"holding_rate": 0.3}


def test_is_generic_client_label_covers_mcp_default():
    assert is_generic_client_label("MCP client")
