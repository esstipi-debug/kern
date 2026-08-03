# Resiliencia de versión de API en Shopify — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que los conectores de Shopify detecten cuándo Shopify les está sirviendo una versión de API distinta a la que piden, y subir el pin a `2026-07` con el cambio incompatible que eso exige.

**Architecture:** Un módulo nuevo `src/connectors/shopify_api_version.py` concentra el pin (hoy duplicado en dos archivos) y dos comprobaciones: una por petición contra el header `X-Shopify-API-Version`, y una bajo demanda contra la query `publicApiVersions`. Los dos clientes GraphQL pasan sus respuestas por la primera. El bump del pin es el último paso, para que las tareas 1-4 demuestren el mecanismo antes de cambiar la política.

**Tech Stack:** Python 3.11+, pytest, `uv`. Sin dependencias nuevas — `httpx` ya es opcional y no se toca.

Spec: [`docs/superpowers/specs/2026-08-02-shopify-api-version-resilience-design.md`](../specs/2026-08-02-shopify-api-version-resilience-design.md)

## Global Constraints

- **Todo offline.** Ningún test toca la red. El repo es offline-first; los stand-ins son `InMemoryShopify` e `InMemoryShopifyInventory`.
- **Comandos:** `cd "C:\Users\Gamer\Music\scm\supply-chain-optimization"` y siempre `uv run --no-sync pytest ...`. Sin `--no-sync`, `uv run` re-sincroniza el venv al lockfile y la suite falla en colección con `ModuleNotFoundError: fastapi`.
- **Nunca commitear `uv.lock`.** Se ensucia solo. `git checkout -- uv.lock` antes de cada `git add`.
- **Rama:** `shopify-api-version-resilience`, salida de `origin/main` (`d00c6b1`).
- **`PINNED_VERSION` arranca en `"2024-10"`** y solo cambia en la Tarea 5. Las tareas 1-4 no alteran ningún comportamiento contra una tienda real.
- **Una respuesta sin headers nunca es un error.** Los stand-ins offline no devuelven objetos con `.headers`; tratar su ausencia como deriva rompe la suite entera.
- **Idioma:** docstrings y comentarios en inglés (el resto de `src/connectors/` lo está). Mensajes de commit en español, como el historial reciente.

## File Structure

| Archivo | Responsabilidad | Tarea |
|---|---|---|
| `src/connectors/shopify_api_version.py` | *Nuevo.* Pin único, detección de deriva por header, descubrimiento de versiones, estado del pin | 1, 2 |
| `tests/test_shopify_api_version.py` | *Nuevo.* Cobertura del módulo anterior | 1, 2 |
| `scripts/check_shopify_api_version.py` | *Nuevo.* Invocación explícita del descubrimiento (necesita credenciales) | 2 |
| `src/connectors/shopify_prices.py` | Cablear pin único + `is_write` | 3 |
| `tests/test_connector_shopify_prices.py` | Primeros tests de `ShopifyClient` (el transporte) | 3 |
| `src/connectors/shopify_inventory.py` | Cablear pin único + `is_write`, arreglar el payload, `@idempotent` | 4, 5 |
| `tests/test_shopify_inventory.py` | Primeros tests de `ShopifyInventoryClient` | 4, 5 |

**Contexto que el implementador no puede adivinar:** `ShopifyClient` (precios) y `ShopifyInventoryClient` (stock) **no tienen ningún test hoy**. Los dos archivos de test existentes cubren solo los stand-ins y los stores. Los tests de transporte de las tareas 3 y 4 son los primeros que ejecutan ese código.

---

### Task 1: Detección de deriva por header

**Files:**
- Create: `src/connectors/shopify_api_version.py`
- Test: `tests/test_shopify_api_version.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `PINNED_VERSION: str` (arranca `"2024-10"`)
  - `ShopifyVersionDriftError(RuntimeError)`
  - `served_version(response: Any) -> str | None`
  - `check_served(response: Any, *, requested: str, is_write: bool) -> None`

- [ ] **Step 1: Write the failing tests**

Crea `tests/test_shopify_api_version.py`:

```python
"""Tests for src/connectors/shopify_api_version.py."""

from __future__ import annotations

import logging

import pytest

from src.connectors.shopify_api_version import (
    PINNED_VERSION,
    ShopifyVersionDriftError,
    check_served,
    served_version,
)


class _Resp:
    """Minimal stand-in for an httpx response: only what the checks read."""

    def __init__(self, headers: dict | None = None) -> None:
        if headers is not None:
            self.headers = headers


def test_served_version_reads_the_header():
    assert served_version(_Resp({"X-Shopify-API-Version": "2025-10"})) == "2025-10"


def test_served_version_is_case_insensitive():
    # httpx headers are case-insensitive; a plain dict is not, so we normalise.
    assert served_version(_Resp({"x-shopify-api-version": "2025-10"})) == "2025-10"


def test_served_version_is_none_without_the_header():
    assert served_version(_Resp({})) is None


def test_served_version_is_none_without_headers_at_all():
    # The offline stand-ins return plain objects with no .headers attribute.
    assert served_version(_Resp()) is None


