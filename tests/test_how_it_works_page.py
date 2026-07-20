"""Tests for webapp/how_it_works_page.py -- the /how-it-works page renderer.
Rendering-helper tests call the (module-private, deliberately imported
directly here) functions without an HTTP client; Task 6 adds HTTP-level
tests through the real FastAPI app, mirroring tests/test_stocky_alternative_page.py."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from webapp.how_it_works_page import _donut_svg  # noqa: E402


def test_donut_svg_has_one_circle_per_segment_and_correct_total() -> None:
    svg = _donut_svg([("A", 2), ("B", 1), ("C", 1)], element_id="test-donut")
    assert svg.count("<circle") == 3
    assert 'id="test-donut"' in svg
    assert ">4<" in svg  # the total, rendered as center text


def test_donut_svg_segment_percentages_are_correct() -> None:
    svg = _donut_svg([("A", 2), ("B", 1), ("C", 1)], element_id="test-donut")
    assert 'data-pct="50"' in svg
    assert svg.count('data-pct="25"') == 2


def test_donut_svg_escapes_labels() -> None:
    svg = _donut_svg([("<script>", 1), ("B", 1)], element_id="xss-donut")
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_donut_svg_rejects_empty_total() -> None:
    import pytest

    with pytest.raises(ValueError):
        _donut_svg([("A", 0), ("B", 0)], element_id="empty-donut")


def test_donut_svg_rejects_negative_count() -> None:
    import pytest

    with pytest.raises(ValueError):
        _donut_svg([("A", -5), ("B", 10)], element_id="negative-donut")
