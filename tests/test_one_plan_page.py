"""HTTP-level tests for GET /one-plan (the English AU/NZ agency landing page,
webapp/one_plan_page.py).

This page mirrors webapp/stocky_alternative_page.py exactly (own English shell,
same dark/teal visual system, same two real offers, same FAQ + JSON-LD
mechanism) but positions Kern as a fractional planning team rather than a Stocky
replacement. It carries real brand/compliance stakes:

  * a hard banned-words list must never appear in the rendered HTML, and
  * the fractional-team economics must be framed against a loaded full-time
    planner hire (~USD 100-120k/yr), giving the SHIPPED Starter price
    (USD 900/mo = ~USD 10,800/yr) a ratio of ~10% -- NOT the stale "~40-50%"
    figure a prior draft computed on a since-repriced Growth tier.

Both are asserted below against the *rendered* HTML, not template literals.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402
from webapp.offers import get_offer  # noqa: E402
from webapp.one_plan_page import H1_PROMISE_B  # noqa: E402

client = TestClient(app)

# Terms that must NEVER appear in the rendered page (brand/compliance guardrail).
# Scanned case-insensitively against the full rendered HTML string.
_BANNED_WORDS: tuple[str, ...] = (
    "certificado",
    "certified",
    "audit-grade",
    "meets iso",
    "cumple iso",
    "exceed",  # from the banned "EXCEED"
    "10x",
    "10×",  # 10 followed by the unicode multiplication sign
    "digital twin",
    "operate your whole chain",
    "operate your entire chain",
)


def test_one_plan_page_ok() -> None:
    resp = client.get("/one-plan")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_h1_carries_the_promise_b_positioning_spine() -> None:
    body = client.get("/one-plan").text
    # The verbatim H1 promise-B string (shared constant, so page + test can't drift).
    assert H1_PROMISE_B in body
    # And a readable, entity-free key phrase, so the intent is legible without the constant.
    assert "one plan that stops fighting itself" in body


def test_rendered_html_contains_no_banned_words() -> None:
    body = client.get("/one-plan").text.lower()
    hits = [term for term in _BANNED_WORDS if term.lower() in body]
    assert not hits, f"banned term(s) present in rendered /one-plan HTML: {hits}"


def test_page_contains_valid_faq_jsonld() -> None:
    body = client.get("/one-plan").text
    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
    )
    assert match is not None, "FAQPage JSON-LD script tag not found"
    doc = json.loads(match.group(1))
    assert doc["@type"] == "FAQPage"
    assert len(doc["mainEntity"]) >= 3
    for entry in doc["mainEntity"]:
        assert entry["@type"] == "Question"
        assert entry["name"]
        assert entry["acceptedAnswer"]["text"]


def test_page_reuses_the_two_real_offers_and_invents_no_pricing() -> None:
    body = client.get("/one-plan").text
    starter = get_offer("starter-fundamentos")
    diagnostico = get_offer("diagnostico-arranque")
    assert starter is not None and diagnostico is not None
    assert starter.slug in body
    assert diagnostico.slug in body
    # The authoritative price strings from webapp/offers.py appear verbatim --
    # no new price point is authored on this page.
    assert starter.price in body
    assert diagnostico.price in body


def test_economics_framed_against_a_full_time_hire_not_the_stale_ratio() -> None:
    body = client.get("/one-plan").text.lower()
    # Wrapper-A framing: compare to a loaded full-time planner hire.
    assert "full-time" in body
    assert "planner" in body
    # The stale, since-corrected figures must not resurface on THIS page.
    assert "40-50%" not in body
    assert "40-50" not in body
    assert "$4,000" not in body


def test_page_degrades_cleanly_without_any_sales_env_vars(monkeypatch) -> None:
    for slug in ("starter-fundamentos", "diagnostico-arranque"):
        offer = get_offer(slug)
        assert offer is not None
        monkeypatch.delenv(offer.stripe_env_var, raising=False)
    monkeypatch.delenv("CALENDLY_URL", raising=False)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    resp = client.get("/one-plan")
    assert resp.status_code == 200
    assert "mailto:?subject=" in resp.text


def test_page_links_to_free_demo_and_packages() -> None:
    body = client.get("/one-plan").text
    assert 'href="/demo"' in body
    assert 'href="/paquetes"' in body


def test_faq_contains_the_three_deal_killer_objections() -> None:
    """Task 6 (B2) / plan Asset 3 spec: the FAQ must cover the 3 deal-killer
    objections (0 clients, black-box, "my ERP already does this"), and the
    FAQPage JSON-LD must carry the same three, not just the rendered HTML.
    Task 5 already shipped these entries in webapp.one_plan_page._FAQ; this
    test pins that down as a regression guard rather than duplicating them.
    """
    body = client.get("/one-plan").text
    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', body, re.DOTALL
    )
    assert match is not None, "FAQPage JSON-LD script tag not found"
    doc = json.loads(match.group(1))
    jsonld_questions = " ".join(entry["name"].lower() for entry in doc["mainEntity"])

    body_lower = body.lower()
    for needle, label in (
        ("zero paying clients", "0-clients objection"),
        ("black box", "black-box objection"),
        ("already does this", "'my ERP already does this' objection"),
    ):
        assert needle in body_lower, f"rendered HTML missing the {label}"
        assert needle in jsonld_questions, f"FAQPage JSON-LD missing the {label}"
