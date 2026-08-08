"""Stocky Migration & Offboarding Importer for Kern (Phase 4).

Parses CSV exports from Shopify Stocky (suppliers, purchase orders, reorder points)
and converts them into Kern client parameter profiles and inventory intake structures.

**Absence is declared with a motive; it is never filled in.** This module feeds a
paid deliverable (the "Chequeo de migracion Stocky", USD 350-400 fixed), and it
used to break that rule in the most expensive way available: when the merchant's
export lacked a lead-time / MOQ / currency column it produced ``14`` / ``1`` /
``'USD'``, and ``jobs/stocky_migration_job.py`` printed those under the heading
"Parametros de cliente detectados". A Chilean merchant who never exported a
currency column got their profile sealed in dollars and believed it had been
*detected*. The purchase-order reader had the same shape --
``int(row.get(A) or row.get(B, 0))`` makes "there is no such column" and "this
line really was free" the same number, and a PO with no supplier column became
"Unknown", a supplier name that reads like a fact.

So every optional field on the parsed records is ``T | None`` plus a
:class:`FieldAbsence` carrying a reason a merchant can act on ("no hay columna
Currency en el export" is a different problem from "la columna vino vacia para
esta fila"). ``__post_init__`` makes a dishonest record UNCONSTRUCTIBLE: a
``None`` without a reason, a value that also claims to be absent, or an absence
naming a field that does not exist all raise. The reasons are Spanish because
they surface in the merchant's report; the code and docstrings are English, the
same split ``src/stocky_migration.py`` uses.

Nothing here decides how thin a measurement is *too* thin -- that would be a
policy nobody verified. :func:`client_profile_evidence` states the coverage and
:func:`verify_client_profile_params` vetoes the dishonest combination: a
partially measured parameter shipped under a "detected" heading with its
coverage note dropped.
"""

from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd


@dataclass(frozen=True)
class FieldAbsence:
    """One field the export did not measure, and why.

    The reason is the whole point: "the column is not in the file", "the column
    is there but this row's cell is empty" and "the cell says 'por confirmar'"
    are three different things for the merchant, and collapsing them into a
    silent default is what this module exists to prevent.
    """

    field: str
    reason: str


def absence_reason(record: object, field_name: str) -> str | None:
    """Why ``field_name`` is missing on ``record``, or ``None`` if it was measured."""
    for absence in getattr(record, "absences", ()):
        if absence.field == field_name:
            return absence.reason
    return None


def absent_fields(record: object) -> tuple[str, ...]:
    """The names of every field on ``record`` the export did not measure."""
    return tuple(a.field for a in getattr(record, "absences", ()))


def _check_absences(record: object, optional_fields: tuple[str, ...]) -> None:
    """Reject any record whose declared absences disagree with its own values.

    Called from every ``__post_init__``. The four rejections below are what make
    a deceptive record impossible to build rather than merely discouraged: you
    cannot leave a hole unexplained, you cannot explain a hole that is not
    there, you cannot point at a field nobody has, and you cannot pass off an
    empty string as a motive.
    """
    seen: set[str] = set()
    for absence in record.absences:  # type: ignore[attr-defined]
        if absence.field not in optional_fields:
            raise ValueError(
                f"{type(record).__name__} declares an absence for unknown field "
                f"{absence.field!r} (known optional fields: {optional_fields})")
        if absence.field in seen:
            raise ValueError(
                f"{type(record).__name__} declares {absence.field!r} absent twice")
        if not absence.reason.strip():
            raise ValueError(
                f"{type(record).__name__} declares {absence.field!r} absent with no "
                "motive - an absence without a reason is indistinguishable from a bug")
        seen.add(absence.field)

    for name in optional_fields:
        value = getattr(record, name)
        if value is None and name not in seen:
            raise ValueError(
                f"{type(record).__name__}.{name} is None but no reason was recorded - "
                "absence is declared with a motive, never left blank")
        if value is not None and name in seen:
            raise ValueError(
                f"{type(record).__name__}.{name} carries the value {value!r} AND an "
                "absence - it cannot be both measured and missing")


def _reject_negative(record: object, names: tuple[str, ...]) -> None:
    """A negative quantity/price is not a measurement, it is a corrupt cell.

    The parsers never produce one (they turn it into a declared absence naming
    the offending value); this guards the constructor against a caller who
    builds a record by hand.
    """
    for name in names:
        value = getattr(record, name)
        if value is not None and value < 0:
            raise ValueError(
                f"{type(record).__name__}.{name} is {value!r}: a negative value is not "
                "a measurement. Record it as an absence with the raw cell as its motive.")


