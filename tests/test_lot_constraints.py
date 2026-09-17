"""Terminos del proveedor: cuando Kern dice 40 y el proveedor vende de a 30, 60, 90.

Antes de esto el unico camino era ``apply_order_rules``: UNA terminal (redondear
hacia arriba) y ninguna explicacion. El plan decia 60, nadie sabia que 30 existia
ni que costaba cada opcion, y el consejo de sombreros podia acordar 40 -- una
cantidad que ningun proveedor iba a aceptar. Tres propiedades cierran ese punto
muerto y son lo que se fija aca:

1. El motor (``src/constraints.py``) elige entre los dos lotes legales vecinos por
   costo anual EOQ y devuelve el otro como alternativa con su penalidad: siempre
   dos terminales, nunca un numero redondeado a secas.
2. El job de inventario y su QA, CSV, deck y OPTIONS llevan esa decision por SKU;
   un ``(R,S)`` con terminos sale como residual, no en silencio.
3. Los sombreros discuten solo sobre lotes que el proveedor acepta, el baseline es
   el lote factible mas barato, y ``cross_impact`` nombra los vecinos legales de
   una propuesta infactible.
"""
from __future__ import annotations

import csv
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from jobs import cross_impact_job, deliverables, hats_job, intake, inventory_deliverable, qa
from jobs import inventory_optimization as io
from scm_agent import tool_options
from scm_agent.tools import hat_settlement_tool, hat_tension_tool, inventory_tool
from src.constraints import (
    cheapest_feasible_lot,
    feasible_lots,
    feasible_neighbours,
    is_feasible_lot,
    lot_rule,
)
from src.eoq import compute_eoq, total_cost
from src.hat_council import settle, tension_map
from src.hats import (
    MIN_FEASIBLE_Q_POINTS,
    SL_GRID,
    HatConfig,
    anchor_quantities,
    baseline_plan,
    build_inputs,
    candidate_grid,
)
from src.sources import DataFrameDemandSource

# -- 1. el motor ------------------------------------------------------------------


def test_lot_rule_is_one_short_label_everywhere():
    assert lot_rule() == ""
    assert lot_rule(60) == "moq 60"
    assert lot_rule(0, 30) == "multiple 30"
    assert lot_rule(60, 30) == "moq 60, multiple 30"
    assert lot_rule(12.5, 2.5) == "moq 12.5, multiple 2.5"
    with pytest.raises(ValueError):
        lot_rule(-1)


def test_is_feasible_lot_tolerates_float_noise_but_not_real_violations():
    assert is_feasible_lot(60.00000001, order_multiple=30)
    assert not is_feasible_lot(45, order_multiple=30)
    assert not is_feasible_lot(59.9, minimum_order_quantity=60)
    assert is_feasible_lot(60, minimum_order_quantity=60, order_multiple=30)
    assert not is_feasible_lot(0, order_multiple=30)


def test_feasible_neighbours_offer_both_sides_only_when_both_are_legal():
    assert feasible_neighbours(40, order_multiple=30) == (60, 30)
    # Debajo del MOQ no hay lote legal: "pedir menos que el minimo" no es una opcion.
    assert feasible_neighbours(40, minimum_order_quantity=60, order_multiple=30) == (60, None)
    assert feasible_neighbours(40, minimum_order_quantity=60) == (60, None)
    assert feasible_neighbours(70, minimum_order_quantity=60, order_multiple=30) == (90, 60)
    # Un objetivo ya legal no se mueve y no tiene nada que elegir.
    assert feasible_neighbours(60, order_multiple=30) == (60, None)
    with pytest.raises(ValueError):
        feasible_neighbours(0, order_multiple=30)


def test_feasible_lots_enumerates_the_multiples_above_the_moq():
    assert feasible_lots(20, 100, order_multiple=30) == [30, 60, 90]
    assert feasible_lots(20, 100, minimum_order_quantity=50, order_multiple=30) == [60, 90]
    assert feasible_lots(20, 100) == []          # sin multiplo el eje es continuo
    assert feasible_lots(100, 20, order_multiple=30) == []


