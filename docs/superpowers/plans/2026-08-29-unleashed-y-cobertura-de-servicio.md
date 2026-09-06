# Unleashed adapter + service-coverage guard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `src/connectors/unleashed.py` — an `InventorySource` over the Unleashed
API — behind `tests/test_connector_service_contract.py`, a guard that forces every
connector to declare, field by field, which part of outbound customer service it can
measure, and then *executes* that declaration against the adapter to prove it true.

**Architecture:** A new `ServiceCoverage` declaration type lives in
`src/connectors/__init__.py`, beside the `OrderLine` whose three optional service fields
it describes. Each adapter module exports a module-level `SERVICE_COVERAGE` constant. The
guard discovers every module in `src/connectors/` that defines `def orders(`, requires a
declaration for each (or an explicitly-named exclusion), requires all three service fields
to be spelled out with no omissions, and — the part that makes it a guard rather than a
docstring — runs each adapter over a fixture and asserts the observed `None`/non-`None`
pattern matches what was declared. The Unleashed adapter reads `/SalesOrders` for demand
and dates, and joins `/SalesShipments` for the shipped quantity per line.

**Tech Stack:** Python 3.11, pytest, dataclasses, `hmac`/`hashlib`/`base64` from the
stdlib (Unleashed signs the query string with HMAC-SHA256). No new dependency: the HTTP
transport is injected httpx-style, exactly as `shopify_connect.live_http` does it, and the
module ships an `InMemoryUnleashed` stand-in so every test runs offline.

**Spec:** `docs/handoffs/HANDOFF-adaptadores-canal-2026-08-29.md` (sections 1, 2, 4-D1,
4-D3, 7), plus the two vendor-doc corrections recorded under "Verified facts" below.

## Global Constraints

- Branch: `feat/unleashed-cobertura-servicio`, cut from `origin/main` @ `5a1108c` (D3 = A).
- **The worktree is shared with another session.** Re-run `git status` immediately before
  every `git add`. `.mcp.json` (modified) and `data/bateria/` (untracked) are NOT part of
  this thread — never `git add -A`, never `git commit` without an explicit pathspec.
- Python: run the worktree's own interpreter, `.venv/Scripts/python.exe`, never the parent's.
- `ruff`: `line-length = 120`, `select = ["E", "F", "I"]`, `target-version = "py311"`.
- `None` is load-bearing and is never a stand-in for a number. A service field the backend
  does not report travels as `None` to the deliverable. Never `0.0` (an invented stockout),
  never `quantity` (an invented perfect fill). This is the rule `src/connectors/__init__.py`
  documents on `OrderLine` and `src/order_fulfillment.py` enforces in its denominators.
- Do NOT touch pricing: `webapp/pricing_quote.py`, `webapp/billing.py`,
  `scm_agent/package_specs.py`. Decision D2 (AU/NZ entry price) is the user's and is open.
- Do NOT wire BWM (D4 = B): `src/mcdm.py` and `jobs/sourcing_job.py` stay untouched.
- Commit message format: `<type>: <description>`, types `feat|fix|refactor|docs|test|chore`.

## Verified facts (2026-08-29, from vendor docs — these correct the handoff)

The handoff's D1 rationale says Unleashed cannot report shipped quantity per line. That is
true of `/SalesOrders` and false of the API as a whole. Both corrections are load-bearing
for the coverage declaration, which is the artifact that must not lie.

| Fact | Source |
|---|---|
| `SalesOrder`: `OrderNumber`, `OrderDate`, `RequiredDate`, `CompletedDate`, `OrderStatus` ∈ {Parked, Placed, Completed, Backordered, Deleted} | https://apidocs.unleashedsoftware.com/SalesOrders |
| `SalesOrderLine`: `LineNumber`, `Product.ProductCode`, `OrderQuantity`, `UnitPrice`, `DueDate`. **No** shipped/delivered quantity. | same |
| `SalesShipmentLine`: **`ShipmentQty`** (shipped qty per line), `SalesOrderLineNumber`, `Product.ProductCode`; parent `SalesShipment` carries `OrderNumber`, `OrderGuid`, `DispatchDate`, `ShipmentStatus`. | https://apidocs.unleashedsoftware.com/SalesShipments |
| Unleashed has **no arrival date anywhere**. `CompletedDate` and `DispatchDate` are both dispatch events. Its OTIF is therefore "shipped by the required date", never "arrived by". | both pages above; endpoint index has no Deliveries/Fulfilment resource |
| Auth: headers `api-auth-id`, `api-auth-signature` = base64(HMAC-SHA256(query-string-without-`?`, private key)), plus `client-type`, `Accept`, `Content-Type`. | https://apidocs.unleashedsoftware.com/AuthenticationHelp |
| Paging: `GET /{Resource}/{page}?pageSize=N`; response envelope `{"Pagination": {...}, "Items": [...]}`. | same docs |

