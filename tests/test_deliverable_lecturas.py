"""Las lecturas salen en el entregable RIO ABAJO del gate de citas, y no tocan nada mas.

Tres cosas se fijan aca, y las tres son de producto antes que de codigo:

1. **Nada de lo que ya se emite cambia.** La seccion nueva es opcional y condicional, asi
   que un entregable sin lecturas tiene que renderizar BYTE A BYTE lo mismo que antes. No
   se promete: los dos golden de abajo (en/es) se capturaron con ``to_markdown()`` sobre
   el commit anterior al cambio y se comparan con ``==``, no con ``in``.

2. **Un pasaje no es una cita.** ``readings`` y ``citations`` son dos campos, dos
   secciones y dos listas: un pasaje del indice de lectura no tiene ``node_id`` y
   ``filter_citations`` lo rechazaria con razon. Poblar uno no puede mover el otro.

3. **La pagina es la FISICA del PDF.** ``page_label`` viaja TEXTUAL ("PDF p.83-84") y la
   intro lo dice en la misma linea en que dice que no son citas. El folio impreso del
   libro arranca despues (Vandeput: 26 paginas de corrimiento; Liu: 12), asi que un
   cliente que lee "p.83" y abre el libro impreso en la 83 no encuentra nada -- y una
   cita que el propio cliente falsea es peor que no citar.
"""
from __future__ import annotations

from openpyxl import load_workbook

from nosilo_core.guided import ExecutionOption
from src import i18n
from src.connectors import lectura
from src.connectors.lectura import MAX_EXCERPT_CHARS, Reading
from src.deliverable import Branding, DataSource, Deliverable, Finding, Kpi

# ---- el entregable de referencia, con TODAS las secciones pobladas -------------------


def _poblado(lang: str = "en", **extra) -> Deliverable:
    campos = dict(
        title="Inventory Optimization",
        client="Acme DTC",
        summary="Analyzed 320 SKUs; recommended $48,000 inventory investment at 95% service level.",
        findings=(
            Finding("12 SKUs at stockout risk", "Reorder now to hold 95% service", impact="$9,200 protected"),
            Finding("18 SKUs overstocked", "Excess vs target", impact="$14,500 cash to release"),
        ),
        kpis=(
            Kpi("Inventory turns", "6.2", "8-12", "Cash efficiency of stock"),
            Kpi("Fill rate", "94%", "95%+", "Service the customer sees"),
        ),
        data_sources=(
            DataSource("Demand history", "shopify_orders.csv", "weekly"),
            DataSource("On-hand stock", "Cin7 export", "daily"),
        ),
        recommendations=("Issue POs for the 12 at-risk SKUs", "Mark down the 18 overstocked SKUs"),
        options=(
            ExecutionOption(label="Do X", summary="best move", recommended=True,
                            action="apply X", tradeoffs="fast"),
            ExecutionOption(label="Do Y", summary="the alternative", action="apply Y", tradeoffs="slow"),
        ),
        citations=("Reorder Point - vandeput.pdf Ch.4 -> src/eoq.py:L12",),
        confidence=0.9,
        residual="Approve the drafted POs and confirm supplier lead times before issuing.",
        prepared="2026-06-23",
        lang=lang,
        branding=Branding(name="Acme Consulting", logo_url="https://acme.example/logo.png"),
    )
    campos.update(extra)  # `extra` PISA, no duplica: varios tests apagan `citations`
    return Deliverable(**campos)


_LECTURAS = (
    Reading(source="vandeput-inventory-optimization",
            page_label="PDF p.83-84",
            excerpt="Safety stock sizing depends on the risk period, not on the lead time alone."),
    Reading(source="liu-supply-chain-analytics",
            page_label="PDF p.12",
            excerpt="Analytics without a decision attached is reporting."),
)


# ---- 1. byte a byte: lo que ya se emitia no se movio ---------------------------------

