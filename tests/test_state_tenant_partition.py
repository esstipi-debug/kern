"""Tests for per-client partitioning of the system-state store (src/state).

Two properties, and the second one is why this exists:

- A client's snapshots live in their own SQLite index, under their own
  directory. Isolation is PHYSICAL, not a WHERE clause -- a query that forgets
  to filter by tenant cannot be written, because the other tenant's rows are
  in a different file. A ``client_id`` column would put that filter in every
  SELECT in ``store.py`` and make forgetting one a silent cross-client read.

- ``store=None`` is REJECTED instead of silently resolving to a shared store.
  Every call site in the tree already passes ``store=`` explicitly, so the old
  ``store or default_store()`` fallback was never reached on purpose -- the
  only way to reach it was a ``None`` forwarded down from an entry point, and
  what it did then was serve one client's history to another without a word.
  Resolving the shared store is now a visible decision at the edge.

A client id is REJECTED rather than sanitized, which is the opposite of what
``_sanitize`` does for ``cycle_id``. Two reasons, both fatal:

- ``_SAFE_CHARS`` maps anything outside ``[A-Za-z0-9._-]`` to ``_``, so "a/b"
  and "a_b" would sanitize to the SAME directory. Silently merging two clients
  into one partition is precisely the leak this module exists to prevent.
- ``.`` and ``..`` are inside the allowed set, so ``_sanitize("..")`` returns
  ".." untouched and ``base / ".."`` walks straight out of the base path.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.state import store as store_module
from src.state.store import ClientIdError, StateStore, store_for
from src.state.system_state import MissingStoreError, history, latest, snapshot


def _forecast(qty: float) -> pd.DataFrame:
    return pd.DataFrame({"product_id": ["SKU-A"], "period": ["2026-01"], "forecast_qty": [qty]})


# ---- where a partition lands ------------------------------------------------


def test_the_default_partition_is_the_base_path_itself(tmp_path):
    """No subdirectory for the unnamed partition, so an existing deployment's
    ``data/state`` keeps working untouched -- this change needs no migration of
    the live Fly volume, and that is worth the small asymmetry with named
    clients."""
    assert store_for(None, base_path=tmp_path).base_path == tmp_path


def test_a_named_client_gets_its_own_directory(tmp_path):
    assert store_for("acme", base_path=tmp_path).base_path == tmp_path / "acme"


# ---- isolation --------------------------------------------------------------


def test_one_clients_history_is_invisible_to_another(tmp_path):
    """The concrete hazard: ``run_forecast_backtest`` reads ``history("forecast")``
    and recalibrates safety stock from it. Two clients in one process must not
    recalibrate each other."""
    acme = store_for("acme", base_path=tmp_path)
    globex = store_for("globex", base_path=tmp_path)

    snapshot("forecast", _forecast(110.0), "1", store=acme)

    assert [s.cycle_id for s in history("forecast", store=acme)] == ["1"]
    assert history("forecast", store=globex) == []
    assert latest("forecast", store=globex) is None


def test_the_default_partition_does_not_see_a_named_clients_rows(tmp_path):
    """The default partition is the base directory and a named client is a
    subdirectory of it -- so this is the one pairing where a path bug would
    actually leak, and it is worth pinning rather than assuming."""
    shared = store_for(None, base_path=tmp_path)
    acme = store_for("acme", base_path=tmp_path)

    snapshot("forecast", _forecast(110.0), "1", store=acme)

    assert history("forecast", store=shared) == []


def test_two_clients_may_use_the_same_cycle_id(tmp_path):
    """Monotonic ``cycle_id`` is a per-domain invariant WITHIN a client. If it
    were enforced across clients, one client's cycle would reject another's --
    a cross-tenant coupling hiding inside a correctness rule."""
    acme = store_for("acme", base_path=tmp_path)
    globex = store_for("globex", base_path=tmp_path)

    snapshot("forecast", _forecast(110.0), "7", store=acme)
    snapshot("forecast", _forecast(95.0), "7", store=globex)

    assert latest("forecast", store=globex).payload["forecast_qty"].iloc[0] == 95.0


# ---- a client id is rejected, never rewritten -------------------------------


@pytest.mark.parametrize("client_id", ["..", "../..", "../etc", "."])
def test_a_client_id_that_would_escape_the_base_path_is_rejected(client_id, tmp_path):
    """``_sanitize`` leaves "." and ".." untouched (both are inside its allowed
    character class), so sanitizing is not a defense here -- only rejection is."""
    with pytest.raises(ClientIdError):
        store_for(client_id, base_path=tmp_path)


@pytest.mark.parametrize("client_id", ["a/b", "a\\b", "acme/../globex", "C:/data"])
def test_a_client_id_with_a_separator_is_rejected_not_sanitized(client_id, tmp_path):
    """Sanitizing would map "a/b" onto "a_b"'s directory. Two distinct clients
    sharing one partition is the exact leak this whole change prevents, so a
    separator must fail loudly instead of being quietly rewritten."""
    with pytest.raises(ClientIdError):
        store_for(client_id, base_path=tmp_path)


@pytest.mark.parametrize("client_id", ["", "   ", "\t"])
def test_a_blank_client_id_is_rejected(client_id, tmp_path):
    """Blank must not silently mean "the shared partition" -- that would turn a
    dropped identity into a cross-client read, which is the bug being fixed."""
    with pytest.raises(ClientIdError):
        store_for(client_id, base_path=tmp_path)


def test_a_rejected_client_id_writes_nothing(tmp_path):
    """Rejection happens before any directory is created, so a bad id cannot
    litter the base path with partial partitions."""
    with pytest.raises(ClientIdError):
        store_for("a/b", base_path=tmp_path)

    assert list(tmp_path.iterdir()) == []


# ---- the silent fallback is gone --------------------------------------------


def test_snapshot_rejects_a_missing_store(tmp_path):
    with pytest.raises(MissingStoreError):
        snapshot("forecast", _forecast(110.0), "1", store=None)


def test_latest_rejects_a_missing_store():
    with pytest.raises(MissingStoreError):
        latest("forecast", store=None)


def test_history_rejects_a_missing_store():
    with pytest.raises(MissingStoreError):
        history("forecast", store=None)


def test_a_rejected_store_creates_no_directory(tmp_path, monkeypatch):
    """``default_store()`` mkdir's its base path on construction, so the old
    fallback did not merely READ the shared partition on a forgotten store --
    it created one. Rejecting must leave the filesystem untouched."""
    monkeypatch.setattr(store_module, "DEFAULT_BASE_PATH", str(tmp_path / "state"))
    monkeypatch.setattr(store_module, "_default_store", None)

    with pytest.raises(MissingStoreError):
        snapshot("forecast", _forecast(110.0), "1", store=None)

    assert not (tmp_path / "state").exists()


def test_an_explicit_store_still_works(tmp_path):
    """The guard rejects None, not the parameter -- passing a store is and stays
    the normal path."""
    store = StateStore(tmp_path / "state")
    snapshot("forecast", _forecast(110.0), "1", store=store)

    assert latest("forecast", store=store).cycle_id == "1"