**Consequence for the plan, stated so it is a decision and not a drift:** D1 (Unleashed
first) stands unchanged. What changes is only the declared *reason*. `delivered_qty` stays
`None` on the `/SalesOrders`-only path — exactly the behaviour the handoff asked for, and
the honest answer for a client whose shipments we have not read — and becomes a real number
when shipments are supplied. `delivered_date` is a **dispatch** date and the declaration
says so in those words, because calling a departure an arrival turns every carrier delay
into punctuality (the rule `shopify_service.py` decision #2 already sets for this codebase).

## File structure

| File | Responsibility |
|---|---|
| `src/connectors/__init__.py` (modify) | Add `FieldCoverage`, `ServiceCoverage` and the three status constants next to `OrderLine`. Nothing else moves. |
| `src/connectors/unleashed.py` (create, ~330 lines) | The adapter: signing, paging, DTO mapping, the shipment join, `InMemoryUnleashed`, `live_http`, `SERVICE_COVERAGE`. |
| `src/connectors/simulator.py` (modify, +1 constant) | Declare its coverage. |
| `src/connectors/http_client.py` (modify, +1 constant) | Declare its coverage. |
| `src/connectors/mcp_source.py` (modify, +1 constant) | Declare its coverage. |
| `src/connectors/odoo.py` (modify, +1 constant) | Declare its coverage. |
| `tests/test_connector_service_contract.py` (create) | The guard: declaration completeness, declaration-vs-observation, and the drift check that fails when a new adapter appears undeclared. |
| `tests/test_connector_unleashed.py` (create) | The adapter's own suite: signing, paging, mapping, the join, and every `None` that must survive. |

---

### Task 1: The coverage declaration type

**Files:**
- Modify: `src/connectors/__init__.py` (insert after the `OrderLine` dataclass, ~line 78)
- Test: `tests/test_connector_service_contract.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `COVERAGE_MEASURED`, `COVERAGE_DERIVED`, `COVERAGE_UNAVAILABLE` (str constants);
  `FieldCoverage(status: str, source: str, means: str)` frozen dataclass with a
  `.reports` bool property; `ServiceCoverage(backend: str, delivered_qty: FieldCoverage,
  promised_date: FieldCoverage, delivered_date: FieldCoverage)` frozen dataclass with
  `.fields() -> dict[str, FieldCoverage]`. Every later task imports these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_connector_service_contract.py` with the module docstring and this first
block. (Later tasks append to this file; do not rewrite the header.)

```python
"""One declaration, every adapter: which part of customer service each backend can measure.

Why this exists. ``src/order_fulfillment.py`` measures fill rate, outbound OTIF (SCOR
RL.1.1) and lost sales from three OPTIONAL fields on ``OrderLine`` --
``delivered_qty``, ``promised_date``, ``delivered_date``. Which of the three a backend
can actually fill is the whole competitive question this codebase is built around: the
handoff of 2026-08-29 verified that StockTrim's sales-order schema carries none of them
and that Netstock measures only the inbound mirror. That finding is worth exactly as
much as its shelf life, and its shelf life is one adapter written by someone who did not
read it.

So the adapters declare it, in code, and this file executes the declaration. A
``SERVICE_COVERAGE`` constant that says ``delivered_qty`` is unavailable, sitting beside
an adapter that returns a number for it, fails here. So does the reverse -- a connector
claiming to measure something its fixture never produces. A declaration nobody runs is a
comment, and a comment is what the previous version of this argument was.

The third test is the drift guard: a NEW module in ``src/connectors/`` that defines
``orders()`` and does not declare its coverage fails, rather than quietly inheriting the
benefit of the doubt.

Scope, stated rather than implied: the fixtures here exercise the four offline-runnable
adapters (simulator, HTTP client, MCP source, Unleashed) plus Odoo through its
``InMemoryOdoo`` stand-in. ``ledger.CanonicalLedger`` is a MERGE of sources, not a
backend, and its ``orders()`` takes no ``since`` -- it is named in ``_NOT_A_BACKEND``
rather than left to be forgotten.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.connectors import (  # noqa: E402
    COVERAGE_DERIVED,
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
    ServiceCoverage,
)


def test_a_field_that_reports_is_distinguishable_from_one_that_cannot():
    """``reports`` is the single bit the guard branches on. Measured and derived both
    produce a number; unavailable never does, and no fourth status may sneak past by
    being truthy."""
    measured = FieldCoverage(COVERAGE_MEASURED, "SalesOrder.RequiredDate", "promise")
    derived = FieldCoverage(COVERAGE_DERIVED, "qty - unfulfilled", "deduced")
    absent = FieldCoverage(COVERAGE_UNAVAILABLE, "", "the API has no such field")
    assert measured.reports is True
    assert derived.reports is True
    assert absent.reports is False


def test_an_unavailable_field_must_still_say_why():
    """The whole point is the sentence. ``UNAVAILABLE`` with an empty ``means`` is a
    shrug, and a shrug is what a competitor's spec sheet also says."""
    with pytest.raises(ValueError, match="means"):
        FieldCoverage(COVERAGE_UNAVAILABLE, "", "")


def test_a_reporting_field_must_name_its_source_field():
    """A number with no named backend field cannot be reconciled by a client, and cannot
    be re-verified when the vendor changes its schema."""
    with pytest.raises(ValueError, match="source"):
        FieldCoverage(COVERAGE_MEASURED, "", "the promised date")


def test_an_unknown_status_is_refused_rather_than_treated_as_absent():
    with pytest.raises(ValueError, match="status"):
        FieldCoverage("probably", "X.y", "who knows")


def test_coverage_exposes_exactly_the_three_service_fields_by_name():
    """Keyed by the ``OrderLine`` attribute name, so the guard can compare a declaration
    against an observed line without a translation table that could itself drift."""
    coverage = ServiceCoverage(
        backend="demo",
        delivered_qty=FieldCoverage(COVERAGE_MEASURED, "A.b", "units that left"),
        promised_date=FieldCoverage(COVERAGE_MEASURED, "A.c", "the promise"),
        delivered_date=FieldCoverage(COVERAGE_UNAVAILABLE, "", "no arrival is recorded"),
    )
    assert set(coverage.fields()) == {"delivered_qty", "promised_date", "delivered_date"}
    assert coverage.fields()["delivered_date"].reports is False
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Expected: collection error — `ImportError: cannot import name 'COVERAGE_DERIVED' from 'src.connectors'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/connectors/__init__.py`, insert immediately after the `OrderLine` dataclass:

```python
#: The backend reports this field directly, as its own column.
COVERAGE_MEASURED = "measured"
#: The backend does not have this field, but it is computable from ones it does have
#: (Shopify's ordered-minus-unfulfilled, Unleashed's shipment join). Still a number, and
#: still a measurement - the deduction is named in ``FieldCoverage.source``.
COVERAGE_DERIVED = "derived"
#: The backend has nothing that can produce this field. It is always ``None``, every line,
#: forever - not a gap in this adapter but a gap in the system of record.
COVERAGE_UNAVAILABLE = "unavailable"

_COVERAGE_STATUSES = (COVERAGE_MEASURED, COVERAGE_DERIVED, COVERAGE_UNAVAILABLE)


@dataclass(frozen=True)
class FieldCoverage:
    """What one adapter can say about one service field, and what the number means.

    ``means`` is not decoration. Odoo's delivery date is a stock move, Unleashed's is a
    dispatch, Shopify's is a carrier-confirmed arrival: three different facts arriving in
    the same attribute. A client reconciling an OTIF against their own numbers needs the
    sentence, and an engineer adding the fourth backend needs it more.
    """

    #: One of the ``COVERAGE_*`` constants.
    status: str
    #: The backend field(s) this comes from, spelled as the vendor spells them.
    source: str
    #: What the value actually is, in a sentence. Required even when nothing is
    #: reported - especially then, because that sentence is the finding.
    means: str

    def __post_init__(self) -> None:
        if self.status not in _COVERAGE_STATUSES:
            raise ValueError(
                f"status {self.status!r} is not one of {_COVERAGE_STATUSES}: a field is "
                "either reported, deduced, or absent from the backend."
            )
        if not self.means.strip():
            raise ValueError(
                "means must say what the number is, or - for an unavailable field - what "
                "the backend has instead. That sentence is the point of the declaration."
            )
        if self.reports and not self.source.strip():
            raise ValueError(
                "source must name the backend field a reported value comes from: a number "
                "a client cannot trace back to their own system is a number they cannot check."
            )

    @property
    def reports(self) -> bool:
        """Whether a line from this adapter may carry a value here at all."""
        return self.status in (COVERAGE_MEASURED, COVERAGE_DERIVED)


@dataclass(frozen=True)
class ServiceCoverage:
    """An adapter's field-by-field answer to 'what customer service can you measure?'

    Every adapter exports one as a module-level ``SERVICE_COVERAGE``, and
    ``tests/test_connector_service_contract.py`` runs it against the adapter rather than
    reading it. The three fields are the three optional ones on ``OrderLine``; there is no
    default and none may be omitted, because the omission is exactly the claim being made.
    """

    backend: str
    delivered_qty: FieldCoverage
    promised_date: FieldCoverage
    delivered_date: FieldCoverage

    def fields(self) -> dict[str, FieldCoverage]:
        """Keyed by ``OrderLine`` attribute name, so a caller can zip the two together."""
        return {
            "delivered_qty": self.delivered_qty,
            "promised_date": self.promised_date,
            "delivered_date": self.delivered_date,
        }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Expected: 5 passed.

- [ ] **Step 5: Run the existing suites that import this module, to prove nothing broke**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connectors.py tests/test_order_fulfillment.py tests/test_connector_service_lines.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit (explicit pathspec — the worktree is shared)**

```bash
git status --short && git add src/connectors/__init__.py tests/test_connector_service_contract.py && git commit -m "feat(conectores): un adaptador declara campo por campo que servicio puede medir"
```

---

### Task 2: The guard executes the declaration against every existing adapter

**Files:**
- Modify: `tests/test_connector_service_contract.py` (append)
- Modify: `src/connectors/simulator.py`, `src/connectors/http_client.py`,
  `src/connectors/mcp_source.py`, `src/connectors/odoo.py` (one constant each)

**Interfaces:**
- Consumes: Task 1's `ServiceCoverage` / `FieldCoverage` / `COVERAGE_*`.
- Produces: the module-level name `SERVICE_COVERAGE: ServiceCoverage` in each adapter
  module, and the test helper `_observed(orders) -> dict[str, bool]` used by Task 3.

- [ ] **Step 1: Write the failing test** — append to `tests/test_connector_service_contract.py`

```python
# -- the adapters under test ----------------------------------------------------

from dataclasses import dataclass  # noqa: E402
from typing import Callable  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.connectors import Order  # noqa: E402
from src.connectors.emulator import create_app  # noqa: E402
from src.connectors.http_client import StoreApiClient  # noqa: E402
from src.connectors.odoo import InMemoryOdoo, OdooConnector  # noqa: E402
from src.connectors.simulator import demo_store  # noqa: E402


@dataclass(frozen=True)
class AdapterCase:
    """An adapter, its declaration, and a fixture rich enough to exercise every field
    the declaration claims. A thin fixture is how a true declaration fails: the guard
    cannot tell 'this backend cannot report it' from 'this fixture forgot to'."""

    name: str
    module: str
    build: Callable[[], object]


def _simulator():
    return demo_store()


def _http_client():
    return StoreApiClient(TestClient(create_app(demo_store())))


def _odoo():
    """A confirmed order promised for the 10th, served short, shipped on the 12th.
    Same shape as ``tests/test_connector_odoo_service.py``, kept local so a change
    there cannot silently weaken this guard."""
    return OdooConnector(
        InMemoryOdoo(
            {
                "product.product": {
                    1: {"default_code": "SKU-1", "name": "Widget",
                        "list_price": 20.0, "standard_price": 12.0},
                },
                "sale.order": {
                    200: {"name": "S0001", "date_order": "2026-01-05 10:00:00",
                          "commitment_date": "2026-01-10 00:00:00", "state": "sale",
                          "order_line": [300]},
                },
                "sale.order.line": {
                    300: {"product_id": [1, "Widget"], "product_uom_qty": 10.0,
                          "price_unit": 20.0, "qty_delivered": 8.0},
                },
                "stock.picking.type": {50: {"code": "outgoing"}},
                "stock.move": {
                    600: {"sale_line_id": [300, "S0001"], "state": "done",
                          "date": "2026-01-12 11:00:00", "picking_type_id": [50, "Delivery"]},
                },
            }
        )
    )


ADAPTERS: tuple[AdapterCase, ...] = (
    AdapterCase("simulator", "src.connectors.simulator", _simulator),
    AdapterCase("http_client", "src.connectors.http_client", _http_client),
    AdapterCase("odoo", "src.connectors.odoo", _odoo),
)


@pytest.fixture(params=ADAPTERS, ids=lambda case: case.name)
def adapter(request) -> AdapterCase:
    return request.param


def _coverage(module_name: str) -> ServiceCoverage:
    import importlib

    module = importlib.import_module(module_name)
    declared = getattr(module, "SERVICE_COVERAGE", None)
    assert isinstance(declared, ServiceCoverage), (
        f"{module_name} implements orders() but declares no SERVICE_COVERAGE. Every "
        "adapter says field by field what service it can measure; silence reads as a "
        "capability nobody checked."
    )
    return declared


def _observed(orders: list[Order]) -> dict[str, bool]:
    """Per service field: did ANY line come back carrying a value?

    ``any``, not ``all``: a real backend leaves the promise blank on the orders nobody
    promised a date for, and that is correct behaviour rather than a broken claim. The
    declaration is about the adapter's ceiling, so one line reaching it is proof.
    """
    lines = [line for order in orders for line in order.lines]
    return {
        field: any(getattr(line, field) is not None for line in lines)
        for field in ("delivered_qty", "promised_date", "delivered_date")
    }


# -- the contract ---------------------------------------------------------------


def test_every_adapter_declares_all_three_service_fields(adapter: AdapterCase):
    coverage = _coverage(adapter.module)
    assert set(coverage.fields()) == {"delivered_qty", "promised_date", "delivered_date"}
    assert coverage.backend.strip(), "a declaration must name the backend it describes"


def test_a_field_declared_unavailable_never_comes_back_with_a_value(adapter: AdapterCase):
    """The direction that protects the client. An adapter that quietly starts filling a
    field it declared absent is filling it with something, and the something is a guess."""
    coverage = _coverage(adapter.module)
    observed = _observed(adapter.build().orders())
    for name, field in coverage.fields().items():
        if not field.reports:
            assert observed[name] is False, (
                f"{adapter.name} declares {name} {field.status} ({field.means}) but a line "
                f"came back carrying one. Either the backend gained the field - update the "
                f"declaration - or the adapter is inventing it."
            )


def test_a_field_declared_reportable_actually_arrives(adapter: AdapterCase):
    """The direction that protects the differentiator. A declaration is a sales claim;
    this is the only thing standing between it and a slide."""
    coverage = _coverage(adapter.module)
    observed = _observed(adapter.build().orders())
    for name, field in coverage.fields().items():
        if field.reports:
            assert observed[name] is True, (
                f"{adapter.name} declares {name} {field.status} from {field.source!r}, but "
                f"no line in its own fixture carried one."
            )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Expected: 9 failures — `simulator implements orders() but declares no SERVICE_COVERAGE`, and the same for `http_client` and `odoo`.

- [ ] **Step 3: Write the minimal implementation — four declarations**

Append to `src/connectors/simulator.py` (end of file):

```python
#: What a simulated store can measure. Everything, by construction - it is the only
#: "backend" here whose schema we chose. That is exactly why it is declared: the demo
#: store is the fixture every other test reaches for, and a fixture that reports
#: perfectly is how "we measure service" gets believed without a real backend behind it.
SERVICE_COVERAGE = ServiceCoverage(
    backend="simulator (offline stand-in, not a system of record)",
    delivered_qty=FieldCoverage(
        COVERAGE_MEASURED, "OrderLine.delivered_qty",
        "units the simulated store shipped; invented by demo_store(), never observed",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED, "OrderLine.promised_date",
        "order date + 3 days, the lead time demo_store() pretends to promise",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED, "OrderLine.delivered_date",
        "the promise, plus two days on part of the lines so the demo is not perfect",
    ),
)
```

with the import at the top of `simulator.py` extended to:

```python
from src.connectors import (
    COVERAGE_MEASURED,
    FieldCoverage,
    InventoryLevel,
    Order,
    OrderLine,
    Product,
    ServiceCoverage,
)
```

Append to `src/connectors/http_client.py` (end of file), extending its import the same way:

```python
#: Whatever the store on the other end reports. This adapter is a transport, not a
#: backend: it maps JSON to DTOs and its ceiling is the ceiling of the API it points at.
#: Declared MEASURED because the emulator (``connectors.emulator``) serves all three, and
#: a live store that omits one produces ``None`` through ``_number``/``_text`` - which is
#: the correct answer for that store, not a violation of this claim.
SERVICE_COVERAGE = ServiceCoverage(
    backend="commerce HTTP store API (offline: connectors.emulator)",
    delivered_qty=FieldCoverage(
        COVERAGE_MEASURED, "GET /admin/orders -> line_items[].delivered_qty",
        "units the store reported shipping; null round-trips to None, never to 0.0",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED, "GET /admin/orders -> line_items[].promised_date",
        "the delivery date the store says it promised the customer",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED, "GET /admin/orders -> line_items[].delivered_date",
        "the date the store says the line reached the customer",
    ),
)
```

Append to `src/connectors/mcp_source.py` (end of file), extending its import the same way:

```python
#: The remote MCP server's ceiling, which we do not control. All three are optional in
#: the tool schema and arrive as None when the server omits them (see ``_num_opt`` /
#: ``_text_opt``); declared MEASURED because a server that publishes them is the case
#: this adapter exists for, and one that does not is reported as not measured.
SERVICE_COVERAGE = ServiceCoverage(
    backend="remote MCP server (tool schema, not a fixed vendor)",
    delivered_qty=FieldCoverage(
        COVERAGE_MEASURED, "orders tool -> lines[].delivered_qty",
        "units the server says were served; absent -> None, never filled in from quantity",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED, "orders tool -> lines[].promised_date",
        "the delivery date the server says was promised",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED, "orders tool -> lines[].delivered_date",
        "the date the server says the line was served",
    ),
)
```

Append to `src/connectors/odoo.py` (end of file), extending its import from `src.connectors`
the same way:

```python
#: Lo que un Odoo estandar puede medir del servicio al cliente. Las tres columnas
#: existen, y las tres son opcionales en una base viva -- por eso ``OdooConnector`` sondea
#: cada campo antes de pedirlo (``_has_field``) y una ausencia viaja como ``None``.
SERVICE_COVERAGE = ServiceCoverage(
    backend="Odoo (XML-RPC, modelos estandar de Ventas e Inventario)",
    delivered_qty=FieldCoverage(
        COVERAGE_MEASURED, "sale.order.line.qty_delivered",
        "unidades entregadas de la linea; el campo llega con el puente Ventas-Inventario "
        "y sin el puente el servicio sale no medido",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED, "sale.order.commitment_date",
        "la fecha comprometida de la ORDEN, replicada a sus lineas; en blanco es lo "
        "habitual y saca la linea de OTIF en vez de medirla contra date_order",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_DERIVED, "stock.move (state=done, picking_type=outgoing) -> max(date)",
        "la fecha del ULTIMO despacho que sirvio la linea. Es DESPACHO, no llegada: "
        "Odoo no registra la recepcion del cliente, asi que este OTIF mide salir a "
        "tiempo del deposito y no llegar a tiempo",
    ),
)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Expected: 14 passed.

- [ ] **Step 5: Run every suite that touches the modified adapters**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connectors.py tests/test_connector_odoo.py tests/test_connector_odoo_service.py tests/test_connector_emulator.py tests/test_connector_ledger.py tests/test_mcp_inventory_source.py tests/test_connector_replenish.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git status --short && git add tests/test_connector_service_contract.py src/connectors/simulator.py src/connectors/http_client.py src/connectors/mcp_source.py src/connectors/odoo.py && git commit -m "test(conectores): el guard corre la declaracion de cobertura contra cada adaptador"
```

---

### Task 3: The drift guard — a new adapter cannot arrive undeclared

**Files:**
- Modify: `tests/test_connector_service_contract.py` (append)

**Interfaces:**
- Consumes: Task 2's `ADAPTERS`.
- Produces: `_NOT_A_BACKEND` (the named exclusion set), read by nothing else.

This is the test that gives the guard its teeth over time. Without it, the file's promise —
"every adapter declares its coverage" — decays into "the ones someone remembered", which is
exactly the decay `tests/test_connector_contract.py` already guards against on the write side.

- [ ] **Step 1: Write the failing test** — append to `tests/test_connector_service_contract.py`

```python
#: Modules in ``src/connectors/`` that define ``orders()`` and are deliberately NOT held
#: to a declaration. Each exclusion is a decision on the record, not an oversight.
_NOT_A_BACKEND = {
    # A MERGE of other sources, not a system of record: its coverage is the union of its
    # channels' and cannot be declared statically. Its orders() also takes no ``since``,
    # so it does not satisfy InventorySource in the first place.
    "ledger",
    # A FastAPI app that SERVES a SimulatedStore over HTTP; its orders() is a route
    # handler, not an adapter method. What it serves is declared by simulator.py, and
    # what reads it is declared by http_client.py.
    "emulator",
}


def test_every_connector_that_reads_orders_declares_its_service_coverage():
    """Drift guard: a new adapter is one entry in ADAPTERS away from being held to the
    same three assertions. Landing one without a declaration fails here, at the moment it
    lands, rather than the first time a client asks what their OTIF was computed from."""
    import re

    connectors_dir = _REPO_ROOT / "src" / "connectors"
    readers = {
        path.stem
        for path in sorted(connectors_dir.glob("*.py"))
        if path.stem != "__init__"
        and re.search(r"def orders\(", path.read_text(encoding="utf-8"))
    }
    covered = {case.module.rsplit(".", 1)[-1] for case in ADAPTERS}
    assert readers == covered | _NOT_A_BACKEND, (
        "connector(s) reading orders but absent from both ADAPTERS and _NOT_A_BACKEND: "
        f"{sorted(readers - covered - _NOT_A_BACKEND)}. Add a SERVICE_COVERAGE and an "
        "AdapterCase, or record here why this one is not a backend."
    )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py::test_every_connector_that_reads_orders_declares_its_service_coverage -q
```

Expected: FAIL — `connector(s) reading orders but absent from both ADAPTERS and _NOT_A_BACKEND: ['mcp_source']`. (`mcp_source` was declared in Task 2 but has no `AdapterCase`; the fixture needs a fake tool caller.)

- [ ] **Step 3: Write the minimal implementation** — add the MCP fixture and its case

Insert before `ADAPTERS` in the test file:

```python
from src.connectors.mcp_source import McpInventorySource  # noqa: E402

#: One order the way an MCP server publishes it: all three service keys present, so the
#: fixture reaches the ceiling the declaration claims.
_MCP_ORDERS = [
    {
        "order_id": "SO-1",
        "created_at": "2026-01-05",
        "lines": [
            {"sku": "SKU-1", "quantity": 10.0, "price": 20.0, "delivered_qty": 8.0,
             "promised_date": "2026-01-10", "delivered_date": "2026-01-12"},
        ],
    }
]


def _mcp():
    return McpInventorySource(lambda name, args: _MCP_ORDERS)
```

and extend `ADAPTERS`:

```python
ADAPTERS: tuple[AdapterCase, ...] = (
    AdapterCase("simulator", "src.connectors.simulator", _simulator),
    AdapterCase("http_client", "src.connectors.http_client", _http_client),
    AdapterCase("mcp_source", "src.connectors.mcp_source", _mcp),
    AdapterCase("odoo", "src.connectors.odoo", _odoo),
)
```

- [ ] **Step 4: Run the whole guard**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git status --short && git add tests/test_connector_service_contract.py && git commit -m "test(conectores): un adaptador nuevo sin declaracion de cobertura rompe el guard"
```

---

### Task 4: Unleashed — signing and paging

**Files:**
- Create: `src/connectors/unleashed.py`
- Create: `tests/test_connector_unleashed.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `UnleashedCredentials(api_id: str, api_key: str, client_type: str = "kern/kern")`;
  `signature(query: str, api_key: str) -> str`; `auth_headers(creds, query) -> dict[str, str]`;
  `UnleashedError`; `fetch_pages(http, resource, params, creds, *, max_pages=100) -> list[dict]`.
  Task 5 calls `fetch_pages`; Task 7 calls `live_http`.

- [ ] **Step 1: Write the failing test** — create `tests/test_connector_unleashed.py`

```python
"""El adaptador de Unleashed: firma, paginado, mapeo y el empalme de despachos.

Unleashed es la plataforma del canal elegido para AU/NZ. Su API firma cada request con
HMAC-SHA256 sobre la QUERY STRING, no sobre la URL ni el cuerpo, y pagina por segmento de
ruta (``/SalesOrders/2``) en vez de por cursor. Las dos cosas son faciles de escribir mal
de una forma que anda igual en el primer pedido y falla en el segundo, asi que se fijan
aca antes que nada.

El resto del archivo es sobre los ``None``. Ese es el producto: la handoff del 2026-08-29
verifico que StockTrim no puede medir el servicio saliente y que Netstock mide el espejo
entrante, y lo unico que sostiene esa diferencia es que este adaptador no invente los
numeros que Unleashed no da.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from src.connectors.unleashed import (
    UnleashedCredentials,
    UnleashedError,
    auth_headers,
    fetch_pages,
    signature,
)


class FakeHttp:
    """Un transporte httpx-style que devuelve respuestas preparadas y guarda lo pedido."""

    class _Response:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status
            self.text = str(payload)

        def json(self) -> dict:
            return self._payload

    def __init__(self, pages: list[dict], status: int = 200) -> None:
        self._pages = list(pages)
        self._status = status
        self.calls: list[tuple[str, dict, dict]] = []

    def get(self, path, *, params=None, headers=None):
        self.calls.append((path, dict(params or {}), dict(headers or {})))
        payload = self._pages.pop(0) if self._pages else {"Pagination": {}, "Items": []}
        return FakeHttp._Response(payload, self._status)


_CREDS = UnleashedCredentials(api_id="the-id", api_key="the-key")


def _page(items: list[dict], page: int, pages: int) -> dict:
    return {
        "Pagination": {"NumberOfItems": len(items), "PageSize": 200,
                       "PageNumber": page, "NumberOfPages": pages},
        "Items": items,
    }


def test_the_signature_is_hmac_sha256_of_the_query_string_in_base64():
    """La referencia de Unleashed (AuthenticationHelp) firma los parametros SIN el '?'.
    Se recalcula aqui a mano en vez de pegar un valor esperado: un valor pegado pasa
    igual si el algoritmo cambia y alguien actualiza la constante."""
    query = "pageSize=200&startDate=2026-01-01"
    esperado = base64.b64encode(
        hmac.new(b"the-key", query.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    assert signature(query, "the-key") == esperado


def test_a_request_with_no_parameters_signs_the_empty_string():
    """El caso que rompe en produccion y no en la demo: firmar '?' o el path devuelve
    401 solo en los endpoints sin filtros."""
    esperado = base64.b64encode(
        hmac.new(b"the-key", b"", hashlib.sha256).digest()
    ).decode("ascii")
    assert signature("", "the-key") == esperado


def test_the_headers_carry_the_id_the_signature_and_the_client_type():
    headers = auth_headers(_CREDS, "pageSize=200")
    assert headers["api-auth-id"] == "the-id"
    assert headers["api-auth-signature"] == signature("pageSize=200", "the-key")
    assert headers["client-type"] == "kern/kern"
    assert headers["Accept"] == "application/json"


def test_the_api_key_never_appears_in_a_header_or_a_url():
    """La clave privada firma y no viaja. Un adaptador que la mande en una cabecera la
    deja en cada log de proxy del cliente."""
    headers = auth_headers(_CREDS, "pageSize=200")
    assert "the-key" not in "".join(f"{k}{v}" for k, v in headers.items())


def test_paging_walks_every_page_and_stops_at_the_last():
    http = FakeHttp([
        _page([{"OrderNumber": "SO-1"}], page=1, pages=2),
        _page([{"OrderNumber": "SO-2"}], page=2, pages=2),
    ])
    items = fetch_pages(http, "SalesOrders", {"pageSize": 200}, _CREDS)
    assert [i["OrderNumber"] for i in items] == ["SO-1", "SO-2"]
    assert [call[0] for call in http.calls] == ["/SalesOrders/1", "/SalesOrders/2"]


def test_every_page_is_signed_with_its_own_query_string():
    """La firma cubre los parametros, y el numero de pagina va en la RUTA -- pero si
    alguien lo mueve a los parametros, reusar la firma de la primera pagina da 401 en la
    segunda y nunca en la primera."""
    http = FakeHttp([
        _page([{"OrderNumber": "SO-1"}], page=1, pages=2),
        _page([{"OrderNumber": "SO-2"}], page=2, pages=2),
    ])
    fetch_pages(http, "SalesOrders", {"pageSize": 200, "startDate": "2026-01-01"}, _CREDS)
    for _path, params, headers in http.calls:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        assert headers["api-auth-signature"] == signature(query, "the-key")


def test_a_single_page_response_makes_exactly_one_call():
    http = FakeHttp([_page([{"OrderNumber": "SO-1"}], page=1, pages=1)])
    assert len(fetch_pages(http, "SalesOrders", {}, _CREDS)) == 1
    assert len(http.calls) == 1


def test_a_non_200_raises_instead_of_returning_an_empty_read():
    """Un 401 que devuelve [] es una tienda sin ventas: fill rate no medido, cero
    hallazgos, y un cliente al que se le informa que no tiene quiebres."""
    http = FakeHttp([_page([], page=1, pages=1)], status=401)
    with pytest.raises(UnleashedError, match="401"):
        fetch_pages(http, "SalesOrders", {}, _CREDS)


def test_paging_stops_at_the_cap_rather_than_looping_forever():
    """Un servidor que devuelve NumberOfPages creciente no puede colgar un job."""
    http = FakeHttp([_page([{"OrderNumber": f"SO-{i}"}], page=i, pages=999)
                     for i in range(1, 10)])
    items = fetch_pages(http, "SalesOrders", {}, _CREDS, max_pages=3)
    assert len(items) == 3
    assert len(http.calls) == 3
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_unleashed.py -q
```

Expected: collection error — `ModuleNotFoundError: No module named 'src.connectors.unleashed'`.

- [ ] **Step 3: Write the minimal implementation** — create `src/connectors/unleashed.py`

```python
"""Unleashed — ``InventorySource`` sobre el ERP del canal AU/NZ elegido.

Unleashed es una de las dos plataformas del beachhead (la otra es Cin7 Core). Este
adaptador lee su catalogo, su stock y sus ordenes de venta, y -- lo unico que importa --
llena las tres columnas de servicio que ``src/order_fulfillment.py`` necesita para medir
fill rate, OTIF saliente (SCOR RL.1.1) y venta perdida.

QUE PUEDE Y QUE NO PUEDE MEDIR UNLEASHED (verificado contra la doc del vendor, 2026-08-29;
la declaracion ejecutable esta al final del archivo, en ``SERVICE_COVERAGE``):

===================== =========================================================
clave de OrderLine    campo de Unleashed
===================== =========================================================
ordered_qty           ``SalesOrderLine.OrderQuantity``
unit_price            ``SalesOrderLine.UnitPrice``
promised_date         ``SalesOrder.RequiredDate`` -- la fecha que el cliente
                      pidio, a nivel ORDEN. Vacia es lo normal, y una linea sin
                      promesa sale de OTIF en vez de medirse contra OrderDate.
delivered_qty         ``SalesShipmentLine.ShipmentQty``, sumada por linea de
                      orden y empalmada por (OrderNumber, SalesOrderLineNumber).
                      NO sale de /SalesOrders: ahi no existe. Sin despachos
                      leidos queda en None -- que es la respuesta correcta para
                      un cliente cuyos despachos no leimos, no un 0%.
delivered_date        ``SalesShipment.DispatchDate`` del ULTIMO despacho de la
                      linea, o ``SalesOrder.CompletedDate`` cuando no se leyeron
                      despachos.
===================== =========================================================

Tres decisiones, y ninguna es de comodidad:

1. **Unleashed no tiene fecha de llegada. Ninguna.** Ni ``CompletedDate`` ni
   ``DispatchDate`` son la recepcion del cliente: las dos son el momento en que la
   mercaderia SALIO. El indice de endpoints no tiene ningun recurso de entrega. Asi que
   el OTIF que sale de aca mide *despachar antes de la fecha pedida*, no *llegar*, y la
   declaracion lo dice con esas palabras. Llamar llegada a una salida convierte cada
   demora de transporte en puntualidad -- la misma regla que ``shopify_service.py`` fija
   en su decision 2, donde si hay ``deliveredAt`` y por eso se exige.

2. **La cantidad despachada se EMPALMA, no se deduce.** ``/SalesShipments`` trae
   ``ShipmentQty`` por linea con ``SalesOrderLineNumber``, asi que un despacho parcial se
   suma y una orden servida en dos camiones da la suma real. Deducirla de ``OrderStatus``
   (``Completed`` -> todo servido) seria inventar: un ``Completed`` puede haberse cerrado
   con menos unidades de las pedidas.

3. **Las ordenes ``Deleted`` y ``Parked`` no cuentan.** Una borrada no es demanda y una
   estacionada no es un compromiso; contarlas mete quiebres que nadie sufrio.

La clave privada FIRMA y no viaja: Unleashed autentica con HMAC-SHA256 sobre la query
string, en base64, en ``api-auth-signature``. Mandar la clave en una cabecera la deja en
cada log de proxy del cliente.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


class UnleashedError(RuntimeError):
    """Unleashed contesto algo que no es una lectura. Nunca se degrada a una lista vacia.

    Un 401 que vuelve como ``[]`` es indistinguible de una tienda sin ventas, y produce
    un informe que le dice al cliente que no tiene quiebres.
    """


@dataclass(frozen=True)
class UnleashedCredentials:
    """El par de la API. ``api_key`` firma y NO se manda en ninguna cabecera."""

    api_id: str
    api_key: str
    #: Unleashed exige identificar la app: ``<cuenta>/<app>``, minusculas, sin espacios.
    client_type: str = "kern/kern"


def signature(query: str, api_key: str) -> str:
    """HMAC-SHA256 de la query string, en base64. ``query`` va SIN el ``?`` inicial.

    Sin parametros se firma la cadena vacia. Firmar el ``?`` o el path devuelve 401 solo
    en los endpoints que no llevan filtros, que es donde nadie mira primero.
    """
    digest = hmac.new(api_key.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _query_string(params: dict[str, Any]) -> str:
    """Los parametros como los va a mandar el transporte, para firmar exactamente eso.

    Ordenados: la firma cubre el texto, asi que dos ordenes distintos del mismo dict dan
    dos firmas distintas y una de las dos es la que el servidor no espera.
    """
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)


def auth_headers(creds: UnleashedCredentials, query: str) -> dict[str, str]:
    """Las cinco cabeceras que Unleashed exige, con la firma de ``query``."""
    return {
        "api-auth-id": creds.api_id,
        "api-auth-signature": signature(query, creds.api_key),
        "client-type": creds.client_type,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_pages(
    http: Any,
    resource: str,
    params: dict[str, Any],
    creds: UnleashedCredentials,
    *,
    max_pages: int = 100,
) -> list[dict]:
    """Todas las paginas de un recurso, concatenadas. ``[]`` solo si no hay nada.

    Unleashed pagina por SEGMENTO DE RUTA (``/SalesOrders/2``), no por cursor, y la firma
    cubre solo los parametros -- que son los mismos en todas las paginas. Aun asi se
    recalcula por pagina: si alguien mueve el numero a los parametros, reusar la firma
    falla en la pagina 2 y nunca en la 1.

    ``max_pages`` es un tope y no una optimizacion: un servidor que devuelve
    ``NumberOfPages`` creciente colgaria el job para siempre.
    """
    limpios = {k: v for k, v in params.items() if v is not None}
    query = _query_string(limpios)
    headers = auth_headers(creds, query)
    items: list[dict] = []
    pagina = 1
    while pagina <= max_pages:
        resp = http.get(f"/{resource}/{pagina}", params=limpios, headers=headers)
        estado = getattr(resp, "status_code", 200)
        if estado != 200:
            raise UnleashedError(
                f"Unleashed contesto {estado} en /{resource}/{pagina}. Una lectura fallida "
                "no se degrada a vacio: un cliente sin ventas y un token rechazado dan el "
                "mismo informe, y uno de los dos dice que no hay quiebres."
            )
        cuerpo = resp.json() or {}
        items.extend(cuerpo.get("Items") or [])
        total = int((cuerpo.get("Pagination") or {}).get("NumberOfPages") or 1)
        if pagina >= total:
            break
        pagina += 1
    return items
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_unleashed.py -q
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git status --short && git add src/connectors/unleashed.py tests/test_connector_unleashed.py && git commit -m "feat(unleashed): firma HMAC sobre la query string y paginado por segmento de ruta"
```

---

### Task 5: Unleashed — catalog, stock, and the demand side of orders

**Files:**
- Modify: `src/connectors/unleashed.py`
- Modify: `tests/test_connector_unleashed.py`

**Interfaces:**
- Consumes: Task 4's `fetch_pages`, `UnleashedCredentials`.
- Produces: `UnleashedConnector(http, creds, *, max_pages=100)` with `list_products()`,
  `inventory_levels()`, `orders(since=None)`; module constants `SALE_STATES_COUNTED`,
  `_SHIPPED_STATUSES`. Task 6 extends `orders()`; Task 7 declares its coverage.

- [ ] **Step 1: Write the failing test** — append to `tests/test_connector_unleashed.py`

```python
# -- catalogo, stock y el lado de demanda ---------------------------------------

from src.connectors import InventorySource, Order  # noqa: E402
from src.connectors.unleashed import UnleashedConnector  # noqa: E402


class RoutedHttp:
    """Un transporte que sirve payloads distintos segun el recurso pedido."""

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> dict:
            return self._payload

    def __init__(self, by_resource: dict[str, list[dict]]) -> None:
        self._by_resource = by_resource
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, *, params=None, headers=None):
        self.calls.append((path, dict(params or {})))
        recurso = path.strip("/").split("/")[0]
        items = self._by_resource.get(recurso, [])
        return RoutedHttp._Response(
            {"Pagination": {"NumberOfPages": 1, "PageNumber": 1}, "Items": items}
        )


_PRODUCTS = [
    {"ProductCode": "SKU-1", "ProductDescription": "Widget",
     "SellPriceTier1": {"Value": 20.0}, "DefaultSellPrice": 20.0, "LastCost": 12.0},
    {"ProductCode": "SKU-2", "ProductDescription": "Gadget",
     "DefaultSellPrice": 50.0, "LastCost": 30.0},
]

_STOCK = [
    {"ProductCode": "SKU-1", "QtyOnHand": 40.0, "AvailableQty": 35.0, "WarehouseCode": "MAIN"},
    {"ProductCode": "SKU-2", "QtyOnHand": 5.0, "AvailableQty": 5.0, "WarehouseCode": "MAIN"},
]

#: Una orden pedida el 5, prometida para el 10, cerrada el 12. Y una borrada, que no cuenta.
_ORDERS = [
    {
        "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
        "RequiredDate": "2026-01-10T00:00:00", "CompletedDate": "2026-01-12T00:00:00",
        "OrderStatus": "Completed",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 10.0, "UnitPrice": 20.0},
            {"LineNumber": 2, "Product": {"ProductCode": "SKU-2"},
             "OrderQuantity": 4.0, "UnitPrice": 50.0},
        ],
    },
    {
        "OrderNumber": "SO-2", "OrderDate": "2026-01-06T00:00:00",
        "RequiredDate": None, "CompletedDate": None, "OrderStatus": "Backordered",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 5.0, "UnitPrice": 20.0},
        ],
    },
    {
        "OrderNumber": "SO-3", "OrderDate": "2026-01-07T00:00:00",
        "OrderStatus": "Deleted",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 999.0, "UnitPrice": 20.0},
        ],
    },
]


