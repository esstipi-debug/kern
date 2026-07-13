"""Tests for src/seo/pdp_writer.py (Linchpin 3.0 PR-24, S3 PDP content
generation from a client's own catalog).

Numbers/strings verified by hand (and cross-checked by running the actual
functions -- pure deterministic templating, so both agree) -- no network, no
file I/O outside the write() test (which uses tmp_path).
"""

from __future__ import annotations

import json
from dataclasses import replace

from src.seo import pdp_writer as pw
from src.seo.schema_gen import CatalogItem


def _full_item(**overrides) -> CatalogItem:
    defaults = dict(
        product_id="SKU-100", title="Wireless Mouse X200", price=29.99, currency="USD",
        description="Ergonomic wireless mouse with silent clicks", brand="Acme",
        on_hand=12.0, condition="new",
    )
    defaults.update(overrides)
    return CatalogItem(**defaults)


_ENRICHMENT = pw.PdpEnrichment(
    category="Computer Accessories", attributes={"color": "black", "connectivity": "bluetooth"}
)


# -- build_claims / build_pdp_content: hand-verified claim-by-claim ---------


def test_build_claims_full_item_matches_hand_verified_claim_list() -> None:
    claims = pw.build_claims(_full_item(), _ENRICHMENT)
    assert [(c.text, c.field, c.field_value) for c in claims] == [
        ("Wireless Mouse X200.", "title", "Wireless Mouse X200"),
        ("By Acme.", "brand", "Acme"),
        ("Category: Computer Accessories.", "category", "Computer Accessories"),
        ("Color: black.", "attributes.color", "black"),
        ("Connectivity: bluetooth.", "attributes.connectivity", "bluetooth"),
        ("Ergonomic wireless mouse with silent clicks.", "description", "Ergonomic wireless mouse with silent clicks"),
        ("Priced at 29.99 USD.", "price", "29.99"),
        ("Available now.", "on_hand", "InStock"),
        ("Sold as new.", "condition", "NewCondition"),
    ]


def test_build_pdp_content_full_item_title_tag_and_body_copy() -> None:
    content = pw.build_pdp_content(_full_item(), _ENRICHMENT)
    assert content.title_tag == "Wireless Mouse X200 | Acme"
    assert content.body_copy == (
        "Wireless Mouse X200. By Acme. Category: Computer Accessories. Color: black. "
        "Connectivity: bluetooth. Ergonomic wireless mouse with silent clicks. "
        "Priced at 29.99 USD. Available now. Sold as new."
    )
    assert content.generator == "template"
    assert content.product_id == "SKU-100"


def test_build_pdp_content_meta_description_stops_before_155_chars() -> None:
    # Hand-verified: title(20) + brand(9) + category(32) + color(14) +
    # connectivity(25) + description(45) sum to 145 chars including
    # separators -- adding the 21-char " Priced at 29.99 USD." would push
    # the running total to 166 > META_DESCRIPTION_MAX_CHARS (155), so the
    # price/availability/condition claims are excluded from the meta
    # description (still present in the fuller body_copy).
    content = pw.build_pdp_content(_full_item(), _ENRICHMENT)
    assert content.meta_description == (
        "Wireless Mouse X200. By Acme. Category: Computer Accessories. Color: black. "
        "Connectivity: bluetooth. Ergonomic wireless mouse with silent clicks."
    )
    assert len(content.meta_description) == 145
    assert len(content.meta_description) <= pw.META_DESCRIPTION_MAX_CHARS
    assert "Priced at" not in content.meta_description


def test_build_pdp_content_source_fields_maps_field_to_generated_text() -> None:
    content = pw.build_pdp_content(_full_item(), _ENRICHMENT)
    assert content.source_fields["title"] == "Wireless Mouse X200."
    assert content.source_fields["brand"] == "By Acme."
    assert content.source_fields["attributes.color"] == "Color: black."
    assert content.source_fields["on_hand"] == "Available now."


# -- sparse catalog row: shorter copy, never fabricated filler --------------


def test_build_pdp_content_sparse_item_produces_only_title_claim() -> None:
    item = CatalogItem(product_id="SKU-200", title="Bare Widget")
    content = pw.build_pdp_content(item)
    assert content.title_tag == "Bare Widget"
    assert content.meta_description == "Bare Widget."
    assert content.body_copy == "Bare Widget."
    assert len(content.claims) == 1
    assert content.claims[0].field == "title"
    assert content.source_fields == {"title": "Bare Widget."}


