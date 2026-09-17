"""Objection verdict for one area over one decision change (spec 2026-07-31).

Pure: frozen dataclasses + pure functions, no pandas, no I/O, no scm_agent
imports. Knows nothing about SKUs or demand - it takes one area's KPI at two
points and the operator's declared tolerance, and says whether that area would
object.

Two rules that are easy to get wrong and are therefore explicit here:

1. Direction is NOT uniform. Three hats minimize a cost-like KPI; `comercial`
   maximizes fill rate. A plain ``abs(delta) > threshold`` would report an
   objection when the CFO's capital charge FALLS.
2. An area can be un-judgeable for TWO different reasons, and both must land on
   NO_EVALUABLE rather than on a quiet `acepta`. `judge(threshold=None)` is the
   operator never declaring how much deterioration the area tolerates;
   `unmeasurable()` is there being no KPI to compare at all (transporte on a SKU
   with no freight profile). Same status, different `reason` -- and the caller
   must produce one verdict per area in `src.hats.HAT_KEYS`, not one per area it
   happened to be able to score.

3. The planner's headline KPI does not capture its own service constraint.
   ``src.hats.hat_kpis`` returns ``policy_cost = -_u_planner_valid(...)``, the
   RAW policy cost; the ``sl_target`` penalty lives in ``src.hats.utilities``,
   not here. So a proposal that drops the service level below target makes the
   planner's KPI CHEAPER. ``breaches_sl_target`` is the caller-supplied flag
   that forces an objection in that case, regardless of the delta.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.hats import HAT_COMERCIAL, HAT_KEYS, HAT_PLANNER, headline_kpi

ACEPTA = "acepta"
OBJETA = "objeta"
NO_EVALUABLE = "no_evaluable"

# The only hat whose headline KPI is better when it goes UP.
MAXIMIZED_HATS = frozenset({HAT_COMERCIAL})


@dataclass(frozen=True)
class Verdict:
    """One area's stance on a proposed change.

    Every optional field is ``None`` - never 0.0 - when the verdict is
    NO_EVALUABLE: a declared gap, not a fabricated number.
    """

    hat_key: str
    status: str
    kpi_name: str
    kpi_current: float | None
    kpi_proposed: float | None
    deterioration: float | None
    threshold: float | None
    reason: str


def unmeasurable(hat_key: str, reason: str) -> Verdict:
    """NO_EVALUABLE for an area whose KPI could not be computed at all.

    Every optional field stays None, exactly as in the no-threshold case: the
    point of this constructor is that a gap keeps its shape no matter which of
    the two gaps it is. `reason` must say WHICH input is missing -- a caller that
    writes "not evaluable" and stops has moved the question, not answered it.
    """
    _require_known_hat(hat_key)
    if not reason.strip():
        raise ValueError("unmeasurable requires a reason naming the missing input")
    return Verdict(
        hat_key=hat_key, status=NO_EVALUABLE, kpi_name=headline_kpi(hat_key),
        kpi_current=None, kpi_proposed=None, deterioration=None, threshold=None,
        reason=reason,
    )


def _require_known_hat(hat_key: str) -> None:
    if hat_key not in HAT_KEYS:
        raise ValueError(f"unknown hat: {hat_key!r} (expected one of {HAT_KEYS})")


def _require_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")


def deterioration(hat_key: str, kpi_current: float, kpi_proposed: float) -> float:
    """How much worse the proposal is for this hat, in the KPI's own units.

    Positive = worse. Sign flips for the maximized hats so that "worse" always
    reads the same way downstream.
    """
    _require_known_hat(hat_key)
    _require_finite(kpi_current, "kpi_current")
    _require_finite(kpi_proposed, "kpi_proposed")
    if hat_key in MAXIMIZED_HATS:
        return kpi_current - kpi_proposed
    return kpi_proposed - kpi_current


def judge(
    hat_key: str,
    kpi_current: float,
    kpi_proposed: float,
    threshold: float | None,
    *,
    breaches_sl_target: bool = False,
) -> Verdict:
    """Verdict for one hat. ``threshold`` None => NO_EVALUABLE (never a default).

    ``threshold`` is INCLUSIVE: a deterioration exactly equal to it accepts.
    """
    _require_known_hat(hat_key)
    kpi_name = headline_kpi(hat_key)

    if threshold is None:
        return Verdict(
            hat_key=hat_key, status=NO_EVALUABLE, kpi_name=kpi_name,
            kpi_current=None, kpi_proposed=None, deterioration=None, threshold=None,
            reason=(f"{hat_key}: sin umbral de objecion declarado en el perfil del "
                    "cliente - esta area no es evaluable"),
        )

    _require_finite(threshold, "threshold")
    delta = deterioration(hat_key, kpi_current, kpi_proposed)

    if hat_key == HAT_PLANNER and breaches_sl_target:
        status = OBJETA
        reason = ("planner: el nivel de servicio propuesto cae bajo config.sl_target - "
                  "objeta por restriccion de servicio, no por el delta de policy_cost "
                  f"({delta:+,.2f})")
    elif delta > threshold:
        status = OBJETA
        reason = (f"{hat_key}: {kpi_name} empeora {delta:,.4f}, sobre el umbral "
                  f"declarado {threshold:,.4f}")
    else:
        status = ACEPTA
        reason = (f"{hat_key}: {kpi_name} varia {delta:+,.4f}, dentro del umbral "
                  f"declarado {threshold:,.4f}")

    return Verdict(
        hat_key=hat_key, status=status, kpi_name=kpi_name,
        kpi_current=kpi_current, kpi_proposed=kpi_proposed,
        deterioration=delta, threshold=threshold, reason=reason,
    )
