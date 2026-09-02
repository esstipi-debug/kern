# Adaptadores de llegada: Shippit y Sendle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que los otros dos transportistas del canal AU/NZ -- Shippit y Sendle -- puedan aportar la fecha de LLEGADA al motor de servicio, con la misma declaracion de cobertura que se le exige a Starshipit.

**Architecture:** Dos adaptadores nuevos contra el `ArrivalSource` Protocol que ya existe en `src/connectors/arrivals.py`. El Protocol NO se toca. Lo que cambia respecto de Starshipit es la LLAVE de consulta: Starshipit acepta la referencia del ERP (`order_number`), y estos dos no -- cada uno se consulta por su propio identificador interno. Eso obliga a un mapa `order_id -> llave del transportista` que el llamador provee, y obliga a distinguir dos silencios que hoy se ven identicos.

**Tech Stack:** Python 3.11+, `httpx` (ya en el repo), `pytest`. Sin dependencias nuevas.

**Spec:** `docs/handoffs/HANDOFF-servicio-realizado-2026-08-31.md` (seccion "Lo que falta"), y el modulo `src/connectors/arrivals.py` cuyo docstring es la especificacion viva del Protocol.

---

## Global Constraints

Copiados de la doc de cada proveedor, verificados el 2026-09-01. **Ningun valor de esta seccion se infiere: cada uno salio de la referencia del proveedor.**

### Shippit

- Base URL: `https://app.shippit.com/api/3`
- Autenticacion: cabecera `Authorization: Bearer <api_key>`. **La clave VIAJA** en cada request, como Cin7 y Starshipit y a diferencia de Unleashed (que firma).
- Endpoint: `GET /orders/{tracking_number}/tracking`
- Envoltorio de respuesta: `{"response": {"tracking_number", "tracking_url", "success", "track": [...]}}`. El array **NO** esta en la raiz.
- Cada elemento de `track`: `status`, `date` (ISO-8601), `timestamp`, `status_owner`.
- Estado de entrega: `"Completed"`. El webhook lo emite en minuscula (`"completed"`), asi que la comparacion es case-insensitive.
- Otros estados observados: `"With Driver"`, `"In Transit"`, `"Ready For Pick Up"`, `"Despatch In Progress"`, `"Order Placed"`.
- La llave del path es el `tracking_number` **de Shippit** (formato tipo `PP39TBECV7QSSPK`), no el numero de guia del courier -- ese viaja aparte como `courier_job_id`.

### Sendle

- Base URL: `https://api.sendle.com` (produccion), `https://sandbox.sendle.com` (sandbox).
- Autenticacion: **HTTP Basic**, usuario = Sendle ID, password = API key.
- Cabeceras: `Content-Type: application/json`, `Accept: application/json`.
- Endpoint: `GET /api/tracking/{sendle_reference}`
- Respuesta: `{"state", "tracking_events": [...], "origin": {...}, "destination": {...}}`. Sin envoltorio.
- Cada evento: `event_type`, `scan_time` (ISO-8601), `description`; opcionales `reason`, `origin_location`, `destination_location`.
- `event_type` de entrega: `"Delivered"`. Otros: `"Pickup Attempted"`, `"Pickup"`, `"Info"`, `"In Transit"`.
- Limite de tasa: **10 requests por segundo por IP**.
- La llave del path es el `sendle_reference` -- el identificador que Sendle asigna al crear la orden -- no el `customer_reference` del comercio.

### Ambos

- Nada de credenciales en logs, notas ni entregables. `live_http()` no toma credenciales; las cabeceras se arman por request. Es el patron que ya siguen `starshipit.py` y `cin7.py`.
- La fecha de llegada sale SIEMPRE del array de eventos, nunca de un campo de resumen.
- Se toma el PRIMER evento de entrega, no el ultimo: un segundo evento es duplicado o reintento del transportista.
- Salida `Arrival.arrived_on` en ISO `YYYY-MM-DD` (10 caracteres), igual que Starshipit.

---

## Decisiones de diseño (y por que)

### D1 -- La llave de consulta: mapa provisto, Protocol intacto

Starshipit consulta `GET /api/track?order_number={ref}`: acepta la referencia del ERP, asi que el adaptador recibe `order_ids` y listo.

Shippit y Sendle **no pueden hacer eso**. Se consultan por su identificador propio (`tracking_number` y `sendle_reference` respectivamente), que el ERP no conoce salvo que alguien lo haya guardado. Cin7 Core, por ejemplo, guarda un `TrackingNumber` en la linea de envio; Unleashed no expone ninguno.