def _connector(**extra) -> UnleashedConnector:
    http = RoutedHttp({"Products": _PRODUCTS, "StockOnHand": _STOCK,
                       "SalesOrders": _ORDERS, **extra})
    return UnleashedConnector(http, _CREDS)


def test_the_connector_satisfies_the_inventory_source_protocol():
    assert isinstance(_connector(), InventorySource)


def test_products_map_code_description_price_and_cost():
    productos = {p.sku: p for p in _connector().list_products()}
    assert productos["SKU-1"].title == "Widget"
    assert productos["SKU-1"].price == 20.0
    assert productos["SKU-1"].cost == 12.0


def test_stock_uses_available_not_on_hand():
    """``QtyOnHand`` incluye lo ya comprometido a otras ordenes. Planificar sobre el
    on-hand promete unidades que ya tienen dueno."""
    niveles = {lvl.sku: lvl for lvl in _connector().inventory_levels()}
    assert niveles["SKU-1"].available == 35.0
    assert niveles["SKU-1"].location == "MAIN"


def test_orders_carry_the_demand_side_and_sort_by_date():
    pedidos = _connector().orders()
    assert [o.order_id for o in pedidos] == ["SO-1", "SO-2"]
    primera = pedidos[0]
    assert primera.created_at == "2026-01-05"
    assert [(ln.sku, ln.quantity, ln.price) for ln in primera.lines] == [
        ("SKU-1", 10.0, 20.0), ("SKU-2", 4.0, 50.0),
    ]