def test_build_pdp_content_sparse_item_never_claims_availability_or_price() -> None:
    # No price, no currency, no on_hand/availability signal at all -- must
    # not fabricate "Available now." or a price sentence out of nothing.
    item = CatalogItem(product_id="SKU-201", title="Mystery Item")
    content = pw.build_pdp_content(item)
    fields = {c.field for c in content.claims}
    assert fields == {"title"}
    assert "Available" not in content.body_copy
    assert "Priced at" not in content.body_copy


def test_missing_required_pdp_fields_only_needs_product_id_and_title() -> None:
    item = CatalogItem(product_id="SKU-202", title="X", brand=None, price=None, currency=None)
    assert pw.missing_required_pdp_fields(item) == []


def test_missing_required_pdp_fields_flags_blank_title() -> None:
    item = CatalogItem(product_id="SKU-203", title="   ")
    assert pw.missing_required_pdp_fields(item) == ["title"]


def test_build_pdp_content_raises_on_missing_title() -> None:
    item = CatalogItem(product_id="SKU-204", title="")
    try:
        pw.build_pdp_content(item)
        raised = False
    except ValueError as exc:
        raised = True
        assert "title" in str(exc)
    assert raised


# -- title tag length handling ------------------------------------------------


def test_build_title_tag_appends_brand_when_it_fits() -> None:
    item = CatalogItem(product_id="SKU-300", title="Compact Keyboard", brand="Acme")
    assert pw.build_title_tag(item) == "Compact Keyboard | Acme"


def test_build_title_tag_omits_brand_when_combined_exceeds_max() -> None:
    item = CatalogItem(product_id="SKU-301", title="A" * 55, brand="SuperLongBrandName")
    tag = pw.build_title_tag(item)
    assert tag == "A" * 55
    assert len(tag) <= pw.TITLE_TAG_MAX_CHARS


def test_build_title_tag_truncates_long_title_at_word_boundary() -> None:
    item = CatalogItem(product_id="SKU-302", title="A" * 80)
    tag = pw.build_title_tag(item)
    assert len(tag) == pw.TITLE_TAG_MAX_CHARS
    assert tag.endswith("...")


# -- availability / condition claims -----------------------------------------


def test_availability_claim_in_stock_from_on_hand() -> None:
    item = CatalogItem(product_id="SKU-400", title="X", on_hand=5.0)
    content = pw.build_pdp_content(item)
    assert content.body_copy == "X. Available now."


def test_availability_claim_out_of_stock_from_on_hand_zero() -> None:
    item = CatalogItem(product_id="SKU-401", title="X", on_hand=0.0)
    content = pw.build_pdp_content(item)
    assert content.body_copy == "X. Currently unavailable."


def test_availability_claim_explicit_override_wins_over_on_hand() -> None:
    item = CatalogItem(product_id="SKU-402", title="X", on_hand=999.0, availability="preorder")
    content = pw.build_pdp_content(item)
    assert content.body_copy == "X. Available for pre-order."
    assert content.claims[-1].field == "availability"


def test_condition_claim_refurbished() -> None:
    item = CatalogItem(product_id="SKU-403", title="X", condition="refurbished")
    content = pw.build_pdp_content(item)
    assert content.body_copy == "X. Sold as refurbished."


def test_availability_and_condition_copy_cover_every_schema_gen_token() -> None:
    from src.seo.schema_gen import SCHEMA_AVAILABILITY_TOKENS, SCHEMA_CONDITION_TOKENS

    assert set(pw.AVAILABILITY_COPY) == set(SCHEMA_AVAILABILITY_TOKENS)
    assert set(pw.CONDITION_COPY) == set(SCHEMA_CONDITION_TOKENS)


# -- verify_claims_traceable: the QA gate ------------------------------------


def test_verify_claims_traceable_passes_for_template_output() -> None:
    content = pw.build_pdp_content(_full_item(), _ENRICHMENT)
    assert pw.verify_claims_traceable(content) == []
    assert pw.claims_traceable_passed(content) is True


def test_verify_claims_traceable_passes_for_sparse_output() -> None:
    content = pw.build_pdp_content(CatalogItem(product_id="SKU-500", title="X"))
    assert pw.verify_claims_traceable(content) == []


def test_verify_claims_traceable_catches_fabricated_extractive_claim() -> None:
    content = pw.build_pdp_content(_full_item(), _ENRICHMENT)
    bogus = pw.Claim(text="Free shipping worldwide.", field="brand", field_value="Acme")
    bad_content = replace(
        content, claims=content.claims + (bogus,), body_copy=content.body_copy + " " + bogus.text
    )
    issues = pw.verify_claims_traceable(bad_content)
    assert len(issues) == 1
    assert "Acme" in issues[0]
    assert pw.claims_traceable_passed(bad_content) is False