**Lo elegido:** los dos adaptadores reciben `refs: Mapping[str, str]` -- clave = `order_id` del ERP, valor = llave del transportista -- y emiten `Arrival(order_id=<la del ERP>, ...)`. El Protocol `arrivals(*, since=None) -> list[Arrival]` no cambia, y `with_arrivals()` empalma igual que con Starshipit.

**Descartado:** agregarle al Protocol un metodo `arrivals_by_key()`. `ArrivalSource` es `runtime_checkable` y se satisface estructuralmente; sumarle un metodo des-satisface en silencio a Starshipit en cuanto algo corra un `isinstance`. Es la misma razon por la que `ArrivalSource` no se doblo dentro de `InventorySource`, y ya esta escrita en el docstring de `arrivals.py`.

**Descartado tambien:** que el adaptador resuelva el mapa solo, consultando el endpoint de ordenes del transportista. En Sendle eso agrega una llamada por pedido contra un limite de 10/segundo; en Shippit no hay listado por referencia del comercio -- el `retailer_order_number` aparece en el **webhook**, no en la respuesta de tracking. Resolver el mapa es trabajo del que tiene el ERP delante, no del adaptador.

### D2 -- Dos silencios que hoy se ven iguales

`with_arrivals()` interpreta una orden sin llegada como "el transportista no la reporto" y le saca las lineas del denominador de OTIF. Para Starshipit eso es honesto: se pregunto por la referencia y contesto 404.

Para Shippit y Sendle, una orden sin llave en el mapa produce **exactamente el mismo silencio** por un motivo completamente distinto: no se pregunto nunca. Mismo output, dos significados -- que es el patron de degradacion silenciosa que este repo ya pago una vez.

**Lo elegido:** los dos adaptadores aceptan tambien `order_ids` (el conjunto completo del ERP, opcional). Lo que este en `order_ids` y no en `refs` se cuenta y se publica en `notes` con su propio mensaje, distinto del tope de lecturas. Sin `order_ids` no hay nota: si solo te dieron un mapa, solo hablas del mapa.

### D3 -- El campo trampa de cada uno

Cada adaptador se prueba contra el campo que *parece* la fecha de entrega y no lo es:

| Transportista | Campo trampa | Por que enganña |
|---|---|---|
| Starshipit (ya hecho) | `last_updated_date` | Es la ultima actualizacion; cualquier evento posterior corre la entrega hacia adelante |
| Shippit | `expected_delivery_date` (webhook) | Es la **promesa**, no la llegada. Usarlo daria un OTIF perfecto por construccion |
| Sendle | `state == "Delivered"` | Es un estado terminal **sin marca de tiempo**. Un estado no es una fecha |

Sendle es el caso mas filoso: `state` dice que llego, y el adaptador igual devuelve `None` si no hay evento `Delivered` con `scan_time`. Preferir "no se cuando" antes que inventar una fecha.

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `src/connectors/shippit.py` (crear) | `delivered_on()`, `ShippitArrivals`, `SERVICE_COVERAGE`, `live_http()`, `auth_headers()` |
| `src/connectors/sendle.py` (crear) | `delivered_on()`, `SendleArrivals`, `SERVICE_COVERAGE`, `live_http()`, `auth_headers()` |
| `tests/test_connector_shippit.py` (crear) | Trampas y comportamiento propio de Shippit |
| `tests/test_connector_sendle.py` (crear) | Trampas y comportamiento propio de Sendle |
| `tests/test_connector_service_contract.py` (modificar) | Sumar los dos a `ARRIVAL_SOURCES` + el guard de D2 |

---

### Task 1: Shippit -- la fecha de llegada

**Files:**
- Create: `src/connectors/shippit.py`
- Test: `tests/test_connector_shippit.py`

**Interfaces:**
- Consumes: `src.connectors.arrivals.Arrival`; `src.connectors.{COVERAGE_MEASURED, COVERAGE_UNAVAILABLE, FieldCoverage, ServiceCoverage}`
- Produces:
  - `delivered_on(response: dict) -> str | None` -- recibe el cuerpo COMPLETO (con envoltorio `response`), devuelve ISO `YYYY-MM-DD`
  - `ShippitArrivals(http, creds: ShippitCredentials, refs: Mapping[str, str], *, order_ids: Sequence[str] | None = None, max_orders: int = 500)` con `.arrivals(*, since=None) -> list[Arrival]` y `.notes: tuple[str, ...]`
  - `ShippitCredentials(api_key: str)`
  - `SERVICE_COVERAGE: ServiceCoverage`
  - `ShippitError(RuntimeError)`
  - `API_BASE = "https://app.shippit.com/api/3"`, `DELIVERED = "completed"`

