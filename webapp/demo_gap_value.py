"""Demo funnel: la brecha de plazo, en plata.

`webapp/demo_lead_time_scan.py` entrega la brecha en dias -- el hecho que se gana
el derecho a seguir hablando. Una brecha en dias no vende: 05-GTM-KERN §5 dice que
el discurso son **dos monedas**, liberacion de caja al balance para el gerente
general y ahorro recurrente al resultado para operaciones. Este modulo es esa
conversion, y es el puente del escaneo gratis al Diagnostico pago.

El metodo es una resta, no un modelo nuevo:

    SS_erp   = motor con el plazo DECLARADO y sigma_L = 0   <- lo que hace el ERP hoy
    SS_real  = motor con el plazo y la sigma_L OBSERVADOS   <- lo que las recepciones dicen
    plata    = (SS_real - SS_erp) x costo unitario

Los dos lados se reportan separados, y esto no es matiz de presentacion:

  * donde SS_real < SS_erp hay **caja liberable**: buffer parado que el plazo real
    no justifica.
  * donde SS_real > SS_erp hay **exposicion**: SKU que hoy estan cortos y se
    quiebran, y para los que el numero correcto es *subir* el inventario.

Reportar solo el primero convierte esto en "reduce inventario", que es exactamente
la frase que §5 prohibe porque operaciones la lee como "quieres que me quiebre".
El neto baja y el servicio sube, pero solo si se dicen las dos mitades.

Todo el motor ya existia: `src/risk_period.py:40` es literalmente la formula del
§5, `jobs/inventory_optimization.run` acepta `lead_times` y `lead_time_stds` por
SKU, y `src/supplier_scorecard.py` produce los tres numeros por proveedor. Este
modulo no calcula: corre el motor dos veces y resta.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from jobs import intake, inventory_optimization
from src.supplier_scorecard import (
    LEAD_TIME_MEASURED,
    SupplierScore,
    declared_lead_times_for_skus,
    lead_time_stds_for_skus,
    lead_times_for_skus,
    score_supplier,
)
from webapp.demo_lead_time_scan import MIN_DELIVERIES, to_deliveries

DAYS_PER_YEAR = 365.0

#: Costo de mantener inventario, anual, como fraccion del valor. 05-GTM-KERN §5
#: descompone 18-28% (capital 10-14, bodegaje 3-6, seguro 0,5-1, obsolescencia
#: 3-8) y manda usar el total, no solo el costo de capital: contra 10% el ahorro
#: recurrente sale a la mitad y la recuperacion se duplica.
HOLDING_RATE = 0.22

CTA_PATH = "/paquetes/diagnostico-arranque"


@dataclass(frozen=True)
class SkuGap:
    """Un SKU, sus dos dimensionamientos, y la diferencia en plata."""

    product_id: str
    supplier: str
    declared_lt_days: float
    observed_lt_days: float
    observed_lt_std_days: float
    ss_erp: float
    ss_real: float
    unit_cost: float

    @property
    def delta_units(self) -> float:
        return self.ss_real - self.ss_erp

    @property
    def delta_value(self) -> float:
        """Positivo = falta buffer (exposicion). Negativo = sobra (caja liberable)."""
        return self.delta_units * self.unit_cost


@dataclass(frozen=True)
class GapValue:
    gaps: tuple[SkuGap, ...]
    #: Recepciones descartadas por causa, de `to_deliveries`.
    dropped_receipts: dict[str, int]
    #: Lo que la demanda perdio en la ingesta. Un numero de plata construido sobre
    #: el 40% del archivo tiene que decir que era el 40%.
    intake_quality: intake.IntakeQuality | None
    qa_issues: tuple[str, ...]
    findings: tuple[str, ...]

    # -- el embudo de SKU -----------------------------------------------------
    # Cada cifra de plata de este resultado suma sobre `gaps`, que es una
    # interseccion estrechada tres veces. Sin estos contadores el entregable dice
    # "SKU valorizados: 12" sin decir 12 de cuantos, que es exactamente lo que el
    # docstring de este modulo prohibe. Son locales de `run_gap_value` y no se
    # pueden recalcular desde el dataclass: hacerlo obligaria a re-ingerir los dos
    # archivos, y dos ingestas del mismo archivo pueden discrepar.
    #: SKU distintos en el archivo de MOVIMIENTOS. El denominador de todo lo demas.
    n_demand_skus: int = 0
    #: SKU distintos que las RECEPCIONES identifican con un proveedor.
    n_receipt_skus: int = 0
    #: Los que estan en los dos archivos. Interseccion, no una suma.
    n_matched_skus: int = 0
    #: Los tres requisitos son PARALELOS, no encadenados: un SKU puede tener plazo
    #: observado sin declarado. Encadenarlos en un embudo daria barras que suben.
    n_with_declared: int = 0
    n_with_observed: int = 0
    n_with_std: int = 0
    #: Comparables que el motor NO dimensiono (menos de MIN_PERIODS_FOR_POLICY
    #: periodos de demanda, o sin costo usable), asi que no tienen los dos
    #: buffers que esta pantalla resta. Tienen plazo declarado, observado y
    #: dispersion -- la unica razon por la que quedan afuera es que no hay
    #: politica que comparar, y eso hay que decirlo: contarlos como una brecha
    #: de 0 diria "el ERP y la realidad coinciden en este SKU", que es lo
    #: contrario de "nadie lo dimensiono".
    n_unsized_skus: int = 0
    #: Recepciones que entraron, y las que quedaron fuera por proveedor corto --
    #: la cuarta causa de descarte, que `dropped_receipts` no ve.
    n_receipts_used: int = 0
    n_receipts_below_min: int = 0
    n_suppliers_below_min: int = 0

    @property
    def ok(self) -> bool:
        return not self.qa_issues

    @property
    def cash_release(self) -> float:
        """Caja liberable: el buffer que el plazo real no justifica. Positivo."""
        return -sum(g.delta_value for g in self.gaps if g.delta_value < 0)

    @property
    def exposure(self) -> float:
        """Lo que falta poner donde hoy se quiebran. Positivo."""
        return sum(g.delta_value for g in self.gaps if g.delta_value > 0)

    @property
    def net(self) -> float:
        """Negativo = el inventario total baja. Es el numero del balance."""
        return sum(g.delta_value for g in self.gaps)

    @property
    def recurring_saving(self) -> float:
        """Ahorro anual de bajar el buffer sobre-dimensionado.

        Se calcula sobre ``cash_release`` y NO sobre el neto: son dos decisiones
        separadas. Los SKU cuyo plazo declarado estaba inflado se pueden
        redimensionar hoy y ese ahorro es real, independiente de que en otros SKU
        haya que subir. Atarlo al neto esconde una accion ejecutable detras de otra.
        """
        return self.cash_release * HOLDING_RATE

    @property
    def net_carrying_change(self) -> float:
        """Cambio anual del costo de mantener si se hacen las dos cosas.

        **Positivo significa que mantener el inventario cuesta MAS**, y hay que
        decirlo. La salida #1 sola tiende a ese lado: sigma_L solo suma varianza,
        asi que salvo donde el plazo declarado estuviera inflado, medir la
        dispersion revela buffer que falta, no que sobra. Lo que baja el neto es
        el pooling multi-echelon (fuente #2 de 05-GTM-KERN §5), que necesita la
        red de bodegas y no estos dos archivos.
        """
        return self.net * HOLDING_RATE

    @property
    def headline(self) -> dict:
        under = [g for g in self.gaps if g.delta_value > 0]
        over = [g for g in self.gaps if g.delta_value < 0]
        worst = max(self.gaps, key=lambda g: abs(g.delta_value)) if self.gaps else None
        return {
            "n_skus": len(self.gaps),
            "n_under": len(under),
            "n_over": len(over),
            "cash_release": round(self.cash_release, 2),
            "exposure": round(self.exposure, 2),
            "net": round(self.net, 2),
            "recurring_saving": round(self.recurring_saving, 2),
            "net_carrying_change": round(self.net_carrying_change, 2),
            "holding_rate": HOLDING_RATE,
            "worst_sku": worst.product_id if worst else None,
            "worst_value": round(worst.delta_value, 2) if worst else None,
        }


def _days_to_periods(days: float, periods_per_year: float) -> float:
    """Dias -> periodos del modelo de inventario.

    El scorecard mide en dias y ``inventory_optimization.run`` espera periodos
    (semanales por defecto): pasarle dias infla el periodo de riesgo ~7x y con el
    todo el stock de seguridad. `lead_times_for_skus` avisa de esto en su docstring
    y es la clase de error que no se ve, porque el resultado sigue pareciendo un
    numero razonable.
    """
    return days * periods_per_year / DAYS_PER_YEAR


def sku_supplier_map(deliveries_by_supplier: dict[str, list[dict]]) -> dict[str, str]:
    """SKU -> el proveedor que mas se lo entrego.

    Un SKU con dos proveedores tiene dos plazos y dos dispersiones. Elegir el de
    mas recepciones es una simplificacion declarada, no la respuesta correcta: la
    correcta es un plazo mezclado ponderado por volumen, que es trabajo del
    Diagnostico pago. Empate: gana el nombre alfabeticamente menor, para que dos
    corridas sobre el mismo archivo den el mismo numero.
    """
    counts: dict[str, dict[str, int]] = {}
    for supplier, rows in deliveries_by_supplier.items():
        for row in rows:
            sku = row.get("product_id")
            if sku:
                counts.setdefault(str(sku), {})[supplier] = (
                    counts.setdefault(str(sku), {}).get(supplier, 0) + 1
                )
    return {
        sku: min(sorted(by_sup), key=lambda s: (-by_sup[s], s))
        for sku, by_sup in counts.items()
    }


def _qa_issues(
    gaps: list[SkuGap],
    *,
    scorecards: list[SupplierScore],
    sku_supplier: dict[str, str],
    cost_synthesized: bool,
) -> list[str]:
    """Nunca un "no" a secas: cada issue dice que falta y donde conseguirlo."""
    issues: list[str] = []
    if cost_synthesized:
        issues.append(
            "el archivo de demanda no trae costo unitario, asi que no hay cifra de "
            "plata que reportar -- el motor lo rellena con 1,0 por SKU y valorizar "
            "sobre eso seria inventar el numero. Esta en el maestro de articulos "
            "(costo estandar o promedio ponderado); lo exporta contabilidad o compras."
        )
    if not scorecards:
        issues.append(
            f"ningun proveedor llega a {MIN_DELIVERIES} recepciones con fechas "
            "validas: sin plazo observado no hay con que contrastar el declarado."
        )
    elif not any(
        s.lead_time_std is not None and s.lead_time_source == LEAD_TIME_MEASURED
        for s in scorecards
    ):
        issues.append(
            "hay plazo promedio pero ninguna dispersion medible (se necesitan 2+ "
            "recepciones por proveedor). La dispersion es el termino que el ERP no "
            "tiene y el que explica la mayor parte del buffer: sin ella la resta da "
            "casi cero y no dice nada."
        )
    if not sku_supplier:
        issues.append(
            "el export de recepciones no trae identificador de producto, asi que no "
            "se puede saber que SKU compra a que proveedor. Sin ese cruce el plazo "
            "medido no se puede aterrizar en ningun stock de seguridad."
        )
    if not gaps and not issues:
        issues.append(
            "ningun SKU aparece en los dos archivos a la vez. Suelen ser codigos "
            "distintos entre compras y ventas: el Diagnostico incluye la "
            "homologacion, el escaneo automatico no la intenta."
        )
    return issues


def _findings(value: GapValue) -> tuple[str, ...]:
    h = value.headline
    findings: list[str] = []

    if value.net < 0:
        findings.append(
            f"El inventario total baja ${abs(value.net):,.0f} y el servicio sube. "
            f"No es un intercambio: son {h['n_over']} SKU que bajan y "
            f"{h['n_under']} que suben, y el neto queda a favor porque el buffer "
            "que ya tienen esta mal repartido, no porque falte."
        )
    else:
        # El caso comun, y hay que decirlo sin disfrazarlo: medir la dispersion
        # revela buffer que falta. No es una mala noticia -- es riesgo que ya
        # estaban corriendo sin tenerlo en el balance.
        findings.append(
            f"Dimensionado contra los plazos reales, el inventario total sube "
            f"${value.net:,.0f}. Eso no es un costo nuevo: es riesgo que ya estabas "
            f"corriendo y que no aparecia en ninguna parte. Los plazos observados "
            f"piden mas buffer que el que el sistema calcula en {h['n_under']} de "
            f"{h['n_skus']} SKU, y la diferencia sale casi entera de la dispersion "
            "del plazo, que tu ERP no tiene como campo."
        )

    findings.append(
        f"${value.cash_release:,.0f} en caja liberable, una vez, al balance: "
        f"buffer parado en {h['n_over']} SKU cuyo plazo declarado estaba inflado "
        f"respecto de lo que el proveedor realmente demora. A "
        f"{HOLDING_RATE * 100:.0f}% de costo de mantener son "
        f"${value.recurring_saving:,.0f} al anio, y es ejecutable por si solo: no "
        "depende de que hagas nada con los SKU que estan cortos."
    )

    if h["n_under"]:
        peor_corto = max(
            (g for g in value.gaps if g.delta_value > 0), key=lambda g: g.delta_value
        )
        findings.append(
            f"${value.exposure:,.0f} de exposicion en {h['n_under']} SKU cuyo plazo "
            f"real es peor que el declarado: hoy se quiebran y la reunion concluye "
            f"que el proveedor no cumple. El mayor es {peor_corto.product_id} "
            f"({peor_corto.supplier}): la ficha dice "
            f"{peor_corto.declared_lt_days:.0f} dias, las recepciones "
            f"{peor_corto.observed_lt_days:.0f} con desviacion "
            f"{peor_corto.observed_lt_std_days:.0f}."
        )
        findings.append(
            "Y la pregunta que este escaneo no puede contestar: cuantas "
            "detenciones o compras de emergencia tuviste el anio pasado por un "
            "item que no estaba. Esa cifra no esta arriba porque nadie la "
            f"registra, y en repuestos suele ser mayor que los ${value.exposure:,.0f}."
        )

    q = value.intake_quality
    if q is not None and q.n_dropped:
        findings.append(
            f"Se descartaron {q.n_dropped} de {q.n_raw} filas del archivo de demanda "
            f"({q.dropped_fraction * 100:.0f}%) por fecha o cantidad ilegibles. "
            "Los numeros de arriba salen del resto."
        )
    return tuple(findings)


def run_gap_value(
    receipts: pd.DataFrame,
    demand: pd.DataFrame,
    *,
    service_level: float = 0.95,
    periods_per_year: float = inventory_optimization.PERIODS_PER_YEAR,
    period: str = "W",
) -> GapValue:
    """Recepciones + demanda -> la brecha de plazo valorizada, con compuerta de QA.

    Levanta ValueError cuando a un archivo le faltan columnas estructurales; el
    endpoint lo mapea a un 400 accionable.
    """
    grouped, dropped = to_deliveries(receipts)
    scorecards = [
        score_supplier(supplier, rows)
        for supplier, rows in sorted(grouped.items())
        if len(rows) >= MIN_DELIVERIES
    ]
    sku_supplier = sku_supplier_map(grouped)
    # La cuarta causa de descarte: filas que se leyeron bien y aun asi no entraron
    # en ningun numero, porque su proveedor no junta MIN_DELIVERIES.
    _short = {s: rows for s, rows in grouped.items() if len(rows) < MIN_DELIVERIES}

    mapping = intake.detect_columns(demand)
    if mapping.unmatched_required:
        raise ValueError(
            "al archivo de demanda le faltan columnas: "
            f"{', '.join(mapping.unmatched_required)}. Se necesita una fila por "
            "movimiento con fecha, producto, cantidad y costo unitario. "
            f"Columnas vistas: {list(demand.columns)[:12]}"
        )
    canonical, quality = intake.normalize_tracked(demand, mapping, period=period)
    cost_synthesized = "unit_cost" in quality.fully_synthesized_columns

    declared_days = declared_lead_times_for_skus(scorecards, sku_supplier)
    observed_days = lead_times_for_skus(scorecards, sku_supplier)
    observed_std_days = lead_time_stds_for_skus(scorecards, sku_supplier)

    # Solo los SKU con las tres cosas: declarado (el contrafactual), observado y
    # dispersion observada. Sin dispersion la resta da casi cero y el numero, en
    # vez de ser conservador, seria mudo sobre el termino que importa.
    comparables = sorted(
        set(declared_days) & set(observed_days) & set(observed_std_days)
    )

    # El embudo, todo intersecado contra los SKU de la demanda para que cada parte
    # sea una fraccion valida del mismo denominador. Los SKU que solo aparecen en
    # las recepciones se cuentan aparte: no son un subconjunto y dibujarian una
    # barra mas alta que la primera.
    _demand_skus = set(canonical["product_id"].unique())
    _matched = _demand_skus & set(sku_supplier)
    _funnel = {
        "n_demand_skus": len(_demand_skus),
        "n_receipt_skus": len(sku_supplier),
        "n_matched_skus": len(_matched),
        "n_with_declared": len(_matched & set(declared_days)),
        "n_with_observed": len(_matched & set(observed_days)),
        "n_with_std": len(_matched & set(observed_std_days)),
        "n_receipts_used": sum(len(rows) for rows in grouped.values() if len(rows) >= MIN_DELIVERIES),
        "n_receipts_below_min": sum(len(rows) for rows in _short.values()),
        "n_suppliers_below_min": len(_short),
    }

    gaps: list[SkuGap] = []
    n_unsized = 0
    if comparables and not cost_synthesized:
        to_p = lambda d: _days_to_periods(d, periods_per_year)  # noqa: E731
        erp = inventory_optimization.run(
            canonical,
            service_level=service_level,
            periods_per_year=periods_per_year,
            lead_times={s: to_p(declared_days[s]) for s in comparables},
            lead_time_stds=None,  # sigma_L = 0: el campo que el ERP no tiene
        )
        real = inventory_optimization.run(
            canonical,
            service_level=service_level,
            periods_per_year=periods_per_year,
            lead_times={s: to_p(observed_days[s]) for s in comparables},
            lead_time_stds={s: to_p(observed_std_days[s]) for s in comparables},
        )
        by_erp = {r.product_id: r for r in erp.recommendations}
        for r in real.recommendations:
            base = by_erp.get(r.product_id)
            if base is None or r.product_id not in comparables:
                continue
            # Esta pantalla RESTA dos dimensionamientos. Un SKU que el motor se
            # nego a dimensionar no tiene ninguno de los dos (`safety_stock` es
            # `None` desde el 2026-09-19), y antes de eso traia 0.0 en los dos,
            # asi que entraba con una brecha de 0,00: "el ERP y la realidad
            # coinciden" sobre un SKU que nadie dimensiono. Se excluye, y el
            # embudo lo cuenta.
            if base.safety_stock is None or r.safety_stock is None:
                n_unsized += 1
                continue
            gaps.append(
                SkuGap(
                    product_id=r.product_id,
                    supplier=sku_supplier[r.product_id],
                    declared_lt_days=declared_days[r.product_id],
                    observed_lt_days=observed_days[r.product_id],
                    observed_lt_std_days=observed_std_days[r.product_id],
                    ss_erp=base.safety_stock,
                    ss_real=r.safety_stock,
                    unit_cost=r.unit_cost,
                )
            )

    # El bucle de arriba es el unico que lo sabe, asi que entra al embudo aca.
    _funnel["n_unsized_skus"] = n_unsized

    issues = _qa_issues(
        gaps,
        scorecards=scorecards,
        sku_supplier=sku_supplier,
        cost_synthesized=cost_synthesized,
    )
    value = GapValue(
        gaps=tuple(gaps),
        dropped_receipts=dropped,
        intake_quality=quality,
        qa_issues=tuple(issues),
        findings=(),
        **_funnel,
    )
    if issues:
        return value
    return GapValue(
        gaps=value.gaps,
        dropped_receipts=value.dropped_receipts,
        intake_quality=value.intake_quality,
        qa_issues=(),
        findings=_findings(value),
        **_funnel,
    )


def render_mini_report(value: GapValue, *, dataset_label: str, ts: str) -> str:
    """El mini-reporte persistido. Misma forma que los otros dos embudos."""
    h = value.headline
    lines = [
        "# La brecha de plazo, en plata",
        "",
        f"- **Dataset:** {dataset_label}",
        f"- **Fecha:** {ts}",
        f"- **SKU valorizados:** {h['n_skus']} "
        f"({h['n_over']} sobre-dimensionados, {h['n_under']} cortos)",
        f"- **Costo de mantener usado:** {HOLDING_RATE * 100:.0f}% anual",
        "",
        "## Las dos monedas",
        "",
        "| | Monto | A quien le importa |",
        "|---|---:|---|",
        f"| Caja liberable (una vez, al balance) | ${value.cash_release:,.0f} | "
        "Gerente general / CFO |",
        f"| Ahorro recurrente sobre esa caja | ${value.recurring_saving:,.0f}/anio | "
        "Gerente de operaciones |",
        f"| Exposicion no cubierta (capital a poner) | ${value.exposure:,.0f} | "
        "Operaciones |",
        f"| Cambio neto del inventario | ${value.net:,.0f} | Ambos |",
        "",
        "## SKU por SKU",
        "",
        "| SKU | Proveedor | Declarado | Observado | Desv. | SS actual | SS real | Plata |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for g in sorted(value.gaps, key=lambda g: g.delta_value):
        lines.append(
            f"| {g.product_id} | {g.supplier} | {g.declared_lt_days:.0f} d | "
            f"{g.observed_lt_days:.0f} d | {g.observed_lt_std_days:.0f} d | "
            f"{g.ss_erp:,.0f} | {g.ss_real:,.0f} | ${g.delta_value:,.0f} |"
        )
    lines += ["", "## Hallazgos", ""]
    lines += [f"{i}. {f}" for i, f in enumerate(value.findings, start=1)]
    lines += [
        "",
        "## Siguiente paso",
        "",
        f"Diagnostico de Arranque: {CTA_PATH}",
        "",
        "*Un signo negativo en la columna Plata es buffer que el plazo real no "
        "justifica; uno positivo es buffer que falta. La columna 'SS actual' es lo "
        "que sale de dimensionar contra el plazo declarado con dispersion cero, que "
        "es lo que hace un ERP. Ningun numero de aqui se escribe en tus sistemas.*",
    ]
    return "\n".join(lines) + "\n"


def render_followup_email(value: GapValue, *, email: str, dataset_label: str) -> str:
    """Borrador para el operador. Nunca se envia automaticamente."""
    h = value.headline
    return "\n".join(
        [
            f"Para: {email}",
            f"Asunto: ${value.cash_release:,.0f} liberables y "
            f"${value.exposure:,.0f} de exposicion que no estaba medida",
            "",
            "Hola,",
            "",
            f"Cruce tu export de recepciones con el de movimientos ({dataset_label}) "
            f"y dimensione {h['n_skus']} SKU dos veces: una con el plazo que dice tu "
            "sistema, otra con el que dicen tus recepciones. La diferencia:",
            "",
            f"- ${value.cash_release:,.0f} en caja liberable en {h['n_over']} SKU con "
            f"el plazo declarado inflado. A {HOLDING_RATE * 100:.0f}% de costo de "
            f"mantener son ${value.recurring_saving:,.0f} al anio, y se puede hacer "
            "sin tocar nada mas.",
            f"- ${value.exposure:,.0f} de exposicion en {h['n_under']} SKU cuyo "
            "proveedor demora mas de lo que la ficha dice. Eso no es un costo nuevo: "
            "es riesgo que ya estabas corriendo.",
            "",
            "La mayor parte de la diferencia no viene del promedio del plazo sino de "
            "su dispersion, que no existe como campo en el ERP. Por eso el sistema "
            "dimensiona contra la promesa del proveedor y no contra su comportamiento.",
            "",
            f"Detalle y alcance: {CTA_PATH}",
            "",
            "Si te sirve, respondeme y revisamos los SKU uno por uno en 20 minutos.",
            "",
            "[FIRMA-OPERADOR]",
            "",
            "--",
            "BORRADOR generado por la valorizacion. Revisar y enviar a mano; "
            "Kern nunca envia correo automaticamente.",
        ]
    ) + "\n"