def test_verify_claims_traceable_catches_claim_missing_from_body_copy() -> None:
    content = pw.build_pdp_content(_full_item())
    phantom = pw.Claim(text="This sentence was never inserted.", field="title", field_value="Wireless Mouse X200")
    bad_content = replace(content, claims=content.claims + (phantom,))  # body_copy NOT updated
    issues = pw.verify_claims_traceable(bad_content)
    assert any("not found verbatim in body_copy" in issue for issue in issues)


def test_verify_claims_traceable_catches_wrong_derived_sentence_for_token() -> None:
    item = CatalogItem(product_id="SKU-600", title="X", on_hand=5.0)
    content = pw.build_pdp_content(item)
    wrong = pw.Claim(text="Ships next day.", field="on_hand", field_value="InStock")
    bad_claims = tuple(c for c in content.claims if c.field != "on_hand") + (wrong,)
    bad_content = replace(content, claims=bad_claims, body_copy=" ".join(c.text for c in bad_claims))
    issues = pw.verify_claims_traceable(bad_content)
    assert len(issues) == 1
    assert "closed-vocabulary" in issues[0]


# -- catalog_to_pdp_content: batch entry point -------------------------------


def test_catalog_to_pdp_content_excludes_rows_missing_title() -> None:
    items = [
        CatalogItem(product_id="A1", title="Item A"),
        CatalogItem(product_id="A2", title="   "),
        CatalogItem(product_id="A3", title="Item C"),
    ]
    report = pw.catalog_to_pdp_content(items)
    assert report.n_generated == 2
    assert report.n_excluded == 1
    assert report.excluded[0].product_id == "A2"
    assert report.excluded[0].reasons == ("title",)
    assert [g.product_id for g in report.generated] == ["A1", "A3"]


def test_catalog_to_pdp_content_applies_enrichment_by_product_id() -> None:
    items = [CatalogItem(product_id="A1", title="Item A"), CatalogItem(product_id="A2", title="Item B")]
    enrichments = {"A1": pw.PdpEnrichment(category="Widgets")}
    report = pw.catalog_to_pdp_content(items, enrichments)
    by_id = {g.product_id: g for g in report.generated}
    assert "Category: Widgets." in by_id["A1"].body_copy
    assert "Category:" not in by_id["A2"].body_copy


def test_catalog_to_pdp_content_no_exclusions_reports_zero() -> None:
    report = pw.catalog_to_pdp_content([CatalogItem(product_id="A1", title="Item A")])
    assert report.n_excluded == 0
    assert report.excluded == ()
    assert "0 excluded" in report.summary


# -- LLM enhancement path: strict schema + rule-10 re-verification ----------


def _polishing_llm(req: pw.PdpEnhancementRequest) -> pw.LlmPdpEnhancement:
    assert all(c.field not in pw.PROTECTED_CLAIM_FIELDS for c in req.claims)  # never sees protected claims
    title = req.claims[0].field_value
    return pw.LlmPdpEnhancement(
        meta_description=f"{title} -- polished for readability.",
        body_copy=f"{title} is a great product, polished for readability.",
        reason="stubbed rewrite for testing",
    )


def test_llm_pdp_enhancement_schema_rejects_empty_fields() -> None:
    try:
        pw.LlmPdpEnhancement(meta_description="", body_copy="x", reason="x")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_enhance_with_llm_accepts_valid_rewrite_and_appends_protected_suffix() -> None:
    item = CatalogItem(product_id="SKU-700", title="Test Product", brand="Acme", on_hand=5.0)
    content = pw.build_pdp_content(item)
    enhanced = pw.enhance_with_llm(content, _polishing_llm)
    assert enhanced.generator == "llm"
    assert enhanced.body_copy == "Test Product is a great product, polished for readability. Available now."
    assert enhanced.meta_description == "Test Product -- polished for readability."
    assert enhanced.title_tag == content.title_tag  # title tag is never LLM-touched
    assert enhanced.claims == content.claims  # citation trail unchanged


def test_enhance_with_llm_falls_back_when_title_dropped() -> None:
    item = CatalogItem(product_id="SKU-701", title="Test Product", on_hand=5.0)
    content = pw.build_pdp_content(item)

    def drops_title(req: pw.PdpEnhancementRequest) -> pw.LlmPdpEnhancement:
        return pw.LlmPdpEnhancement(meta_description="Great gadget for you.", body_copy="Great gadget, buy now.", reason="x")

    enhanced = pw.enhance_with_llm(content, drops_title)
    assert enhanced is content
    assert enhanced.generator == "template"