- [ ] **Step 1: Escribir los tests que fallan**

```python
"""Shippit -- el segundo transportista del canal AU/NZ."""
from __future__ import annotations

import pytest

from src.connectors.shippit import (
    ShippitArrivals,
    ShippitCredentials,
    ShippitError,
    delivered_on,
)


def _cuerpo(track):
    return {"response": {"tracking_number": "PP39TBECV7QSSPK", "success": True, "track": track}}


def test_la_llegada_sale_del_evento_completed():
    cuerpo = _cuerpo([
        {"status": "Order Placed", "date": "2024-04-18T10:00:00+10:00"},
        {"status": "In Transit", "date": "2024-04-20T08:00:00+10:00"},
        {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
    ])
    assert delivered_on(cuerpo) == "2024-04-22"


def test_un_evento_posterior_no_corre_la_fecha_de_entrega():
    """La entrega ocurrio la primera vez. Un escaneo posterior no la mueve."""
    cuerpo = _cuerpo([
        {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
        {"status": "Completed", "date": "2024-04-30T08:00:00+10:00"},
    ])
    assert delivered_on(cuerpo) == "2024-04-22"


def test_el_orden_del_array_no_importa():
    """Shippit publica el ejemplo con el evento mas nuevo primero. Tomar track[0] o
    track[-1] es apostar a un orden que la doc no garantiza."""
    cuerpo = _cuerpo([
        {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
        {"status": "In Transit", "date": "2024-04-20T08:00:00+10:00"},
    ])
    assert delivered_on(cuerpo) == "2024-04-22"


def test_el_estado_se_compara_sin_distinguir_mayusculas():
    """El tracking devuelve "Completed" y el webhook "completed". Es el mismo evento."""
    assert delivered_on(_cuerpo([{"status": "completed", "date": "2024-04-22T14:03:11Z"}]))


def test_expected_delivery_date_no_es_la_llegada():
    """LA TRAMPA. `expected_delivery_date` es la PROMESA de Shippit, no la entrega.
    Usarlo daria un OTIF perfecto por construccion: la promesa siempre cumple la promesa."""
    cuerpo = _cuerpo([{"status": "Completed", "date": "2024-04-22T14:03:11+10:00"}])
    cuerpo["response"]["expected_delivery_date"] = "2024-04-10T09:00:00+10:00"

    assert delivered_on(cuerpo) == "2024-04-22"


def test_sin_evento_completed_no_hay_fecha():
    assert delivered_on(_cuerpo([{"status": "In Transit", "date": "2024-04-20T08:00:00Z"}])) is None


def test_el_array_track_no_esta_en_la_raiz():
    """El envoltorio `response` es la convencion de Shippit. Leer `track` de la raiz
    devolveria None siempre, y eso se lee como 'no llego' en vez de como un bug."""
    plano = {"track": [{"status": "Completed", "date": "2024-04-22T14:03:11Z"}]}
    assert delivered_on(plano) is None


class _Http:
    """Doble de httpx. Registra que se consulto, para probar D1."""

    class _R:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def json(self):
            return self._payload

    def __init__(self, por_llave: dict, status: int = 200):
        self.por_llave, self._status = por_llave, status
        self.consultadas: list[str] = []

    def get(self, path, *, params=None, headers=None):
        llave = path.rsplit("/", 2)[-2]
        self.consultadas.append(llave)
        if llave not in self.por_llave:
            return _Http._R({}, 404)
        return _Http._R(self.por_llave[llave], self._status)


_CREDS = ShippitCredentials(api_key="secreta")


def test_se_consulta_por_la_llave_de_shippit_y_se_devuelve_la_del_erp():
    """D1 entero en un test: entra el tracking_number de Shippit, sale el OrderNumber
    del ERP. Devolver la llave del transportista haria que el empalme no encuentre nada."""
    http = _Http({"PPxyz": _cuerpo([{"status": "Completed", "date": "2024-04-22T14:03:11Z"}])})
    fuente = ShippitArrivals(http, _CREDS, {"SO-1": "PPxyz"})

    llegadas = fuente.arrivals()

    assert http.consultadas == ["PPxyz"]
    assert [(a.order_id, a.arrived_on) for a in llegadas] == [("SO-1", "2024-04-22")]


def test_una_orden_sin_llave_se_declara_y_no_se_calla():
    """D2. Sin esta nota, una orden que nunca se consulto sale del denominador de OTIF
    identica a una que el transportista dio por no entregada."""
    http = _Http({"PPxyz": _cuerpo([{"status": "Completed", "date": "2024-04-22T14:03:11Z"}])})
    fuente = ShippitArrivals(http, _CREDS, {"SO-1": "PPxyz"}, order_ids=["SO-1", "SO-2", "SO-3"])

    fuente.arrivals()

    assert http.consultadas == ["PPxyz"]
    assert any("2 de 3" in n for n in fuente.notes), fuente.notes


def test_sin_order_ids_no_se_inventa_una_nota():
    """Si solo te dieron un mapa, solo hablas del mapa."""
    http = _Http({"PPxyz": _cuerpo([{"status": "Completed", "date": "2024-04-22T14:03:11Z"}])})
    fuente = ShippitArrivals(http, _CREDS, {"SO-1": "PPxyz"})

    fuente.arrivals()

    assert fuente.notes == ()


def test_un_404_es_una_respuesta_no_una_falla():
    """Shippit no conoce esa referencia: el pedido pudo haber salido por fuera."""
    fuente = ShippitArrivals(_Http({}), _CREDS, {"SO-1": "PPxyz"})
    assert fuente.arrivals() == []


def test_un_error_de_red_no_se_degrada_a_no_llego():
    """Degradar un 500 a 'sin llegada' saca la linea del denominador de OTIF por un
    problema de red y no por falta de dato. El numero sale mejor y nadie se entera."""
    http = _Http({"PPxyz": _cuerpo([])}, status=500)
    fuente = ShippitArrivals(http, _CREDS, {"SO-1": "PPxyz"})

    with pytest.raises(ShippitError):
        fuente.arrivals()


def test_la_credencial_no_viaja_en_la_url():
    from src.connectors.shippit import auth_headers

    assert auth_headers(_CREDS) == {"Authorization": "Bearer secreta"}
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_connector_shippit.py -q
```

