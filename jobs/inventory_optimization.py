"""Inventory Optimization playbook.

Given canonical demand (from intake), runs the engine end to end for every SKU
— forecast (sigma_e) -> (s,Q)/(R,S) policy -> portfolio budget allocation — and
returns a structured report ready to turn into client deliverables.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy import stats as _sps

from jobs.intake import IntakeQuality
from src import order_fulfillment
from src.classification import classify_portfolio
from src.classification import service_levels as class_service_levels
from src.constraints import (
    InventoryItem,
    allocate_under_budget,
    cheapest_feasible_lot,
    feasible_neighbours,
    is_feasible_lot,
    lot_rule,
)
from src.ddmrp import net_flow_position
from src.fill_rate import fill_rate_from_safety_stock
from src.forecasting import (
    DEFAULT_OOS_ORIGINS,
    MIN_PERIODS_FOR_POLICY,
    forecast_with_oos_sigma,
)
from src.horizon import DECLARABLES as HORIZON_DECLARABLES
from src.horizon import cap_policy, capped_cycle_stock, periods_until
from src.perishables import bound_order_quantity_by_expiry
from src.policies import continuous_review_sq, periodic_review_rs
from src.risk_period import provisional_cover
from src.safety_stock import tune_service_level
from src.sources import DataFrameDemandSource

PERIODS_PER_YEAR = 52.0
# Service level gained per point of fill-rate gap in the closed loop. The engine's
# own default (0.5) drives a 15-point shortfall straight past 1.0 into its hi=0.999
# clamp, which turns every chronically short SKU into a 99.9% target regardless of
# how big the gap was.
SERVICE_LEVEL_TUNING_STEP = 0.1


def _cola_de_coberturas(report, *, sola: bool = False) -> str:
    """La frase de las COBERTURAS PROVISIONALES, que no son ni una politica ni una
    ausencia y por eso no entran en ninguna de las dos mitades de `unsized_note`.

    Tiene que decir tres cosas o no sirve: que el numero existe (hay unidades que
    comprar), que NO tiene buffer detras (se espera que se quede corto la mitad de las
    veces, porque cubre la media), y que su capital va APARTE del titular -- si no,
    el lector suma mal.

    `sola=True` cuando NO hay un encabezado de inversion delante (o sea, cuando esta
    frase va dentro del titular). Ahi no hay un "more" que contar ni una cifra de la
    que separarse, y decirlo igual mandaria al lector a buscar un numero que no
    existe.
    """
    n = int(getattr(report, "n_provisional_cover", 0) or 0)
    if not n:
        return ""
    plata = float(getattr(report, "provisional_investment", 0.0) or 0.0)
    cabeza = (
        f" {n} SKU(s) got a PROVISIONAL COVER instead of a policy (${plata:,.0f})"
        if sola else
        f" {n} more SKU(s) got a PROVISIONAL COVER instead of a policy "
        f"(${plata:,.0f}, counted separately)"
    )
    return (
        cabeza + ": too little history to estimate dispersion, so the figure is the "
        "measured mean over the risk period with NO safety stock - it is expected to "
        "fall short about half the time, and it is there so a new SKU is not simply "
        "left unstocked."
    )


def unsized_note(report) -> tuple[str | None, str]:
    """Como se cuenta, en prosa, lo que este modulo NO dimensiono.

    Devuelve `(titular_o_None, cola)`. El titular reemplaza al encabezado cuando
    NINGUN SKU quedo dimensionado; la cola se agrega al encabezado normal cuando
    solo algunos. Vive aca, y no en cada superficie, porque son DOS las que lo
    escriben -- el resumen del agente (`scm_agent/tools.py`) y el encabezado del
    deck que lee el cliente (`jobs/inventory_deliverable.py`) -- y arreglar una
    sola dejaba a la otra diciendo "recommended $0 inventory investment" sobre una
    corrida en la que no se decidio nada.

    Separa las dos causas porque significan cosas OPUESTAS, que es la misma
    distincion que hacen los campos del registro:
      - DECLINADO (`insufficient_history`, `skipped_zero_cost`): el motor no pudo
        decidir. Cero no es la respuesta, es la ausencia de una.
      - MEDIDO EN CERO (`skipped_zero_demand`): el motor SI decidio, y la respuesta
        es cero. Ahi no hay nada que estacionar y decirlo es un hallazgo, no una
        disculpa.
    Meterlas en la misma frase -- que es lo que esta funcion nacio arreglando --
    repite en la prosa la confusion que los campos dejaron de tener.

    Quien decide si el titular reemplaza al encabezado es cuantos SKU quedaron
    DIMENSIONADOS, no cuantos quedaron sin nada. No es lo mismo desde que existe la
    cobertura provisional: una cartera entera de coberturas no tiene ningun SKU
    declinado, asi que la condicion vieja la dejaba pasar al encabezado normal --
    *"recommended inventory investment $0 at 95% service level"* sobre una corrida
    en la que no se dimensiono ninguna politica y en la que si hay plata que gastar.
    Es el mismo defecto que este archivo nacio arreglando, un piso mas arriba.
    """
    n_skus = int(getattr(report, "n_skus", 0) or 0)
    n_declinados = (int(getattr(report, "n_insufficient_history", 0) or 0)
                    + int(getattr(report, "n_skipped_zero_cost", 0) or 0))
    n_medidos = int(getattr(report, "n_skipped_zero_demand", 0) or 0)
    n_cobertura = int(getattr(report, "n_provisional_cover", 0) or 0)
    n_total = n_declinados + n_medidos
    if not n_skus or not (n_total or n_cobertura):
        return None, ""

    if n_skus - n_total - n_cobertura > 0:
        cola = ""
        if n_total:
            partes = []
            if n_declinados:
                partes.append(f"{n_declinados} the engine declined to size")
            if n_medidos:
                partes.append(f"{n_medidos} with zero measured demand")
            cola = (f" {n_total} SKU(s) carry no sized policy ({', '.join(partes)}) "
                    "and are excluded from that figure.")
        return None, cola + _cola_de_coberturas(report)

    if not n_total:
        # Toda la cartera recibio cobertura: no hubo negativa que contar, pero
        # tampoco hubo politica, y el encabezado normal publicaria un $0 que la
        # propia cola contradice.
        return (
            f"No policy was sized for any of the {n_skus} SKU(s)."
            + _cola_de_coberturas(report, sola=True),
            "",
        )

    if not n_declinados:
        cuantos = (f"All {n_skus}" if n_medidos == n_skus
                   else f"{n_medidos} of the {n_skus}")
        return (
            f"{cuantos} SKU(s) have zero measured demand, so their reorder point "
            "and buffer are zero and there is nothing to stage. That is a "
            "measurement, not a refusal - but check whether the export split one "
            "SKU's sales across spellings before acting on it."
            + _cola_de_coberturas(report, sola=True),
            "",
        )
    n_tiempo = int(getattr(report, "n_insufficient_history", 0) or 0)
    n_costo = int(getattr(report, "n_skipped_zero_cost", 0) or 0)
    causas = []
    if n_tiempo:
        causas.append(f"{n_tiempo} for too little demand history")
    if n_costo:
        causas.append(f"{n_costo} for no usable unit cost")
    if n_medidos:
        # Se nombra aparte, y despues del "declined", porque no es una negativa:
        # es la respuesta.
        cabeza = (f"No policy was sized for any of the {n_skus} SKU(s): the engine "
                  f"declined {n_declinados} ({', '.join(causas)}) and {n_medidos} "
                  "have zero measured demand")
    elif n_declinados == n_skus:
        cabeza = (f"No policy was sized: the engine declined all {n_skus} SKU(s) "
                  f"({', '.join(causas)})")
    else:
        cabeza = (f"No policy was sized for any of the {n_skus} SKU(s): the engine "
                  f"declined {n_declinados} ({', '.join(causas)})")
    return (
        cabeza + ". Where it declined there is no reorder point, no buffer and no "
        "investment - this is NOT a recommendation to hold nothing."
        + _cola_de_coberturas(report, sola=True),
        "",
    )


#: Periodos observados minimos para emitir una COBERTURA PROVISIONAL. Debajo de esto
#: no hay ni siquiera una media que valga la pena: con una sola observacion "el
#: promedio" es esa observacion, y llamarlo tasa de demanda es inventar. Con dos ya
#: hay un promedio, que es poco pero es una medicion, y el plazo del proveedor viene
#: del archivo. La compuerta de politica (`MIN_PERIODS_FOR_POLICY`, 6) NO se toca: lo
#: que cambia es que entre las dos hay ahora un escalon en vez de un abismo.
MIN_PERIODS_FOR_COVER = 2

#: El periodo de revision que entra en el periodo de riesgo de la cobertura. Es el
#: MISMO 1.0 con el que este job llama a `periodic_review_rs`: si la cobertura usara
#: otro, dos lineas del mismo entregable estarian cubriendo ventanas distintas sin
#: decirlo.
PROVISIONAL_REVIEW_PERIODS = 1.0

#: La `policy_kind` de una linea que trae una COBERTURA pero no una politica. No esta
#: en `UNSIZED_POLICY_KINDS` -- trae un numero sobre el que un operador actua -- ni es
#: una politica dimensionada: no tiene buffer, no tiene z y no tiene nivel de servicio
#: alcanzado. Esa forma de tres campos (disparador SI, buffer NO) es lo que la declara.
PROVISIONAL_POLICY_KINDS = frozenset({"provisional_cover"})

#: Las `policy_kind` que este modulo emite cuando NO dimensiono una politica. Es la
#: definicion CANONICA: hasta el 2026-09-19 vivia copiada a mano en tres lugares y las
#: tres copias no coincidian -- `jobs/qa.py` tenia las tres clases, y tanto
#: `src/verify/coherencia.py` como `scm_agent/package_specs.py` tenian solo dos,
#: olvidando `insufficient_history`. Esa tercera copia es la que mas dolia: hacia que un
#: SKU sin politica entrara a la planilla de pedidos del cliente como `kern_optimized`
#: con disparador cero, pisando el numero propio del cliente y suprimiendo una reposicion
#: real. Un test fija que las copias restantes sigan siendo iguales a esta.
UNSIZED_POLICY_KINDS = frozenset({
    "skipped_zero_demand", "skipped_zero_cost", "insufficient_history",
})

#: Todo lo que NO es una politica completamente dimensionada. Es el conjunto que leen
#: las invariantes por SKU de `src/verify/coherencia.py`, que presuponen buffer y z:
#: una cobertura provisional los tiene en `None` a proposito, asi que pasarla por ahi
#: reventaria, y bajarle las invariantes a todo el resto seria peor.
NOT_FULLY_SIZED_POLICY_KINDS = UNSIZED_POLICY_KINDS | PROVISIONAL_POLICY_KINDS


@dataclass(frozen=True)
class SkuRecommendation:
    product_id: str
    method: str
    intermittent: bool
    #: `None` cuando no hay un pronostico publicable: o no se calculo, o se
    #: calculo sobre menos historia que el minimo y por eso no se puede citar
    #: (ver `reorder_point`). NUNCA 0.0 para decir "no se sabe": un cero es una
    #: tasa de demanda, y aguas abajo se divide por el.
    #:
    #: Una linea `provisional_cover` lo tiene en `None` y trae en cambio
    #: `observed_mean_per_period`: un PRONOSTICO es una extrapolacion que la
    #: compuerta de historia prohibe citar, y un promedio de lo que se vendio es
    #: una medicion. No son lo mismo y no comparten campo.
    forecast: float | None
    error_std: float
    bias: float
    mae: float
    policy_kind: str
    order_quantity: float | None
    order_up_to: float | None
    # --- Los campos que describen una POLITICA son anulables --------------------
    # `None` cuando el motor no pudo calcular el valor; `0.0` solo cuando lo
    # calculo y dio cero. Un `0.0` aca significa "medido, y la respuesta es
    # ninguno" -- que es justo lo contrario de lo que pasa en las ramas
    # `insufficient_history` y `skipped_zero_cost`, donde no se calculo nada.
    #
    # Hasta el 2026-09-19 una negativa salia con estos cuatro campos en 0.0 y el
    # aviso viajaba aparte (`policy_kind`, `sigma_source`, y un residual de
    # `scm_agent/tool_options.py`). Todo consumidor que lee el NUMERO leia lo
    # contrario de lo que decia la prosa: `src/simworld/runners.py` lo ejecutaba
    # como "no pedir nunca", y `jobs/state_bridge.py` -> `src/alerting.py` emitia
    # `dead_stock` -- "N units on hand with no demand" -- sobre un SKU que vendia
    # todas las semanas y cuyo unico problema era tener cinco periodos en vez de
    # seis. `_usable` en el puente existe para omitir justamente esa linea ("un
    # disparador ausente no es cero (pediria siempre)"); el 0.0 del productor la
    # derrotaba.
    reorder_point: float | None
    safety_stock: float | None
    z_factor: float | None
    service_level: float       # cycle service level actually used (per-SKU, may be differentiated)
    unit_cost: float
    lead_periods: float
    cycle_investment: float
    ss_investment: float
    investment: float
    status: str  # "ok" | "high_bias" | "review"
    # What the run actually did, so the deliverable can describe it instead of
    # asserting a template methodology the engine stopped using.
    sigma_source: str = "forecast_error"      # see ForecastResult.sigma_source
    buffer_distribution: str = "normal"       # the distribution that sized the buffer
    lead_time_std: float = 0.0                # sigma_L fed to the risk period
    lead_time_std_source: str = "defaulted_zero"   # "client_file" | "defaulted_zero"
    risk_periods: float = 0.0                 # tau actually used (L, or L+R for (R,S))
    # Observed demand periods behind this line. Carried on the record (not just
    # counted on the report) for two reasons: jobs/qa.py asserts the minimum-history
    # gate actually fired for every priced line, and the deliverable can show the
    # reader how much history each number rests on.
    n_periods: int = 0
    # Service the client ACTUALLY got - order-line fill rate, outbound OTIF, days
    # out of stock, lost sales - with the provenance of each number attached
    # (src/order_fulfillment.py). Until this field existed the only service number
    # on the record was `service_level` above, which is the policy's TARGET: a
    # client comparing our deck against their own numbers had nothing here that
    # answered "what did we actually serve?". None only if the caller built the
    # record by hand; run() always fills it, with provenance 'not_measured' when
    # neither order lines nor a client-reported fill rate came in.
    realized_service: order_fulfillment.RealizedService | None = None
    #: Solo en `provisional_cover`: el promedio simple de lo que se vendio por
    #: periodo, y los periodos de riesgo sobre los que se extendio para llegar a la
    #: cobertura. Van en el registro y no solo en la prosa para que el entregable
    #: pueda mostrar la aritmetica entera -- `cobertura = media x periodos` -- en vez
    #: de pedirle al lector que confie en un numero suelto.
    observed_mean_per_period: float | None = None
    cover_periods: float | None = None
    # The beta this sized policy IMPLIES (Vandeput 7.3.1, via src/fill_rate.py):
    # provenance 'model_implied'. It is what the model expects to achieve, not what
    # happened, and it is deliberately NOT named *_fill_rate so that neither a
    # reader nor a column mapping can pick it up as realized service. None when the
    # policy has no cycle demand or no dispersion to imply a beta from.
    model_implied_beta: float | None = None
    # Supplier terms and what honouring them did to Q. `order_quantity` above is
    # the lot the supplier will ACCEPT; `order_quantity_unconstrained` is what the
    # EOQ asked for before the MOQ / case-pack rule (src.constraints.
    # cheapest_feasible_lot) moved it. The rule is applied to (s,Q) only: an (R,S)
    # policy orders a different quantity every review, so its terms are carried
    # here (`lot_rule`, `lot_moq`, `lot_multiple`) for the buyer to apply per
    # order, and the report counts those SKUs so the options can say so.
    #
    # `lot_alternative_qty` is the OTHER feasible neighbour with its own penalty:
    # the second terminal. Kern says 40 and the supplier sells by 30 -> the record
    # carries 60 (chosen) AND 30 (alternative), each priced, never just "60".
    order_quantity_unconstrained: float | None = None
    lot_rule: str = ""
    lot_moq: float = 0.0
    lot_multiple: float = 0.0
    lot_penalty_usd: float = 0.0
    lot_alternative_qty: float | None = None
    lot_alternative_penalty_usd: float | None = None
    # The stock POSITION this SKU sits at today, and what it means for the policy
    # above. `s` and `Q` say how much to keep and how much to order; they say
    # nothing about WHEN, and until this existed the tool never looked at the
    # shelf. `inventory_position` is src.ddmrp.net_flow_position(on_hand, on_order,
    # 0): stock on hand plus stock already bought and in transit, so an order that
    # transport delayed is not bought twice. `on_order` is None - never 0.0 - when
    # nobody measured it (no column in the file, or a blank cell for this SKU): a
    # zero would claim nothing is inbound. `order_now` compares the position with
    # the trigger (s for (s,Q), S for (R,S)); `order_qty_now` is Q, or S minus the
    # position. All None when the file carried no stock on hand at all.
    on_hand: float | None = None
    on_order: float | None = None
    inventory_position: float | None = None
    order_now: bool | None = None
    order_qty_now: float | None = None
    # La fecha de fin de vida comercial de este SKU y que le hizo a la politica
    # (`src/horizon.py`). `horizon_verdict` es SIEMPRE uno de los seis: `no_date` (el
    # caso normal, la mayoria de una cartera no trae fecha), `inert` (la trae y esta
    # lejos), `applied` (topeo al menos una cantidad), y los tres que se NIEGAN a topear
    # y hay que nombrar -- `expired`, `would_zero`, `unusable`.
    #
    # Los tres `*_before_horizon` son lo que el motor habia dimensionado antes del tope,
    # y existen por la misma razon que `order_quantity_unconstrained` existe para los
    # terminos del proveedor: un plan que topea y no muestra contra que topeo le pide al
    # lector que confie. `None` cuando no hubo tope.
    #
    # El tope se aplica a las TRES cantidades y no a dos: la regla medida topea "las dos
    # mitades de la politica", pero el arnes que la midio ejecuta todo como (R,S) y no
    # tiene lote. Aca un (s,Q) trae `order_up_to=None` y sus dos mitades son `s` y `Q`,
    # asi que la MISMA regla -- acotar por lo que queda por vender -- se aplica a las
    # tres. Es una traduccion declarada, no una equivalencia: ver
    # documentation/KERN_FECHA_DE_FIN_POR_SKU.md.
    end_date: str | None = None
    horizon_verdict: str = "no_date"
    periods_remaining: float | None = None
    remaining_demand: float | None = None
    reorder_point_before_horizon: float | None = None
    order_up_to_before_horizon: float | None = None
    order_quantity_before_horizon: float | None = None
    #: El capital de ciclo que este SKU pedia ANTES del tope. Misma regla que los tres
    #: de arriba: se llena solo si el tope LO MOVIO, y en blanco -- nunca en cero --
    #: cuando no. Un tope puede bajar el objetivo sin liberar capital (el ciclo ya
    #: cabia debajo del restante), y ahi este campo tiene que quedar vacio.
    cycle_investment_before_horizon: float | None = None
    # Shelf-life / perishable horizon (src/perishables.py)
    days_to_expiry: float | None = None
    perishable_verdict: str | None = None
    spoilage_risk_units: float = 0.0
    spoilage_risk_usd: float = 0.0
    order_quantity_before_shelf_life: float | None = None


@dataclass(frozen=True)
class JobReport:
    recommendations: list[SkuRecommendation]
    params: dict
    requested_investment: float
    cycle_floor: float
    final_investment: float
    safety_stock_scale: float
    feasible: bool
    budget: float | None
    n_skus: int
    n_at_risk: int
    n_intermittent: int
    # How much of the source file survived intake, and why the rest didn't
    # (jobs/intake.py) — None when the caller used the untracked intake.prepare()
    # path. scm_agent/tool_options.py's inventory_options() reads this to decide
    # whether to escalate. ``IntakeQuality.reading`` (mapping / sheet / hygiene)
    # rides along so the deliverable can tell the client how their file was read.
    intake_quality: IntakeQuality | None = None
    intake_quality_threshold: float = 0.20
    # De donde salieron las columnas de servicio y que significan, cuando el frame de
    # order_lines lo declaro (jobs/intake.py::service_lines lo pone en .attrs). Viaja hasta
    # el mapa de fuentes del entregable: el deck no ve el frame de entrada, ve este reporte,
    # asi que sin este campo el caveat se queda en el resumen del operador y el cliente que
    # abre el Excel lee un OTIF sin saber si su fecha es un despacho o una llegada.
    service_note: str = ""
    # SKUs skipped because their aggregate demand was zero (see run()'s per-SKU
    # loop) - a real new/seasonal SKU with no activity, or a data-quality split
    # that zeroed one spelling's demand. Isolated, not fatal to the batch.
    n_skipped_zero_demand: int = 0
    # SKUs skipped because no row carried a usable (>0) unit_cost (see run()'s
    # per-SKU loop) - a cost lost on load and reported as 0 instead of blank.
    # Isolated for the same reason as zero demand: compute_eoq's holding-cost
    # floor (1e-6) would otherwise turn a near-zero mean_unit_cost into an
    # absurdly large, silently "valid-looking" order quantity.
    n_skipped_zero_cost: int = 0
    # SKUs refused for too little demand history (see run()'s per-SKU loop and
    # src/forecasting.py's MIN_PERIODS_FOR_POLICY). Distinct from the two skips
    # above: those are degenerate DATA (no demand at all, no usable cost), this
    # one is degenerate KNOWLEDGE - there may be plenty of real demand, we simply
    # have too few observations of it to size a buffer honestly. Reported so
    # scm_agent/tool_options.py can state it as a residual instead of letting a
    # portfolio of refusals come back as a cheerful, cheap-looking plan.
    n_insufficient_history: int = 0
    # SKUs que recibieron una COBERTURA PROVISIONAL en vez de una politica: tienen
    # entre `MIN_PERIODS_FOR_COVER` y `MIN_PERIODS_FOR_POLICY` periodos, asi que hay
    # media pero no sigma. `provisional_investment` es el capital que esas coberturas
    # piden, y va APARTE de `requested_investment` a proposito: el plan optimizado y
    # las coberturas provisionales son dos preguntas distintas, una pasa por el
    # allocator de presupuesto y la otra no, y sumarlas en un solo titular haria
    # parecer dimensionado lo que no lo esta. Esconderla seria peor: son unidades que
    # alguien va a comprar.
    n_provisional_cover: int = 0
    provisional_investment: float = 0.0
    # Supplier terms. `n_lot_constrained` counts (s,Q) SKUs whose file declared an
    # MOQ or a case-pack multiple (the rule was applied, whether or not it moved
    # Q); `n_lot_snapped` those it actually moved; `lot_penalty_total_usd` what
    # honouring the terms costs per year against the unconstrained EOQ, summed;
    # `n_lot_rule_unapplied` the (R,S) SKUs that carry terms this job cannot
    # apply (see SkuRecommendation.lot_rule). scm_agent/tool_options.py turns the
    # first three into a renegotiation OPTION and the last into a residual.
    n_lot_constrained: int = 0
    n_lot_snapped: int = 0
    lot_penalty_total_usd: float = 0.0
    n_lot_rule_unapplied: int = 0
    # Stock position (see SkuRecommendation.on_hand). `n_positioned` SKUs came with
    # stock on hand; `n_netted` of them also with a measured on-order figure;
    # `n_order_now` sit at or below their trigger on today's position; and
    # `n_reorders_avoided_by_transit` would have been re-ordered on the shelf
    # figure alone but are covered by stock already inbound - the double buy that
    # counting the in-transit prevents (H4 in KERN_DISRUPCIONES_Y_DESAJUSTES.md).
    # `position_note` is jobs/intake.py's sentence about where the position came
    # from, including the warning when nothing could be netted; it travels to the
    # deliverable the same way `service_note` does.
    n_positioned: int = 0
    n_netted: int = 0
    n_order_now: int = 0
    n_reorders_avoided_by_transit: int = 0
    units_on_order: float = 0.0
    position_note: str = ""
    # Fecha de fin por SKU (ver SkuRecommendation.end_date). `n_with_end_date` SKUs
    # trajeron una; `n_horizon_capped` fueron topeados de verdad; `n_horizon_declarable`
    # traen una fecha que este job se NEGO a usar (vencida, o que daria un tope de cero,
    # o sin media que citar) y que la superficie tiene que nombrar -- un tope de cero es
    # una discontinuacion, y esa la decide una persona. `horizon_units_freed` son las
    # unidades de objetivo que el tope saco del plan, que es el numero que vuelve legible
    # "compra menos de lo que no vas a vender".
    n_with_end_date: int = 0
    n_horizon_capped: int = 0
    n_horizon_declarable: int = 0
    horizon_units_freed: float = 0.0
    #: El capital que el tope saca del plan, en usd. `horizon_units_freed` son unidades
    #: de objetivo; esto es lo que esas unidades COSTABAN, que es la mitad que faltaba:
    #: hasta el 2026-09-23 el tope movia las cantidades y el titular `requested_investment`
    #: salia identico al centavo. Es la resta de los `cycle_investment_before_horizon`.
    horizon_capital_freed_usd: float = 0.0
    horizon_note: str = ""
    #: Que paso con la memoria del Tower en esta corrida: el ciclo con el que se
    #: escribio el snapshot ``stock``, o por que no se escribio
    #: (``jobs/state_bridge.py``). Viaja en el reporte y no solo en el resumen, para
    #: que un llamador pueda comprobarlo sin parsear prosa.
    state_note: str = ""


def _stock_position(
    pid: str,
    on_hand_by_sku: dict[str, float] | None,
    on_order_by_sku: dict[str, float] | None,
) -> tuple[float | None, float | None, float | None]:
    """``(on_hand, on_order, position)`` for one SKU; each None when nobody measured it.

    Absent from the map is NOT zero: a SKU nobody reported an on-order figure for
    keeps ``None`` and is sized against the shelf alone, with the report saying so.
    """
    on_hand = None if on_hand_by_sku is None else on_hand_by_sku.get(pid)
    if on_hand is None or on_hand != on_hand:      # None or NaN
        return None, None, None
    on_order = None if on_order_by_sku is None else on_order_by_sku.get(pid)
    if on_order is not None and on_order != on_order:
        on_order = None
    # qualified_demand=0.0 is a declared absence, not a shortcut: a demand file has
    # no committed-demand column, and DDMRP's third term needs one.
    position = net_flow_position(float(on_hand), float(on_order or 0.0), 0.0)
    return float(on_hand), (None if on_order is None else float(on_order)), position


def _unidades_liberadas(recs: list) -> float:
    """Unidades de COMPRA que el tope por fecha de fin saco del plan.

    Una linea por SKU topeado, y para cada una la cantidad que se iba a comprar: `Q` en
    un (s,Q), el objetivo en un (R,S) o en una cobertura. Es el numero que vuelve legible
    "compra menos de lo que no vas a vender" en el resumen y en las OPCIONES.
    """
    total = 0.0
    for r in recs:
        if r.horizon_verdict != "applied":
            continue
        if r.order_quantity_before_horizon is not None:
            total += r.order_quantity_before_horizon - float(r.order_quantity or 0.0)
        elif r.order_up_to_before_horizon is not None:
            total += r.order_up_to_before_horizon - float(r.order_up_to or 0.0)
    return total


def _capital_liberado(recs) -> float:
    """Lo que el tope saca del plan, en usd -- la resta de los `*_before_horizon`.

    Se calcula de los campos del REGISTRO y no de un acumulador aparte, por la misma
    razon que `_unidades_liberadas`: asi el numero del resumen es exactamente la suma de
    lo que el cliente ve fila por fila en el Excel, y no puede derivar de el.
    """
    total = 0.0
    for r in recs:
        if r.cycle_investment_before_horizon is None:
            continue
        total += r.cycle_investment_before_horizon - float(r.cycle_investment or 0.0)
    return total


def _fecha_texto(valor) -> str | None:
    """Una fecha como ISO corto para el registro, o ``None``.

    El registro publica texto y no un ``Timestamp`` porque viaja a un CSV, a un deck y a
    un JSON, y los tres serializan distinto un objeto de pandas.
    """
    if valor is None:
        return None
    try:
        return pd.Timestamp(valor).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _lote_legal_bajo(tope: float, moq: float, multiple: float) -> float:
    """El lote legal mas grande que no supera ``tope``; si no hay ninguno, el minimo legal.

    Lo usa el tope por fecha de fin sobre la Q de un ``(s,Q)`` con terminos del
    proveedor: "no compres mas de lo que podes vender" y "el proveedor vende de a 12"
    tienen que valer a la vez, y la cantidad que las cumple a las dos es el lote legal
    inmediatamente por debajo del restante. Cuando el restante no llega ni al lote
    minimo, comprar menos que el minimo no es una opcion que Kern pueda imprimir
    (`src.constraints.feasible_neighbours`), asi que la Q baja hasta el minimo legal y
    no mas -- la compuerta lo acepta (`jobs.qa._horizon_issues`).
    """
    reglas = {"minimum_order_quantity": moq or 0.0, "order_multiple": multiple or 0.0}
    if is_feasible_lot(tope, **reglas):
        return tope
    arriba, abajo = feasible_neighbours(tope, **reglas)
    return abajo if abajo is not None else arriba


def _order_now(
    kind: str, position: float | None, reorder_point: float,
    order_quantity: float | None, order_up_to: float | None,
) -> tuple[bool | None, float | None]:
    """The execution signal of the policy at ``position``: (s,Q) orders Q once the
    position is at or below s; (R,S) orders up to S at the review. None when there
    is no position to compare against.

    Una COBERTURA PROVISIONAL usa la aritmetica de ``(R,S)`` y no por comodidad: es
    literalmente un objetivo de reposicion sin lote, y su ``order_up_to`` es la
    cobertura. Sin esta rama el arreglo del arranque muerto quedaba a medias -- el
    marcador ejecuta la cobertura (asi se midieron los +19,4 puntos) pero la lista
    "que pedir hoy" que lee el cliente no la nombraba, o sea que el numero publicado
    describia una corrida que un cliente no podia reproducir.
    """
    if position is None:
        return None, None
    if kind == "(s, Q)":
        due = position <= reorder_point
        return due, (float(order_quantity or 0.0) if due else 0.0)
    if (kind == "(R, S)" or kind in PROVISIONAL_POLICY_KINDS) and order_up_to is not None:
        due = position < order_up_to
        return due, (max(0.0, float(order_up_to) - position) if due else 0.0)
    return None, None


def _observed_skewness(series) -> float | None:
    """Sample skewness of the demand history, or None when it cannot be estimated.

    Feeds ``demand_distribution='auto'``: the engine fits a gamma instead of a
    normal when skewness exceeds the coefficient of variation (Vandeput ch. 9).
    """
    arr = np.asarray(series, dtype=float)
    if arr.size < 3:
        return None
    # Skewness of a (near-)constant series is undefined: scipy warns about
    # catastrophic cancellation and returns a meaningless value. Flat demand is
    # not skewed, so let the engine keep the normal.
    spread = float(np.std(arr))
    if not np.isfinite(spread) or spread <= 1e-9 * max(1.0, abs(float(np.mean(arr)))):
        return None
    value = float(_sps.skew(arr))
    return value if np.isfinite(value) else None


def _lines_by_sku(order_lines: pd.DataFrame | None) -> dict[str, list[dict]]:
    """Group the client's outbound order lines by SKU, once, before the per-SKU loop."""
    if order_lines is None or order_lines.empty or "product_id" not in order_lines.columns:
        return {}
    return {
        str(pid): group.to_dict("records")
        for pid, group in order_lines.groupby("product_id")
    }


