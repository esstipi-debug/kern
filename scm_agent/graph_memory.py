"""Graph query learning loop — records query outcomes as graph nodes.

When a structural query proves useful (or wrong, or corrected), this module
writes a ``query_outcome`` node into the books graph, linked to every node
that was cited. The next session's graph load automatically includes these
learning nodes, so the agent's retrieval improves over time without any
manual curation.

Also writes the traditional markdown file under ``knowledge/graph-memory/``
for backwards compatibility with existing ``graphify reflect`` workflows.

Invariants:
  - query_outcome nodes are self-contained (no external side effects).
  - The graph file is always valid JSON after a write (atomic write via temp).
  - Duplicate prevention: same question + outcome within 5 minutes is a no-op.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_LOG = logging.getLogger("kern.graph_memory")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BOOKS_GRAPH = _REPO_ROOT / "knowledge" / "scm-books" / "graph.json"
_MEMORY_DIR = _REPO_ROOT / "knowledge" / "graph-memory"


def _slug(text: str, max_len: int = 60) -> str:
    """URL-safe slug from freeform text."""
    import re as _re
    s = _re.sub(r"[^a-z0-9]+", "_", text.lower().strip())[:max_len]
    return s.strip("_")


def save_query_outcome(
    question: str,
    answer: str,
    source_nodes: list[str],
    *,
    outcome: str = "useful",
    graph_path: str | Path | None = None,
    memory_dir: str | Path | None = None,
) -> dict | None:
    """Record a query outcome as a graph node + markdown memory file.

    Parameters
    ----------
    question : the original query.
    answer : short plain-language answer (1-3 sentences).
    source_nodes : list of graph node IDs that were cited in the answer.
    outcome : one of ``"useful"``, ``"wrong"``, ``"corrected"``, ``"dead_end"``.
    graph_path : override books graph path. Defaults to ``knowledge/scm-books/graph.json``.
    memory_dir : override memory dir. Defaults to ``knowledge/graph-memory/``.

    Returns
    -------
    The newly created ``query_outcome`` node dict, or ``None`` if deduplicated.
    """
    graph_path = Path(graph_path) if graph_path else _BOOKS_GRAPH
    memory_dir = Path(memory_dir) if memory_dir else _MEMORY_DIR

    # Load current graph
    if not graph_path.exists():
        _LOG.warning("Books graph not found at %s — skipping memory write", graph_path)
        return None

    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _LOG.error("Could not read books graph: %s", exc)
        return None

    nodes: list[dict] = graph.get("nodes", [])
    links: list[dict] = graph.get("links", [])

    # Dedup: skip if same question + outcome exists within 5 minutes
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    for existing in nodes:
        if existing.get("node_type") != "query_outcome":
            continue
        if existing.get("question", "").strip().lower() != question.strip().lower():
            continue
        if existing.get("outcome") != outcome:
            continue
        existing_ts = existing.get("captured_at", "")
        if existing_ts:
            try:
                existing_dt = datetime.fromisoformat(existing_ts)
                if (now - existing_dt).total_seconds() < 300:
                    _LOG.debug("Duplicate query outcome suppressed (within 5 min)")
                    return None
            except ValueError:
                pass

    # Create the query_outcome node
    node_id = f"query_outcome::{_slug(question)}"
    node = {
        "id": node_id,
        "label": f"Q: {question[:80]}",
        "node_type": "query_outcome",
        "question": question,
        "answer": answer,
        "outcome": outcome,
        "source_nodes": source_nodes,
        "captured_at": now_iso,
        "repo": "knowledge",
    }
    nodes.append(node)

    # Create edges from query_outcome to each cited node
    new_edges = []
    for src_id in source_nodes:
        # Verify the cited node actually exists in the graph
        if not any(n["id"] == src_id for n in nodes if n["id"] != node_id):
            continue
        edge = {
            "source": node_id,
            "target": src_id,
            "relation": "cited_in",
            "confidence": "INFERRED",
        }
        new_edges.append(edge)
    links.extend(new_edges)

    # Atomic write: write to temp file, then rename
    graph["nodes"] = nodes
    graph["links"] = links
    try:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            dir=str(graph_path.parent),
            prefix=".graph_memory_",
        )
        with fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2, ensure_ascii=False)
        Path(tmp_path).replace(graph_path)
        _LOG.info(
            "Recorded query outcome (%s) with %d source edges -> %s",
            outcome, len(new_edges), graph_path,
        )
    except OSError as exc:
        _LOG.error("Failed to write query outcome to graph: %s", exc)
        return None

    # Also write the markdown file for backwards compatibility
    _write_markdown(question, answer, source_nodes, outcome, now_iso, memory_dir)

    return node


def _write_markdown(
    question: str,
    answer: str,
    source_nodes: list[str],
    outcome: str,
    timestamp: str,
    memory_dir: Path,
) -> None:
    """Write the traditional graph-memory markdown file."""
    memory_dir.mkdir(parents=True, exist_ok=True)

    slug = _slug(question)
    ts = timestamp.replace(":", "").replace("-", "")[:15].replace("T", "_")
    filename = f"query_{ts}_{slug}.md"
    filepath = memory_dir / filename

    source_list = "\n".join(f"- {n}" for n in source_nodes)
    content = f"""---
