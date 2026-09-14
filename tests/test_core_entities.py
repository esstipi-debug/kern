"""The canonical entity store: identity, provenance, resolution, bitemporality.

Grouped by the property under test rather than by module, because the properties
are what the design promises: identity is stable, provenance is per field,
conflicts resolve deterministically, guarded fields never move on their own, and
history is genuinely append-only.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nosilo_core.entities import (
    ABSENT,
    LINK_EXACT_KEY,
    LINK_MINTED,
    EntityStoreError,
    EntityType,
    EntityTypeRegistry,
    FieldProvenance,
    FieldValue,
    SourceRef,
    SqliteEntityStore,
    new_entity_id,
    resolve,
    to_decimal,
)
from nosilo_core.entities.resolution import (
    REASON_GUARDED,
    REASON_NOT_OWNER,
    REASON_RECENCY,
    REASON_TIEBREAK,
    REASON_TRUST,
)

ERP = "erp"
PORTAL = "portal"

WIDGET = EntityType(
    name="widget",
    natural_keys=("code",),
    field_owners={"cost": ERP},
    guarded_fields=frozenset({"bank_account"}),
)


def _prov(system: str, recorded_at: str, trust: float = 1.0, ident: str = "1") -> FieldProvenance:
    return FieldProvenance(SourceRef(system, "obj", ident), recorded_at, trust)


def _held(value: object, system: str, recorded_at: str, trust: float = 1.0) -> FieldValue:
    return FieldValue(value, _prov(system, recorded_at, trust))


@pytest.fixture()
def registry() -> EntityTypeRegistry:
    reg = EntityTypeRegistry()
    reg.register(WIDGET)
    return reg


@pytest.fixture()
def store(registry: EntityTypeRegistry) -> SqliteEntityStore:
    made = SqliteEntityStore(registry, ":memory:")
    yield made
    made.close()


# ---- identity ---------------------------------------------------------------


def test_entity_ids_are_unique() -> None:
    assert len({new_entity_id() for _ in range(500)}) == 500


def test_to_decimal_never_routes_through_binary_float() -> None:
    # Decimal(0.1) is 0.1000000000000000055511151231257827; the str hop is the fix.
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal("2.675") == Decimal("2.675")
    assert to_decimal(Decimal("3.14")) == Decimal("3.14")


def test_source_refs_order_totally_so_ties_can_be_broken() -> None:
    a = SourceRef("a", "obj", "1")
    b = SourceRef("b", "obj", "1")
    assert sorted([b, a]) == [a, b]
    assert str(a) == "a:obj:1"


# ---- entity type declaration ------------------------------------------------


def test_entity_type_rejects_a_field_that_is_both_owned_and_guarded() -> None:
    with pytest.raises(ValueError, match="both owned and guarded"):
        EntityType(name="x", field_owners={"f": ERP}, guarded_fields=frozenset({"f"}))


def test_entity_type_rejects_a_blank_name() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        EntityType(name="  ")


def test_registry_refuses_a_conflicting_redefinition(registry: EntityTypeRegistry) -> None:
    with pytest.raises(ValueError, match="already registered"):
        registry.register(EntityType(name="widget", natural_keys=("other",)))


def test_registry_tolerates_an_identical_re_registration(registry: EntityTypeRegistry) -> None:
    registry.register(WIDGET)  # idempotent import order should not explode
    assert registry.names() == ("widget",)


def test_registry_names_the_known_types_when_asked_for_an_unknown_one(registry: EntityTypeRegistry) -> None:
    with pytest.raises(KeyError, match="widget"):
        registry.get("nope")


# ---- resolution: the deterministic rule -------------------------------------


def test_first_value_is_accepted_with_its_provenance() -> None:
    out = resolve(WIDGET, {}, {"title": "Bolt"}, _prov(PORTAL, "2026-01-01"))
    assert out.fields["title"].value == "Bolt"
    assert out.fields["title"].provenance.source.system == PORTAL
    assert out.accepted == {"title"}
    assert out.conflicts == ()


def test_restating_the_same_value_changes_nothing() -> None:
    current = {"title": _held("Bolt", PORTAL, "2026-01-01")}
    out = resolve(WIDGET, current, {"title": "Bolt"}, _prov(ERP, "2026-06-01"))
    assert not out.changed
    assert out.conflicts == ()
    # Provenance keeps pointing at where the value came FROM, not who last agreed.
    assert out.fields["title"].provenance.source.system == PORTAL


def test_absent_is_not_a_claim_and_never_clears_a_value() -> None:
    current = {"cost": _held(Decimal("5"), ERP, "2026-01-01")}
    out = resolve(WIDGET, current, {"cost": ABSENT}, _prov(PORTAL, "2026-06-01"))
    assert not out.changed
    assert out.fields["cost"].value == Decimal("5")


def test_none_is_a_real_claim_unlike_absent() -> None:
    current = {"title": _held("Bolt", PORTAL, "2026-01-01")}
    out = resolve(WIDGET, current, {"title": None}, _prov(PORTAL, "2026-06-01"))
    assert out.changed
    assert out.fields["title"].value is None


def test_the_declared_owner_always_wins_its_field() -> None:
    current = {"cost": _held(Decimal("9"), PORTAL, "2026-06-01", trust=99.0)}
    out = resolve(WIDGET, current, {"cost": Decimal("5")}, _prov(ERP, "2026-01-01", trust=0.1))
    # Owner wins despite older timestamp and far lower trust.
    assert out.fields["cost"].value == Decimal("5")
    assert out.accepted == {"cost"}


def test_a_non_owner_cannot_overwrite_the_owner() -> None:
    current = {"cost": _held(Decimal("5"), ERP, "2026-01-01")}
    out = resolve(WIDGET, current, {"cost": Decimal("9")}, _prov(PORTAL, "2026-06-01", trust=99.0))
    assert not out.changed
    assert [c.reason for c in out.conflicts] == [REASON_NOT_OWNER]
    assert out.fields["cost"].value == Decimal("5")


def test_two_non_owners_compete_on_the_deterministic_tail() -> None:
    # Owner (erp) has not spoken; portal vs feed falls through to trust.
    current = {"cost": _held(Decimal("9"), PORTAL, "2026-01-01", trust=0.5)}
    out = resolve(WIDGET, current, {"cost": Decimal("7")}, _prov("feed", "2026-01-01", trust=0.9))
    assert out.fields["cost"].value == Decimal("7")
    assert out.accepted == {"cost"}


def test_guarded_field_is_held_for_a_human_not_overwritten() -> None:
    current = {"bank_account": _held("ES11", PORTAL, "2026-01-01")}
    out = resolve(WIDGET, current, {"bank_account": "ES99"}, _prov(ERP, "2026-06-01", trust=1000.0))
    assert not out.changed
    assert out.fields["bank_account"].value == "ES11"
    assert [c.reason for c in out.conflicts] == [REASON_GUARDED]
    assert "ES99" in out.conflicts[0].describe()


def test_guarded_field_still_accepts_its_very_first_value() -> None:
    out = resolve(WIDGET, {}, {"bank_account": "ES11"}, _prov(PORTAL, "2026-01-01"))
    assert out.fields["bank_account"].value == "ES11"


def test_higher_trust_wins() -> None:
    current = {"title": _held("old", PORTAL, "2026-06-01", trust=0.2)}
    out = resolve(WIDGET, current, {"title": "new"}, _prov("feed", "2026-01-01", trust=0.8))
    assert out.fields["title"].value == "new"
    assert out.accepted == {"title"}


def test_lower_trust_loses_and_says_why() -> None:
    current = {"title": _held("keep", PORTAL, "2026-01-01", trust=0.9)}
    out = resolve(WIDGET, current, {"title": "drop"}, _prov("feed", "2026-06-01", trust=0.1))
    assert out.conflicts[0].reason == REASON_TRUST
    assert out.fields["title"].value == "keep"


def test_equal_trust_falls_through_to_recency() -> None:
    current = {"title": _held("old", PORTAL, "2026-01-01")}
    out = resolve(WIDGET, current, {"title": "new"}, _prov("feed", "2026-06-01"))
    assert out.fields["title"].value == "new"
    stale = resolve(WIDGET, current, {"title": "older"}, _prov("feed", "2025-01-01"))
    assert stale.conflicts[0].reason == REASON_RECENCY


def test_equal_trust_and_instant_fall_through_to_source_order() -> None:
    """The last tiebreak is what makes the ordering total - and replay stable."""
    current = {"title": _held("from-a", "aaa", "2026-01-01")}
    wins = resolve(WIDGET, current, {"title": "from-z"}, _prov("zzz", "2026-01-01"))
    assert wins.fields["title"].value == "from-z"

    current_z = {"title": _held("from-z", "zzz", "2026-01-01")}
    loses = resolve(WIDGET, current_z, {"title": "from-a"}, _prov("aaa", "2026-01-01"))
    assert loses.fields["title"].value == "from-z"
    assert loses.conflicts[0].reason == REASON_TIEBREAK


def test_a_source_revising_its_own_earlier_claim_always_wins() -> None:
    """Even inside a single clock tick.

    Two observations from one poller can share a timestamp string on a platform
    with coarse timer resolution (~15 ms on Windows). Comparing recency first
    would make a genuine update look like a duplicate and drop it - which is
    exactly the bug this ordering exists to prevent.
    """
    current = {"title": _held("first", PORTAL, "2026-01-01")}
    out = resolve(WIDGET, current, {"title": "second"}, _prov(PORTAL, "2026-01-01"))
    assert out.fields["title"].value == "second"
    assert out.conflicts == ()


def test_a_source_revising_itself_beats_even_a_higher_trust_predecessor() -> None:
    current = {"title": _held("first", PORTAL, "2026-06-01", trust=9.0)}
    out = resolve(WIDGET, current, {"title": "second"}, _prov(PORTAL, "2026-01-01", trust=0.1))
    assert out.fields["title"].value == "second"


def test_conflicts_come_back_in_a_reproducible_order() -> None:
    current = {
        "title": _held("t", ERP, "2026-06-01"),
        "bank_account": _held("ES11", ERP, "2026-06-01"),
        "cost": _held(Decimal("5"), ERP, "2026-06-01"),
    }
    incoming = {"title": "x", "bank_account": "ES99", "cost": Decimal("9")}
    first = resolve(WIDGET, current, incoming, _prov(PORTAL, "2026-01-01"))
    second = resolve(WIDGET, current, dict(reversed(list(incoming.items()))), _prov(PORTAL, "2026-01-01"))
    assert [c.field for c in first.conflicts] == [c.field for c in second.conflicts]
    assert [c.field for c in first.conflicts] == ["bank_account", "cost", "title"]


def test_one_guarded_conflict_does_not_block_the_ordinary_fields_beside_it() -> None:
    current = {"bank_account": _held("ES11", PORTAL, "2026-01-01")}
    out = resolve(
        WIDGET,
        current,
        {"bank_account": "ES99", "title": "Bolt"},
        _prov(PORTAL, "2026-06-01"),
    )
    assert out.fields["title"].value == "Bolt"
    assert out.fields["bank_account"].value == "ES11"
    assert out.accepted == {"title"}


# ---- store: identity and cross-references -----------------------------------


def test_a_new_source_record_mints_an_entity(store: SqliteEntityStore) -> None:
    result = store.observe("widget", SourceRef(ERP, "prod", "42"), {"code": "A1"})
    assert result.created
    assert result.link_method == LINK_MINTED
    assert result.version is not None
    assert store.current(result.nosilo_id).attributes["code"] == "A1"


def test_the_same_source_record_resolves_to_the_same_entity(store: SqliteEntityStore) -> None:
    ref = SourceRef(ERP, "prod", "42")
    first = store.observe("widget", ref, {"code": "A1"})
    second = store.observe("widget", ref, {"code": "A1", "title": "Bolt"})
    assert second.nosilo_id == first.nosilo_id
    assert not second.created


def test_two_systems_meet_on_an_exact_natural_key(store: SqliteEntityStore) -> None:
    """The point of the whole layer: one SKU, two source ids, one identity."""
    erp = store.observe("widget", SourceRef(ERP, "product.product", "42"), {"code": "A1"})
    shop = store.observe("widget", SourceRef("shopify", "variant", "99"), {"code": "A1", "title": "Bolt"})

    assert shop.nosilo_id == erp.nosilo_id
    assert shop.link_method == LINK_EXACT_KEY
    assert not shop.created
    assert {str(x.source) for x in store.xrefs(erp.nosilo_id)} == {
        "erp:product.product:42",
        "shopify:variant:99",
    }


def test_different_natural_keys_stay_different_entities(store: SqliteEntityStore) -> None:
    a = store.observe("widget", SourceRef(ERP, "prod", "1"), {"code": "A1"})
    b = store.observe("widget", SourceRef(ERP, "prod", "2"), {"code": "B2"})
    assert a.nosilo_id != b.nosilo_id
    assert len(store.entities_of_type("widget")) == 2


def test_relinking_a_source_record_to_another_entity_is_refused(store: SqliteEntityStore) -> None:
    ref = SourceRef(ERP, "prod", "42")
    store.observe("widget", ref, {"code": "A1"})
    other = store.observe("widget", SourceRef(ERP, "prod", "43"), {"code": "B2"})
    with pytest.raises(EntityStoreError, match="already linked"):
        store.link(other.nosilo_id, ref, confidence=1.0, method="manual")


def test_observing_an_unregistered_type_fails_loudly(store: SqliteEntityStore) -> None:
    with pytest.raises(KeyError, match="unknown entity type"):
        store.observe("gizmo", SourceRef(ERP, "x", "1"), {"code": "A1"})


# ---- store: versioning and bitemporality ------------------------------------


def test_an_observation_that_changes_nothing_writes_no_version(store: SqliteEntityStore) -> None:
    """A poller re-reading an unchanged catalogue must not grow the history."""
    ref = SourceRef(ERP, "prod", "42")
    store.observe("widget", ref, {"code": "A1", "title": "Bolt"}, recorded_at="2026-01-01T00:00:00+00:00")
    for hour in range(2, 8):
        repeat = store.observe(
            "widget", ref, {"code": "A1", "title": "Bolt"}, recorded_at=f"2026-01-0{hour}T00:00:00+00:00"
        )
        assert repeat.version is None
        assert not repeat.changed
    assert len(store.history(store.resolve_source(ref))) == 1


def test_versions_increment_and_history_derives_its_closing_bounds(store: SqliteEntityStore) -> None:
    ref = SourceRef(ERP, "prod", "42")
    store.observe("widget", ref, {"code": "A1", "title": "v1"}, recorded_at="2026-01-01T00:00:00+00:00")
    store.observe("widget", ref, {"code": "A1", "title": "v2"}, recorded_at="2026-06-01T00:00:00+00:00")
    entity = store.resolve_source(ref)

    history = store.history(entity)
    assert [v.version for v in history] == [1, 2]
    # Closing bounds are derived from the neighbour, never stored.
    assert history[0].superseded_at == "2026-06-01T00:00:00+00:00"
    assert history[0].valid_to == "2026-06-01T00:00:00+00:00"
    assert history[0].is_current is False
    assert history[1].superseded_at is None
    assert history[1].is_current is True


def test_known_at_reconstructs_what_was_known_then(store: SqliteEntityStore) -> None:
    """The question that matters when judging a past decision."""
    ref = SourceRef(ERP, "prod", "42")
    store.observe("widget", ref, {"code": "A1", "title": "v1"}, recorded_at="2026-01-01T00:00:00+00:00")
    store.observe("widget", ref, {"code": "A1", "title": "v2"}, recorded_at="2026-06-01T00:00:00+00:00")
    entity = store.resolve_source(ref)

    assert store.as_of(entity, known_at="2026-03-01T00:00:00+00:00").attributes["title"] == "v1"
    assert store.as_of(entity, known_at="2026-09-01T00:00:00+00:00").attributes["title"] == "v2"
    assert store.as_of(entity, known_at="2025-01-01T00:00:00+00:00") is None


def test_valid_at_answers_the_business_time_question_separately(store: SqliteEntityStore) -> None:
    ref = SourceRef(ERP, "prod", "42")
    # Learned in January, but only true in the world from March.
    store.observe(
        "widget", ref, {"code": "A1", "title": "v1"},
        valid_from="2026-01-01T00:00:00+00:00", recorded_at="2026-01-01T00:00:00+00:00",
    )
    store.observe(
        "widget", ref, {"code": "A1", "title": "v2"},
        valid_from="2026-03-01T00:00:00+00:00", recorded_at="2026-01-15T00:00:00+00:00",
    )
    entity = store.resolve_source(ref)

    assert store.as_of(entity, valid_at="2026-02-01T00:00:00+00:00").attributes["title"] == "v1"
    assert store.as_of(entity, valid_at="2026-04-01T00:00:00+00:00").attributes["title"] == "v2"
    # Known since mid-January, even though it only became true in March.
    assert store.as_of(
        entity, valid_at="2026-04-01T00:00:00+00:00", known_at="2026-01-20T00:00:00+00:00"
    ).attributes["title"] == "v2"


def test_backdating_a_version_is_refused_rather_than_silently_reordered(store: SqliteEntityStore) -> None:
    ref = SourceRef(ERP, "prod", "42")
    store.observe("widget", ref, {"code": "A1", "title": "v1"}, valid_from="2026-06-01T00:00:00+00:00")
    with pytest.raises(EntityStoreError, match="precedes"):
        store.observe("widget", ref, {"code": "A1", "title": "v0"}, valid_from="2026-01-01T00:00:00+00:00")


def test_history_is_append_only_no_update_or_delete_ever_touches_a_version(
    registry: EntityTypeRegistry,
) -> None:
    """The claim in the module docstring, enforced rather than asserted in prose."""
    made = SqliteEntityStore(registry, ":memory:")
    statements: list[str] = []
    made._conn.set_trace_callback(statements.append)

    ref = SourceRef(ERP, "prod", "42")
    made.observe("widget", ref, {"code": "A1", "title": "v1"})
    made.observe("widget", ref, {"code": "A1", "title": "v2"})
    made.observe("widget", ref, {"code": "A1", "title": "v3"})

    offenders = [
        sql
        for sql in statements
        if "entity_version" in sql.lower() and ("update " in sql.lower() or "delete " in sql.lower())
    ]
    assert offenders == [], f"entity_version was mutated: {offenders}"
    assert len(made.history(made.resolve_source(ref))) == 3
    made.close()


# ---- store: provenance and value fidelity -----------------------------------


def test_provenance_is_kept_per_field_not_per_record(store: SqliteEntityStore) -> None:
    store.observe("widget", SourceRef(ERP, "prod", "42"), {"code": "A1", "cost": Decimal("5.00")})
    result = store.observe("widget", SourceRef("shopify", "variant", "99"), {"code": "A1", "title": "Bolt"})

    current = store.current(result.nosilo_id)
    assert current.provenance_of("cost").source.system == ERP
    assert current.provenance_of("title").source.system == "shopify"
    assert {s.system for s in current.sources()} == {ERP, "shopify"}


def test_decimal_survives_the_round_trip_through_storage(store: SqliteEntityStore) -> None:
    """If money came back as float this whole layer would be worse than useless."""
    result = store.observe(
        "widget", SourceRef(ERP, "prod", "42"), {"code": "A1", "cost": to_decimal("19.99")}
    )
    stored = store.current(result.nosilo_id).attributes["cost"]
    assert stored == Decimal("19.99")
    assert isinstance(stored, Decimal)


def test_nested_decimals_survive_too(store: SqliteEntityStore) -> None:
    result = store.observe(
        "widget",
        SourceRef(ERP, "prod", "42"),
        {"code": "A1", "tiers": [{"qty": 10, "price": Decimal("1.05")}]},
    )
    assert store.current(result.nosilo_id).attributes["tiers"][0]["price"] == Decimal("1.05")


def test_a_conflict_surfaces_without_losing_the_rest_of_the_observation(store: SqliteEntityStore) -> None:
    store.observe("widget", SourceRef(ERP, "prod", "42"), {"code": "A1", "bank_account": "ES11"})
    result = store.observe(
        "widget", SourceRef("shopify", "variant", "99"),
        {"code": "A1", "bank_account": "ES99", "title": "Bolt"},
    )
    assert [c.reason for c in result.conflicts] == [REASON_GUARDED]
    current = store.current(result.nosilo_id)
    assert current.attributes["bank_account"] == "ES11"
    assert current.attributes["title"] == "Bolt"


def test_two_stores_fed_the_same_observations_end_up_byte_identical(
    registry: EntityTypeRegistry,
) -> None:
    """Determinism, end to end - the property that makes replay trustworthy."""
    observations = [
        (SourceRef(ERP, "prod", "42"), {"code": "A1", "cost": Decimal("5")}, "2026-01-01T00:00:00+00:00"),
        (SourceRef("shopify", "v", "9"), {"code": "A1", "title": "Bolt"}, "2026-02-01T00:00:00+00:00"),
        (SourceRef("feed", "f", "7"), {"code": "A1", "title": "Bolt XL"}, "2026-03-01T00:00:00+00:00"),
    ]

    def run() -> list[tuple[str, object]]:
        made = SqliteEntityStore(registry, ":memory:")
        for ref, attrs, when in observations:
            made.observe("widget", ref, attrs, recorded_at=when)
        entity = made.resolve_source(observations[0][0])
        snapshot = [
            (v.version, tuple(sorted((n, str(f.value), str(f.provenance.source)) for n, f in v.fields.items())))
            for v in made.history(entity)
        ]
        made.close()
        return snapshot

    assert run() == run()