Esperado: FAIL con `ModuleNotFoundError: No module named 'src.connectors.shippit'`.

- [ ] **Step 3: Escribir `src/connectors/shippit.py`**

Docstring del modulo: la referencia verificada (endpoint, envoltorio, campos, estado de entrega), y las decisiones D1/D2/D3 en prosa, siguiendo la forma de `starshipit.py`.

```python
API_BASE = "https://app.shippit.com/api/3"
DELIVERED = "completed"
_MAX_ORDERS = 500

_NOTA_TOPE = (
    "Se consultaron {leidas} de {total} referencias (tope max_orders={tope}). "
    "/orders/{{ref}}/tracking consulta una por vez, asi que la cobertura de llegada "
    "alcanza SOLO a esas ordenes."
)
_NOTA_SIN_LLAVE = (
    "{sin} de {total} ordenes no tienen numero de seguimiento de Shippit y NO se "
    "consultaron. No es que el transportista las haya dado por no entregadas: nunca se "
    "pregunto por ellas. Sus lineas salen del denominador de OTIF por falta de mapeo, no "
    "por falta de entrega."
)


class ShippitError(RuntimeError):
    """Shippit contesto algo que no se puede leer como una respuesta."""


@dataclass(frozen=True)
class ShippitCredentials:
    api_key: str


def auth_headers(creds: ShippitCredentials) -> dict[str, str]:
    return {"Authorization": f"Bearer {creds.api_key}"}


def _iso(valor: Any) -> str | None:
    texto = str(valor or "").strip()
    return texto[:10] if len(texto) >= 10 else None


def delivered_on(payload: dict) -> str | None:
    """La fecha del PRIMER evento ``Completed``, o ``None``."""
    eventos = ((payload or {}).get("response") or {}).get("track") or []
    fechas = [
        fecha
        for ev in eventos
        if str((ev or {}).get("status") or "").strip().lower() == DELIVERED
        and (fecha := _iso((ev or {}).get("date"))) is not None
    ]
    return min(fechas) if fechas else None


class ShippitArrivals:
    def __init__(self, http, creds, refs, *, order_ids=None, max_orders=_MAX_ORDERS):
        self._http, self._creds = http, creds
        self._refs = dict(refs)
        self._order_ids = list(order_ids) if order_ids is not None else None
        self._max_orders = max_orders
        self.notes: tuple[str, ...] = ()

    def arrivals(self, *, since: str | None = None) -> list[Arrival]:
        pares = list(self._refs.items())[: self._max_orders]
        notas: list[str] = []
        if len(pares) != len(self._refs):
            notas.append(
                _NOTA_TOPE.format(
                    leidas=len(pares), total=len(self._refs), tope=self._max_orders
                )
            )
        if self._order_ids is not None:
            sin_llave = [o for o in self._order_ids if o not in self._refs]
            if sin_llave:
                notas.append(
                    _NOTA_SIN_LLAVE.format(sin=len(sin_llave), total=len(self._order_ids))
                )
        self.notes = tuple(notas)

        salida: list[Arrival] = []
        for order_id, ref in pares:
            resp = self._http.get(
                f"/orders/{ref}/tracking", headers=auth_headers(self._creds)
            )
            estado = getattr(resp, "status_code", 200)
            if estado == 404:
                continue
            if estado != 200:
                raise ShippitError(
                    f"Shippit contesto {estado} consultando la referencia {ref!r}. Una "
                    "consulta fallida no se degrada a 'no llego': eso sacaria la linea "
                    "del denominador de OTIF por un problema de red."
                )
            cuando = delivered_on(resp.json() or {})
            if cuando is not None and (since is None or cuando >= since):
                salida.append(Arrival(order_id, cuando))
        return salida


SERVICE_COVERAGE = ServiceCoverage(
    backend="Shippit (API v3)",
    delivered_qty=FieldCoverage(
        status=COVERAGE_UNAVAILABLE,
        source="",
        means=(
            "Shippit mueve bultos, no lineas de pedido: la respuesta de tracking no trae "
            "ni SKU ni cantidad. Cuanto se sirvio lo pone el ERP."
        ),
    ),
    promised_date=FieldCoverage(
        status=COVERAGE_UNAVAILABLE,
        source="",
        means=(
            "El transportista no conoce la promesa comercial. `expected_delivery_date` es "
            "la estimacion de Shippit, no lo que el vendedor prometio, y no se usa."
        ),
    ),
    delivered_date=FieldCoverage(
        status=COVERAGE_MEASURED,
        source="response.track[status=Completed].date",
        means=(
            "La fecha del primer evento Completed: cuando el envio llego al cliente, no "
            "cuando salio del deposito."
        ),
    ),
)


def live_http(*, timeout: float = 30.0):
    """El cliente httpx apuntado a Shippit. **No lleva credenciales.**"""
    import httpx

    return httpx.Client(base_url=API_BASE, timeout=timeout)
```