#: Capturado con ``to_markdown()`` ANTES de agregar el campo ``readings`` (BASE
#: d210995). Se compara con ``==`` a proposito: un ``in`` no ve un espacio de mas.
GOLDEN_EN = """![Acme Consulting](https://acme.example/logo.png)

# Inventory Optimization - Acme DTC
*Prepared 2026-06-23*

## Executive summary

Analyzed 320 SKUs; recommended $48,000 inventory investment at 95% service level.

## Key findings

- **12 SKUs at stockout risk** - Reorder now to hold 95% service _(impact: $9,200 protected)_
- **18 SKUs overstocked** - Excess vs target _(impact: $14,500 cash to release)_

## Recommendations

1. Issue POs for the 12 at-risk SKUs
2. Mark down the 18 overstocked SKUs

## Options to act

Ranked, executable options - the recommended default is marked:

1. **Do X** _(recommended)_ - best move
   - Action: apply X
   - Trade-off: fast
2. **Do Y** - the alternative
   - Action: apply Y
   - Trade-off: slow

## KPIs

| KPI | Value | Target | Why it matters |
|---|---|---|---|
| Inventory turns | 6.2 | 8-12 | Cash efficiency of stock |
| Fill rate | 94% | 95%+ | Service the customer sees |

## Data sources

Every figure above is traceable to its origin:

| Metric | Source | Refresh |
|---|---|---|
| Demand history | shopify_orders.csv | weekly |
| On-hand stock | Cin7 export | daily |

## Methodology & grounding

Grounded in the L3 supply-chain knowledge base:

- Reorder Point - vandeput.pdf Ch.4 -> src/eoq.py:L12

## Coverage & handoff

Confidence: **90%**.
Approve the drafted POs and confirm supplier lead times before issuing.

---
_Prepared by Acme Consulting_
"""

GOLDEN_ES = """![Acme Consulting](https://acme.example/logo.png)

# Inventory Optimization - Acme DTC
*Preparado 2026-06-23*

## Resumen ejecutivo

Analyzed 320 SKUs; recommended $48,000 inventory investment at 95% service level.

## Hallazgos principales

- **12 SKUs at stockout risk** - Reorder now to hold 95% service _(impact: $9,200 protected)_
- **18 SKUs overstocked** - Excess vs target _(impact: $14,500 cash to release)_

## Recomendaciones

1. Issue POs for the 12 at-risk SKUs
2. Mark down the 18 overstocked SKUs

## Opciones para actuar

Opciones priorizadas y ejecutables - la recomendada esta marcada:

1. **Do X** _(recomendada)_ - best move
   - Accion: apply X
   - Contrapartida: fast
2. **Do Y** - the alternative
   - Accion: apply Y
   - Contrapartida: slow

## KPIs

| KPI | Valor | Objetivo | Por que importa |
|---|---|---|---|
| Inventory turns | 6.2 | 8-12 | Cash efficiency of stock |
| Fill rate | 94% | 95%+ | Service the customer sees |

## Fuentes de datos

Cada cifra de arriba es trazable a su origen:

| Metrica | Fuente | Actualizacion |
|---|---|---|
| Demand history | shopify_orders.csv | weekly |
| On-hand stock | Cin7 export | daily |

## Metodologia y fundamento

Fundamentado en la base de conocimiento L3 de cadena de suministro:

- Reorder Point - vandeput.pdf Ch.4 -> src/eoq.py:L12

## Cobertura y transferencia

Confianza: **90%**.
Approve the drafted POs and confirm supplier lead times before issuing.

---
_Preparado por Acme Consulting_
"""


def test_un_entregable_sin_lecturas_renderiza_byte_a_byte_lo_mismo_que_antes_en():
    assert _poblado("en").to_markdown() == GOLDEN_EN


def test_un_entregable_sin_lecturas_renderiza_byte_a_byte_lo_mismo_que_antes_es():
    assert _poblado("es").to_markdown() == GOLDEN_ES


def test_readings_por_defecto_esta_vacio():
    assert Deliverable(title="T", client="C", summary="s").readings == ()


def test_sin_lecturas_no_hay_seccion_de_lecturas():
    md = _poblado("en").to_markdown()
    assert i18n.label("hdr_readings", "en") not in md
    assert i18n.label("hdr_readings", "es") not in md


# ---- 2. la seccion propia, y donde va -----------------------------------------------


