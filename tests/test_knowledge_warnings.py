"""Fail-loud behaviour for the L3 knowledge layer.

A missing or corrupt graph must surface an explicit, actionable warning (and a
log line) instead of silently dropping citations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from scm_agent.knowledge import KnowledgeBase

REPO = Path(__file__).resolve().parent.parent
BOOKS = REPO / "knowledge" / "scm-books" / "graph.json"


def _valid_graph(p: Path) -> Path:
    p.write_text('{"nodes": [{"id": "x", "label": "X"}], "links": []}', encoding="utf-8")
    return p


def test_warnings_empty_when_both_graphs_load(tmp_path: Path) -> None:
    kb = KnowledgeBase(books_path=_valid_graph(tmp_path / "b.json"),
                       code_path=_valid_graph(tmp_path / "c.json"))
    assert kb.warnings() == []


def test_missing_code_graph_surfaces_actionable_warning(tmp_path: Path) -> None:
    kb = KnowledgeBase(books_path=BOOKS, code_path=tmp_path / "absent.json")
    warns = kb.warnings()
    assert any(w.startswith("code") for w in warns)
    assert any("graphify" in w.lower() for w in warns)  # tells the user how to fix it
    assert not any(w.startswith("books") for w in warns)  # books loaded fine


def test_corrupt_graph_flagged_as_unreadable(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    kb = KnowledgeBase(books_path=BOOKS, code_path=bad)
    assert any("unreadable" in w for w in kb.warnings())


def test_missing_code_graph_logs_a_warning(tmp_path: Path, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="linchpin.knowledge"):
        KnowledgeBase(books_path=BOOKS, code_path=tmp_path / "absent.json")
    assert any("code" in r.getMessage() for r in caplog.records)