def test_cheapest_feasible_lot_chooses_by_eoq_cost_and_keeps_the_runner_up():
    # D=1000, K=50, h=1 -> Q*=316.2; multiplo 100: 300 cuesta 316.67/anio, 400 cuesta 325.
    q_star = compute_eoq(1000.0, 1.0, 50.0).order_quantity
    d = cheapest_feasible_lot(
        q_star, annual_demand=1000.0, holding_cost_per_unit=1.0, fixed_order_cost=50.0,
        order_multiple=100.0,
    )
    assert d.rule == "multiple 100" and d.constrained and d.snapped
    assert (d.chosen.quantity, d.chosen.side) == (300, "down")
    assert (d.alternative.quantity, d.alternative.side) == (400, "up")
    base = total_cost(q_star, 1000.0, 1.0, 50.0)
    assert d.chosen.penalty_usd == pytest.approx(total_cost(300, 1000.0, 1.0, 50.0) - base)
    assert d.chosen.penalty_usd == pytest.approx(0.4389, abs=1e-3)
    assert d.alternative.penalty_usd == pytest.approx(8.7722, abs=1e-3)
    assert d.alternative.penalty_usd >= d.chosen.penalty_usd


def test_cheapest_feasible_lot_below_the_moq_has_one_terminal_and_says_so():
    d = cheapest_feasible_lot(
        40.0, annual_demand=1000.0, holding_cost_per_unit=1.0, fixed_order_cost=50.0,
        minimum_order_quantity=60.0,
    )
    assert (d.chosen.quantity, d.chosen.side) == (60, "up")
    assert d.alternative is None


def test_cheapest_feasible_lot_leaves_an_exact_target_alone():
    d = cheapest_feasible_lot(
        60.0, annual_demand=1000.0, holding_cost_per_unit=1.0, fixed_order_cost=50.0,
        order_multiple=30.0,
    )
    assert d.chosen.side == "exact" and d.chosen.quantity == 60
    assert d.alternative is None and d.chosen.penalty_usd == 0.0
    assert d.constrained and not d.snapped


def test_cheapest_feasible_lot_without_terms_is_the_identity():
    d = cheapest_feasible_lot(
        40.0, annual_demand=1000.0, holding_cost_per_unit=1.0, fixed_order_cost=50.0,
    )
    assert d.rule == "" and not d.constrained and not d.snapped
    assert d.chosen.quantity == 40.0 and d.alternative is None


def test_a_cost_tie_goes_up_more_cover_fewer_orders():
    # K*D/Q + h*Q/2 es igual en Q1 y Q2 cuando Q1*Q2 = 2KD/h: con D=K=h=1, Q*=sqrt(2)
    # y multiplo 1, los vecinos 1 y 2 cuestan exactamente 1.5 cada uno.
    d = cheapest_feasible_lot(
        2 ** 0.5, annual_demand=1.0, holding_cost_per_unit=1.0, fixed_order_cost=1.0,
        order_multiple=1.0,
    )
    assert d.chosen.annual_cost == pytest.approx(d.alternative.annual_cost)
    assert (d.chosen.quantity, d.chosen.side) == (2, "up")


# -- 2. el job de inventario --------------------------------------------------------

_SKUS = {
    # sku: (media semanal, costo unitario, moq, multiplo)
    "A": (20.0, 5.0, 0.0, 30.0),
    "B": (6.0, 40.0, 60.0, 0.0),
    "C": (15.0, 8.0, 0.0, 0.0),
}


