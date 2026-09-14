"""Tests for the flow_map job: intake, the QA gate, deliverables."""
import re
from pathlib import Path

import pandas as pd
import pytest

from jobs import flow_map_job
from nosilo_core.guided import ExecutionOption, HandoffPacket, as_handoff, as_options
from src.flow_map import UNMEASURABLE, EdgeBuffer, EdgeStat, FlowGraph, FlowReport
from src.flow_svg import _CHAR_WIDTH_ESTIMATE, render_page, render_sankey

GOLDEN = Path(__file__).parent / "fixtures" / "flow_map" / "sankey_golden.svg"


def _frame(rows):
    return pd.DataFrame(rows)


def _lane(origin, destination, days, n, start_day=1, qty=10.0):
    return [
        {
            "ship_date": f"2026-01-{start_day + i:02d}",
            "receipt_date": f"2026-01-{start_day + i + int(days):02d}",
            "origin": origin, "destination": destination,
            "sku": "SKU-A", "quantity": qty, "unit_cost": 5.0,
        }
        for i in range(n)
    ]


def _healthy_csv(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", "STORE-1", 4, 8, start_day=2)
    path = tmp_path / "movimientos.csv"
    _frame(rows).to_csv(path, index=False)
    return path


def test_prepare_maps_aliases_and_measures_transit(tmp_path):
    movements = flow_map_job.prepare(str(_healthy_csv(tmp_path)))
    assert len(movements) == 16
    assert {m.origin for m in movements} == {"SUP-1", "DC-1"}
    assert all(m.transit_days > 0 for m in movements)


def test_prepare_names_the_exact_missing_column(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8)
    for row in rows:
        del row["receipt_date"]
    path = tmp_path / "sin_llegada.csv"
    _frame(rows).to_csv(path, index=False)
    with pytest.raises(ValueError, match="fecha_llegada"):
        flow_map_job.prepare(str(path))


def test_gate_blocks_when_most_volume_is_unmeasurable(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8, qty=1.0) + _lane("DC-1", "STORE-9", 3, 3, qty=500.0)
    path = tmp_path / "flaco.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    issues = flow_map_job.verify(report)
    assert any("unmeasurable" in i for i in issues)


def test_gate_blocks_on_inverted_dates(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", "STORE-1", 4, 8, start_day=2)
    rows[0]["receipt_date"] = "2026-01-01"
    rows[0]["ship_date"] = "2026-01-09"
    path = tmp_path / "invertido.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    assert any("arrival before departure" in i for i in flow_map_job.verify(report))


def test_gate_blocks_a_single_edge_graph(tmp_path):
    path = tmp_path / "una_arista.csv"
    _frame(_lane("SUP-1", "DC-1", 2, 8)).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    assert any("fewer than two lanes" in i for i in flow_map_job.verify(report))


def test_healthy_data_passes_the_gate(tmp_path):
    report = flow_map_job.run(flow_map_job.prepare(str(_healthy_csv(tmp_path))))
    assert flow_map_job.verify(report) == []
    assert report.edges[0].status != UNMEASURABLE


def test_gate_invariant_fires_if_a_ranked_option_ever_points_at_an_unmeasurable_edge():
    """_buffers only ever ranks MEASURED edges (src/flow_map.py), so this hand-built
    report violates that invariant on purpose: it is the only way to exercise this
    defensive check, which cannot occur via analyze() as the engine is written today.
    """
    edge = EdgeStat(
        origin="SUP-1", destination="DC-1", n=1, volume=10.0, status=UNMEASURABLE,
    )
    buffer = EdgeBuffer(origin="SUP-1", destination="DC-1", buffer_units=5.0, buffer_value=25.0)
    outcome = as_options(
        "one option",
        [ExecutionOption(label="Stabilise SUP-1 -> DC-1", summary="x", score=25.0, action="a")],
    )
    report = FlowReport(
        graph=FlowGraph(nodes=("SUP-1", "DC-1")),
        edges=(edge,),
        buffers=(buffer,),
        n_movements=1,
        n_negative_transit=0,
        unmeasurable_volume_share=1.0,
        span_days=10.0,
        outcome=outcome,
        summary="hand-built report to exercise the invariant guard",
    )
    issues = flow_map_job.verify(report)
    assert any("a ranked option leans on an unmeasurable lane" in i for i in issues)


def _many_rows(n, *, bad_date=0, bad_qty=0):
    """n rows across two well-observed lanes, with the first bad_date/bad_qty rows
    (in that order) corrupted so prepare() must drop them.
    """
    rows = _lane("SUP-1", "DC-1", 2, n // 2) + _lane("DC-1", "STORE-1", 4, n - n // 2, start_day=2)
    for row in rows[:bad_date]:
        row["ship_date"] = "not-a-date"
    for row in rows[bad_date:bad_date + bad_qty]:
        row["quantity"] = "not-a-number"
    return rows


def test_prepare_tolerates_dropped_rows_just_under_the_threshold(tmp_path):
    rows = _many_rows(20, bad_date=1, bad_qty=1)  # 2 of 20 = 10%, not > MAX_DROPPED_SHARE
    path = tmp_path / "casi_al_limite.csv"
    _frame(rows).to_csv(path, index=False)
    movements = flow_map_job.prepare(str(path))
    assert len(movements) == 18


def test_prepare_refuses_when_too_many_rows_are_dropped(tmp_path):
    rows = _many_rows(20, bad_date=2, bad_qty=1)  # 3 of 20 = 15% > MAX_DROPPED_SHARE
    path = tmp_path / "muy_sucio.csv"
    _frame(rows).to_csv(path, index=False)
    with pytest.raises(ValueError, match=r"3 of 20 row\(s\)"):
        flow_map_job.prepare(str(path))


def test_prepare_all_dropped_raises_no_usable_movements_not_the_threshold_message(tmp_path):
    """Precedence: when every row is unusable, the 'no usable movements' error fires
    -- not the dropped-share threshold message, even though the share is also over
    the limit in that case.
    """
    rows = _lane("SUP-1", "DC-1", 2, 8)
    for row in rows:
        row["quantity"] = "not-a-number"
    path = tmp_path / "todo_sucio.csv"
    _frame(rows).to_csv(path, index=False)
    with pytest.raises(ValueError, match="no usable movements"):
        flow_map_job.prepare(str(path))


def test_sankey_is_deterministic(tmp_path):
    """Same source CSV, run through prepare/run twice independently, must render to
    byte-identical SVG -- not just the same FlowReport object rendered twice (which
    would only prove render_sankey has no internal randomness, not that the pipeline
    itself is deterministic).
    """
    csv_path = str(_healthy_csv(tmp_path))
    first = flow_map_job.run(flow_map_job.prepare(csv_path))
    second = flow_map_job.run(flow_map_job.prepare(csv_path))
    assert render_sankey(first) == render_sankey(second)


def test_sankey_matches_the_golden(tmp_path):
    report = flow_map_job.run(flow_map_job.prepare(str(_healthy_csv(tmp_path))))
    assert render_sankey(report) == GOLDEN.read_text(encoding="utf-8")


def test_sankey_viewbox_is_never_shorter_than_the_drawing(tmp_path):
    """Regression for CRITICAL 1 (2026-07-31 flow_map fix wave): _layout used to take
    each node's TOP y-coordinate for the canvas bottom, never adding its height, so the
    viewBox was always shorter than the drawing and <svg> silently clipped it (a client
    opening flow_map.html saw only the first ~120px of a 560px map). Parse the actual
    rect bottoms and text baselines out of the rendered SVG rather than hardcoding a
    number, so this survives a future layout change.
    """
    report = flow_map_job.run(flow_map_job.prepare(str(_healthy_csv(tmp_path))))
    svg = render_sankey(report)

    viewbox_match = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert viewbox_match is not None
    viewbox_height = int(viewbox_match.group(2))

    rect_bottoms = [
        float(y) + float(h)
        for y, h in re.findall(r'<rect x="[\d.]+" y="([\d.]+)" width="\d+" height="([\d.]+)"', svg)
    ]
    text_baselines = [float(y) for y in re.findall(r'<text x="[\d.]+" y="([\d.]+)"', svg)]
    assert rect_bottoms and text_baselines

    assert viewbox_height >= max(rect_bottoms)
    assert viewbox_height >= max(text_baselines)


def test_sankey_viewbox_width_covers_the_widest_label(tmp_path):
    """Horizontal twin of the height regression above (CRITICAL 1). _layout used
    to size the canvas from the rightmost node's RECT only, never accounting for
    its label, which is drawn to the right of the rect (x + _NODE_WIDTH + 8 in
    render_sankey). <svg> clips overflow by default, so long node names in the
    last column were silently truncated. Parse the actual text x's and labels out
    of the rendered SVG and estimate each one's right edge with the same
    conservative per-character constant the production code uses, rather than
    hardcoding a pixel number -- this must survive a future layout change.

    width=0 disables render_sankey's advisory-width floor so the assertion
    exercises the layout's own computed width, not the default 960px canvas
    that would mask the bug on modest node names.
    """
    long_name = "WAREHOUSE-INTERNATIONAL-HUB"  # 27 chars, well past the 25+ char bar
    assert len(long_name) >= 25
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", long_name, 4, 8, start_day=2)
    path = tmp_path / "nombre_largo.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))

    svg = render_sankey(report, width=0)

    viewbox_match = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    assert viewbox_match is not None
    viewbox_width = float(viewbox_match.group(1))

    label_matches = re.findall(r'<text x="([\d.]+)"[^>]*>([^<]*)</text>', svg)
    assert label_matches
    assert any(long_name in label for _x, label in label_matches)

    for x, label in label_matches:
        estimated_right_edge = float(x) + len(label) * _CHAR_WIDTH_ESTIMATE
        assert viewbox_width >= estimated_right_edge


def test_page_is_self_contained(tmp_path):
    report = flow_map_job.run(flow_map_job.prepare(str(_healthy_csv(tmp_path))))
    page = render_page(report, client="Acme")
    assert "<svg" in page
    # "http://" alone is not a valid signal here: every SVG root legitimately
    # carries xmlns="http://www.w3.org/2000/svg" (an XML namespace id, not a
    # network fetch). What actually matters is that the page loads nothing
    # external -- no script tag, no src=, no http(s) href, no @import, no CDN.
    assert "<script" not in page
    assert "src=" not in page
    assert 'href="http' not in page and "href='http" not in page
    assert "@import" not in page
    assert "cdn" not in page.lower()


def test_write_operational_emits_csv_and_html(tmp_path):
    report = flow_map_job.run(flow_map_job.prepare(str(_healthy_csv(tmp_path))))
    paths = flow_map_job.write_operational(report, tmp_path / "out", client="Acme")
    assert paths["csv"].exists() and paths["html"].exists()
    body = paths["csv"].read_text(encoding="utf-8")
    assert "mean_days" in body


def _lane_no_cost(origin, destination, days, n, start_day=1, qty=10.0):
    """Same as _lane but with no cost column at all -- the movements file this client
    sent simply never carried a costo_unitario/unit_cost column."""
    return [
        {
            "ship_date": f"2026-01-{start_day + i:02d}",
            "receipt_date": f"2026-01-{start_day + i + int(days):02d}",
            "origin": origin, "destination": destination,
            "sku": "SKU-A", "quantity": qty,
        }
        for i in range(n)
    ]


def test_cost_free_export_ranks_an_option_with_no_value_clause(tmp_path):
    """Regression for CRITICAL 2 (2026-07-31 flow_map fix wave): when the movements
    file carries no cost column at all, the ranked option text must never claim the
    buffer is worth 0 -- it must simply omit the value clause.
    """
    rows = (
        _lane_no_cost("SUP-1", "DC-1", 2, 8)
        + _lane_no_cost("DC-1", "STORE-1", 4, 8, start_day=2)
    )
    path = tmp_path / "sin_costo.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    assert flow_map_job.verify(report) == []
    assert report.buffers
    top = report.outcome.options[0]
    assert "in value" not in top.summary
    assert "0 in value" not in top.summary
    assert "unit(s) of protective stock" in top.summary


def test_cost_free_export_writes_an_empty_buffer_value_cell(tmp_path):
    rows = (
        _lane_no_cost("SUP-1", "DC-1", 2, 8)
        + _lane_no_cost("DC-1", "STORE-1", 4, 8, start_day=2)
    )
    path = tmp_path / "sin_costo2.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    paths = flow_map_job.write_operational(report, tmp_path / "out3")
    body = paths["csv"].read_text(encoding="utf-8")
    lines = body.splitlines()
    header = lines[0].split(",")
    buffer_value_idx = header.index("buffer_value")
    data_rows = [line.split(",") for line in lines[1:]]
    # At least one measured lane exists, and its buffer_value cell must be empty --
    # never a printed 0 or 0.0 standing in for "no cost was recorded."
    measured_rows = [row for row in data_rows if row[header.index("status")] == "measured"]
    assert measured_rows
    for row in measured_rows:
        assert row[buffer_value_idx] == ""


def test_cost_free_export_deck_never_prints_a_zero_value(tmp_path):
    rows = (
        _lane_no_cost("SUP-1", "DC-1", 2, 8)
        + _lane_no_cost("DC-1", "STORE-1", 4, 8, start_day=2)
    )
    path = tmp_path / "sin_costo3.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    deck = flow_map_job.build_deck(report, client="Acme")
    for finding in deck.findings:
        assert "0 in value" not in finding.detail
        assert "(0 in value)" not in finding.detail


def test_genuinely_zero_cost_row_is_still_treated_as_a_real_cost(tmp_path):
    """A movements row that carries unit_cost=0 (a real zero-cost transfer) must be
    measured as a real 0.0 mean_unit_cost, never folded into "no cost recorded" -- so
    its buffer_value is a real (zero) number, not an empty cell.
    """
    rows = _lane("SUP-1", "DC-1", 2, 8)
    for row in rows:
        row["unit_cost"] = 0.0
    rows += _lane("DC-1", "STORE-1", 4, 8, start_day=2)
    path = tmp_path / "costo_cero.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    edge = next(e for e in report.edges if (e.origin, e.destination) == ("SUP-1", "DC-1"))
    assert edge.mean_unit_cost == 0.0


def test_unmeasurable_lane_writes_empty_cells_not_zeros(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", "STORE-9", 3, 3)
    path = tmp_path / "mixto.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    paths = flow_map_job.write_operational(report, tmp_path / "out2")
    line = next(
        row for row in paths["csv"].read_text(encoding="utf-8").splitlines()
        if "STORE-9" in row
    )
    assert ",0," not in line and ",0.0," not in line
    assert ",," in line


def test_render_sankey_on_empty_report_returns_minimal_valid_svg():
    """A FlowReport with no nodes is reachable (e.g. a hand-built empty graph);
    _layout must not raise ValueError from max() on an empty sequence.
    """
    report = FlowReport(
        graph=FlowGraph(nodes=()),
        edges=(),
        buffers=(),
        n_movements=0,
        n_negative_transit=0,
        unmeasurable_volume_share=0.0,
        span_days=1.0,
        outcome=as_handoff(
            "no data",
            [HandoffPacket(title="widen the export window", steps=["re-run"])],
        ),
        summary="empty report",
    )
    svg = render_sankey(report)
    assert svg.startswith("<svg")
    assert "<title>" in svg and "<desc>" in svg
    assert "<rect" not in svg
    assert svg.endswith("</svg>")


def test_deck_names_the_top_lane_and_declares_the_unmeasurable_ones(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", "STORE-1", 4, 8, start_day=2)
    path = tmp_path / "deck.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    deck = flow_map_job.build_deck(report, client="Acme", citations=("Vandeput, ch. 4",))
    assert deck.client == "Acme"
    assert deck.options == tuple(report.outcome.options)
    assert "Vandeput, ch. 4" in deck.citations
    assert any("lane" in f.title.lower() or "transit" in f.title.lower() for f in deck.findings)
    assert "movement" in deck.residual
