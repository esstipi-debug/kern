"""Acquisition tier for the pricing titan (Linchpin 3.0 plan section 6.1).

As of PR-15: ``structured.py`` (L1 -- JSON-LD/microdata/OpenGraph, pure over
an already-fetched HTML string), ``pdp_fetcher.py`` (PR-13's one-shot L1 GET),
``base.py`` (the ``Fetcher`` protocol, the per-domain compliance gate, and the
circuit breaker), ``meli_api.py`` (L0 -- MercadoLibre's public Items API), and
``watcher.py`` (L2 -- a changedetection.io webhook adapter, no network I/O of
its own; it only parses a POST body someone else's server sent). The
remaining per-tier fetchers named in the plan's file tree --
``amazon_api.py``, ``shopify_api.py``, ``spiders/``, ``browser.py`` -- are
NOT built (Amazon/Shopify are ``[CRED]``-gated and de-prioritized per the
plan; L3 spiders were deliberately out of Fase B's sequence, see
``jobs/price_monitor.py``'s module docstring).
"""

from __future__ import annotations