def test_con_lecturas_aparece_la_seccion_entre_metodologia_y_cobertura():
    md = _poblado("en", readings=_LECTURAS).to_markdown()
    metodologia = md.index(f"## {i18n.label('hdr_methodology', 'en')}")
    lecturas = md.index(f"## {i18n.label('hdr_readings', 'en')}")
    cobertura = md.index(f"## {i18n.label('hdr_coverage_handoff', 'en')}")
    assert metodologia < lecturas < cobertura


def test_la_seccion_sigue_yendo_antes_de_cobertura_aunque_no_haya_citas():
    # Sin citas no hay seccion de Metodologia; la de lecturas no puede caerse con ella
    # ni saltar detras del bloque de cobertura, que es el cierre del documento.
    md = _poblado("en", citations=(), readings=_LECTURAS).to_markdown()
    assert f"## {i18n.label('hdr_methodology', 'en')}" not in md
    assert md.index(f"## {i18n.label('hdr_readings', 'en')}") < md.index(
        f"## {i18n.label('hdr_coverage_handoff', 'en')}"
    )


def test_cada_linea_lleva_libro_pagina_textual_y_extracto_entre_comillas():
    md = _poblado("en", readings=_LECTURAS).to_markdown()
    assert ('- **vandeput-inventory-optimization** PDF p.83-84 - "Safety stock sizing '
            'depends on the risk period, not on the lead time alone."') in md
    assert ('- **liu-supply-chain-analytics** PDF p.12 - '
            '"Analytics without a decision attached is reporting."') in md


def test_la_pagina_viaja_textual_y_nunca_se_convierte_en_folio_pelado():
    md = _poblado("en", readings=(Reading("vandeput", "PDF p.83-84", "x"),)).to_markdown()
    assert "PDF p.83-84" in md
    assert "p.83-84 -" in md  # el rotulo entero, no "p.83" suelto
    assert " p.83 " not in md


def test_una_lectura_sin_pagina_igual_dice_que_no_la_tiene():
    md = _poblado("en", readings=(Reading("vandeput", "sin pagina", "x"),)).to_markdown()
    assert '- **vandeput** sin pagina - "x"' in md


def test_una_lectura_sin_extracto_no_inventa_un_par_de_comillas_vacias():
    # El conector permite una entrada sin cuerpo (libro + pagina ya es procedencia
    # completa). Renderizar `- **x** PDF p.5 - ""` afirma una cita textual que nadie leyo.
    md = _poblado("en", readings=(Reading("vandeput", "PDF p.5", ""),)).to_markdown()
    assert "- **vandeput** PDF p.5" in md
    assert '""' not in md


def test_el_markdown_con_lecturas_sigue_siendo_ascii_y_cp1252():
    md = _poblado("es", readings=_LECTURAS).to_markdown()
    assert md.isascii()
    md.encode("cp1252")


# ---- 3. la intro: no son citas, y la pagina es la FISICA -----------------------------


def test_la_intro_dice_que_no_son_las_fuentes_citadas_del_metodo():
    for lang, palabra in (("en", "cited"), ("es", "citadas")):
        intro = i18n.label("readings_intro", lang)
        assert palabra in intro.lower()
        assert intro in _poblado(lang, readings=_LECTURAS).to_markdown()


def test_la_intro_dice_que_la_pagina_es_la_fisica_del_pdf_no_el_folio_impreso():
    # Esta mitad es la que evita que el cliente falsee la cita solo: abre el libro
    # impreso en la 83, no encuentra nada, y la cita queda desacreditada.
    for lang, fisica, impreso in (("en", "physical", "printed"), ("es", "fisica", "impreso")):
        intro = i18n.label("readings_intro", lang).lower()
        assert fisica in intro and impreso in intro
        assert "pdf" in intro


def test_la_intro_es_una_sola_linea():
    for lang in ("en", "es"):
        assert "\n" not in i18n.label("readings_intro", lang)


def test_el_header_y_la_intro_salen_de_i18n_en_los_dos_idiomas():
    for lang in ("en", "es"):
        md = _poblado(lang, readings=_LECTURAS).to_markdown()
        assert f"## {i18n.label('hdr_readings', lang)}" in md
        assert i18n.label("readings_intro", lang) in md
    assert i18n.label("hdr_readings", "en") != i18n.label("hdr_readings", "es")