def test_enhance_with_llm_falls_back_on_exception() -> None:
    content = pw.build_pdp_content(CatalogItem(product_id="SKU-702", title="Test Product"))

    def raises(req: pw.PdpEnhancementRequest) -> pw.LlmPdpEnhancement:
        raise RuntimeError("network down")

    enhanced = pw.enhance_with_llm(content, raises)
    assert enhanced is content


def test_enhance_with_llm_falls_back_on_wrong_return_type() -> None:
    content = pw.build_pdp_content(CatalogItem(product_id="SKU-703", title="Test Product"))
    enhanced = pw.enhance_with_llm(content, lambda req: "not a dataclass")
    assert enhanced is content


def test_enhance_with_llm_falls_back_when_meta_description_too_long() -> None:
    content = pw.build_pdp_content(CatalogItem(product_id="SKU-704", title="Test Product"))

    def too_long(req: pw.PdpEnhancementRequest) -> pw.LlmPdpEnhancement:
        return pw.LlmPdpEnhancement(
            meta_description="Test Product " + "x" * 160, body_copy="Test Product filler.", reason="x"
        )

    enhanced = pw.enhance_with_llm(content, too_long)
    assert enhanced is content


def test_enhance_with_llm_never_hands_protected_claims_to_callable() -> None:
    item = CatalogItem(product_id="SKU-705", title="Test Product", on_hand=5.0, condition="new")
    content = pw.build_pdp_content(item)
    seen_fields: list[str] = []

    def spy(req: pw.PdpEnhancementRequest) -> pw.LlmPdpEnhancement:
        seen_fields.extend(c.field for c in req.claims)
        return pw.LlmPdpEnhancement(meta_description="Test Product.", body_copy="Test Product ready to ship.", reason="x")

    pw.enhance_with_llm(content, spy)
    assert "on_hand" not in seen_fields
    assert "condition" not in seen_fields
    assert "title" in seen_fields


def test_enhance_with_llm_returns_unchanged_when_no_claims_at_all() -> None:
    # Defensive path: a manually-constructed PdpContent with no claims (never
    # produced by build_pdp_content, which always emits a title claim) --
    # nothing rephrase-safe exists, so the callable must not even be called.
    empty_content = pw.PdpContent(
        product_id="X", title_tag="X", meta_description="", body_copy="", claims=(), source_fields={}
    )

    def must_not_be_called(req: pw.PdpEnhancementRequest) -> pw.LlmPdpEnhancement:
        raise AssertionError("llm callable should not be invoked with no soft claims")

    result = pw.enhance_with_llm(empty_content, must_not_be_called)
    assert result is empty_content


def test_catalog_to_pdp_content_respects_llm_budget() -> None:
    items = [CatalogItem(product_id=f"B{i}", title=f"Item {i}") for i in range(3)]
    report = pw.catalog_to_pdp_content(items, llm=_polishing_llm, llm_budget=1)
    assert report.n_llm_enhanced == 1
    generators = [g.generator for g in report.generated]
    assert generators.count("llm") == 1
    assert generators.count("template") == 2


def test_catalog_to_pdp_content_without_llm_stays_template_only() -> None:
    items = [CatalogItem(product_id="C1", title="Item C1")]
    report = pw.catalog_to_pdp_content(items)
    assert report.n_llm_enhanced == 0
    assert report.generated[0].generator == "template"


# -- write_pdp_content --------------------------------------------------------


def test_write_pdp_content_writes_per_sku_combined_and_excluded_files(tmp_path) -> None:
    items = [CatalogItem(product_id="SKU-800", title="Item"), CatalogItem(product_id="SKU-801", title="   ")]
    report = pw.catalog_to_pdp_content(items)
    written = pw.write_pdp_content(report, tmp_path)

    per_sku_path = written["pdp:SKU-800"]
    assert per_sku_path.exists()
    per_sku_doc = json.loads(per_sku_path.read_text(encoding="utf-8"))
    assert per_sku_doc["product_id"] == "SKU-800"
    assert per_sku_doc["title_tag"] == "Item"
    assert per_sku_doc["generator"] == "template"

    combined_doc = json.loads(written["combined"].read_text(encoding="utf-8"))
    assert len(combined_doc) == 1
    assert combined_doc[0]["product_id"] == "SKU-800"

    excluded_text = written["excluded_csv"].read_text(encoding="utf-8")
    assert "SKU-801" in excluded_text
    assert "title" in excluded_text
