"""Tests for src/seo/llms_txt.py (Linchpin 3.0 PR-23, S2 llms.txt generation).

Structure/content verified by hand against the llmstxt.org convention (H1 +
blockquote + optional context + `## Section` + markdown link list items) --
no network I/O anywhere in this file.
"""

from __future__ import annotations

from src.seo import llms_txt as lt
from src.seo import schema_gen as sg


def _catalog() -> list[sg.CatalogItem]:
    return [
        sg.CatalogItem(
            product_id="SKU-001", title="Wireless Mouse", price=29.99, currency="USD",
            url="https://shop.example.com/mouse", description="Ergonomic wireless mouse",
        ),
        sg.CatalogItem(
            product_id="SKU-002", title="Mechanical Keyboard", price=79.5, currency="USD",
            url="https://shop.example.com/keyboard",
        ),  # no description -> falls back to price+currency line
        sg.CatalogItem(
            product_id="SKU-003", title="No URL Item", price=5.0, currency="USD",
        ),  # missing url -> excluded
        sg.CatalogItem(product_id="SKU-004", title="", url="https://shop.example.com/blank"),  # missing title
    ]


_SITE = lt.SiteInfo(name="Example Shop", summary="We sell fine gear for everyday use.")


# -- missing_required_llms_fields (title + url only; price NOT required) ----


def test_missing_required_llms_fields_does_not_require_price() -> None:
    item = sg.CatalogItem(product_id="SKU-100", title="No Price Page", url="https://shop.example.com/x")
    assert lt.missing_required_llms_fields(item) == []


def test_missing_required_llms_fields_requires_title_and_url() -> None:
    no_url = sg.CatalogItem(product_id="SKU-101", title="Has Title")
    no_title = sg.CatalogItem(product_id="SKU-102", url="https://shop.example.com/y")
    assert lt.missing_required_llms_fields(no_url) == ["url"]
    assert lt.missing_required_llms_fields(no_title) == ["title"]


# -- catalog_to_llms_pages -----------------------------------------------------


def test_catalog_to_llms_pages_hand_verified_counts_and_reasons() -> None:
    pages, excluded = lt.catalog_to_llms_pages(_catalog())
    assert len(pages) == 2
    assert {p.product_id for p in pages} == {"SKU-001", "SKU-002"}
    excluded_by_id = {e.product_id: e.reasons for e in excluded}
    assert excluded_by_id["SKU-003"] == ("url",)
    assert excluded_by_id["SKU-004"] == ("title",)


def test_page_description_uses_description_when_present() -> None:
    pages, _ = lt.catalog_to_llms_pages(_catalog())
    mouse = next(p for p in pages if p.product_id == "SKU-001")
    assert mouse.description == "Ergonomic wireless mouse"


def test_page_description_falls_back_to_price_and_currency() -> None:
    pages, _ = lt.catalog_to_llms_pages(_catalog())
    keyboard = next(p for p in pages if p.product_id == "SKU-002")
    assert keyboard.description == "Mechanical Keyboard -- 79.50 USD"


def test_page_description_falls_back_to_title_alone() -> None:
    item = sg.CatalogItem(product_id="SKU-200", title="Mystery Item", url="https://shop.example.com/mystery")
    pages, _ = lt.catalog_to_llms_pages([item])
    assert pages[0].description == "Mystery Item"


# -- generate_llms_txt / build_llms_txt ---------------------------------------


def test_generate_llms_txt_hand_verified_structure() -> None:
    pages, _ = lt.catalog_to_llms_pages(_catalog())
    text = lt.generate_llms_txt(_SITE, pages)

    expected = (
        "# Example Shop\n"
        "\n"
        "> We sell fine gear for everyday use.\n"
        "\n"
        "## Products\n"
        "- [Wireless Mouse](https://shop.example.com/mouse): Ergonomic wireless mouse\n"
        "- [Mechanical Keyboard](https://shop.example.com/keyboard): Mechanical Keyboard -- 79.50 USD\n"
        "\n"
        "---\n"
        f"{lt.FOOTER_LINE}\n"
    )
    assert text == expected