_SUPPLIER_OPTIONAL = ("contact_email", "lead_time_days", "moq", "currency")


@dataclass(frozen=True)
class StockySupplier:
    """One supplier row. ``supplier_id`` may be a slug DERIVED from the name (a
    surrogate key over data the export really carried), which is not the same as
    a value invented to fill a hole -- the four optional fields below are the
    ones the export either measured or did not."""

    supplier_id: str
    name: str
    contact_email: str | None = None
    lead_time_days: int | None = None
    moq: int | None = None
    currency: str | None = None
    absences: tuple[FieldAbsence, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("StockySupplier.name is empty - a nameless supplier is not "
                             "a supplier; it used to be recorded as 'Unknown'")
        _check_absences(self, _SUPPLIER_OPTIONAL)
        _reject_negative(self, ("lead_time_days", "moq"))


_PO_OPTIONAL = (
    "po_number",
    "supplier_name",
    "status",
    "quantity_ordered",
    "quantity_received",
    "cost_per_unit",
    "order_date",
    "expected_date",
)


@dataclass(frozen=True)
class StockyPurchaseOrder:
    """One purchase-order line. ``sku`` is the only field a PO row cannot lack
    (rows without one are skipped by the parser); everything else, including the
    PO number and the status, is measured or declared absent. ``status`` has no
    default on purpose: an order whose status nobody exported is not a draft.
    """

    sku: str
    po_number: str | None = None
    supplier_name: str | None = None
    status: str | None = None  # draft, sent, partial, received, cancelled
    quantity_ordered: int | None = None
    quantity_received: int | None = None
    cost_per_unit: Decimal | None = None
    order_date: str | None = None
    expected_date: str | None = None
    absences: tuple[FieldAbsence, ...] = ()

    def __post_init__(self) -> None:
        if not self.sku.strip():
            raise ValueError("StockyPurchaseOrder.sku is empty - a PO line with no SKU "
                             "cannot be keyed to anything")
        _check_absences(self, _PO_OPTIONAL)
        _reject_negative(self, ("quantity_ordered", "quantity_received", "cost_per_unit"))


@dataclass
class StockyReorderPoint:
    sku: str
    min_reorder_point: int
    max_reorder_point: int
    target_stock: int


@dataclass
class StockyMigrationBatch:
    suppliers: list[StockySupplier] = field(default_factory=list)
    purchase_orders: list[StockyPurchaseOrder] = field(default_factory=list)
    reorder_points: list[StockyReorderPoint] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "suppliers_count": len(self.suppliers),
            "purchase_orders_count": len(self.purchase_orders),
            "reorder_points_count": len(self.reorder_points),
        }


# --------------------------------------------------------------------------
# raw-cell reading: the three ways a value can be missing
# --------------------------------------------------------------------------


def _raw(row: dict, columns: tuple[str, ...]) -> tuple[str | None, str | None]:
    """``(value, reason)`` for the first of ``columns`` that carries something.

    Exactly one of the two is ``None``. Stocky exports title the same field
    several ways, so a field is only *missing a column* when none of its aliases
    is in the header at all -- distinguished from a header that exists with an
    empty cell, because the merchant fixes those two problems differently.
    """
    present = [c for c in columns if c in row]
    if not present:
        return None, f"no hay columna {' / '.join(columns)} en el export"
    for name in present:
        cell = row[name]
        if cell is not None and str(cell).strip():
            return str(cell).strip(), None
    return None, f"la columna {' / '.join(present)} vino vacia en esta fila"


def _as_int(raw: str | None, reason: str | None, column_label: str) -> tuple[int | None, str | None]:
    """Parse a whole number, or hand back a reason quoting the offending cell."""
    if raw is None:
        return None, reason
    try:
        number = Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None, f"{column_label} trae un valor no numerico: {raw!r}"
    if number != number.to_integral_value():
        return None, f"{column_label} trae un valor no entero: {raw!r}"
    if number < 0:
        return None, f"{column_label} trae un valor negativo: {raw!r}"
    return int(number), None


def _as_money(raw: str | None, reason: str | None, column_label: str) -> tuple[Decimal | None, str | None]:
    """Parse money as :class:`Decimal` straight from the text (never via float,
    per the repo convention), or hand back a reason quoting the cell."""
    if raw is None:
        return None, reason
    try:
        amount = Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None, f"{column_label} trae un valor no numerico: {raw!r}"
    if amount < 0:
        return None, f"{column_label} trae un valor negativo: {raw!r}"
    return amount, None


