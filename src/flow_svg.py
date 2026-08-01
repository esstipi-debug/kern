"""Deterministic sankey rendering for a FlowReport -- no dependencies, no CDN.

The deliverable has to open on a plant PC with no internet, travel by mail and
survive an air-gapped client, so the layout is computed here rather than handed
to a JS library. Everything is sorted explicitly: same report in, byte-identical
SVG out, which is what makes the golden test meaningful.
"""

from __future__ import annotations

from html import escape

from src.flow_map import MEASURED, UNMEASURABLE, EdgeStat, FlowReport

_COL_WIDTH = 220
_NODE_WIDTH = 18
_NODE_GAP = 14
_MIN_NODE_HEIGHT = 8
_MARGIN = 40
_MAX_STROKE = 26.0
_MIN_STROKE = 1.5
_LABEL_OFFSET = 8
_FONT_SIZE = 13
# Deliberate conservative estimate, not a measurement: SVG text has no width
# without a font engine, so this is a per-character advance at _FONT_SIZE that
# is wide enough to cover the system-ui/sans-serif stack actually used below.
# An over-wide canvas is harmless (just some blank margin); an under-wide one
# clips the last column's labels, which is the bug this constant exists to
# prevent. Chosen as roughly 0.62 * font-size, a safe upper bound for average
# character width in a proportional sans-serif at this size.
_CHAR_WIDTH_ESTIMATE = 8.0


def _node_throughput(report: FlowReport) -> dict[str, float]:
    flow: dict[str, float] = {n: 0.0 for n in report.graph.nodes}
    for e in report.edges:
        flow[e.origin] = flow.get(e.origin, 0.0) + e.volume
        flow[e.destination] = flow.get(e.destination, 0.0) + e.volume
    return flow


def _layout(report: FlowReport) -> tuple[dict[str, tuple[float, float, float]], int, int]:
    """node -> (x, y, height), plus canvas width and height. Deterministic by sort."""
    echelon = report.graph.echelon
    columns: dict[int, list[str]] = {}
    for node in report.graph.nodes:
        columns.setdefault(echelon[node], []).append(node)

    if not columns:
        return {}, _MARGIN * 2, _MARGIN * 2

    throughput = _node_throughput(report)
    tallest_column = max(
        (sum(throughput[n] for n in nodes) for nodes in columns.values()), default=1.0
    ) or 1.0
    usable_height = 520.0

    placed: dict[str, tuple[float, float, float]] = {}
    for level in sorted(columns):
        y = float(_MARGIN)
        for node in sorted(columns[level]):
            height = max(
                _MIN_NODE_HEIGHT, usable_height * (throughput[node] / tallest_column)
            )
            placed[node] = (float(_MARGIN + level * _COL_WIDTH), y, height)
            y += height + _NODE_GAP
    rect_width = _MARGIN * 2 + (max(columns) * _COL_WIDTH) + _NODE_WIDTH
    # Right edge of the widest rendered label, not just the rect -- the label is
    # drawn at x + _NODE_WIDTH + _LABEL_OFFSET (see render_sankey below) and can
    # extend well past the rect alone. Same shape of bug as the height fix just
    # below: <svg> clips overflow, so a canvas sized only for the rect silently
    # truncates the last column's node names. Estimated, not measured -- see
    # _CHAR_WIDTH_ESTIMATE -- and kept as a floor against rect_width so this can
    # only widen the canvas, never narrow it below the rects.
    label_right_edge = max(
        (x + _NODE_WIDTH + _LABEL_OFFSET + len(node) * _CHAR_WIDTH_ESTIMATE
         for node, (x, _y, _h) in placed.items()),
        default=0.0,
    )
    width = max(rect_width, label_right_edge + _MARGIN)
    # Bottom edge of the lowest node's rect, not its top -- a bare `y` here leaves the
    # canvas shorter than the drawing (the tallest node's own height goes uncounted),
    # and <svg> clips overflow by default, so the drawing gets silently truncated.
    bottom = max(y + h for _, y, h in placed.values())
    height = int(bottom + _MARGIN + 40)
    return placed, int(width), height


def _stroke_width(edge: EdgeStat, max_volume: float) -> float:
    if max_volume <= 0:
        return _MIN_STROKE
    return _MIN_STROKE + (_MAX_STROKE - _MIN_STROKE) * (edge.volume / max_volume)