def test_a_deleted_order_is_not_demand():
    """Una orden borrada con 999 unidades deformaria el pronostico de SKU-1 sola."""
    skus = [ln.quantity for o in _connector().orders() for ln in o.lines]
    assert 999.0 not in skus


def test_since_travels_as_the_start_date_filter():
    """Se filtra en el SERVIDOR. Traer el historico entero para descartarlo en memoria
    es lo que hace que la primera corrida contra una cuenta viva tarde minutos."""
    conector = _connector()
    conector.orders(since="2026-01-06")
    llamadas = [params for path, params in conector._http.calls if "SalesOrders" in path]
    assert llamadas and llamadas[0].get("startDate") == "2026-01-06"


def test_an_order_with_no_required_date_leaves_the_promise_empty():
    """Ausente y no ``OrderDate``: la fecha en que el cliente compro no es una promesa,
    y medir contra ella declara tarde a todo lo que no salio el mismo dia."""
    so2 = next(o for o in _connector().orders() if o.order_id == "SO-2")
    assert all(ln.promised_date is None for ln in so2.lines)


def test_the_promised_date_is_the_required_date_truncated_to_a_day():
    so1 = next(o for o in _connector().orders() if o.order_id == "SO-1")
    assert all(ln.promised_date == "2026-01-10" for ln in so1.lines)