def _absences(pairs: dict[str, str | None]) -> tuple[FieldAbsence, ...]:
    """Turn ``{field: reason_or_None}`` into the record's absence tuple."""
    return tuple(FieldAbsence(name, reason) for name, reason in pairs.items() if reason)


# --------------------------------------------------------------------------
# parsers
# --------------------------------------------------------------------------

_LEAD_TIME_COLUMNS = ("Lead Time (days)", "Lead Time", "lead_time")
_MOQ_COLUMNS = ("MOQ", "Min Order Qty", "moq")
_CURRENCY_COLUMNS = ("Currency", "currency")
_EMAIL_COLUMNS = ("Email", "Contact Email")
_NAME_COLUMNS = ("Supplier Name", "Name", "supplier_name")
_SUPPLIER_ID_COLUMNS = ("ID", "Supplier ID")


def parse_stocky_suppliers_csv(csv_content: str | io.StringIO) -> list[StockySupplier]:
    """Parse a Stocky suppliers export. Any parameter the file does not carry
    comes back ``None`` with its reason attached -- never as 14 days / MOQ 1 /
    USD, which is what a merchant then reads as "detected".

    A row with no supplier name raises: that is not a gap in a supplier record,
    it is the absence of the record's identity, and calling it "Unknown" put a
    fabricated counterparty into a paid report.
    """
    f = io.StringIO(csv_content) if isinstance(csv_content, str) else csv_content

    suppliers = []
    for line_no, row in enumerate(csv.DictReader(f), start=1):
        name, _ = _raw(row, _NAME_COLUMNS)
        if not name:
            raise ValueError(
                f"suppliers CSV row {line_no} has no supplier name (looked for "
                f"{' / '.join(_NAME_COLUMNS)}). Fix the export rather than shipping "
                "an 'Unknown' supplier in the migration report.")

        supplier_id, _ = _raw(row, _SUPPLIER_ID_COLUMNS)
        email, email_reason = _raw(row, _EMAIL_COLUMNS)
        lead_time, lead_reason = _as_int(*_raw(row, _LEAD_TIME_COLUMNS), "el lead time")
        moq, moq_reason = _as_int(*_raw(row, _MOQ_COLUMNS), "el MOQ")
        currency, currency_reason = _raw(row, _CURRENCY_COLUMNS)

        suppliers.append(
            StockySupplier(
                # Derived from the name when the export carries no id column: a
                # slug over real data, not a value standing in for missing data.
                supplier_id=str(supplier_id or name.lower().replace(" ", "_")),
                name=str(name),
                contact_email=email,
                lead_time_days=lead_time,
                moq=moq,
                currency=currency,
                absences=_absences({
                    "contact_email": email_reason,
                    "lead_time_days": lead_reason,
                    "moq": moq_reason,
                    "currency": currency_reason,
                }),
            )
        )
    return suppliers


_PO_NUMBER_COLUMNS = ("PO Number", "PO #", "Number")
_PO_SUPPLIER_COLUMNS = ("Supplier", "Supplier Name")
_PO_STATUS_COLUMNS = ("Status",)
_PO_SKU_COLUMNS = ("SKU", "Variant SKU")
_PO_ORDERED_COLUMNS = ("Quantity Ordered", "Ordered Qty")
_PO_RECEIVED_COLUMNS = ("Quantity Received", "Received Qty")
_PO_COST_COLUMNS = ("Cost Price", "Unit Cost")
_PO_ORDER_DATE_COLUMNS = ("Created At", "Order Date")
_PO_EXPECTED_DATE_COLUMNS = ("Expected At", "Delivery Date")


