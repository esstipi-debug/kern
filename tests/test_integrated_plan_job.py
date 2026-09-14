"""Tests for jobs/integrated_plan.py (Linchpin 3.0 PR-20, A5 v1).

End-to-end: real CSVs -> forecast -> P2 demand shaping / P4 liquidation
demand shaping -> purchase plan -> >=3 coherence checks -> QA -> ONE
deliverable. Numbers below were computed by actually running this module
(see the module's own docstring for the formulas) and are asserted with
``pytest.approx`` where floating-point OLS/elasticity math is involved.
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from jobs import integrated_plan as ip
from jobs.qa import integrated_plan_passed, verify_integrated_plan
from scm_agent.citation_gate import MIN_CITATIONS
from scm_agent.knowledge import KnowledgeBase
from src.deliverable import Deliverable
from src.sop_engine.cash_plan import parse_payment_terms
from src.sop_engine.coherence import CHECK_BUDGET_FEASIBILITY, CHECK_PROMO_COVERAGE, CHECK_SERVICE_LEVEL
from src.sop_engine.demand_plan import LIQUIDATION_SOURCE, NO_SHIFT_SOURCE, PRICE_OPTIMIZER_SOURCE


def _write_demand_csv(path, series: dict[str, list[float]]) -> str:
    rows = [
        {"sku": sku, "period": t, "demand": q}
        for sku, vals in series.items()
        for t, q in enumerate(vals)
    ]
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_stock_csv(path, rows: list[dict]) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_price_csv(path, rows: list[dict]) -> str:
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


# ---- scenario A: a P2 price-cut promo with NO covering purchase order ----
# (the plan's literal coherence-check example, sourced from the price
# optimizer rather than the liquidation report -- see
# tests/test_sop_engine_pipeline.py for the same example sourced from a
# LiquidationLine directly at the pure-function level.)

def _promo_scenario(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {
        "SKU-A": [10.0] * 12,
        "SKU-PROMO": [20.0] * 12,
    })
    # price 4 -> 2 (halved), quantity 10 -> 40 (quadrupled): log-log OLS
    # elasticity = ln(40/10) / ln(2/4) = ln(4) / ln(0.5) = -2.0 exactly.
    price_csv = _write_price_csv(tmp_path / "prices.csv", [
        {"date": "2026-01-05", "product_id": "SKU-PROMO", "price": 4.0, "quantity": 10},
        {"date": "2026-01-12", "product_id": "SKU-PROMO", "price": 4.0, "quantity": 10},
        {"date": "2026-01-19", "product_id": "SKU-PROMO", "price": 2.0, "quantity": 40},
        {"date": "2026-01-26", "product_id": "SKU-PROMO", "price": 2.0, "quantity": 40},
    ])
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-A", "on_hand": 200, "unit_cost": 5.0, "reorder_point": 20.0, "incoming_po": 0.0},
        {"product_id": "SKU-PROMO", "on_hand": 5.0, "unit_cost": 1.0, "reorder_point": 2.0, "incoming_po": 0.0},
    ])
    params = {"stock_path": stock_csv, "price_history_path": price_csv}
    payload = ip.prepare(demand_csv, params)
    bundle = ip.run(payload)
    return bundle


def test_prepare_requires_stock_path(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"A": [1.0] * 6})
    with pytest.raises(ValueError, match="stock_path"):
        ip.prepare(demand_csv, {})


def test_promo_scenario_end_to_end(tmp_path):
    bundle = _promo_scenario(tmp_path)

    by_id = {line.product_id: line for line in bundle.plan.demand_plan}
    assert by_id["SKU-A"].source == NO_SHIFT_SOURCE
    assert by_id["SKU-A"].shaped_demand == pytest.approx(10.0)

    promo = by_id["SKU-PROMO"]
    assert promo.source == PRICE_OPTIMIZER_SOURCE
    # elasticity -2.0, current price 3.0 (median of 4,4,2,2) -> unconstrained
    # p* = c*e/(e+1) = 2.0 (c=1.0), a 33% cut -- the optimizer's default
    # +/-20% per-step move band clamps it to 3.0 * 0.8 = 2.4 (the observed
    # range band [2/1.3, 4*1.3] does not bind):
    # ratio = (2.4/3)**-2 - 1 = 0.5625 -> +56.25% -> shaped = 20 * 1.5625 = 31.25
    assert promo.demand_shift_pct == pytest.approx(56.25, abs=0.01)
    assert promo.shaped_demand == pytest.approx(31.25, abs=0.01)

    purchase_by_id = {line.product_id: line for line in bundle.plan.purchase_plan}
    promo_purchase = purchase_by_id["SKU-PROMO"]
    assert promo_purchase.recommended_order == pytest.approx(26.25, abs=0.01)  # 31.25 - 5 - 0

    checks_by_kind = {}
    for c in bundle.plan.checks:
        checks_by_kind.setdefault(c.check, []).append(c)
    assert set(checks_by_kind) == {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}

    promo_coverage_results = checks_by_kind[CHECK_PROMO_COVERAGE]
    failed_promo = next(c for c in promo_coverage_results if c.product_id == "SKU-PROMO")
    assert failed_promo.passed is False
    assert "SKU-PROMO" in failed_promo.message
    assert "31.2" in failed_promo.message  # shaped demand (31.25 at :.1f), citable

    service_results = checks_by_kind[CHECK_SERVICE_LEVEL]
    assert any(c.product_id == "SKU-A" and c.passed for c in service_results)
    assert any(c.product_id == "SKU-PROMO" and not c.passed for c in service_results)

    assert bundle.plan.n_checks_failed >= 1
    assert "FAILED" in bundle.plan.summary


def test_promo_scenario_qa_passes_despite_failed_coherence_checks(tmp_path):
    """A FAILED coherence check is a reported FINDING, not a QA blocker --
    the deliverable still ships (mirrors every other job's QA gate: it
    checks structural soundness, not that the business outcome is good)."""
    bundle = _promo_scenario(tmp_path)
    assert any(not c.passed for c in bundle.plan.checks)
    assert verify_integrated_plan(bundle) == []
    assert integrated_plan_passed(bundle) is True


def test_promo_scenario_write_deliverable_produces_one_integrated_report(tmp_path):
    bundle = _promo_scenario(tmp_path)
    out_dir = tmp_path / "out"
    written = ip.write_deliverable(bundle, out_dir=out_dir, client="Acme Co", brief="integrated plan")

    assert set(written) == {
        "demand_plan", "purchase_plan", "coherence_checks", "cash_commitment", "report", "workbook",
    }
    for path in written.values():
        assert path.exists()

    report_text = written["report"].read_text(encoding="utf-8")
    assert "Acme Co" in report_text
    assert "SKU-PROMO" in report_text
    # ONE integrated narrative, not four stapled reports: the single deck
    # mentions both the forecast step and the failed coherence check.
    assert "coherence check" in report_text.lower() or "Coherence check" in report_text


def test_write_operational_produces_one_named_csv_per_step(tmp_path):
    bundle = _promo_scenario(tmp_path)
    out_dir = tmp_path / "csv_out"
    written = ip.write_operational(bundle, out_dir, client="Acme")
    assert set(written) == {"demand_plan", "purchase_plan", "coherence_checks", "cash_commitment"}
    demand_df = pd.read_csv(written["demand_plan"])
    assert set(demand_df["product_id"]) == {"SKU-A", "SKU-PROMO"}
    checks_df = pd.read_csv(written["coherence_checks"])
    assert set(checks_df["check"]) == {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}


def test_the_purchase_csv_dates_every_line_and_says_where_the_term_came_from(tmp_path):
    """El renglon de compra ya no es un monto suelto: dice con que plazo se planto
    y si ese plazo se leyo o se asumio. El CSV de muestra no tiene columna de
    condiciones de pago, asi que el camino honesto es 'assumed_default'."""
    bundle = _promo_scenario(tmp_path)
    written = ip.write_operational(bundle, tmp_path / "csv_terms", client="Acme")
    purchase_df = pd.read_csv(written["purchase_plan"])
    for column in ("payment_terms", "payment_terms_source", "payment_due_days", "payment_due_week"):
        assert column in purchase_df.columns
    assert set(purchase_df["payment_terms"]) == {"net_30"}
    assert set(purchase_df["payment_terms_source"]) == {"assumed_default"}
    assert set(purchase_df["payment_due_week"]) == {4}


def test_the_cash_csv_lays_the_purchase_money_out_week_by_week(tmp_path):
    bundle = _promo_scenario(tmp_path)
    written = ip.write_operational(bundle, tmp_path / "csv_cash", client="Acme")
    cash_df = pd.read_csv(written["cash_commitment"])
    assert list(cash_df["week"]) == list(range(5))  # contiguo desde la semana 0
    # Sin plazos declarados todo vence en la semana 4, y ese total es el mismo
    # order_value que el plan de compra recomendo.
    purchase_df = pd.read_csv(written["purchase_plan"])
    assert cash_df["disbursement"].iloc[4] == pytest.approx(purchase_df["order_value"].sum(), abs=0.01)
    assert cash_df["disbursement"].iloc[:4].sum() == 0.0
    assert cash_df["cumulative"].iloc[4] == pytest.approx(cash_df["disbursement"].sum(), abs=0.01)


def test_a_stated_payment_term_is_read_from_the_stock_file_and_dates_its_line(tmp_path):
    """El plazo es un input externo como incoming_po: cuando el archivo lo trae,
    la linea sale marcada como leida y su plata se mueve de semana."""
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-A": [10.0] * 12})
    stock_csv = tmp_path / "stock.csv"
    pd.DataFrame([
        {"product_id": "SKU-A", "on_hand": 0.0, "unit_cost": 3.0, "payment_terms": "net 60"},
    ]).to_csv(stock_csv, index=False)

    payload = ip.prepare(str(demand_csv), {"stock_path": str(stock_csv)})
    bundle = ip.run(payload)
    (line,) = bundle.plan.purchase_plan
    assert (line.payment_terms, line.payment_terms_source) == ("net_60", "stated")
    assert line.payment_due_week == 8
    assert bundle.plan.cash_schedule.terms_assumed_for == ()
    assert bundle.plan.cash_schedule.weeks[-1].week == 8


def test_una_columna_de_plazos_numericos_con_una_celda_vacia_no_mata_el_job(tmp_path):
    """Reproduccion del hallazgo (a): un CSV de cliente perfectamente razonable.

    payment_terms con los dias como numeros y UNA sola celda vacia hace que
    pandas lea la columna como float64, asi que cada valor llega a _text_cell
    como 30.0 y salia str() como '30.0'. parse_payment_terms lo rechazaba
    (no pasa .isdigit()) y SkuPurchaseInputs levantaba ValueError: prepare()
    entero moria por una celda en blanco en una columna opcional.
    """
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {
        "A": [10.0] * 12, "B": [10.0] * 12, "C": [10.0] * 12,
    })
    stock_csv = tmp_path / "stock.csv"
    stock_csv.write_text(
        "product_id,on_hand,unit_cost,payment_terms\n"
        "A,10,5.0,30\n"
        "B,20,6.0,\n"
        "C,5,7.0,60\n",
        encoding="ascii",
    )
    assert pd.read_csv(stock_csv)["payment_terms"].dtype == "float64"  # la premisa del bug

    payload = ip.prepare(str(demand_csv), {"stock_path": str(stock_csv)})
    assert payload["sku_inputs"]["A"].payment_terms == "30"
    assert payload["sku_inputs"]["B"].payment_terms == ""   # la celda vacia sigue siendo "nadie lo dijo"
    assert payload["sku_inputs"]["C"].payment_terms == "60"

    lines = {line.product_id: line for line in ip.run(payload).plan.purchase_plan}
    assert (lines["A"].payment_terms, lines["A"].payment_terms_source) == ("net_30", "stated")
    assert (lines["B"].payment_terms, lines["B"].payment_terms_source) == ("net_30", "assumed_default")
    assert (lines["C"].payment_terms, lines["C"].payment_terms_source) == ("net_60", "stated")


def test_parse_payment_terms_acepta_el_entero_que_llega_con_coma_flotante(tmp_path):
    """La otra mitad del hallazgo (a): el numero con .0 puede entrar por
    cualquiera de los dos lados, asi que los dos lo aguantan. Media jornada
    ('30.5') sigue siendo ilegible: redondearla pondria una fecha que nadie
    pacto en el calendario."""
    assert parse_payment_terms("30.0") == ("net_30", 30)
    assert parse_payment_terms("net_30.0") == ("net_30", 30)
    assert parse_payment_terms("0.0") == ("cash", 0)
    with pytest.raises(ValueError, match="unreadable payment terms"):
        parse_payment_terms("30.5")


def test_un_as_of_con_hora_se_estrecha_a_fecha_en_el_calendario(tmp_path):
    """Reproduccion del hallazgo (b): datetime (y pd.Timestamp) son subclases
    de date, asi que pasaban enteros por _as_of; cash_plan._week_start los
    sumaba y .isoformat() escribia '2026-09-07T00:00:00' en week_start, con
    formato distinto al de la misma columna anclada con un date."""
    assert ip._as_of(datetime(2026, 8, 10, 14, 30)) == date(2026, 8, 10)
    assert type(ip._as_of(datetime(2026, 8, 10, 14, 30))) is date
    assert type(ip._as_of(pd.Timestamp("2026-08-10"))) is date

    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-A": [100.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-A", "on_hand": 0.0, "unit_cost": 10.0},
    ])
    payload = ip.prepare(str(demand_csv), {
        "stock_path": str(stock_csv), "as_of": datetime(2026, 8, 10, 14, 30),
    })
    schedule = ip.run(payload).plan.cash_schedule
    assert schedule.weeks[0].week_start == "2026-08-10"
    assert schedule.weeks[4].week_start == "2026-09-07"
    assert not any("T" in w.week_start for w in schedule.weeks)


def test_ninguna_funcion_del_job_pasa_el_techo_de_cincuenta_lineas():
    """Reproduccion del hallazgo (c): el archivo crecio 183 lineas en vez de
    extraer, y run()/_prepare_sku_inputs/_cash_findings/build_deck quedaron
    sobre el techo de 50 del repo. El eje de caja vive ahora en su modulo
    hermano; esto es lo que impide que vuelva a engordar en silencio."""
    source = Path(ip.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    too_long = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.end_lineno - node.lineno + 1 > 50
    }
    assert too_long == {}, f"funciones sobre 50 lineas: {too_long}"


def test_a_weekly_cash_cap_flags_the_week_that_does_not_fit(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-A": [100.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-A", "on_hand": 0.0, "unit_cost": 10.0},
    ])
    payload = ip.prepare(str(demand_csv), {
        "stock_path": str(stock_csv), "cash_cap_per_week": 50.0, "as_of": "2026-08-10",
    })
    schedule = ip.run(payload).plan.cash_schedule
    assert schedule.cap_per_week == 50.0
    assert schedule.n_weeks_over_cap == 1
    assert schedule.first_week_over_cap == 4
    assert schedule.weeks[0].week_start == "2026-08-10"
    assert schedule.weeks[4].week_start == "2026-09-07"


def test_the_deck_names_the_lines_whose_payment_term_it_assumed(tmp_path):
    """Un calendario que no puede nombrar sus supuestos se lee como medido."""
    bundle = _promo_scenario(tmp_path)
    deck = ip.build_deck(bundle, client="Acme")
    titles = [f.title for f in deck.findings]
    assert "Cash commitment by week (step 5)" in titles
    assert "Payment terms ASSUMED, not read" in titles
    assumed = next(f for f in deck.findings if f.title == "Payment terms ASSUMED, not read")
    assert "net_30" in assumed.detail
    # SKU-PROMO es el que pide: su semana de pago descansa sobre el supuesto y por
    # eso se nombra. SKU-A llega con 200 unidades en mano contra una demanda de 10,
    # asi que no ordena nada y NO aparece -- el aviso habla de las fechas de este
    # calendario, y una linea sin desembolso no pone ninguna.
    assert "SKU-PROMO" in assumed.detail
    assert "SKU-A" not in assumed.detail
    kpi_names = [k.name for k in deck.kpis]
    assert "Peak weekly cash commitment" in kpi_names
    assert "Lines on ASSUMED payment terms" in kpi_names


def test_build_deck_returns_a_deliverable(tmp_path):
    bundle = _promo_scenario(tmp_path)
    deck = ip.build_deck(bundle, client="Acme", citations=("cite one", "cite two"))
    assert isinstance(deck, Deliverable)
    assert deck.title.startswith("Integrated Plan")
    assert len(deck.findings) >= 3  # forecast + demand-shaping + at least one failed check


# ---- scenario B: P4 liquidation demand-shaping wiring (the SAME stock CSV
# doubles as the E&O input when it carries daily_demand) ----

def test_liquidation_demand_shaping_wires_through_when_stock_csv_has_daily_demand(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-CLR": [1.0] * 12})
    # flat price (no variation) -> P2 reports needs_data; P4's default-discount
    # heuristic still fires because the SKU is excess (on_hand >> target cover).
    price_csv = _write_price_csv(tmp_path / "prices.csv", [
        {"date": "2026-01-05", "product_id": "SKU-CLR", "price": 10.0, "quantity": 5},
        {"date": "2026-01-12", "product_id": "SKU-CLR", "price": 10.0, "quantity": 6},
        {"date": "2026-01-19", "product_id": "SKU-CLR", "price": 10.0, "quantity": 4},
        {"date": "2026-01-26", "product_id": "SKU-CLR", "price": 10.0, "quantity": 5},
    ])
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-CLR", "on_hand": 2000.0, "unit_cost": 5.0, "daily_demand": 1.0,
         "reorder_point": 5.0, "incoming_po": 0.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv, "price_history_path": price_csv})
    assert payload["liquidation_lines"]["SKU-CLR"].method == "default_discount"
    assert payload["price_shifts"]["SKU-CLR"].status == "needs_data"

    bundle = ip.run(payload)
    line = bundle.plan.demand_plan[0]
    assert line.source == LIQUIDATION_SOURCE
    # excess_units = on_hand - target_cover_days(90) * daily_demand = 2000 - 90 = 1910
    # shaped_demand = base_forecast(1.0) + units_to_clear(1910.0)
    assert line.shaped_demand == pytest.approx(1911.0)
    assert verify_integrated_plan(bundle) == []


def test_include_liquidation_false_skips_p4_even_with_a_qualifying_sku(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-CLR": [1.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "SKU-CLR", "on_hand": 2000.0, "unit_cost": 5.0, "daily_demand": 1.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv, "include_liquidation": False})
    assert payload["liquidation_lines"] == {}


# ---- budget scenario ----

def test_budget_exceeded_scenario_end_to_end(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"A": [200.0] * 6, "B": [200.0] * 6})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "A", "on_hand": 0.0, "unit_cost": 1.0, "reorder_point": 100.0},
        {"product_id": "B", "on_hand": 0.0, "unit_cost": 1.0, "reorder_point": 100.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv, "budget": 50.0})
    bundle = ip.run(payload)
    budget_result = next(c for c in bundle.plan.checks if c.check == CHECK_BUDGET_FEASIBILITY)
    assert budget_result.passed is False
    assert bundle.plan.allocation is not None
    assert bundle.plan.allocation.feasible is False
    assert verify_integrated_plan(bundle) == []  # a failed check is a finding, not a QA blocker


# ---- SKUs missing purchase data are excluded, never silently ----

def test_forecast_only_skus_are_dropped_with_a_named_reason(tmp_path):
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"A": [10.0] * 6, "ORPHAN": [5.0] * 6})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"product_id": "A", "on_hand": 50.0, "unit_cost": 2.0},
    ])
    payload = ip.prepare(demand_csv, {"stock_path": stock_csv})
    bundle = ip.run(payload)
    assert bundle.dropped_no_purchase_data == ("ORPHAN",)
    assert "ORPHAN" in bundle.summary
    assert bundle.plan.n_skus == 1


# ---- L3 citation grounding: S&OP method citations must be present, on-topic ----
# ---- and immune to client-brief lexical noise (3.0-audit finding #7) ----

# Real, plainly-S&OP briefs an operator would actually type. Every one of these
# degraded to ZERO L3 citations before the fix (6/8 in the reproduction battery):
# the S&OP concept cluster is fragmented across disconnected graph components, so
# the top-3 grounded candidates could all land in different islands and every one
# be dropped by the (unchanged, strict) 2-hop citation gate.
_REALISTIC_SOP_BRIEFS = (
    "Build an integrated business plan balancing our demand forecast against supply and purchasing.",
    "Reconcile our sales forecast with production and procurement capacity for the quarter.",
    "Run an integrated demand-supply plan: what to buy, make, and when, given our inventory.",
    "We want a monthly S&OP cycle aligning marketing demand plan with operations supply plan.",
    "Aggregate our SKU demand into a family-level supply and purchasing plan.",
    "Integrated business planning across demand, supply, inventory and purchasing for Q3.",
)

# Briefs whose incidental tokens used to drag off-topic citations past the
# permissive 2-hop gate when the free-text brief was fed into grounding: "budget
# cap" -> an emissions "cap-and-trade" citation; EOQ/reorder-point wording ->
# inventory citations displacing the real S&OP nodes. Grounding on the fixed
# keyword set (not the brief) must keep these deterministic and on-topic.
_NOISY_SOP_BRIEFS = (
    "S&OP: reconcile forecast demand against on-hand inventory, incoming POs and the budget cap.",
    "Coordinate the demand plan with economic order quantity, safety stock and reorder point.",
    "Reconcile demand and supply while accounting for cycle inventory and economic order quantity.",
)

# Strong, unambiguous S&OP terms only. Deliberately excludes bare "demand"/
# "supply", which match SCM book *filenames* (e.g. "…-supply-chain-management.pdf")
# and would let an off-topic citation from any supply-chain-titled book pass.
_STRONG_SOP_TERMS = ("s&op", "operations planning", "aggregate planning")

# Off-topic concepts that must never appear in an S&OP deck's citations (the
# exact precision regressions caught in adversarial review).
_OFF_TOPIC_TERMS = ("cap-and-trade", "emission", "economic order quantity", "reorder point")


@pytest.fixture(scope="module")
def _kb() -> KnowledgeBase:
    return KnowledgeBase()


@pytest.mark.parametrize("brief", _REALISTIC_SOP_BRIEFS)
def test_realistic_sop_brief_keeps_its_l3_citations(_kb, brief):
    """The recall regression: a plainly-S&OP brief must ground at least
    MIN_CITATIONS citations, never silently degrade to zero because the gate's
    candidate pool was too shallow to reach past the graph's S&OP islands."""
    cites = ip.gated_citations(brief, kb=_kb)
    assert len(cites) >= MIN_CITATIONS, f"{brief!r} degraded to {len(cites)} citation(s)"


