"""Contingent-fee pricing for the Sprint de Liquidacion package (capability M-E3).

The package's commercial promise is "cobramos solo un % de lo que realmente
recuperas" - the opposite of a fixed-scope retainer. This module is the single
place that turns a recovered-cash figure into a fee, and later turns an actual
post-liquidation sales file into a real-vs-estimated closing annex.

Two invariants the calculator enforces on every call (not just at the CLI):
  * ``fee_pct`` must stay in [MIN_FEE_PCT, MAX_FEE_PCT] (10-20%, the range
    the commercial brief authorizes) - never a silently-accepted number outside it.
  * the fee can never exceed the cash actually recovered - a contingent fee
    that costs the client more than it recovered is not "solo % de lo
    recuperado" anymore, it is a disguised fixed fee. ``floor`` raises a small
    recovery up to a minimum worth invoicing, but only up to what came back.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_FEE_PCT = 0.10
MAX_FEE_PCT = 0.20
DEFAULT_FEE_PCT = 0.15   # midpoint of the authorized 10-20% range
DEFAULT_FLOOR = 1500.0   # below this, a % fee is not worth invoicing separately


@dataclass(frozen=True)
class ContingentFee:
    """One fee computation, with enough detail to explain it to a client."""

    recovered_cash: float
    fee_pct: float
    floor: float
    fee: float
    floor_applied: bool          # the % alone would have been below the floor
    capped_by_recovered: bool    # even the floor exceeded what was recovered

    @property
    def effective_pct(self) -> float:
        """The fee as a share of recovered cash - never above 100% (fee is
        capped at recovered_cash; 0/0 -> 0.0, not NaN), but can run well
        ABOVE the negotiated fee_pct whenever the floor binds on a small
        recovery (e.g. fee_pct=0.10, floor=1500 on a 2,000 recovery ->
        effective_pct=0.75). The floor, not fee_pct, is what's capped by
        recovered_cash - see calculate_contingent_fee."""
        return (self.fee / self.recovered_cash) if self.recovered_cash > 0 else 0.0


def calculate_contingent_fee(
    recovered_cash: float,
    fee_pct: float = DEFAULT_FEE_PCT,
    floor: float = DEFAULT_FLOOR,
) -> ContingentFee:
    """fee = max(fee_pct * recovered_cash, floor), capped at recovered_cash.

    Zero (or negative-clamped-to-zero) recovery always yields a zero fee -
    a genuinely contingent fee cannot charge a floor on nothing recovered,
    that would just be a fixed fee wearing a percentage's clothes.
    """
    if not math.isfinite(recovered_cash) or recovered_cash < 0:
        raise ValueError(f"recovered_cash must be finite and >= 0, got {recovered_cash!r}")
    if not math.isfinite(fee_pct) or not MIN_FEE_PCT <= fee_pct <= MAX_FEE_PCT:
        raise ValueError(
            f"fee_pct must be in [{MIN_FEE_PCT}, {MAX_FEE_PCT}] (the authorized "
            f"contingent-fee range), got {fee_pct!r}"
        )
    if not math.isfinite(floor) or floor < 0:
        raise ValueError(f"floor must be finite and >= 0, got {floor!r}")

    if recovered_cash <= 0:
        return ContingentFee(
            recovered_cash=recovered_cash, fee_pct=fee_pct, floor=floor,
            fee=0.0, floor_applied=False, capped_by_recovered=False,
        )

    raw = fee_pct * recovered_cash
    floored = max(raw, floor)
    fee = min(floored, recovered_cash)
    return ContingentFee(
        recovered_cash=recovered_cash, fee_pct=fee_pct, floor=floor, fee=fee,
        floor_applied=floored > raw,
        capped_by_recovered=fee < floored,
    )


def render_fee_estimate(fee: ContingentFee, *, client: str = "Client") -> str:
    """The pre-engagement estimate annex: explicitly NOT an invoice."""
    lines = [
        "# Estimacion de honorarios - Sprint de Liquidacion",
        "",
        f"- **Cliente:** {client}",
        f"- **Cash recuperable estimado:** {fee.recovered_cash:,.0f}",
        f"- **% contingente:** {fee.fee_pct * 100:.0f}%",
        f"- **Piso configurado:** {fee.floor:,.0f}",
        f"- **Honorario estimado:** {fee.fee:,.0f} ({fee.effective_pct * 100:.1f}% efectivo)",
        "",
    ]
    if fee.capped_by_recovered:
        lines.append(
            "El recupero estimado esta por debajo del piso configurado - el "
            "honorario nunca supera el 100% de lo recuperado."
        )
    elif fee.floor_applied:
        lines.append(
            f"Se aplico el piso de {fee.floor:,.0f} (el {fee.fee_pct * 100:.0f}% solo daba menos) - "
            f"la tasa efectiva ({fee.effective_pct * 100:.1f}%) queda por encima del {fee.fee_pct * 100:.0f}% "
            "negociado en este recupero puntual."
        )
    lines += [
        "",
        "**ESTA ES UNA ESTIMACION, NO UNA FACTURA.** El honorario real se calcula "
        "sobre el cash efectivamente recuperado, medido al cierre del sprint "
        "(ver `--measure` / el anexo de cierre) - nunca se cobra sobre una "
        "proyeccion.",
    ]
    return "\n".join(lines) + "\n"


# ---- post-liquidation measurement: estimated vs. actually recovered ----------

@dataclass(frozen=True)
class SkuRecoveryMeasurement:
    product_id: str
    estimated_recovered: float
    actual_recovered: float
    variance: float       # actual - estimated (positive = beat the estimate)
    variance_pct: float   # variance / estimated; 0.0 when estimated == 0


@dataclass(frozen=True)
class MeasuredRecovery:
    lines: tuple[SkuRecoveryMeasurement, ...]
    total_estimated: float
    total_actual: float
    total_variance: float
    total_variance_pct: float
    estimated_fee: ContingentFee
    actual_fee: ContingentFee


def _validate_recovery_dict(values: dict[str, float], name: str) -> None:
    for pid, value in values.items():
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name}[{pid!r}] must be finite and >= 0, got {value!r}")


def measure_recovery(
    estimated_by_sku: dict[str, float],
    actual_by_sku: dict[str, float],
    *,
    fee_pct: float = DEFAULT_FEE_PCT,
    floor: float = DEFAULT_FLOOR,
) -> MeasuredRecovery:
    """Compare the liquidation plan's per-SKU estimate to real post-sprint sales.

    ``actual_by_sku`` need not cover every planned SKU (unsold lines measure as
    0 actual) and may include SKUs outside the plan (ignored - the contingent
    fee is scoped to what the plan targeted, not incidental sales elsewhere).

    Every value in both dicts must be finite and >= 0 - validated here (with
    the offending dict name and SKU in the message) rather than left to
    surface later as an opaque "recovered_cash" ValueError from
    ``calculate_contingent_fee``, which the caller of THIS function never
    passed directly. A garbled post-liquidation sales CSV is the realistic
    source of a NaN/negative value reaching this boundary.
    """
    _validate_recovery_dict(estimated_by_sku, "estimated_by_sku")
    _validate_recovery_dict(actual_by_sku, "actual_by_sku")
    lines = tuple(
        _measure_one(pid, est, actual_by_sku.get(pid, 0.0))
        for pid, est in sorted(estimated_by_sku.items())
    )
    total_estimated = sum(line.estimated_recovered for line in lines)
    total_actual = sum(line.actual_recovered for line in lines)
    total_variance = total_actual - total_estimated
    total_variance_pct = (total_variance / total_estimated) if total_estimated > 0 else 0.0
    return MeasuredRecovery(
        lines=lines, total_estimated=total_estimated, total_actual=total_actual,
        total_variance=total_variance, total_variance_pct=total_variance_pct,
        estimated_fee=calculate_contingent_fee(total_estimated, fee_pct, floor),
        actual_fee=calculate_contingent_fee(total_actual, fee_pct, floor),
    )


def _measure_one(product_id: str, estimated: float, actual: float) -> SkuRecoveryMeasurement:
    variance = actual - estimated
    variance_pct = (variance / estimated) if estimated > 0 else 0.0
    return SkuRecoveryMeasurement(
        product_id=product_id, estimated_recovered=estimated, actual_recovered=actual,
        variance=variance, variance_pct=variance_pct,
    )


def render_measurement_annex(measured: MeasuredRecovery, *, client: str = "Client") -> str:
    """The closing annex: estimated vs. actual recovery, and the REAL fee owed."""
    lines = [
        "# Anexo de cierre - Sprint de Liquidacion",
        "",
        f"- **Cliente:** {client}",
        f"- **Recupero estimado:** {measured.total_estimated:,.0f}",
        f"- **Recupero real:** {measured.total_actual:,.0f}",
        f"- **Variacion:** {measured.total_variance:+,.0f} ({measured.total_variance_pct * 100:+.1f}%)",
        "",
        "## Por SKU",
        "",
        "| SKU | Estimado | Real | Variacion |",
        "|---|---:|---:|---:|",
    ]
    for line in measured.lines:
        lines.append(
            f"| {line.product_id} | {line.estimated_recovered:,.0f} | "
            f"{line.actual_recovered:,.0f} | {line.variance:+,.0f} ({line.variance_pct * 100:+.0f}%) |"
        )
    lines += [
        "",
        "## Honorario",
        "",
        f"- Honorario estimado (pre-medicion): {measured.estimated_fee.fee:,.0f}",
        f"- **Honorario real (sobre el cash efectivamente recuperado): "
        f"{measured.actual_fee.fee:,.0f}**",
        "",
        "El honorario se factura sobre el honorario real de esta seccion, nunca "
        "sobre la estimacion inicial.",
    ]
    return "\n".join(lines) + "\n"