def test_without_shipments_the_delivered_quantity_is_none_not_zero():
    """El None que sostiene el producto entero. ``/SalesOrders`` no trae cantidad
    despachada, y rellenarla con 0.0 le regala al cliente un 0% de fill rate sobre
    lineas que nadie midio; rellenarla con lo pedido le regala un 100%."""
    lineas = [ln for o in _connector().orders() for ln in o.lines]
    assert lineas and all(ln.delivered_qty is None for ln in lineas)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_unleashed.py -q
```

Expected: `ImportError: cannot import name 'UnleashedConnector'`.

- [ ] **Step 3: Write the minimal implementation** — append to `src/connectors/unleashed.py`

```python
#: Los estados que SON demanda. ``Deleted`` no es una venta y ``Parked`` es un borrador
#: que nadie confirmo; contarlos mete quiebres que ningun cliente sufrio.
SALE_STATES_COUNTED = ("Placed", "Backordered", "Completed")


def _text(value: Any) -> str | None:
    """Texto no vacio, o ``None``. Unleashed manda ``null`` y tambien ``""``."""
    if value is None:
        return None
    texto = str(value).strip()
    return texto or None


def _iso_date(value: Any) -> str | None:
    """La fecha ISO de un instante de Unleashed, o ``None`` si no vino ninguno.

    Unleashed manda ``2026-01-10T00:00:00`` y a veces ``/Date(...)`` en XML; solo se
    acepta la forma ISO, y cualquier otra se rechaza en vez de adivinarse -- una fecha
    mal parseada saca la linea de OTIF, que es mejor que ordenarla contra otra que no
    significa lo mismo.
    """
    texto = _text(value)
    if texto is None or len(texto) < 10:
        return None
    fecha = texto[:10]
    try:
        int(fecha[0:4]), int(fecha[5:7]), int(fecha[8:10])
    except ValueError:
        return None
    return fecha if fecha[4] == "-" and fecha[7] == "-" else None


