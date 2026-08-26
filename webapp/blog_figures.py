"""Inline SVG figures for the blog: the diagrams a supply-chain reader expects,
plus a small warehouse icon set.

Why this exists at all: the articles are correct and completely mute. A page
about safety stock that never draws the saw-tooth is asking the reader to hold a
picture in their head that the whole field already agreed to draw.

Three rules this module enforces, because the alternative is decoration:

1. **Every figure is data the article already declares.** A figure is authored in
   the markdown as a fenced ``kern-figure`` block holding JSON. The renderer
   draws exactly those numbers -- it never invents, derives or "smooths" one.
2. **A ``label`` is mandatory.** Section 10 forbids unlabelled numbers, and a
   chart is the easiest place in a page to smuggle one. A figure without a label
   raises at load time.
3. **No JavaScript.** Animation is CSS keyframes on SVG geometry, wrapped in a
   ``prefers-reduced-motion`` guard. A crawler gets clean markup, the page keeps
   its LCP, and nothing depends on a script executing.

The icon set (pallet, box, shelf, truck, chart) is deliberately flat, single
stroke, drawn on the same 24x24 grid, and used at section scale. It is the one
place the module is decorative rather than informative, which is why it is small.
"""
from __future__ import annotations

import json
from html import escape
from typing import Any

# Palette lifted from the Stocky shell so figures read as part of the page.
_INK_LINE = "#1e2733"
_ACCENT = "#4fd1c5"
_ACCENT_BRIGHT = "#5eead4"
_WARN = "#f5b942"
_DANGER = "#f97362"
_MUTED = "#9aa7b6"
_FAINT = "#5e6b7a"
_TXT = "#e7eef6"

FIGURE_TYPES = ("sawtooth", "bars", "buckets", "icons")


class FigureError(ValueError):
    """A ``kern-figure`` block is malformed. Raised at load time."""


FIGURE_CSS = """
  .fig{margin:26px 0 22px;border:1px solid var(--line);border-radius:var(--r);
    background:var(--panel-2,#0f141d);padding:16px 16px 12px;overflow:hidden}
  .fig svg{display:block;width:100%;height:auto}
  .fig figcaption{margin:10px 0 0;font:500 12px/1.55 var(--mono);color:var(--faint)}
  .fig .t{font:600 11px/1 var(--mono);fill:#9aa7b6;letter-spacing:.04em}
  .fig .tv{font:700 13px/1 var(--mono);fill:#e7eef6}
  .fig .tb{font:700 12px/1 var(--mono);fill:#080b11}
  .ico{display:inline-block;vertical-align:-3px;margin-right:8px}
  .ico svg{width:20px;height:20px;stroke:var(--accent);fill:none;
    stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
  .iconrow{display:flex;gap:26px;flex-wrap:wrap;align-items:center;justify-content:center;padding:4px 0 2px}
  .iconrow figure{margin:0;text-align:center}
  .iconrow svg{width:38px;height:38px;stroke:var(--accent);fill:none;stroke-width:1.4;
    stroke-linecap:round;stroke-linejoin:round}
  .iconrow span{display:block;margin-top:7px;font:500 11px/1.3 var(--mono);color:var(--faint)}

  /* Motion. Everything is a stroke reveal or a scale from the baseline, so a
     reduced-motion reader sees the finished figure, never a broken one. */
  @media (prefers-reduced-motion: no-preference){
    .fig .draw{stroke-dasharray:var(--len,1200);stroke-dashoffset:var(--len,1200);
      animation:figdraw 1.9s cubic-bezier(.6,.02,.2,1) forwards}
    .fig .draw.d2{animation-delay:.35s}
    .fig .bar{transform:scaleY(0);transform-origin:bottom;animation:figbar .8s cubic-bezier(.2,.7,.3,1) forwards}
    .fig .seg{transform:scaleX(0);transform-origin:left;animation:figseg .9s cubic-bezier(.2,.7,.3,1) forwards}
    .fig .fade{opacity:0;animation:figfade .5s ease forwards}
    .iconrow svg .roll{animation:figroll 3.2s linear infinite}
  }
  @keyframes figdraw{to{stroke-dashoffset:0}}
  @keyframes figbar{to{transform:scaleY(1)}}
  @keyframes figseg{to{transform:scaleX(1)}}
  @keyframes figfade{to{opacity:1}}
  @keyframes figroll{0%{transform:translateX(0)}50%{transform:translateX(3px)}100%{transform:translateX(0)}}
"""

# --------------------------------------------------------------------------- #
# Icons: one 24x24 grid, single stroke.
# --------------------------------------------------------------------------- #

