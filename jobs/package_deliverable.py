"""Consolidated deck for a commercial package run - the single document the
operator opens first (each tool's own full deliverable sits in its subfolder).

Composes a ``src.deliverable.Deliverable`` so the package deck renders exactly
like every other Linchpin deck (Markdown + XLSX via ``write_all``):

- executive summary: what ran, what was skipped and why
- one key finding per executed tool (its result summary)
- the recommended next action from each tool that ranked options
- a coverage table (via the data-source map): tool -> input -> cadence -> status
- the aggregated residual: what stays on the human side, per the
  never-unprotected contract

Duck-typed against the runner's ``PackageSpec``/``StepOutcome`` (no scm_agent
import) so this module stays a pure deliverable builder like its siblings.
"""

from __future__ import annotations

from src.deliverable import DataSource, Deliverable, Finding, Kpi

_STATUS_LABEL = {
    "ok": "ejecutado (QA ok)",
    "skipped": "omitido",
}


def _summary(spec, outcomes, client: str) -> str:
    executed = [o for o in outcomes if o.status == "ok"]
    skipped = [o for o in outcomes if o.status == "skipped"]
    lines = (
        f"{spec.title} ({spec.price}; cadencia: {spec.cadence}) para {client}. "
        f"Se ejecutaron {len(executed)} de {len(outcomes)} analisis del alcance; "
        "todos los ejecutados pasaron su QA (si uno solo hubiera fallado, este "
        "paquete no se habria emitido)."
    )
    if skipped:
        names = ", ".join(o.title for o in skipped)
        lines += (f" Omitidos por falta de insumo u origen no configurado: {names} "
                  "- ver la tabla de cobertura.")
    return lines


def build(spec, outcomes, *, client: str = "Client", prepared: str = "") -> Deliverable:
    """Compose the package-level deck from the runner's step outcomes."""
    executed = [o for o in outcomes if o.status == "ok"]
    skipped = [o for o in outcomes if o.status == "skipped"]

    findings = tuple(
        Finding(title=o.title, detail=o.summary or "(sin resumen)") for o in executed
    )

    recommendations: list[str] = []
    for o in executed:
        guided = o.guided
        options = list(getattr(guided, "options", ()) or ())
        recommended = next((opt for opt in options if getattr(opt, "recommended", False)), None)
        if recommended is not None:
            recommendations.append(f"{o.title}: {recommended.label}")

    kpis = (
        Kpi(name="Analisis ejecutados", value=str(len(executed)),
            target=str(len(outcomes)),
            rationale="alcance del paquete efectivamente corrido este ciclo"),
        Kpi(name="QA aprobado", value=f"{len(executed)}/{len(executed)}",
            target="100%",
            rationale="el paquete solo se emite si todos los analisis pasan su QA"),
        Kpi(name="Analisis omitidos", value=str(len(skipped)), target="0",
            rationale="pasos opcionales sin insumo este ciclo; enviarlos los habilita"),
    )

    data_sources = tuple(
        DataSource(
            field=o.title,
            source=(o.source if o.status == "ok" else (o.skip_reason or o.source)),
            cadence=f"{o.cadence} - {_STATUS_LABEL.get(o.status, o.status)}",
        )
        for o in outcomes
    )

    residual_lines = [
        "Cada analisis conserva su bloque de cobertura y handoff en su propia "
        "subcarpeta; la decision final y la ejecucion comercial (aprobar compras, "
        "negociar, liquidar) quedan del lado del operador.",
    ]
    for o in executed:
        guided_summary = getattr(o.guided, "summary", "") or ""
        if guided_summary:
            residual_lines.append(f"- {o.title}: {guided_summary}")
    residual = "\n".join(residual_lines)

    citations: list[str] = []
    for o in executed:
        for c in o.citations:
            if c not in citations:
                citations.append(c)

    return Deliverable(
        title=spec.title,
        client=client,
        summary=_summary(spec, outcomes, client),
        findings=findings,
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations[:10]),
        residual=residual,
        prepared=prepared,
    )
