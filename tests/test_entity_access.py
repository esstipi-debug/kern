"""Tenant isolation and the governed read path, over real supply-chain data.

Two questions this file answers, and they are the two an enterprise security
review asks first:

1. Can one tenant's data reach another? (No, structurally - the tenant is in
   every primary key and every ``WHERE``, not in each query author's memory.)
2. Can a reader see a supplier's bank account? (Only with the clearance for it,
   and the field is dropped rather than masked so its existence does not leak.)
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from nosilo_core.access import (
    ALLOW,
    CONFIDENTIAL,
    DENY,
    DROP,
    INTERNAL,
    PUBLIC,
    REASON_CAPABILITY_PURPOSE,
    REASON_UNGOVERNED,
    RESTRICTED,
    Capability,
    PolicyEngine,
    Principal,
    Rule,
)
from nosilo_core.entities import SourceRef, SqliteEntityStore
from src.entities import (
    SKU,
    SOURCE_ODOO,
    SUPPLIER,
    build_scm_registry,
    observe_attributes,
    scm_policy,
)

TENANT = "acme"

BUYER = Principal(id="u1", kind="human", tenant=TENANT, roles=frozenset({"buyer"}))
TREASURY = Principal(id="u2", kind="human", tenant=TENANT, roles=frozenset({"treasury"}))

ALLOW_READS = scm_policy("v1", rules=(Rule(effect=ALLOW, actions=frozenset({"entity.read"})),))


def _store(tmp_path, tenant: str = TENANT, policy: PolicyEngine | None = None) -> SqliteEntityStore:
    return SqliteEntityStore(
        build_scm_registry(), str(tmp_path / "entities.sqlite3"), tenant=tenant, policy=policy
    )


def _a_supplier(store: SqliteEntityStore) -> str:
    return observe_attributes(
        store, SUPPLIER.name,
        system=SOURCE_ODOO, source_entity="res.partner", source_id="7",
        attributes={
            "tax_id": "B12345678",
            "legal_name": "Tornillos SA",
            "bank_account": "ES11 1111 2222 3333",
            "credit_limit": Decimal("50000"),
            "payment_terms": "net60",
        },
    ).nosilo_id


# ---- tenant isolation -------------------------------------------------------


def test_two_tenants_over_one_file_never_see_each_other(tmp_path) -> None:
    acme = _store(tmp_path, tenant="acme")
    globex = _store(tmp_path, tenant="globex")

    mine = observe_attributes(
        acme, SKU.name, system=SOURCE_ODOO, source_entity="product.product", source_id="42",
        attributes={"sku_code": "A1", "title": "Acme bolt"},
    )
    theirs = observe_attributes(
        globex, SKU.name, system=SOURCE_ODOO, source_entity="product.product", source_id="42",
        attributes={"sku_code": "A1", "title": "Globex bolt"},
    )

    # Same source system, same source id, same natural key - and still two
    # entities, because resolution never looks outside the tenant.
    assert mine.nosilo_id != theirs.nosilo_id
    assert acme.current(theirs.nosilo_id) is None
    assert globex.current(mine.nosilo_id) is None
    assert acme.resolve_source(SourceRef(SOURCE_ODOO, "product.product", "42")) == mine.nosilo_id
    assert acme.entities_of_type(SKU.name) == (mine.nosilo_id,)
    acme.close()
    globex.close()


def test_history_and_xrefs_are_tenant_scoped_too(tmp_path) -> None:
    acme = _store(tmp_path, tenant="acme")
    globex = _store(tmp_path, tenant="globex")
    mine = _a_supplier(acme)

    assert globex.history(mine) == ()
    assert globex.xrefs(mine) == ()
    assert globex.as_of(mine) is None
    assert len(acme.history(mine)) == 1
    acme.close()
    globex.close()


def test_a_blank_tenant_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        SqliteEntityStore(build_scm_registry(), ":memory:", tenant="  ")


# ---- the governed read ------------------------------------------------------


def test_without_a_policy_the_store_behaves_exactly_as_before(tmp_path) -> None:
    store = _store(tmp_path)
    supplier = _a_supplier(store)
    result = store.read(supplier, principal=BUYER, purpose="planning")
    assert result.allowed
    assert result.decision.reason_code == REASON_UNGOVERNED
    assert result.values["bank_account"] == "ES11 1111 2222 3333"
    assert result.withheld == {}
    store.close()


def test_a_buyer_never_sees_a_supplier_bank_account(tmp_path) -> None:
    store = _store(tmp_path, policy=PolicyEngine(ALLOW_READS))
    supplier = _a_supplier(store)

    result = store.read(
        supplier, principal=BUYER, purpose="planning",
        capability=Capability(tenant=TENANT, max_sensitivity=INTERNAL),
    )
    assert result.allowed
    assert result.values["legal_name"] == "Tornillos SA"
    # Dropped, not masked: a masked account still confirms which one is on file.
    assert "bank_account" not in result.values
    assert "credit_limit" not in result.values
    assert result.withheld["bank_account"] == DROP
    store.close()


def test_treasury_with_the_clearance_sees_it(tmp_path) -> None:
    store = _store(tmp_path, policy=PolicyEngine(ALLOW_READS))
    supplier = _a_supplier(store)

    result = store.read(
        supplier, principal=TREASURY, purpose="planning",
        capability=Capability(tenant=TENANT, max_sensitivity=RESTRICTED),
    )
    assert result.values["bank_account"] == "ES11 1111 2222 3333"
    assert result.withheld == {}
    store.close()


def test_unit_cost_is_bucketed_rather_than_dropped(tmp_path) -> None:
    """Order of magnitude is often all a planning conversation needs."""
    store = _store(tmp_path, policy=PolicyEngine(ALLOW_READS))
    sku = observe_attributes(
        store, SKU.name, system=SOURCE_ODOO, source_entity="product.product", source_id="42",
        attributes={"sku_code": "A1", "title": "Bolt", "unit_cost": Decimal("42.50")},
    ).nosilo_id

    result = store.read(
        sku, principal=BUYER, purpose="planning",
        capability=Capability(tenant=TENANT, max_sensitivity=INTERNAL),
    )
    assert result.values["sku_code"] == "A1"
    assert result.values["unit_cost"] == "10-100"
    store.close()


def test_a_denial_leaks_nothing_not_even_existence(tmp_path) -> None:
    store = _store(tmp_path, policy=PolicyEngine(scm_policy("v1", rules=(Rule(effect=DENY),))))
    supplier = _a_supplier(store)

    result = store.read(supplier, principal=BUYER, purpose="planning")
    assert not result.allowed
    assert result.version is None
    assert result.values == {}
    # A missing entity is indistinguishable from a denied one, on purpose:
    # anything else turns every denial into an existence oracle.
    missing = store.read("no-such-entity", principal=BUYER, purpose="planning")
    assert (missing.version, missing.values) == (result.version, result.values)
    store.close()


def test_the_declared_purpose_is_enforced_on_the_read(tmp_path) -> None:
    store = _store(tmp_path, policy=PolicyEngine(ALLOW_READS))
    supplier = _a_supplier(store)

    result = store.read(
        supplier, principal=BUYER, purpose="marketing",
        capability=Capability(tenant=TENANT, purposes=frozenset({"planning"})),
    )
    assert not result.allowed
    assert result.decision.reason_code == REASON_CAPABILITY_PURPOSE
    store.close()


def test_the_ungoverned_read_path_still_returns_true_values(tmp_path) -> None:
    """``observe`` merges against ``current``, so it must never see a masked value.

    If the write path read through the filter, a poorly-cleared caller's next
    observation would compare its real value against ``[redacted]``, decide it
    changed, and persist the mask as if it were data.
    """
    store = _store(tmp_path, policy=PolicyEngine(ALLOW_READS))
    supplier = _a_supplier(store)
    assert store.current(supplier).attributes["bank_account"] == "ES11 1111 2222 3333"
    store.close()


def test_a_read_by_an_agent_is_bounded_by_its_delegated_capability(tmp_path) -> None:
    store = _store(tmp_path, policy=PolicyEngine(ALLOW_READS))
    supplier = _a_supplier(store)
    agent = Principal(id="a1", kind="agent", tenant=TENANT, on_behalf_of="u2")

    # Treasury may see bank details; the agent it delegates to is capped lower.
    delegated = Capability(tenant=TENANT, max_sensitivity=RESTRICTED).attenuate(
        max_sensitivity=PUBLIC,
        sensitivity_rank={PUBLIC: 0, INTERNAL: 1, CONFIDENTIAL: 2, RESTRICTED: 3},
        label="u2->a1",
    )
    result = store.read(supplier, principal=agent, purpose="planning", capability=delegated)
    assert result.allowed
    assert "bank_account" not in result.values
    assert "legal_name" not in result.values  # INTERNAL, above a PUBLIC clearance
    store.close()


def test_a_pre_tenant_database_fails_loudly_instead_of_silently(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE entity (nosilo_id TEXT PRIMARY KEY, entity_type TEXT, created_at TEXT)")

    from nosilo_core.entities import EntityStoreError

    with pytest.raises(EntityStoreError, match="pre-tenant"):
        SqliteEntityStore(build_scm_registry(), str(path))