def _realized_service(
    pid: str,
    lines: list[dict],
    observed_fill_rates: dict[str, float] | None,
    stockout_days: dict[str, int] | None,
    promise_days: dict[str, float] | float | None = None,
    *,
    shipped_units: float,
    unit_cost: float,
    n_periods: int,
) -> order_fulfillment.RealizedService:
    """The service this SKU actually delivered, from the strongest evidence available.

    Order lines beat a reported rate: they carry the dates OTIF needs and the days
    a scalar beta can never produce. A reported rate is still a fact about the
    client's operation, so it is carried through and labelled 'client_reported'
    rather than being consumed by the tuning loop and thrown away. With neither,
    the answer is 'not_measured' - never a zero nobody observed.
    """
    days = (stockout_days or {}).get(pid)
    if lines:
        return order_fulfillment.measure_from_order_lines(
            pid, lines, stockout_days=days, periods_covered=n_periods,
            promise_days=promise_days,
        )
    reported = (observed_fill_rates or {}).get(pid)
    if reported is None:
        return order_fulfillment.not_measured(pid)
    return order_fulfillment.from_reported_fill_rate(
        pid,
        float(reported),
        shipped_units=shipped_units,
        unit_cost=unit_cost if unit_cost > 0 else None,
        periods_covered=n_periods,
        stockout_days=days,
    )


