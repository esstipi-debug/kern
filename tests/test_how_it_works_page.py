"""Tests for webapp/how_it_works_page.py -- the /how-it-works page renderer.
Rendering-helper tests call the (module-private, deliberately imported
directly here) functions without an HTTP client; Task 6 adds HTTP-level
tests through the real FastAPI app, mirroring tests/test_stocky_alternative_page.py."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from html import escape  # noqa: E402

from webapp.how_it_works_page import _donut_svg  # noqa: E402


def test_donut_svg_has_one_circle_per_segment_and_correct_total() -> None:
    svg = _donut_svg([("A", 2), ("B", 1), ("C", 1)], element_id="test-donut")
    assert svg.count("<circle") == 3
    assert 'id="test-donut"' in svg
    assert ">4<" in svg  # the total, rendered as center text


def test_donut_svg_segment_percentages_are_correct() -> None:
    svg = _donut_svg([("A", 2), ("B", 1), ("C", 1)], element_id="test-donut")
    assert 'data-pct="50"' in svg
    assert svg.count('data-pct="25"') == 2


def test_donut_svg_escapes_labels() -> None:
    svg = _donut_svg([("<script>", 1), ("B", 1)], element_id="xss-donut")
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_donut_svg_rejects_empty_total() -> None:
    import pytest

    with pytest.raises(ValueError):
        _donut_svg([("A", 0), ("B", 0)], element_id="empty-donut")


def test_donut_svg_rejects_negative_count() -> None:
    import pytest

    with pytest.raises(ValueError):
        _donut_svg([("A", -5), ("B", 10)], element_id="negative-donut")


from webapp.how_it_works_data import CertCoverage, IsoClause  # noqa: E402
from webapp.how_it_works_page import (  # noqa: E402
    _coverage_bar,
    _expandable_card,
    _iso_accordion_row,
    _stepper,
)


def test_expandable_card_has_toggle_button_and_hidden_detail() -> None:
    html = _expandable_card("Title", "Summary", "<p>Detail</p>", card_id="card-1")
    assert 'data-target="card-1"' in html
    assert 'id="card-1"' in html
    assert "hidden" in html
    assert "Title" in html and "Summary" in html and "Detail" in html


def test_coverage_bar_renders_level_and_covered_gaps() -> None:
    cert = CertCoverage(
        "CPIM", "ASCM", "High", covered=("Forecasting",), gaps=("MRP-II",)
    )
    html = _coverage_bar(cert, bar_id="cert-cpim")
    assert "CPIM" in html and "ASCM" in html and "High" in html
    assert "Forecasting" in html
    assert "MRP-II" in html
    assert html.count('class="bar-seg filled"') == 4  # "High" = 4/4 segments filled


def test_coverage_bar_partial_level_fills_two_segments() -> None:
    cert = CertCoverage("CPSM", "ISM", "Partial", covered=("X",), gaps=("Y",))
    html = _coverage_bar(cert, bar_id="cert-cpsm")
    assert html.count('class="bar-seg filled"') == 2


def test_iso_accordion_row_renders_clause_and_behavior() -> None:
    clause = IsoClause("8.7 Control of nonconforming outputs", "QA fails => zero deliverables.")
    html = _iso_accordion_row(clause, row_id="iso-1")
    assert "8.7 Control of nonconforming outputs" in html
    assert "QA fails =&gt; zero deliverables." in html or "QA fails => zero deliverables." in html


def test_stepper_renders_all_stages() -> None:
    html = _stepper([("Brief", "A plain-language request."), ("QA", "Gate that vetoes bad results.")])
    assert "Brief" in html and "A plain-language request." in html
    assert "QA" in html and "Gate that vetoes bad results." in html


from webapp.how_it_works_page import render_how_it_works_html  # noqa: E402


def test_page_reports_the_real_tool_count_and_26_sources() -> None:
    """La pagina no puede inflar el conteo de fuentes ni reportar tools viejas.

    Este test combina las dos correcciones, una de cada lado del rescate de
    `feat/pattern-scan`, porque cada lado tenia razon en una mitad.

    **El conteo de tools se deriva, no se escribe.** Fijar el literal era
    fragil por construccion: los tokens de color hex del propio <style> de la
    pagina pueden contener la cifra (documentado en fe372d9 con "41" y
    ``#283341``), asi que la asercion podia pasar pasara lo que pasara. Y de
    hecho paso algo peor: ``assert "49" in html`` seguia verde mientras el
    registry tenia **50** tools, porque la meta description conservaba un 49
    escrito a mano que nunca se sustituia. El cuerpo de la pagina decia 50 y
    su propia meta decia 49. Anclar a las frases que reportan el numero, con
    ``len(TOOLS)`` interpolado, es lo que hace que esto se rompa cuando el
    registry se mueve y la copia no.

    **La cifra de fuentes es 26, no 33.** Un recuento de
    ``knowledge/scm-books/graph.json`` (2026-07-24) mostro que el grafo tiene
    35 ``source_file`` distintos, pero 9 son capitulos de un mismo volumen
    editado (Cohen & Dai) y 1 era el propio ``KERN_ATLAS_SOMBREROS_SCM.md`` de
    Kern: 33 contaba capitulos como obras enteras y contaba un documento
    interno como literatura del campo.
    """
    from webapp.how_it_works_data import TOOLS

    html = render_how_it_works_html()
    tool_count = str(len(TOOLS))
    assert f"Kern's {tool_count} supply-chain capabilities" in html
    assert f"{tool_count} agent-routable capabilities" in html
    assert "27 curated external sources" in html
    # Todo conteo de fuentes superado que esta pagina llevo o estuvo tentada de llevar.
    for stale in ("33 curated", "25 curated", "35 curated", "26 curated", "41 curated"):
        assert stale not in html, stale


def test_source_count_claim_still_matches_the_books_graph() -> None:
    """The source count is a claim made to prospects, so it gets a tripwire too.

    The page says "26 curated external sources". That number is not directly
    countable from graph.json -- it is distinct external WORKS, whereas the
    graph stores files: 9 of them are single chapters of edited volumes, and
    KERN_ATLAS_SOMBREROS_SCM.md is Kern's own internal doc, not a curated
    external source (counting it was the overclaim this test exists to stop
    coming back).

    So this pins the countable thing -- distinct non-Kern source files -- and
    documents the mapping. Add a book and this fails, forcing whoever adds it
    to re-derive the works count instead of letting the page quietly drift,
    which is exactly how it got stuck at a stale 33.

    The gap widened on 2026-09-04 and the page did NOT move. L3 source #30
    (document control and standard work) added 7 source files while the works
    count stayed at 26, because not one of the 7 is a curated work: 6 are web
    artifacts (a product page, a catalogue page, two vendor articles, a training
    provider's learning-center page, a podcast interview) and the 7th is a body-of-
    knowledge map, not a book. Reading AIAG's product page for CQI-8 is not having
    read CQI-8, and the filenames say so on purpose -- see
    documentation/SOP_DOCUMENT_CONTROL_SOURCES.md, which records the evidence tier
    of each and the three concepts CUT because their provenance did not hold up.

    So of the 42 external files, 35 are book/chapter PDFs and 7 are secondary web
    artifacts. El #42 es Vandeput, *Data Science for Supply Chain Forecasting*
    (2021, 2ed), agregado el 2026-09-15: SI es una obra curada -- se leyo el libro
    entero, no una ficha de producto -- asi que a diferencia de los 7 artefactos
    web del 2026-09-04, este SI movio el conteo de obras de 26 a 27. If you are here because this assert failed: the number below tracks
    FILES. Moving "26 curated external sources" is a separate, deliberate decision
    that requires the new source to actually be a curated work.
    """
    import json
    from pathlib import Path

    graph = json.loads(
        (_REPO_ROOT / "knowledge" / "scm-books" / "graph.json").read_text(encoding="utf-8")
    )
    files = {
        Path(str(n["source_file"])).name
        for n in graph["nodes"]
        if n.get("source_file")
    }
    external = {f for f in files if not f.startswith("KERN_")}

    assert len(external) == 42, sorted(external)
    assert "KERN_ATLAS_SOMBREROS_SCM.md" in files  # the internal doc is in the graph...
    assert "KERN_ATLAS_SOMBREROS_SCM.md" not in external  # ...and excluded from the claim


def test_page_has_both_donut_lenses_totaling_49() -> None:
    html = render_how_it_works_html()
    assert 'id="donut-domain"' in html
    assert 'id="donut-scor"' in html
    assert 'id="donut-guided"' in html  # the never-unprotected donut, 4 outcomes


def test_page_lists_all_five_certifications() -> None:
    html = render_how_it_works_html()
    for name in ("CPIM", "CLTD", "CSCP", "SCPro", "CPSM"):
        assert name in html


def test_page_has_no_certification_overclaim_language() -> None:
    html = render_how_it_works_html().lower()
    assert "kern is certified" not in html
    assert "kern is ascm-certified" not in html
    assert "kern is iso" not in html


def test_page_has_no_sales_content() -> None:
    html = render_how_it_works_html().lower()
    assert "buy.stripe.com" not in html
    assert "btn-primary" not in html  # the site's CTA-button class, intentionally absent here


def test_page_has_trademark_disclaimer_and_source_doc_link() -> None:
    html = render_how_it_works_html()
    assert "ASCM" in html
    assert "not affiliated with" in html or "not certified by" in html
    assert "KERN_NIVEL_REFERENCIA_SCM" in html


def test_page_has_quiet_nav_links() -> None:
    html = render_how_it_works_html()
    assert 'href="/"' in html


def test_page_loads_the_interactivity_script() -> None:
    html = render_how_it_works_html()
    assert '<script src="/static/how_it_works.js?v=2"></script>' in html


def test_both_donut_lenses_have_a_named_clickable_legend() -> None:
    """Every donut segment's group must be nameable without hovering: each lens
    carries a legend with one entry per bucket (swatch + name + count), wired
    to the same reveal behavior as clicking the segment (data-donut/data-bucket)."""
    from html import escape

    from webapp.how_it_works_data import DOMAIN_AREA_ORDER, SCOR_BUCKET_ORDER

    html = render_how_it_works_html()
    for area in DOMAIN_AREA_ORDER:
        assert f'data-donut="donut-domain" data-bucket="{escape(area)}"' in html
    for bucket in SCOR_BUCKET_ORDER:
        assert f'data-donut="donut-scor" data-bucket="{escape(bucket)}"' in html
    # 12 domain entries + 7 SCOR entries = 19 legend rows total
    assert html.count('class="legend-item"') == len(DOMAIN_AREA_ORDER) + len(SCOR_BUCKET_ORDER)
    assert html.count('class="legend-item"') == 19


def test_every_registered_tool_is_rendered_on_the_page() -> None:
    """The rendered-HTML half of the drift guard.

    tests/test_how_it_works_data.py asserts the TOOLS table matches the
    registry; this asserts the table actually reaches the page, so a
    rendering bug (a bucket the donut does not draw, a truncated loop)
    cannot hide a tool that the data layer believes is listed."""
    from webapp.how_it_works_data import TOOLS

    html = render_how_it_works_html()
    for tool in TOOLS:
        assert escape(tool.label) in html, tool.key


from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402

client = TestClient(app)


def test_how_it_works_route_returns_200_html() -> None:
    resp = client.get("/how-it-works")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_how_it_works_route_serves_the_interactivity_script() -> None:
    resp = client.get("/static/how_it_works.js")
    assert resp.status_code == 200
    assert "bindDonutLensTabs" in resp.text


def test_how_it_works_route_body_matches_direct_render() -> None:
    """HTMLResponse must not transform the string in any way -- catches a
    route-wiring bug (wrong render call, double-escaping, truncation) that
    the Task 4 direct-render tests can't see since they never go through
    the HTTP layer."""
    resp = client.get("/how-it-works")
    assert resp.text == render_how_it_works_html()


# -- the same claim, made outside the page ------------------------------------

_SOURCE_COUNT_DOCS = ("pyproject.toml", "README.md", "CLAUDE.md")

# Every source count these three files have carried, or could be tempted back to.
# "25" is the one that actually rotted here: it was right in July 2026 and stayed
# in all three long after the page had moved to 26.
_SUPERSEDED_SOURCE_CLAIMS = (
    "25 SCM sources",
    "25 curated",
    "25 sources",
    # "26 curated" quedo superado el 2026-09-15 al entrar Vandeput, *Data Science for
    # Supply Chain Forecasting* (2021, 2ed) como obra #27. "27 curated" salio de esta
    # lista el mismo dia: era el numero al que NO habia que saltar mientras las 7
    # fuentes del 2026-09-04 eran artefactos web, y ahora es el numero correcto.
    "26 curated",
    "33 curated",
    "35 curated",
    "41 curated",
)


def test_the_source_count_claim_is_the_same_everywhere_it_is_made() -> None:
    """The page has a tripwire; the three docs that repeat its claim had none.

    That asymmetry is exactly how pyproject.toml, README.md and CLAUDE.md sat on
    a stale 25 while ``test_source_count_claim_still_matches_the_books_graph``
    kept the client-facing page honest at 26 -- a count written into prose cannot
    fail a build, so it drifts silently and nothing complains.

    knowledge/scm-books/README.md is deliberately NOT in this list: its numbers
    are a running log anchored to when each source was added ("added as the 25th
    source", "the other 24 sources are..."), which stay true as the total moves.
    """
    for name in _SOURCE_COUNT_DOCS:
        text = (_REPO_ROOT / name).read_text(encoding="utf-8")
        assert "27 curated external" in text, name
        for stale in _SUPERSEDED_SOURCE_CLAIMS:
            assert stale not in text, f"{name} carries a superseded claim: {stale}"
