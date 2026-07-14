"""Bilingual labels for the commercial-package deck (capability M-E4).

Scope, deliberately bounded (per the 2.0 protocol: "dos diccionarios planos,
sin dependencias nuevas"):

1. The CONSOLIDATED package deck's own generated prose (``jobs/package_deliverable.py``
   headers, KPI names, coverage/handoff text).
2. The 37 registered tools' TITLES (short labels, e.g. "Data Quality & SKU
   Master (MDM)" -> "Calidad de Datos y Maestro de SKUs (MDM)").
3. ``src.deliverable.Deliverable``'s own structural scaffolding (section
   headers, table column names, Excel sheet names) -- ``Deliverable.lang``
   defaults to ``"en"`` (unchanged for the ~37 individual tool decks below,
   which never pass it), and only ``package_deliverable.py::build()`` passes
   the package's ``lang`` explicitly, so translating the CONSOLIDATED deck's
   scaffolding never touches an individual tool's own (English) deck.

Explicitly OUT of scope, and it stays that way even after the above -- two
distinct sources of tool-native English prose leak into a translated "es"
package deck unconditionally, by design, not by oversight:

- Each tool's own narrative prose (``jobs/<x>_job.py`` ``build_deck()``
  Finding/summary text) stays in its engine-native language (English)
  regardless of ``lang`` -- translating ~37 files of SCM business prose is
  not a "two flat dictionaries" job.
- Each tool's ``GuidedOutcome`` text (``scm_agent/tool_options.py`` -
  ``ExecutionOption.label``/``.summary``, echoed into the package deck's
  Recommendations and Coverage & handoff sections) is the same category of
  problem at the same scale and is equally out of scope here.

When an ``LLMProvider`` is configured, ``scm_agent.llm.narrative_rewrite``
DOES rewrite a step's summary into the target language on the fly (see
there) -- that is the intended path to full per-tool bilingual parity, not
this static table; the guided-outcome text above is NOT currently run
through that rewrite, so it stays English even with a provider configured.
``PackageSpec``'s own commercial fields (``title``/``price``/``cadence``/
``audience``) are also untranslated here: those are brand/pricing copy, not
engineering labels: this matches the same commercial-copy vs. code-labels
boundary that already governs the package's own one-pagers.
"""

from __future__ import annotations

Lang = str  # "es" | "en" -- not a real Literal to avoid a new import surface

DEFAULT_LANG: Lang = "es"
SUPPORTED_LANGS: tuple[Lang, ...] = ("es", "en")

# ---- package-deck labels (jobs/package_deliverable.py) ------------------------