def test_check_served_passes_when_versions_match():
    check_served(_Resp({"X-Shopify-API-Version": "2024-10"}), requested="2024-10", is_write=False)


def test_check_served_raises_on_a_drifted_write():
    resp = _Resp({"X-Shopify-API-Version": "2025-10"})
    with pytest.raises(ShopifyVersionDriftError, match="2024-10.*2025-10"):
        check_served(resp, requested="2024-10", is_write=True)


def test_check_served_only_warns_on_a_drifted_read(caplog):
    resp = _Resp({"X-Shopify-API-Version": "2025-10"})
    with caplog.at_level(logging.WARNING):
        check_served(resp, requested="2024-10", is_write=False)
    assert "2025-10" in caplog.text


def test_check_served_is_silent_without_a_header_even_on_writes():
    # Offline stand-ins must not be treated as drift, or the whole suite breaks.
    check_served(_Resp(), requested="2024-10", is_write=True)


def test_pinned_version_looks_like_a_shopify_version():
    assert len(PINNED_VERSION) == 7 and PINNED_VERSION[4] == "-"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_api_version.py -q
```

Esperado: fallo en la colección — `ModuleNotFoundError: No module named 'src.connectors.shopify_api_version'`.

- [ ] **Step 3: Write the implementation**

Crea `src/connectors/shopify_api_version.py`:

```python
"""Shopify Admin API version pinning and drift detection.

Shopify releases an API version every quarter and supports each one for at least
twelve months. A request for a version that is no longer accessible does NOT
fail: Shopify *falls forward* and serves the request with the oldest accessible
stable version, returning 200. A stale pin therefore looks healthy while the
semantics underneath it silently move.

Two signals catch it, both from the API itself:

- ``X-Shopify-API-Version`` on every response carries the version actually used.
  Comparing it against the requested one detects drift that already happened.
- ``publicApiVersions`` lists the accessible versions and whether each is
  supported. It anticipates drift that has not happened yet (see
  ``discover_versions``).

This module owns the pin for BOTH Shopify connectors. It used to be duplicated
as ``_DEFAULT_API_VERSION`` in each of them, which is how a pin gets bumped in
one file and forgotten in the other.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_HEADER = "x-shopify-api-version"

#: The Admin API version both Shopify connectors request.
PINNED_VERSION = "2024-10"


class ShopifyVersionDriftError(RuntimeError):
    """Shopify served a different API version than the one requested.

    Raised on writes only. Writing stock or prices under the semantics of a
    version other than the one the code was written against puts bad data in
    the customer's system of record; a drifted read is recoverable.
    """


def served_version(response: Any) -> str | None:
    """Return the API version Shopify actually used, or None if unknowable.

    None means "no information", not "no drift": the offline stand-ins return
    objects without headers, and callers must treat that as a pass.
    """
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        items = headers.items()
    except AttributeError:
        return None
    for key, value in items:
        if str(key).lower() == _HEADER:
            return str(value)
    return None


def check_served(response: Any, *, requested: str, is_write: bool) -> None:
    """Raise on a drifted write, warn on a drifted read, pass when unknown."""
    served = served_version(response)
    if served is None or served == requested:
        return
    message = (
        f"Shopify API version drift: requested {requested!r} but the response was "
        f"served by {served!r}. The pinned version is most likely out of support and "
        f"Shopify is falling forward. Bump PINNED_VERSION in "
        f"src/connectors/shopify_api_version.py after verifying the operations in use."
    )
    if is_write:
        raise ShopifyVersionDriftError(message)
    logger.warning(message)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_shopify_api_version.py -q
```

Esperado: 9 passed.

- [ ] **Step 5: Commit**

```bash
git checkout -- uv.lock
git add src/connectors/shopify_api_version.py tests/test_shopify_api_version.py
git commit -m "feat(shopify): detectar cuando Shopify sirve una version distinta a la pedida

Shopify no falla ante una version retirada: hace fall-forward en silencio.
El header X-Shopify-API-Version delata la version realmente usada. Falla duro
en escrituras, avisa en lecturas, y no rompe los stand-ins sin headers."
```

---

### Task 2: Descubrimiento de versiones y estado del pin

**Files:**
- Modify: `src/connectors/shopify_api_version.py` (añadir al final)
- Modify: `tests/test_shopify_api_version.py` (añadir al final)
- Create: `scripts/check_shopify_api_version.py`

**Interfaces:**
- Consumes: `PINNED_VERSION` de la Tarea 1.
- Produces:
  - `SUPPORTED = "SOPORTADA"`, `EXPIRING = "POR_VENCER"`, `EXPIRED = "VENCIDA"` (str)
  - `VERSIONS_QUERY: str`
  - `parse_versions(data: dict) -> list[dict]` — cada dict con `handle: str`, `supported: bool`
  - `check_pin(versions: list[dict], *, pinned: str) -> str`
  - `discover_versions(graphql) -> list[dict]` — `graphql` es cualquier callable `(query: str, variables: dict) -> dict`

**Cómo se define "POR_VENCER".** `publicApiVersions` da `supported: bool`, sin fechas. La versión soportada **más antigua** es, por construcción del calendario trimestral de Shopify, la siguiente en caer. Esa es la definición: si el pin es el handle soportado más antiguo, está por vencer. Es derivable de la API, sin calendario hardcodeado.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/test_shopify_api_version.py`:

```python
from src.connectors.shopify_api_version import (  # noqa: E402
    EXPIRED,
    EXPIRING,
    SUPPORTED,
    check_pin,
    discover_versions,
    parse_versions,
)

_VERSIONS_PAYLOAD = {
    "publicApiVersions": [
        {"handle": "2025-10", "supported": True, "displayName": "2025-10"},
        {"handle": "2026-01", "supported": True, "displayName": "2026-01"},
        {"handle": "2026-07", "supported": True, "displayName": "2026-07"},
        {"handle": "unstable", "supported": False, "displayName": "unstable"},
    ]
}


def test_parse_versions_keeps_handle_and_support_flag():
    versions = parse_versions(_VERSIONS_PAYLOAD)
    assert {"handle": "2025-10", "supported": True} in versions
    assert len(versions) == 4


def test_parse_versions_rejects_an_unexpected_shape():
    with pytest.raises(ValueError, match="publicApiVersions"):
        parse_versions({"somethingElse": []})


def test_parse_versions_rejects_entries_without_a_handle():
    with pytest.raises(ValueError, match="handle"):
        parse_versions({"publicApiVersions": [{"supported": True}]})


def test_check_pin_reports_a_supported_version():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="2026-01") == SUPPORTED


def test_check_pin_reports_the_oldest_supported_version_as_expiring():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="2025-10") == EXPIRING


def test_check_pin_reports_an_absent_version_as_expired():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="2024-10") == EXPIRED


def test_check_pin_reports_an_unsupported_version_as_expired():
    assert check_pin(parse_versions(_VERSIONS_PAYLOAD), pinned="unstable") == EXPIRED


def test_discover_versions_calls_graphql_and_parses():
    calls = []

    def fake_graphql(query, variables):
        calls.append((query, variables))
        return _VERSIONS_PAYLOAD

    versions = discover_versions(fake_graphql)
    assert len(versions) == 4
    assert "publicApiVersions" in calls[0][0]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_api_version.py -q
```

Esperado: `ImportError: cannot import name 'EXPIRED' from 'src.connectors.shopify_api_version'`.

- [ ] **Step 3: Write the implementation**

Añade al final de `src/connectors/shopify_api_version.py`:

```python
#: The pinned version is accessible and not the next one to be retired.
SUPPORTED = "SOPORTADA"
#: The pinned version is the oldest supported one, so it is next to be retired.
EXPIRING = "POR_VENCER"
#: The pinned version is not in the supported set. Shopify is falling forward.
EXPIRED = "VENCIDA"

VERSIONS_QUERY = """
query PublicApiVersions {
  publicApiVersions {
    handle
    supported
    displayName
  }
}
"""


def parse_versions(data: dict) -> list[dict]:
    """Normalise a ``publicApiVersions`` payload to ``[{handle, supported}]``."""
    rows = data.get("publicApiVersions")
    if not isinstance(rows, list):
        raise ValueError(f"unexpected publicApiVersions payload: {data!r}")
    versions = []
    for row in rows:
        handle = (row or {}).get("handle")
        if not handle:
            raise ValueError(f"publicApiVersions entry without a handle: {row!r}")
        versions.append({"handle": str(handle), "supported": bool(row.get("supported"))})
    return versions


def check_pin(versions: list[dict], *, pinned: str) -> str:
    """Classify the pin against the versions Shopify currently reports.

    ``publicApiVersions`` carries no dates, only a support flag. Since Shopify
    retires versions in release order, the OLDEST supported handle is by
    construction the next one to go -- that is what EXPIRING means here.
    """
    supported = sorted(v["handle"] for v in versions if v["supported"])
    if pinned not in supported:
        return EXPIRED
    if supported and pinned == supported[0]:
        return EXPIRING
    return SUPPORTED


def discover_versions(graphql) -> list[dict]:
    """Run ``publicApiVersions`` through any ``(query, variables) -> data`` callable.

    Deliberately NOT called at import time or on every request: it needs the
    network, and doing so would break the offline suite. Call it from
    ``scripts/check_shopify_api_version.py`` on a quarterly cadence.
    """
    return parse_versions(graphql(VERSIONS_QUERY, {}))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_shopify_api_version.py -q
```

Esperado: 17 passed.

- [ ] **Step 5: Add the script**

Crea `scripts/check_shopify_api_version.py`:

```python
"""Report whether the pinned Shopify API version is still supported.