def test_generate_llms_txt_includes_optional_context() -> None:
    site = lt.SiteInfo(name="Example Shop", summary="Short summary.", context="Longer context paragraph.")
    text = lt.generate_llms_txt(site, [])
    assert "Longer context paragraph." in text
    lines = text.splitlines()
    assert lines[2] == "> Short summary."
    assert lines[4] == "Longer context paragraph."


def test_generate_llms_txt_groups_pages_by_section_in_first_seen_order() -> None:
    pages = [
        lt.LlmsPage(product_id="A", title="A", url="https://x/a", description="a", section="Guides"),
        lt.LlmsPage(product_id="B", title="B", url="https://x/b", description="b", section="Products"),
        lt.LlmsPage(product_id="C", title="C", url="https://x/c", description="c", section="Guides"),
    ]
    text = lt.generate_llms_txt(_SITE, pages)
    lines = text.splitlines()
    guides_idx = lines.index("## Guides")
    products_idx = lines.index("## Products")
    assert guides_idx < products_idx  # "Guides" seen first (page A)
    assert lines[guides_idx + 1] == "- [A](https://x/a): a"
    assert lines[guides_idx + 2] == "- [C](https://x/c): c"


def test_build_llms_txt_report_counts_and_summary() -> None:
    report = lt.build_llms_txt(_SITE, _catalog())
    assert report.n_pages == 2
    assert report.n_excluded == 2
    assert "2 page(s)" in report.summary
    assert "2 SKU(s) excluded" in report.summary
    assert report.text == lt.generate_llms_txt(_SITE, report.pages)


# -- validate_llms_txt (structural checker) -----------------------------------


def test_validate_llms_txt_accepts_a_correct_document() -> None:
    report = lt.build_llms_txt(_SITE, _catalog())
    assert lt.validate_llms_txt(report.text) == []
    assert lt.is_valid_llms_txt(report.text) is True


def test_validate_llms_txt_catches_a_deliberately_malformed_example() -> None:
    malformed = "Example Shop\n\nNo blockquote here.\n"
    issues = lt.validate_llms_txt(malformed)
    assert any("H1 site-name" in i for i in issues)
    assert any("blockquote" in i for i in issues)
    assert lt.is_valid_llms_txt(malformed) is False


def test_validate_llms_txt_catches_a_section_with_no_list_items() -> None:
    malformed = "# Shop\n\n> Summary.\n\n## Products\n\nSome prose, no list items.\n"
    issues = lt.validate_llms_txt(malformed)
    assert any("paired with at least one" in i for i in issues)


def test_validate_llms_txt_rejects_non_string() -> None:
    assert lt.validate_llms_txt(123) == ["llms.txt content must be a string"]


def test_validate_llms_txt_accepts_document_with_no_pages_at_all() -> None:
    text = lt.generate_llms_txt(_SITE, [])
    assert lt.validate_llms_txt(text) == []


# -- write_llms_txt (local file write, UTF-8 round trip) ---------------------


def test_write_llms_txt_round_trips_non_ascii_utf8_content(tmp_path) -> None:
    site = lt.SiteInfo(name="Tienda Ejemplo", summary="Vendemos artículos de calidad para el hogar.")
    item = sg.CatalogItem(
        product_id="SKU-300", title="Cafetera Española", url="https://tienda.example.com/cafetera",
        description="Cafetera de acero inoxidable, fácil de usar.",
    )
    report = lt.build_llms_txt(site, [item])
    path = lt.write_llms_txt(report.text, tmp_path / "llms.txt")

    raw_bytes = path.read_bytes()
    decoded = raw_bytes.decode("utf-8")  # raises UnicodeDecodeError if not valid UTF-8
    assert decoded == report.text
    assert "Cafetera Española" in decoded
    assert "artículos" in decoded
    assert lt.validate_llms_txt(decoded) == []