def parse_stocky_purchase_orders_csv(csv_content: str | io.StringIO) -> list[StockyPurchaseOrder]:
    """Parse a Stocky Purchase Orders export.

    The quantities and the cost keep the distinction the old
    ``int(row.get(A) or row.get(B, 0))`` destroyed: a line genuinely ordered at
    zero units or shipped at no charge parses to ``0`` with no absence, while a
    column the export never had parses to ``None`` with its reason. Rows with no
    SKU are skipped -- there is nothing to key them to.
    """
    f = io.StringIO(csv_content) if isinstance(csv_content, str) else csv_content

    pos = []
    for row in csv.DictReader(f):
        sku, _ = _raw(row, _PO_SKU_COLUMNS)
        if not sku:
            continue

        po_num, po_num_reason = _raw(row, _PO_NUMBER_COLUMNS)
        supplier, supplier_reason = _raw(row, _PO_SUPPLIER_COLUMNS)
        status, status_reason = _raw(row, _PO_STATUS_COLUMNS)
        ordered, ordered_reason = _as_int(*_raw(row, _PO_ORDERED_COLUMNS), "la cantidad pedida")
        received, received_reason = _as_int(*_raw(row, _PO_RECEIVED_COLUMNS), "la cantidad recibida")
        cost, cost_reason = _as_money(*_raw(row, _PO_COST_COLUMNS), "el costo unitario")
        order_date, order_date_reason = _raw(row, _PO_ORDER_DATE_COLUMNS)
        expected_date, expected_date_reason = _raw(row, _PO_EXPECTED_DATE_COLUMNS)

        pos.append(
            StockyPurchaseOrder(
                sku=str(sku),
                po_number=po_num,
                supplier_name=supplier,
                status=status.lower() if status else None,
                quantity_ordered=ordered,
                quantity_received=received,
                cost_per_unit=cost,
                order_date=order_date,
                expected_date=expected_date,
                absences=_absences({
                    "po_number": po_num_reason,
                    "supplier_name": supplier_reason,
                    "status": status_reason,
                    "quantity_ordered": ordered_reason,
                    "quantity_received": received_reason,
                    "cost_per_unit": cost_reason,
                    "order_date": order_date_reason,
                    "expected_date": expected_date_reason,
                }),
            )
        )
    return pos


def parse_stocky_reorder_points_csv(csv_content: str | io.StringIO) -> list[StockyReorderPoint]:
    """Parse Stocky reorder points / min-max CSV export.

    NOTE (known gap, deliberately not fixed here): a missing "Min Stock" column
    still lands as ``0``, which ``src.stocky_migration.audit_sku_master`` then
    reports as "reorden <=0 -- limpiar antes de migrar", a data defect the
    merchant does not actually have. Making these ``int | None`` crashes that
    audit (``None <= 0``), and ``src/stocky_migration.py`` is outside this
    change's ownership. See the handoff note for the exact diff it needs.
    """
    if isinstance(csv_content, str):
        f = io.StringIO(csv_content)
    else:
        f = csv_content

    reader = csv.DictReader(f)
    reorders = []
    for row in reader:
        sku = row.get("SKU") or row.get("Variant SKU", "")
        min_pt = int(row.get("Min Stock") or row.get("Reorder Point", 0))
        max_pt = int(row.get("Max Stock") or row.get("Target Stock", 0))
        target = int(row.get("Target Stock") or max_pt or min_pt)

        if sku:
            reorders.append(
                StockyReorderPoint(
                    sku=str(sku),
                    min_reorder_point=min_pt,
                    max_reorder_point=max_pt,
                    target_stock=target,
                )
            )
    return reorders


def to_intake_frame(batch: StockyMigrationBatch) -> pd.DataFrame:
    """A per-SKU product-master frame from a parsed batch, keyed by SKU across
    the reorder-point and purchase-order exports.

    Columns: ``sku``, ``reorder_point`` (Stocky min), ``max_reorder_point``,
    ``target_stock``, ``primary_supplier`` (the supplier on that SKU's most
    recent PO, if any), ``last_unit_cost`` (that PO's cost). This is the shape
    Kern's ``jobs.data_quality`` consumes for the migration health check -- it
    is deliberately NOT a demand-history frame: Stocky's migration exports
    carry no sales time series, so no forecast can be honestly built from them
    (that absence is itself part of the migration verdict -- see
    ``src/stocky_migration.py``). Returns an empty frame (with the columns
    still defined) when the batch has neither reorder points nor POs.

    ``primary_supplier`` / ``last_unit_cost`` are empty both when the SKU has no
    PO at all and when its PO carried no such column: either way nothing was
    measured, so the cell must not read as "Unknown" or as a cost of 0.
    """
    # Latest PO per SKU wins (order_date desc; None dates sort last), so a
    # SKU re-ordered from a new supplier reflects the current relationship.
    latest_po: dict[str, StockyPurchaseOrder] = {}
    for po in sorted(batch.purchase_orders, key=lambda p: (p.order_date or "")):
        latest_po[po.sku] = po  # last write wins -> most recent order_date

    skus = {rp.sku for rp in batch.reorder_points} | set(latest_po)
    rp_by_sku = {rp.sku: rp for rp in batch.reorder_points}

    rows = []
    for sku in sorted(skus):
        rp = rp_by_sku.get(sku)
        po = latest_po.get(sku)
        rows.append(
            {
                "sku": sku,
                "reorder_point": rp.min_reorder_point if rp else None,
                "max_reorder_point": rp.max_reorder_point if rp else None,
                "target_stock": rp.target_stock if rp else None,
                "primary_supplier": po.supplier_name if po else None,
                "last_unit_cost": po.cost_per_unit if po else None,
            }
        )
    columns = ["sku", "reorder_point", "max_reorder_point", "target_stock", "primary_supplier", "last_unit_cost"]
    return pd.DataFrame(rows, columns=columns)


