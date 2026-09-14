"""Verificacion del webhook de pago y extraccion de la compra (brecha L3).

Hasta acá el billing era una superficie muerta: ``webapp/billing.py`` lo declara
en su propio docstring -- *"it does NOT call the Stripe API"* -- y resolvia una
variable ``STRIPE_LINK_<SLUG>`` puesta a mano. No existia el evento que diera de
alta una credencial sola, asi que cada venta terminaba en un operador emitiendo
la key a mano (``examples/issue_mcp_key.py``).

SIN SDK, Y NO POR AHORRO. Verificar la firma de Stripe es HMAC-SHA256 sobre
``"{t}.{cuerpo crudo}"``: ``hmac`` y ``hashlib`` de la stdlib alcanzan. Meter el
SDK de Stripe para esto agregaria una dependencia con acceso a red a la ruta que
recibe dinero, y el Dockerfile de produccion no la instala. La promesa que
``billing.py`` ya hacia ("no new Stripe SDK dependency") se mantiene.

EL CUERPO CRUDO, NO EL JSON REPARSEADO. La firma cubre los bytes exactos que
Stripe envio. Serializar de nuevo el dict cambia separadores, orden y escapes, y
la verificacion falla -- o peor, alguien "arregla" eso comparando el JSON en vez
de la firma. Por eso :func:`verify_signature` toma ``bytes`` y el llamador tiene
que leer el body sin tocarlo.

LO QUE ESTE MODULO NO HACE: no llama a la API de Stripe, no crea sesiones de
checkout, no mueve dinero. Solo verifica lo que llego y dice que compra es.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass

#: Ventana de tolerancia del timestamp firmado, en segundos. La misma que usa
#: Stripe por defecto. Sin ella, una firma valida capturada una vez sirve para
#: siempre: el HMAC prueba autenticidad, no frescura.
DEFAULT_TOLERANCE = 300

#: El unico evento que da de alta una credencial. Se nombra explicitamente en vez
#: de aceptar cualquier cosa con "completed": Stripe manda decenas de tipos por
#: el mismo endpoint y un `in` demasiado laxo emite keys por eventos que no son
#: una compra.
PURCHASE_EVENT = "checkout.session.completed"


class SignatureError(ValueError):
    """La firma no verifica: ausente, mal formada, vencida o incorrecta.

    Un solo tipo para los cuatro casos a proposito. El detalle sirve para el log
    del operador, pero la RESPUESTA al emisor no debe distinguirlos: decirle a
    quien prueba firmas cual de los cuatro fallo le entrega un oraculo.
    """


@dataclass(frozen=True)
class Purchase:
    """Una compra pagada, lista para emitir su credencial."""

    payment_ref: str
    email: str
    client_name: str
    amount_total: int | None
    currency: str | None


def _parse_header(header: str) -> tuple[int, list[str]]:
    """``t=...,v1=...,v1=...`` -> (timestamp, firmas v1).

    Stripe puede mandar VARIAS ``v1`` durante una rotacion de secreto: la vieja y
    la nueva viajan juntas para que el endpoint no se caiga en la transicion.
    Quedarse solo con la primera hace que la rotacion rompa el cobro.
    """
    timestamp: int | None = None
    firmas: list[str] = []
    for parte in (header or "").split(","):
        clave, _, valor = parte.strip().partition("=")
        if clave == "t":
            try:
                timestamp = int(valor)
            except ValueError as exc:
                raise SignatureError(f"timestamp no numerico: {valor!r}") from exc
        elif clave == "v1":
            firmas.append(valor)
    if timestamp is None or not firmas:
        raise SignatureError("cabecera Stripe-Signature ausente o sin t/v1")
    return timestamp, firmas


def verify_signature(
    payload: bytes,
    header: str,
    secret: str,
    *,
    tolerance: int = DEFAULT_TOLERANCE,
    now: float | None = None,
) -> int:
    """Verifica la firma de Stripe sobre ``payload``. Devuelve el timestamp firmado.

    Levanta :class:`SignatureError` si no verifica. Nunca devuelve un booleano:
    un ``if verificar(...)`` que alguien olvide negar falla abierto, y esta es la
    ruta que da credenciales.
    """
    if not secret:
        # Sin secreto configurado NO se acepta nada. La convencion de "variable
        # vacia apaga la funcion" que este repo usa en otros lados (LINCHPIN_API_KEY,
        # notify()) seria aca un fallo abierto: apagaria la verificacion, no el
        # endpoint.
        raise SignatureError("no hay secreto de webhook configurado")
    timestamp, firmas = _parse_header(header)
    if now is None:
        now = time.time()
    if abs(now - timestamp) > tolerance:
        raise SignatureError(f"timestamp fuera de la ventana de {tolerance}s")

    firmado = str(timestamp).encode("ascii") + b"." + payload
    esperada = hmac.new(secret.encode("utf-8"), firmado, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(esperada, f) for f in firmas):
        raise SignatureError("ninguna firma v1 coincide")
    return timestamp


def purchase_from_event(cuerpo: bytes) -> Purchase | None:
    """La compra que este evento representa, o ``None`` si no es una.

    ``None`` cubre tres casos distintos que el llamador trata igual (responder
    200 y no hacer nada): un tipo de evento que no es una compra, una sesion que
    se completo SIN pagarse, y una sesion sin email. Los tres son normales en el
    mismo endpoint.
    """
    try:
        evento = json.loads(cuerpo)
    except json.JSONDecodeError:
        return None
    if not isinstance(evento, dict) or evento.get("type") != PURCHASE_EVENT:
        return None

    sesion = ((evento.get("data") or {}).get("object")) or {}
    if not isinstance(sesion, dict):
        return None
    # `completed` NO implica `paid`: con metodos de pago asincronos la sesion se
    # completa y el dinero llega (o no) despues. Emitir la credencial ahi seria
    # regalar acceso ante un debito que todavia puede fallar.
    if sesion.get("payment_status") != "paid":
        return None

    # La referencia estable de la COMPRA es el id de la sesion, no el del evento:
    # una misma sesion genera varios eventos (completed, async_payment_succeeded)
    # y todos hablan de un unico pago. Anclar en el id del evento emitiria una
    # credencial por evento.
    payment_ref = sesion.get("id")
    detalles = sesion.get("customer_details") or {}
    email = (detalles.get("email") or sesion.get("customer_email") or "").strip()
    if not payment_ref or not email:
        return None

    client_name = (sesion.get("client_reference_id") or "").strip() or email
    return Purchase(
        payment_ref=str(payment_ref),
        email=email,
        client_name=client_name,
        amount_total=sesion.get("amount_total"),
        currency=sesion.get("currency"),
    )
