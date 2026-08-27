"""CI can run without the private nosilo-core package.

Set KERN_CI_NO_CORE=1 to skip test files whose import graph reaches nosilo_core.
The authenticate-core action still hard-fails when it is invoked with empty
inputs; the workflow simply does not invoke it in that case.

``collect_ignore`` is used instead of ``pytest_ignore_collect``: pytest 9 still
imports command-line/collection paths that the ignore-collect hook would skip.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _no_core import depends_on_nosilo_core  # noqa: E402

collect_ignore: list[str] = []
if os.environ.get("KERN_CI_NO_CORE") == "1":
    collect_ignore = [
        path.name
        for path in sorted(_TESTS_DIR.glob("test_*.py"))
        if depends_on_nosilo_core(path)
    ]