def test_gated_citations_stay_on_topic_sop(_kb):
    """Precision: every kept citation is a genuinely S&OP / aggregate-planning
    source, matched on strong terms (not filename substrings)."""
    for brief in _REALISTIC_SOP_BRIEFS:
        for cite in ip.gated_citations(brief, kb=_kb):
            low = cite.lower()
            assert any(term in low for term in _STRONG_SOP_TERMS), f"off-topic citation kept: {cite!r}"


@pytest.mark.parametrize("brief", _NOISY_SOP_BRIEFS)
def test_brief_lexical_noise_never_surfaces_off_topic_citations(_kb, brief):
    """The precision regression caught in adversarial review: a brief mentioning
    "budget cap" or "economic order quantity" must NOT drag an emissions /
    inventory citation into the S&OP deck. Grounding on the fixed keyword set
    (not the brief) keeps citations on-topic regardless of client wording."""
    cites = ip.gated_citations(brief, kb=_kb)
    assert len(cites) >= MIN_CITATIONS
    for cite in cites:
        low = cite.lower()
        assert any(term in low for term in _STRONG_SOP_TERMS), f"off-topic citation kept: {cite!r}"
        assert not any(term in low for term in _OFF_TOPIC_TERMS), f"off-topic citation kept: {cite!r}"