def _number(value: Any) -> float | None:
    """Un numero reportado, o ``None`` cuando el backend no reporto ninguno."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class UnleashedConnector:
    """``InventorySource`` sobre Unleashed. Ver el docstring del modulo para el mapeo.

    ``http`` es cualquier objeto con ``.get(path, params=..., headers=...)`` estilo httpx:
    ``live_http(creds)`` arma el real, y los tests inyectan uno de mentira. El adaptador
    no arma su propio transporte a proposito -- un secreto que se ensambla en la capa HTTP
    hay que auditarlo en cada ruta que lo use.
    """

    def __init__(self, http: Any, creds: UnleashedCredentials, *, max_pages: int = 100) -> None:
        self._http = http
        self._creds = creds
        self._max_pages = max_pages

    def _read(self, resource: str, params: dict[str, Any] | None = None) -> list[dict]:
        return fetch_pages(
            self._http, resource, params or {}, self._creds, max_pages=self._max_pages
        )

    # -- read side (InventorySource) ------------------------------------------

    def list_products(self) -> list[Product]:
        productos: list[Product] = []
        for row in self._read("Products", {"pageSize": _PAGE_SIZE}):
            sku = _text(row.get("ProductCode"))
            if sku is None:
                continue
            productos.append(
                Product(
                    sku,
                    _text(row.get("ProductDescription")) or sku,
                    _number(row.get("DefaultSellPrice")) or 0.0,
                    _number(row.get("LastCost")) or 0.0,
                )
            )
        return productos

    def inventory_levels(self) -> list[InventoryLevel]:
        """El disponible, no el fisico.

        ``AvailableQty`` descuenta lo ya comprometido a otras ordenes; ``QtyOnHand`` no.
        Planificar sobre el fisico promete unidades que ya tienen dueno. Sin
        ``AvailableQty`` se cae al fisico, que es lo unico que queda y se sabe optimista.
        """
        niveles: list[InventoryLevel] = []
        for row in self._read("StockOnHand", {"pageSize": _PAGE_SIZE}):
            sku = _text(row.get("ProductCode"))
            if sku is None:
                continue
            disponible = _number(row.get("AvailableQty"))
            if disponible is None:
                disponible = _number(row.get("QtyOnHand")) or 0.0
            niveles.append(
                InventoryLevel(sku, disponible, _text(row.get("WarehouseCode")) or "default")
            )
        return niveles

    def orders(self, *, since: str | None = None) -> list[Order]:
        """Las ordenes que son demanda, con lo pedido en cada linea.

        ``since`` se filtra en el SERVIDOR (``startDate``): traer el historico entero
        para descartarlo en memoria es lo que hace que la primera corrida contra una
        cuenta viva tarde minutos y pague rate limit por nada.

        La promesa es ``RequiredDate``, que es de la ORDEN, y por eso todas las lineas
        llevan la misma. Vacia es lo habitual y significa que no se prometio fecha: la
        linea sale de OTIF en vez de medirse contra ``OrderDate``, que es cuando el
        cliente compro y no cuando le dijimos que llegaba.

        ``delivered_qty`` queda ``None`` aqui SIEMPRE: ``/SalesOrders`` no trae cantidad
        despachada. La llena ``orders_with_shipments()`` empalmando ``/SalesShipments``.
        """
        params: dict[str, Any] = {
            "pageSize": _PAGE_SIZE,
            "orderStatus": ",".join(SALE_STATES_COUNTED),
        }
        if since is not None:
            params["startDate"] = since
        return self._to_orders(self._read("SalesOrders", params))

    def _to_orders(
        self, filas: list[dict], despachos: dict[tuple[str, int], "_Shipped"] | None = None
    ) -> list[Order]:
        """Las filas crudas -> ``Order`` canonicos, ordenados por fecha.

        ``despachos`` es lo que ``/SalesShipments`` reporto, indexado por
        ``(OrderNumber, LineNumber)``. En ``None`` -- el camino de ``orders()`` -- las
        tres columnas de despacho quedan sin llenar y el servicio sale no medido.
        """
        salida: list[Order] = []
        for fila in filas:
            estado = _text(fila.get("OrderStatus")) or ""
            if estado not in SALE_STATES_COUNTED:
                continue
            numero = _text(fila.get("OrderNumber"))
            creada = _iso_date(fila.get("OrderDate"))
            if numero is None or creada is None:
                continue
            prometida = _iso_date(fila.get("RequiredDate"))
            completada = _iso_date(fila.get("CompletedDate"))
            lineas: list[OrderLine] = []
            for ln in fila.get("SalesOrderLines") or []:
                sku = _text((ln.get("Product") or {}).get("ProductCode"))
                pedidas = _number(ln.get("OrderQuantity"))
                if sku is None or pedidas is None:
                    continue
                enviado = (
                    despachos.get((numero, int(ln.get("LineNumber") or 0)))
                    if despachos is not None else None
                )
                lineas.append(
                    OrderLine(
                        sku,
                        pedidas,
                        _number(ln.get("UnitPrice")) or 0.0,
                        delivered_qty=enviado.qty if enviado else None,
                        promised_date=prometida,
                        delivered_date=(
                            enviado.dispatched if enviado
                            else (completada if despachos is None else None)
                        ),
                    )
                )
            if lineas:
                salida.append(Order(numero, creada, tuple(lineas)))
        return sorted(salida, key=lambda o: o.created_at)
```

Add at the top of the module, after the imports:

```python
from src.connectors import InventoryLevel, Order, OrderLine, Product

#: El maximo que acepta Unleashed. Menos paginas es menos rate limit y menos latencia en
#: la primera corrida, que es la unica que un prospecto mira.
_PAGE_SIZE = 200
```

Note: `_Shipped` is referenced in `_to_orders` and defined in Task 6. Until then the
annotation is a forward reference in quotes and `despachos` is only ever `None`, so the
module imports and every Task 5 test passes.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_unleashed.py -q
```

Expected: 18 passed.

- [ ] **Step 5: Commit**

```bash
git status --short && git add src/connectors/unleashed.py tests/test_connector_unleashed.py && git commit -m "feat(unleashed): catalogo, disponible y el lado de demanda de las ordenes"
```

---

### Task 6: Unleashed — the shipment join fills the delivered quantity

**Files:**
- Modify: `src/connectors/unleashed.py`
- Modify: `tests/test_connector_unleashed.py`

**Interfaces:**
- Consumes: Task 5's `UnleashedConnector._to_orders`, `_iso_date`, `_number`, `_text`.
- Produces: `_Shipped(qty: float, dispatched: str | None)` frozen dataclass;
  `shipments_by_line(rows) -> dict[tuple[str, int], _Shipped]`;
  `UnleashedConnector.orders_with_shipments(since=None) -> list[Order]`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_connector_unleashed.py`

```python
# -- el empalme de despachos ----------------------------------------------------

from src.connectors import realized_service  # noqa: E402
from src.connectors.unleashed import shipments_by_line  # noqa: E402

#: SO-1 linea 1 salio en DOS camiones (6 + 2 de 10 pedidas), el ultimo el 12.
#: SO-1 linea 2 salio entera el 9. SO-2 no tiene ningun despacho.
_SHIPMENTS = [
    {
        "ShipmentNumber": "SH-1", "OrderNumber": "SO-1", "ShipmentStatus": "Completed",
        "DispatchDate": "2026-01-09T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 6.0},
            {"LineNumber": 2, "SalesOrderLineNumber": 2,
             "Product": {"ProductCode": "SKU-2"}, "ShipmentQty": 4.0},
        ],
    },
    {
        "ShipmentNumber": "SH-2", "OrderNumber": "SO-1", "ShipmentStatus": "Completed",
        "DispatchDate": "2026-01-12T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 2.0},
        ],
    },
    {
        "ShipmentNumber": "SH-3", "OrderNumber": "SO-1", "ShipmentStatus": "Deleted",
        "DispatchDate": "2026-01-08T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 99.0},
        ],
    },
]


def _connector_con_despachos() -> UnleashedConnector:
    return _connector(SalesShipments=_SHIPMENTS)


def test_partial_shipments_of_one_line_are_summed():
    """Seis mas dos, no dos. Quedarse con el ultimo despacho reporta un quiebre de ocho
    unidades sobre una linea que se sirvio en dos camiones."""
    porlinea = shipments_by_line(_SHIPMENTS)
    assert porlinea[("SO-1", 1)].qty == 8.0


def test_the_dispatch_date_of_a_line_is_the_last_one_that_served_it():
    """Con la del PRIMER camion, una orden partida declara a tiempo lo que termino de
    salir tres dias despues."""
    assert shipments_by_line(_SHIPMENTS)[("SO-1", 1)].dispatched == "2026-01-12"


def test_a_deleted_shipment_never_shipped_anything():
    """SH-3 trae 99 unidades y esta borrada. Sumarla da un fill rate mayor a uno."""
    assert shipments_by_line(_SHIPMENTS)[("SO-1", 1)].qty == 8.0


def test_the_delivered_quantity_arrives_on_the_line():
    so1 = next(o for o in _connector_con_despachos().orders_with_shipments()
               if o.order_id == "SO-1")
    porsku = {ln.sku: ln for ln in so1.lines}
    assert porsku["SKU-1"].delivered_qty == 8.0
    assert porsku["SKU-2"].delivered_qty == 4.0


def test_a_line_with_no_shipment_stays_none_even_when_others_shipped():
    """El caso que separa este adaptador de uno que miente: SO-2 no tiene despacho, y
    dentro de la MISMA lectura donde SO-1 si lo tiene. Un 0.0 aqui es un quiebre total
    inventado sobre una orden que quiza salga manana."""
    so2 = next(o for o in _connector_con_despachos().orders_with_shipments()
               if o.order_id == "SO-2")
    assert all(ln.delivered_qty is None for ln in so2.lines)
    assert all(ln.delivered_date is None for ln in so2.lines)


def test_the_delivered_date_is_the_dispatch_not_the_order_completion():
    """``CompletedDate`` cierra la ORDEN entera; el despacho es por linea. Con despachos
    leidos gana el despacho, o una linea que salio el 9 hereda el 12 de su vecina."""
    so1 = next(o for o in _connector_con_despachos().orders_with_shipments()
               if o.order_id == "SO-1")
    porsku = {ln.sku: ln for ln in so1.lines}
    assert porsku["SKU-1"].delivered_date == "2026-01-12"
    assert porsku["SKU-2"].delivered_date == "2026-01-09"


def test_the_whole_chain_produces_a_measured_fill_rate_and_otif():
    """La prueba de que esto sirve para algo: adaptador -> realized_service -> numeros.
    SKU-1 sirvio 8 de 10 y salio el 12 contra una promesa del 10: 80% y OTIF 0.
    SKU-2 sirvio 4 de 4 y salio el 9: 100% y OTIF 1."""
    registros = {r.product_id: r
                 for r in realized_service(_connector_con_despachos().orders_with_shipments())}
    assert registros["SKU-1"].fill_rate == pytest.approx(8.0 / 15.0)
    assert registros["SKU-1"].otif == 0.0
    assert registros["SKU-2"].fill_rate == 1.0
    assert registros["SKU-2"].otif == 1.0
    assert registros["SKU-1"].provenance == "observed"


def test_the_shipment_read_is_filtered_by_the_same_window_as_the_orders():
    """Dos ventanas distintas dan un fill rate calculado sobre un universo de ordenes y
    unos despachos sobre otro, y la diferencia no se ve en ninguna de las dos salidas."""
    conector = _connector_con_despachos()
    conector.orders_with_shipments(since="2026-01-06")
    despachos = [params for path, params in conector._http.calls if "SalesShipments" in path]
    assert despachos and despachos[0].get("startDate") == "2026-01-06"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_unleashed.py -q
```

Expected: `ImportError: cannot import name 'shipments_by_line'`.

- [ ] **Step 3: Write the minimal implementation** — append to `src/connectors/unleashed.py`

```python
#: Los estados de despacho que efectivamente sacaron mercaderia. Uno borrado trae
#: cantidad igual, y sumarlo da un fill rate mayor a uno sobre unidades que no salieron.
_SHIPPED_STATUSES = ("Completed", "Dispatched", "Shipped", "Placed")


@dataclass(frozen=True)
class _Shipped:
    """Lo que salio de una linea de orden, sumado sobre todos sus despachos."""

    qty: float
    #: La fecha del ULTIMO despacho que la sirvio. Es DESPACHO, no llegada.
    dispatched: str | None


def shipments_by_line(filas: list[dict]) -> dict[tuple[str, int], _Shipped]:
    """``/SalesShipments`` -> ``{(OrderNumber, SalesOrderLineNumber): _Shipped}``.

    Se SUMA por linea porque Unleashed admite despachos parciales: una linea servida en
    dos camiones tiene dos ``SalesShipmentLine``, y quedarse con el ultimo reporta como
    quiebre lo que si se sirvio. La fecha, en cambio, es la del ULTIMO: la linea no
    termino de salir hasta ese camion, y usar la del primero declara a tiempo una orden
    que se completo tres dias despues.

    El empalme es por ``SalesOrderLineNumber`` y no por ``ProductCode``: una orden puede
    llevar el mismo SKU en dos lineas (precios o depositos distintos), y empalmar por
    producto le adjudicaria a una lo que salio de la otra.
    """
    acumulado: dict[tuple[str, int], _Shipped] = {}
    for envio in filas:
        if (_text(envio.get("ShipmentStatus")) or "") not in _SHIPPED_STATUSES:
            continue
        numero = _text(envio.get("OrderNumber"))
        if numero is None:
            continue
        salida = _iso_date(envio.get("DispatchDate"))
        for ln in envio.get("SalesShipmentLines") or []:
            linea = ln.get("SalesOrderLineNumber")
            cantidad = _number(ln.get("ShipmentQty"))
            if linea is None or cantidad is None:
                continue
            clave = (numero, int(linea))
            previo = acumulado.get(clave)
            if previo is None:
                acumulado[clave] = _Shipped(cantidad, salida)
                continue
            acumulado[clave] = _Shipped(
                previo.qty + cantidad,
                # ``max`` sobre las que existen: un despacho sin fecha no borra la del
                # otro camion, y tampoco se inventa una para el que no la trajo.
                max([d for d in (previo.dispatched, salida) if d], default=None),
            )
    return acumulado
```

and add the method to `UnleashedConnector`, immediately after `orders()`:

```python
    def orders_with_shipments(self, *, since: str | None = None) -> list[Order]:
        """Las ordenes CON lo que realmente salio: la lectura completa del servicio.

        Dos llamadas en vez de una, y es la unica forma: ``/SalesOrders`` no tiene
        cantidad despachada y ``/SalesShipments`` no tiene lo pedido. Con las dos, este
        adaptador mide fill rate y OTIF de salida -- que es exactamente lo que la
        competencia de anillo 2 verificada el 2026-08-29 no puede medir.

        La MISMA ventana ``since`` filtra las dos lecturas, a proposito: dos ventanas
        distintas dan un fill rate calculado sobre un universo de ordenes y unos
        despachos sobre otro, y esa diferencia no se ve en ninguna de las dos salidas por
        separado.

        Una linea sin despacho queda en ``None``, no en 0.0, incluso dentro de una
        lectura donde otras si lo tienen: la orden puede salir manana.
        """
        params: dict[str, Any] = {
            "pageSize": _PAGE_SIZE,
            "orderStatus": ",".join(SALE_STATES_COUNTED),
        }
        envios: dict[str, Any] = {"pageSize": _PAGE_SIZE}
        if since is not None:
            params["startDate"] = since
            envios["startDate"] = since
        despachos = shipments_by_line(self._read("SalesShipments", envios))
        return self._to_orders(self._read("SalesOrders", params), despachos=despachos)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_unleashed.py -q
```

Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git status --short && git add src/connectors/unleashed.py tests/test_connector_unleashed.py && git commit -m "feat(unleashed): el empalme de despachos llena la cantidad servida por linea"
```

---

### Task 7: Unleashed declares its coverage, and the guard executes it

**Files:**
- Modify: `src/connectors/unleashed.py`
- Modify: `tests/test_connector_service_contract.py`

**Interfaces:**
- Consumes: Task 1's `ServiceCoverage`/`FieldCoverage`, Task 3's `ADAPTERS`, Task 6's
  `orders_with_shipments`.
- Produces: `unleashed.SERVICE_COVERAGE`, `unleashed.live_http(creds, *, timeout=30.0)`.

This is where the two halves meet. The coverage the adapter declares is the coverage the
guard runs, over the same fixture, on the same fields.

- [ ] **Step 1: Write the failing test** — in `tests/test_connector_service_contract.py`

Add the Unleashed fixture before `ADAPTERS`:

```python
from tests.test_connector_unleashed import (  # noqa: E402
    _CREDS as _UNLEASHED_CREDS,
    _ORDERS as _UNLEASHED_ORDERS,
    _SHIPMENTS as _UNLEASHED_SHIPMENTS,
    RoutedHttp as _UnleashedHttp,
)
from src.connectors.unleashed import UnleashedConnector  # noqa: E402


class _UnleashedAdapter:
    """Unleashed leido ENTERO: ordenes mas despachos.

    ``orders()`` sola deja ``delivered_qty`` en ``None`` a proposito -- /SalesOrders no
    la trae -- y la declaracion describe el techo del adaptador, no el de su lectura mas
    barata. Envolver ``orders_with_shipments`` es lo que hace que el guard verifique la
    afirmacion que efectivamente se le hace a un cliente.
    """

    def __init__(self) -> None:
        self._conector = UnleashedConnector(
            _UnleashedHttp({"SalesOrders": _UNLEASHED_ORDERS,
                            "SalesShipments": _UNLEASHED_SHIPMENTS}),
            _UNLEASHED_CREDS,
        )

    def orders(self, *, since: str | None = None):
        return self._conector.orders_with_shipments(since=since)


def _unleashed():
    return _UnleashedAdapter()
```

and extend `ADAPTERS`:

```python
ADAPTERS: tuple[AdapterCase, ...] = (
    AdapterCase("simulator", "src.connectors.simulator", _simulator),
    AdapterCase("http_client", "src.connectors.http_client", _http_client),
    AdapterCase("mcp_source", "src.connectors.mcp_source", _mcp),
    AdapterCase("odoo", "src.connectors.odoo", _odoo),
    AdapterCase("unleashed", "src.connectors.unleashed", _unleashed),
)
```

and add the assertion that pins the finding this whole thread rests on:

```python
def test_unleashed_declares_its_delivery_date_as_a_dispatch_and_not_an_arrival():
    """El hallazgo que sostiene el argumento de venta, fijado en un test para que no se
    pierda cuando alguien 'mejore' el docstring.

    Unleashed no registra ninguna fecha de llegada: ni ``CompletedDate`` ni
    ``DispatchDate`` son la recepcion del cliente, y el indice de endpoints no tiene
    recurso de entrega. Entonces el OTIF que sale de aca mide DESPACHAR antes de la fecha
    pedida. Un adaptador que lo declare como llegada convierte cada demora de transporte
    en puntualidad, y se lo dice a un cliente que puede verificarlo."""
    from src.connectors.unleashed import SERVICE_COVERAGE

    entrega = SERVICE_COVERAGE.delivered_date
    assert entrega.reports is True
    assert "despacho" in entrega.means.lower()
    assert "llegada" in entrega.means.lower()


def test_unleashed_derives_the_delivered_quantity_from_a_second_endpoint():
    """No es MEASURED: /SalesOrders no la trae. Que sea DERIVED y que la fuente nombre
    /SalesShipments es lo que le dice al que lea esto por que hay dos lecturas."""
    from src.connectors import COVERAGE_DERIVED
    from src.connectors.unleashed import SERVICE_COVERAGE

    assert SERVICE_COVERAGE.delivered_qty.status == COVERAGE_DERIVED
    assert "SalesShipment" in SERVICE_COVERAGE.delivered_qty.source
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Expected: failures — `src.connectors.unleashed implements orders() but declares no SERVICE_COVERAGE`, plus the two new tests failing on `ImportError`.

- [ ] **Step 3: Write the minimal implementation** — append to `src/connectors/unleashed.py`

```python
def live_http(creds: UnleashedCredentials, *, timeout: float = 30.0) -> Any:
    """El cliente httpx apuntado a la API de Unleashed.

    ``creds`` viaja igual: la firma se calcula por request en ``fetch_pages``, porque
    depende de la query string de ESE request. Aqui solo se fija la base y el timeout --
    acotado y no infinito, porque esta lectura corre dentro de un request web y una
    cuenta que no contesta tiene que soltar el hilo en vez de quedarselo.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depende del extra instalado
        raise UnleashedError(
            "httpx es necesario para hablar con Unleashed -- instala el extra que lo "
            "trae, o inyecta un transporte con http= (los tests lo hacen)"
        ) from exc
    return httpx.Client(base_url=API_BASE, timeout=timeout)


