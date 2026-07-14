"""Tests for src/pricing_intel/acquire/auto_approve.py (discovery-assisted
price intel plan, Task 1 / PR-1, requirement R1): self-onboarding a
competitor domain via robots.txt ONLY into ``config/sites/<domain>.yaml``.

Guarantees under test:
- a robots.txt allow writes a config with ``tos_decision: limited`` (NEVER
  ``allowed``), ``max_tier_allowed: L1`` (NEVER higher), ``pii_policy: none``,
  and ``robots_txt_respected: True`` -- and the raw written file text never
  contains the string "allowed" for the ToS field;
- a robots.txt disallow writes NOTHING to disk and reports a machine-readable
  rejection reason;
- an unresolvable URL (bare id, non-http scheme, empty string) is rejected
  before any disk I/O, with ``domain is None``;
- an already-existing config (any approval status) is returned verbatim,
  byte-unchanged on disk -- auto-onboarding never overwrites, downgrades, or
  re-dates a config that may have been human-reviewed;
- a robots.txt ``Crawl-delay`` becomes ``rate_limit_seconds`` when present,
  else a safe default is used;
- every field ``SiteConfig.__post_init__`` requires is actually populated
  (``robots_checked_at`` a valid ISO date, ``tos_summary`` non-empty), so the
  write never raises at the boundary.

All robots.txt reads are either fully offline via the injected
``robots_reader`` seam, or (for the two tests that exercise the real default
``RobotFileParser`` path to prove Crawl-delay wiring) via monkeypatching
``RobotFileParser.read`` to parse fixed text directly -- no test in this
file ever touches the network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.robotparser import RobotFileParser

import pytest

from src.pricing_intel.acquire.auto_approve import (
    AUTO_MAX_TIER,
    AUTO_TOS_DECISION,
    AUTO_TOS_SUMMARY,
    DEFAULT_RATE_LIMIT_SECONDS,
    OnboardingResult,
    auto_approve_site,
)
from src.pricing_intel.acquire.base import load_site_config

NOW = date(2026, 7, 14)


# -- module constants (NON-GOAL 1 / NON-GOAL 2 guardrails) --------------------


def test_auto_tos_decision_constant_is_limited() -> None:
    assert AUTO_TOS_DECISION == "limited"


def test_auto_max_tier_constant_is_l1() -> None:
    assert AUTO_MAX_TIER == "L1"


def test_auto_tos_summary_constant_is_the_fixed_disclosure() -> None:
    assert AUTO_TOS_SUMMARY == (
        "Auto-onboarded via robots.txt only; Terms of Service not reviewed by a human."
    )


# -- robots.txt allow -> writes a "limited" config -----------------------------


def test_writes_limited_config_when_robots_allows(tmp_path: Path) -> None:
    result = auto_approve_site(
        "https://newsite.test/products/1",
        config_dir=tmp_path,
        robots_reader=lambda robots_url, user_agent: True,
        now=NOW,
    )

    assert isinstance(result, OnboardingResult)
    assert result.domain == "newsite.test"
    assert result.approved is True
    assert result.config_path is not None
    assert result.config_path.exists()

    loaded = load_site_config("newsite.test", config_dir=tmp_path)
    assert loaded.tos_decision == "limited"
    assert loaded.max_tier_allowed == "L1"
    assert loaded.pii_policy == "none"
    assert loaded.robots_txt_respected is True

    # "allowed" as a bare substring also appears in the legitimate field name
    # "max_tier_allowed" -- assert the specific tos_decision VALUE instead,
    # which is the actual NON-GOAL-2 guardrail this test protects.
    raw_text = result.config_path.read_text(encoding="utf-8")
    assert "tos_decision: limited" in raw_text
    assert "tos_decision: allowed" not in raw_text


def test_never_writes_allowed(tmp_path: Path) -> None:
    assert AUTO_TOS_DECISION == "limited"

    result = auto_approve_site(
        "https://another.test/p/1",
        config_dir=tmp_path,
        robots_reader=lambda robots_url, user_agent: True,
        now=NOW,
    )

    loaded = load_site_config(result.domain, config_dir=tmp_path)
    assert loaded.tos_decision == "limited"
    # is_approved is True via the "limited" path (not "prohibited"), never
    # via a bogus "allowed" self-grant.
    assert loaded.is_approved is True


# -- robots.txt disallow -> writes NOTHING -------------------------------------


def test_rejects_and_writes_nothing_when_robots_disallows(tmp_path: Path) -> None:
    result = auto_approve_site(
        "https://blocked.test/p/1",
        config_dir=tmp_path,
        robots_reader=lambda robots_url, user_agent: False,
        now=NOW,
    )

    assert result.approved is False
    assert result.config_path is None
    assert "robots_disallow" in result.reason
    assert list(tmp_path.iterdir()) == []


# -- unresolvable URL -----------------------------------------------------------


@pytest.mark.parametrize("bad_url", ["MLA123456", "ftp://old.example.com", ""])
def test_id_only_or_malformed_url_rejected(tmp_path: Path, bad_url: str) -> None:
    result = auto_approve_site(bad_url, config_dir=tmp_path, now=NOW)

    assert result.domain is None
    assert result.approved is False
    assert result.config_path is None
    assert list(tmp_path.iterdir()) == []


# -- existing config is never overwritten --------------------------------------


def test_existing_config_not_overwritten(tmp_path: Path) -> None:
    existing_path = tmp_path / "preexisting.test.yaml"
    original_text = (
        "domain: preexisting.test\n"
        "robots_txt_respected: true\n"
        "robots_checked_at: '2026-07-01'\n"
        "tos_summary: 'human-reviewed, explicitly prohibited'\n"
        "tos_decision: prohibited\n"
        "rate_limit_seconds: 10.0\n"
        "max_tier_allowed: L0\n"
    )
    existing_path.write_text(original_text, encoding="utf-8")

    result = auto_approve_site(
        "https://preexisting.test/p/1",
        config_dir=tmp_path,
        robots_reader=lambda robots_url, user_agent: True,  # would approve if this were new
        now=NOW,
    )

    assert result.domain == "preexisting.test"
    assert result.approved is False  # reflects the existing prohibited record, unchanged
    assert result.reason == "config_already_exists"
    assert existing_path.read_text(encoding="utf-8") == original_text  # byte-unchanged


# -- robots.txt Crawl-delay -> rate_limit_seconds ------------------------------


def test_robots_crawl_delay_becomes_rate_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(self: RobotFileParser) -> None:
        self.parse(["User-agent: *", "Allow: /", "Crawl-delay: 12"])

    monkeypatch.setattr(RobotFileParser, "read", fake_read)

    result = auto_approve_site("https://crawldelay.test/p/1", config_dir=tmp_path, now=NOW)

    assert result.approved is True
    loaded = load_site_config("crawldelay.test", config_dir=tmp_path)
    assert loaded.rate_limit_seconds == 12.0


def test_robots_no_crawl_delay_uses_safe_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_read(self: RobotFileParser) -> None:
        self.parse(["User-agent: *", "Allow: /"])

    monkeypatch.setattr(RobotFileParser, "read", fake_read)

    result = auto_approve_site("https://nodelay.test/p/1", config_dir=tmp_path, now=NOW)

    assert result.approved is True
    loaded = load_site_config("nodelay.test", config_dir=tmp_path)
    assert loaded.rate_limit_seconds == DEFAULT_RATE_LIMIT_SECONDS


# -- required SiteConfig fields are populated (guards __post_init__ ValueError) -


def test_required_siteconfig_fields_populated(tmp_path: Path) -> None:
    result = auto_approve_site(
        "https://fieldscheck.test/p/1",
        config_dir=tmp_path,
        robots_reader=lambda robots_url, user_agent: True,
        now=NOW,
    )

    loaded = load_site_config(result.domain, config_dir=tmp_path)
    date.fromisoformat(loaded.robots_checked_at)  # raises ValueError if not a valid ISO date
    assert loaded.robots_checked_at == "2026-07-14"
    assert loaded.tos_summary != ""
    assert loaded.tos_summary == AUTO_TOS_SUMMARY
