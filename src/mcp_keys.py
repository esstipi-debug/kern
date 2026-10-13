"""Per-client API key store for the read-only MCP server (Phase A go-to-market).

Kern's MCP server sells access to individual paying clients, each needing
their own revocable credential - unlike ``webapp/security.py``'s single shared
``LINCHPIN_API_KEY`` (fine for gating one operator's own dashboard, wrong for
multiple distinct customers). Phase A billing is manual (Stripe Payment Link,
then the operator issues a key by hand - see ``examples/issue_mcp_key.py``).

Hasta el 2026-10-13 este store solo hacia issue/validate/revoke, sin medicion de
uso ni vencimiento: una key paga podia golpear la API para siempre y cosechar
salidas y citas (es decir, el mapeo concepto->fuente del grafo). Ahora una key
puede nacer con **vencimiento** y con **cuota**, y el uso se cuenta.

Las dos son opcionales y ilimitadas por omision (NULL): una key emitida antes de
este cambio -o emitida hoy sin flags- se comporta exactamente como antes. La
cuota se aplica a la ventana (``quota_window_seconds``, que rota) o de por vida
si no hay ventana. El limite de *velocidad* sigue siendo otro: vive en
``webapp/security.py::rate_limit``, es por cliente autenticado y es lo que evita
que una key sature la instancia.

Desde el webhook de pago (``webapp/stripe_webhook.py``) cada key emitida por
una compra queda atada al email del comprador y a la referencia del pago, con
un indice unico parcial sobre esa referencia: un reintento de Stripe no puede
acuñar una segunda credencial. La emision manual sigue funcionando sin ninguno
de los dos campos.

Keys are high-entropy random tokens (``secrets.token_urlsafe``); only their
SHA-256 hash is ever persisted, mirroring how GitHub/Stripe-style tokens work -
the plaintext is returned once, at ``issue()`` time, and cannot be recovered
from the store afterward. SQLite (stdlib), same pattern as
``nosilo_core.writeback_store.SqliteAuditLedger``.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.data_paths import resolve_state_path

DEFAULT_PATH = resolve_state_path("mcp_keys")
KEY_PREFIX = "lpk_"  # legacy "Linchpin key" prefix, kept stable so issued keys keep working - recognizable in logs/support tickets without being guessable

#: Why a presented key was accepted or refused. The middleware needs the
#: difference: an expired or unknown credential is a 401, an exhausted quota is
#: a 429 with a different thing to tell the client ("pay" vs "authenticate").
REASON_OK = "ok"
REASON_UNKNOWN = "unknown"
REASON_REVOKED = "revoked"
REASON_EXPIRED = "expired"
REASON_QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True)
class KeyCheck:
    """Outcome of presenting a key, richer than the ``str | None`` it replaces.

    ``validate()`` still returns just the client name (or None) because that is
    the contract its callers and tests already hold; this carries the *why*, so
    the HTTP layer can answer 401 vs 429 without guessing.
    """

    allowed: bool
    reason: str
    client_name: str | None = None
    expires_at: float | None = None
    requests_used: int | None = None
    quota_requests: int | None = None
    remaining: int | None = None


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class McpKeyStore:
    """Issue, validate, and revoke per-client API keys for the MCP server."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + an explicit Lock: this store is a lazy
        # singleton (webapp/app.py::_get_mcp_key_store) read by an ASGI
        # middleware (webapp/mcp_auth.py) whose request-handling thread is not
        # guaranteed to be the one that constructed the store - Starlette's
        # BaseHTTPMiddleware in particular can dispatch through anyio's
        # from-thread portal. SQLite itself tolerates a connection being used
        # from multiple threads as long as access is serialized; the stdlib
        # sqlite3 module's default (check_same_thread=True) is a conservative
        # guard against exactly that lack of serialization, so disabling it is
        # only safe paired with the lock below.
        # timeout=30: same reasoning as SqliteAuditLedger - don't raise "database is
        # locked" under routine multi-worker contention on a shared keys file.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, timeout=30.0, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS keys ("
                " key_hash TEXT PRIMARY KEY,"
                " client_name TEXT NOT NULL,"
                " issued_at REAL NOT NULL,"
                " active INTEGER NOT NULL DEFAULT 1,"
                " last_used_at REAL"
                ")"
            )
            # Migraciones aditivas. Un CREATE TABLE IF NOT EXISTS no toca una tabla
            # que ya existe, asi que las bases ya emitidas necesitan el ALTER,
            # guardado por PRAGMA porque sqlite no tiene ADD COLUMN IF NOT EXISTS.
            #
            # email/payment_ref (cableado del webhook de pago): sin email no hay a
            # quien entregarle la key, y sin referencia de pago no hay forma de saber
            # cual compra la origino ni de resistir un reintento.
            #
            # expires_at / quota_* / requests_used (2026-10-13): una key paga podia
            # golpear la API para siempre. Las cuatro son NULLABLE a proposito: una
            # key ya emitida -o emitida hoy sin flags- sigue siendo ilimitada y sin
            # vencimiento, asi que este cambio no altera ninguna credencial viva.
            columnas = {fila[1] for fila in self._conn.execute("PRAGMA table_info(keys)")}
            nuevas = {
                "email": "TEXT",
                "payment_ref": "TEXT",
                "expires_at": "REAL",
                "quota_requests": "INTEGER",
                "quota_window_seconds": "REAL",
                "window_started_at": "REAL",
                "requests_used": "INTEGER NOT NULL DEFAULT 0",
            }
            for columna, tipo in nuevas.items():
                if columna not in columnas:
                    self._conn.execute(f"ALTER TABLE keys ADD COLUMN {columna} {tipo}")
            # IDEMPOTENCIA POR ESQUEMA, no por memoria. Stripe reintenta un webhook
            # hasta recibir un 2xx, y un proceso que recuerda que ya lo atendio
            # olvida en cada deploy -- justo el modo de falla que L1 acaba de
            # cerrar. Con este indice, el segundo intento NO PUEDE acuñar una
            # segunda key: lo impide la base, no el codigo. Parcial (WHERE NOT
            # NULL) porque las keys emitidas a mano no tienen pago y no deben
            # chocar entre si.
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS keys_payment_ref"
                " ON keys(payment_ref) WHERE payment_ref IS NOT NULL"
            )
            self._conn.commit()

    def issue(
        self,
        client_name: str,
        *,
        now: float | None = None,
        email: str | None = None,
        payment_ref: str | None = None,
        expires_at: float | None = None,
        quota_requests: int | None = None,
        quota_window_seconds: float | None = None,
    ) -> str:
        """Mint a new key for ``client_name`` and return it in PLAINTEXT.

        This is the only time the plaintext is ever available - the caller must
        hand it to the client immediately; it cannot be retrieved from the store
        again.

        ``email`` y ``payment_ref`` son opcionales para no romper la emision
        manual (``examples/issue_mcp_key.py``), pero la ruta de pago los llena
        siempre: sin email no hay a quien entregarle la key, y sin referencia de
        pago la key no es trazable a la compra que la origino. Un ``payment_ref``
        repetido levanta ``sqlite3.IntegrityError`` por el indice unico parcial;
        usar :meth:`issue_for_payment` si eso es un reintento esperado.

        ``expires_at`` y ``quota_requests`` son tambien opcionales, y NULL
        significa "sin limite" en los dos casos: la emision de siempre no cambia
        de comportamiento. Se validan aca, en el momento de acuñar, porque una
        key que nace vencida o con cuota cero es un error de quien la emite y no
        algo que deba descubrir el cliente al usarla.
        """
        if now is None:
            now = time.time()
        if expires_at is not None and expires_at <= now:
            raise ValueError("expires_at ya paso: una key no puede nacer vencida")
        if quota_requests is not None and quota_requests < 1:
            raise ValueError("quota_requests tiene que ser >= 1 (o None para ilimitada)")
        if quota_window_seconds is not None:
            if quota_requests is None:
                raise ValueError("quota_window_seconds no significa nada sin quota_requests")
            if quota_window_seconds <= 0:
                raise ValueError("quota_window_seconds tiene que ser > 0")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO keys (key_hash, client_name, issued_at, active, last_used_at,"
                " email, payment_ref, expires_at, quota_requests, quota_window_seconds,"
                " window_started_at, requests_used)"
                " VALUES (?, ?, ?, 1, NULL, ?, ?, ?, ?, ?, NULL, 0)",
                (
                    _hash(key), client_name, now, email, payment_ref,
                    expires_at, quota_requests, quota_window_seconds,
                ),
            )
            self._conn.commit()
        return key

    def issue_for_payment(
        self,
        client_name: str,
        payment_ref: str,
        *,
        email: str | None = None,
        now: float | None = None,
    ) -> str | None:
        """Como :meth:`issue`, pero devuelve ``None`` si esa compra ya emitio una.

        Stripe reintenta un webhook hasta recibir un 2xx, y tambien reenvia
        eventos a mano desde su panel. Devolver None (en vez de acuñar otra key o
        de reventar) deja que el llamador conteste 200 y corte la cadena de
        reintentos sin duplicar credenciales. No se puede devolver la key
        original: solo se guarda su hash, que es justamente el punto.
        """
        if not payment_ref:
            raise ValueError("payment_ref vacio: una emision por pago tiene que ser trazable")
        try:
            return self.issue(client_name, now=now, email=email, payment_ref=payment_ref)
        except sqlite3.IntegrityError:
            return None

    def check(self, presented_key: str, *, now: float | None = None) -> KeyCheck:
        """Decide whether ``presented_key`` may be used right now, and say why not.

        One transaction under the lock: the read, the quota decision and the
        counter bump have to be atomic, or two concurrent requests both see "one
        request left" and both pass.

        Counters move **only on an allowed request**. A refused one is not usage,
        and a client hammering an exhausted quota must not be able to push their
        own window forward by doing so.
        """
        if not presented_key:
            return KeyCheck(allowed=False, reason=REASON_UNKNOWN)
        if now is None:
            now = time.time()
        key_hash = _hash(presented_key)
        with self._lock:
            row = self._conn.execute(
                "SELECT client_name, active, expires_at, quota_requests,"
                " quota_window_seconds, window_started_at, requests_used"
                " FROM keys WHERE key_hash = ?",
                (key_hash,),
            ).fetchone()
            if row is None:
                return KeyCheck(allowed=False, reason=REASON_UNKNOWN)
            client, active, expires_at, quota, window, window_started, used = row
            used = int(used or 0)
            if not active:
                return KeyCheck(allowed=False, reason=REASON_REVOKED, client_name=client)
            if expires_at is not None and now >= expires_at:
                return KeyCheck(
                    allowed=False, reason=REASON_EXPIRED, client_name=client, expires_at=expires_at
                )
            if quota is not None:
                # A rolling window that has elapsed resets the count. A lifetime
                # quota has no window at all (``quota_window_seconds`` NULL).
                if window is not None and (window_started is None or now - window_started >= window):
                    used = 0
                    window_started = now
                if used >= quota:
                    return KeyCheck(
                        allowed=False, reason=REASON_QUOTA_EXCEEDED, client_name=client,
                        expires_at=expires_at, requests_used=used, quota_requests=quota,
                        remaining=0,
                    )
            used += 1
            self._conn.execute(
                "UPDATE keys SET last_used_at = ?, requests_used = ?, window_started_at = ?"
                " WHERE key_hash = ?",
                (now, used, window_started, key_hash),
            )
            self._conn.commit()
            return KeyCheck(
                allowed=True, reason=REASON_OK, client_name=client, expires_at=expires_at,
                requests_used=used, quota_requests=quota,
                remaining=None if quota is None else quota - used,
            )

    def validate(self, presented_key: str, *, now: float | None = None) -> str | None:
        """Return the owning client_name if ``presented_key`` may be used now; None
        otherwise (empty input, unknown key, revoked key, expired key, or a key
        that is over quota).

        Deliberately still just the name: its callers and tests hold that
        contract. Use :meth:`check` when the *reason* matters - the HTTP layer
        needs it to answer 401 for a credential that is dead and 429 for one that
        is merely exhausted, which are two different things to tell a client.

        Updates ``last_used_at`` and the usage counter on success so an operator
        can see whether a client is actually using what they paid for."""
        result = self.check(presented_key, now=now)
        return result.client_name if result.allowed else None

    def revoke(self, presented_key: str) -> bool:
        """Deactivate ``presented_key``. Returns whether an active key was found and
        revoked. Does not delete the row - list_keys() keeps showing it (inactive),
        preserving the audit trail of what was ever issued."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE keys SET active = 0 WHERE key_hash = ? AND active = 1", (_hash(presented_key),)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def revoke_client(self, client_name: str) -> int:
        """Deactivate every active key belonging to ``client_name``. Returns how many."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE keys SET active = 0 WHERE client_name = ? AND active = 1", (client_name,)
            )
            self._conn.commit()
            return cur.rowcount

    def list_keys(self) -> list[dict]:
        """Operator-facing listing. Never includes the plaintext key or its hash."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT client_name, issued_at, active, last_used_at, email, payment_ref,"
                " expires_at, quota_requests, quota_window_seconds, requests_used"
                " FROM keys ORDER BY issued_at"
            ).fetchall()
        # `email` es PII y aparece aca porque el operador la necesita para
        # entregar la key: mientras no exista envio de correo, ese ultimo tramo
        # es humano. No se expone por ninguna superficie de cliente.
        #
        # expires_at / quota_requests / requests_used estan aca porque sin ellos el
        # operador no puede contestar la pregunta que su cliente le va a hacer
        # ("me queda cuota?"). Siguen siendo superficie de operador, no de cliente:
        # list_keys() lo lee solo examples/issue_mcp_key.py.
        return [
            {
                "client_name": r[0], "issued_at": r[1], "active": bool(r[2]), "last_used_at": r[3],
                "email": r[4], "payment_ref": r[5], "expires_at": r[6],
                "quota_requests": r[7], "quota_window_seconds": r[8],
                "requests_used": int(r[9] or 0),
            }
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
