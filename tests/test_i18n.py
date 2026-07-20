"""Tests for src/i18n.py: dict completeness + the label()/tool_title() lookup
helpers' fallback behavior (a missing/unknown key must degrade, never crash
a deck)."""
from __future__ import annotations

import re

import pytest

from src.i18n import (
    DEFAULT_LANG,
    LABELS,
    SUPPORTED_LANGS,
    TOOL_TITLES,
    label,
    tool_title,
)


def test_default_lang_is_supported():
    assert DEFAULT_LANG in SUPPORTED_LANGS


@pytest.mark.parametrize("key", list(LABELS))
def test_every_label_has_both_languages(key):
    assert set(LABELS[key]) == set(SUPPORTED_LANGS), key


@pytest.mark.parametrize("key", list(TOOL_TITLES))
def test_every_tool_title_has_both_languages(key):
    assert set(TOOL_TITLES[key]) == set(SUPPORTED_LANGS), key


def test_all_registered_tool_keys_are_covered():
    # Cross-check against the actual registry so a newly registered tool
    # without an i18n entry is caught here, not discovered in a client deck.
    tools_src = (__import__("pathlib").Path(__file__).resolve().parents[1]
                 / "scm_agent" / "tools.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'key="([a-z_]+)"', tools_src))
    assert registered == set(TOOL_TITLES)


def test_label_formats_kwargs():
    text = label("executed_of_scope", "es", executed=3, total=5)
    assert text == "Se ejecutaron 3 de 5 analisis del alcance"


def test_label_falls_back_to_spanish_for_unknown_language():
    assert label("cadence_word", "fr") == label("cadence_word", "es")


def test_label_falls_back_to_the_key_itself_for_unknown_key():
    assert label("this_key_does_not_exist", "es") == "this_key_does_not_exist"


def test_tool_title_known_key_both_languages():
    assert tool_title("data_quality", "en") == "Data Quality & SKU Master (MDM)"
    assert tool_title("data_quality", "es") == "Calidad de Datos y Maestro de SKUs (MDM)"


def test_tool_title_unknown_key_falls_back_to_given_fallback():
    assert tool_title("not_a_real_tool", "es", fallback="Original Title") == "Original Title"


def test_tool_title_unknown_key_without_fallback_uses_the_key():
    assert tool_title("not_a_real_tool", "es") == "not_a_real_tool"


def test_no_translation_is_identical_to_its_english_source_by_accident():
    # A sanity net: catches copy-paste entries where the "es" value was left
    # as the English string (an easy mistake across 38 hand-written rows).
    identical = [k for k, v in TOOL_TITLES.items() if v["es"] == v["en"]]
    assert identical == []
