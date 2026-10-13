"""Tests for the per-client API key store backing the read-only MCP server.

Guarantees under test:
- issue() returns a high-entropy plaintext key exactly once; nothing else ever
  sees or stores that plaintext (only its hash lands in the DB);
- validate() maps a live key back to its client_name, rejects anything else,
  and records last-used-at for operator visibility;
- revoke()/revoke_client() take a key out of service without deleting its
  audit trail (list_keys() still shows it, just inactive);
- keys survive a simulated process restart (same guarantee writeback's ledger has);
- a key can expire and can carry a quota, and both are OFF by default: a key
  issued without them behaves exactly as before this existed;
- an exhausted quota is refused *without* consuming quota, so a client hammering
  a dead key cannot advance their own window.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.mcp_keys import (
    KEY_PREFIX,
    REASON_EXPIRED,
    REASON_OK,
    REASON_QUOTA_EXCEEDED,
    REASON_REVOKED,
    REASON_UNKNOWN,
    McpKeyStore,
)


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
    # `expires_at`, `quota_requests`, `quota_window_seconds` y `requests_used` se
    # sumaron el 2026-10-13 junto con la cuota y el vencimiento: sin ellos el
    # operador no puede contestar "me queda cuota?", que es la pregunta que su
    # cliente le va a hacer. Misma regla: superficie de operador, no de cliente.
    assert set(entry) == {
        "client_name", "issued_at", "active", "last_used_at", "email", "payment_ref",
        "expires_at", "quota_requests", "quota_window_seconds", "requests_used",
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


# --------------------------------------------------------------------------- #
# Vencimiento y cuota (2026-10-13)
#
# Una key paga podia golpear la API para siempre y cosechar salidas y citas - es
# decir, el mapeo concepto->fuente del grafo. Ahora puede nacer con vencimiento y
# con cuota. Las dos son opcionales: sin flags, nada de esto cambia una key.
# --------------------------------------------------------------------------- #


def test_a_key_issued_without_limits_behaves_exactly_as_before():
    store = _mem_store()
    key = store.issue("Acme Co")

    assert store.validate(key) == "Acme Co"
    [entry] = store.list_keys()
    assert entry["expires_at"] is None
    assert entry["quota_requests"] is None
    assert entry["quota_window_seconds"] is None


def test_an_expired_key_is_refused_and_says_so():
    store = _mem_store()
    key = store.issue("Acme Co", now=0.0, expires_at=100.0)

    assert store.validate(key, now=99.0) == "Acme Co"  # todavia sirve
    resultado = store.check(key, now=100.0)  # el instante exacto ya no
    assert resultado.allowed is False
    assert resultado.reason == REASON_EXPIRED
    assert resultado.client_name == "Acme Co"  # se sabe de quien era
    assert store.validate(key, now=200.0) is None


def test_an_expired_key_still_shows_up_for_the_operator():
    """Vencida no es borrada: el operador tiene que poder ver que existio."""
    store = _mem_store()
    key = store.issue("Acme Co", now=0.0, expires_at=100.0)
    store.check(key, now=200.0)

    [entry] = store.list_keys()
    assert entry["client_name"] == "Acme Co"
    assert entry["active"] is True  # vencida != revocada
    assert entry["expires_at"] == 100.0


def test_issue_refuses_a_key_that_would_be_born_expired():
    store = _mem_store()
    with pytest.raises(ValueError, match="nacer vencida"):
        store.issue("Acme Co", now=1000.0, expires_at=999.0)


def test_quota_refuses_the_request_after_the_cap():
    store = _mem_store()
    key = store.issue("Acme Co", quota_requests=2)

    assert store.check(key).reason == REASON_OK
    assert store.check(key).reason == REASON_OK
    tercera = store.check(key)
    assert tercera.allowed is False
    assert tercera.reason == REASON_QUOTA_EXCEEDED
    assert tercera.quota_requests == 2
    assert tercera.remaining == 0
    assert store.validate(key) is None


def test_a_refused_request_does_not_consume_quota():
    """El modo de falla que este test cierra: contar los rechazos dejaria que un
    cliente que golpea una cuota agotada empujara su propia ventana."""
    store = _mem_store()
    key = store.issue("Acme Co", quota_requests=2)
    store.check(key)
    store.check(key)

    for _ in range(5):
        store.check(key)

    [entry] = store.list_keys()
    assert entry["requests_used"] == 2  # no 7


def test_quota_remaining_counts_down():
    store = _mem_store()
    key = store.issue("Acme Co", quota_requests=3)

    assert store.check(key).remaining == 2
    assert store.check(key).remaining == 1
    assert store.check(key).remaining == 0


def test_a_windowed_quota_renews_after_the_window_elapses():
    store = _mem_store()
    key = store.issue("Acme Co", quota_requests=1, quota_window_seconds=100.0)

    assert store.check(key, now=0.0).allowed is True
    assert store.check(key, now=50.0).allowed is False  # dentro de la ventana
    assert store.check(key, now=150.0).allowed is True  # la ventana roto
    assert store.check(key, now=150.0).allowed is False  # y se volvio a llenar


def test_a_lifetime_quota_never_renews():
    store = _mem_store()
    key = store.issue("Acme Co", quota_requests=1)

    assert store.check(key, now=0.0).allowed is True
    assert store.check(key, now=10_000_000.0).allowed is False


def test_an_unlimited_key_still_counts_usage_for_the_operator():
    """Sin cuota no hay limite, pero el uso se mide igual: es la unica forma de
    saber si un cliente usa lo que pago."""
    store = _mem_store()
    key = store.issue("Acme Co")

    for _ in range(3):
        store.check(key)

    [entry] = store.list_keys()
    assert entry["requests_used"] == 3
    assert entry["quota_requests"] is None


def test_a_revoked_key_reports_revoked_not_unknown():
    """El middleware los trata igual (401), pero el operador necesita la diferencia:
    'revocada' es una accion suya, 'desconocida' es alguien probando keys."""
    store = _mem_store()
    key = store.issue("Acme Co")
    store.revoke(key)

    assert store.check(key).reason == REASON_REVOKED
    assert store.check("lpk_nunca-existio").reason == REASON_UNKNOWN
    assert store.check("").reason == REASON_UNKNOWN


def test_issue_validates_the_quota_arguments():
    store = _mem_store()
    with pytest.raises(ValueError, match=">= 1"):
        store.issue("Acme Co", quota_requests=0)
    with pytest.raises(ValueError, match="sin quota_requests"):
        store.issue("Acme Co", quota_window_seconds=60.0)
    with pytest.raises(ValueError, match="> 0"):
        store.issue("Acme Co", quota_requests=5, quota_window_seconds=0.0)


def test_migration_is_additive_on_a_database_created_before_this_existed(tmp_path):
    """Una base ya emitida tiene que seguir funcionando, con sus keys ilimitadas.

    Se crea a mano el esquema ANTERIOR (sin las columnas de cuota/vencimiento), se
    inserta una key, y se abre con el store nuevo. Es el caso real: el volumen de
    Fly ya tiene ese archivo.
    """
    import hashlib

    path = tmp_path / "mcp_keys.sqlite3"
    key = KEY_PREFIX + "clave-emitida-antes-del-cambio"
    viejo = sqlite3.connect(str(path))
    viejo.execute(
        "CREATE TABLE keys ("
        " key_hash TEXT PRIMARY KEY, client_name TEXT NOT NULL, issued_at REAL NOT NULL,"
        " active INTEGER NOT NULL DEFAULT 1, last_used_at REAL, email TEXT, payment_ref TEXT)"
    )
    viejo.execute(
        "INSERT INTO keys (key_hash, client_name, issued_at, active, last_used_at)"
        " VALUES (?, ?, ?, 1, NULL)",
        (hashlib.sha256(key.encode()).hexdigest(), "Cliente Viejo", 1.0),
    )
    viejo.commit()
    viejo.close()

    store = McpKeyStore(path)

    assert store.validate(key) == "Cliente Viejo"  # sigue sirviendo
    [entry] = store.list_keys()
    assert entry["expires_at"] is None  # sin vencimiento
    assert entry["quota_requests"] is None  # sin cuota
    assert entry["requests_used"] == 1  # y ya se cuenta el uso
