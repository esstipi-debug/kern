"""Export analysis results to CSV for Excel / Power BI — planned export layer."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.sanitize import defuse_formula


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if is_dataclass(value):
        return _flatten(asdict(value), prefix)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            nested = _flatten(item, f"{prefix}{key}_" if prefix else f"{key}_")
            out.update(nested)
        return out
    if prefix:
        return {prefix.rstrip("_"): value}
    return {"value": value}


def write_summary_csv(
    rows: list[dict[str, Any]],
    path: Path | str,
) -> Path:
    """Write one row per SKU/scenario to CSV."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    safe_rows = [{k: defuse_formula(v) for k, v in row.items()} for row in rows]
    pd.DataFrame(safe_rows).to_csv(out, index=False)
    return out


def write_policy_comparison(
    product_id: str,
    eoq: dict[str, float],
    sq: dict[str, float],
    rs: dict[str, float],
    simulation: dict[str, float] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a flat row for summary export."""
    row: dict[str, Any] = {
        "product_id": product_id,
        **{f"eoq_{k}": v for k, v in eoq.items()},
        **{f"sq_{k}": v for k, v in sq.items()},
        **{f"rs_{k}": v for k, v in rs.items()},
    }
    if simulation:
        row.update({f"sim_{k}": v for k, v in simulation.items()})
    if extra:
        row.update(extra)
    return row
