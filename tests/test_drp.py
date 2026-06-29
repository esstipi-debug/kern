"""Tests for the offline DRP engine: time-phased grid + multi-echelon rollup."""

from src.drp import Branch, drp_plan, rollup_gross_requirements


def _branch() -> Branch:
    # forecast 10/period, on-hand 15, lead time 1, safety stock 5, lot-for-lot
    return Branch("B1", forecast=(10, 10, 10, 10), on_hand=15, lead_time=1, safety_stock=5)


def test_drp_grid_nets_and_offsets_releases():
    rows = drp_plan(_branch())
    # projected on-hand holds the safety-stock floor every period
    assert [r.projected_on_hand for r in rows] == [5, 5, 5, 5]
    # net + receipts kick in once on-hand is drawn down
    assert [r.planned_receipt for r in rows] == [0, 10, 10, 10]
    # releases are offset one period earlier (lead time 1); last receipt's release is past-due-free
    assert [r.planned_order_release for r in rows] == [10, 10, 10, 0]


def test_lot_size_rounds_receipts_up():
    b = Branch("B", forecast=(7, 7), on_hand=0, lead_time=0, safety_stock=0, lot_size=10)
    rows = drp_plan(b)
    assert rows[0].planned_receipt == 10      # ceil(7/10)*10
    assert rows[1].planned_receipt == 10


def test_rollup_sums_branch_releases_per_period():
    plan_a = drp_plan(_branch())                                  # releases [10,10,10,0]
    plan_b = drp_plan(Branch("B2", (5, 5, 0, 0), on_hand=0, lead_time=1, safety_stock=0))
    total = rollup_gross_requirements([plan_a, plan_b], 4)
    # period 0 of the DC's gross requirements = the two branches' period-0 releases summed
    assert total[0] == plan_a[0].planned_order_release + plan_b[0].planned_order_release
    assert len(total) == 4
    assert total[0] >= 10