#: Lo que Unleashed puede y no puede medir del servicio al cliente. Verificado contra la
#: doc del vendor el 2026-08-29; ``tests/test_connector_service_contract.py`` lo CORRE
#: contra este adaptador en vez de leerlo.
SERVICE_COVERAGE = ServiceCoverage(
    backend="Unleashed (API v1: /SalesOrders + /SalesShipments)",
    delivered_qty=FieldCoverage(
        COVERAGE_DERIVED,
        "SalesShipmentLine.ShipmentQty, sumada por (OrderNumber, SalesOrderLineNumber)",
        "unidades que SALIERON de la linea, sumando despachos parciales. No esta en "
        "/SalesOrders: sin leer /SalesShipments queda en None, que es 'no lo medimos' y "
        "no un 0% de fill rate",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED,
        "SalesOrder.RequiredDate",
        "la fecha que el cliente pidio, a nivel ORDEN y replicada a sus lineas. Vacia es "
        "lo habitual y saca la linea de OTIF en vez de medirla contra OrderDate",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_DERIVED,
        "SalesShipment.DispatchDate del ultimo despacho de la linea "
        "(SalesOrder.CompletedDate cuando no se leyeron despachos)",
        "la fecha de DESPACHO, no de llegada. Unleashed no registra ninguna llegada: no "
        "tiene recurso de entrega y sus dos fechas son salidas. El OTIF que sale de aca "
        "mide despachar antes de la fecha pedida",
    ),
)
```

Extend the module's imports:

```python
from src.connectors import (
    COVERAGE_DERIVED,
    COVERAGE_MEASURED,
    FieldCoverage,
    InventoryLevel,
    Order,
    OrderLine,
    Product,
    ServiceCoverage,
)

