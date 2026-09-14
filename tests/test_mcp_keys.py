"""Tests for the per-client API key store backing the read-only MCP server.

Guarantees under test:
- issue() returns a high-entropy plaintext key exactly once; nothing else ever
  sees or stores that plaintext (only its hash lands in the DB);
- validate() maps a live key back to its client_name, rejects anything else,
  and records last-used-at for operator visibility;
- revoke()/revoke_client() take a key out of service without deleting its
  audit trail (list_keys() still shows it, just inactive);
- keys survive a simulated process restart (same guarantee writeback's ledger has).
"""

from __future__ import annotations

from src.mcp_keys import KEY_PREFIX, McpKeyStore


def _mem_store() -> McpKeyStore:
    return McpKeyStore(":memory:")


def test_issued_key_has_the_expected_prefix_and_is_high_entropy():
    store = _mem_store()

    key_a = store.issue("Acme Co")
    key_b = store.issue("Acme Co")

    assert key_a.startswith(KEY_PREFIX)
    assert key_b.startswith(KEY_PREFIX)
    assert key_a != key_b  # two issuances for the same client are still distinct secrets
    assert len(key_a) >= 32  # not a short/guessable token


def test_validate_a_freshly_issued_key_returns_its_client_name():
    store = _mem_store()
    key = store.issue("Acme Co")

    assert store.validate(key) == "Acme Co"


def test_validate_rejects_unknown_garbage_and_empty_keys():
    store = _mem_store()
    store.issue("Acme Co")

    assert store.validate("not-a-real-key") is None
    assert store.validate("") is None
    assert store.validate(KEY_PREFIX + "0" * 40) is None  # right shape, wrong secret


def test_plaintext_key_is_never_persisted_only_its_hash():
    """The whole point of the store: a DB dump must not reveal usable keys."""
    store = McpKeyStore(":memory:")
    key = store.issue("Acme Co")

    rows = store._conn.execute("SELECT key_hash FROM keys").fetchall()
    assert len(rows) == 1
    stored_hash = rows[0][0]
    assert stored_hash != key
    assert key not in stored_hash


def test_revoke_takes_a_key_out_of_service_without_deleting_its_history():
    store = _mem_store()
    key = store.issue("Acme Co")

    revoked = store.revoke(key)

    assert revoked is True
    assert store.validate(key) is None
    # still visible to the operator, just inactive - not silently erased.
    listed = store.list_keys()
    assert len(listed) == 1
    assert listed[0]["client_name"] == "Acme Co"
    assert listed[0]["active"] is False


def test_revoke_of_an_unknown_key_reports_nothing_to_revoke():
    store = _mem_store()

    assert store.revoke("lpk_totally-unknown") is False


def test_revoke_client_disables_every_key_for_that_client_only():
    store = _mem_store()
    key1 = store.issue("Acme Co")
    key2 = store.issue("Acme Co")
    other_key = store.issue("Globex")

    count = store.revoke_client("Acme Co")

    assert count == 2
    assert store.validate(key1) is None
    assert store.validate(key2) is None
    assert store.validate(other_key) == "Globex"  # untouched


def test_validate_updates_last_used_at_for_operator_visibility():
    store = _mem_store()
    key = store.issue("Acme Co")
    before = store.list_keys()[0]
    assert before["last_used_at"] is None

    store.validate(key, now=1000.0)

    after = store.list_keys()[0]
    assert after["last_used_at"] == 1000.0


def test_list_keys_never_exposes_the_plaintext_or_the_hash():
    store = _mem_store()
    key = store.issue("Acme Co")

    [entry] = store.list_keys()

    assert key not in repr(entry)
    assert "key_hash" not in entry
    assert "key" not in entry
    # Conjunto EXACTO, no un subconjunto: el punto de este test es que agregar un
    # campo a esta salida sea una decision consciente y no un descuido. `email` y
    # `payment_ref` se sumaron al cablear el webhook de pago (brecha L3) porque el
    # operador necesita saber A QUIEN entregarle la credencial y por que compra se
    # emitio. `email` es PII y por eso importa donde vive: list_keys() lo lee solo
    # examples/issue_mcp_key.py, una CLI de operador -- ninguna ruta de webapp lo
    # devuelve, verificado al agregarlo.
    assert set(entry) == {
        "client_name", "issued_at", "active", "last_used_at", "email", "payment_ref",
    }


def test_list_keys_no_filtra_la_key_de_una_compra():
    """La emision por pago pasa por el mismo colador: se guarda el hash, nunca el
    plaintext, tambien cuando la key nace de un webhook."""
    store = _mem_store()
    key = store.issue_for_payment("Acme Co", "cs_123", email="compra@cliente.com")

    [entry] = store.list_keys()

    assert key not in repr(entry)
    assert entry["payment_ref"] == "cs_123"
    assert entry["email"] == "compra@cliente.com"


def test_keys_survive_a_simulated_process_restart(tmp_path):
    path = tmp_path / "mcp_keys.sqlite3"
    key = McpKeyStore(path).issue("Acme Co")

    reopened = McpKeyStore(path)

    assert reopened.validate(key) == "Acme Co"


def test_two_stores_on_the_same_file_do_not_lock_each_other_out(tmp_path):
    """Mirrors the writeback ledger's cross-process safety expectation - a real
    deploy has multiple worker processes validating keys against the same file."""
    path = tmp_path / "mcp_keys.sqlite3"
    writer = McpKeyStore(path)
    key = writer.issue("Acme Co")
    reader = McpKeyStore(path)

    assert reader.validate(key) == "Acme Co"
    assert writer.validate(key) == "Acme Co"


def test_validate_works_from_a_different_thread_than_the_store_was_created_on():
    """Repro of a real failure: Starlette's BaseHTTPMiddleware (which
    webapp/mcp_auth.py uses) can dispatch a request on a different thread than the
    one that constructed the McpKeyStore singleton, and Python's sqlite3 module
    raises ProgrammingError on a cross-thread connection by default."""
    import threading

    store = _mem_store()  # created on the test's (main) thread
    key = store.issue("Acme Co")
    outcome: dict = {}

    def _validate_elsewhere():
        try:
            outcome["client_name"] = store.validate(key)
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            outcome["error"] = exc

    thread = threading.Thread(target=_validate_elsewhere)
    thread.start()
    thread.join()

    assert outcome.get("error") is None, f"validate() raised on another thread: {outcome.get('error')}"
    assert outcome["client_name"] == "Acme Co"