- [ ] **Step 4: Correr y verificar que pasa**

```bash
.venv/Scripts/python.exe -m pytest tests/test_connector_shippit.py -q
```

Esperado: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add src/connectors/shippit.py tests/test_connector_shippit.py
git commit -m "feat(connectors): adaptador de llegada Shippit"
```

---

### Task 2: Sendle -- un estado no es una fecha

**Files:**
- Create: `src/connectors/sendle.py`
- Test: `tests/test_connector_sendle.py`

**Interfaces:**
- Consumes: lo mismo que Task 1.
- Produces:
  - `delivered_on(payload: dict) -> str | None` -- recibe el cuerpo (sin envoltorio)
  - `SendleArrivals(http, creds: SendleCredentials, refs: Mapping[str, str], *, order_ids=None, max_orders=500)` con `.arrivals(*, since=None)` y `.notes`
  - `SendleCredentials(sendle_id: str, api_key: str)`
  - `SERVICE_COVERAGE`, `SendleError(RuntimeError)`
  - `API_BASE = "https://api.sendle.com"`, `SANDBOX_BASE = "https://sandbox.sendle.com"`, `DELIVERED = "delivered"`

- [ ] **Step 1: Escribir los tests que fallan**

```python
"""Sendle -- el tercer transportista del canal AU/NZ."""
from __future__ import annotations

import pytest

from src.connectors.sendle import (
    SendleArrivals,
    SendleCredentials,
    SendleError,
    delivered_on,
)


