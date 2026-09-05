"""One root for every path Kern writes to: ``KERN_DATA_DIR``.

Why this exists
---------------
A self-serve client runs ``docker run`` against their own data. Before this
module, pointing Kern at a volume meant setting eight separate environment
variables, one per store - and ``fly.toml`` is the proof of how that goes: it
re-roots eight of them by hand and misses the ninth. ``LINCHPIN_SCHEDULER_PATH``
is not there, so the APScheduler jobstore still defaults onto ``/app/data``, the
ephemeral rootfs that PR #188 fixed for the other five. It does not bite
production today only because the cron endpoint calls the synchronous
``run_once()`` and never ``build_scheduler()``; it bites the first client who
runs the background scheduler. A per-variable fix in ``fly.toml`` would not help
that client at all, because they do not have a ``fly.toml``.

The contract
------------
Three levels, most specific first:

1. the store's own ``LINCHPIN_*`` variable, if set - **production keeps winning**,
   so the eight lines in ``fly.toml`` behave exactly as they do today;
2. otherwise ``KERN_DATA_DIR``, if set - one variable moves all nine at once;
3. otherwise the literal default the store has always had.

With ``KERN_DATA_DIR`` unset, every path resolves precisely where it did before
this module existed. That is the whole safety argument: no migration, no moved
files, nothing to coordinate with a running deploy.

``KERN_DATA_DIR=/data`` reproduces production's layout exactly - there is a test
that reads ``fly.toml`` and asserts it, so the registry cannot drift away from
what the live deploy actually runs.

What is NOT rooted here
-----------------------
Read-only configuration (``config/autonomy.yaml``, ``config/markets``,
``config/sites``, ``config/verticals``, ``config/monitors.yaml``,
``config/retention.yaml``, ``config/event_routing.yaml``) ships inside the image
and is not client state; rooting it at a client's volume would point it at an
empty directory. The nine sample CSVs committed under ``data/`` are in the same
category - they travel in the image, which is also why ``KERN_DATA_DIR``
defaults to ``data/`` rather than replacing it: a client mounts their volume
somewhere else (``-e KERN_DATA_DIR=/data -v kern:/data``) and the samples stay
readable underneath.

On the name
-----------
This is the project's first ``KERN_*`` environment variable; every existing one
is ``LINCHPIN_*`` and stays that way on purpose, as an infrastructure contract.
The exception is deliberate: the three-variable contract promised to self-serve
clients names ``KERN_DATA_DIR``, and a client-facing variable is the one place
where the product's current name should win over the old one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The environment variable that moves every state path at once.
ROOT_ENV = "KERN_DATA_DIR"

#: Where state lives when ``KERN_DATA_DIR`` is unset. Relative to the repo root,
#: which under the Docker image (``WORKDIR /app``) means ``/app/data``.
_DEFAULT_ROOT = _REPO_ROOT / "data"


@dataclass(frozen=True)
class StatePath:
    """One durable store's path, and the three ways it can be resolved.

    ``legacy_default`` is copied VERBATIM from the module it replaces, relative
    string and all. Making these absolute would look tidier and would be a
    behaviour change: ``"data/events.sqlite3"`` is resolved against the working
    directory, and the whole safety argument for this refactor is that an unset
    root changes nothing at all.

    ``env`` is the store's long-standing per-store override and still wins.
    ``under_root`` is where it goes relative to ``KERN_DATA_DIR`` - chosen to
    match what ``fly.toml`` already sets, not invented here. ``module`` is the
    file that owns the path, which is what the "no new hardcoded default" guard
    reads.
    """

    env: str
    under_root: str
    legacy_default: str
    module: str


#: Every path Kern writes to. A new store belongs here; the guard test in
#: tests/test_data_paths.py fails on a module that keeps its own "data/" literal.
STATE_PATHS: dict[str, StatePath] = {
    "events": StatePath(
        env="LINCHPIN_EVENTS_PATH",
        under_root="events.sqlite3",
        legacy_default="data/events.sqlite3",
        module="scm_agent/events.py",
    ),
    "autonomy": StatePath(
        env="LINCHPIN_AUTONOMY_PATH",
        under_root="autonomy.sqlite3",
        legacy_default="data/autonomy.sqlite3",
        module="scm_agent/autonomy.py",
    ),
    "scheduler": StatePath(
        env="LINCHPIN_SCHEDULER_PATH",
        under_root="scheduler.sqlite3",
        legacy_default="data/scheduler.sqlite3",
        module="jobs/scheduler.py",
    ),
    "state": StatePath(
        env="LINCHPIN_STATE_PATH",
        under_root="state",
        legacy_default="data/state",
        module="src/state/store.py",
    ),
    "pricing_ledger": StatePath(
        env="LINCHPIN_PRICING_LEDGER_PATH",
        under_root="pricing_intel/ledger",
        legacy_default="data/pricing_intel/ledger",
        module="src/pricing_intel/ledger.py",
    ),
    "sku_map": StatePath(
        env="LINCHPIN_SKU_MAP_PATH",
        under_root="pricing_intel/sku_map",
        legacy_default="data/pricing_intel/sku_map",
        module="src/pricing_intel/match/sku_map.py",
    ),
    "mcp_keys": StatePath(
        env="LINCHPIN_MCP_KEYS_PATH",
        under_root="mcp_keys.sqlite3",
        legacy_default="data/mcp_keys.sqlite3",
        module="src/mcp_keys.py",
    ),
    # The two webapp stores default OUTSIDE data/ for historical reasons, so
    # their legacy_default is not _DEFAULT_ROOT-relative. Their under_root names
    # match fly.toml (/data/leads_ledger, /data/leads) rather than being tidied,
    # because production's directory names are the contract, not this file's.
    "leads": StatePath(
        env="LINCHPIN_LEADS_DIR",
        under_root="leads_ledger",
        legacy_default=str(_REPO_ROOT / "webapp" / "_leads"),
        module="webapp/app.py",
    ),
    "lead_reports": StatePath(
        env="LINCHPIN_LEAD_REPORTS_DIR",
        under_root="leads",
        legacy_default=str(_REPO_ROOT / "deliverables" / "leads"),
        module="webapp/app.py",
    ),
}


def data_root() -> Path:
    """The directory Kern writes state into.

    ``KERN_DATA_DIR`` when set to something non-blank, else ``<repo>/data``. A
    variable set to whitespace is treated as absent rather than as a directory
    named " ", matching how every ``LINCHPIN_*`` path variable already behaves.
    """
    configured = os.environ.get(ROOT_ENV, "").strip()
    return Path(configured) if configured else _DEFAULT_ROOT


def resolve_state_path(name: str) -> str:
    """Resolve one registered store's path, most specific source first.

    Returns a ``str`` because every call site here assigns it to a module-level
    default that has always been a string, and some of them are compared against
    string literals in tests that predate this module.
    """
    try:
        spec = STATE_PATHS[name]
    except KeyError:  # pragma: no cover - programmer error, not a runtime path
        known = ", ".join(sorted(STATE_PATHS))
        raise KeyError(f"unknown state path {name!r}; registered: {known}") from None

    explicit = os.environ.get(spec.env, "").strip()
    if explicit:
        return explicit

    configured_root = os.environ.get(ROOT_ENV, "").strip()
    if configured_root:
        return str(Path(configured_root) / spec.under_root)

    return spec.legacy_default
