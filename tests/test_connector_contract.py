"""One contract, every system-of-record wrapper: the invariants that make
safe-staging writeback safe, asserted against each connector rather than once
against the reference implementation.

Why this exists. ``nosilo_core/writeback.py`` documents a store surface --
``read / applied_keys / claim / release / commit / rollback`` -- and six classes
implement it today (Odoo reorder rules, Odoo prices, client Excel workbooks,
MercadoLibre prices, Shopify prices, Shopify inventory). Each had its own test
file asserting its own behavior in its own words. Nothing asserted that they
agree. A connector that quietly claims a key twice, or rolls back a field that
never existed by writing ``None`` instead of removing it, would pass its own
suite and still corrupt a client's system of record -- and it would do so on the
exact path (apply -> rollback) an operator reaches for when something already
went wrong.

The parametrization IS the test. A seventh connector is one entry in
:data:`STORES` away from being held to the same six invariants, which is the
cheapest possible answer to "does this abstraction actually generalize".

Scope, stated rather than implied: this covers the reference ``InMemoryStore``
plus the four connectors addressable by a single field edit (Excel, Odoo prices,
MercadoLibre prices, Shopify prices). ``odoo.py`` and ``shopify_inventory.py``
also implement the surface but need a richer fixture (reorder rules, inventory
levels) than a price change, and keep their own suites; the final test in this
file fails if a NEW implementor appears and is silently left out of both lists.
``simulator.py`` exposes ``rollback`` alone, so it is not an implementor at all.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
from openpyxl import Workbook

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nosilo_core.writeback import ABSENT, InMemoryStore  # noqa: E402

from nosilo_core import writeback  # noqa: E402
from src.connectors.excel import ExcelWorkbookStore  # noqa: E402
from src.connectors.meli_prices import InMemoryMeli, MeliPriceStore  # noqa: E402
from src.connectors.odoo import InMemoryOdoo  # noqa: E402
from src.connectors.odoo_prices import OdooPriceStore  # noqa: E402
from src.connectors.shopify_prices import InMemoryShopify, ShopifyPriceStore  # noqa: E402


@dataclass(frozen=True)
class StoreCase:
    """A connector, plus the two things the contract needs to exercise it: an
    entity that exists in its backend, and a field on that entity it can write."""

    name: str
    target: str
    entity_id: str
    field: str
    new_value: object
    build: Callable[[Path], object]


def _in_memory(_tmp: Path) -> InMemoryStore:
    return InMemoryStore({"SKU-1": {"price": 20.0}})


def _excel(tmp: Path) -> ExcelWorkbookStore:
    # Addressed by sheet name + cell address, not SKU + column: the workbook IS
    # the system of record, and a client's planilla has no schema to assume.
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Plan"
    sheet["A1"], sheet["B1"] = "sku", "price"
    sheet["A2"], sheet["B2"] = "SKU-1", 20.0
    path = tmp / "planilla.xlsx"
    workbook.save(path)
    return ExcelWorkbookStore(path)


def _odoo_prices(_tmp: Path) -> OdooPriceStore:
    return OdooPriceStore(
        InMemoryOdoo(
            {"product.product": {1: {"default_code": "SKU-1", "name": "Widget",
                                     "list_price": 20.0, "standard_price": 12.0}}}
        )
    )


def _meli_prices(_tmp: Path) -> MeliPriceStore:
    return MeliPriceStore(InMemoryMeli({"SKU-1": {"item_id": "MLA1", "price": 20.0}}))


def _shopify_prices(_tmp: Path) -> ShopifyPriceStore:
    return ShopifyPriceStore(
        InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    )


STORES: tuple[StoreCase, ...] = (
    StoreCase("in_memory", "reference", "SKU-1", "price", 25.0, _in_memory),
    StoreCase("excel", "excel:planilla", "Plan", "B2", 25.0, _excel),
    StoreCase("odoo_prices", "odoo:prices", "SKU-1", "price", 25.0, _odoo_prices),
    StoreCase("meli_prices", "meli:seller", "SKU-1", "price", 25.0, _meli_prices),
    StoreCase("shopify_prices", "shopify:demo-shop", "SKU-1", "price", 25.0, _shopify_prices),
)


@pytest.fixture(params=STORES, ids=lambda case: case.name)
def case(request) -> StoreCase:
    return request.param


def _stage(store, case: StoreCase, value, key: str = "cs-1"):
    return writeback.stage(
        store,
        case.target,
        {case.entity_id: {case.field: value}},
        risk_tier=writeback.TIER_REVERSIBLE,
        idempotency_key=key,
        reason="connector contract test",
    )


def _apply(store, changeset, who: str = "operator"):
    return writeback.apply(store, changeset, approval=writeback.approve(changeset, who))


# -- the contract --------------------------------------------------------------


def test_read_of_an_unknown_entity_returns_empty_not_an_error(case: StoreCase, tmp_path: Path):
    """A missing SKU is an ordinary answer, not an exception. Callers branch on
    an empty dict; a connector that raises here turns a routine lookup into a
    500."""
    store = case.build(tmp_path)
    assert store.read("SKU-DOES-NOT-EXIST") == {}


def test_apply_writes_the_value_and_read_reflects_it(case: StoreCase, tmp_path: Path):
    store = case.build(tmp_path)
    changeset = _stage(store, case, case.new_value)
    # Staging alone must not have touched the backend.
    assert store.read(case.entity_id)[case.field] != case.new_value
    _apply(store, changeset)
    assert store.read(case.entity_id)[case.field] == case.new_value


def test_claiming_the_same_key_twice_is_refused(case: StoreCase, tmp_path: Path):
    """Idempotency is the whole point of the key: a retried apply -- the normal
    consequence of a timeout the caller never saw resolve -- must not write
    twice."""
    store = case.build(tmp_path)
    assert store.claim("cs-dup") is True
    assert store.claim("cs-dup") is False


def test_release_frees_a_claimed_key(case: StoreCase, tmp_path: Path):
    store = case.build(tmp_path)
    store.claim("cs-rel")
    store.release("cs-rel")
    assert store.claim("cs-rel") is True


def test_applying_the_same_changeset_twice_writes_once(case: StoreCase, tmp_path: Path):
    """A retry must be a no-op, NOT an error: the caller retrying is usually one
    whose first attempt timed out without ever learning it had succeeded. The
    contract is `idempotent_skip=True` -- raising would push that caller into
    error handling for a write that already landed correctly."""
    store = case.build(tmp_path)
    changeset = _stage(store, case, case.new_value, key="cs-once")
    first = _apply(store, changeset)
    assert first.applied is True and first.idempotent_skip is False

    second = _apply(store, changeset)
    assert second.applied is False and second.idempotent_skip is True
    assert second.audit_id == "cs-once"

    assert store.read(case.entity_id)[case.field] == case.new_value
    assert "cs-once" in store.applied_keys()


def test_rollback_restores_the_previous_value_exactly(case: StoreCase, tmp_path: Path):
    store = case.build(tmp_path)
    before = store.read(case.entity_id)[case.field]
    changeset = _stage(store, case, case.new_value, key="cs-rb")
    _apply(store, changeset)
    store.rollback("cs-rb")
    assert store.read(case.entity_id)[case.field] == before


def test_rollback_of_an_unknown_key_raises_rather_than_passing_silently(
    case: StoreCase, tmp_path: Path
):
    """A rollback that quietly does nothing is worse than one that fails: the
    operator walks away believing the change was undone."""
    store = case.build(tmp_path)
    with pytest.raises(KeyError):
        store.rollback("cs-never-applied")


# -- the reference store's own extras ------------------------------------------


def test_reference_store_removes_a_field_that_did_not_exist_before(tmp_path: Path):
    """The ABSENT sentinel path: rolling back a NEW field must delete it, not
    set it to None. Asserted only against InMemoryStore because the real
    connectors address fixed columns/attributes that always pre-exist -- there
    is no "new field" to create in a Shopify variant or a spreadsheet column
    the workbook does not have."""
    store = InMemoryStore({"SKU-1": {"price": 20.0}})
    changeset = writeback.stage(
        store,
        "reference",
        {"SKU-1": {"discount": 0.15}},
        risk_tier=writeback.TIER_REVERSIBLE,
        idempotency_key="cs-absent",
        reason="new field",
    )
    writeback.apply(store, changeset, approval=writeback.approve(changeset, "operator"))
    assert store.read("SKU-1")["discount"] == 0.15

    store.rollback("cs-absent")
    assert "discount" not in store.read("SKU-1")
    assert ABSENT is not None  # the sentinel is importable by connectors, by design


def test_every_full_surface_connector_is_covered_by_this_suite():
    """Drift guard: a new connector implementing the store surface must be added
    to STORES. Otherwise this file's promise -- 'every wrapper honors the same
    contract' -- silently becomes 'the ones someone remembered'."""
    import re

    connectors_dir = _REPO_ROOT / "src" / "connectors"
    required = {"def read", "def applied_keys", "def claim", "def release",
                "def commit", "def rollback"}
    implementors = set()
    for path in sorted(connectors_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        if all(re.search(rf"{m}\b", source) for m in required):
            implementors.add(path.stem)

    covered = {"excel", "odoo_prices", "meli_prices", "shopify_prices"}
    # odoo.py and shopify_inventory.py expose the surface through stores that
    # need a richer fixture (reorder rules / inventory levels) than a price
    # edit; they keep their own suites. Named here so the exclusion is a
    # decision on the record, not an oversight.
    known_uncovered = {"odoo", "shopify_inventory"}
    assert implementors == covered | known_uncovered, (
        f"connector(s) implementing the store surface but absent from this file: "
        f"{sorted(implementors - covered - known_uncovered)}"
    )
