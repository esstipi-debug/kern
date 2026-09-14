"""La app del App Store de Shopify: OAuth, webhooks de GDPR y Billing (brecha L2).

Lo que existia hasta acá era una CUSTOM APP: el comerciante la crea en su propio
admin y entrega un Admin API access token. Sirve para uno a uno y no se lista en
ningún lado. La cabecera de ``src/connectors/shopify_connect.py`` ya nombraba lo
que falta para la otra: *"OAuth, app embebida, los tres webhooks obligatorios de
GDPR y Billing API"*. Este modulo es esa mitad de protocolo.

DOS HMAC DISTINTOS, Y CONFUNDIRLOS ES EL BUG CLASICO. Shopify firma dos cosas de
dos maneras que no se parecen:

  OAuth callback -> HMAC-SHA256 HEX sobre los parametros de QUERY ordenados,
                    unidos como ``k=v&k=v``, sin el propio ``hmac``.
  Webhooks       -> HMAC-SHA256 en BASE64 sobre el CUERPO CRUDO, tal cual llego.

Usar el esquema equivocado no falla ruidosamente: falla como "firma invalida"
para todo, y el siguiente paso natural es aflojar la verificacion. Por eso son
dos funciones con nombres distintos y ningun parametro compartido.

EL DOMINIO SE VALIDA DURO, NO SE NORMALIZA Y YA. ``normalize_shop_domain`` (de
shopify_connect) existe para que el comerciante pueda pegar cualquier forma de su
tienda, y a proposito deja pasar un dominio publico personalizado TAL CUAL. Eso
es correcto para leer una tienda y seria un AGUJERO acá: el ``shop`` llega por
query string y termina dentro de una URL a la que se redirige al comerciante. Sin
un gate que exija la forma ``*.myshopify.com``, es un open redirect regalado. Por
eso :func:`shop_valido` normaliza y DESPUES exige la forma canonica.

NADA DE ESTE MODULO SALE A LA RED POR SU CUENTA. Las dos llamadas que si son
salientes -- canjear el code por un token y crear el cargo -- reciben el cliente
HTTP inyectado, mismo patron que ``src/connectors/http_client.py`` y que
``jobs/notify.py``: en un test entra un doble y no hay red.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from src.connectors.shopify_connect import REQUIRED_SCOPES, normalize_shop_domain

#: La unica forma de dominio que esta app acepta para construir una URL.
_CANONICO = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

#: Los tres webhooks que Shopify EXIGE a toda app publica. No son opcionales y su
#: ausencia (o una respuesta que no sea 2xx) desaprueba la app entera.
GDPR_TOPICS = ("customers/data_request", "customers/redact", "shop/redact")

#: Ventana del ``timestamp`` del callback de OAuth. El HMAC prueba autenticidad,
#: no frescura: sin esto, un callback valido capturado una vez sirve para siempre.
DEFAULT_MAX_AGE = 300


class ShopifyAppError(RuntimeError):
    """Dominio invalido, firma que no verifica, o respuesta que no se entiende.

    Un solo tipo: la RESPUESTA al emisor no debe distinguir los casos -- decirle
    a quien prueba firmas cual fallo le entrega un oraculo. El detalle va al log.
    """


def shop_valido(crudo: str) -> str:
    """``mitienda`` / la URL del admin / lo que sea -> ``mitienda.myshopify.com``.

    Levanta si lo que queda no es un dominio de tienda. Ver el docstring del
    modulo: sin este gate el ``shop`` de la query entra en una URL de redireccion.
    """
    dominio = normalize_shop_domain(crudo or "")
    if not _CANONICO.match(dominio):
        raise ShopifyAppError(f"no es un dominio de tienda de Shopify: {crudo!r}")
    return dominio


def install_url(
    shop: str, *, api_key: str, redirect_uri: str, state: str,
    scopes: tuple[str, ...] = REQUIRED_SCOPES,
) -> str:
    """La URL de autorizacion a la que se manda al comerciante.

    ``state`` es obligatorio y sin valor por defecto a proposito: es lo unico que
    ata el callback a la sesion que lo inicio, y un default lo volveria adivinable.
    """
    if not api_key:
        raise ShopifyAppError("falta la API key de la app")
    if not state:
        raise ShopifyAppError("falta el state: sin el, el callback no es atribuible")
    consulta = urlencode({
        "client_id": api_key,
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
    })
    return f"https://{shop_valido(shop)}/admin/oauth/authorize?{consulta}"


def verify_oauth_callback(
    params: dict[str, str], secret: str, *, state_esperado: str,
    now: float | None = None, max_age: int = DEFAULT_MAX_AGE,
) -> str:
    """Verifica el callback de OAuth y devuelve el dominio de la tienda.

    Cuatro cosas, y las cuatro hacen falta: la firma (HEX sobre la query), la
    frescura del ``timestamp``, que el ``state`` sea el que se emitio, y que el
    ``shop`` sea un dominio de tienda de verdad.
    """
    if not secret:
        # Sin secreto NO se acepta nada. La convencion del repo de "variable
        # vacia apaga la funcion" apagaria acá la verificacion, no el endpoint.
        raise ShopifyAppError("no hay secreto de app configurado")

    recibido = params.get("hmac", "")
    if not recibido:
        raise ShopifyAppError("callback sin hmac")
    # `signature` es el parametro legado del esquema viejo; Shopify indica
    # excluirlo del calculo igual que `hmac`.
    firmables = {k: v for k, v in params.items() if k not in ("hmac", "signature")}
    mensaje = "&".join(f"{k}={firmables[k]}" for k in sorted(firmables))
    esperado = hmac.new(secret.encode("utf-8"), mensaje.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, recibido):
        raise ShopifyAppError("la firma del callback no verifica")

    if not hmac.compare_digest(params.get("state", ""), state_esperado or ""):
        raise ShopifyAppError("el state no coincide con el emitido")

    try:
        emitido = int(params.get("timestamp", ""))
    except ValueError as exc:
        raise ShopifyAppError("timestamp ausente o no numerico") from exc
    if abs((time.time() if now is None else now) - emitido) > max_age:
        raise ShopifyAppError(f"callback fuera de la ventana de {max_age}s")

    return shop_valido(params.get("shop", ""))


def verify_webhook(cuerpo: bytes, cabecera_hmac: str, secret: str) -> None:
    """Verifica un webhook de Shopify. BASE64 sobre el cuerpo CRUDO.

    El cuerpo tiene que ser los bytes exactos que llegaron: re-serializar el JSON
    cambia separadores y orden, la firma deja de verificar, y el arreglo tentador
    es comparar el JSON en vez de la firma.
    """
    if not secret:
        raise ShopifyAppError("no hay secreto de app configurado")
    if not cabecera_hmac:
        raise ShopifyAppError("webhook sin cabecera X-Shopify-Hmac-Sha256")
    digest = hmac.new(secret.encode("utf-8"), cuerpo, hashlib.sha256).digest()
    esperado = base64.b64encode(digest).decode("ascii")
    if not hmac.compare_digest(esperado, cabecera_hmac):
        raise ShopifyAppError("la firma del webhook no verifica")


@dataclass(frozen=True)
class Instalacion:
    """El resultado de un canje exitoso."""

    shop: str
    access_token: str
    scopes: tuple[str, ...]


def exchange_code(shop: str, code: str, *, api_key: str, api_secret: str, http: Any) -> Instalacion:
    """Canjea el ``code`` del callback por un Admin API token.

    ``http`` es cualquier objeto con ``.post()`` estilo httpx -- el mismo patron
    inyectable de ``src/connectors/http_client.py``. Este modulo no abre una
    conexion por su cuenta ni en produccion ni en un test.
    """
    dominio = shop_valido(shop)
    if not code:
        raise ShopifyAppError("callback sin code")
    respuesta = http.post(
        f"https://{dominio}/admin/oauth/access_token",
        json={"client_id": api_key, "client_secret": api_secret, "code": code},
    )
    if getattr(respuesta, "status_code", 0) != 200:
        # El cuerpo NO se incluye: puede traer el secreto que se acaba de enviar.
        raise ShopifyAppError(f"Shopify rechazo el canje del code (HTTP {getattr(respuesta, 'status_code', '?')})")
    datos = respuesta.json()
    token = (datos or {}).get("access_token", "")
    if not token:
        raise ShopifyAppError("la respuesta del canje no trae access_token")
    return Instalacion(
        shop=dominio,
        access_token=token,
        scopes=tuple(s for s in (datos.get("scope") or "").split(",") if s),
    )


#: La mutation de Billing. Se escribe entera acá y no se arma por concatenacion:
#: una GraphQL mal formada vuelve como 200 con `errors` adentro, que es
#: exactamente el modo de falla silenciosa que este repo persigue.
_SUBSCRIPTION_MUTATION = """
mutation crear($name: String!, $url: URL!, $price: Decimal!, $test: Boolean!) {
  appSubscriptionCreate(
    name: $name
    returnUrl: $url
    test: $test
    lineItems: [{plan: {appRecurringPricingDetails: {price: {amount: $price, currencyCode: USD}}}}]
  ) {
    confirmationUrl
    userErrors { field message }
  }
}
"""


def create_subscription(
    shop: str, access_token: str, *, nombre: str, precio_usd: str, return_url: str,
    http: Any, api_version: str = "2026-07", test: bool = False,
) -> str:
    """Crea el cargo recurrente y devuelve la URL de confirmacion del comerciante.

    ``test=True`` crea un cargo de prueba (no cobra). Es un parametro explicito y
    sin default verdadero: un cargo de prueba en produccion es una app que no
    factura, y el modo de falla es que nadie lo note hasta el cierre de mes.
    """
    dominio = shop_valido(shop)
    if not access_token:
        raise ShopifyAppError("falta el token de la tienda")
    respuesta = http.post(
        f"https://{dominio}/admin/api/{api_version}/graphql.json",
        headers={"X-Shopify-Access-Token": access_token, "Content-Type": "application/json"},
        json={
            "query": _SUBSCRIPTION_MUTATION,
            "variables": {"name": nombre, "url": return_url, "price": precio_usd, "test": test},
        },
    )
    if getattr(respuesta, "status_code", 0) != 200:
        raise ShopifyAppError(f"Billing API respondio HTTP {getattr(respuesta, 'status_code', '?')}")
    cuerpo = respuesta.json() or {}
    # Un 200 con `errors` o con `userErrors` NO es un exito: la GraphQL de Shopify
    # devuelve 200 para casi todo y el error viaja adentro del cuerpo.
    if cuerpo.get("errors"):
        raise ShopifyAppError(f"Billing API devolvio errores: {cuerpo['errors']}")
    creado = ((cuerpo.get("data") or {}).get("appSubscriptionCreate")) or {}
    if creado.get("userErrors"):
        raise ShopifyAppError(f"Billing API rechazo el cargo: {creado['userErrors']}")
    url = creado.get("confirmationUrl")
    if not url:
        raise ShopifyAppError("Billing API no devolvio confirmationUrl")
    return str(url)
