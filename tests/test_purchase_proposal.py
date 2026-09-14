"""Tests for src/purchase_proposal.py: replenishment needs -> a draft PO per supplier.

The unit under test is the step that turns "buy 120 units of SKU-1" into something a
buyer can send: split by supplier, dated on both ends (release and need-by), with the
reason travelling on the line. A SKU with no supplier is a residual to hand back, never
a line to drop.
"""

from datetime import date

import pytest

from src.purchase_proposal import (
    EMPTY_PROPOSAL,
    LEAD_TIME_ASSUMED,
    LEAD_TIME_STATED,
    UNSOURCED,
    ProposedLine,
    PurchaseProposal,
    ReplenishmentNeed,
    build_proposal,
    proposal_rows,
)

_TODAY = date(2026, 8, 10)

_SUPPLIERS = {"SKU-1": "ACME", "SKU-2": "BETA", "SKU-3": "ACME"}
_LEAD_TIMES = {"SKU-1": 7.0, "SKU-2": 21.0, "SKU-3": 3.0}


def _needs() -> list[ReplenishmentNeed]:
    return [
        ReplenishmentNeed("SKU-1", 120.0, reason="below ROP", days_to_stockout=4.0),
        ReplenishmentNeed("SKU-2", 80.0, reason="projected stockout", days_to_stockout=10.0),
        ReplenishmentNeed("SKU-3", 15.0, reason="red zone", days_to_stockout=1.0),
    ]


def _proposal(**kwargs) -> PurchaseProposal:
    return build_proposal(
        kwargs.pop("needs", _needs()),
        supplier_by_sku=kwargs.pop("supplier_by_sku", _SUPPLIERS),
        lead_time_days=kwargs.pop("lead_time_days", _LEAD_TIMES),
        today=kwargs.pop("today", _TODAY),
        **kwargs,
    )


# -- grouping -----------------------------------------------------------------


def test_the_buy_is_split_into_one_order_per_supplier():
    proposal = _proposal()

    assert [o.supplier for o in proposal.orders] == ["ACME", "BETA"]
    acme, beta = proposal.orders
    assert [ln.sku for ln in acme.lines] == ["SKU-1", "SKU-3"]
    assert [ln.sku for ln in beta.lines] == ["SKU-2"]
    assert acme.total_qty == pytest.approx(135.0)
    assert proposal.n_orders == 2 and proposal.n_lines == 3


def test_every_line_carries_supplier_sku_qty_dates_and_reason():
    line = _proposal().orders[0].lines[0]

    assert (line.supplier, line.sku, line.order_qty) == ("ACME", "SKU-1", 120.0)
    assert line.order_date == _TODAY
    assert line.required_date == date(2026, 8, 14)      # 4 days of cover left
    assert line.expected_arrival == date(2026, 8, 17)   # 7-day lead time
    assert line.reason == "below ROP"


# -- dates --------------------------------------------------------------------


def test_the_suppliers_lead_time_drives_the_expected_arrival():
    proposal = _proposal()

    by_sku = {ln.sku: ln for ln in proposal.all_lines}
    assert by_sku["SKU-1"].expected_arrival == date(2026, 8, 17)   # +7
    assert by_sku["SKU-2"].expected_arrival == date(2026, 8, 31)   # +21
    assert by_sku["SKU-3"].expected_arrival == date(2026, 8, 13)   # +3


def test_a_sku_with_no_lead_time_falls_back_to_the_default():
    proposal = _proposal(lead_time_days={}, default_lead_time_days=5.0)

    assert all(ln.expected_arrival == date(2026, 8, 15) for ln in proposal.all_lines)


# -- la procedencia del lead time ---------------------------------------------


def test_la_linea_con_lead_time_mapeado_queda_declarada_como_leida():
    linea = {ln.sku: ln for ln in _proposal().all_lines}["SKU-1"]

    assert linea.lead_time_source == LEAD_TIME_STATED
    assert linea.arrives_late is True and linea.days_late == 3


def test_una_linea_sin_lead_time_mapeado_dice_que_lo_asumio():
    """El default es un supuesto aunque el que llama lo haya elegido, igual que
    ``resolve_payment_terms`` marca TERMS_ASSUMED con un default explicito."""
    proposal = _proposal(
        needs=[ReplenishmentNeed("SKU-9", 10.0, reason="rojo", days_to_stockout=1.0)],
        supplier_by_sku={"SKU-9": "ACME"},
        lead_time_days={},
        default_lead_time_days=5.0,
    )

    assert proposal.all_lines[0].lead_time_source == LEAD_TIME_ASSUMED