Needs live credentials, so it is NOT part of the test suite. Run it quarterly,
or after any Shopify changelog entry that touches the operations in use.

    SHOPIFY_SHOP_URL=https://acme.myshopify.com \
    SHOPIFY_ACCESS_TOKEN=shpat_... \
    uv run --no-sync python scripts/check_shopify_api_version.py
"""

from __future__ import annotations

import os
import sys

from src.connectors.shopify_api_version import (
    EXPIRED,
    EXPIRING,
    PINNED_VERSION,
    check_pin,
    discover_versions,
)


def main() -> int:
    shop = os.environ.get("SHOPIFY_SHOP_URL")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if not shop or not token:
        print("SHOPIFY_SHOP_URL and SHOPIFY_ACCESS_TOKEN are required", file=sys.stderr)
        return 2

    import httpx

    client = httpx.Client(base_url=shop, headers={"X-Shopify-Access-Token": token}, timeout=30.0)
    path = f"/admin/api/{PINNED_VERSION}/graphql.json"

    def graphql(query: str, variables: dict) -> dict:
        resp = client.post(path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f"Shopify GraphQL error: {body['errors']}")
        served = resp.headers.get("X-Shopify-API-Version")
        if served and served != PINNED_VERSION:
            print(f"DRIFT: requested {PINNED_VERSION}, served {served}")
        return body["data"]

    versions = discover_versions(graphql)
    status = check_pin(versions, pinned=PINNED_VERSION)
    supported = sorted(v["handle"] for v in versions if v["supported"])
    print(f"pin={PINNED_VERSION} status={status} supported={supported}")
    return 1 if status in (EXPIRED, EXPIRING) else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git checkout -- uv.lock
git add src/connectors/shopify_api_version.py tests/test_shopify_api_version.py scripts/check_shopify_api_version.py
git commit -m "feat(shopify): descubrir versiones soportadas y clasificar el pin

publicApiVersions dice que versiones hay y cuales estan soportadas. La mas
antigua soportada es la siguiente en caer, asi que ese es el criterio de
POR_VENCER, sin calendario hardcodeado. El script queda fuera de la suite
porque necesita credenciales."
```

---

### Task 3: Cablear el conector de precios

**Files:**
- Modify: `src/connectors/shopify_prices.py` — líneas 19-21 (comentario), 52 (`_DEFAULT_API_VERSION`), 79-95 (`__init__` y `_graphql`), 114 y 133 (llamadas)
- Test: `tests/test_connector_shopify_prices.py` (añadir al final)

**Interfaces:**
- Consumes: `PINNED_VERSION`, `ShopifyVersionDriftError`, `check_served` de la Tarea 1.
- Produces: `ShopifyClient._graphql(query, variables, *, is_write=False)`.

La clase se llama `ShopifyClient` (no `ShopifyPricesClient`, pese a lo que dice el docstring de `shopify_inventory.py`).

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/test_connector_shopify_prices.py`:

```python
# ---- transport (ShopifyClient) ------------------------------------------------
# First tests to ever exercise the real GraphQL client; everything above this
# line covers InMemoryShopify and the writeback store instead.

from src.connectors.shopify_api_version import (  # noqa: E402
    PINNED_VERSION,
    ShopifyVersionDriftError,
)
from src.connectors.shopify_prices import ShopifyClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self.headers = headers if headers is not None else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """httpx-shaped stand-in that records what was posted."""

    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self._headers = headers
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> _FakeResponse:
        self.calls.append((path, json))
        return _FakeResponse(self._payload, self._headers)


_VARIANT_PAYLOAD = {
    "data": {
        "productVariants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/1",
                        "price": "20.0",
                        "sku": "SKU-1",
                        "product": {"id": "gid://shopify/Product/9"},
                    }
                }
            ]
        }
    }
}

_UPDATE_PAYLOAD = {"data": {"productVariantsBulkUpdate": {"userErrors": []}}}


def test_client_uses_the_shared_pinned_version():
    http = _FakeHttp(_VARIANT_PAYLOAD)
    ShopifyClient(http).find_variant_by_sku("SKU-1")
    assert http.calls[0][0] == f"/admin/api/{PINNED_VERSION}/graphql.json"


def test_client_read_tolerates_drift():
    http = _FakeHttp(_VARIANT_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    variant = ShopifyClient(http).find_variant_by_sku("SKU-1")
    assert variant["sku"] == "SKU-1"


def test_client_write_refuses_to_run_under_drift():
    http = _FakeHttp(_VARIANT_PAYLOAD)
    client = ShopifyClient(http)
    client.find_variant_by_sku("SKU-1")

    drifted = _FakeHttp(_UPDATE_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    client._http = drifted
    with pytest.raises(ShopifyVersionDriftError):
        client.update_variant_price("gid://shopify/ProductVariant/1", 25.0)


def test_client_write_succeeds_without_drift():
    http = _FakeHttp(_VARIANT_PAYLOAD)
    client = ShopifyClient(http)
    client.find_variant_by_sku("SKU-1")

    client._http = _FakeHttp(_UPDATE_PAYLOAD, headers={"X-Shopify-API-Version": PINNED_VERSION})
    client.update_variant_price("gid://shopify/ProductVariant/1", 25.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_connector_shopify_prices.py -q -k "client"
```

Esperado: `test_client_write_refuses_to_run_under_drift` falla (no se lanza nada); `test_client_uses_the_shared_pinned_version` puede pasar por casualidad — se rompe en cuanto la Tarea 5 mueva el pin, que es justo lo que debe detectar.

- [ ] **Step 3: Write the implementation**

En `src/connectors/shopify_prices.py`:

1. Borra la línea 52 (`_DEFAULT_API_VERSION = "2024-10"`) y añade el import junto a los demás:

```python
from src.connectors.shopify_api_version import PINNED_VERSION, check_served
```

2. En `__init__`, cambia el default:

```python
    def __init__(self, http: Any, *, api_version: str = PINNED_VERSION) -> None:
```

3. Reemplaza `_graphql` entero:

```python
    def _graphql(self, query: str, variables: dict, *, is_write: bool = False) -> dict:
        resp = self._http.post(self._graphql_path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        check_served(resp, requested=self._api_version, is_write=is_write)
        body = resp.json()
        if body.get("errors"):
            raise ShopifyPricesError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]
```

4. Guarda la versión pedida en `__init__`, justo antes de `self._graphql_path`:

```python
        self._api_version = api_version
```

5. En `update_variant_price`, marca la llamada como escritura:

```python
        data = self._graphql(
            mutation,
            {"productId": product_id, "variants": [{"id": variant_id, "price": str(price)}]},
            is_write=True,
        )
```

6. Sustituye el comentario de las líneas 19-21 por:

```
  The GraphQL field/mutation names are pinned by
  ``src/connectors/shopify_api_version.PINNED_VERSION``; drift against the
  version Shopify actually serves is detected per request by ``check_served``.
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_connector_shopify_prices.py -q
```

Esperado: todos pasan, incluidos los que ya existían.

- [ ] **Step 5: Commit**

```bash
git checkout -- uv.lock
git add src/connectors/shopify_prices.py tests/test_connector_shopify_prices.py
git commit -m "feat(shopify): cablear la deteccion de deriva en el conector de precios

Toma el pin del modulo compartido en vez de su propia constante, y pasa cada
respuesta por check_served. Son ademas los primeros tests que ejecutan
ShopifyClient: el transporte GraphQL no tenia ninguno."
```

---

### Task 4: Cablear el conector de inventario y arreglar el payload

**Files:**
- Modify: `src/connectors/shopify_inventory.py` — línea 28 (`_DEFAULT_API_VERSION`), 53-66 (`__init__` y `_graphql`), 153-188 (`adjust_inventory`), 190-225 (`set_inventory`)
- Test: `tests/test_shopify_inventory.py` (añadir al final)

**Interfaces:**
- Consumes: `PINNED_VERSION`, `ShopifyVersionDriftError`, `check_served` de la Tarea 1.
- Produces: `ShopifyInventoryClient._graphql(query, variables, *, is_write=False)`.

**El defecto a arreglar.** `set_inventory` lee `res.get("inventorySetGroup")`; la doc de `2026-07` dice que `inventorySetQuantities` devuelve `inventoryAdjustmentGroup`. Cuando no encuentra el campo, el método hace `return available` — devuelve el valor que se quiso escribir como si Shopify lo hubiera confirmado. Un fallo es indistinguible de un éxito. Se lee el campo correcto y se falla si no está.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/test_shopify_inventory.py`:

```python
# ---- transport (ShopifyInventoryClient) ---------------------------------------
# First tests to ever exercise the real GraphQL client.

from src.connectors.shopify_api_version import (  # noqa: E402
    PINNED_VERSION,
    ShopifyVersionDriftError,
)
from src.connectors.shopify_inventory import ShopifyInventoryClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self.headers = headers if headers is not None else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self._headers = headers
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> _FakeResponse:
        self.calls.append((path, json))
        return _FakeResponse(self._payload, self._headers)


_LOCATIONS_PAYLOAD = {
    "data": {
        "locations": {
            "edges": [
                {"node": {"id": "gid://shopify/Location/1", "name": "Main", "isPrimary": True}}
            ]
        }
    }
}

_SET_PAYLOAD = {
    "data": {
        "inventorySetQuantities": {
            "inventoryAdjustmentGroup": {"changes": [{"quantityAfterChange": 180}]},
            "userErrors": [],
        }
    }
}

_SET_PAYLOAD_EMPTY = {
    "data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}}
}


def test_inventory_client_uses_the_shared_pinned_version():
    http = _FakeHttp(_LOCATIONS_PAYLOAD)
    ShopifyInventoryClient(http).list_locations()
    assert http.calls[0][0] == f"/admin/api/{PINNED_VERSION}/graphql.json"


def test_inventory_client_read_tolerates_drift():
    http = _FakeHttp(_LOCATIONS_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    assert ShopifyInventoryClient(http).list_locations()[0]["name"] == "Main"


def test_inventory_client_write_refuses_to_run_under_drift():
    http = _FakeHttp(_SET_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    with pytest.raises(ShopifyVersionDriftError):
        ShopifyInventoryClient(http).set_inventory(
            "gid://shopify/InventoryItem/SKU-1", "gid://shopify/Location/1", 180
        )


def test_set_inventory_reads_the_inventory_adjustment_group():
    http = _FakeHttp(_SET_PAYLOAD, headers={"X-Shopify-API-Version": PINNED_VERSION})
    qty = ShopifyInventoryClient(http).set_inventory(
        "gid://shopify/InventoryItem/SKU-1", "gid://shopify/Location/1", 180
    )
    assert qty == 180


def test_set_inventory_fails_instead_of_echoing_the_requested_quantity():
    # The old code returned `available` when the payload field was missing, so a
    # silent failure was indistinguishable from a confirmed write.
    http = _FakeHttp(_SET_PAYLOAD_EMPTY, headers={"X-Shopify-API-Version": PINNED_VERSION})
    with pytest.raises(ShopifyInventoryError, match="did not confirm"):
        ShopifyInventoryClient(http).set_inventory(
            "gid://shopify/InventoryItem/SKU-1", "gid://shopify/Location/1", 180
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_inventory.py -q -k "inventory_client or set_inventory"
```

Esperado: fallan `test_inventory_client_write_refuses_to_run_under_drift`, `test_set_inventory_reads_the_inventory_adjustment_group` (el código lee `inventorySetGroup`, devuelve el eco `180` — pasa por la razón equivocada) y `test_set_inventory_fails_instead_of_echoing_the_requested_quantity` (devuelve `180` en vez de fallar).

- [ ] **Step 3: Write the implementation**

En `src/connectors/shopify_inventory.py`:

1. Borra la línea 28 (`_DEFAULT_API_VERSION = "2024-10"`) y añade:

```python
from src.connectors.shopify_api_version import PINNED_VERSION, check_served
```

2. En `ShopifyInventoryClient.__init__`, cambia el default y guarda la versión:

```python
    def __init__(self, http: Any, *, api_version: str = PINNED_VERSION) -> None:
```

y antes de `self._graphql_path`:

```python
        self._api_version = api_version
```

3. Reemplaza `_graphql`:

```python
    def _graphql(self, query: str, variables: dict, *, is_write: bool = False) -> dict:
        resp = self._http.post(self._graphql_path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        check_served(resp, requested=self._api_version, is_write=is_write)
        body = resp.json()
        if body.get("errors"):
            raise ShopifyInventoryError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]
```

4. En `adjust_inventory`, marca la llamada:

```python
        data = self._graphql(mutation, {"input": input_data}, is_write=True)
```

5. En `set_inventory`, marca la llamada y arregla la lectura del payload. Sustituye desde `data = self._graphql(...)` hasta el final del método:

```python
        data = self._graphql(mutation, {"input": input_data}, is_write=True)
        res = data.get("inventorySetQuantities") or {}
        errors = res.get("userErrors") or []
        if errors:
            raise ShopifyInventoryError(f"Shopify rejected setting inventory: {errors}")
        # The payload field is inventoryAdjustmentGroup, not inventorySetGroup. Echoing
        # `available` back on a missing field made a failed write look like a success.
        changes = (res.get("inventoryAdjustmentGroup") or {}).get("changes", [])
        if not changes:
            raise ShopifyInventoryError(
                f"Shopify did not confirm the inventory set for {inventory_item_id!r} "
                f"at {location_id!r}: no inventoryAdjustmentGroup in the response"
            )
        return int(changes[0]["quantityAfterChange"])
```

6. En la mutación `SetInventory`, cambia `inventorySetGroup` por `inventoryAdjustmentGroup`:

```
          inventorySetQuantities(input: $input) {
            inventoryAdjustmentGroup {
              changes {
                quantityAfterChange
              }
            }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run --no-sync pytest tests/test_shopify_inventory.py -q
```

Esperado: todos pasan, incluidos los tres que ya existían.

- [ ] **Step 5: Commit**

```bash
git checkout -- uv.lock
git add src/connectors/shopify_inventory.py tests/test_shopify_inventory.py
git commit -m "fix(shopify): set_inventory devolvia el valor pedido como si fuera confirmado

Leia inventorySetGroup, que la doc de 2026-07 no reconoce; el payload es
inventoryAdjustmentGroup. Al no encontrarlo hacia return available, asi que un
fallo era indistinguible de un exito. Ahora falla. De paso, el conector toma el
pin compartido y pasa por check_served."
```

---

### Task 5: Subir el pin a 2026-07 con `@idempotent`

**Files:**
- Modify: `src/connectors/shopify_api_version.py` (`PINNED_VERSION`)
- Modify: `src/connectors/shopify_inventory.py` — Protocol `ShopifyInventoryRPC`, `adjust_inventory`, `set_inventory`, `InMemoryShopifyInventory`, `ShopifyInventoryStore.commit` y `_apply_restore`
- Test: `tests/test_shopify_inventory.py`, `tests/test_shopify_api_version.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: `adjust_inventory(inventory_item_id, location_id, delta, reason="correction", *, idempotency_key: str)` y `set_inventory(inventory_item_id, location_id, available, reason="correction", *, idempotency_key: str)` — en el Protocol, en el cliente real y en el stand-in.

**Por qué las dos cosas van juntas.** La doc de `2026-07` dice: *«As of version `2026-04`, the idempotency key is required and must be provided using the `@idempotent` directive»*, para `inventorySetQuantities` y `inventoryAdjustQuantities`. Subir el pin sin la directiva deja las escrituras de stock rotas; añadir la directiva sin subir el pin la manda a una versión que no la conoce. Un revisor no puede aprobar una mitad sin la otra.

**De dónde sale la clave.** `ShopifyInventoryStore.commit` ya recibe `changeset.idempotency_key`. Nunca viajó a la petición. Para `_apply_restore` (el rollback) se deriva `f"{key}:restore"`, porque una restauración es una escritura distinta de la original y compartir clave haría que Shopify la descartara como duplicada.

- [ ] **Step 1: Write the failing tests**

Añade al final de `tests/test_shopify_inventory.py`:

```python
def test_set_inventory_sends_the_idempotent_directive_and_key():
    http = _FakeHttp(_SET_PAYLOAD, headers={"X-Shopify-API-Version": PINNED_VERSION})
    ShopifyInventoryClient(http).set_inventory(
        "gid://shopify/InventoryItem/SKU-1",
        "gid://shopify/Location/1",
        180,
        idempotency_key="key-abc",
    )
    _path, sent = http.calls[0]
    assert "@idempotent" in sent["query"]
    assert sent["variables"]["idempotencyKey"] == "key-abc"


def test_adjust_inventory_sends_the_idempotent_directive_and_key():
    payload = {
        "data": {
            "inventoryAdjustQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"delta": -10, "quantityAfterChange": 140}]},
                "userErrors": [],
            }
        }
    }
    http = _FakeHttp(payload, headers={"X-Shopify-API-Version": PINNED_VERSION})
    ShopifyInventoryClient(http).adjust_inventory(
        "gid://shopify/InventoryItem/SKU-1",
        "gid://shopify/Location/1",
        -10,
        idempotency_key="key-xyz",
    )
    _path, sent = http.calls[0]
    assert "@idempotent" in sent["query"]
    assert sent["variables"]["idempotencyKey"] == "key-xyz"


def test_store_commit_passes_the_changeset_key_through():
    class _RecordingRPC(InMemoryShopifyInventory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.keys: list[str] = []

        def set_inventory(self, inventory_item_id, location_id, available,
                          reason="correction", *, idempotency_key):
            self.keys.append(idempotency_key)
            return super().set_inventory(inventory_item_id, location_id, available,
                                         reason, idempotency_key=idempotency_key)

    loc = "gid://shopify/Location/1"
    rpc = _RecordingRPC(stock={"SKU-1": {loc: 150}})
    store = ShopifyInventoryStore(rpc, location_id=loc)
    cs = writeback.Changeset(
        idempotency_key="key-inv-777",
        target="shopify_inventory",
        risk_tier=writeback.TIER_REVERSIBLE,
        changes=(writeback.Change(entity_id="SKU-1", field="quantity", before=150, after=180),),
    )
    store.claim(cs.idempotency_key)
    store.commit(cs, approved_by="operator@kern.ai")
    assert rpc.keys == ["key-inv-777"]

    store.rollback("key-inv-777")
    # A restore is a distinct write: sharing the key would make Shopify drop it.
    assert rpc.keys[-1] == "key-inv-777:restore"
```

Y añade a `tests/test_shopify_api_version.py`:

```python
def test_pin_is_the_version_that_requires_the_idempotent_directive():
    # 2026-04 onwards requires @idempotent on the inventory mutations; the
    # connector sends it, so the pin must not fall below that.
    assert PINNED_VERSION >= "2026-04"
```

Necesitas importar `InMemoryShopifyInventory` en `tests/test_shopify_inventory.py` si no está ya:

```python
from src.connectors.shopify_inventory import InMemoryShopifyInventory  # noqa: E402
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run --no-sync pytest tests/test_shopify_inventory.py tests/test_shopify_api_version.py -q
```

Esperado: `TypeError: set_inventory() got an unexpected keyword argument 'idempotency_key'`, y el assert del pin falla porque sigue en `2024-10`.

- [ ] **Step 3: Write the implementation**

1. En `src/connectors/shopify_api_version.py`:

```python
PINNED_VERSION = "2026-07"
```

2. En `src/connectors/shopify_inventory.py`, actualiza el Protocol:

```python
    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int,
                         reason: str = "correction", *, idempotency_key: str) -> int: ...

    def set_inventory(self, inventory_item_id: str, location_id: str, available: int,
                      reason: str = "correction", *, idempotency_key: str) -> int: ...
```

3. En `ShopifyInventoryClient.adjust_inventory`, cambia firma, mutación y variables:

```python
    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int,
                         reason: str = "correction", *, idempotency_key: str) -> int:
        mutation = """
        mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!, $idempotencyKey: String!)
        @idempotent(key: $idempotencyKey) {
          inventoryAdjustQuantities(input: $input) {
            inventoryAdjustmentGroup {
              changes {
                delta
                quantityAfterChange
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
```

y la llamada:

```python
        data = self._graphql(
            mutation,
            {"input": input_data, "idempotencyKey": idempotency_key},
            is_write=True,
        )
```

4. Lo mismo en `set_inventory`:

```python
    def set_inventory(self, inventory_item_id: str, location_id: str, available: int,
                      reason: str = "correction", *, idempotency_key: str) -> int:
        mutation = """
        mutation SetInventory($input: InventorySetQuantitiesInput!, $idempotencyKey: String!)
        @idempotent(key: $idempotencyKey) {
          inventorySetQuantities(input: $input) {
            inventoryAdjustmentGroup {
              changes {
                quantityAfterChange
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
```

y la llamada:

```python
        data = self._graphql(
            mutation,
            {"input": input_data, "idempotencyKey": idempotency_key},
            is_write=True,
        )
```

5. En `InMemoryShopifyInventory`, acepta el argumento (lo ignora, es un stand-in):

```python
    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int,
                         reason: str = "correction", *, idempotency_key: str = "offline") -> int:
```

```python
    def set_inventory(self, inventory_item_id: str, location_id: str, available: int,
                      reason: str = "correction", *, idempotency_key: str = "offline") -> int:
```

6. En `ShopifyInventoryStore.commit`, pasa la clave del changeset:

```python
                self._rpc.set_inventory(inv_item_id, self._location_id, new_qty,
                                        idempotency_key=changeset.idempotency_key)
```

7. `_apply_restore` necesita la clave. Cambia su firma y las dos llamadas:

```python
    def _apply_restore(self, restore, *, idempotency_key: str) -> None:
        for entity_id, _field, original in restore:
            item = self._rpc.find_inventory_item_by_sku(entity_id)
            if item is None or not item.get("inventory_item_id"):
                raise ShopifyInventoryError(f"cannot restore: SKU {entity_id!r} no longer exists")
            self._rpc.set_inventory(item["inventory_item_id"], self._location_id, int(original),
                                    idempotency_key=f"{idempotency_key}:restore")
```

En `commit`, dentro del `except Exception:`:

```python
            self._apply_restore(restore, idempotency_key=changeset.idempotency_key)
```

En `rollback`:

```python
        self._apply_restore(entry.restore, idempotency_key=idempotency_key)
```

8. Actualiza el docstring del módulo (líneas 1-13) para que la lista de mutaciones diga que llevan `@idempotent`.

9. **Actualiza los tests de la Tarea 4**, que llaman a `set_inventory` sin la clave y ahora es obligatoria. Son tres, todos en `tests/test_shopify_inventory.py`: `test_inventory_client_write_refuses_to_run_under_drift`, `test_set_inventory_reads_the_inventory_adjustment_group` y `test_set_inventory_fails_instead_of_echoing_the_requested_quantity`. En los tres, añade el argumento a la llamada:

```python
        ShopifyInventoryClient(http).set_inventory(
            "gid://shopify/InventoryItem/SKU-1",
            "gid://shopify/Location/1",
            180,
            idempotency_key="key-t4",
        )
```

Sin esto la Tarea 5 rompe tres tests verdes con `TypeError: set_inventory() missing 1 required keyword-only argument`.

10. **Actualiza el test del pin en `tests/test_shopify_api_version.py`.** La Tarea 1 lo dejó afirmando el valor exacto, para que el pin no se moviera por accidente antes de esta tarea. Ahora sí se mueve, así que el test tiene que moverse con él. Renómbralo y cambia el valor:

```python
def test_pinned_version_is_2026_07():
    assert PINNED_VERSION == "2026-07"
```

Sin esto, la Tarea 5 rompe `test_pinned_version_is_2024_10`. Que ese test falle es la señal correcta: el bump es un cambio deliberado y tiene que verse en el diff.

- [ ] **Step 4: Run the full suite**

```bash
uv run --no-sync pytest tests/ -q
```

Esperado: todo verde. La línea base antes de empezar es **4416 tests · 3 skipped · 95,71%**; el número final debe ser mayor y **cero regresiones**. Si algo rojo no lo tocaste tú, no lo arregles a ciegas: mira si es la trampa de `MAX_PATH` o del `uv.lock` (ver Global Constraints).

- [ ] **Step 5: Commit**

```bash
git checkout -- uv.lock
git add src/connectors/shopify_api_version.py src/connectors/shopify_inventory.py tests/
git commit -m "feat(shopify): subir el pin a 2026-07 con la directiva @idempotent

Desde 2026-04 las dos mutaciones de inventario exigen la clave de idempotencia
via @idempotent. La clave ya existia en writeback y nunca viajaba en la
peticion; ahora se enhebra. El rollback usa una clave derivada porque una
restauracion es una escritura distinta de la original.

Verificado contra la doc de 2026-07, NO contra una tienda real."
```

---

## Verificación final

- [ ] `uv run --no-sync pytest tests/ -q` en verde, sin regresiones sobre 4416.
- [ ] `grep -rn "_DEFAULT_API_VERSION" src/` no devuelve nada.
- [ ] `grep -rn "inventorySetGroup" src/` no devuelve nada.
- [ ] `git diff origin/main --stat` toca solo los siete archivos de la tabla de arriba.
- [ ] `git status` limpio, sin `uv.lock`.

## Lo que este trabajo NO verifica

Todo se probó offline. Las cuatro operaciones se verificaron **contra la documentación de `2026-07`, no contra una tienda**. Que el bump funcione de verdad queda sin medir hasta correr `scripts/check_shopify_api_version.py` y una escritura de stock contra un store de desarrollo.

**No describir esto como comprobado en ningún README** hasta que eso ocurra.
