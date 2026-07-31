"""cross_impact playbook: one proposed decision -> the measured effect on the
other areas + an objection verdict (spec docs/superpowers/specs/2026-07-31-
cross-impact-design.md).

Scope boundary vs the two hat tools (READ BEFORE TOUCHING EITHER). ``hat_tension``
answers "where is the disagreement?" - it computes each hat's IDEAL from the grid
and never judges whether a magnitude is tolerable. This module answers a different
question: an area has ALREADY proposed a change, and we measure what it does to
the others and whether any of them would object, against a threshold the operator
declared in the client profile. Neither module imports the other beyond reusing
``hats_job.prepare`` for the CSV read.
"""

from __future__ import annotations

from src.hats import HAT_KEYS, Candidate

from . import hats_job

_POINT_KEYS = ("q", "sl")


def _parse_point(raw: object, name: str) -> Candidate:
    """Turn a {'q': ..., 'sl': ...} mapping into a Candidate, or raise."""
    if raw is None:
        raise ValueError(
            f"'{name}' is required: pass {{'q': <order quantity>, 'sl': <service "
            "level>}}. There is no default - assuming one would state a policy "
            "nobody verified.")
    if not isinstance(raw, dict):
        raise ValueError(f"'{name}' must be a mapping with keys {_POINT_KEYS}, got {type(raw).__name__}")
    missing = [k for k in _POINT_KEYS if k not in raw]
    if missing:
        raise ValueError(f"'{name}' is missing {', '.join(missing)} (needs {_POINT_KEYS})")
    try:
        return Candidate(order_quantity=float(raw["q"]), service_level=float(raw["sl"]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' is not a valid decision point: {exc}") from exc


def _parse_thresholds(raw: object) -> dict[str, float]:
    """Validate the per-area thresholds. Absent/empty is LEGAL - those areas
    come out NO_EVALUABLE rather than taking an assumed tolerance."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("'objection_thresholds' must be a mapping of area -> tolerance")
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in HAT_KEYS:
            raise ValueError(
                f"objection_thresholds key {key!r} is not a known area (expected one of {HAT_KEYS})")
        try:
            num = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"objection_thresholds[{key!r}] must be a number: {exc}") from exc
        if not num > 0:
            raise ValueError(f"objection_thresholds[{key!r}] must be > 0, got {num}")
        out[key] = num
    return out


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Build the single-SKU payload. Every gap raises ValueError - the tool
    wrapper turns that into needs_clarification, never a default."""
    params = params or {}

    sku = params.get("sku")
    if not sku:
        raise ValueError("'sku' is required: cross_impact evaluates ONE decision at a time")

    proposed_by = params.get("proposed_by")
    if proposed_by not in HAT_KEYS:
        raise ValueError(
            f"'proposed_by' is required and must be one of {HAT_KEYS}, got {proposed_by!r}")

    current = _parse_point(params.get("current"), "current")
    proposed = _parse_point(params.get("proposed"), "proposed")
    if current == proposed:
        raise ValueError(
            "'current' and 'proposed' are the same point - no hay cambio que evaluar")

    thresholds = _parse_thresholds(params.get("objection_thresholds"))

    # Reuse hats_job's CSV read wholesale; params['sku'] narrows it to one SKU.
    payload = hats_job.prepare(data_path, params)
    inputs = payload["inputs"]
    if len(inputs) != 1:
        raise ValueError(
            f"expected exactly one usable SKU for {sku!r}, got {len(inputs)}")

    return {
        "inputs": inputs[0],
        "current": current,
        "proposed": proposed,
        "proposed_by": proposed_by,
        "thresholds": thresholds,
        "config": payload["config"],
    }