def test_citations_are_brief_independent(_kb):
    """These citations ground the S&OP *method*, not the client's numbers, so
    they must be identical across every brief -- deterministic and auditable,
    never a function of incidental brief wording."""
    results = {ip.gated_citations(b, kb=_kb) for b in (*_REALISTIC_SOP_BRIEFS, *_NOISY_SOP_BRIEFS)}
    assert len(results) == 1, f"citations varied by brief: {results}"


def test_gated_citations_are_capped_to_a_tight_set(_kb):
    """The wider candidate pool feeds the gate but must not bloat the deck: the
    returned, displayed set stays bounded."""
    for brief in _REALISTIC_SOP_BRIEFS:
        assert len(ip.gated_citations(brief, kb=_kb)) <= ip._MAX_CITATIONS


# ---- el sniffer de columnas no puede matar la cartera por un nombre ----

def test_una_columna_terms_con_incoterms_no_mata_la_corrida(tmp_path):
    """Reproduccion del hallazgo ALTO: 'terms' es tan a menudo INCOTERMS como
    plazo de pago.

    _PAYMENT_TERMS_COLS incluia el alias generico 'terms'. Un stock.csv con una
    columna de Incoterms llamada 'terms' (FOB / CIF / DDP / EXW, comunisimo en
    exports de sistemas de compras) era sniffeada como condiciones de pago,
    SkuPurchaseInputs rechazaba 'FOB' como plazo ilegible y prepare() moria para
    la cartera ENTERA. En este mismo repo jobs/landed_cost_job.py ya lee esa
    misma columna como Incoterm (_INCOTERM_COLS), asi que el nombre estaba
    reclamado por dos conceptos distintos a la vez.
    """
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {
        "SKU-A": [10.0] * 12, "SKU-B": [10.0] * 12, "SKU-C": [10.0] * 12,
    })
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"sku": "SKU-A", "on_hand": 10.0, "unit_cost": 5.0, "terms": "FOB"},
        {"sku": "SKU-B", "on_hand": 20.0, "unit_cost": 6.0, "terms": "CIF"},
        {"sku": "SKU-C", "on_hand": 5.0, "unit_cost": 7.0, "terms": "DDP"},
    ])

    payload = ip.prepare(str(demand_csv), {"stock_path": str(stock_csv)})

    # Ningun Incoterm entra como plazo: la columna no es de plazos.
    assert {i.payment_terms for i in payload["sku_inputs"].values()} == {""}
    lines = {line.product_id: line for line in ip.run(payload).plan.purchase_plan}
    assert all(line.payment_terms_source == "assumed_default" for line in lines.values())