def test_sin_lead_time_la_linea_no_afirma_que_llega_a_tiempo():
    """El bug: con el default 0.0 la llegada caia el mismo dia de la orden, asi que
    ``arrives_late`` salia False -- 'llega a tiempo' afirmado sobre un dato que nadie
    dio. Sin lead time no hay veredicto, ni a favor ni en contra."""
    proposal = _proposal(
        needs=[ReplenishmentNeed("SKU-9", 10.0, reason="rojo", days_to_stockout=1.0)],
        supplier_by_sku={"SKU-9": "ACME"},
        lead_time_days={},
    )
    linea = proposal.all_lines[0]

    assert linea.arrives_late is None
    assert linea.days_late is None


def test_la_linea_sin_veredicto_no_entra_en_late_lines_pero_sale_nombrada():
    """No es tarde (no se sabe), y por eso mismo no puede desaparecer: sale en
    ``lead_time_assumed_for`` como el residual que un comprador tiene que cerrar."""
    proposal = _proposal(
        needs=[
            ReplenishmentNeed("SKU-1", 120.0, reason="bajo ROP", days_to_stockout=4.0),
            ReplenishmentNeed("SKU-9", 10.0, reason="rojo", days_to_stockout=1.0),
        ],
        supplier_by_sku={"SKU-1": "ACME", "SKU-9": "ACME"},
        lead_time_days={"SKU-1": 7.0},
    )

    assert [ln.sku for ln in proposal.late_lines] == ["SKU-1"]
    assert proposal.lead_time_assumed_for == ("SKU-9",)
    assert proposal.orders[0].late_lines == (proposal.late_lines[0],)


def test_con_todos_los_lead_times_mapeados_no_hay_residual():
    assert _proposal().lead_time_assumed_for == ()


def test_a_line_that_arrives_after_it_is_needed_is_flagged():
    proposal = _proposal()

    by_sku = {ln.sku: ln for ln in proposal.all_lines}
    assert by_sku["SKU-1"].arrives_late is True and by_sku["SKU-1"].days_late == 3
    assert by_sku["SKU-2"].arrives_late is True     # needed +10, arrives +21
    assert by_sku["SKU-3"].arrives_late is True     # needed +1, arrives +3
    assert len(proposal.late_lines) == 3


def test_a_lead_time_inside_the_cover_window_is_not_late():
    proposal = _proposal(
        needs=[ReplenishmentNeed("SKU-1", 10.0, reason="red", days_to_stockout=30.0)],
    )

    line = proposal.all_lines[0]
    assert line.required_date == date(2026, 9, 9)
    assert line.arrives_late is False and line.days_late == 0
    assert proposal.late_lines == ()


# -- the residual -------------------------------------------------------------


def test_a_sku_with_no_supplier_comes_back_unsourced_and_is_not_dropped():
    proposal = _proposal(supplier_by_sku={"SKU-1": "ACME"})

    assert [o.supplier for o in proposal.orders] == ["ACME"]
    assert [ln.sku for ln in proposal.unsourced] == ["SKU-2", "SKU-3"]
    assert all(ln.supplier == UNSOURCED for ln in proposal.unsourced)
    assert all(ln.sourced is False for ln in proposal.unsourced)
    assert proposal.n_lines == 3            # nothing was lost
    assert proposal.total_qty == pytest.approx(215.0)


def test_a_blank_supplier_label_is_the_same_as_none():
    proposal = _proposal(supplier_by_sku={"SKU-1": "   ", "SKU-2": "BETA", "SKU-3": ""})

    assert [o.supplier for o in proposal.orders] == ["BETA"]
    assert [ln.sku for ln in proposal.unsourced] == ["SKU-1", "SKU-3"]


def test_an_integer_supplier_id_is_accepted_as_the_group_key():
    """``OdooConnector.primary_supplier_by_sku()`` returns partner ids, not names."""
    proposal = _proposal(supplier_by_sku={"SKU-1": 42, "SKU-2": 42, "SKU-3": 7})

    assert [o.supplier for o in proposal.orders] == ["42", "7"]
    assert [ln.sku for ln in proposal.orders[0].lines] == ["SKU-1", "SKU-2"]