def _demand(skus: dict = _SKUS, *, with_terms: bool = True, periods: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    dates = pd.date_range("2025-01-06", periods=periods, freq="W-MON")
    rows = []
    for sku, (mu, cost, moq, mult) in skus.items():
        for d in dates:
            row = {
                "date": d, "product_id": sku,
                "quantity": max(0, round(rng.normal(mu, mu * 0.2))),
                "unit_cost": cost, "lead_time_days": 14, "lead_time_std": 0.0,
            }
            if with_terms:
                row.update({"moq": moq, "order_multiple": mult})
            rows.append(row)
    return pd.DataFrame(rows)


def _rec(report, sku):
    return next(r for r in report.recommendations if r.product_id == sku)


def test_run_snaps_sq_quantities_to_the_cheapest_legal_lot_and_keeps_the_runner_up():
    report = io.run(_demand())
    a = _rec(report, "A")
    assert a.policy_kind == "(s, Q)" and a.lot_rule == "multiple 30"
    assert a.order_quantity % 30 == 0
    assert a.order_quantity_unconstrained is not None
    assert a.order_quantity_unconstrained != a.order_quantity
    assert a.lot_alternative_qty is not None and a.lot_alternative_qty % 30 == 0
    assert a.lot_alternative_penalty_usd >= a.lot_penalty_usd >= 0
    # El ciclo, la inversion y la beta implicita siguen al lote elegido, no al EOQ.
    assert a.cycle_investment == pytest.approx(a.order_quantity / 2 * a.unit_cost)
    assert a.investment == pytest.approx(a.cycle_investment + a.ss_investment)

    b = _rec(report, "B")
    assert b.lot_rule == "moq 60" and b.order_quantity >= 60

    c = _rec(report, "C")
    assert c.lot_rule == "" and c.order_quantity_unconstrained is None

    assert report.n_lot_constrained == 2
    assert report.n_lot_snapped == 1
    assert report.lot_penalty_total_usd == pytest.approx(a.lot_penalty_usd)
    assert report.n_lot_rule_unapplied == 0
    assert qa.verify(report) == []


def test_run_without_terms_columns_is_byte_for_byte_the_old_plan():
    with_cols = io.run(_demand({"C": _SKUS["C"]}, with_terms=False))
    without = io.run(_demand({"C": _SKUS["C"]}, with_terms=True))   # C carries zeros
    assert _rec(with_cols, "C").order_quantity == _rec(without, "C").order_quantity
    assert with_cols.n_lot_constrained == without.n_lot_constrained == 0
    assert _rec(with_cols, "C").lot_rule == ""


def test_per_sku_overrides_win_over_the_file():
    report = io.run(_demand(), order_multiples={"C": 50.0}, minimum_order_quantities={"A": 500.0})
    assert _rec(report, "C").order_quantity % 50 == 0
    assert _rec(report, "C").lot_rule == "multiple 50"
    a = _rec(report, "A")
    assert a.lot_rule == "moq 500, multiple 30" and a.order_quantity >= 500
    assert qa.verify(report) == []


def _intermittent_demand() -> pd.DataFrame:
    dates = pd.date_range("2025-01-06", periods=40, freq="W-MON")
    qty = [0, 0, 0, 9, 0, 0, 0, 0, 7, 0] * 4
    return pd.DataFrame({
        "date": dates, "product_id": "Z", "quantity": qty, "unit_cost": 12.0,
        "lead_time_days": 14, "moq": 0.0, "order_multiple": 30.0,
    })


def test_rs_skus_carry_the_rule_but_do_not_apply_it_and_the_options_say_so():
    report = io.run(_intermittent_demand())
    z = _rec(report, "Z")
    assert z.policy_kind == "(R, S)"
    assert z.order_quantity is None and z.lot_rule == "multiple 30"
    assert z.order_quantity_unconstrained is None and z.lot_penalty_usd == 0.0
    assert report.n_lot_rule_unapplied == 1 and report.n_lot_constrained == 0
    assert qa.verify(report) == []
    out = tool_options.inventory_options(report)
    assert any("(R,S)" in r.description and "MOQ" in r.description for r in out.residuals)
    assert not any(o.label.startswith("Renegotiate") for o in out.options)


def test_qa_rejects_a_quantity_the_supplier_would_refuse():
    report = io.run(_demand())
    a = _rec(report, "A")
    broken = replace(a, order_quantity=a.order_quantity + 1)
    issues = qa.verify(replace(report, recommendations=[broken]))
    assert any("violates the supplier terms" in i for i in issues)

    cheaper_alt = replace(a, lot_alternative_penalty_usd=a.lot_penalty_usd - 1.0)
    issues = qa.verify(replace(report, recommendations=[cheaper_alt]))
    assert any("alternative lot is cheaper" in i for i in issues)

    no_origin = replace(a, order_quantity_unconstrained=None)
    issues = qa.verify(replace(report, recommendations=[no_origin]))
    assert any("no unconstrained Q" in i for i in issues)


def test_csv_deck_and_report_carry_both_terminals(tmp_path):
    report = io.run(_demand())
    path = deliverables.write_csv(report, tmp_path / "summary.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = {r["product_id"]: r for r in csv.DictReader(fh)}
    for col in ("order_qty_unconstrained", "supplier_lot_rule", "lot_penalty_usd_yr",
                "lot_alternative_Q", "lot_alternative_penalty_usd_yr"):
        assert col in rows["A"]
    assert rows["A"]["supplier_lot_rule"] == "multiple 30"
    assert float(rows["A"]["lot_alternative_Q"]) % 30 == 0
    assert rows["C"]["supplier_lot_rule"] == "" and rows["C"]["lot_alternative_Q"] == ""

    deck = inventory_deliverable.build(report)
    finding = next(f for f in deck.findings if f.title == "Order quantities honour the supplier's terms")
    assert "runner" not in finding.title and "alternative" in finding.detail
    assert any(k.name.startswith("SKUs on supplier terms") for k in deck.kpis)
    assert any("lot penalties" in r for r in deck.recommendations)

    md = deliverables.write_report_md(report, tmp_path / "report.md").read_text(encoding="utf-8")
    assert "supplier terms" in md and "lot_alternative_Q" in md

    plain = inventory_deliverable.build(io.run(_demand(with_terms=False)))
    assert not any("supplier" in f.title for f in plain.findings)


def test_options_add_the_renegotiation_terminal_right_after_adopt():
    out = tool_options.inventory_options(io.run(_demand()))
    labels = [o.label for o in out.options]
    assert labels[0].startswith("Adopt")
    assert labels[1] == "Renegotiate supplier terms where rounding costs most"
    renegotiate = out.options[1]
    assert "A (multiple 30)" in renegotiate.summary and "runner-up" in renegotiate.summary
    assert out.options[0].recommended and not renegotiate.recommended

    plain = tool_options.inventory_options(io.run(_demand(with_terms=False)))
    assert not any(o.label.startswith("Renegotiate") for o in plain.options)


# -- intake y metadata ---------------------------------------------------------


def test_intake_maps_spanish_supplier_terms_and_emits_them_only_when_present():
    raw = pd.DataFrame({
        "Fecha": ["2026-01-05", "2026-01-06", "2026-01-05"],
        "Código": ["X", "X", "Y"], "Cantidad": [3, 4, 5], "Costo": [2.0, 2.0, 3.0],
        "Pedido mínimo": [50, 40, None], "Unidades por caja": [12, 12, 6],
    })
    mapping = intake.detect_columns(raw)
    assert mapping.mapping["moq"] == "Pedido mínimo"
    assert mapping.mapping["order_multiple"] == "Unidades por caja"
    canon, quality = intake.normalize_tracked(raw, mapping, period="W")
    assert list(canon.columns)[-2:] == ["moq", "order_multiple"]
    x = canon[canon["product_id"] == "X"].iloc[0]
    assert (x["moq"], x["order_multiple"]) == (50, 12)     # el mas estricto gana
    y = canon[canon["product_id"] == "Y"].iloc[0]
    assert y["moq"] == 0.0                                  # celda vacia = sin regla
    assert ("moq", 1) in quality.synthesized

    plain = raw.drop(columns=["Pedido mínimo", "Unidades por caja"])
    canon_plain = intake.normalize(plain, intake.detect_columns(plain), period="W")
    assert list(canon_plain.columns) == [
        "date", "product_id", "quantity", "unit_cost", "lead_time_days", "lead_time_std",
    ]

    meta = DataFrameDemandSource(canon).metadata("X")
    assert (meta.minimum_order_quantity, meta.order_multiple) == (50.0, 12.0)
    assert DataFrameDemandSource(canon_plain).metadata("X").order_multiple == 0.0


def test_the_tools_declare_the_terms_as_optional_columns():
    for tool in (inventory_tool(), hat_tension_tool(), hat_settlement_tool()):
        assert {"moq", "order_multiple"} <= set(tool.optional_columns), tool.key


# -- 3. los sombreros ------------------------------------------------------------

_BASE = dict(sku="SKU-L", annual_demand=52 * 400.0, mean_weekly=400.0, std_weekly=90.0,
             lead_time_weeks=2.0, unit_cost=30.0, config=HatConfig())


@pytest.mark.parametrize("terms", [
    {"order_multiple": 100.0},
    {"minimum_order_quantity": 1500.0},
    {"minimum_order_quantity": 600.0, "order_multiple": 250.0},
    {"order_multiple": 1.0},          # multiplo fino: miles de lotes, se ralea
    {"order_multiple": 5000.0},       # multiplo grueso: pocos lotes, se extiende
])
def test_the_grid_holds_only_legal_lots_and_always_contains_the_baseline(terms):
    inp = build_inputs(**_BASE, **terms)
    grid = candidate_grid(inp)
    assert grid == candidate_grid(inp)
    qs = sorted({c.order_quantity for c in grid})
    assert all(is_feasible_lot(q, **inp.lot_terms) for q in qs)
    assert len(qs) >= MIN_FEASIBLE_Q_POINTS
    assert len(grid) == len(qs) * len(SL_GRID)
    base = baseline_plan(inp)
    assert any(abs(q - base.order_quantity) < 1e-9 for q in qs)
    assert is_feasible_lot(base.order_quantity, **inp.lot_terms)


def test_without_terms_the_grid_is_the_classic_25_linear_points_plus_anchors():
    inp = build_inputs(**_BASE)
    q_eoq, q_disc = anchor_quantities(inp)
    lo, hi = 0.5 * min(q_eoq, q_disc), 1.25 * max(q_eoq, q_disc)
    expected = sorted({lo + i * (hi - lo) / 24 for i in range(25)} | {q_eoq, q_disc})
    assert sorted({c.order_quantity for c in candidate_grid(inp)}) == pytest.approx(expected)
    assert baseline_plan(inp).order_quantity == pytest.approx(q_eoq)


def test_the_baseline_snaps_exactly_like_the_inventory_job_does():
    inp = build_inputs(**_BASE, order_multiple=100.0)
    cfg = inp.config
    q_eoq = compute_eoq(inp.annual_demand, cfg.holding_rate * inp.unit_cost, cfg.order_cost).order_quantity
    expected = cheapest_feasible_lot(
        q_eoq, annual_demand=inp.annual_demand,
        holding_cost_per_unit=cfg.holding_rate * inp.unit_cost, fixed_order_cost=cfg.order_cost,
        order_multiple=100.0,
    ).chosen.quantity
    assert baseline_plan(inp).order_quantity == expected == 600.0


def test_ideals_and_the_settlement_are_legal_lots():
    inp = build_inputs(**_BASE, order_multiple=100.0)
    tmap = tension_map(inp)
    assert tmap.lot_rule == "multiple 100"
    for e in tmap.ideals.values():
        assert e.candidate.order_quantity % 100 == 0
    s = settle(inp, None)
    assert s.lot_rule == "multiple 100" and s.chosen.order_quantity % 100 == 0


def _hats_csv(tmp_path, *, terms: bool):
    path = tmp_path / "hats.csv"
    rows = [("SKU-L", q, 30, 14, "", 100 if terms else "") for q in (380, 410, 395, 420, 390, 405)]
    rows += [("SKU-F", q, 10, 7, "", "") for q in (100, 120, 90, 110, 105, 95)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["product_id", "quantity", "unit_cost", "lead_time_days", "moq", "order_multiple"])
        w.writerows(rows)
    return str(path)


def test_hats_job_reads_the_terms_gates_on_them_and_says_so(tmp_path):
    payload = hats_job.prepare(_hats_csv(tmp_path, terms=True), {})
    rules = {i.sku: i.lot_rule for i in payload["inputs"]}
    assert rules == {"SKU-F": "", "SKU-L": "multiple 100"}

    tension = hats_job.run_tension(payload)
    assert hats_job.verify_tension(tension) == []
    assert "Terminos del proveedor de SKU-L (multiple 100)" in tension.outcome.summary
    ruled = next(m for m in tension.maps if m.sku == "SKU-L")
    assert all(e.candidate.order_quantity % 100 == 0 for e in ruled.ideals.values())
    deck = hats_job.build_tension_deck(tension)
    assert any(f.title == "Terminos del proveedor" for f in deck.findings)

    settlement = hats_job.run_settlement(payload)
    assert hats_job.verify_settlement(settlement) == []
    assert "Terminos del proveedor aplicados en 1 SKU(s)" in settlement.outcome.summary
    settled = next(s for s in settlement.settlements if s.sku == "SKU-L")
    assert settled.chosen.order_quantity % 100 == 0
    assert any(f.title == "Terminos del proveedor"
               for f in hats_job.build_settlement_deck(settlement).findings)


def test_verify_tension_rejects_an_ideal_the_supplier_would_refuse(tmp_path):
    tension = hats_job.run_tension(hats_job.prepare(_hats_csv(tmp_path, terms=True), {}))
    ruled = next(m for m in tension.maps if m.sku == "SKU-L")
    key, ev = next(iter(ruled.ideals.items()))
    bad = replace(ev, candidate=replace(ev.candidate, order_quantity=ev.candidate.order_quantity + 1))
    broken = replace(ruled, ideals={**ruled.ideals, key: bad})
    maps = tuple(broken if m.sku == "SKU-L" else m for m in tension.maps)
    issues = hats_job.verify_tension(replace(tension, maps=maps))
    assert any("violates the supplier terms" in i for i in issues)

    # Y sin terminos el piso de 125 candidatos sigue vigente, intacto.
    free = next(m for m in tension.maps if m.sku == "SKU-F")
    starved = replace(free, candidates_evaluated=100)
    maps = tuple(starved if m.sku == "SKU-F" else m for m in tension.maps)
    assert any("only 100 candidates" in i for i in hats_job.verify_tension(replace(tension, maps=maps)))


def test_a_run_wide_param_is_the_fallback_when_the_file_has_no_column(tmp_path):
    payload = hats_job.prepare(_hats_csv(tmp_path, terms=False), {"order_multiple": 30})
    assert {i.sku: i.lot_rule for i in payload["inputs"]} == {"SKU-F": "multiple 30", "SKU-L": "multiple 30"}
    # La columna del archivo le gana al param del run.
    payload = hats_job.prepare(_hats_csv(tmp_path, terms=True), {"order_multiple": 30})
    assert {i.sku: i.lot_rule for i in payload["inputs"]} == {"SKU-F": "multiple 30", "SKU-L": "multiple 100"}
    with pytest.raises(ValueError, match="order_multiple must be >= 0"):
        hats_job.prepare(_hats_csv(tmp_path, terms=False), {"order_multiple": -5})


def _cross_params(q_proposed: float) -> dict:
    return {
        "sku": "SKU-L", "proposed_by": "comprador",
        "current": {"q": 1600, "sl": 0.95}, "proposed": {"q": q_proposed, "sl": 0.95},
        "objection_thresholds": {"comprador": 0.05, "planner": 0.05, "cfo": 0.05, "comercial": 0.05},
    }


def test_cross_impact_names_the_nearest_legal_lots_when_the_proposal_is_illegal(tmp_path):
    path = _hats_csv(tmp_path, terms=True)
    report = cross_impact_job.run(cross_impact_job.prepare(path, _cross_params(1750)))
    assert cross_impact_job.verify(report) == []
    residual = next(r for r in report.outcome.residuals if "no cumple los terminos" in r.description)
    assert "1,700 o 1,800" in residual.description and "multiple 100" in residual.description

    legal = cross_impact_job.run(cross_impact_job.prepare(path, _cross_params(1800)))
    assert not any("no cumple los terminos" in r.description for r in legal.outcome.residuals)