LABELS: dict[str, dict[Lang, str]] = {
    "cadence_word": {"es": "cadencia", "en": "cadence"},
    "for_client": {"es": "para", "en": "for"},
    "executed_of_scope": {
        "es": "Se ejecutaron {executed} de {total} analisis del alcance",
        "en": "{executed} of {total} analyses in scope were executed",
    },
    "all_passed_qa": {
        "es": "todos los ejecutados pasaron su QA (si uno solo hubiera fallado, "
              "este paquete no se habria emitido)",
        "en": "every one executed passed its QA (had a single one failed, this "
              "package would not have been issued)",
    },
    "skipped_preamble": {
        "es": "Omitidos por falta de insumo u origen no configurado",
        "en": "Skipped for missing input or an unconfigured source",
    },
    "see_coverage_table": {
        "es": "ver la tabla de cobertura",
        "en": "see the coverage table",
    },
    "no_summary": {"es": "(sin resumen)", "en": "(no summary)"},
    "kpi_executed_name": {"es": "Analisis ejecutados", "en": "Analyses executed"},
    "kpi_executed_rationale": {
        "es": "alcance del paquete efectivamente corrido este ciclo",
        "en": "package scope actually run this cycle",
    },
    "kpi_qa_name": {"es": "QA aprobado", "en": "QA approved"},
    "kpi_qa_rationale": {
        "es": "el paquete solo se emite si todos los analisis pasan su QA",
        "en": "the package is issued only if every analysis passes its QA",
    },
    "kpi_skipped_name": {"es": "Analisis omitidos", "en": "Analyses skipped"},
    "kpi_skipped_rationale": {
        "es": "pasos opcionales sin insumo este ciclo; enviarlos los habilita",
        "en": "optional steps with no input this cycle; sending the file enables them",
    },
    "status_ok": {"es": "ejecutado (QA ok)", "en": "executed (QA passed)"},
    "status_skipped": {"es": "omitido", "en": "skipped"},
    "residual_preamble": {
        "es": "Cada analisis conserva su bloque de cobertura y handoff en su propia "
              "subcarpeta; la decision final y la ejecucion comercial (aprobar "
              "compras, negociar, liquidar) quedan del lado del operador.",
        "en": "Each analysis keeps its own coverage/handoff block in its own "
              "subfolder; the final decision and commercial execution (approving "
              "purchases, negotiating, liquidating) stay on the operator's side.",
    },

    # ---- src.deliverable.Deliverable structural scaffolding ------------------
    # Deliverable.lang defaults to "en" (unchanged for individual tool decks,
    # which never pass it) - only package_deliverable.py passes the package's
    # own lang, so only the CONSOLIDATED deck's headers/labels below translate.
    "hdr_title_field": {"es": "Titulo", "en": "Title"},
    "hdr_client_field": {"es": "Cliente", "en": "Client"},
    "hdr_prepared_field": {"es": "Preparado", "en": "Prepared"},
    "hdr_confidence_field": {"es": "Confianza", "en": "Confidence"},
    "hdr_executive_summary": {"es": "Resumen ejecutivo", "en": "Executive summary"},
    "hdr_key_findings": {"es": "Hallazgos principales", "en": "Key findings"},
    "hdr_recommendations": {"es": "Recomendaciones", "en": "Recommendations"},
    "hdr_options": {"es": "Opciones para actuar", "en": "Options to act"},
    "options_intro": {
        "es": "Opciones priorizadas y ejecutables - la recomendada esta marcada:",
        "en": "Ranked, executable options - the recommended default is marked:",
    },
    "recommended_flag": {"es": " _(recomendada)_", "en": " _(recommended)_"},
    "action_label": {"es": "Accion", "en": "Action"},
    "tradeoff_label": {"es": "Contrapartida", "en": "Trade-off"},
    "hdr_kpis": {"es": "KPIs", "en": "KPIs"},
    "col_kpi": {"es": "KPI", "en": "KPI"},
    "col_value": {"es": "Valor", "en": "Value"},
    "col_target": {"es": "Objetivo", "en": "Target"},
    "col_why_it_matters": {"es": "Por que importa", "en": "Why it matters"},
    "hdr_data_sources": {"es": "Fuentes de datos", "en": "Data sources"},
    "data_sources_intro": {
        "es": "Cada cifra de arriba es trazable a su origen:",
        "en": "Every figure above is traceable to its origin:",
    },
    "col_metric": {"es": "Metrica", "en": "Metric"},
    "col_source": {"es": "Fuente", "en": "Source"},
    "col_refresh": {"es": "Actualizacion", "en": "Refresh"},
    "hdr_methodology": {"es": "Metodologia y fundamento", "en": "Methodology & grounding"},
    "methodology_intro": {
        "es": "Fundamentado en la base de conocimiento L3 de cadena de suministro:",
        "en": "Grounded in the L3 supply-chain knowledge base:",
    },
    "hdr_coverage_handoff": {"es": "Cobertura y transferencia", "en": "Coverage & handoff"},
    "no_residual": {
        "es": "Sin acciones residuales: el analisis de arriba esta listo para usar.",
        "en": "No residual actions: the analysis above is ready to use.",
    },
    "footer_prepared_by": {"es": "Preparado por", "en": "Prepared by"},
    # Excel-only: a distinct row label from hdr_prepared_field ("Prepared" -
    # the date row) so two rows both starting with "Prepared" don't sit next
    # to each other holding unrelated values (a date vs. a company name).
    "branding_name_field": {"es": "Marca", "en": "Brand"},
    "branding_logo_field": {"es": "Logo", "en": "Logo"},
    # Excel-only: sheet names + the few column headers not already covered above.
    "sheet_summary": {"es": "Resumen", "en": "Summary"},
    "sheet_kpis": {"es": "KPIs", "en": "KPIs"},
    "sheet_findings": {"es": "Hallazgos", "en": "Findings"},
    "sheet_data_sources": {"es": "Fuentes de Datos", "en": "Data Sources"},
    "sheet_options": {"es": "Opciones", "en": "Options"},
    "sheet_citations": {"es": "Citas", "en": "Citations"},
    "col_finding": {"es": "Hallazgo", "en": "Finding"},
    "col_detail": {"es": "Detalle", "en": "Detail"},
    "col_impact": {"es": "Impacto", "en": "Impact"},
    "col_option": {"es": "Opcion", "en": "Option"},
    "col_recommended": {"es": "Recomendada", "en": "Recommended"},
    "col_summary": {"es": "Resumen", "en": "Summary"},
    "yes_flag": {"es": "si", "en": "yes"},

    # ---- price_intelligence's own Fuentes section (Linchpin 3.0 PR-13, ------
    # golden rule 7: acquisition tier per datum) -- distinct from the L3
    # book-citations "Methodology & grounding" section every deck already has.
    "hdr_fuentes": {"es": "Fuentes", "en": "Sources"},
    "fuentes_intro": {
        "es": "Cada precio de competencia de este reporte con su procedencia "
              "(tier de adquisicion, extractor y version, confianza, fecha):",
        "en": "Every competitor price in this report with its provenance "
              "(acquisition tier, extractor and version, confidence, date):",
    },
    "fuentes_col_product": {"es": "Producto", "en": "Product"},
    "fuentes_col_site": {"es": "Sitio", "en": "Site"},
    "fuentes_col_tier": {"es": "Tier", "en": "Tier"},
    "fuentes_col_extractor": {"es": "Extractor", "en": "Extractor"},
    "fuentes_col_confidence": {"es": "Confianza", "en": "Confidence"},
    "fuentes_col_observed_at": {"es": "Observado", "en": "Observed"},
    "fuentes_quarantine_hdr": {"es": "Cuarentena y descartes", "en": "Quarantine & discards"},
    "fuentes_quarantine_intro": {
        "es": "Nunca se incluyen como si fueran confiables -- reportados aparte:",
        "en": "Never included as if trustworthy -- reported separately:",
    },
    "fuentes_skipped_hdr": {"es": "Referencias omitidas", "en": "Skipped references"},
}