def test_una_columna_sniffeada_cuyo_contenido_no_son_plazos_se_trata_como_no_declarada(tmp_path):
    """La red de seguridad detras del alias: sacar 'terms' arregla el caso
    reportado, pero cualquier alias sniffeado sigue decidiendo un contrato duro
    sobre una coincidencia de NOMBRE. 'supplier_terms' con condiciones de
    entrega adentro no puede matar la cartera: nadie declaro esa columna, este
    modulo la ADIVINO, y una adivinanza no puede reventar la corrida."""
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-A": [10.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"sku": "SKU-A", "on_hand": 0.0, "unit_cost": 3.0, "supplier_terms": "L/C at sight"},
    ])

    payload = ip.prepare(str(demand_csv), {"stock_path": str(stock_csv)})
    assert payload["sku_inputs"]["SKU-A"].payment_terms == ""
    (line,) = ip.run(payload).plan.purchase_plan
    assert line.payment_terms_source == "assumed_default"
    # y el calendario NOMBRA el supuesto, que es lo que hace honesta la degradacion
    assert ip.run(payload).plan.cash_schedule.terms_assumed_for == ("SKU-A",)


def test_una_columna_sniffeada_con_plazos_de_verdad_se_sigue_leyendo(tmp_path):
    """La red de seguridad no puede comerse el caso bueno: un alias especifico
    con plazos legibles adentro se lee igual que siempre."""
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-A": [10.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"sku": "SKU-A", "on_hand": 0.0, "unit_cost": 3.0, "supplier_terms": "net 60"},
    ])

    payload = ip.prepare(str(demand_csv), {"stock_path": str(stock_csv)})
    (line,) = ip.run(payload).plan.purchase_plan
    assert (line.payment_terms, line.payment_terms_source) == ("net_60", "stated")


def test_una_columna_de_plazos_DECLARADA_por_el_caller_sigue_fallando_rapido(tmp_path):
    """El otro lado del contrato: pasar payment_terms_col es AFIRMAR que esa
    columna son plazos. Un valor ilegible ahi es un error de dato real y tiene
    que parar la corrida -- degradarlo silenciosamente convertiria una
    afirmacion del caller en un supuesto nuestro."""
    demand_csv = _write_demand_csv(tmp_path / "demand.csv", {"SKU-A": [10.0] * 12})
    stock_csv = _write_stock_csv(tmp_path / "stock.csv", [
        {"sku": "SKU-A", "on_hand": 0.0, "unit_cost": 3.0, "plazo": "FOB"},
    ])

    with pytest.raises(ValueError, match="unreadable payment terms"):
        ip.prepare(str(demand_csv), {"stock_path": str(stock_csv), "payment_terms_col": "plazo"})