_ICONS: dict[str, str] = {
    "box": '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
    "pallet": '<path d="M4 6h16v6H4z"/><path d="M8 6v6M16 6v6"/><path d="M3 16h18M5 16v3M12 16v3M19 16v3"/>',
    "shelf": '<path d="M3 4h18v16H3z"/><path d="M3 9.3h18M3 14.6h18"/><path d="M7.5 4v5.3M14 9.3v5.3"/>',
    "truck": '<g class="roll"><path d="M2 6h11v9H2z"/><path d="M13 9h4l3 3v3h-7z"/>'
             '<circle cx="6" cy="17.5" r="1.8"/><circle cx="16.5" cy="17.5" r="1.8"/></g>',
    "chart": '<path d="M3 3v18h18"/><path d="M7 15l3.5-4 3 2.5L20 7"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5.3l3.4 2"/>',
}

_ICON_LABELS = {
    "es": {"box": "SKU", "pallet": "Stock", "shelf": "Bodega", "truck": "Lead time",
           "chart": "Demanda", "clock": "Cobertura"},
    "en": {"box": "SKU", "pallet": "On hand", "shelf": "Warehouse", "truck": "Lead time",
           "chart": "Demand", "clock": "Cover"},
}


def icon(name: str) -> str:
    """A single inline icon, sized for running text or a heading."""
    path = _ICONS.get(name)
    if path is None:
        raise FigureError(f"unknown icon {name!r}; have {sorted(_ICONS)}")
    return f'<span class="ico" aria-hidden="true"><svg viewBox="0 0 24 24">{path}</svg></span>'


def _icons_row(spec: dict[str, Any], lang: str) -> str:
    names = spec.get("names") or ["box", "pallet", "shelf", "truck", "chart", "clock"]
    labels = _ICON_LABELS.get(lang, _ICON_LABELS["en"])
    cells = []
    for n in names:
        if n not in _ICONS:
            raise FigureError(f"unknown icon {n!r}")
        cells.append(
            f'<figure><svg viewBox="0 0 24 24" aria-hidden="true">{_ICONS[n]}</svg>'
            f"<span>{escape(str(spec.get('labels', {}).get(n, labels.get(n, n))))}</span></figure>"
        )
    return f'<div class="iconrow">{"".join(cells)}</div>'


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #

def _sawtooth(spec: dict[str, Any]) -> str:
    """The canonical inventory saw-tooth: stock drawn down, replenished at the
    reorder point, with the safety-stock band underneath.

    Reads ``safety`` and ``reorder`` as unit levels and ``peak`` as the top of
    the cycle. Geometry only: the shape is drawn from those three numbers, so a
    reader can check the picture against the article's own figures.
    """
    peak = float(spec["peak"]); reorder = float(spec["reorder"]); safety = float(spec["safety"])
    if not (peak > reorder >= safety >= 0):
        raise FigureError("sawtooth needs peak > reorder >= safety >= 0")
    W, H, PAD_L, PAD_B, PAD_T = 640, 240, 46, 28, 14
    plot_h = H - PAD_B - PAD_T
    y = lambda v: PAD_T + plot_h - (v / peak) * plot_h  # noqa: E731
    cycles, x0, x1 = 3, PAD_L, W - 12
    span = (x1 - x0) / cycles

    pts: list[str] = []
    for c in range(cycles):
        sx = x0 + c * span
        pts.append(f"{sx:.1f},{y(peak):.1f}")
        pts.append(f"{sx + span * 0.72:.1f},{y(reorder):.1f}")
        pts.append(f"{sx + span * 0.92:.1f},{y(safety * 1.06):.1f}")
        pts.append(f"{sx + span * 0.92:.1f},{y(peak):.1f}")
    line = " ".join(pts)

    return f"""<svg viewBox="0 0 {W} {H}" role="img">
<rect x="{PAD_L}" y="{y(safety):.1f}" width="{x1 - PAD_L:.1f}" height="{PAD_T + plot_h - y(safety):.1f}"
      fill="{_ACCENT}" opacity=".10"/>
<line x1="{PAD_L}" y1="{y(safety):.1f}" x2="{x1}" y2="{y(safety):.1f}"
      stroke="{_ACCENT}" stroke-width="1" stroke-dasharray="4 4"/>
<line x1="{PAD_L}" y1="{y(reorder):.1f}" x2="{x1}" y2="{y(reorder):.1f}"
      stroke="{_WARN}" stroke-width="1" stroke-dasharray="4 4"/>
<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{x1}" y2="{PAD_T + plot_h}" stroke="{_INK_LINE}" stroke-width="1"/>
<polyline class="draw" style="--len:2600" points="{line}"
          fill="none" stroke="{_ACCENT_BRIGHT}" stroke-width="2"
          stroke-linejoin="round" stroke-linecap="round"/>
<text class="t" x="4" y="{y(reorder) + 4:.1f}" fill="{_WARN}">{escape(str(spec.get('reorder_label', 's')))}</text>
<text class="t" x="4" y="{y(safety) + 4:.1f}" fill="{_ACCENT}">{escape(str(spec.get('safety_label', 'Ss')))}</text>
<text class="tv" x="{x1}" y="{y(reorder) - 8:.1f}" text-anchor="end">{escape(str(spec['reorder']))}</text>
<text class="tv" x="{x1}" y="{y(safety) - 8:.1f}" text-anchor="end">{escape(str(spec['safety']))}</text>
</svg>"""