#: La base de la API v1. Se fija aqui y no en cada llamada para que ``live_http`` sea el
#: unico lugar que la conoce.
API_BASE = "https://api.unleashedsoftware.com"
```

- [ ] **Step 4: Run the guard and the adapter suite together**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py tests/test_connector_unleashed.py -q
```

Expected: 49 passed (26 adapter + 23 guard).

- [ ] **Step 5: Run ruff over everything touched**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m ruff check src/connectors/ tests/test_connector_service_contract.py tests/test_connector_unleashed.py
```

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git status --short && git add src/connectors/unleashed.py tests/test_connector_service_contract.py && git commit -m "feat(unleashed): declara su cobertura de servicio y el guard la ejecuta"
```

---

### Task 8: Full-suite verification and the handoff record

**Files:**
- Modify: `docs/handoffs/HANDOFF-adaptadores-canal-2026-08-29.md` (append a closing section)

- [ ] **Step 1: Run the verification command from the handoff**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_order_fulfillment.py -q
```

Expected: all pass, unchanged.

- [ ] **Step 2: Run every connector suite**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/ -k "connector or fulfillment or mcp_inventory or simulation_tool" -q
```

Expected: all pass. Any failure here is a regression introduced by the four declarations
added in Task 2 — fix the adapter, not the guard.

- [ ] **Step 3: Run the whole suite**

```bash
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/ -q -x --timeout=600
```

Expected: pass. If `pytest-timeout` is not installed, drop `--timeout=600`.

- [ ] **Step 4: Record the two vendor-doc corrections in the handoff**

Append to `docs/handoffs/HANDOFF-adaptadores-canal-2026-08-29.md`:

```markdown
## 8. Cerrado el 2026-08-29 (rama `feat/unleashed-cobertura-servicio`)

Entregado: `src/connectors/unleashed.py`, `tests/test_connector_unleashed.py`,
`tests/test_connector_service_contract.py`, y la declaracion `SERVICE_COVERAGE` en los
cinco adaptadores que leen ordenes.

**Dos correcciones a la seccion 2 de este mismo documento**, verificadas contra la doc del
vendor y ahora fijadas en tests:

1. Unleashed **SI** expone cantidad despachada por linea, en `/SalesShipments` ->
   `SalesShipmentLines[].ShipmentQty`, empalmable por `(OrderNumber,
   SalesOrderLineNumber)`. No esta en `/SalesOrders`, que es lo que decia la seccion 2 y
   es cierto de ese endpoint. D1 (Unleashed primero) no cambia; cambia su costo declarado:
   el fill rate NO queda incompleto si se leen los despachos.
2. Unleashed **no tiene ninguna fecha de llegada**. `CompletedDate` y `DispatchDate` son
   las dos eventos de SALIDA, y el indice de endpoints no tiene recurso de entrega. Su
   OTIF es "despachado antes de la fecha pedida", nunca "llegado antes de". Fijado en
   `test_unleashed_declares_its_delivery_date_as_a_dispatch_and_not_an_arrival`.

Sin tocar: D2 (precio de entrada AU/NZ, sigue abierta y es del usuario), D4 (BWM se queda
donde esta), `.mcp.json` y `data/bateria/` (de otra sesion).
```

- [ ] **Step 5: Commit**

```bash
git status --short && git add docs/handoffs/HANDOFF-adaptadores-canal-2026-08-29.md && git commit -m "docs(handoff): las dos correcciones de la doc de Unleashed, ya fijadas en tests"
```

---

## Self-review

**Spec coverage.** Handoff §1 (adapter + guard) → Tasks 4-7 and 1-3. §4-D1 (Unleashed
first) → Tasks 4-6, with the `delivered_qty=None` path preserved in `orders()` and tested
in `test_without_shipments_the_delivered_quantity_is_none_not_zero`. §4-D3 (branch off
main) → Global Constraints, done. §4-D4 (leave BWM) → Global Constraints, no task touches
`mcdm.py` or `sourcing_job.py`. §4-D2 (price) → deliberately no task. §5 traps: shared
worktree → explicit pathspec in every commit step; venv per worktree → every command uses
`.venv/Scripts/python.exe`; long heredocs → every file is written with the editor, no
`cat <<EOF`; "tests ≠ in production" → the guard executes declarations rather than reading
them, which is the same lesson applied. §6 skills → this plan is `writing-plans`; every
task is RED → GREEN → commit per `test-driven-development`, guard before adapter.

**Placeholder scan.** No TBD/TODO. Every code step carries the literal code. Task 5's
forward reference to `_Shipped` is named as such with the reason it still imports.

**Type consistency.** `FieldCoverage(status, source, means)` and `.reports` are used
identically in Tasks 1, 2, 3 and 7. `ServiceCoverage.fields()` returns the three
`OrderLine` attribute names and `_observed()` reads those same three by `getattr` — one
spelling, no translation table. `_Shipped(qty, dispatched)` is constructed in Task 6 and
read in Task 5's `_to_orders` under those exact attribute names. `fetch_pages(http,
resource, params, creds, *, max_pages)` has one signature across Tasks 4, 5 and 6.
`AdapterCase(name, module, build)` is defined in Task 2 and extended in Tasks 3 and 7 with
the same three positional fields.
