"""Regenerate docs/assets/capabilities-by-area.png from the README's tool table.

The README's "full list, by area" table is the source of the per-area counts; the
registry (`build_default_registry()`) is the source of truth for which tools exist.
The script fails if the two disagree, so the image can never drift silently again.
"""
from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scm_agent.tools import build_default_registry

ROOT = Path(__file__).resolve().parent.parent
COLORS = ["#2f6f68", "#3fb950", "#5eead4", "#d4a017", "#6b8cba", "#a67bb8",
          "#c0644c", "#7f8596", "#4a6fa5", "#8a6d3b", "#b0b0b0"]


def areas_from_readme() -> dict[str, list[str]]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    block = text.split("The full list, by area")[1].split("</details>")[0]
    out: dict[str, list[str]] = {}
    for line in block.splitlines():
        m = re.match(r"\| \*\*(.+?)\*\* \| (.+) \|$", line)
        if m:
            # tool names are the backticked tokens that start a cell item ("`x` (...)" or "`x` ·")
            names = [n for n in re.findall(r"(?:^|· )`([a-z_]+)`", m.group(2))]
            out[m.group(1)] = names
    return out


def main() -> None:
    areas = areas_from_readme()
    listed = {n for v in areas.values() for n in v}
    registered = {t.key for t in build_default_registry().list()}
    if listed != registered:
        raise SystemExit(f"README table != registry: only in README {sorted(listed - registered)}, "
                         f"only in registry {sorted(registered - listed)}")
    items = sorted(((k, len(v)) for k, v in areas.items()), key=lambda kv: -kv[1])
    total = sum(n for _, n in items)
    fig, ax = plt.subplots(figsize=(9.5, 6.5))
    ax.pie([n for _, n in items], colors=COLORS[: len(items)], startangle=90, counterclock=False,
           wedgeprops={"width": 0.4, "edgecolor": "white", "linewidth": 2})
    ax.text(0, 0.06, str(total), ha="center", va="center", fontsize=34, fontweight="bold", color="#1f2d3a")
    ax.text(0, -0.2, "tools", ha="center", va="center", fontsize=16, fontweight="bold", color="#1f2d3a")
    ax.legend([f"{k} ({n})" for k, n in items], loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=12)
    ax.set_title(f"What Kern covers — {total} registered capabilities by domain", loc="left", fontsize=15)
    fig.savefig(ROOT / "docs/assets/capabilities-by-area.png", dpi=110, bbox_inches="tight", facecolor="white")
    print(total, items)


if __name__ == "__main__":
    main()
