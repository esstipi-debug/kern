"""Opt-in security layer for the Inventory Planner web app.

Three controls, all configured by environment variable so local/dev use is
unchanged out of the box:

* **Security headers + path-aware CSP** — always on. The dashboard gets a strict
  policy; only the `/console` React/Babel prototype gets the relaxed allowances
  it needs (unpkg CDN + runtime ``eval``).
* **Rate limiting** — sliding window per client IP. Disabled unless
  ``LINCHPIN_RATE_LIMIT`` > 0.
* **API-key gate** — required only when ``LINCHPIN_API_KEY`` is set.

Config is read at *call time* via module globals so tests (and a reload) can
flip it with ``monkeypatch.setattr(security, "API_KEY", ...)``.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from nosilo_core.access import PolicyEngine, PolicySet
from nosilo_core.writeback import UNSIGNED_APPROVALS_ENV, unsigned_approvals_allowed
from webapp import principal


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---- configuration (module-level so it stays monkeypatchable) -----------------
RATE_LIMIT = _int_env("LINCHPIN_RATE_LIMIT", 0)  # max requests per window; 0 disables
RATE_WINDOW = _int_env("LINCHPIN_RATE_WINDOW", 60)  # window length, seconds
#: La variable del contrato self-serve, y la que este deploy ya tiene como
#: secret. La legada gana si estan las dos: solo puede pasar en el deploy del
#: operador, y ahi la vieja es la configurada. Mismo criterio conservador que
#: KERN_DATA_DIR -- lo que produccion ya setea sigue mandando.
API_KEY_ENV_CONTRATO = "KERN_API_KEY"
API_KEY_ENV_LEGADO = "LINCHPIN_API_KEY"


def leer_api_key() -> str:
    """El valor de la key, mire donde mire. Vacio = sin gate."""
    for nombre in (API_KEY_ENV_LEGADO, API_KEY_ENV_CONTRATO):
        if nombre in os.environ:
            valor = os.environ[nombre].strip()
            if valor:
                return valor
    return ""


def estado_api_key() -> str:
    """``configurada`` | ``apagada_a_proposito`` | ``sin_configurar``.

    Los tres, no dos, porque la spec de empaquetado lo pide asi: *"la diferencia
    entre 'no configuraste' y 'configuraste vacio' es explicita"*. Un
    duenno-operador solo en su LAN que apaga la auth a proposito no tiene que
    recibir la misma alarma que alguien que se la olvido y expuso el puerto.
    """
    if leer_api_key():
        return "configurada"
    declarada = any(n in os.environ for n in (API_KEY_ENV_LEGADO, API_KEY_ENV_CONTRATO))
    return "apagada_a_proposito" if declarada else "sin_configurar"


API_KEY = leer_api_key()  # empty disables the gate
CORS_ORIGINS = [o.strip() for o in os.environ.get("LINCHPIN_CORS_ORIGINS", "").split(",") if o.strip()]
ENV = os.environ.get("LINCHPIN_ENV", "development").strip().lower()  # "production" tightens checks
REQUIRE_SECURE = os.environ.get("LINCHPIN_REQUIRE_SECURE", "").strip().lower() in ("1", "true", "yes")
# Signs writeback Approvals (nosilo_core/writeback.py) so they can't be forged by
# constructing the dataclass directly. NOT the "empty disables" convention above:
# empty means every approval-gated write is refused (fail closed).
APPROVAL_SECRET = os.environ.get("LINCHPIN_APPROVAL_SECRET", "").strip()
# The development opt-in that lets apply() take unsigned approvals while no secret
# is set. Read through nosilo_core so what counts as "on" lives in one place.
ALLOW_UNSIGNED_APPROVALS = unsigned_approvals_allowed()


#: The deployment's access policy. ``None`` means ungoverned: every principal is
#: permitted every action and no field classification is applied, which is the
#: shipped default and what keeps this layer from changing existing behaviour.
#: Set at boot by a deployment that wants authorisation. Module-level so tests
#: (and a reload) can flip it, same convention as the gates above.
POLICY: PolicySet | None = None


def policy_engine() -> PolicyEngine:
    """The decision point, built from :data:`POLICY` at call time."""
    return PolicyEngine(POLICY)


def production_warnings() -> list[str]:
    """Misconfiguration warnings for a production deploy.

    Empty in development (or when production is fully secured). The app logs these
    loudly at startup and, when ``LINCHPIN_REQUIRE_SECURE`` is set, refuses to
    boot - so an unauthenticated public deploy fails *loud*, not silent.
    """
    if ENV != "production":
        return []
    out: list[str] = []
    if not API_KEY:
        out.append("LINCHPIN_API_KEY is not set - POST /api/jobs is unauthenticated")
    if POLICY is None:
        out.append(
            "webapp.security.POLICY is not set - no access policy is configured, so every "
            "principal is permitted every action and no field classification is applied"
        )
    if principal.TRUSTED_IDENTITY_HEADERS:
        # Reading identity from client-settable headers is a complete
        # authentication bypass unless a reverse proxy strips the client's own
        # copy first. That requirement lives outside this codebase, so the most
        # this can do is refuse to let it be forgotten.
        out.append(
            "LINCHPIN_TRUSTED_IDENTITY_HEADERS is on - X-Auth-Subject is taken at face value. "
            "This is safe ONLY behind a proxy that terminates auth and strips client-supplied "
            "X-Auth-* headers; without one, any caller can claim any identity"
        )
        if not API_KEY:
            out.append(
                "LINCHPIN_TRUSTED_IDENTITY_HEADERS is on with no LINCHPIN_API_KEY - there is "
                "nothing at all stopping a caller from asserting its own identity"
            )
    if RATE_LIMIT <= 0:
        out.append("LINCHPIN_RATE_LIMIT is 0 - POST /api/jobs is not rate limited")
    if not APPROVAL_SECRET:
        out.append(
            "LINCHPIN_APPROVAL_SECRET is not set - nosilo_core.writeback refuses every "
            "approval-gated write (Odoo, Shopify, Excel, autonomy promotions) until it is"
        )
    if ALLOW_UNSIGNED_APPROVALS:
        out.append(
            f"{UNSIGNED_APPROVALS_ENV} is on - a development and test switch: while "
            "LINCHPIN_APPROVAL_SECRET is unset, any code with access to "
            "nosilo_core.writeback can sign an approval by hand and apply() takes it"
        )
    return out


def banner_de_arranque() -> list[str]:
    """Lo que se grita al arrancar cuando `POST /api/jobs` queda sin gate.

    Precondicion 4 de la spec de empaquetado: *arranque sin API key = banner
    explicito, no silencio ni crash*.

    Por que no alcanzaba `production_warnings()`: esa lista arranca con
    ``if ENV != "production": return []``, y un cliente que corre `docker run`
    no setea `LINCHPIN_ENV`. O sea que el unico aviso que existia estaba apagado
    justo en el caso que importa. Medido antes de esto, importando `webapp.app`
    con el entorno limpio: cero lineas de log sobre seguridad.

    Este banner NO mira `ENV`, y no reemplaza a `production_warnings()` -- que
    sigue igual porque `LINCHPIN_REQUIRE_SECURE` se niega a bootear cuando esa
    lista trae algo, y volverla no vacia en development habria roto el arranque
    de cualquiera que la tuviera puesta.

    Devuelve [] cuando hay key. Nunca levanta: la spec pide banner, no crash.
    """
    estado = estado_api_key()
    if estado == "configurada":
        return []

    if estado == "apagada_a_proposito":
        return [
            "Kern arranca SIN AUTENTICACION, por configuracion explicita "
            f"({API_KEY_ENV_CONTRATO} esta declarada y vacia).",
            "POST /api/jobs y las descargas de deliverables quedan abiertos a "
            "cualquiera que alcance este puerto.",
        ]

    return [
        "=" * 72,
        "Kern arranca SIN AUTENTICACION: no hay API key configurada.",
        "",
        "POST /api/jobs corre trabajos contra tus datos y GET /jobs-output/* "
        "sirve los entregables. Ahora mismo los dos estan abiertos a cualquiera "
        "que alcance este puerto.",
        "",
        "Para cerrarlo:",
        f"    docker run -p 8000:8000 -e {API_KEY_ENV_CONTRATO}=<una-clave-larga> ...",
        "",
        f"Para dejarlo abierto a proposito, declara {API_KEY_ENV_CONTRATO} vacia "
        "y este aviso baja a una linea.",
        "=" * 72,
    ]


def emitir_banner_de_arranque() -> None:
    """Manda el banner al log. Lo llama `webapp/app.py` al importar.

    WARNING cuando falta la key (es un olvido posible), INFO cuando esta apagada
    a proposito (es una decision, y repetirla en WARNING entrena a ignorar los
    warnings).
    """
    lineas = banner_de_arranque()
    if not lineas:
        return
    log = logging.getLogger("linchpin.security")
    nivel = logging.WARNING if estado_api_key() == "sin_configurar" else logging.INFO
    for linea in lineas:
        log.log(nivel, "%s", linea)


def aviso_de_escrituras() -> list[str]:
    """Lo que se dice al arrancar sobre las escrituras que necesitan aprobacion.

    ``nosilo_core.writeback.apply()`` falla cerrado: sin ``LINCHPIN_APPROVAL_SECRET``
    rechaza toda escritura que necesita aprobacion, porque una aprobacion firmada con
    clave vacia la puede reproducir cualquiera. La app igual arranca y sirve
    lecturas; esto dice por que la primera aprobacion va a rebotar, antes de que
    rebote.

    Canal aparte del banner de la API key, a proposito: la precondicion 4 de la spec
    de empaquetado es sobre la key, y `banner_de_arranque()` no cambia. Devuelve []
    con secreto (ahi el opt-in de desarrollo es inerte). Nunca levanta.
    """
    if APPROVAL_SECRET:
        return []
    if ALLOW_UNSIGNED_APPROVALS:
        return [
            f"{UNSIGNED_APPROVALS_ENV} esta activo y no hay LINCHPIN_APPROVAL_SECRET: las "
            "aprobaciones no se firman, y cualquier codigo con acceso a "
            "nosilo_core.writeback puede fabricar una. Solo para desarrollo y tests.",
        ]
    return [
        "Sin LINCHPIN_APPROVAL_SECRET: Kern lee y calcula, pero rechaza toda escritura "
        "que necesita aprobacion (Odoo, Shopify, Excel, promociones de autonomia) hasta "
        "que la configures.",
    ]


def emitir_aviso_de_escrituras() -> None:
    """Manda el aviso al log, en WARNING. Lo llama `webapp/app.py` al importar.

    WARNING en los dos estados, tambien en el seguro, por el criterio de
    `emitir_banner_de_arranque`: un secreto que falta es un olvido posible, y quien
    quiere escribir se tiene que enterar aca y no en el primer rechazo. Ademas es lo
    unico que se ve: `observability.configure_logging()` solo configura
    `linchpin.access`, asi que este logger cae en el handler de ultimo recurso de
    Python, que imprime de WARNING para arriba. Medido arrancando la app con
    LINCHPIN_LOG_LEVEL=INFO: una linea INFO aca no sale.
    """
    log = logging.getLogger("linchpin.security")
    for linea in aviso_de_escrituras():
        log.warning("%s", linea)


# ---- rate-limiter state -------------------------------------------------------
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limit() -> None:
    """Drop all rate-limiter state (used by tests for isolation)."""
    _BUCKETS.clear()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "anon"


def rate_limit(request: Request, *, key: str | None = None) -> None:
    """FastAPI dependency: 429 once a client exceeds RATE_LIMIT within RATE_WINDOW.

    ``key`` overrides the default per-IP bucket (``_client_key(request)``) - pass
    a caller identity (e.g. an authenticated MCP client's name) to give that
    caller its own quota instead of sharing one bucket with every other request
    from the same source IP (NAT, a corporate proxy, or another tenant behind the
    same egress address would otherwise throttle each other). Callers that don't
    pass ``key`` are unaffected - same IP-keyed behavior as before.
    """
    limit = RATE_LIMIT
    if limit <= 0:  # feature off
        return
    window = RATE_WINDOW
    now = time.monotonic()
    bucket = _BUCKETS[key if key is not None else _client_key(request)]
    cutoff = now - window
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(bucket[0] + window - now))
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


def require_api_key(request: Request) -> None:
    """FastAPI dependency: 401 unless ``X-API-Key`` matches LINCHPIN_API_KEY.

    A no-op when no key is configured (the shipped default), so the app stays
    open for local use.
    """
    expected = API_KEY
    if not expected:  # gate off
        return
    provided = request.headers.get("x-api-key", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


async def principal_middleware(request: Request, call_next):
    """Attach the caller's identity and starting authority to every request.

    Runs for every route, including the ones with no auth dependency, because
    "who was this" is as useful on an anonymous request as on an authenticated
    one - an audit entry that only exists for authenticated calls cannot answer
    what an unauthenticated caller did.

    Attaches, never enforces. Authorisation is the policy engine's job at the
    point of access; a middleware that returned 403 here would have to guess the
    action and resource from the URL, which is exactly the kind of guess that
    goes wrong quietly. Handlers read ``request.state.principal``.

    Cannot fail the request: identity derivation is wrapped so a malformed header
    degrades to anonymous rather than turning into a 500.
    """
    try:
        who = principal.principal_from_request(request, api_key=API_KEY)
    except Exception:  # pragma: no cover - defensive; derivation is already total
        who = principal.anonymous()
    request.state.principal = who
    request.state.capability = principal.capability_for(who)
    return await call_next(request)


def request_principal(request: Request):
    """The principal attached by :func:`principal_middleware`.

    Falls back to anonymous rather than raising, so a handler exercised without
    the middleware (a unit test, a sub-application) behaves like an
    unauthenticated call instead of blowing up.
    """
    return getattr(request.state, "principal", None) or principal.anonymous()


def request_capability(request: Request):
    """The starting authority attached by :func:`principal_middleware`."""
    existing = getattr(request.state, "capability", None)
    return existing if existing is not None else principal.capability_for(request_principal(request))


async def jobs_output_auth_middleware(request: Request, call_next):
    """Gate ``GET /jobs-output/*`` behind the same shared ``LINCHPIN_API_KEY`` as
    ``POST /api/jobs``. Today's "access control" on a deliverable download is pure
    obscurity - an unguessable ``tempfile.mkdtemp()`` directory name, no expiry, no
    revocation - so once an operator opts into API-key auth for submitting jobs,
    fetching their output should require the same key, not just the URL.

    No-op when ``API_KEY`` is unset (the shipped default), same convention as
    ``require_api_key`` - this only tightens behavior for operators who already
    enabled the gate.
    """
    if request.url.path.startswith("/jobs-output/") and API_KEY:
        provided = request.headers.get("x-api-key", "")
        if not hmac.compare_digest(provided, API_KEY):
            return JSONResponse({"detail": "invalid or missing API key"}, status_code=401)
    return await call_next(request)


# ---- security headers + path-aware CSP ----------------------------------------
_BASE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)
# The /console prototype boots React + ReactDOM + Babel-standalone from unpkg and
# compiles JSX in the browser, which needs 'unsafe-eval' and the CDN origin. Scope
# those allowances to that surface only — the dashboard keeps the strict policy.
_CONSOLE_CSP = _BASE_CSP.replace(
    "script-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com",
)


def csp_for_path(path: str) -> str:
    if path.startswith("/console") or path.startswith("/static/prototype"):
        return _CONSOLE_CSP
    return _BASE_CSP


async def security_headers_middleware(request: Request, call_next):
    """Attach hardening headers to every response (idempotent via setdefault)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", csp_for_path(request.url.path))
    return response
