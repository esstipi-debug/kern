"""A package sold in Spanish must not hand the client English tool decks.

``spec.lang`` reached only the consolidated package deck. Every per-tool deck under the
same folder kept rendering in English - including the "not measured" token PR #193 routed
through ``src/i18n.py`` specifically so a Spanish deck could say "no medido".

Not every ``build_deck`` can take a language yet, so the runner asks each one whether it
accepts ``lang`` rather than assuming. That is deliberately introspection and not a flag
on the Tool: a flag has to be remembered when a tool *gains* language support, and the
failure mode of forgetting is a deck that silently stays English - the exact class of
silent-default bug this thread has been closing.
"""

from pathlib import Path

import pytest

from scm_agent import packages
from scm_agent.package_specs import PACKAGES
from scm_agent.tools import build_default_registry

# The REGISTERED tools whose deck accepts a language today. An inventory, not a contract:
# when a tool gains `lang`, this test tells you it is now being wired through.
# `jobs/integrated_plan.py` and `jobs/price_intelligence.py` also have a lang-aware
# build_deck, but neither is wired to a registered Tool.deck (both are CLI-only), so a
# package run never reaches them.
_EXPECTED_LANG_AWARE = {"sourcing", "landed_cost", "financial_kpis"}


def test_the_runner_can_tell_which_decks_accept_a_language():
    accepts = packages.deck_accepts_lang

    assert accepts(lambda report, out_dir, client, citations, confidence, options: {}) is False
    assert accepts(
        lambda report, out_dir, client, citations, confidence, options, lang="en": {}
    ) is True


def test_a_tool_without_a_deck_is_not_asked_about_language():
    assert packages.deck_accepts_lang(None) is False


def test_the_lang_aware_decks_are_the_ones_we_think_they_are():
    wired = {
        tool.key for tool in build_default_registry().list()
        if packages.deck_accepts_lang(tool.deck)
    }

    assert wired == _EXPECTED_LANG_AWARE


def test_every_lang_aware_deck_still_defaults_to_english_on_its_own():
    """A tool run outside a package keeps rendering English - only a package overrides."""
    import inspect

    registry = build_default_registry()
    for key in sorted(_EXPECTED_LANG_AWARE):
        param = inspect.signature(registry.get(key).deck).parameters["lang"]
        assert param.default == "en", key


# ---- end to end -------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_intake(tmp_path_factory) -> Path:
    from examples.run_package import build_demo_intake

    return build_demo_intake(tmp_path_factory.mktemp("intake"))


class _NoKnowledge:
    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


def _run(spec, intake, out):
    from examples.run_package import DEMO_PARAMS

    return packages.run_package(
        spec, intake, out_dir=out, params=dict(DEMO_PARAMS),
        knowledge=_NoKnowledge(), clients_root=None,
    )


def test_a_spanish_package_writes_its_sourcing_deck_in_spanish(demo_intake, tmp_path):
    spec = PACKAGES["proyecto_sourcing"]
    assert spec.lang == "es"

    result = _run(spec, demo_intake, tmp_path)

    deck = next(Path(p) for k, p in result.deliverables.items()
                if k.startswith("sourcing_deck_") and p.endswith(".md"))
    text = deck.read_text(encoding="utf-8")
    assert "Resumen ejecutivo" in text
    assert "Executive summary" not in text


def test_the_same_package_still_writes_decks_for_tools_that_take_no_language(demo_intake, tmp_path):
    """The 35 decks that cannot take `lang` must keep being written, not skipped."""
    spec = PACKAGES["proyecto_sourcing"]

    result = _run(spec, demo_intake, tmp_path)

    assert result.status == "ok", result.summary
    assert any(k.startswith("landed_cost_deck_") for k in result.deliverables)