def test_la_llegada_sale_del_evento_delivered():
    payload = {
        "state": "Delivered",
        "tracking_events": [
            {"event_type": "Pickup", "scan_time": "2024-04-18T10:00:00+10:00"},
            {"event_type": "In Transit", "scan_time": "2024-04-20T08:00:00+10:00"},
            {"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11+10:00"},
        ],
    }
    assert delivered_on(payload) == "2024-04-22"


def test_un_estado_entregado_sin_evento_no_da_fecha():
    """LA TRAMPA DE SENDLE. `state` es terminal y NO trae marca de tiempo. Un estado no
    es una fecha: antes 'no se cuando llego' que una fecha inventada, porque la inventada
    entra al OTIF y nadie la vuelve a mirar."""
    assert delivered_on({"state": "Delivered", "tracking_events": []}) is None


def test_un_evento_posterior_no_corre_la_fecha_de_entrega():
    payload = {
        "state": "Delivered",
        "tracking_events": [
            {"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11+10:00"},
            {"event_type": "Delivered", "scan_time": "2024-04-30T08:00:00+10:00"},
        ],
    }
    assert delivered_on(payload) == "2024-04-22"


def test_un_intento_de_retiro_no_es_una_entrega():
    """`Pickup Attempted` contiene la palabra que no hay que buscar. Se compara el
    event_type entero, no una subcadena."""
    payload = {"tracking_events": [
        {"event_type": "Pickup Attempted", "scan_time": "2024-04-18T10:00:00Z"},
    ]}
    assert delivered_on(payload) is None


def test_sin_eventos_no_hay_fecha():
    assert delivered_on({}) is None


class _Http:
    class _R:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def json(self):
            return self._payload

    def __init__(self, por_llave: dict, status: int = 200):
        self.por_llave, self._status = por_llave, status
        self.consultadas: list[str] = []

    def get(self, path, *, params=None, headers=None):
        llave = path.rsplit("/", 1)[-1]
        self.consultadas.append(llave)
        if llave not in self.por_llave:
            return _Http._R({}, 404)
        return _Http._R(self.por_llave[llave], self._status)


_CREDS = SendleCredentials(sendle_id="mi-id", api_key="secreta")
_ENTREGADO = {"tracking_events": [
    {"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11Z"},
]}


def test_se_consulta_por_la_referencia_de_sendle_y_se_devuelve_la_del_erp():
    http = _Http({"S3ND1E": _ENTREGADO})
    fuente = SendleArrivals(http, _CREDS, {"SO-1": "S3ND1E"})

    llegadas = fuente.arrivals()

    assert http.consultadas == ["S3ND1E"]
    assert [(a.order_id, a.arrived_on) for a in llegadas] == [("SO-1", "2024-04-22")]


def test_una_orden_sin_referencia_se_declara_y_no_se_calla():
    http = _Http({"S3ND1E": _ENTREGADO})
    fuente = SendleArrivals(http, _CREDS, {"SO-1": "S3ND1E"}, order_ids=["SO-1", "SO-2", "SO-3"])

    fuente.arrivals()

    assert any("2 de 3" in n for n in fuente.notes), fuente.notes


def test_un_error_de_red_no_se_degrada_a_no_llego():
    fuente = SendleArrivals(_Http({"S3ND1E": {}}, status=500), _CREDS, {"SO-1": "S3ND1E"})
    with pytest.raises(SendleError):
        fuente.arrivals()


def test_la_credencial_va_en_basic_auth_y_no_en_la_url():
    """Sendle usa HTTP Basic: Sendle ID de usuario, API key de password."""
    import base64

    from src.connectors.sendle import auth_headers

    cabeceras = auth_headers(_CREDS)
    esperado = base64.b64encode(b"mi-id:secreta").decode()

    assert cabeceras["Authorization"] == f"Basic {esperado}"
    assert cabeceras["Accept"] == "application/json"


def test_el_sandbox_es_una_base_distinta():
    """Sendle cobra: correr contra produccion crea envios reales. La base de sandbox
    existe para que probar no cueste plata."""
    from src.connectors.sendle import API_BASE, SANDBOX_BASE

    assert SANDBOX_BASE != API_BASE
    assert "sandbox" in SANDBOX_BASE
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_connector_sendle.py -q
```

Esperado: FAIL con `ModuleNotFoundError`.

- [ ] **Step 3: Escribir `src/connectors/sendle.py`**

Misma forma que Task 1. Diferencias:

```python
API_BASE = "https://api.sendle.com"
SANDBOX_BASE = "https://sandbox.sendle.com"
DELIVERED = "delivered"
#: Sendle limita a 10 requests por segundo por IP. Con lecturas secuenciales no se roza,
#: y se documenta para que nadie paralelice sin saberlo.
RATE_LIMIT_PER_SECOND = 10


