"""Defense-in-depth redaction of browser snapshots before they reach the LLM.

The driver already never *reads* a password field (``ElementInfo.value`` is
``None`` for ``type="password"`` inputs — see the JS serializer in ``cdp.py``).
This module is the second layer, applied unconditionally at the mouth of the
LLM call: whatever slips through — a text field that echoed a secret back, an
email/phone visible in page text, a customer's card number in an order view —
is scrubbed so the model never sees it and it never lands in a prompt, log or
trace.

Scope, stated rather than implied: this redacts (1) any element whose role or
type says it is a password field, and (2) the PII/token patterns most likely to
appear in an admin backend — email addresses, phone numbers, 13-19-digit card /
account-number runs, and well-known secret prefixes. It is *not* a general data
classifier; the contract is "no passwords, no obvious PII, no obvious secrets"
redundantly enforced.
"""

from __future__ import annotations

import re
from dataclasses import replace

from src.connectors.browser.base import ElementInfo, PageSnapshot

PASSWORD_PLACEHOLDER = "<redacted>"

#: Fields whose value must never survive a snapshot. ``role`` carries the
#: input's ``type`` (see ``base.ElementInfo``), so "password" is the canonical
#: marker; the tag-level check is a belt-and-suspenders for a serializer that
#: ever forgets to set ``role``.
_PASSWORD_ROLES = frozenset({"password", "new-password", "current-password"})

# Email: user@host.tld. Bounded length so a whole page of addresses still
# masks quickly without catastrophic regex behavior.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{2,63}\.[A-Za-z]{2,24}\b")

# Phone-ish numbers with 7+ digits (national/local formats vary wildly); this is
# intentionally broad — over-masking a bare number in admin text is safer than
# leaking a customer's line.
_PHONE_RE = re.compile(r"(?<!\d)\+?[\d(][\d\s()./-]{7,19}\d\b")

# Card / account numbers: 13-19 consecutive digits (optionally grouped in
# 4s with spaces/dashes). Mask the whole run — the number is one unit.
_CARD_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b|\b\d{13,19}\b")

# Well-known secret prefixes (Stripe/AWS/GitHub/Slack/token Bearer). These are
# short, precise signatures — the prefix alone is not user-visible prose the
# way a bare digit run might be, so masking them is safe.
_TOKEN_RE = re.compile(
    r"\b("
    r"sk_live_[A-Za-z0-9]{12,}|"        # Stripe
    r"sk_[A-Za-z0-9]{24,}|"             # other provider keys
    r"AKIA[0-9A-Z]{16}|"                # AWS access key id
    r"ghp_[A-Za-z0-9]{20,}|"            # GitHub PAT
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"    # Slack
    r"Bearer\s+[A-Za-z0-9._~+/=-]{16,}" # Authorization header value
    r")\b"
)


def redact_text(text: str | None) -> str | None:
    """Mask PII/secret patterns in a string; ``None`` passes through unchanged."""
    if not text:
        return text
    out = _TOKEN_RE.sub(PASSWORD_PLACEHOLDER, text)
    out = _EMAIL_RE.sub(PASSWORD_PLACEHOLDER, out)
    out = _CARD_RE.sub(PASSWORD_PLACEHOLDER, out)
    out = _PHONE_RE.sub(PASSWORD_PLACEHOLDER, out)
    return out


def redact_snapshot(snapshot: PageSnapshot) -> PageSnapshot:
    """Return a copy of ``snapshot`` with passwords and PII forced out.

    Applied to every snapshot at the mouth of the LLM call (the loop never
    sends a raw snapshot to the model). Pure: returns a new ``PageSnapshot``
    and never mutates its argument.
    """
    def _masked_value(element: ElementInfo) -> str | None:
        # A password field's value never survives — whatever the serializer put
        # there, it becomes the placeholder. Every other field is still PII-
        # masked, because an admin page can echo a secret back into any input.
        if element.role in _PASSWORD_ROLES:
            return PASSWORD_PLACEHOLDER
        return element.value if element.value is None else redact_text(element.value)

    elements = tuple(
        replace(
            element,
            value=_masked_value(element),
            text=redact_text(element.text),
        )
        for element in snapshot.elements
    )
    return PageSnapshot(
        url=snapshot.url,
        title=redact_text(snapshot.title) or "",
        text=redact_text(snapshot.text) or "",
        elements=elements,
        screenshot_b64=snapshot.screenshot_b64,
    )
