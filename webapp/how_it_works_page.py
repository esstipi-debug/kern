"""Server-rendered HTML for GET /how-it-works.

An internal/onboarding page (not a sales page -- see
docs/superpowers/specs/2026-07-19-how-it-works-page-design.md): what Kern
does, its 41 capabilities under two donut-chart lenses (domain area / SCOR
Digital Standard process), how it's grounded and adapted per client, and its
alignment with SCOR/CPIM/CLTD/CSCP/SCPro/CPSM/ISO 9001/28000.

Reuses the SAME dark/teal visual system as webapp/stocky_alternative_page.py
(Inter + JetBrains Mono, --ink/--panel/--accent tokens) for visual
consistency across the site, but is otherwise a fully self-contained page
(its own <style> block, per this codebase's per-page convention -- see
paquetes_page.py/pricing_page.py/tower_page.py, none of which share a CSS
file either).

No charting library: donuts are hand-built inline SVG (stroke-dasharray/
stroke-dashoffset arcs). All interactivity (lens tabs, click-to-expand tool
lists, expandable cards/accordion) lives in webapp/static/how_it_works.js,
loaded via a <script src> tag -- confirmed compatible with the base CSP
(webapp/security.py's csp_for_path() only special-cases /console and
/static/prototype; this route gets the strict default, which already allows
'self'-hosted scripts).
"""

from __future__ import annotations

import math
from html import escape
from typing import Sequence

_DONUT_COLORS: tuple[str, ...] = (
    "#4fd1c5",  # accent
    "#5eead4",  # accent-bright
    "#f5b942",  # warn
    "#8b7cf6",
    "#f47174",
    "#63b3ed",
    "#68d391",
    "#f6ad55",
    "#b794f4",
)


def _donut_svg(
    segments: Sequence[tuple[str, int]],
    *,
    element_id: str,
    size: int = 240,
    stroke_width: int = 36,
) -> str:
    """Render an accessible inline SVG donut chart.

    `segments` is an ordered sequence of (label, count) pairs; count must be
    >= 0 and the total must be > 0. Each segment becomes one <circle> arc
    carrying data-label/data-count/data-pct attributes (read by
    static/how_it_works.js for click-to-filter) plus a native <title> so a
    hover tooltip works with zero JS.
    """
    if any(count < 0 for _, count in segments):
        raise ValueError("segment counts must be non-negative")
    total = sum(count for _, count in segments)
    if total <= 0:
        raise ValueError("donut segments must sum to a positive total")

    radius = (size - stroke_width) / 2
    circumference = 2 * math.pi * radius
    center = size / 2

    cumulative = 0.0
    arcs: list[str] = []
    for i, (label, count) in enumerate(segments):
        color = _DONUT_COLORS[i % len(_DONUT_COLORS)]
        fraction = count / total
        dash = fraction * circumference
        gap = circumference - dash
        offset = -cumulative
        cumulative += dash
        pct = round(fraction * 100)
        safe_label = escape(str(label))
        arcs.append(
            f'<circle class="donut-seg" data-label="{safe_label}" data-count="{escape(str(count))}" '
            f'data-pct="{escape(str(pct))}" tabindex="0" cx="{escape(f"{center:.3f}")}" cy="{escape(f"{center:.3f}")}" r="{escape(f"{radius:.3f}")}" '
            f'fill="none" stroke="{color}" stroke-width="{escape(str(stroke_width))}" '
            f'stroke-dasharray="{escape(f"{dash:.3f}")} {escape(f"{gap:.3f}")}" stroke-dashoffset="{escape(f"{offset:.3f}")}" '
            f'transform="rotate(-90 {escape(f"{center:.3f}")} {escape(f"{center:.3f}")})">'
            f"<title>{safe_label}: {escape(str(count))} ({escape(str(pct))}%)</title>"
            "</circle>"
        )

    return (
        f'<svg class="donut" id="{escape(element_id)}" viewBox="0 0 {escape(str(size))} {escape(str(size))}" '
        f'width="{escape(str(size))}" height="{escape(str(size))}" role="img" aria-label="Donut chart">'
        + "".join(arcs)
        + f'<text x="{escape(f"{center:.3f}")}" y="{escape(f"{center:.3f}")}" class="donut-total" text-anchor="middle" '
        f'dominant-baseline="middle">{escape(str(total))}</text>'
        + "</svg>"
    )
