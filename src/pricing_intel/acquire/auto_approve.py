"""Auto-onboarding for a new competitor domain via robots.txt ONLY (Task 1 /
PR-1 of the discovery-assisted competitor price intelligence plan, R1).

Given a competitor product-page URL, this module resolves the bare domain
(reusing ``acquire.base.normalize_domain``), checks ONLY that domain's
robots.txt for permission to fetch (NEVER its Terms of Service -- a human
reviews ToS separately; this module never does), and -- if and only if
robots.txt permits it -- self-writes a minimal, conservative
``config/sites/<domain>.yaml`` that the existing compliance gate
(``acquire.base.require_approved_site``) can subsequently read.

Two invariants this module can never violate (plan NON-GOALS 2 and 1; see
the plan's Task 1 "Risk callouts"):

1. The written config's ``tos_decision`` is ALWAYS :data:`AUTO_TOS_DECISION`
   (``"limited"``), NEVER ``"allowed"`` -- robots.txt says nothing about a
   site's Terms of Service, so an automated process has no basis to claim a
   human legal review ever happened. Tests assert both the module constant
   AND the raw written file text (no future edit can drift this silently).
2. ``max_tier_allowed`` is ALWAYS :data:`AUTO_MAX_TIER` (``"L1"``) --
   auto-onboarding never self-grants a higher acquisition tier than the
   minimal one discovery needs. A move to L2/L3 always requires a human
   (enforced downstream by a later PR's watch-escalation check, R5).

The robots.txt read is the ONLY network I/O in this module, and it lives
entirely behind the injectable ``robots_reader`` seam of
:func:`auto_approve_site` (see :func:`_check_robots`) -- so the test suite
never has to touch a real robots.txt (Risk callout: "the robots read is the
single network touch -- keep it injectable so CI never hits a real
robots.txt"). The allow/deny decision is delegated verbatim to
``urllib.robotparser.RobotFileParser.can_fetch`` semantics: per RFC 9309, a
401/403 fetching robots.txt itself is treated as disallow-all, while a
404/absent robots.txt is treated as allow-all. Any read that raises (DNS
failure, timeout, malformed response, ...) is treated conservatively as a
rejection -- never as an implicit allow.

Idempotent and non-destructive: if ``config/sites/<domain>.yaml`` already
exists, this module returns that file's current approval status verbatim and
writes NOTHING -- it never overwrites, downgrades, or re-dates a config a
human (or an earlier auto-approval) may already have produced.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import yaml

from . import base
from .pdp_fetcher import USER_AGENT

# NEVER "allowed" -- see module docstring point 1. Feeds the written config
# AND is asserted directly by tests, so a future edit here fails loudly
# instead of silently drifting toward a bogus ToS clearance.
AUTO_TOS_DECISION = "limited"

# NEVER higher -- see module docstring point 2.
AUTO_MAX_TIER = "L1"

AUTO_TOS_SUMMARY = "Auto-onboarded via robots.txt only; Terms of Service not reviewed by a human."

# Used when robots.txt exists but declares no Crawl-delay for our UA (or when
# an injected robots_reader stub doesn't expose one at all -- see
# _check_robots).
DEFAULT_RATE_LIMIT_SECONDS = 5.0


@dataclass(frozen=True)
class OnboardingResult:
    """Outcome of one :func:`auto_approve_site` call. ``domain`` is ``None``
    only when ``url`` itself could not be resolved to a bare domain at all
    (see ``acquire.base.normalize_domain``); every other rejection still
    reports the resolved ``domain`` (for logging/reporting) even though
    ``config_path`` stays ``None`` -- nothing was written."""

    domain: str | None
    approved: bool
    config_path: Path | None
    reason: str


def _existing_config_result(domain: str, config_dir: Path | str, config_path: Path) -> OnboardingResult | None:
    """``None`` if no config exists yet for ``domain``; otherwise the
    non-destructive result this module must return without writing
    anything -- a human-reviewed (or previously auto-approved) record is
    never silently overwritten, downgraded, or re-dated."""
    try:
        existing = base.load_site_config(domain, config_dir=config_dir)
    except base.SiteNotConfiguredError:
        return None
    return OnboardingResult(domain, existing.is_approved, config_path, "config_already_exists")


def _check_robots(
    url: str, user_agent: str, robots_reader: Callable[[str, str], bool] | None
) -> tuple[bool, float | None]:
    """The ONLY network I/O in this module. Returns ``(allowed,
    crawl_delay_seconds)``.

    When ``robots_reader`` is supplied (tests: fully offline), it alone
    decides ``allowed`` and ``crawl_delay_seconds`` is always ``None`` -- an
    injected stub has no real robots.txt body to report a Crawl-delay from.

    When ``robots_reader`` is ``None`` (the production default), builds one
    ``RobotFileParser``, ``.set_url(...)``, ``.read()`` -- and answers both
    ``can_fetch(user_agent, url)`` and ``crawl_delay(user_agent)`` from that
    SAME parse, so there is still exactly one network read either way.

    Any exception during the read (DNS failure, timeout, ...) is swallowed
    and treated as ``(False, None)`` -- a conservative rejection, never an
    implicit allow.
    """
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        if robots_reader is not None:
            return robots_reader(robots_url, user_agent), None
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.read()
        delay = parser.crawl_delay(user_agent)
        return parser.can_fetch(user_agent, url), (float(delay) if delay is not None else None)
    except Exception:
        return False, None


def _write_config(
    domain: str, config_dir: Path | str, config_path: Path, rate_limit_seconds: float, today: date
) -> OnboardingResult:
    """Write the auto-onboarded YAML and re-load it through
    ``base.load_site_config`` before returning, so a malformed write (e.g. a
    future edit that drops a field ``SiteConfig.__post_init__`` requires)
    fails loudly right here instead of silently persisting a config no
    fetcher could actually load later."""
    data = {
        "domain": domain,
        "robots_txt_respected": True,
        "robots_checked_at": today.isoformat(),
        "tos_summary": AUTO_TOS_SUMMARY,
        "tos_decision": AUTO_TOS_DECISION,
        "rate_limit_seconds": rate_limit_seconds,
        "max_tier_allowed": AUTO_MAX_TIER,
        "pii_policy": "none",
    }
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    loaded = base.load_site_config(domain, config_dir=config_dir)
    return OnboardingResult(domain, loaded.is_approved, config_path, "auto_approved_via_robots_txt")


def auto_approve_site(
    url: str,
    *,
    config_dir: Path | str = base.DEFAULT_SITES_CONFIG_DIR,
    robots_reader: Callable[[str, str], bool] | None = None,
    user_agent: str = USER_AGENT,
    now: date | None = None,
) -> OnboardingResult:
    """Self-onboard ``url``'s domain via robots.txt ONLY -- see module
    docstring for the two invariants this can never violate.

    Never touches the network except through the single seam described in
    :func:`_check_robots`. Returns without writing anything for an
    unresolvable ``url`` (reason ``"invalid_url"``), a domain containing
    characters ``base._config_path`` refuses (a port, userinfo, or any other
    character outside its safe-hostname regex; reason
    ``"invalid_domain_characters"``) -- ``base.normalize_domain`` does NOT
    reject these itself, so this is a second, explicit boundary check rather
    than an uncaught ``ValueError`` -- an already-existing config of any
    approval status (reason ``"config_already_exists"``), or a robots.txt
    disallow (reason mentions ``"robots_disallow"``); writes
    ``config/sites/<domain>.yaml`` and re-loads it through
    ``base.load_site_config`` only on a clean robots.txt allow.
    """
    domain = base.normalize_domain(url)
    if domain is None:
        return OnboardingResult(None, False, None, "invalid_url")

    try:
        config_path = base._config_path(domain, config_dir)
    except ValueError:
        # base._config_path's _SAFE_DOMAIN regex rejects a port, userinfo, or
        # any other character normalize_domain lets through -- a rejection,
        # never an uncaught exception (no silent caps: still returns a
        # machine-readable reason, just for a different failure mode).
        return OnboardingResult(domain, False, None, "invalid_domain_characters")

    existing_result = _existing_config_result(domain, config_dir, config_path)
    if existing_result is not None:
        return existing_result

    allowed, crawl_delay = _check_robots(url, user_agent, robots_reader)
    if not allowed:
        return OnboardingResult(domain, False, None, "robots_disallow")

    rate_limit_seconds = crawl_delay if crawl_delay is not None else DEFAULT_RATE_LIMIT_SECONDS
    today = now or date.today()
    return _write_config(domain, config_dir, config_path, rate_limit_seconds, today)
