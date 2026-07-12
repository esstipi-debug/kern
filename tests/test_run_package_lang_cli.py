"""Tests for examples/run_package.py's --lang resolution: CLI override >
the client's stored profile.lang > the package default ("es")."""
from __future__ import annotations

import pytest

from examples.run_package import _resolve_lang
from src import client_profile


def test_cli_override_wins_over_everything(tmp_path):
    client_profile.upsert_profile("Acme Bilingue", "Acme Bilingue", root=tmp_path, lang="en")
    assert _resolve_lang("Acme Bilingue", "es", root=tmp_path) == "es"


def test_falls_back_to_client_profile_lang(tmp_path):
    client_profile.upsert_profile("Acme Bilingue", "Acme Bilingue", root=tmp_path, lang="en")
    assert _resolve_lang("Acme Bilingue", None, root=tmp_path) == "en"


def test_defaults_to_spanish_when_no_profile_and_no_cli(tmp_path):
    assert _resolve_lang("Nobody On Record", None, root=tmp_path) == "es"


def test_ignores_generic_client_label(tmp_path):
    # "Client" is the generic placeholder label -- must never attempt a real
    # profile lookup (see client_profile.is_generic_client_label).
    assert _resolve_lang("Client", None, root=tmp_path) == "es"


def test_corrupt_profile_fails_loudly_instead_of_silently_defaulting(tmp_path):
    # A corrupt profile.json is a data-integrity problem, not "no profile" --
    # must never silently degrade to the default language (see
    # scm_agent/packages.py::_load_profile for the established pattern this
    # mirrors: unslugifiable label => no profile, corrupt file => raise).
    slug = client_profile.slugify_client_id("Acme Corrupto")
    profile_dir = tmp_path / slug
    profile_dir.mkdir(parents=True)
    (profile_dir / "profile.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt"):
        _resolve_lang("Acme Corrupto", None, root=tmp_path)
