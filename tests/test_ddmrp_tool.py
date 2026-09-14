"""Tests for the DDMRP agent job + tool (8th tool).

Reads a parts/buffer CSV into the DDMRP inputs (pandas directly, not the parallel loop's
intake.py), sizes the red/yellow/green buffers + net-flow planning signals, and the tool
wires it into the orchestrator so "size our DDMRP buffers" produces the buffer plan deck.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from jobs import ddmrp_job as ddj
from nosilo_core import llm
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.purchase_proposal import UNSOURCED

_TODAY = date(2026, 8, 10)


def _parts_df() -> pd.DataFrame:
    return pd.DataFrame({
        "part_id": ["P1", "P2"],
        "adu": [10.0, 5.0],
        "dlt": [5.0, 10.0],
        "ltf": [0.5, 0.5],
        "vf": [0.4, 0.3],
        "on_hand": [20.0, 200.0],
        "on_order": [0.0, 0.0],
        "qualified_demand": [30.0, 10.0],
    })


def _sourced_df() -> pd.DataFrame:
    """Three parts in the red across two suppliers, so the grouping has something to do."""
    return pd.DataFrame({
        "part_id": ["P1", "P2", "P3"],
        "adu": [10.0, 10.0, 10.0],
        "dlt": [5.0, 20.0, 5.0],
        "on_hand": [20.0, 20.0, 20.0],
        "on_order": [0.0, 0.0, 0.0],
        "qualified_demand": [5.0, 5.0, 5.0],
        "supplier": ["ACME", "BETA", "ACME"],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_the_parts_into_buffer_inputs(tmp_path):
    csv = tmp_path / "parts.csv"
    _parts_df().to_csv(csv, index=False)

    records = ddj.prepare(str(csv), {})

    by = {r["part_id"]: r for r in records}
    assert by["P1"]["adu"] == 10.0 and by["P1"]["dlt"] == 5.0
    assert by["P1"]["qualified_demand"] == 30.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="part_id|adu|dlt"):
        ddj.prepare(str(csv), {})


def test_prepare_carries_the_supplier_through_to_the_record():
    records = ddj.prepare_records(_sourced_df())

    assert {r["part_id"]: r["supplier"] for r in records} == {
        "P1": "ACME", "P2": "BETA", "P3": "ACME",
    }


def test_prepare_leaves_the_supplier_blank_when_the_column_is_absent():
    """A parts table without a supplier column is a normal input, not an error."""
    assert all(r["supplier"] == "" for r in ddj.prepare_records(_parts_df()))


def test_prepare_reads_the_supplier_under_an_alias_or_an_override():
    df = _sourced_df().rename(columns={"supplier": "vendor"})
    assert ddj.prepare_records(df)[0]["supplier"] == "ACME"

    df = _sourced_df().rename(columns={"supplier": "who_makes_it"})
    assert ddj.prepare_records(df, {"supplier_col": "who_makes_it"})[0]["supplier"] == "ACME"


def test_prepare_treats_a_missing_supplier_cell_as_blank():
    df = _sourced_df()
    df.loc[1, "supplier"] = None

    by = {r["part_id"]: r["supplier"] for r in ddj.prepare_records(df)}
    assert by["P2"] == "" and by["P1"] == "ACME"


# -- run + qa -----------------------------------------------------------------


def test_run_sizes_buffers_and_flags_the_red_part():
    records = ddj.prepare_records(_parts_df())

    report = ddj.run(records)

    by = {p.part_id: p for p in report.parts}
    # P1: yellow 50, red 35, green 25 -> TOG 110; NFP 20-30 = -10 -> red, order to TOG.
    assert by["P1"].zones.tog == pytest.approx(110.0)
    assert by["P1"].signal.zone == "red"
    assert by["P1"].signal.order_recommended is True
    assert by["P1"].signal.order_qty == pytest.approx(120.0)
    # P2 is over green -> no order.
    assert by["P2"].signal.zone == "over_green"
    assert report.n_red == 1 and report.n_order == 1
    assert report.total_order_qty == pytest.approx(120.0)
    assert ddj.verify(report) == []


def test_parts_are_sorted_most_urgent_first():
    report = ddj.run(ddj.prepare_records(_parts_df()))
    assert report.parts[0].part_id == "P1"          # lowest priority ratio = most urgent


# -- the draft purchase orders ------------------------------------------------


def test_run_groups_the_buy_into_one_draft_po_per_supplier():
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    assert [o.supplier for o in report.proposal.orders] == ["ACME", "BETA"]
    acme, beta = report.proposal.orders
    assert [ln.sku for ln in acme.lines] == ["P1", "P3"]
    assert acme.total_qty == pytest.approx(195.0)   # 97.5 + 97.5
    assert [ln.sku for ln in beta.lines] == ["P2"]
    assert beta.total_qty == pytest.approx(435.0)
    assert report.proposal.unsourced == ()


def test_each_part_carries_its_own_po_line_with_supplier_dates_and_reason():
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    line = {p.part_id: p.po_line for p in report.parts}["P1"]
    assert line.supplier == "ACME" and line.sku == "P1"
    assert line.order_qty == pytest.approx(97.5)
    assert line.order_date == _TODAY
    assert line.required_date == date(2026, 8, 12)      # 1.5 days of cover left
    assert line.expected_arrival == date(2026, 8, 15)   # 5-day decoupled lead time
    assert "red zone" in line.reason and "TOG" in line.reason


def test_the_decoupled_lead_time_drives_the_expected_arrival_per_supplier():
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    by = {p.part_id: p.po_line for p in report.parts}
    assert by["P1"].expected_arrival == date(2026, 8, 15)   # dlt 5
    assert by["P2"].expected_arrival == date(2026, 8, 30)   # dlt 20


def test_a_part_above_top_of_green_gets_no_po_line():
    report = ddj.run(ddj.prepare_records(_parts_df()), today=_TODAY)

    by = {p.part_id: p for p in report.parts}
    assert by["P2"].signal.zone == "over_green"
    assert by["P2"].po_line is None
    assert by["P1"].po_line is not None
    assert report.proposal.n_lines == 1


def test_without_a_supplier_column_the_lines_come_back_unsourced_and_qa_still_passes():
    report = ddj.run(ddj.prepare_records(_parts_df()), today=_TODAY)

    assert report.proposal.orders == ()
    assert [ln.sku for ln in report.proposal.unsourced] == ["P1"]
    assert report.proposal.unsourced[0].supplier == UNSOURCED
    assert ddj.verify(report) == []          # a residual is not a QA failure


def test_the_proposal_reconstructs_the_restock_the_write_side_takes():
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    assert report.proposal.restock_by_sku() == pytest.approx(
        {"P1": 97.5, "P2": 435.0, "P3": 97.5}
    )
    assert sum(report.proposal.restock_by_sku().values()) == pytest.approx(
        report.total_order_qty
    )


def test_the_run_is_reproducible_for_a_given_today():
    records = ddj.prepare_records(_sourced_df())

    assert ddj.run(records, today=_TODAY) == ddj.run(records, today=_TODAY)


def test_run_without_today_still_dates_the_lines():
    report = ddj.run(ddj.prepare_records(_sourced_df()))

    assert all(p.po_line.order_date == date.today() for p in report.parts)


# -- el lead time imposible: se rechaza en el borde ---------------------------
#
# Aca vivia un chequeo de fechas dentro de ``verify()`` -- "expected_arrival <
# order_date" -- y era codigo muerto: ``purchase_proposal._days`` es
# ``max(0, ...)``, asi que una llegada nunca es anterior a su fecha de orden. El
# test que lo cubria armaba a mano el estado imposible con ``dataclasses.replace``
# y despues comprobaba que ``verify()`` lo marcara: probaba que un ``if`` funciona,
# no que el sistema pueda llegar ahi. Se borro con lo que cubria.
#
# Lo que ese chequeo perseguia -- un lead time que no es un lead time -- si existe,
# y se agarra ahora donde el valor entra. Estos tests fijan las dos mitades: que el
# gate viejo era inalcanzable, y que el agujero real esta cerrado.


def test_una_llegada_nunca_cae_antes_de_su_fecha_de_orden():
    """La prueba de que el gate viejo era codigo muerto, no una opinion sobre el."""
    from src.purchase_proposal import ReplenishmentNeed, build_proposal

    propuesta = build_proposal(
        [ReplenishmentNeed(sku="X", order_qty=10.0, days_to_stockout=1.0)],
        supplier_by_sku={"X": "ACME"},
        lead_time_days={"X": -99.0},
        today=_TODAY,
    )
    linea = propuesta.all_lines[0]

    # -99 dias no retrocede el calendario: se recorta a 0 y la llegada cae EL MISMO
    # dia de la orden. Nunca antes. Por eso aquel ``if`` no era alcanzable -- y por
    # eso tampoco servia para detectar el lead time negativo que lo origino.
    assert linea.expected_arrival == linea.order_date
    assert not linea.expected_arrival < linea.order_date


def test_un_dlt_negativo_se_rechaza_al_entrar_y_nombra_la_parte():
    """El agujero verdadero: el -99 llega a ``build_proposal`` como el lead time del
    proveedor, el recorte lo convierte en "llega el dia que lo pedis", y eso sale
    impreso como si fuera un compromiso. Se rechaza donde el valor entra."""
    df = _sourced_df()
    df.loc[1, "dlt"] = -99.0

    with pytest.raises(ValueError) as err:
        ddj.run(ddj.prepare_records(df), today=_TODAY)

    mensaje = str(err.value)
    assert "P2" in mensaje                       # la fila, nombrada
    assert "-99" in mensaje                      # y el valor que hay que corregir
    assert "lead time" in mensaje


def test_el_caso_que_pasaba_limpio_por_qa_tampoco_entra_mas():
    """Con ADU 0 y un MOQ las zonas colapsan a -0.0 / -0.0 / MOQ, y en Python
    ``-0.0 >= 0`` es True: el chequeo de zonas de ``verify()`` daba el visto bueno
    sobre una linea de OC con la fecha de llegada fabricada por el recorte. Ese es
    el escenario que ningun gate posterior podia ver."""
    assert -0.0 >= 0                             # la razon por la que verify() no lo veia

    df = pd.DataFrame({
        "part_id": ["X"], "adu": [0.0], "dlt": [-99.0], "on_hand": [0.0],
        "on_order": [0.0], "qualified_demand": [0.0], "moq": [50.0], "supplier": ["ACME"],
    })

    with pytest.raises(ValueError, match="lead time"):
        ddj.run(ddj.prepare_records(df), today=_TODAY)


def test_un_adu_negativo_se_rechaza_igual():
    """Unidades por dia y cantidad de dias son la misma imposibilidad; chequear una
    y no la otra deja el gemelo del bug al lado del bug."""
    df = _sourced_df()
    df.loc[0, "adu"] = -3.0

    with pytest.raises(ValueError, match="average daily usage"):
        ddj.run(ddj.prepare_records(df), today=_TODAY)


def test_un_dlt_de_cero_sigue_siendo_valido():
    """El control del borde: cero no es negativo. Una parte de entrega inmediata es
    un dato legitimo, y una llegada el mismo dia que la orden es su fecha correcta
    -- no la fabricada por el recorte de un numero imposible."""
    df = pd.DataFrame({
        "part_id": ["X"], "adu": [10.0], "dlt": [0.0], "on_hand": [0.0],
        "on_order": [0.0], "qualified_demand": [5.0], "supplier": ["ACME"],
    })

    reporte = ddj.run(ddj.prepare_records(df), today=_TODAY)

    linea = reporte.parts[0].po_line
    assert linea is not None
    assert linea.expected_arrival == _TODAY
    assert ddj.verify(reporte) == []


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable():
    report = ddj.run(ddj.prepare_records(_parts_df()))

    deck = ddj.build_deck(report, client="Acme", citations=("Ptak & Smith - DDMRP",), confidence=0.85)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "DDMRP" in md or "buffer" in md.lower()
    assert "## Coverage & handoff" in md


def test_the_deck_names_the_draft_pos_and_their_suppliers():
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    md = ddj.build_deck(report, client="Acme").to_markdown()

    assert md.isascii()
    assert "draft purchase order" in md.lower()
    assert "ACME" in md and "BETA" in md


def test_the_deck_says_how_many_lines_have_no_supplier():
    report = ddj.run(ddj.prepare_records(_parts_df()), today=_TODAY)

    md = ddj.build_deck(report, client="Acme").to_markdown()

    assert md.isascii()
    assert "unsourced" in md.lower()


# -- operational files --------------------------------------------------------


def test_write_operational_emits_the_buffers_and_the_purchase_orders(tmp_path):
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    out = ddj.write_operational(report, tmp_path, client="Acme")

    assert set(out) == {"csv", "purchase_orders_csv"}
    pos = pd.read_csv(out["purchase_orders_csv"])
    assert list(pos["supplier"]) == ["ACME", "ACME", "BETA"]
    assert list(pos["sku"]) == ["P1", "P3", "P2"]
    assert set(pos["order_date"]) == {"2026-08-10"}
    assert list(pos["expected_arrival"]) == ["2026-08-15", "2026-08-15", "2026-08-30"]
    assert all(str(r).strip() for r in pos["reason"])


def test_the_buffer_file_keeps_its_columns_and_gains_the_po_ones(tmp_path):
    report = ddj.run(ddj.prepare_records(_sourced_df()), today=_TODAY)

    buffers = pd.read_csv(ddj.write_operational(report, tmp_path)["csv"])

    assert {"part_id", "tor", "toy", "tog", "nfp", "zone", "order_qty", "priority"} <= set(buffers.columns)
    assert {"supplier", "order_date", "required_date", "expected_arrival", "order_reason"} <= set(buffers.columns)
    assert set(buffers["supplier"]) == {"ACME", "BETA"}


def test_a_part_with_no_order_leaves_the_po_columns_blank(tmp_path):
    report = ddj.run(ddj.prepare_records(_parts_df()), today=_TODAY)

    buffers = pd.read_csv(ddj.write_operational(report, tmp_path)["csv"])

    over_green = buffers[buffers["zone"] == "over_green"].iloc[0]
    assert pd.isna(over_green["order_date"]) or over_green["order_date"] == ""


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_ddmrp():
    reg = tools.build_default_registry()
    res = intent.classify("size our DDMRP buffers and net flow plan", reg, llm.RulesFallback())
    assert res.job_type == "ddmrp"


def test_ddmrp_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("build the monthly S&OP plan", reg, p).job_type == "sop"
    assert intent.classify("select the best supplier by OTIF", reg, p).job_type == "sourcing"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_ddmrp_and_emits_the_deck(tmp_path):
    csv = tmp_path / "parts.csv"
    _parts_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("size the DDMRP buffer zones and net flow", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "ddmrp"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
