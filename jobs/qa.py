"""Automated QA — verify a JobReport's numbers before it reaches a client.

The first gate of the human-in-the-loop model: catch any internal inconsistency
(bad investment math, infeasible-but-flagged-feasible, negative safety stock,
out-of-range allocation) so the human only reviews sound output.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from nosilo_core.guided import EXECUTED, GuidedOutcome, verify_guided
from src.constraints import apply_order_rules, is_feasible_lot
from src.forecasting import MIN_PERIODS_FOR_POLICY
from src.sop_engine.coherence import CHECK_BUDGET_FEASIBILITY, CHECK_PROMO_COVERAGE, CHECK_SERVICE_LEVEL

from . import forecast_job
from .inventory_optimization import (
    MIN_PERIODS_FOR_COVER,
    PROVISIONAL_POLICY_KINDS,
    UNSIZED_POLICY_KINDS,
    JobReport,
)
from .leadership import DIMS, ChainProfile
from .pricing import PricingReport

if TYPE_CHECKING:  # avoid a real circular import: jobs.digest_job -> scm_agent.events
    # -> scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    # `from __future__ import annotations` above already makes the verify_digest type
    # hint a lazy string, so this import only ever runs for a type checker, never at
    # module load time.
    from .digest_job import DigestResult

    # Same hazard, same fix: jobs.integrated_plan -> scm_agent.knowledge/citation_gate ->
    # scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    from .integrated_plan import IntegratedPlanBundle

    # Same hazard, same fix: jobs.price_intelligence -> src.pricing_intel.sanity ->
    # scm_agent.events -> scm_agent (package __init__) -> scm_agent.tools -> here.
    from .price_intelligence import PriceIntelReport

    # Same hazard, same fix: jobs.price_watch -> scm_agent.events ->
    # scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    from .price_watch import PriceWatchCycleReport

TOL = 1e-6
PRICE_INTEL_COVERAGE_MIN = 0.60  # plan section 6.9 item 2: ">=60% SKUs con >=1 competidor confirmado"

#: policy_kinds that carry NO quantity by design -- the engine declined to size them
#: (degenerate demand, degenerate cost, or too little history). Everything else is a
#: "priced" line: a number an operator is being asked to act on, and therefore subject
#: to the evidence invariants in verify() below.
UNPRICED_POLICY_KINDS = UNSIZED_POLICY_KINDS

#: Los campos que describen una politica dimensionada. jobs/inventory_optimization.py
#: los emite en ``None`` exactamente cuando no pudo dimensionarla.
SIZED_POLICY_FIELDS = ("forecast", "reorder_point", "safety_stock", "z_factor")

#: La unica policy_kind sin precio cuyos CEROS son una medicion y no una ausencia:
#: la demanda agregada se midio y dio cero, asi que un pronostico de 0 y un
#: disparador de 0 son lo que el motor calculo. Las otras dos no calcularon nada.
MEASURED_ZERO_POLICY_KINDS = frozenset({"skipped_zero_demand"})

#: La forma EXACTA de una cobertura provisional: trae disparador y objetivo, y NO trae
#: pronostico, buffer ni z. No es una tercera laxitud sino una tercera forma, y se
#: chequea entera en las dos direcciones -- que traiga lo que tiene que traer y que no
#: traiga lo que no puede traer. Si algun dia una cobertura sale con `safety_stock`, es
#: que alguien la dimensiono con una sigma que no existe.
COVER_FIELDS_PRESENT = ("reorder_point",)
COVER_FIELDS_ABSENT = ("forecast", "safety_stock", "z_factor")
_REQUIRED_INTEGRATED_PLAN_CHECKS = {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}


def _sized_field_issues(r) -> list[str]:
    """La ausencia de politica se chequea en las DOS direcciones.

    Hasta el 2026-09-19 una negativa del motor salia con `reorder_point`,
    `safety_stock`, `z_factor` y `forecast` en 0.0, y el aviso viajaba por otro
    canal (`policy_kind`, un residual). Todo consumidor que leia el NUMERO leia lo
    contrario de lo que decia la prosa: `src/simworld/runners.py` lo ejecutaba como
    "no pedir nunca", el Tower emitia `dead_stock` sobre un SKU que vendia todas las
    semanas, y `scm_agent/package_specs.py` lo enhebraba en la PLANILLA DE PEDIDOS
    del cliente como `kern_optimized` con disparador cero. Un 0 significa "medido, y
    la respuesta es ninguno"; una negativa no midio nada.

    Tres formas de mentir, y las tres estan prohibidas aca:
      1. una linea sin politica que traiga un numero;
      2. una linea con politica a la que le falte uno -- sin esto, "arreglar" el
         defecto anulando todo pasaria la compuerta y dejaria sin politica a los SKU
         que si se dimensionaron;
      3. una linea de demanda medida en cero que MEZCLE ceros con nulos. Esa clase
         tiene dos formas legitimas -- los cuatro campos en 0.0 cuando la demanda
         anual dio exactamente cero, y los cuatro en None cuando dio negativa, que
         no es la medicion de nada -- y ninguna a medias.
    """
    valores = {campo: getattr(r, campo, None) for campo in SIZED_POLICY_FIELDS}

    if r.policy_kind in PROVISIONAL_POLICY_KINDS:
        out = [
            f"{r.product_id}: {r.policy_kind} has no {campo}; a provisional cover "
            "still has to say how much to hold"
            for campo in COVER_FIELDS_PRESENT if getattr(r, campo, None) is None
        ]
        out += [
            f"{r.product_id}: {r.policy_kind} carries a {campo} of "
            f"{getattr(r, campo):.4g} - a cover has no dispersion behind it, so "
            "presenting one as if it did is the confident-but-wrong claim the "
            "minimum-history gate exists to stop"
            for campo in COVER_FIELDS_ABSENT if getattr(r, campo, None) is not None
        ]
        # Y la aritmetica que el entregable publica tiene que cerrar: la cobertura es
        # la media medida por los periodos de riesgo, no un numero suelto al lado.
        media, tau = r.observed_mean_per_period, r.cover_periods
        if media is None or tau is None:
            out.append(
                f"{r.product_id}: {r.policy_kind} without the mean and the risk "
                "periods it was derived from; the deliverable cannot show its work"
            )
        elif r.reorder_point is not None:
            esperada = media * tau
            # Con el tope por fecha de fin la cuenta cierra en DOS pasos y el registro
            # trae los dos: la cobertura de antes del tope es media x tau, y la publicada
            # es la menor entre esa y lo que queda por vender. Exigir media x tau sobre la
            # topeada tumbaba la corrida entera (revision del PR #369, 2026-10-09).
            if getattr(r, "horizon_verdict", None) == "applied":
                antes = getattr(r, "reorder_point_before_horizon", None)
                if antes is None or abs(antes - esperada) > max(TOL, abs(antes) * 1e-9):
                    out.append(
                        f"{r.product_id}: capped provisional cover does not carry the "
                        f"observed mean {media:.4g} over {tau:.4g} risk period(s) it came from"
                    )
                restante = getattr(r, "remaining_demand", None)
                if restante is not None:
                    esperada = min(esperada, restante)
            if abs(r.reorder_point - esperada) > max(TOL, abs(r.reorder_point) * 1e-9):
                out.append(
                    f"{r.product_id}: provisional cover {r.reorder_point:.4g} is not the "
                    f"observed mean {media:.4g} over {tau:.4g} risk period(s)"
                )
        return out

    if r.policy_kind not in UNPRICED_POLICY_KINDS:
        return [
            f"{r.product_id}: {r.policy_kind} has no {campo}; only a declined SKU "
            "may leave a sized field null"
            for campo, valor in valores.items() if valor is None
        ]

    if r.policy_kind not in MEASURED_ZERO_POLICY_KINDS:
        return [
            f"{r.product_id}: {r.policy_kind} carries a {campo} of {valor:.4g} - "
            "the engine declined to size this SKU, so the absence must be null, "
            "not a number an operator or a downstream job can act on"
            for campo, valor in valores.items() if valor is not None
        ]

    nulos = sorted(c for c, v in valores.items() if v is None)
    if nulos and len(nulos) != len(valores):
        return [
            f"{r.product_id}: {r.policy_kind} mixes measured zeros with nulls "
            f"({', '.join(nulos)} absent) - the zeros of this kind are a measurement, "
            "so either all four are measured or none of them is"
        ]
    return [
        f"{r.product_id}: {r.policy_kind} carries a {campo} of {valor:.4g} - a SKU "
        "whose demand measured zero has a zero policy, not a positive one"
        for campo, valor in valores.items() if valor is not None and valor != 0.0
    ]


def verify(report: JobReport) -> list[str]:
    """Return a list of QA issues. Empty list = passed."""
    issues: list[str] = []

    if not report.recommendations:
        issues.append("report has no SKU recommendations")

    for r in report.recommendations:
        if abs(r.investment - (r.cycle_investment + r.ss_investment)) > max(TOL, abs(r.investment) * 1e-9):
            issues.append(f"{r.product_id}: investment != cycle + safety")
        issues.extend(_sized_field_issues(r))
        if r.safety_stock is not None and r.safety_stock < -TOL:
            issues.append(f"{r.product_id}: negative safety stock")
        if r.reorder_point is not None and r.reorder_point < -TOL:
            issues.append(f"{r.product_id}: negative reorder point")
        if r.policy_kind == "(s, Q)" and (r.order_quantity is None or r.order_quantity <= 0):
            issues.append(f"{r.product_id}: (s,Q) without a positive order quantity")
        issues.extend(_lot_issues(r))
        issues.extend(_position_issues(r))
        issues.extend(_horizon_issues(r))
        if r.policy_kind == "(R, S)" and (r.order_up_to is None or r.order_up_to <= 0):
            issues.append(f"{r.product_id}: (R,S) without a positive order-up-to level")
        # the lead-only reorder must stay below order-up-to for (R,S)
        if r.policy_kind == "(R, S)" and r.order_up_to is not None and r.reorder_point > r.order_up_to + TOL:
            issues.append(f"{r.product_id}: reorder point exceeds order-up-to level")

        # ---- evidence invariants: does this number rest on anything? ----
        # These are the second line behind jobs/inventory_optimization.py's
        # minimum-history gate. The gate can be bypassed (a caller lowering
        # min_periods, a future refactor dropping the branch, a report assembled
        # by hand); these cannot, because they are checked on the record itself.
        if r.policy_kind in PROVISIONAL_POLICY_KINDS:
            # No se la exime de la invariante de evidencia: se le aplica la SUYA. Una
            # cobertura descansa en una media, y una media necesita al menos dos
            # observaciones -- por debajo de eso el numero es una sola venta con otro
            # nombre.
            if getattr(r, "n_periods", 0) < MIN_PERIODS_FOR_COVER:
                issues.append(
                    f"{r.product_id}: provisional cover built on "
                    f"{getattr(r, 'n_periods', 0)} period(s) of demand history, below "
                    f"the {MIN_PERIODS_FOR_COVER}-period minimum for an average"
                )
        elif r.policy_kind not in UNPRICED_POLICY_KINDS:
            n_periods = getattr(r, "n_periods", 0)
            if n_periods < MIN_PERIODS_FOR_POLICY:
                issues.append(
                    f"{r.product_id}: priced policy built on {n_periods} period(s) of demand "
                    f"history, below the {MIN_PERIODS_FOR_POLICY}-period minimum"
                )

        # sigma_source 'unavailable' means NO dispersion was estimable. A positive
        # buffer cannot come from that - z_alpha * sigma * sqrt(tau) with no sigma
        # is a fabricated number. The converse (unavailable + zero buffer) is the
        # legitimate measured-flat-history case and must keep passing: see
        # src/forecasting.py's to_engine_inputs "measured, not missing" branch.
        if (getattr(r, "sigma_source", "") == "unavailable"
                and r.safety_stock is not None and r.safety_stock > TOL):
            issues.append(
                f"{r.product_id}: safety stock {r.safety_stock:.4g} with no estimable "
                "dispersion (sigma_source='unavailable')"
            )

    if not (0.0 - TOL <= report.safety_stock_scale <= 1.0 + TOL):
        issues.append(f"safety_stock_scale out of [0,1]: {report.safety_stock_scale}")
    if report.final_investment > report.requested_investment + max(TOL, report.requested_investment * 1e-9):
        issues.append("final investment exceeds requested")

    if report.budget is not None:
        if report.feasible and report.final_investment > report.budget + 1.0:
            issues.append("flagged feasible but final investment exceeds budget")
        if not report.feasible and report.cycle_floor <= report.budget + TOL:
            issues.append("flagged infeasible but cycle-stock floor fits the budget")

    # `requested_investment` es el PLAN OPTIMIZADO, y una cobertura provisional no
    # entra en el: no pasa por el allocator de presupuesto porque no tiene buffer que
    # escalar. Su capital se reporta aparte (`provisional_investment`) y se resta aca,
    # para que la invariante siga comparando dos cosas que son la misma cosa. Sumarlo
    # al titular haria parecer dimensionado lo que no lo esta; omitirlo del reporte
    # esconderia unidades que alguien va a comprar.
    sku_sum = sum(
        r.investment for r in report.recommendations
        if r.policy_kind not in PROVISIONAL_POLICY_KINDS
    )
    if report.budget is None and abs(sku_sum - report.requested_investment) > max(1.0, sku_sum * 1e-6):
        issues.append("requested investment != sum of SKU investments")
    provisional = sum(
        r.investment for r in report.recommendations
        if r.policy_kind in PROVISIONAL_POLICY_KINDS
    )
    if abs(provisional - getattr(report, "provisional_investment", 0.0)) > max(1.0, provisional * 1e-6):
        issues.append("provisional investment != sum of provisional SKU investments")

    return issues


def _lot_issues(r) -> list[str]:
    """Supplier-terms invariants on one (s,Q) record that carries a rule.

    The lot Kern prints must be one the supplier accepts (on the multiple, at or
    above the MOQ), the unconstrained EOQ it was derived from must still be on
    the record and positive, and the runner-up lot -- the second terminal -- must
    be feasible too and never cheaper than the one chosen, or the choice was
    wrong. A rule on an (R,S) line is carried, not applied, so nothing is
    checked there beyond the record naming it.
    """
    rule = getattr(r, "lot_rule", "")
    if not rule or r.policy_kind != "(s, Q)" or r.order_quantity is None:
        return []
    issues: list[str] = []
    moq, multiple = getattr(r, "lot_moq", 0.0), getattr(r, "lot_multiple", 0.0)
    terms = dict(minimum_order_quantity=moq, order_multiple=multiple)
    if not is_feasible_lot(r.order_quantity, **terms):
        issues.append(
            f"{r.product_id}: order quantity {r.order_quantity:.4g} violates the supplier "
            f"terms ({rule})")
    q0 = getattr(r, "order_quantity_unconstrained", None)
    if q0 is None or q0 <= 0:
        issues.append(f"{r.product_id}: supplier terms applied but no unconstrained Q on record")
    if getattr(r, "lot_penalty_usd", 0.0) < -TOL:
        issues.append(f"{r.product_id}: negative lot penalty")
    alt = getattr(r, "lot_alternative_qty", None)
    if alt is not None:
        if not is_feasible_lot(alt, **terms):
            issues.append(
                f"{r.product_id}: alternative lot {alt:.4g} violates the supplier terms ({rule})")
        alt_penalty = getattr(r, "lot_alternative_penalty_usd", None)
        if alt_penalty is None or alt_penalty + TOL < getattr(r, "lot_penalty_usd", 0.0):
            issues.append(
                f"{r.product_id}: the alternative lot is cheaper than the chosen one")
    return issues


def _techo_del_tope(r, nombre: str, restante: float) -> float:
    """Hasta donde puede quedar una cantidad topeada: el restante, salvo la Q de un lote
    con terminos del proveedor cuando el restante no llega ni al lote minimo legal. Ahi
    comprar menos que el minimo no es una opcion que Kern pueda imprimir, y el tope baja
    la Q hasta el minimo y no mas (`jobs.inventory_optimization._lote_legal_bajo`)."""
    if nombre != "order quantity" or not getattr(r, "lot_rule", ""):
        return restante
    minimo_legal = apply_order_rules(
        1e-9, minimum_order_quantity=getattr(r, "lot_moq", 0.0) or 0.0,
        order_multiple=getattr(r, "lot_multiple", 0.0) or 0.0,
    )
    return max(restante, minimo_legal)


def _horizon_issues(r) -> list[str]:
    """Invariantes del tope por fecha de fin sobre un registro (`src/horizon.py`).

    Cuatro, y las cuatro salen de un modo de falla concreto:

    1. **Un tope nunca anula.** Si una linea topeada saliera con disparador o cantidad en
       cero, el tope habria convertido una fecha mal tipeada en "no compres nunca mas".
       Esa es la razon de ser de los verdictos `expired` / `would_zero`, y esta compuerta
       es lo que impide que un cambio futuro los saltee.
    2. **Un `*_before_horizon` aparece si y solo si ese numero se movio**, y siempre
       hacia abajo. Un "antes" igual al "despues" hace leer un cambio donde no lo hubo, y
       uno menor seria un tope que SUBIO la politica.
    3. **Un verdicto que no topea deja la linea intacta**: ningun `*_before_horizon`.
    4. **Lo topeado respeta el restante.** Si la cantidad publicada supera la demanda que
       queda, el tope no se aplico aunque el registro diga que si.
    5. **El capital sigue a la cantidad, y solo hacia abajo.** Es la que faltaba hasta el
       2026-09-23: el tope movia las tres cantidades y dejaba la inversion calculada
       sobre la politica sin topear -- 12.018 usd que el titular no mostraba, medido
       sobre una cartera real. Va aparte de las tres de arriba porque su invariante es
       DISTINTA en dos puntos: el ciclo SI puede quedar en cero (si el buffer solo ya
       supera el restante, se lo come entero y ahi termina), y su unidad es usd, asi que
       no se compara contra un restante que esta en unidades.
    6. **El tope no toca el buffer**, que es la otra mitad del diseno de
       `src.horizon.capped_cycle_stock`: `ss_investment` tiene que seguir siendo
       exactamente `safety_stock x unit_cost`. Si un cambio futuro hiciera que el tope
       encogiera el safety stock, estaria bajando en silencio un nivel de servicio que
       nadie renegocio, y esta linea se pone roja.
    """
    verdict = getattr(r, "horizon_verdict", "no_date")
    previos = {
        "reorder point": (getattr(r, "reorder_point_before_horizon", None), r.reorder_point),
        "order-up-to": (getattr(r, "order_up_to_before_horizon", None), r.order_up_to),
        "order quantity": (getattr(r, "order_quantity_before_horizon", None), r.order_quantity),
    }
    capital_antes = getattr(r, "cycle_investment_before_horizon", None)
    issues: list[str] = []
    if verdict != "applied":
        for nombre, (antes, _) in previos.items():
            if antes is not None:
                issues.append(
                    f"{r.product_id}: horizon verdict '{verdict}' does not cap, but the "
                    f"record carries a pre-cap {nombre}")
        if capital_antes is not None:
            issues.append(
                f"{r.product_id}: horizon verdict '{verdict}' does not cap, but the "
                f"record carries a pre-cap cycle investment")
        return issues

    restante = getattr(r, "remaining_demand", None)
    if restante is None or restante <= 0:
        issues.append(f"{r.product_id}: horizon cap applied with no positive remaining demand")
    if not any(antes is not None for antes, _ in previos.values()):
        issues.append(f"{r.product_id}: horizon cap applied but nothing was recorded as capped")
    for nombre, (antes, ahora) in previos.items():
        if antes is None:
            continue
        if ahora is None or ahora <= 0:
            # El tope encoge; no discontinua. Una discontinuacion la decide una persona.
            issues.append(f"{r.product_id}: horizon cap zeroed the {nombre}")
        elif ahora >= antes - TOL:
            issues.append(f"{r.product_id}: pre-cap {nombre} is not above the capped one")
        elif restante is not None and ahora > _techo_del_tope(r, nombre, restante) + TOL:
            issues.append(
                f"{r.product_id}: capped {nombre} {ahora:.4g} still exceeds the remaining "
                f"demand {restante:.4g}")

    # 5. El capital, con su propia invariante: puede llegar a cero, y solo baja.
    ahora_cap = getattr(r, "cycle_investment", None)
    if capital_antes is not None:
        if ahora_cap is None or ahora_cap < -TOL:
            issues.append(f"{r.product_id}: horizon cap left a negative cycle investment")
        elif ahora_cap >= capital_antes - TOL:
            issues.append(
                f"{r.product_id}: pre-cap cycle investment is not above the capped one")

    # 6. Y el buffer queda intacto: el tope no renegocia el nivel de servicio.
    ss = getattr(r, "safety_stock", None)
    ss_inv = getattr(r, "ss_investment", None)
    costo = getattr(r, "unit_cost", None)
    if ss is not None and ss_inv is not None and costo is not None:
        esperado = ss * costo
        if abs(ss_inv - esperado) > max(TOL, abs(esperado) * 1e-9):
            issues.append(
                f"{r.product_id}: horizon cap moved the safety-stock investment "
                f"({ss_inv:.4g}) away from safety_stock x unit_cost ({esperado:.4g})")
    return issues


def _position_issues(r) -> list[str]:
    """Stock-position invariants on one record.

    Without stock on hand nothing downstream may claim a position, an on-order
    figure or a due-now signal. With one, the position must be the DDMRP identity
    (on hand + on order, zero when unmeasured), and the due-now signal must be
    exactly what the policy says at that position: (s,Q) orders Q at or below s,
    (R,S) orders up to S below S, nothing otherwise.
    """
    on_hand = getattr(r, "on_hand", None)
    on_order = getattr(r, "on_order", None)
    position = getattr(r, "inventory_position", None)
    due = getattr(r, "order_now", None)
    qty = getattr(r, "order_qty_now", None)
    issues: list[str] = []
    if on_hand is None:
        if any(v is not None for v in (on_order, position, due, qty)):
            issues.append(f"{r.product_id}: position fields set without stock on hand")
        return issues
    expected = on_hand + (on_order or 0.0)
    if position is None or abs(position - expected) > TOL:
        issues.append(f"{r.product_id}: inventory position != on hand + on order")
        return issues
    if due is None:
        if qty is not None:
            issues.append(f"{r.product_id}: order quantity due now without a due-now signal")
        return issues
    if qty is None or qty < -TOL:
        issues.append(f"{r.product_id}: due-now signal without a non-negative quantity")
        return issues
    if r.policy_kind == "(s, Q)" and r.order_quantity is not None:
        should = position <= r.reorder_point
        want = r.order_quantity if should else 0.0
    elif (r.policy_kind == "(R, S)" or r.policy_kind in PROVISIONAL_POLICY_KINDS) \
            and r.order_up_to is not None:
        # Una cobertura provisional es un objetivo de reposicion sin lote, asi que su
        # senal de ejecucion es la MISMA aritmetica que la de un (R,S). Lo que no
        # comparte es el buffer, y eso lo gatea `_sized_field_issues`.
        should = position < r.order_up_to
        want = max(0.0, r.order_up_to - position) if should else 0.0
    else:
        issues.append(f"{r.product_id}: due-now signal on a policy that cannot order")
        return issues
    if bool(due) != should:
        issues.append(f"{r.product_id}: order_now={due} contradicts the position vs trigger")
    if abs(qty - want) > TOL:
        issues.append(f"{r.product_id}: order_qty_now {qty:.4g} != what the policy orders ({want:.4g})")
    return issues


def verify_pricing(report: PricingReport) -> list[str]:
    """Return a list of QA issues for a pricing report. Empty list = passed."""
    issues: list[str] = []
    if not report.recommendations:
        issues.append("pricing report has no recommendations")

    for r in report.recommendations:
        if r.current_price < -TOL:
            issues.append(f"{r.product_id}: negative current price")
        if r.action in {"raise", "lower"}:
            if r.optimal_price is None or r.optimal_price <= 0:
                issues.append(f"{r.product_id}: actionable but no positive optimal price")
            elif r.optimal_price <= r.unit_cost:
                issues.append(f"{r.product_id}: optimal price at or below unit cost")
            if r.elasticity >= -1:
                issues.append(f"{r.product_id}: actionable but demand is inelastic")
            if r.confident and r.profit_uplift_pct is not None and r.profit_uplift_pct < -0.5:
                issues.append(f"{r.product_id}: 'optimal' price lowers modeled profit")
        elif r.action == "inelastic" and r.elasticity < -1:
            issues.append(f"{r.product_id}: flagged inelastic but elasticity < -1")
        elif r.action == "insufficient_data" and r.optimal_price is not None:
            issues.append(f"{r.product_id}: insufficient data but an optimal price was set")

    return issues


def verify_leadership(profile: ChainProfile) -> list[str]:
    """Return a list of QA issues for a CHAIN profile. Empty list = passed."""
    issues: list[str] = []
    codes = {code for code, _ in DIMS}

    if set(profile.scores) != codes:
        issues.append("profile is missing CHAIN dimensions")
    for code, val in profile.scores.items():
        if not 0 <= val <= 4:
            issues.append(f"{code}: score out of 0..4")

    expected_avg = sum(profile.scores.values()) / len(profile.scores) if profile.scores else 0.0
    if abs(profile.average - expected_avg) > 1e-9:
        issues.append("average does not match scores")

    if profile.scores:
        expected_gap = max(profile.scores.values()) - min(profile.scores.values())
        if profile.gap != expected_gap:
            issues.append("gap does not match scores")
        if profile.lever_level != min(profile.scores.values()):
            issues.append("priority lever is not the lowest-scoring dimension")

    if not profile.archetype:
        issues.append("missing archetype")
    return issues


def leadership_passed(profile: ChainProfile) -> bool:
    return not verify_leadership(profile)


def verify_digest(result: DigestResult) -> list[str]:
    """Return a list of QA issues for a daily digest (Linchpin 3.0 PR-3). Empty
    list = passed. The invariant that matters here is "no fabricated data"
    (plan rule 14): the per-type breakdown must actually sum to the reported
    total, an empty ledger must not be reported as having events, and a
    message that claims events happened must actually mention a count."""
    issues: list[str] = []

    if result.event_count < 0:
        issues.append("digest: negative event_count")
    if any(v < 0 for v in result.counts_by_type.values()):
        issues.append("digest: negative count in counts_by_type")

    counted = sum(result.counts_by_type.values())
    if counted != result.event_count:
        issues.append(f"digest: counts_by_type sums to {counted} but event_count is {result.event_count}")

    if not result.message.strip():
        issues.append("digest: empty message")
    elif result.event_count == 0 and "no events" not in result.message:
        issues.append("digest: zero events but message does not say so")
    elif result.event_count > 0 and str(result.event_count) not in result.message:
        issues.append("digest: message does not mention the actual event count")

    return issues


def digest_passed(result: DigestResult) -> bool:
    return not verify_digest(result)


def verify_price_intel(report: PriceIntelReport) -> list[str]:
    """QA invariants for the price-intelligence one-shot deliverable (plan
    section 6.9 item 2). Empty list = passed:

    - coverage: >=60% of products must have >=1 accepted competitor
      observation to ship at all (below that, the position matrix is too
      sparse to be a defensible deliverable).
    - zero quarantined/discarded rows leak into the accepted offers --
      they are reported in their own section (golden rule 14), never
      shipped as if trustworthy.
    - average freshness of the accepted observations stays within the
      report's own stated SLA.
    """
    issues: list[str] = []

    if report.n_products <= 0:
        issues.append("price_intel: no products in scope")
    if not (0.0 - TOL <= report.coverage_pct <= 1.0 + TOL):
        issues.append(f"price_intel: coverage_pct out of [0,1]: {report.coverage_pct}")
    elif report.coverage_pct < PRICE_INTEL_COVERAGE_MIN - TOL:
        issues.append(
            f"price_intel: coverage {report.coverage_pct * 100:.0f}% is below the "
            f"{PRICE_INTEL_COVERAGE_MIN * 100:.0f}% minimum required to ship"
        )

    accepted_refs = {(o.site, o.competitor_sku_ref) for o in report.offers}
    tainted_refs = {(r.site, r.competitor_url) for r in report.rows if r.status in ("quarantined", "discarded")}
    leaked = tainted_refs & accepted_refs
    if leaked:
        issues.append(f"price_intel: {len(leaked)} quarantined/discarded row(s) leaked into the accepted offers")

    if not (0.0 - TOL <= report.quarantine_rate <= 1.0 + TOL):
        issues.append(f"price_intel: quarantine_rate out of [0,1]: {report.quarantine_rate}")

    if report.avg_freshness_hours < -TOL:
        issues.append(f"price_intel: negative avg_freshness_hours: {report.avg_freshness_hours}")
    elif report.avg_freshness_hours > report.sla_hours + TOL:
        issues.append(
            f"price_intel: average freshness {report.avg_freshness_hours:.1f}h exceeds the "
            f"{report.sla_hours:.1f}h SLA"
        )

    return issues


def price_intel_passed(report: PriceIntelReport) -> bool:
    return not verify_price_intel(report)


def coverage_gate(outcome: GuidedOutcome) -> list[str]:
    """Deliverable coverage gate (plan §2.14). Empty list = the deliverable is covered.

    Extends the never-unprotected contract (``verify_guided``) with the residual-block
    requirements: a non-executed result must spell out the human residual - every
    handoff states the risk if skipped, and every escalation routes to a named human
    with an SLA. This is the QA-layer guard against a silent dead end.
    """
    issues = list(verify_guided(outcome))

    for h in outcome.handoffs:
        if not h.risk_if_skipped.strip():
            issues.append(f"handoff '{h.title}' does not state the risk if skipped")

    e = outcome.escalation
    if e is not None:
        if not e.route_to.strip():
            issues.append("escalation has no route_to (named human/role)")
        if not e.sla.strip():
            issues.append("escalation has no SLA")
        if not e.reason.strip():
            issues.append("escalation has no reason")

    return issues


def covered(outcome: GuidedOutcome) -> bool:
    return not coverage_gate(outcome)


def verify_integrated_plan(bundle: IntegratedPlanBundle) -> list[str]:
    """QA gate for A5's integrated plan (Linchpin 3.0 PR-20). Empty list =
    passed. Checks STRUCTURAL soundness (finite numbers, all 3 required
    coherence-check kinds present, internally-consistent counts) -- a
    FAILED coherence check is an intended FINDING this deliverable reports
    to a human (see ``jobs.integrated_plan``'s module docstring), never
    itself a QA failure that blocks the deliverable. A NOT-EVALUABLE check
    is likewise a valid, declared delivery here: whether the plan is allowed
    to CLAIM coherence on top of one is ``jobs.integrated_plan.verify``'s
    question, not this gate's.

    ``plan.n_checks_passed`` and ``bundle.tally.n_passed`` are deliberately NOT
    compared: they count different things on purpose. The first is the engine's
    arithmetic over every check it ran, including any it resolved against a
    placeholder input; the second counts only the checks that could be evaluated.
    Reconciling them would mean deciding the engine's naive count is the truth,
    which is the defect the tally exists to undo.
    """
    issues: list[str] = list(forecast_job.verify(bundle.forecast_report))
    plan = bundle.plan

    if plan.n_skus <= 0:
        issues.append("integrated_plan: no SKUs in the plan")
    if plan.n_checks != len(plan.checks):
        issues.append("integrated_plan: n_checks does not match len(checks)")
    if plan.n_checks_passed + plan.n_checks_failed != plan.n_checks:
        issues.append("integrated_plan: n_checks_passed + n_checks_failed != n_checks")

    # The tally must account for every check the engine produced, exactly once:
    # a result quietly dropped from all three buckets would shrink the "N/M
    # evaluated" denominator with nothing to show for it.
    tally = bundle.tally
    if tally.n_total != len(plan.checks):
        issues.append(
            f"integrated_plan: the coherence tally accounts for {tally.n_total} check(s) but "
            f"the plan holds {len(plan.checks)}")
    tallied = sorted(
        [(c.check, c.product_id) for c in tally.passed + tally.failed]
        + [(u.check, u.product_id) for u in tally.unevaluable]
    )
    if tallied != sorted((c.check, c.product_id) for c in plan.checks):
        issues.append(
            "integrated_plan: the coherence tally is not a partition of the plan's checks "
            f"(tallied {tallied})")

    present_kinds = {c.check for c in plan.checks}
    missing_kinds = _REQUIRED_INTEGRATED_PLAN_CHECKS - present_kinds
    if missing_kinds:
        issues.append(f"integrated_plan: missing required coherence check kind(s): {sorted(missing_kinds)}")

    for line in plan.demand_plan:
        if not math.isfinite(line.base_forecast) or line.base_forecast < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid base_forecast")
        if not math.isfinite(line.shaped_demand) or line.shaped_demand < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid shaped_demand")

    for line in plan.purchase_plan:
        if not math.isfinite(line.recommended_order) or line.recommended_order < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid recommended_order")
        if not math.isfinite(line.order_value) or line.order_value < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid order_value")

    return issues


def integrated_plan_passed(bundle: IntegratedPlanBundle) -> bool:
    return not verify_integrated_plan(bundle)


def verify_price_watch(report: PriceWatchCycleReport) -> list[str]:
    """QA gate for the discovery-assisted watch cycle (Task 11 / PR-11's agent-
    tool wiring). Empty list = passed. STRUCTURAL checks only -- no new
    judgment threshold is invented here (unlike ``verify_price_intel``'s own
    60% coverage bar, a DIFFERENT, already-approved deliverable with its own
    bar): pairs_checked matches the outcome count (golden rule 14), every
    outcome's status is one of the honest, enumerated values
    ``jobs.price_monitor.accept_observation`` actually returns, and every
    outcome carries a non-empty, citable reason.

    R5, checked AGAIN at this gate (not just ``scm_agent.tool_options.
    price_watch_options``): every ``pending_escalations`` entry (Task 9's
    ceiling-raise request) must honor the never-unprotected contract
    (``coverage_gate``, which extends ``verify_guided`` with the residual/
    handoff/escalation completeness checks) and must NEVER be status
    ``EXECUTED`` -- a tier raise is never silently applied.
    """
    issues: list[str] = []

    if report.pairs_checked != len(report.outcomes):
        issues.append(
            f"price_watch: pairs_checked ({report.pairs_checked}) != len(outcomes) ({len(report.outcomes)})"
        )

    valid_statuses = {"accepted", "quarantined", "discarded", "skipped"}
    for o in report.outcomes:
        if o.status not in valid_statuses:
            issues.append(f"price_watch: {o.site}/{o.competitor_sku_ref}: invalid status {o.status!r}")
        if not o.reason:
            issues.append(f"price_watch: {o.site}/{o.competitor_sku_ref}: outcome has no reason")

    for guided in report.pending_escalations:
        if guided.status == EXECUTED:
            issues.append(
                "price_watch: a pending ceiling-raise escalation reports status EXECUTED -- R5 violation"
            )
        issues.extend(f"price_watch: pending escalation: {msg}" for msg in coverage_gate(guided))

    return issues


def price_watch_passed(report: PriceWatchCycleReport) -> bool:
    return not verify_price_watch(report)