def _edge_title(edge: EdgeStat) -> str:
    if edge.status == MEASURED:
        return (
            f"{edge.origin} -> {edge.destination}: {edge.volume:,.0f} units, "
            f"transit {edge.mean_days:.1f} d +/- {edge.stdev_days:.1f} d over {edge.n} movements"
        )
    return (
        f"{edge.origin} -> {edge.destination}: {edge.volume:,.0f} units, "
        f"transit not measurable ({edge.n} movement(s), below the threshold)"
    )


def render_sankey(report: FlowReport, *, client: str = "Client", width: int = 960) -> str:
    """One SVG string. `width` is advisory: the real width comes from the layout."""
    placed, canvas_width, canvas_height = _layout(report)
    canvas_width = max(canvas_width, width)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_width} {canvas_height}" '
        f'width="100%" role="img">',
        f"<title>{escape(client)} flow map</title>",
        "<desc>Observed supply network by echelon; band width is volume, dashed lanes "
        "have too few movements to measure transit.</desc>",
    ]

    if not report.graph.nodes:
        parts.append("</svg>")
        return "".join(parts)

    max_volume = max((e.volume for e in report.edges), default=0.0)
    # measure_edges (src/flow_map.py) already emits edges sorted by (origin,
    # destination), but sort explicitly here too so this module's determinism is
    # provable on its own, not borrowed from an invariant that lives elsewhere.
    for edge in sorted(report.edges, key=lambda e: (e.origin, e.destination)):
        if edge.origin not in placed or edge.destination not in placed:
            continue
        x1, y1, h1 = placed[edge.origin]
        x2, y2, h2 = placed[edge.destination]
        sx, sy = x1 + _NODE_WIDTH, y1 + h1 / 2
        ex, ey = x2, y2 + h2 / 2
        mid = (sx + ex) / 2
        dash = ' stroke-dasharray="6 5"' if edge.status == UNMEASURABLE else ""
        parts.append(
            f'<path d="M{sx:.1f},{sy:.1f} C{mid:.1f},{sy:.1f} {mid:.1f},{ey:.1f} '
            f'{ex:.1f},{ey:.1f}" fill="none" stroke="#5DCAA5" stroke-opacity="0.55" '
            f'stroke-width="{_stroke_width(edge, max_volume):.1f}"{dash}>'
            f"<title>{escape(_edge_title(edge))}</title></path>"
        )
    for node in sorted(placed):
        x, y, h = placed[node]
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{_NODE_WIDTH}" height="{h:.1f}" '
            f'rx="3" fill="#0F6E56"><title>{escape(node)}</title></rect>'
        )
        parts.append(
            f'<text x="{x + _NODE_WIDTH + _LABEL_OFFSET:.1f}" y="{y + h / 2 + 4:.1f}" '
            f'font-family="system-ui, sans-serif" font-size="{_FONT_SIZE}" fill="#2C2C2A">'
            f"{escape(node)}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def render_page(report: FlowReport, *, client: str = "Client") -> str:
    """A single self-contained HTML file: the map, the note, the unmeasurable legend."""
    unmeasurable = [e for e in report.edges if e.status == UNMEASURABLE]
    note = escape(report.graph.cycle_note) if report.graph.cycle_note else ""
    rows = "".join(
        f"<li>{escape(e.origin)} -> {escape(e.destination)}: {e.n} movement(s)</li>"
        for e in unmeasurable
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(client)} flow map</title>"
        "<style>body{font-family:system-ui,sans-serif;margin:2rem;color:#2C2C2A;"
        "background:#fff;max-width:1100px}h1{font-weight:500;font-size:22px}"
        "p,li{font-size:14px;line-height:1.6}</style></head><body>"
        f"<h1>{escape(client)} - observed flow map</h1>"
        f"<p>{escape(report.summary)}</p>"
        + render_sankey(report, client=client)
        + (f"<p>{note}</p>" if note else "")
        + (
            "<p>Lanes with too few movements to measure transit (drawn dashed, excluded "
            f"from the ranking):</p><ul>{rows}</ul>" if unmeasurable else ""
        )
        + "</body></html>"
    )