# ---- 4. readings y citations no se mezclan JAMAS -------------------------------------


def test_poblar_readings_deja_citations_intacto():
    base = _poblado("en")
    con_lecturas = _poblado("en", readings=_LECTURAS)
    assert con_lecturas.citations == base.citations
    seccion = con_lecturas.to_markdown().split(f"## {i18n.label('hdr_methodology', 'en')}")[1]
    seccion = seccion.split(f"## {i18n.label('hdr_readings', 'en')}")[0]
    assert "vandeput-inventory-optimization" not in seccion  # ningun pasaje entro a Metodologia


def test_poblar_citations_deja_readings_vacio_y_sin_seccion():
    d = _poblado("en", citations=("Otra cita - x.pdf Ch.1",))
    assert d.readings == ()
    assert f"## {i18n.label('hdr_readings', 'en')}" not in d.to_markdown()


def test_una_lectura_nunca_aparece_como_vinneta_de_la_seccion_de_citas():
    md = _poblado("en", citations=(), readings=_LECTURAS).to_markdown()
    assert f"## {i18n.label('hdr_methodology', 'en')}" not in md
    assert i18n.label("methodology_intro", "en") not in md


# ---- 5. el xlsx: misma pagina textual, mismo aviso -----------------------------------


def test_el_xlsx_no_gana_ninguna_hoja_cuando_no_hay_lecturas(tmp_path):
    wb = load_workbook(_poblado("en").to_excel(tmp_path / "d.xlsx"))
    assert i18n.label("sheet_readings", "en") not in wb.sheetnames


def test_el_xlsx_gana_la_hoja_de_lecturas_con_el_aviso_y_la_pagina_textual(tmp_path):
    wb = load_workbook(_poblado("en", readings=_LECTURAS).to_excel(tmp_path / "d.xlsx"))
    hoja = wb[i18n.label("sheet_readings", "en")]
    filas = [tuple(r) for r in hoja.iter_rows(values_only=True)]
    # El aviso viaja PEGADO a la pagina: una columna "Page" suelta en una planilla, sin
    # decir que es la fisica del PDF, es exactamente el modo de falla que esto evita.
    assert filas[0][0] == i18n.label("readings_intro", "en")
    assert filas[1] == (i18n.label("readings_col_book", "en"),
                        i18n.label("readings_col_page", "en"),
                        i18n.label("readings_col_excerpt", "en"))
    assert filas[2] == ("vandeput-inventory-optimization", "PDF p.83-84", _LECTURAS[0].excerpt)


def test_la_hoja_de_lecturas_no_toca_la_de_citas(tmp_path):
    wb = load_workbook(_poblado("en", readings=_LECTURAS).to_excel(tmp_path / "d.xlsx"))
    citas = [tuple(r) for r in wb[i18n.label("sheet_citations", "en")].iter_rows(values_only=True)]
    assert citas == [(i18n.label("col_source", "en"),),
                     ("Reorder Point - vandeput.pdf Ch.4 -> src/eoq.py:L12",)]


# ---- 6. el tope del extracto es UNO solo, y es el del conector -----------------------


def test_el_tope_del_conector_es_el_que_llega_a_la_pagina_y_al_xlsx(tmp_path):
    # El tope de copyright vive en el conector (`MAX_EXCERPT_CHARS`), no se vuelve a
    # aplicar aca: un segundo tope seria una segunda verdad. Lo que se fija es que el
    # unico que existe efectivamente llega a los dos formatos.
    largo = "palabra " * 200
    (lectura_larga,) = lectura.parse_readings(f"1. **vandeput** PDF p.9\n   {largo}\n")
    assert len(lectura_larga.excerpt) <= MAX_EXCERPT_CHARS

    d = _poblado("en", readings=(lectura_larga,))
    assert lectura_larga.excerpt in d.to_markdown()
    hoja = load_workbook(d.to_excel(tmp_path / "d.xlsx"))[i18n.label("sheet_readings", "en")]
    assert [tuple(r) for r in hoja.iter_rows(values_only=True)][2][2] == lectura_larga.excerpt
