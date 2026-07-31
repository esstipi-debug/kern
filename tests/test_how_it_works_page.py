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


def test_page_mentions_48_tools_and_33_sources_not_stale_numbers() -> None:
    html = render_how_it_works_html()
    assert "48" in html
    assert "33" in html
    assert "25 curated" not in html  # the README's stale source count must never appear here


def test_page_has_both_donut_lenses_totaling_48() -> None:
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
    # 10 domain entries + 7 SCOR entries = 17 legend rows total
    assert html.count('class="legend-item"') == len(DOMAIN_AREA_ORDER) + len(SCOR_BUCKET_ORDER)
    assert html.count('class="legend-item"') == 17


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
