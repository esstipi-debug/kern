"""Acquisition tier for the pricing titan (Linchpin 3.0 plan section 6.1).

Only ``structured.py`` (L1 -- JSON-LD/microdata/OpenGraph) exists as of
PR-11. The ``Fetcher`` protocol (``base.py``) and the other per-tier
fetchers (``meli_api.py``, ``amazon_api.py``, ``shopify_api.py``,
``watcher.py``, ``spiders/``, ``browser.py``) are later PRs -- see the plan's
file tree. Nothing in this package other than a *future* fetcher performs
network I/O; ``structured.py`` is a pure function over an already-fetched
HTML string.
"""

from __future__ import annotations