def _bars(spec: dict[str, Any]) -> str:
    """A labelled bar comparison, with one bar optionally called out.

    This is the picture behind "the field says 21 and the last 40 shipments
    average 34": a reader who sees the declared marker sitting off the left tail
    understands the whole argument before reading a formula.
    """
    bins: list[list[Any]] = spec["bins"]  # [[label, count], ...]
    highlight = spec.get("highlight")
    W, H, PAD_L, PAD_B, PAD_T = 640, 220, 34, 34, 16
    plot_h = H - PAD_B - PAD_T
    top = max(int(b[1]) for b in bins) or 1
    bw = (W - PAD_L - 14) / len(bins)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    for i, (label, count) in enumerate(bins):
        h = (int(count) / top) * plot_h
        x = PAD_L + i * bw
        hit = highlight is not None and str(label) == str(highlight)
        out.append(
            f'<rect class="bar" style="animation-delay:{i * 55}ms" x="{x + 3:.1f}" '
            f'y="{PAD_T + plot_h - h:.1f}" width="{bw - 6:.1f}" height="{max(h, 1):.1f}" rx="2" '
            f'fill="{_WARN if hit else _ACCENT}" opacity="{"1" if hit else ".72"}"/>'
        )
        out.append(
            f'<text class="t" x="{x + bw / 2:.1f}" y="{H - 14}" text-anchor="middle">{escape(str(label))}</text>'
        )
    out.append(
        f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - 14}" y2="{PAD_T + plot_h}" '
        f'stroke="{_INK_LINE}" stroke-width="1"/>'
    )
    if highlight is not None:
        idx = next((i for i, b in enumerate(bins) if str(b[0]) == str(highlight)), None)
        if idx is not None:
            x = PAD_L + idx * bw + bw / 2
            out.append(
                f'<g class="fade" style="animation-delay:.9s">'
                f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{PAD_T + plot_h}" '
                f'stroke="{_WARN}" stroke-width="1.5" stroke-dasharray="3 3"/>'
                f'<text class="t" x="{x + 6:.1f}" y="{PAD_T + 10}" fill="{_WARN}">'
                f'{escape(str(spec.get("highlight_label", "")))}</text></g>'
            )
    out.append("</svg>")
    return "".join(out)


def _buckets(spec: dict[str, Any]) -> str:
    """Dead / excess / healthy as one bar, sized by at-risk cash rather than by
    units -- which is the point the excess-obsolete pair is making."""
    items: list[dict[str, Any]] = spec["items"]  # [{label, value, tone}]
    total = sum(float(i["value"]) for i in items) or 1.0
    W, H = 640, 108
    tones = {"dead": _DANGER, "excess": _WARN, "healthy": _ACCENT}
    out = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    x = 0.0
    for n, it in enumerate(items):
        w = (float(it["value"]) / total) * W
        colour = tones.get(str(it.get("tone", "healthy")), _MUTED)
        out.append(
            f'<g class="seg" style="animation-delay:{n * 130}ms; transform-origin:{x:.1f}px center">'
            f'<rect x="{x:.1f}" y="18" width="{max(w - 3, 2):.1f}" height="34" rx="4" fill="{colour}"/>'
            f'</g>'
        )
        if w > 74:
            out.append(f'<text class="tb" x="{x + 10:.1f}" y="39">{escape(str(it["label"]))}</text>')
        out.append(
            f'<text class="t" x="{x:.1f}" y="70">{escape(str(it.get("units", "")))}</text>'
            f'<text class="tv" x="{x:.1f}" y="88">{escape(str(it.get("cash", "")))}</text>'
        )
        x += w
    out.append("</svg>")
    return "".join(out)


_RENDERERS = {"sawtooth": _sawtooth, "bars": _bars, "buckets": _buckets}


def render_figure(raw: str, lang: str = "en") -> str:
    """Turn a ``kern-figure`` fenced block into an inline SVG ``<figure>``."""
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FigureError(f"kern-figure block is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise FigureError("kern-figure must be a JSON object")

    kind = spec.get("type")
    if kind not in FIGURE_TYPES:
        raise FigureError(f"kern-figure type {kind!r} not in {FIGURE_TYPES}")

    label = str(spec.get("label", "")).strip()
    if not label:
        # The rule that makes a chart safe in this repo: a figure draws numbers,
        # and an unlabelled number is exactly what section 10 forbids.
        raise FigureError(f"kern-figure ({kind}) has no label; every figure must carry its provenance")

    svg = _icons_row(spec, lang) if kind == "icons" else _RENDERERS[kind](spec)
    return f'<figure class="fig">{svg}<figcaption>{escape(label)}</figcaption></figure>'