@dataclass(frozen=True)
class ProfileEvidence:
    """What the export actually measured behind each seeded profile parameter.

    ``params`` holds only measured values; ``notes`` says how much of the
    supplier base each one rests on, and is EMPTY exactly when every supplier
    carried the field. The counts are kept so a caller can apply its own
    coverage policy -- this module states no threshold of its own, because
    "N suppliers is enough" is a policy nobody has verified.
    """

    params: dict
    notes: tuple[str, ...]
    n_suppliers: int
    n_with_lead_time: int
    n_with_currency: int


def _coverage_note(param: str, measured: int, total: int) -> str:
    """The sentence that has to travel with a partial measurement."""
    return (f"{param}: medido en {measured} de {total} proveedor(es); "
            f"{total - measured} sin ese dato en el export.")


def client_profile_evidence(batch: StockyMigrationBatch) -> ProfileEvidence:
    """The profile parameters a Stocky batch can honestly seed, WITH the
    coverage behind them.

    Only ``lead_time_days`` (the MEDIAN of the suppliers that reported one) and
    ``currency`` (only when every supplier that reported one agrees) are
    derivable at all: holding_rate / order_cost / service_level are not in any
    Stocky export, so they are left absent -- the client answers them once at
    intake, per ``src.client_profile``'s own contract.

    A median over 2 of 50 suppliers is a real number and a thin one. It is
    returned, and ``notes`` says so; deciding whether that is good enough is the
    caller's call, and :func:`verify_client_profile_params` refuses to let the
    number ship under a "detected" heading without the note.
    """
    suppliers = batch.suppliers
    total = len(suppliers)

    lead_times = [s.lead_time_days for s in suppliers if s.lead_time_days is not None]
    currencies = [s.currency for s in suppliers if s.currency]

    params: dict = {}
    notes: list[str] = []

    if lead_times:
        params["lead_time_days"] = float(statistics.median(lead_times))
        if len(lead_times) < total:
            notes.append(_coverage_note("lead_time_days", len(lead_times), total))

    if currencies and len(set(currencies)) == 1:
        params["currency"] = currencies[0]
        if len(currencies) < total:
            notes.append(_coverage_note("currency", len(currencies), total))

    return ProfileEvidence(
        params=params,
        notes=tuple(notes),
        n_suppliers=total,
        n_with_lead_time=len(lead_times),
        n_with_currency=len(currencies),
    )


def to_client_profile_params(batch: StockyMigrationBatch) -> dict:
    """The measured subset of ``src.client_profile.ClientProfile`` params.

    Kept as a plain dict for the existing callers. It carries values ONLY -- ask
    :func:`client_profile_evidence` for the coverage that belongs next to them,
    and run :func:`verify_client_profile_params` before rendering them under a
    heading that claims they were detected.
    """
    return client_profile_evidence(batch).params


def verify_client_profile_params(
    batch: StockyMigrationBatch,
    params: dict,
    *,
    notes: tuple[str, ...] = (),
) -> list[str]:
    """QA gate for the "Parametros de cliente detectados" section. Empty = shippable.

    Vetoes the DISHONEST combination, not the gap. Reporting one parameter and
    omitting the rest is a valid delivery; what is never valid is a parameter no
    row measured, or a partial measurement rendered under a heading that says
    "detected" with the sentence that qualifies it left out.
    """
    issues: list[str] = []
    evidence = client_profile_evidence(batch)

    for key, value in params.items():
        if key not in evidence.params:
            issues.append(
                f"params[{key!r}] = {value!r} but no supplier row in the export measured "
                f"{key} - it cannot be rendered as detected")
        elif evidence.params[key] != value:
            issues.append(
                f"params[{key!r}] = {value!r} does not match the measured "
                f"{evidence.params[key]!r}")

    for note in evidence.notes:
        param = note.split(":", 1)[0]
        if param in params and note not in notes:
            issues.append(
                f"{param} rests on a partial measurement and its coverage note was "
                f"dropped - ship it with: {note}")

    return issues
