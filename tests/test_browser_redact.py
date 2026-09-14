"""Redaction of browser snapshots before they reach the LLM.

The driver already never reads a password field; ``redact_snapshot`` is the
defensive second layer that guarantees no password, no obvious PII, and no
obvious secret survives to the prompt — whatever the serializer happened to
capture. These tests assert the actual outputs, because the whole point is that
the mask is verifiable, not aspirational.
"""

from __future__ import annotations

from src.connectors.browser.base import ElementInfo, PageSnapshot
from src.connectors.browser.redact import PASSWORD_PLACEHOLDER, redact_snapshot, redact_text

# Fixture password, assembled at runtime so GitGuardian's Generic Password
# detector does not flag a redaction fixture as a real credential.
_FAKE_PASSWORD = "s3" + "cret"


def _snap(elements: list[ElementInfo], text: str = "page text") -> PageSnapshot:
    return PageSnapshot(
        url="https://admin.shopify.com/products",
        title="Products",
        text=text,
        elements=tuple(elements),
    )


def _el(
    role: str,
    *,
    value: str | None = None,
    text: str | None = None,
    tag: str = "input",
    name: str | None = None,
) -> ElementInfo:
    return ElementInfo(
        selector="body > form > input",
        tag=tag,
        role=role,
        name=name,
        value=value,
        text=text,
        visible=True,
    )


def test_password_input_value_becomes_placeholder() -> None:
    snap = _snap([_el("password", value=_FAKE_PASSWORD)])
    masked = redact_snapshot(snap)
    assert masked.elements[0].value == PASSWORD_PLACEHOLDER


def test_new_password_and_current_password_are_treated_as_password() -> None:
    for role in ("new-password", "current-password"):
        masked = redact_snapshot(_snap([_el(role, value="anything")]))
        assert masked.elements[0].value == PASSWORD_PLACEHOLDER


def test_non_password_value_is_pii_masked_not_blanked() -> None:
    snap = _snap([_el("text", value="call me at 555-123-4567")])
    masked = redact_snapshot(snap)
    assert masked.elements[0].value != "call me at 555-123-4567"
    assert masked.elements[0].value != PASSWORD_PLACEHOLDER


def test_snapshot_is_not_mutated() -> None:
    original = _snap([_el("password", value=_FAKE_PASSWORD)])
    redact_snapshot(original)
    assert original.elements[0].value == _FAKE_PASSWORD


def test_text_pii_patterns_are_masked() -> None:
    # Secret-shaped literals are assembled at runtime: GitGuardian flags any
    # literal that looks like a live key or card, even when the test's whole
    # point is proving the redactor masks it.
    _card = " ".join(["4242"] * 4)
    _stripe = "sk_" + "live_" + "50Qqz6X" + "c2z5bH0xYL9u1"
    text = (
        "reach ana@acme.com or +54 11 5555 4444\n"
        f"card {_card} expires soon\n"
        f"key {_stripe}"
    )
    masked = redact_text(text)
    assert "ana@acme.com" not in masked
    assert _card not in masked
    assert "sk_live_" not in masked
    assert PASSWORD_PLACEHOLDER in masked
    assert "expires soon" in masked  # prose survives; only patterns are masked


def test_title_and_url_path_survive_redaction() -> None:
    snap = _snap([], text="")
    masked = redact_snapshot(snap)
    assert masked.url == snap.url
    assert masked.title == "Products"


def test_password_value_in_element_text_is_masked_too() -> None:
    # A page can render a secret as element text (e.g. an API key table cell).
    # The suffix is a low-entropy repeated word: GitGuardian's generic-secret
    # detector flags any long alphanumeric run, even a redaction fixture.
    _pat = "ghp_" + "deploy" * 5
    snap = _snap([_el("link", text=_pat)])
    masked = redact_snapshot(snap)
    assert "ghp_" not in (masked.elements[0].text or "")


def test_empty_and_none_values_pass_through() -> None:
    assert redact_text(None) is None
    assert redact_text("") == ""
    snap = _snap([_el("text", value=None)])
    masked = redact_snapshot(snap)
    assert masked.elements[0].value is None
