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

from src import i18n
from src.deliverable import DEFAULT_BRANDING, Branding, DataSource, Deliverable, Finding, Kpi


def _title(o, lang: str) -> str:
    """A step's displayed title in ``lang`` - translated when a tool_title
    entry exists, otherwise the engine's own (English) tool.title unchanged."""
    return i18n.tool_title(o.tool_key, lang, fallback=o.title)


def _summary(spec, outcomes, client: str, lang: str) -> str:
    executed = [o for o in outcomes if o.status == "ok"]
    skipped = [o for o in outcomes if o.status == "skipped"]
    L = lambda key, **kw: i18n.label(key, lang, **kw)  # noqa: E731
    lines = (
        f"{spec.title} ({spec.price}; {L('cadence_word')}: {spec.cadence}) "
        f"{L('for_client')} {client}. "
        f"{L('executed_of_scope', executed=len(executed), total=len(outcomes))}; "
        f"{L('all_passed_qa')}."
    )
    if skipped:
        names = ", ".join(_title(o, lang) for o in skipped)
        lines += f" {L('skipped_preamble')}: {names} - {L('see_coverage_table')}."
    return lines


def build(
    spec, outcomes, *, client: str = "Client", prepared: str = "",
    lang: str | None = None, branding: Branding | None = None,
) -> Deliverable:
    """Compose the package-level deck from the runner's step outcomes.

    ``lang`` defaults to ``spec.lang`` when unset (the normal case - the
    runner passes ``spec`` through unchanged); an explicit ``lang`` here
    always wins, for callers building a deck outside ``run_package``.
    ``branding`` defaults to Linchpin's own (``DEFAULT_BRANDING``) when unset -
    ``run_package`` resolves a partner's white-label identity and passes it
    through explicitly (see ``scm_agent/packages.py``).
    """
    lang = lang if lang is not None else getattr(spec, "lang", i18n.DEFAULT_LANG)
    L = lambda key, **kw: i18n.label(key, lang, **kw)  # noqa: E731
    executed = [o for o in outcomes if o.status == "ok"]
    skipped = [o for o in outcomes if o.status == "skipped"]

    findings = tuple(
        Finding(title=_title(o, lang), detail=o.summary or L("no_summary")) for o in executed
    )

    recommendations: list[str] = []
    for o in executed:
        guided = o.guided
        options = list(getattr(guided, "options", ()) or ())
        recommended = next((opt for opt in options if getattr(opt, "recommended", False)), None)
        if recommended is not None:
            recommendations.append(f"{_title(o, lang)}: {recommended.label}")

    kpis = (
        Kpi(name=L("kpi_executed_name"), value=str(len(executed)),
            target=str(len(outcomes)), rationale=L("kpi_executed_rationale")),
        Kpi(name=L("kpi_qa_name"), value=f"{len(executed)}/{len(executed)}",
            target="100%", rationale=L("kpi_qa_rationale")),
        Kpi(name=L("kpi_skipped_name"), value=str(len(skipped)), target="0",
            rationale=L("kpi_skipped_rationale")),
    )

    status_label = {"ok": L("status_ok"), "skipped": L("status_skipped")}
    data_sources = tuple(
        DataSource(
            field=_title(o, lang),
            source=(o.source if o.status == "ok" else (o.skip_reason or o.source)),
            cadence=f"{o.cadence} - {status_label.get(o.status, o.status)}",
        )
        for o in outcomes
    )

    residual_lines = [L("residual_preamble")]
    for o in executed:
        guided_summary = getattr(o.guided, "summary", "") or ""
        if guided_summary:
            residual_lines.append(f"- {_title(o, lang)}: {guided_summary}")
    residual = "\n".join(residual_lines)

    citations: list[str] = []
    for o in executed:
        for c in o.citations:
            if c not in citations:
                citations.append(c)

    return Deliverable(
        title=spec.title,
        client=client,
        summary=_summary(spec, outcomes, client, lang),
        findings=findings,
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations[:10]),
        residual=residual,
        prepared=prepared,
        lang=lang,
        branding=branding if branding is not None else DEFAULT_BRANDING,
    )
