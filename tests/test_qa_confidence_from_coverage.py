"""T4a - confidence derived from coverage, on the adjudication path.

src/guided.py's four builders default confidence=1.0 and 27 of 45 call sites never
pass it, so an outcome built on a degenerate scorecard claimed the same certainty as
one built on complete data. verify_guided only ever checked 0 <= c <= 1.

Scope note: "forbid the literal in the deck layer" is a 44-file change (45 literal
confidence defaults across jobs/ + scm_agent/ at the time of writing), which is a
refactor, not a rule. What is in scope here: make confidence derivable, wire the
derivation where a coverage signal actually exists, and ratchet the literal count so
new ones cannot be added silently.

Witness: data/kaggle/olist, 777 delivered POs, 8 sellers, lead time resolvable but no
on-time/in-full flag -> otif and ppm cannot discriminate (T1a) yet the award returned
confidence 1.0 and a deck at 0.85.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pandas as pd
import pytest

from jobs import sourcing_job
from src.guided import confidence_from_coverage

REPO = Path(__file__).resolve().parents[1]
OLIST = REPO / "data" / "kaggle" / "olist"

# Known debt at the time T4 landed. It may go DOWN freely; going up means someone
# hardcoded a new confidence instead of deriving it.
#
# Bumped 43 -> 45 merging origin/main (2026-08-01): jobs/vehicle_routing_job.py and
# jobs/whatif_job.py landed independently on main with their own hardcoded confidence
# defaults, never derived through confidence_from_coverage. Inherited debt from a
# parallel branch, not a new hardcode added here - tracked as a follow-up, not fixed
# in this merge (out of scope: deriving their confidence needs each tool's own notion
# of coverage, not something to guess at while reconciling an unrelated conflict).
LITERAL_CONFIDENCE_CEILING = 45


@pytest.fixture(scope="module")
def degenerate_cards():
    """Real lead time + price (discriminating); on_time/in_full/defects/units are
    measured for every supplier but held constant, so otif and ppm are ranking
    criteria (not ``unmeasured`` - every scorecard has a value) that TOPSIS's own
    dispersion gate must drop for carrying zero variance (``ignored_criteria``),
    not because the column was absent."""
    items = pd.read_csv(OLIST / "olist_order_items_dataset.csv")
    orders = pd.read_csv(OLIST / "olist_orders_dataset.csv")
    df = items.merge(orders, on="order_id", how="inner")
    df = df[df.order_status == "delivered"].copy()
    for col in ("order_purchase_timestamp", "order_delivered_customer_date"):
        df[col] = pd.to_datetime(df[col])
    df = df.dropna(subset=["order_delivered_customer_date"])
    top = df.seller_id.value_counts().head(8).index
    panel = df[df.seller_id.isin(top)].head(777).copy()
    panel["supplier"] = panel.seller_id
    panel["lead_time_days"] = (
        panel.order_delivered_customer_date - panel.order_purchase_timestamp
    ).dt.days
    panel["on_time"] = True
    panel["in_full"] = True
    panel["defects"] = 0
    panel["units"] = 10
    return sourcing_job.score_suppliers(
        panel, supplier_col="supplier", on_time_col="on_time", in_full_col="in_full",
        lead_col="lead_time_days", units_col="units", defects_col="defects",
        price_col="price",
    )


@pytest.fixture(scope="module")
def healthy_cards():
    """Every criterion varies: full confidence must survive."""
    df = pd.DataFrame({
        "supplier": ["S1", "S1", "S2", "S2", "S3", "S3"],
        "on_time": [True, True, True, False, False, True],
        "in_full": [True, True, False, True, True, True],
        "lead_time_days": [5, 6, 9, 11, 14, 13],
        "units": [10, 10, 10, 10, 10, 10],
        "defects": [0, 1, 2, 0, 3, 1],
        "price": [10.0, 10.5, 9.0, 9.4, 11.0, 10.8],
    })
    return sourcing_job.score_suppliers(
        df, supplier_col="supplier", on_time_col="on_time", in_full_col="in_full",
        lead_col="lead_time_days", units_col="units", defects_col="defects",
        price_col="price",
    )


def test_helper_is_monotonic_and_clamped():
    assert confidence_from_coverage([]) == pytest.approx(1.0)
    one = confidence_from_coverage(["ignored_criterion"])
    two = confidence_from_coverage(["ignored_criterion", "ignored_criterion"])
    assert 1.0 > one > two
    assert confidence_from_coverage(["ignored_criterion"] * 50) >= 0.0
    assert confidence_from_coverage([], base=0.85) == pytest.approx(0.85)
    with pytest.raises(KeyError):
        confidence_from_coverage(["not_a_known_gap"])


def test_degenerate_award_no_longer_claims_certainty(degenerate_cards):
    cards, prices = degenerate_cards
    report = sourcing_job.run(cards, prices)
    assert set(report.ranking.ignored_criteria) == {"otif", "ppm"}
    assert report.outcome.confidence < 1.0, "an award on 2 dead criteria claimed 1.0"
    deck = sourcing_job.build_deck(report)
    assert deck.confidence < 0.85, "deck kept its editorial literal"


def test_healthy_award_keeps_full_confidence(healthy_cards):
    cards, prices = healthy_cards
    report = sourcing_job.run(cards, prices)
    assert report.ranking.ignored_criteria == ()
    assert report.outcome.confidence == pytest.approx(1.0)
    assert sourcing_job.build_deck(report).confidence == pytest.approx(0.85)


def test_literal_confidence_defaults_do_not_grow():
    """Ratchet on the 45-site debt: it may shrink, never grow."""
    found = []
    for sub in ("jobs", "scm_agent"):
        for dirpath, dirnames, filenames in os.walk(REPO / sub):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in sorted(filenames):
                if not name.endswith(".py"):
                    continue
                path = Path(dirpath) / name
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    args = node.args
                    defaults = dict(zip(
                        [a.arg for a in args.kwonlyargs], args.kw_defaults
                    ))
                    positional = [a.arg for a in args.args]
                    padded = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
                    defaults.update(dict(zip(positional, padded)))
                    value = defaults.get("confidence")
                    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                        found.append(f"{path.relative_to(REPO)}:{node.lineno} {node.name}")
    assert len(found) <= LITERAL_CONFIDENCE_CEILING, (
        f"literal confidence defaults grew to {len(found)}; derive it instead:\n"
        + "\n".join(found)
    )


def test_abc_deck_confidence_drops_when_value_ranks_on_units():
    """T4b: witness 1 -- flat unit cost must cost the ABC deck confidence."""
    from jobs import abc_xyz_job, intake

    canon = pd.read_csv(REPO / "data" / "kaggle" / "superstore_canonical.csv")
    degenerate = abc_xyz_job.run(abc_xyz_job.aggregate_skus(
        canon, product_col="product_id", demand_col="quantity", cost_col="unit_cost"))
    assert degenerate.value_basis.discriminates is False
    assert abc_xyz_job.build_deck(degenerate).confidence < 0.9

    raw = pd.read_csv(REPO / "data" / "kaggle" / "superstore.csv", encoding="latin-1")
    healthy = abc_xyz_job.run(abc_xyz_job.aggregate_skus(
        intake.normalize(raw, intake.detect_columns(raw)),
        product_col="product_id", demand_col="quantity", cost_col="unit_cost"))
    assert healthy.value_basis.discriminates is True
    assert abc_xyz_job.build_deck(healthy).confidence == pytest.approx(0.9)


def test_sourcing_deck_no_longer_hardcodes_confidence():
    src = (REPO / "jobs" / "sourcing_job.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "build_deck":
            defaults = dict(zip(
                [a.arg for a in node.args.kwonlyargs], node.args.kw_defaults
            ))
            value = defaults.get("confidence")
            assert not (isinstance(value, ast.Constant) and isinstance(value.value, float)), (
                "sourcing_job.build_deck still defaults confidence to a literal"
            )
            return
    pytest.fail("build_deck not found")