@dataclass(frozen=True)
class SendleCredentials:
    sendle_id: str
    api_key: str


def auth_headers(creds: SendleCredentials) -> dict[str, str]:
    """HTTP Basic: Sendle ID de usuario, API key de password."""
    par = f"{creds.sendle_id}:{creds.api_key}".encode()
    return {
        "Authorization": f"Basic {base64.b64encode(par).decode()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def delivered_on(payload: dict) -> str | None:
    """La fecha del PRIMER evento ``Delivered``, o ``None``.

    ``state`` NO se lee: es un estado terminal sin marca de tiempo.
    """
    eventos = (payload or {}).get("tracking_events") or []
    fechas = [
        fecha
        for ev in eventos
        if str((ev or {}).get("event_type") or "").strip().lower() == DELIVERED
        and (fecha := _iso((ev or {}).get("scan_time"))) is not None
    ]
    return min(fechas) if fechas else None
```

El resto de `SendleArrivals` es identico a `ShippitArrivals` salvo el path (`f"/api/tracking/{ref}"`) y el tipo de excepcion.

`SERVICE_COVERAGE`: `delivered_qty` y `promised_date` UNAVAILABLE con el motivo de Sendle; `delivered_date` MEASURED con `source="tracking_events[event_type=Delivered].scan_time"` y un `means` que diga explicitamente que **no** sale de `state`.

- [ ] **Step 4: Correr y verificar que pasa**

```bash
.venv/Scripts/python.exe -m pytest tests/test_connector_sendle.py -q
```

Esperado: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add src/connectors/sendle.py tests/test_connector_sendle.py
git commit -m "feat(connectors): adaptador de llegada Sendle"
```

---

### Task 3: Los dos entran al guard de cobertura

**Files:**
- Modify: `tests/test_connector_service_contract.py` (el bloque `ARRIVAL_SOURCES`, alrededor de la linea 680)

**Interfaces:**
- Consumes: `ShippitArrivals`, `ShippitCredentials`, `SendleArrivals`, `SendleCredentials` de las Tasks 1 y 2.
- Produces: nada nuevo; extiende la parametrizacion existente.

Los cinco tests de llegada que ya existen (`test_toda_fuente_de_llegada_declara_los_tres_campos`, `..._declara_ausente_lo_que_un_transportista_no_sabe`, `..._la_fecha_que_declara_medir_efectivamente_llega`, `..._empalmar_un_transportista_cambia_de_donde_sale_la_fecha`) corren automaticamente sobre cada entrada de `ARRIVAL_SOURCES`. Sumar los dos constructores los pone bajo el guard entero sin escribir un test nuevo por adaptador.

- [ ] **Step 1: Escribir el test que falla**

Agregar los dos constructores y el guard transversal de D2:

```python
def _shippit():
    """Un envio entregado el 22, con un evento posterior que NO debe correr la fecha."""
    from src.connectors.shippit import ShippitArrivals, ShippitCredentials

    class _Http:
        class _R:
            def __init__(self, p, s=200):
                self._p, self.status_code = p, s

            def json(self):
                return self._p

        def get(self, path, *, params=None, headers=None):
            return _Http._R({"response": {
                "tracking_number": "PPxyz",
                "success": True,
                "expected_delivery_date": "2024-04-10T09:00:00+10:00",
                "track": [
                    {"status": "In Transit", "date": "2024-04-20T08:00:00+10:00"},
                    {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
                    {"status": "Completed", "date": "2024-04-30T08:00:00+10:00"},
                ],
            }})

    return ShippitArrivals(_Http(), ShippitCredentials(api_key="g"), {"SO-1": "PPxyz"})


def _sendle():
    """Un envio entregado el 22. `state` dice Delivered y la fecha igual sale del evento."""
    from src.connectors.sendle import SendleArrivals, SendleCredentials

    class _Http:
        class _R:
            def __init__(self, p, s=200):
                self._p, self.status_code = p, s

            def json(self):
                return self._p

        def get(self, path, *, params=None, headers=None):
            return _Http._R({
                "state": "Delivered",
                "tracking_events": [
                    {"event_type": "In Transit", "scan_time": "2024-04-20T08:00:00+10:00"},
                    {"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11+10:00"},
                ],
            })

    return SendleArrivals(
        _Http(), SendleCredentials(sendle_id="g", api_key="g"), {"SO-1": "S3ND1E"}
    )


ARRIVAL_SOURCES: tuple[tuple[str, str, Callable[[], object]], ...] = (
    ("starshipit", "src.connectors.starshipit", _starshipit),
    ("shippit", "src.connectors.shippit", _shippit),
    ("sendle", "src.connectors.sendle", _sendle),
)
```

Y el guard transversal, que es lo unico verdaderamente nuevo de esta task:

```python
#: Los transportistas que NO se pueden consultar por la referencia del ERP. Starshipit si
#: (``/api/track?order_number=``), asi que no esta aca: no tiene mapa que le falte.
_CARRIERS_CON_MAPA = ("shippit", "sendle")


@pytest.mark.parametrize("nombre", _CARRIERS_CON_MAPA)
def test_una_orden_sin_llave_de_transportista_se_declara(nombre):
    """Dos silencios que se ven iguales y significan cosas opuestas.

    ``with_arrivals`` saca del denominador de OTIF a toda orden sin llegada. Para
    Starshipit eso es honesto: se pregunto y contesto 404. Para estos dos, una orden sin
    llave produce el MISMO silencio porque nunca se pregunto -- y un OTIF calculado sobre
    'las que casualmente tenian tracking cargado' no es el OTIF de la cartera.
    """
    build = dict((n, b) for n, _, b in ARRIVAL_SOURCES)[nombre]
    fuente = build()
    fuente._order_ids = ["SO-1", "SO-2", "SO-3"]

    fuente.arrivals()

    assert fuente.notes, f"{nombre} se comio 2 ordenes sin llave y no dijo nada"
    assert any("2 de 3" in n for n in fuente.notes), fuente.notes
```

- [ ] **Step 2: Correr y verificar que falla**

```bash
.venv/Scripts/python.exe -m pytest tests/test_connector_service_contract.py -q
```

Esperado: si las Tasks 1 y 2 estan hechas, los cinco tests parametrizados pasan de 4 a 12 casos y el drift guard (`test_every_connector_that_reads_orders_declares_coverage`) ya no falla por los dos modulos nuevos. Antes de la Task 1, FAIL en la coleccion.

- [ ] **Step 3: Correr la bateria entera**

No hay implementacion nueva en esta task; lo que hay que verificar es que los dos modulos nuevos no rompieron nada mas -- en particular el drift guard, que escanea `src/connectors/*.py` buscando `def orders(` o `def arrivals(`.

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

Esperado: PASS. Cero fallos.

- [ ] **Step 4: Commit**

```bash
git add tests/test_connector_service_contract.py
git commit -m "test(connectors): Shippit y Sendle bajo el guard de cobertura"
```

---

## Self-Review

**1. Cobertura de la especificacion.** El handoff pedia "Shippit y Sendle son los otros del canal y no estan; el Protocol existe para que sumarlos sea un adaptador, no un rediseño". Tasks 1 y 2 son los adaptadores; Task 3 los mete bajo el guard. El Protocol no se toca -- verificado en D1.

**2. Placeholders.** Ninguno. Los dos unicos bloques descritos en prosa en vez de codigo (el `SendleArrivals` de Task 2 y su `SERVICE_COVERAGE`) apuntan a un original textual completo en Task 1, que es lo que la regla "no digas 'similar a Task N'" pide evitar -- aca se dice explicitamente que es identico salvo el path y la excepcion, y ambos estan nombrados.

**3. Consistencia de tipos.** `delivered_on(payload: dict) -> str | None` en los dos modulos. `arrivals(*, since=None) -> list[Arrival]` coincide con el Protocol de `arrivals.py:70`. `notes: tuple[str, ...]` coincide con `StarshipitArrivals.notes`, que es lo que `service_scan.py` ya lee.

**Riesgo abierto y declarado:** el guard de Task 3 toca `fuente._order_ids`, un atributo privado. Es deliberado -- el fixture compartido de `ARRIVAL_SOURCES` construye la fuente sin `order_ids` porque los otros cuatro tests no lo necesitan, y agregarle un parametro al fixture solo para este caso duplicaria los doce casos parametrizados. Si el atributo se renombra, el test falla ruidosamente, que es el comportamiento correcto.