type: "query"
date: "{timestamp}"
question: "{question}"
contributor: "kern"
outcome: "{outcome}"
source_nodes: {json.dumps(source_nodes)}
---

# Q: {question}

## Answer

{answer}

## Outcome

- Signal: {outcome}

## Source Nodes

{source_list}
"""
    try:
        filepath.write_text(content, encoding="utf-8")
        _LOG.debug("Wrote memory markdown: %s", filepath)
    except OSError as exc:
        _LOG.warning("Could not write memory markdown %s: %s", filepath, exc)


def fdopen(fd: int, mode: str, encoding: str = "utf-8"):
    """Open a file descriptor as a file object."""
    import os
    return os.fdopen(fd, mode, encoding=encoding)


def load_outcomes(
    graph_path: str | Path | None = None,
    *,
    outcome_filter: str | None = None,
) -> list[dict]:
    """Load all query_outcome nodes from the books graph.

    Parameters
    ----------
    graph_path : override books graph path.
    outcome_filter : if given, only return nodes matching this outcome type.

    Returns
    -------
    List of query_outcome node dicts, most recent first.
    """
    graph_path = Path(graph_path) if graph_path else _BOOKS_GRAPH
    if not graph_path.exists():
        return []

    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    outcomes = [
        n for n in graph.get("nodes", [])
        if n.get("node_type") == "query_outcome"
        and (outcome_filter is None or n.get("outcome") == outcome_filter)
    ]
    # Sort by captured_at descending (most recent first)
    outcomes.sort(key=lambda n: n.get("captured_at", ""), reverse=True)
    return outcomes


def load_reminders(
    graph_path: str | Path | None = None,
) -> list[dict]:
    """Load all reminder nodes from the books graph.

    Returns list of reminder node dicts, most recent first.
    """
    graph_path = Path(graph_path) if graph_path else _BOOKS_GRAPH
    if not graph_path.exists():
        return []

    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    reminders = [
        n for n in graph.get("nodes", [])
        if n.get("node_type") == "reminder"
    ]
    reminders.sort(key=lambda n: n.get("captured_at", ""), reverse=True)
    return reminders


def save_reminder(
    text: str,
    *,
    tags: list[str] | None = None,
    graph_path: str | Path | None = None,
) -> dict | None:
    """Write a reminder node into the books graph.

    Lightweight bookmark the agent will see on next session load.
    No dedup — the same reminder can be saved multiple times
    (e.g. "evaluar modelo X" cada semana until it's done).

    Parameters
    ----------
    text : the reminder note (plain language).
    tags : optional labels like ["model_upgrade", "eval", "fable5"].
    graph_path : override books graph path.

    Returns
    -------
    The newly created reminder node dict, or None on write failure.
    """
    graph_path = Path(graph_path) if graph_path else _BOOKS_GRAPH

    if not graph_path.exists():
        _LOG.warning("Books graph not found at %s — skipping reminder", graph_path)
        return None

    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _LOG.error("Could not read books graph: %s", exc)
        return None

    now = datetime.now(timezone.utc).isoformat()
    slug = _slug(text)[:40]
    node_id = f"reminder::{slug}_{now[:10]}"

    node = {
        "id": node_id,
        "label": f"Reminder: {text[:80]}",
        "node_type": "reminder",
        "text": text,
        "tags": tags or [],
        "captured_at": now,
        "repo": "knowledge",
    }

    graph.setdefault("nodes", []).append(node)

    # Atomic write
    try:
        fd, tmp_path = tempfile.mkstemp(
            suffix=".json",
            dir=str(graph_path.parent),
            prefix=".graph_reminder_",
        )
        with fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(graph, f, indent=2, ensure_ascii=False)
        Path(tmp_path).replace(graph_path)
        _LOG.info("Saved reminder -> %s", node_id)
        return node
    except OSError as exc:
        _LOG.error("Failed to write reminder to graph: %s", exc)
        return None


def query_history(
    question: str,
    graph_path: str | Path | None = None,
) -> list[dict]:
    """Find past outcomes for a similar question.

    Simple token-overlap matching — good enough for dedup and context.
    Returns matching query_outcome nodes sorted by recency.
    """
    import re as _re

    graph_path = Path(graph_path) if graph_path else _BOOKS_GRAPH
    if not graph_path.exists():
        return []

    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    q_tokens = set(_re.findall(r"[a-z0-9]{3,}", question.lower()))
    if not q_tokens:
        return []

    matches: list[tuple[float, dict]] = []
    for node in graph.get("nodes", []):
        if node.get("node_type") != "query_outcome":
            continue
        past_q = node.get("question", "")
        past_tokens = set(_re.findall(r"[a-z0-9]{3,}", past_q.lower()))
        if not past_tokens:
            continue
        overlap = len(q_tokens & past_tokens) / max(len(q_tokens | past_tokens), 1)
        if overlap > 0.3:
            matches.append((overlap, node))

    matches.sort(key=lambda x: (-x[0], x[1].get("captured_at", "")))
    return [m[1] for m in matches]
