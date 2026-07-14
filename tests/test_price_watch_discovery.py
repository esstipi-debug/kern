"""Tests for jobs/price_watch.py (Discovery-Assisted Price Intel plan, Task 3
/ PR-3): the network entry point wiring auto-onboarding (Task 1) -> the hard
compliance gate -> an advertools crawl -> discovery filtering (Task 2).

Guarantees under test:
- a robots.txt-rejected (or otherwise unapproved) domain is skipped honestly
  BEFORE any crawl attempt -- the crawl adapter is patched to raise if
  called at all, so a wrong gate order would fail loudly, not silently;
- ``require_approved_site`` is a SECOND, authoritative gate independent of
  ``auto_approve_site``'s own verdict -- even a (simulated) stale/wrong
  ``OnboardingResult.approved=True`` cannot smuggle a crawl past a real
  ``prohibited`` config on disk; ``SiteNotApprovedError`` is caught into an
  honest skip, never crawled, never an uncaught exception;
- once both gates clear, crawled pages are reduced to product pages only via
  ``discover.filter_product_pages`` (a non-product page is dropped, not an
  error);
- the crawl adapter's ``custom_settings`` always carry ``ROBOTSTXT_OBEY:
  True`` and a fixed, identifiable, non-rotating User-Agent (NON-GOAL 1
  guardrail) -- asserted directly on the dict passed to the (faked)
  ``advertools.crawl`` call, not just eyeballed from source.

Every test in this file is fully offline: ``auto_approve_site``'s own
network touch is stubbed via its injectable ``robots_reader`` seam (or the
function itself is monkeypatched outright), and the advertools crawl is
either monkeypatched at the ``jobs.price_watch._crawl_domain`` level or, for
the one test that exercises ``_crawl_domain`` itself, via a fake module
injected into ``sys.modules["advertools"]`` -- no test here ever imports a
real ``advertools`` or touches the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from jobs import price_watch as pw

# -- shared fixtures -----------------------------------------------------

_PRODUCT_HTML = """<html><head><title>Widget</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Widget",
 "offers":{"@type":"Offer","price":"9.99","priceCurrency":"USD"}}
