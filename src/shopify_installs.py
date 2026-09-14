"""Las tiendas instaladas de la app de Shopify: dominio -> Admin API token.

Es el almacen que la brecha L2 necesitaba y no existia: una custom app recibe su
token pegado a mano y no guarda nada, una app del App Store lo obtiene por OAuth
una vez y lo usa despues, sin el comerciante delante.

EL TOKEN SE GUARDA EN CLARO, Y HAY QUE DECIRLO. ``src/mcp_keys.py`` guarda solo
el SHA-256 de sus keys porque son NUESTRAS y solo hace falta verificarlas; este
token es de la tienda y hay que PRESENTARLO en cada llamada, asi que un hash no
sirve. No se cifra porque cifrar sin una historia de manejo de claves mueve el
problema de lugar y agrega la ilusion de haberlo resuelto. Lo que si se hace:

  * nunca sale por ``list_installs()`` ni por ninguna ruta HTTP;
  * ``forget()`` lo borra de verdad (DELETE, no un flag), porque ``shop/redact``
    y ``app/uninstalled`` significan exactamente eso;
  * el archivo vive donde vivan los demas almacenes durables -- en Fly, el
    volumen ``/data`` (ver el bloque ``[env]`` de fly.toml), NO el filesystem
    efimero de la Machine, que es la falla que costo el ledger de leads.

SQLite de la stdlib, mismo patron de lock y ``check_same_thread=False`` que
``McpKeyStore``, y por el mismo motivo: lo lee un handler ASGI cuyo hilo no es el
que construyo el store.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_PATH = "data/shopify_installs.sqlite3"


class ShopifyInstallStore:
    """Guarda, recupera y olvida el token de cada tienda instalada."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, timeout=30.0, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS installs ("
                " shop TEXT PRIMARY KEY,"
                " access_token TEXT NOT NULL,"
                " scopes TEXT NOT NULL,"
                " installed_at REAL NOT NULL,"
                " last_seen_at REAL"
                ")"
            )
            self._conn.commit()

    def save(
        self, shop: str, access_token: str, scopes: tuple[str, ...] | list[str],
        *, now: float | None = None,
    ) -> None:
        """Alta o REEMPLAZO del token de ``shop``.

        Upsert y no insert: reinstalar una app es normal (el comerciante la borra
        y la vuelve a poner, o acepta scopes nuevos) y cada reinstalacion emite un
        token nuevo que invalida el anterior. Guardar los dos dejaria uno muerto
        con aspecto de vivo.
        """
        if not shop or not access_token:
            raise ValueError("shop y access_token son obligatorios")
        ahora = time.time() if now is None else now
        with self._lock:
            self._conn.execute(
                "INSERT INTO installs (shop, access_token, scopes, installed_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, NULL)"
                " ON CONFLICT(shop) DO UPDATE SET"
                "  access_token = excluded.access_token,"
                "  scopes = excluded.scopes,"
                "  installed_at = excluded.installed_at",
                (shop, access_token, ",".join(scopes), ahora),
            )
            self._conn.commit()

    def token_for(self, shop: str, *, now: float | None = None) -> str | None:
        """El token de ``shop``, o None si no esta instalada."""
        if not shop:
            return None
        with self._lock:
            fila = self._conn.execute(
                "SELECT access_token FROM installs WHERE shop = ?", (shop,)
            ).fetchone()
            if fila is None:
                return None
            self._conn.execute(
                "UPDATE installs SET last_seen_at = ? WHERE shop = ?",
                (time.time() if now is None else now, shop),
            )
            self._conn.commit()
            return fila[0]

    def forget(self, shop: str) -> bool:
        """Borra la tienda. Devuelve si habia algo que borrar.

        DELETE de verdad y no un flag de inactivo: lo llaman ``shop/redact`` y
        ``app/uninstalled``, y en el primero conservar la fila seria justamente lo
        que el comerciante pidio que no pase.
        """
        with self._lock:
            cur = self._conn.execute("DELETE FROM installs WHERE shop = ?", (shop,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_installs(self) -> list[dict]:
        """Listado de operador. NUNCA incluye el token."""
        with self._lock:
            filas = self._conn.execute(
                "SELECT shop, scopes, installed_at, last_seen_at FROM installs ORDER BY installed_at"
            ).fetchall()
        return [
            {"shop": f[0], "scopes": tuple(s for s in f[1].split(",") if s),
             "installed_at": f[2], "last_seen_at": f[3]}
            for f in filas
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
