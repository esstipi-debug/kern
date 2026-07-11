"""Tests for examples/run_package.py's --verbose flag: makes the citation
gate's (E5) omitted-citation log genuinely inspectable by an operator running
the CLI, not just via pytest's caplog or a script that imports the module.

Runs main() in a subprocess (like other logging.basicConfig()-touching CLI
tests in this repo) rather than in-process: basicConfig() mutates the ROOT
logger process-wide, which would leak into every other test in the same
pytest session if called directly here.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": "."}
    return subprocess.run(
        [sys.executable, "examples/run_package.py", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def test_verbose_surfaces_citation_gate_omissions(tmp_path):
    result = _run_cli("--package", "diagnostico", "--demo", "--out", str(tmp_path), "--verbose")
    assert result.returncode == 0, result.stderr
    assert "linchpin.citation_gate" in result.stderr


def test_without_verbose_citation_gate_logs_are_silent(tmp_path):
    result = _run_cli("--package", "diagnostico", "--demo", "--out", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "linchpin.citation_gate" not in result.stderr