</script></head><body><h1>Widget</h1></body></html>"""

_NON_PRODUCT_HTML = "<html><head><title>About us</title></head><body><h1>About us</h1></body></html>"


def _prohibited_config_yaml(domain: str) -> str:
    return (
        f"domain: {domain}\n"
        "robots_txt_respected: true\n"
        "robots_checked_at: '2026-07-01'\n"
        "tos_summary: 'human-reviewed, explicitly prohibited'\n"
        "tos_decision: prohibited\n"
        "rate_limit_seconds: 5.0\n"
        "max_tier_allowed: L0\n"
        "pii_policy: none\n"
    )


# -- gate 1: auto_approve_site rejection -> no crawl ----------------------


def test_prepare_skips_without_crawl_when_robots_disallows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_if_called(*args, **kwargs):
        raise AssertionError("crawl adapter must never be called when robots.txt disallows")

    monkeypatch.setattr(pw, "_crawl_domain", _raise_if_called)

    result = pw.prepare(
        "https://blocked.test/p/1",
        {"config_dir": tmp_path, "robots_reader": lambda robots_url, user_agent: False},
    )

    assert result["domain"] == "blocked.test"
    assert result["discovered"] == []
    assert result["pages_crawled"] == 0
    assert result["skipped_reason"] is not None
    assert "robots_disallow" in result["skipped_reason"]
    assert isinstance(result["onboarding"], pw.OnboardingResult)
    assert result["onboarding"].approved is False


def test_prepare_skips_without_crawl_on_unresolvable_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_if_called(*args, **kwargs):
        raise AssertionError("crawl adapter must never be called for an unresolvable URL")

    monkeypatch.setattr(pw, "_crawl_domain", _raise_if_called)

    result = pw.prepare("MLA123456", {"config_dir": tmp_path})

    assert result["domain"] is None
    assert result["discovered"] == []
    assert result["skipped_reason"] is not None
    assert "invalid_url" in result["skipped_reason"]


# -- gate 2: require_approved_site is a SECOND, authoritative check --------


def test_prepare_gates_on_require_approved_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "prohibited-race.test"
    (tmp_path / f"{domain}.yaml").write_text(_prohibited_config_yaml(domain), encoding="utf-8")

    # Simulate a stale/wrong onboarding verdict claiming approval (a race,
    # or a bug in auto_approve_site) -- prepare() must still re-check the
    # REAL config on disk and refuse, never trusting onboarding.approved
    # blindly.
    fake_onboarding = pw.OnboardingResult(domain, True, tmp_path / f"{domain}.yaml", "config_already_exists")
    monkeypatch.setattr(pw, "auto_approve_site", lambda *args, **kwargs: fake_onboarding)

    def _raise_if_called(*args, **kwargs):
        raise AssertionError("crawl adapter must never be called when require_approved_site refuses")

    monkeypatch.setattr(pw, "_crawl_domain", _raise_if_called)

    result = pw.prepare(f"https://{domain}/p/1", {"config_dir": tmp_path})

    assert result["domain"] == domain
    assert result["discovered"] == []
    assert result["site_config"] is None
    assert result["skipped_reason"] is not None
    assert "SiteNotApprovedError" in result["skipped_reason"]


# -- gate cleared -> crawl -> discover.filter_product_pages ----------------


def test_prepare_filters_to_product_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    domain = "shop.example.test"
    seed_url = f"https://{domain}/"

    df = pd.DataFrame([
        {"url": f"https://{domain}/p/1", "status": 200, "title": "Widget", "page_html": _PRODUCT_HTML},
        {"url": f"https://{domain}/about", "status": 200, "title": "About us", "page_html": _NON_PRODUCT_HTML},
    ])
    captured_crawl_kwargs: dict = {}

    def _fake_crawl_domain(seed, **kwargs):
        captured_crawl_kwargs.update(kwargs)
        return df

    monkeypatch.setattr(pw, "_crawl_domain", _fake_crawl_domain)

    result = pw.prepare(
        seed_url,
        {"config_dir": tmp_path, "robots_reader": lambda robots_url, user_agent: True},
    )

    assert result["skipped_reason"] is None
    assert result["domain"] == domain
    assert result["pages_crawled"] == 2
    assert len(result["discovered"]) == 1
    assert result["discovered"][0].url == f"https://{domain}/p/1"
    assert result["discovered"][0].site == domain
    assert result["site_config"] is not None
    assert result["site_config"].is_approved is True
    # the crawl adapter was invoked with the hard-gated hostname, not some
    # other value.
    assert captured_crawl_kwargs["hostname"] == domain


def test_prepare_returns_empty_discovered_when_no_page_has_structured_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain = "no-products.example.test"
    seed_url = f"https://{domain}/"
    df = pd.DataFrame([{"url": seed_url, "status": 200, "title": "Home", "page_html": _NON_PRODUCT_HTML}])

    monkeypatch.setattr(pw, "_crawl_domain", lambda seed, **kwargs: df)

    result = pw.prepare(
        seed_url,
        {"config_dir": tmp_path, "robots_reader": lambda robots_url, user_agent: True},
    )

    assert result["skipped_reason"] is None  # not an error -- an honest "found nothing" (discover.py's own contract)
    assert result["pages_crawled"] == 1
    assert result["discovered"] == []


# -- crawl adapter: ROBOTSTXT_OBEY + identifiable UA (NON-GOAL 1 guardrail) --


def test_crawl_adapter_uses_robotstxt_obey_and_identifiable_ua(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    class _FakeAdvertools:
        @staticmethod
        def crawl(url_list, output_file_path, **kwargs):
            captured["url_list"] = url_list
            captured.update(kwargs)
            # deliberately writes nothing -- _crawl_domain must degrade to an
            # empty DataFrame when the output file was never created.

    monkeypatch.setitem(sys.modules, "advertools", _FakeAdvertools())

    df = pw._crawl_domain(
        "https://shop.example.test/",
        hostname="shop.example.test",
        output_file=tmp_path / "out.jl",
        follow_links=True,
        user_agent=pw.DEFAULT_USER_AGENT,
        download_delay=1.0,
        concurrent_requests_per_domain=2,
        scrapy_log_level="ERROR",
    )

    assert df.empty
    settings = captured["custom_settings"]
    assert settings["ROBOTSTXT_OBEY"] is True
    assert settings["USER_AGENT"] == pw.DEFAULT_USER_AGENT
    # identifiable, non-rotating -- never a spoofed browser UA.
    assert "Mozilla" not in pw.DEFAULT_USER_AGENT
    assert pw.DEFAULT_USER_AGENT  # non-empty
    assert captured["xpath_selectors"] == {"page_html": "/html"}


def test_prepare_floors_download_delay_at_default_even_when_site_rate_limit_is_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain's own approved ``rate_limit_seconds`` (e.g. a real
    ``Crawl-delay: 0`` robots.txt, self-onboarded by ``auto_approve_site``)
    must NEVER push the crawl's actual ``DOWNLOAD_DELAY`` below
    ``DEFAULT_DOWNLOAD_DELAY_SECONDS`` -- this crawl targets a THIRD-PARTY
    site under the no-evasion non-goal; it must never be tuned up to run "as
    fast as possible" just because the target site's own declared rate
    happens to allow it. Asserted directly on the ``custom_settings`` dict
    passed to the (faked) ``advertools.crawl`` call, same pattern as
    ``test_crawl_adapter_uses_robotstxt_obey_and_identifiable_ua``.
    """
    domain = "zero-delay.example.test"
    seed_url = f"https://{domain}/"
    # A pre-existing, already-approved config with rate_limit_seconds=0.0 --
    # simulates a site whose own robots.txt declared `Crawl-delay: 0`.
    # auto_approve_site() is non-destructive against an existing config, so
    # this is read back verbatim by gate 1 AND gate 2 -- no robots_reader
    # stub needed.
    (tmp_path / f"{domain}.yaml").write_text(
        f"domain: {domain}\n"
        "robots_txt_respected: true\n"
        "robots_checked_at: '2026-07-01'\n"
        "tos_summary: 'auto-approved via robots.txt'\n"
        "tos_decision: limited\n"
        "rate_limit_seconds: 0.0\n"
        "max_tier_allowed: L1\n"
        "pii_policy: none\n",
        encoding="utf-8",
    )

    captured: dict = {}

    class _FakeAdvertools:
        @staticmethod
        def crawl(url_list, output_file_path, **kwargs):
            captured.update(kwargs)
            # deliberately writes nothing -- _crawl_domain degrades to an
            # empty DataFrame, irrelevant to this test.

    monkeypatch.setitem(sys.modules, "advertools", _FakeAdvertools())

    result = pw.prepare(seed_url, {"config_dir": tmp_path})

    assert result["skipped_reason"] is None
    assert result["site_config"] is not None
    assert result["site_config"].rate_limit_seconds == 0.0
    settings = captured["custom_settings"]
    assert settings["DOWNLOAD_DELAY"] == pw.DEFAULT_DOWNLOAD_DELAY_SECONDS
    assert settings["DOWNLOAD_DELAY"] >= pw.DEFAULT_DOWNLOAD_DELAY_SECONDS


def test_crawl_domain_raises_a_clear_error_when_advertools_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "advertools", None)
    with pytest.raises(pw.AdvertoolsUnavailableError):
        pw._crawl_domain(
            "https://shop.example.test/", hostname="shop.example.test", output_file=tmp_path / "out.jl",
            follow_links=True, user_agent="test", download_delay=0.1,
            concurrent_requests_per_domain=1, scrapy_log_level="ERROR",
        )