def _model_implied_beta(pol, kind: str, mean_demand_per_period: float, review_period: float = 1.0):
    """beta = 1 - Us/dc for the policy just sized (Vandeput 7.3.1). A MODEL number.

    Reported next to - never instead of - ``realized_service``: it says what the
    buffer is designed to achieve, which is exactly the claim a client checks
    against their own fill rate. ``None`` where the identity is undefined
    (no cycle demand, or no dispersion over the risk period).
    """
    cycle_demand = pol.order_quantity if kind == "(s, Q)" else mean_demand_per_period * review_period
    sigma_x = pol.demand_std_risk_period
    if not cycle_demand or cycle_demand <= 0 or sigma_x <= 0:
        return None
    return fill_rate_from_safety_stock(
        pol.safety_stock.safety_stock, float(cycle_demand), float(sigma_x)
    )


def _status(intermittent: bool, bias: float) -> str:
    if intermittent:
        return "review"
    if abs(bias) >= 2:
        return "high_bias"
    return "ok"


def run(
    demand: pd.DataFrame,
    *,
    service_level: float = 0.95,
    holding_rate: float = 0.25,
    order_cost: float = 75.0,
    budget: float | None = None,
    periods_per_year: float = PERIODS_PER_YEAR,
    service_levels: dict[str, float] | None = None,
    differentiate_by_class: bool = False,
    lead_times: dict[str, float] | None = None,
    lead_time_stds: dict[str, float] | None = None,
    observed_fill_rates: dict[str, float] | None = None,
    target_fill_rate: float = 0.95,
    order_lines: pd.DataFrame | None = None,
    stockout_days: dict[str, int] | None = None,
    delivery_promise_days: dict[str, float] | float | None = None,
    intake_quality: IntakeQuality | None = None,
    intake_quality_threshold: float = 0.20,
    min_periods: int = MIN_PERIODS_FOR_POLICY,
    period_freq: str | None = None,
    oos_origins: int = DEFAULT_OOS_ORIGINS,
    minimum_order_quantities: dict[str, float] | None = None,
    order_multiples: dict[str, float] | None = None,
    on_hand_by_sku: dict[str, float] | None = None,
    on_order_by_sku: dict[str, float] | None = None,
    position_note: str = "",
    end_dates: dict[str, object] | None = None,
    horizon_note: str = "",
) -> JobReport:
    """Run the inventory-optimization analysis over canonical demand.

    Safety stock is differentiated per SKU: ``service_levels`` overrides the cycle service
    level per product (or set ``differentiate_by_class`` to derive it from the ABC-XYZ class);
    ``lead_times`` overrides the per-SKU risk period (e.g. observed supplier lead times); and
    ``observed_fill_rates`` closes the loop, nudging each SKU's service level toward
    ``target_fill_rate`` so chronic stockouts raise the buffer and over-service relaxes it.

    Two inputs describe what the client's customers actually experienced, and they
    only ever feed ``SkuRecommendation.realized_service`` - never the policy:

    ``order_lines``
        One row per outbound order line, with ``product_id`` plus any of
        ``ordered_qty``, ``delivered_qty``, ``promised_date``, ``delivered_date``
        and ``unit_price``. The only input that can produce an outbound OTIF.
    ``stockout_days``
        Days at zero stock per SKU, when the client measured them. Without it the
        count falls back to the dates the order lines went short, a lower bound.

    ``observed_fill_rates`` doubles as the weakest of the three: absent order lines
    it is still reported, as a client-reported measurement, instead of being
    consumed by the tuning loop and discarded. A SKU with none of the three comes
    back 'not_measured' rather than as a service number nobody observed.

    ``minimum_order_quantities`` / ``order_multiples`` override, per SKU, the
    supplier terms the canonical frame carries in its ``moq`` / ``order_multiple``
    columns (jobs/intake.py emits them only when the file had them). An (s,Q)
    quantity is then the cheapest lot the supplier accepts, with the runner-up
    kept on the record -- see ``SkuRecommendation.lot_rule``.

    ``on_hand_by_sku`` / ``on_order_by_sku`` are today's stock position, per SKU
    (``jobs.intake.position_maps`` produces both from the client's file). With
    them every record also says whether an order is due NOW, sized against the
    position - on hand plus on order - rather than the shelf alone. Passing
    ``on_order_by_sku=None`` is not the same as passing zeros: it means nobody
    measured the in-transit, the records carry ``on_order=None`` and the report
    says the due-now list could not be netted. ``position_note`` rides along to
    the deliverable, like ``order_lines.attrs['service_note']`` does.
    """
    if not 0 < service_level < 1:
        raise ValueError("service_level must be in (0, 1)")
    if holding_rate <= 0 or order_cost <= 0:
        raise ValueError("holding_rate and order_cost must be > 0")

    source = DataFrameDemandSource(
        demand, periods_per_year=periods_per_year, period_freq=period_freq
    )
    base_levels: dict[str, float] = dict(service_levels or {})
    if not base_levels and differentiate_by_class:
        class_items = [
            {"product_id": p, "unit_cost": source.metadata(p).mean_unit_cost,
             "demand": list(source.demand_series(p))}
            for p in source.list_products()
        ]
        base_levels = class_service_levels(classify_portfolio(class_items))
    lines_by_sku = _lines_by_sku(order_lines)
    # La fecha de fin de vida comercial por SKU (`jobs.intake.end_date_map`), y el "hoy"
    # contra el que se mide cuanto falta. `as_of` es la ULTIMA fecha del historico y
    # NUNCA el reloj: un archivo que termina hace tres meses mediria contra un horizonte
    # que no es el suyo, y con una fecha de fin intermedia todo el catalogo saldria
    # `expired`. Es la misma regla que `tools._fecha_param` aplica en la frontera JSON --
    # el reloj como fallback silencioso fecha el reporte donde no corresponde.
    fin_por_sku = dict(end_dates or {})
    as_of = None
    if fin_por_sku and "date" in getattr(demand, "columns", ()):
        _fechas = pd.to_datetime(demand["date"], errors="coerce")
        as_of = _fechas.max() if _fechas.notna().any() else None
    recs: list[SkuRecommendation] = []
    items: list[InventoryItem] = []

    for pid in source.list_products():
        series = source.demand_series(pid)
        meta = source.metadata(pid)
        restante_periodos = periods_until(
            as_of, fin_por_sku.get(pid), periods_per_year=periods_per_year
        )
        fc = forecast_with_oos_sigma(series, max_origins=oos_origins)
        inputs = fc.to_engine_inputs(periods_per_year=periods_per_year)
        lead = lead_times.get(pid, meta.lead_time_periods) if lead_times else meta.lead_time_periods
        # sigma_L. An explicit override (lead_time_stds) wins; else fall back to the
        # CSV's own lead_time_std column (meta.lead_time_std_periods, wired in src/sources.py
        # long before this override dict existed) - both are client-supplied data, just
        # via different paths, so both are labeled "client_file". Only a true absence of
        # both, i.e. the CSV carries no such column, is a "default", not a measurement -
        # and the deliverable has to say which it was.
        override = (lead_time_stds or {}).get(pid)
        if override is not None:
            lt_std, lt_std_source = float(override), "client_file"
        else:
            lt_std = meta.lead_time_std_periods
            lt_std_source = "client_file" if lt_std > 0 else "defaulted_zero"
        sl = base_levels.get(pid, service_level)
        n_periods = int(len(series))
        # Measured BEFORE the policy gates below, and independently of them: what the
        # customer got last quarter does not stop being a fact because this SKU has too
        # little history to size a buffer from, or no usable cost on file.
        realized = _realized_service(
            pid, lines_by_sku.get(pid, []), observed_fill_rates, stockout_days,
            delivery_promise_days,
            shipped_units=float(np.sum(series)),
            unit_cost=meta.mean_unit_cost,
            n_periods=n_periods,
        )
        # Today's stock position is a FACT about the SKU, measured before any policy
        # gate: an unpriced SKU still carries what is on the shelf and inbound.
        on_hand, on_order, position = _stock_position(pid, on_hand_by_sku, on_order_by_sku)

        # Too little history to size ANY buffer honestly. This gate runs BEFORE the
        # zero-demand and zero-cost guards below on purpose: with two observations
        # you cannot conclude "this SKU has no demand", you can only conclude "I
        # have not seen enough of it" - and labelling the first as the second is
        # exactly the confident-but-wrong claim this whole gate exists to stop.
        #
        # Refuse rather than degrade. A quantity with no dispersion behind it is
        # indistinguishable, in the deliverable, from one backed by two years of
        # history; the operator has no way to tell them apart, so we must not
        # produce it. min_periods is a parameter (not a hardcoded read of the
        # constant) so a client with, say, monthly buckets can raise it - lowering
        # it below the default is a decision someone has to make explicitly.
        if n_periods < min_periods:
            # Negarse a DIMENSIONAR no obliga a no decir nada. Son dos preguntas
            # distintas y hasta el 2026-09-20 se contestaban las dos con silencio:
            #
            #   "cuanto buffer" -> necesita sigma, y con menos de `min_periods`
            #       observaciones no hay sigma honesta. Sigue sin contestarse.
            #   "cuanto hace falta para llegar a la proxima reposicion" -> necesita
            #       la media de lo que se vendio y el plazo del proveedor, y los dos
            #       son hechos MEDIDOS que el archivo ya trae.
            #
            # Medido en el marcador, el silencio no era neutro: el 56% de la brecha
            # contra un punto de reorden fijo era que Kern no podia decidir durante el
            # primer tercio de la corrida (documentation/KERN_POR_QUE_BAJO_EL_PISO.md).
            # Con menos de `MIN_PERIODS_FOR_COVER` ni siquiera hay una media que citar
            # y la negativa se queda entera.
            observada = float(np.mean(series)) if n_periods else 0.0
            if n_periods >= MIN_PERIODS_FOR_COVER and observada > 0 and meta.mean_unit_cost > 0:
                tau = float(lead) + PROVISIONAL_REVIEW_PERIODS
                cobertura = provisional_cover(
                    observada, float(lead), review_period=PROVISIONAL_REVIEW_PERIODS
                )
                # El tope por fecha de fin vale IGUAL para una cobertura: no acumules por
                # encima de lo que todavia podes vender es una regla sobre el objetivo, y
                # una cobertura es un objetivo de reposicion. Dejarla afuera seria peor
                # que en una politica, porque una cobertura no tiene buffer: lo unico que
                # la acota es el plazo.
                tope = cap_policy(
                    cobertura, cobertura,
                    mean_per_period=observada, periods_remaining=restante_periodos,
                )
                cobertura_sin_tope = cobertura if tope.capped else None
                cobertura = tope.trigger
                # La cobertura dimensiona CUANTO; la posicion dice CUANDO, igual que
                # en una politica. Omitirlo dejaria la cobertura en el entregable y
                # fuera de la lista de lo que hay que pedir hoy, que es donde el
                # arranque muerto se siente.
                cobertura_due, cobertura_qty = _order_now(
                    "provisional_cover", position, cobertura, None, cobertura
                )
                recs.append(
                    SkuRecommendation(
                        product_id=pid, method="none", intermittent=False,
                        # `forecast` sigue en None: la compuerta prohibe citar una
                        # extrapolacion, y la media medida viaja en su propio campo.
                        forecast=None, error_std=0.0, bias=0.0, mae=0.0,
                        policy_kind="provisional_cover",
                        # Disparador y objetivo son el MISMO numero: se repone hasta la
                        # cobertura, sin lote. Un EOQ optimiza el canje entre ordenar y
                        # mantener, y optimizar sobre dos observaciones es fingir que se
                        # conoce la curva.
                        order_quantity=None, order_up_to=cobertura,
                        reorder_point=cobertura,
                        # Sin buffer y sin z, y no es un descuido: es lo que separa una
                        # cobertura de una politica. La ausencia en el CAMPO es como el
                        # registro lo declara.
                        safety_stock=None, z_factor=None,
                        service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                        # No se estaciona capital de ciclo ni de seguridad: no hay
                        # politica que lo dimensione. La inversion de esta linea es lo
                        # que cuesta la cobertura, y va entera al ciclo.
                        cycle_investment=cobertura * meta.mean_unit_cost,
                        ss_investment=0.0,
                        investment=cobertura * meta.mean_unit_cost,
                        status="review",
                        sigma_source="insufficient_history",
                        n_periods=n_periods,
                        realized_service=realized,
                        on_hand=on_hand, on_order=on_order, inventory_position=position,
                        order_now=cobertura_due, order_qty_now=cobertura_qty,
                        observed_mean_per_period=observada,
                        cover_periods=tau,
                        end_date=_fecha_texto(fin_por_sku.get(pid)),
                        horizon_verdict=tope.verdict,
                        periods_remaining=tope.periods_remaining,
                        remaining_demand=tope.remaining_demand,
                        # Disparador y objetivo son el MISMO numero en una cobertura, asi
                        # que los dos `*_before_horizon` llevan el mismo valor: no es
                        # redundancia, es que un consumidor que lee una sola de las dos
                        # mitades tiene que encontrar la de antes del tope igual.
                        reorder_point_before_horizon=cobertura_sin_tope,
                        order_up_to_before_horizon=cobertura_sin_tope,
                    )
                )
                continue

            recs.append(
                SkuRecommendation(
                    product_id=pid, method="none", intermittent=False,
                    # El pronostico SI se calculo unas lineas arriba, y a proposito
                    # no se publica: la compuerta existe porque con menos de
                    # `min_periods` observaciones no se puede citar. Publicarlo
                    # aca la derrotaria por la puerta de atras.
                    forecast=None, error_std=0.0, bias=0.0, mae=0.0,
                    policy_kind="insufficient_history", order_quantity=None, order_up_to=None,
                    reorder_point=None, safety_stock=None, z_factor=None,
                    service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                    cycle_investment=0.0, ss_investment=0.0, investment=0.0,
                    status="review",
                    sigma_source="insufficient_history",
                    n_periods=n_periods,
                    realized_service=realized,
                    on_hand=on_hand, on_order=on_order, inventory_position=position,
                )
            )
            continue

        # A SKU with zero aggregate demand (brand-new/seasonal SKU, or a messy
        # export that split one SKU's sales across spellings) would otherwise
        # reach compute_eoq's annual_demand > 0 guard and raise, killing the
        # WHOLE portfolio's run. Isolate it instead: skip and record it as a
        # distinct, flagged recommendation, then keep going.
        if inputs["annual_demand"] <= 0:
            # Los ceros de ESTA rama se quedan, y no es un descuido: cuando la
            # demanda anual da EXACTAMENTE cero se midio y dio cero, asi que "el
            # pronostico es 0 y el disparador es 0" es una medicion, no una
            # ausencia -- la mitad de la regla que las otras dos ramas violaban. Y
            # hay una consecuencia que se pierde si se anulan: el puente
            # (`jobs/state_bridge.py`) escribe la linea y el Tower emite
            # `dead_stock` sobre stock parado sin demanda, que es el evento
            # correcto para este caso.
            #
            # Un `annual_demand` NEGATIVO cae en la misma rama y NO es lo mismo:
            # no es la medicion de nada, es un pronostico que salio bajo cero
            # (`ForecastResult.to_engine_inputs` multiplica por los periodos del
            # anio y no acota por abajo). Publicar 0.0 ahi diria "medimos cero"
            # sobre un SKU que puede estar vendiendo.
            #
            # Y NO es un caso hipotetico, aunque al escribir esto se creyo que si:
            # `auto_ets` extrapola bajo cero sobre una serie decreciente larga.
            # Medido, con catorce semanas de [200, 185, ..., 20, 10] -- 1.440
            # unidades vendidas -- el pronostico sale -8,57 y la demanda anual
            # -445,74. Sin esta distincion ese SKU publicaria ceros y el Control
            # Tower lo reportaria como `dead_stock`: "120 units on hand with no
            # demand", sobre uno que vendio 1.440 unidades en tres meses.
            medido_en_cero = inputs["annual_demand"] == 0
            cero_o_nada = 0.0 if medido_en_cero else None
            recs.append(
                SkuRecommendation(
                    product_id=pid, method="none", intermittent=False,
                    forecast=cero_o_nada, error_std=0.0, bias=0.0, mae=0.0,
                    policy_kind="skipped_zero_demand", order_quantity=None, order_up_to=None,
                    reorder_point=cero_o_nada, safety_stock=cero_o_nada,
                    z_factor=cero_o_nada,
                    service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                    cycle_investment=0.0, ss_investment=0.0, investment=0.0,
                    status="review",
                    n_periods=n_periods,
                    realized_service=realized,
                    on_hand=on_hand, on_order=on_order, inventory_position=position,
                )
            )
            continue

        # Symmetric to the zero-demand skip above, on the cost axis instead of the
        # demand axis: src/sources.py already excludes non-positive unit_cost rows
        # from the mean, so mean_unit_cost <= 0 here means NO row for this SKU ever
        # carried a usable cost. Without it, holding_cost's 1e-6 floor a few lines
        # down would make compute_eoq's q_star = sqrt(2*S*D/h) explode - not a
        # crash, a silently "valid-looking" order of hundreds of thousands of units
        # that the caller has no way to distinguish from a real recommendation.
        if meta.mean_unit_cost <= 0:
            recs.append(
                SkuRecommendation(
                    product_id=pid, method="none", intermittent=False,
                    # Misma regla que `insufficient_history`: la demanda de este
                    # SKU es real y positiva, lo que falta es el costo. Un cero
                    # aca diria que no vende.
                    forecast=None, error_std=0.0, bias=0.0, mae=0.0,
                    policy_kind="skipped_zero_cost", order_quantity=None, order_up_to=None,
                    reorder_point=None, safety_stock=None, z_factor=None,
                    service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                    cycle_investment=0.0, ss_investment=0.0, investment=0.0,
                    status="review",
                    n_periods=n_periods,
                    realized_service=realized,
                    on_hand=on_hand, on_order=on_order, inventory_position=position,
                )
            )
            continue

        holding_cost = max(holding_rate * meta.mean_unit_cost, 1e-6)
        if observed_fill_rates and pid in observed_fill_rates:
            sl = tune_service_level(
                sl, observed_fill_rates[pid], target_fill_rate,
                step=SERVICE_LEVEL_TUNING_STEP,
            )
        skewness = _observed_skewness(series)

        if fc.is_intermittent:
            pol = periodic_review_rs(
                annual_demand=inputs["annual_demand"],
                mean_demand_per_period=inputs["mean_demand_per_period"],
                demand_std_per_period=inputs["demand_std_per_period"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                lead_time_periods=lead,
                lead_time_std=lt_std,
                review_period=1.0,
                cycle_service_level=sl,
                demand_distribution="auto",
                observed_skewness=skewness,
            )
            kind, order_quantity = "(R, S)", None
        else:
            pol = continuous_review_sq(
                annual_demand=inputs["annual_demand"],
                mean_demand_per_period=inputs["mean_demand_per_period"],
                demand_std_per_period=inputs["demand_std_per_period"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                lead_time_periods=lead,
                lead_time_std=lt_std,
                cycle_service_level=sl,
                demand_distribution="auto",
                observed_skewness=skewness,
            )
            kind, order_quantity = "(s, Q)", pol.order_quantity

        # Supplier terms. Both come from the file (src/sources.py reads intake's
        # moq / order_multiple columns) unless the caller overrode them per SKU.
        moq = float((minimum_order_quantities or {}).get(pid, meta.minimum_order_quantity))
        multiple = float((order_multiples or {}).get(pid, meta.order_multiple))
        rule = lot_rule(moq, multiple)
        q_unconstrained: float | None = None
        penalty, alt_qty, alt_penalty = 0.0, None, None
        if kind == "(s, Q)" and rule:
            # The cheapest lot the supplier accepts, valued on the same cost curve
            # the EOQ came from, with the other neighbour kept as the second
            # terminal. Q, cycle stock, the implied beta and the budget item all
            # move together to the chosen lot: a plan that rounds Q but keeps the
            # cycle investment of the unrounded one understates what it costs.
            decision = cheapest_feasible_lot(
                pol.order_quantity,
                annual_demand=inputs["annual_demand"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                minimum_order_quantity=moq,
                order_multiple=multiple,
            )
            q_unconstrained = pol.order_quantity
            order_quantity = decision.chosen.quantity
            penalty = decision.chosen.penalty_usd
            if decision.alternative is not None:
                alt_qty = decision.alternative.quantity
                alt_penalty = decision.alternative.penalty_usd
            if decision.snapped:
                pol = replace(
                    pol, order_quantity=order_quantity,
                    expected_cycle_stock=order_quantity / 2.0,
                )

        ss = pol.safety_stock.safety_stock
        cycle_units = pol.expected_cycle_stock
        reorder = inputs["mean_demand_per_period"] * lead + ss  # mu*L + safety (lead-only)

        # No acumules por encima de lo que todavia podes vender (`src/horizon.py`). Se
        # aplica ANTES de `_order_now` a proposito: topear despues dejaria el disparador
        # bajo y la orden de hoy calculada contra el objetivo viejo, que es justo la
        # mitad del arreglo que la medicion dice que no sirve sola.
        #
        # CUAL es la segunda mitad depende de la politica: un (s,Q) trae
        # `order_up_to=None` y sus dos cantidades son `s` y `Q`; un (R,S) trae `S`.
        # Pasarle siempre `order_up_to` dejaba la decision en manos del disparador solo,
        # y una `Q` mayor que todo lo que quedaba por vender salia sin topear -- medido
        # sobre un SKU con 204 unidades de restante y Q=302. Ver `src/horizon.py`.
        es_sq = kind == "(s, Q)"
        segunda = order_quantity if es_sq else pol.order_up_to_level
        tope = cap_policy(
            reorder, segunda,
            mean_per_period=inputs["mean_demand_per_period"],
            periods_remaining=restante_periodos,
        )
        rp_previo = oup_previo = q_previo = cycle_previo = None
        order_up_to_final = pol.order_up_to_level
        veredicto = tope.verdict
        if tope.capped:
            # Un `*_before_horizon` se llena SOLO si ese numero se movio. El tope puede
            # bajar una mitad y dejar la otra igual -- es el caso comun: un (s,Q) cuya Q
            # excede el restante pero cuyo disparador no --, y escribir el valor viejo en
            # las dos haria leer un cambio donde no lo hubo.
            if tope.trigger < reorder:
                rp_previo, reorder = reorder, tope.trigger
            if es_sq:
                if order_quantity is not None and tope.target < order_quantity:
                    # El tope corre DESPUES del redondeo a lote, asi que su resultado
                    # tiene que volver a ser un lote que el proveedor acepte: el restante
                    # crudo (133,8 con multiplo 12) tumbaba la corrida entera en
                    # `qa._lot_issues`. Revision del PR #369, 2026-10-09.
                    q_topeada = _lote_legal_bajo(tope.target, moq, multiple) if rule else tope.target
                    if q_topeada < order_quantity:
                        q_previo, order_quantity = order_quantity, q_topeada
            elif pol.order_up_to_level is not None and tope.target < pol.order_up_to_level:
                oup_previo, order_up_to_final = pol.order_up_to_level, tope.target
            if rp_previo is None and q_previo is None and oup_previo is None:
                # Lo unico que el tope queria bajar era una Q que ya esta en el lote
                # minimo del proveedor: bajo los terminos vigentes la fecha no cambia
                # nada, y decir "applied" sin ninguna cantidad movida es lo que la
                # compuerta rechaza.
                veredicto = "inert"
        if veredicto == "applied":
            # El capital sigue a la cantidad. Sin esto el tope movia `reorder_point`,
            # `order_up_to` y `Q` y dejaba `cycle_investment`, `ss_investment`,
            # `investment` y el titular `requested_investment` calculados sobre la
            # politica SIN topear: medido sobre una cartera real, 12.018 usd de capital
            # liberado que el titular no mostraba. Es la parte que faltaba para estar a
            # la par de las otras dos cantidades que se mueven despues de dimensionar --
            # el redondeo a lote legal (arriba) y la cobertura provisional, que ya
            # recalculaban su capital. Ver `src.horizon.capped_cycle_stock`: NO toca el
            # safety stock, asi que las dos identidades del registro siguen en pie.
            cycle_previo, cycle_units = cycle_units, capped_cycle_stock(
                cycle_units, ss,
                remaining_demand=tope.remaining_demand,
                order_quantity=order_quantity if es_sq else None,
            )
            if cycle_previo <= cycle_units:  # el tope no libero capital en este SKU
                cycle_previo = None
        # Perishable / shelf life bounding (src/perishables.py)
        shelf_life = meta.days_to_expiry
        perish_verdict = None
        spoilage_units = 0.0
        spoilage_usd = 0.0
        q_before_shelf_life = None

        if shelf_life is not None and shelf_life > 0 and es_sq and order_quantity is not None and order_quantity > 0:
            daily_d = inputs["mean_demand_per_period"] * (periods_per_year / 365.0)
            p_lim = bound_order_quantity_by_expiry(
                unconstrained_q=order_quantity,
                reorder_point=reorder,
                daily_demand=daily_d,
                shelf_life_days=shelf_life,
                unit_cost=meta.mean_unit_cost,
                moq=moq if rule else None,
                order_multiple=multiple if rule else None,
            )
            perish_verdict = p_lim.verdict
            spoilage_units = p_lim.spoilage_risk_units
            spoilage_usd = p_lim.spoilage_risk_usd
            if p_lim.is_constrained and p_lim.constrained_q < order_quantity:
                q_before_shelf_life = order_quantity
                order_quantity = p_lim.constrained_q
                cycle_units = order_quantity / 2.0

        cycle_inv = cycle_units * meta.mean_unit_cost
        ss_inv = ss * meta.mean_unit_cost
        due_now, qty_now = _order_now(kind, position, reorder, order_quantity, order_up_to_final)

        recs.append(
            SkuRecommendation(
                product_id=pid, method=fc.method, intermittent=fc.is_intermittent,
                forecast=fc.forecast, error_std=fc.error_std, bias=fc.bias, mae=fc.mae,
                policy_kind=kind, order_quantity=order_quantity, order_up_to=order_up_to_final,
                reorder_point=reorder, safety_stock=ss, z_factor=pol.safety_stock.service_level_factor,
                service_level=sl,
                unit_cost=meta.mean_unit_cost, lead_periods=lead,
                cycle_investment=cycle_inv, ss_investment=ss_inv, investment=cycle_inv + ss_inv,
                status=_status(fc.is_intermittent, fc.bias),
                sigma_source=fc.sigma_source,
                buffer_distribution=pol.safety_stock.distribution,
                lead_time_std=lt_std, lead_time_std_source=lt_std_source,
                risk_periods=pol.safety_stock.risk_periods,
                n_periods=n_periods,
                realized_service=realized,
                model_implied_beta=_model_implied_beta(
                    pol, kind, inputs["mean_demand_per_period"]
                ),
                order_quantity_unconstrained=q_unconstrained,
                lot_rule=rule, lot_moq=moq, lot_multiple=multiple,
                lot_penalty_usd=penalty,
                lot_alternative_qty=alt_qty, lot_alternative_penalty_usd=alt_penalty,
                on_hand=on_hand, on_order=on_order, inventory_position=position,
                order_now=due_now, order_qty_now=qty_now,
                end_date=_fecha_texto(fin_por_sku.get(pid)),
                horizon_verdict=veredicto,
                periods_remaining=tope.periods_remaining,
                remaining_demand=tope.remaining_demand,
                reorder_point_before_horizon=rp_previo,
                order_up_to_before_horizon=oup_previo,
                order_quantity_before_horizon=q_previo,
                cycle_investment_before_horizon=(
                    None if cycle_previo is None else cycle_previo * meta.mean_unit_cost
                ),
                days_to_expiry=shelf_life,
                perishable_verdict=perish_verdict,
                spoilage_risk_units=spoilage_units,
                spoilage_risk_usd=spoilage_usd,
                order_quantity_before_shelf_life=q_before_shelf_life,
            )
        )
        items.append(InventoryItem(product_id=pid, order_quantity=2.0 * cycle_units, safety_stock=ss, unit_cost=meta.mean_unit_cost))

    cycle_floor = sum(it.cycle_investment for it in items)
    if budget is not None:
        plan = allocate_under_budget(items, budget)
        requested, final, scale, feasible = plan.requested_investment, plan.final_investment, plan.safety_stock_scale, plan.feasible
    else:
        requested = sum(it.investment for it in items)
        final, scale, feasible = requested, 1.0, True

    return JobReport(
        recommendations=recs,
        # ``getattr`` sobre ``.attrs``: un DataFrame siempre lo tiene, pero un llamador
        # puede pasar cualquier cosa con forma de frame y esto es un extra, no un
        # requisito. Un entregable no se cae por una nota que falta.
        service_note=str(getattr(order_lines, "attrs", {}).get("service_note", "") or ""),
        params={"service_level": service_level, "holding_rate": holding_rate, "order_cost": order_cost, "periods_per_year": periods_per_year},
        requested_investment=requested, cycle_floor=cycle_floor, final_investment=final,
        safety_stock_scale=scale, feasible=feasible, budget=budget,
        n_skus=len(recs), n_at_risk=sum(1 for r in recs if r.status == "high_bias"),
        n_intermittent=sum(1 for r in recs if r.intermittent),
        intake_quality=intake_quality, intake_quality_threshold=intake_quality_threshold,
        n_skipped_zero_demand=sum(1 for r in recs if r.policy_kind == "skipped_zero_demand"),
        n_skipped_zero_cost=sum(1 for r in recs if r.policy_kind == "skipped_zero_cost"),
        n_insufficient_history=sum(1 for r in recs if r.policy_kind == "insufficient_history"),
        n_provisional_cover=sum(
            1 for r in recs if r.policy_kind in PROVISIONAL_POLICY_KINDS
        ),
        provisional_investment=sum(
            r.investment for r in recs if r.policy_kind in PROVISIONAL_POLICY_KINDS
        ),
        n_lot_constrained=sum(1 for r in recs if r.policy_kind == "(s, Q)" and r.lot_rule),
        n_lot_snapped=sum(
            1 for r in recs
            if r.policy_kind == "(s, Q)" and r.order_quantity_unconstrained is not None
            and r.order_quantity != r.order_quantity_unconstrained
        ),
        lot_penalty_total_usd=sum(r.lot_penalty_usd for r in recs),
        n_lot_rule_unapplied=sum(1 for r in recs if r.policy_kind == "(R, S)" and r.lot_rule),
        n_with_end_date=sum(1 for r in recs if r.end_date),
        n_horizon_capped=sum(1 for r in recs if r.horizon_verdict == "applied"),
        n_horizon_declarable=sum(1 for r in recs if r.horizon_verdict in HORIZON_DECLARABLES),
        # Unidades de COMPRA que el tope saco del plan. Se mide sobre la cantidad que se
        # termina comprando -- `Q` en un (s,Q), el objetivo en un (R,S) o en una
        # cobertura -- y NO sobre el disparador: el disparador dice cuando pedir, no
        # cuanto, y sumarle su reduccion contaria unidades que nadie iba a comprar. En
        # una cobertura disparador y objetivo son el mismo numero, asi que sumar los dos
        # ademas lo contaria dos veces.
        horizon_units_freed=_unidades_liberadas(recs),
        horizon_capital_freed_usd=_capital_liberado(recs),
        horizon_note=horizon_note,
        n_positioned=sum(1 for r in recs if r.on_hand is not None),
        n_netted=sum(1 for r in recs if r.on_order is not None),
        n_order_now=sum(1 for r in recs if r.order_now),
        n_reorders_avoided_by_transit=sum(1 for r in recs if _reorder_avoided_by_transit(r)),
        units_on_order=sum(r.on_order for r in recs if r.on_order is not None),
        position_note=position_note,
    )


def _reorder_avoided_by_transit(r: SkuRecommendation) -> bool:
    """True when the shelf figure alone would have triggered an order but the stock
    already inbound covers it - the double buy that netting exists to prevent."""
    if r.order_now is None or r.on_order is None or r.on_order <= 0 or r.on_hand is None:
        return False
    on_shelf_alone, _ = _order_now(
        r.policy_kind, r.on_hand, r.reorder_point, r.order_quantity, r.order_up_to,
    )
    return bool(on_shelf_alone) and not r.order_now