def label(key: str, lang: Lang = DEFAULT_LANG, **kwargs: object) -> str:
    """Look up a package-deck label, formatting with ``kwargs`` if given.

    Falls back to Spanish (never raises) on an unrecognized key or language -
    a missing translation must degrade to readable text, not crash a deck.
    """
    entry = LABELS.get(key)
    if entry is None:
        return key
    text = entry.get(lang, entry.get(DEFAULT_LANG, key))
    return text.format(**kwargs) if kwargs else text


# ---- tool titles (scm_agent/tools.py -- every registered Tool.title value) --

TOOL_TITLES: dict[str, dict[Lang, str]] = {
    "inventory_optimization": {"es": "Optimizacion de Inventario", "en": "Inventory Optimization"},
    "pricing": {"es": "Optimizacion de Precio", "en": "Price Optimization"},
    "leadership_chain": {"es": "Liderazgo (CHAIN)", "en": "Leadership (CHAIN)"},
    "cost_to_serve": {"es": "Costo de Servir y Capital de Trabajo", "en": "Cost-to-Serve & Working Capital"},
    "sop": {"es": "Planificacion de Ventas y Operaciones (S&OP / IBP)",
            "en": "Sales & Operations Planning (S&OP / IBP)"},
    "abc_xyz": {"es": "Clasificacion ABC-XYZ", "en": "ABC-XYZ Classification"},
    "sourcing": {"es": "Seleccion y Sourcing de Proveedores", "en": "Supplier Sourcing & Selection"},
    "ddmrp": {"es": "Plan de Buffers DDMRP", "en": "DDMRP Buffer Plan"},
    "landed_cost": {"es": "Estudio de Costo en Destino", "en": "Landed-Cost Study"},
    "warehouse_layout": {"es": "Layout de Bodega (3D)", "en": "Warehouse Layout (3D)"},
    "whatif": {"es": "Estudio What-If / Sensibilidad", "en": "What-If / Sensitivity Study"},
    "financial_kpis": {"es": "KPIs Financieros de Inventario", "en": "Inventory Financial KPIs"},
    "reconciliation": {"es": "Exactitud de Registro de Inventario (IRA)",
                        "en": "Inventory Record Accuracy (IRA)"},
    "returns": {"es": "Devoluciones y Logistica Inversa", "en": "Returns & Reverse Logistics"},
    "queuing": {"es": "Colas / Dotacion de Personal", "en": "Queuing / Staffing"},
    "scheduling": {"es": "Secuenciacion de Trabajos", "en": "Job Sequencing"},
    "risk": {"es": "Evaluacion de Riesgo de Cadena de Suministro", "en": "Supply-Chain Risk Assessment"},
    "forecast": {"es": "Pronostico de Demanda y Pronosticabilidad",
                 "en": "Demand Forecasting & Forecastability"},
    "data_quality": {"es": "Calidad de Datos y Maestro de SKUs (MDM)", "en": "Data Quality & SKU Master (MDM)"},
    "dea": {"es": "Benchmarking de Eficiencia (DEA)", "en": "Efficiency Benchmarking (DEA)"},
    "acceptance_sampling": {"es": "Muestreo de Aceptacion (Calidad en Recepcion)",
                             "en": "Acceptance Sampling (Receiving Quality)"},
    "earned_value": {"es": "Valor Ganado (Control de Proyecto)", "en": "Earned Value (Project Control)"},
    "learning_curve": {"es": "Reduccion de Costo por Curva de Aprendizaje", "en": "Learning-Curve Cost-Down"},
    "odoo_replenishment": {"es": "Reposicion Odoo (ERP en vivo)", "en": "Odoo Replenishment (live ERP)"},
    "excel_replenishment": {"es": "Reposicion en Excel (planilla del cliente)",
                             "en": "Excel Replenishment (client planilla)"},
    "newsvendor": {"es": "Pedido de Periodo Unico (Newsvendor)", "en": "Single-Period (Newsvendor) Order"},
    "cycle_count": {"es": "Programa de Conteo Ciclico", "en": "Cycle-Count Program"},
    "multi_echelon": {"es": "Ubicacion de Stock de Seguridad Multi-Escalon",
                       "en": "Multi-Echelon Safety-Stock Placement"},
    "transportation": {"es": "Seleccion de Modo de Transporte y Flete por Ruta",
                        "en": "Transport-Mode Selection & Lane Freight"},
    "fefo": {"es": "Vencimiento de Lotes y Disposicion FEFO", "en": "Lot Expiry & FEFO Disposition"},
    "slotting": {"es": "Slotting de Bodega (COI + Afinidad)", "en": "Warehouse Slotting (COI + Affinity)"},
    "simulation": {"es": "Politica (R,S) Optimizada por Simulacion",
                   "en": "Simulation-Optimized (R,S) Policy"},
    "digital_twin": {"es": "Gemelo Digital - Fabrica de Escenarios de Red",
                     "en": "Digital Twin - Network Scenario Factory"},
    "excess_obsolete": {"es": "Stock Excedente y Obsoleto (E&O)", "en": "Excess & Obsolete (E&O) Stock"},
    "markdown_liquidation": {"es": "Plan de Liquidacion por Descuento", "en": "Markdown Liquidation Plan"},
    "facility_location": {"es": "Ubicacion de Instalaciones (Diseno de Red)",
                           "en": "Facility Location (Network Design)"},
    "drp": {"es": "Planificacion de Requerimientos de Distribucion (DRP)",
            "en": "Distribution Requirements Planning (DRP)"},
    "vehicle_routing": {"es": "Ruteo y Programacion de Vehiculos", "en": "Vehicle Routing & Scheduling"},
    "price_intelligence": {"es": "Diagnostico de Posicion de Precios", "en": "Price Position Diagnostic"},
    "price_watch": {"es": "Vigilancia de Precios Asistida por Descubrimiento",
                     "en": "Discovery-Assisted Price Watch"},
}


def tool_title(tool_key: str, lang: Lang = DEFAULT_LANG, fallback: str = "") -> str:
    """The tool's display title in ``lang``. Unmapped keys fall back to the
    engine's own (English) title rather than raising - a new tool registered
    without an i18n entry yet must still render, just without translation."""
    entry = TOOL_TITLES.get(tool_key)
    if entry is None:
        return fallback or tool_key
    return entry.get(lang, entry.get(DEFAULT_LANG, fallback or tool_key))