# -- the write-side adapter ---------------------------------------------------


def test_restock_by_sku_rebuilds_the_dict_stage_draft_purchase_orders_takes():
    proposal = _proposal(supplier_by_sku={"SKU-1": "ACME"})

    assert proposal.restock_by_sku() == {"SKU-1": 120.0, "SKU-2": 80.0, "SKU-3": 15.0}


def test_restock_by_sku_sums_two_lines_of_the_same_sku():
    proposal = _proposal(needs=[
        ReplenishmentNeed("SKU-1", 100.0, reason="red"),
        ReplenishmentNeed("SKU-1", 20.0, reason="red"),
    ])

    assert proposal.restock_by_sku() == {"SKU-1": 120.0}


# -- edges + determinism ------------------------------------------------------


def test_a_zero_quantity_need_is_not_a_po_line():
    proposal = _proposal(needs=[
        ReplenishmentNeed("SKU-1", 0.0, reason="green"),
        ReplenishmentNeed("SKU-2", -5.0, reason="over green"),
        ReplenishmentNeed("SKU-3", 15.0, reason="red"),
    ])

    assert proposal.n_lines == 1
    assert proposal.all_lines[0].sku == "SKU-3"


def test_no_needs_produces_an_empty_proposal():
    proposal = _proposal(needs=[])

    assert proposal.orders == () and proposal.unsourced == ()
    assert proposal.n_lines == 0 and proposal.total_qty == 0.0
    assert proposal.restock_by_sku() == {}


def test_the_shared_empty_proposal_is_a_usable_default():
    assert EMPTY_PROPOSAL.n_orders == 0
    assert EMPTY_PROPOSAL.all_lines == ()
    assert EMPTY_PROPOSAL.restock_by_sku() == {}


def test_two_identical_calls_produce_the_same_proposal():
    assert _proposal() == _proposal()


def test_the_order_is_deterministic_regardless_of_input_order():
    forward = _proposal()
    backward = _proposal(needs=list(reversed(_needs())))

    assert forward == backward


def test_lines_are_frozen():
    line = _proposal().orders[0].lines[0]
    with pytest.raises(Exception):
        line.supplier = "OTHER"     # frozen dataclass: no mutation


# -- export rows --------------------------------------------------------------


def test_proposal_rows_are_flat_and_iso_dated():
    rows = proposal_rows(_proposal(supplier_by_sku={"SKU-1": "ACME"}))

    assert [r["supplier"] for r in rows] == ["ACME", UNSOURCED, UNSOURCED]
    first = rows[0]
    assert first["order_date"] == "2026-08-10"
    assert first["required_date"] == "2026-08-14"
    assert first["expected_arrival"] == "2026-08-17"
    assert first["reason"] == "below ROP"
    assert first["lead_time_source"] == LEAD_TIME_STATED
    assert set(first) == {
        "supplier", "sku", "order_qty", "order_date", "required_date",
        "expected_arrival", "arrives_late", "days_late", "lead_time_source", "reason",
    }


def test_la_fila_exportada_deja_vacio_el_veredicto_que_nadie_puede_dar():
    """La convencion del repo en el CSV: None y no False, con la columna de
    procedencia al lado explicando por que la celda esta vacia."""
    rows = proposal_rows(_proposal(
        needs=[ReplenishmentNeed("SKU-9", 10.0, reason="rojo", days_to_stockout=1.0)],
        supplier_by_sku={"SKU-9": "ACME"},
        lead_time_days={},
    ))

    assert rows[0]["arrives_late"] is None
    assert rows[0]["days_late"] is None
    assert rows[0]["lead_time_source"] == LEAD_TIME_ASSUMED


def test_proposal_rows_of_an_empty_proposal_is_empty():
    assert proposal_rows(EMPTY_PROPOSAL) == []


def test_a_hand_built_line_needs_no_engine():
    """The dataclass is engine-neutral: ROP or manual lists build the same line."""
    line = ProposedLine(
        supplier="ACME", sku="SKU-9", order_qty=5.0,
        order_date=_TODAY, required_date=date(2026, 8, 20),
        expected_arrival=date(2026, 8, 15), reason="manual",
    )

    assert line.sourced is True and line.arrives_late is False
