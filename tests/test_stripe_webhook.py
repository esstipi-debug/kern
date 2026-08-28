"""Brecha L3: el pago que emite la credencial, de punta a punta.

`webapp/billing.py` declaraba en su propio docstring que *"it does NOT call the
Stripe API"*: resolvia un `STRIPE_LINK_<SLUG>` puesto a mano, degradaba a mailto,
y no habia handler de webhook en todo `webapp/`. Sin ese evento no existia el
disparador que diera de alta una credencial sola, asi que cada venta terminaba en
un operador emitiendo la key con `examples/issue_mcp_key.py`.

El "cerrada cuando" de la ficha esta partido en dos por una restriccion del
harness, no del diseno: aca se prueba que el evento FIRMADO emite la credencial
atada al pago, y en tests/test_mcp_mount.py que esa credencial abre `POST /mcp/`
donde antes habia 401. Ese archivo ya es duenio del unico TestClient que puede
entrar el lifespan del sub-app de MCP; dos en la misma corrida revientan.

Sin SDK de Stripe: la firma es HMAC-SHA256 sobre "{t}.{cuerpo crudo}" y la
stdlib alcanza. Ninguna prueba de este archivo toca la red ni la API de Stripe.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as app_module  # noqa: E402
from src.mcp_keys import McpKeyStore  # noqa: E402
from webapp import security  # noqa: E402
from webapp.stripe_webhook import (  # noqa: E402
    DEFAULT_TOLERANCE,
    SignatureError,
    purchase_from_event,
    verify_signature,
)

SECRETO = "whsec_de_prueba_no_es_real"


def _firmar(cuerpo: bytes, secreto: str = SECRETO, ts: int | None = None, extra_v1: str | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    firmado = str(ts).encode("ascii") + b"." + cuerpo
    v1 = hmac.new(secreto.encode("utf-8"), firmado, hashlib.sha256).hexdigest()
    cabecera = f"t={ts},v1={v1}"
    return cabecera + (f",v1={extra_v1}" if extra_v1 else "")


def _evento(
    *,
    session_id: str = "cs_test_1",
    email: str | None = "compra@cliente.com",
    payment_status: str = "paid",
    tipo: str = "checkout.session.completed",
    client_reference_id: str | None = None,
) -> bytes:
    sesion: dict = {
        "id": session_id,
        "payment_status": payment_status,
        "amount_total": 35000,
        "currency": "usd",
    }
    if email is not None:
        sesion["customer_details"] = {"email": email}
    if client_reference_id is not None:
        sesion["client_reference_id"] = client_reference_id
    return json.dumps({"id": "evt_1", "type": tipo, "data": {"object": sesion}}).encode("utf-8")


# --- la firma ----------------------------------------------------------------


def test_una_firma_valida_verifica():
    cuerpo = _evento()
    assert verify_signature(cuerpo, _firmar(cuerpo), SECRETO) > 0


def test_una_firma_de_otro_secreto_no_verifica():
    cuerpo = _evento()
    with pytest.raises(SignatureError):
        verify_signature(cuerpo, _firmar(cuerpo, secreto="whsec_otro"), SECRETO)


def test_un_cuerpo_alterado_no_verifica():
    """Lo que la firma protege: el cuerpo, byte a byte."""
    cuerpo = _evento()
    cabecera = _firmar(cuerpo)
    with pytest.raises(SignatureError):
        verify_signature(cuerpo.replace(b"35000", b"1"), cabecera, SECRETO)


def test_una_firma_vieja_no_verifica():
    """El HMAC prueba autenticidad, no frescura. Sin ventana, una firma valida
    capturada una vez sirve para siempre."""
    cuerpo = _evento()
    viejo = int(time.time()) - DEFAULT_TOLERANCE - 60
    with pytest.raises(SignatureError):
        verify_signature(cuerpo, _firmar(cuerpo, ts=viejo), SECRETO)


def test_sin_secreto_configurado_no_se_acepta_nada():
    """EL CASO QUE NO PUEDE FALLAR ABIERTO.

    La convencion del repo es que una variable vacia APAGA la funcion
    (LINCHPIN_API_KEY, notify()). Aca eso apagaria la verificacion, no el
    endpoint: cualquiera podria emitirse una credencial.
    """
    cuerpo = _evento()
    with pytest.raises(SignatureError):
        verify_signature(cuerpo, _firmar(cuerpo), "")


def test_acepta_varias_firmas_durante_una_rotacion():
    """Stripe manda la vieja y la nueva juntas mientras se rota el secreto.
    Quedarse con la primera romperia el cobro justo en la transicion."""
    cuerpo = _evento()
    cabecera = _firmar(cuerpo, extra_v1="0" * 64)
    assert verify_signature(cuerpo, cabecera, SECRETO) > 0
    ts = int(cabecera.split(",")[0][2:])
    invertida = f"t={ts},v1={'0' * 64},v1={cabecera.split('v1=')[1]}"
    assert verify_signature(cuerpo, invertida, SECRETO) > 0


@pytest.mark.parametrize("cabecera", ["", "t=123", "v1=abc", "t=no,v1=abc", "basura"])
def test_una_cabecera_mal_formada_no_verifica(cabecera):
    with pytest.raises(SignatureError):
        verify_signature(_evento(), cabecera, SECRETO)


# --- que evento es una compra ------------------------------------------------


def test_una_sesion_pagada_es_una_compra():
    compra = purchase_from_event(_evento())
    assert compra is not None
    assert (compra.payment_ref, compra.email) == ("cs_test_1", "compra@cliente.com")
    assert compra.client_name == "compra@cliente.com"


def test_el_client_reference_id_manda_sobre_el_email():
    compra = purchase_from_event(_evento(client_reference_id="Acme SpA"))
    assert compra.client_name == "Acme SpA"


def test_una_sesion_completada_pero_no_pagada_no_es_una_compra():
    """`completed` NO implica `paid`: con metodos asincronos la sesion se
    completa y el dinero llega despues, o no llega."""
    assert purchase_from_event(_evento(payment_status="unpaid")) is None


@pytest.mark.parametrize("caso", [
    {"tipo": "invoice.paid"},
    {"tipo": "customer.created"},
    {"email": None},
])
def test_lo_que_no_es_una_compra_devuelve_none(caso):
    assert purchase_from_event(_evento(**caso)) is None


def test_un_cuerpo_que_no_es_json_no_revienta():
    assert purchase_from_event(b"no soy json") is None


def test_la_referencia_es_la_SESION_y_no_el_evento():
    """Una misma sesion genera varios eventos (completed,
    async_payment_succeeded). Anclar en el id del evento emitiria una credencial
    por evento en vez de una por compra."""
    compra = purchase_from_event(_evento(session_id="cs_abc"))
    assert compra.payment_ref == "cs_abc"
    assert compra.payment_ref != "evt_1"


# --- la ruta, de punta a punta -----------------------------------------------


@pytest.fixture(scope="module")
def client():
    """TestClient PELADO, sin `with`: la ruta del webhook no necesita el
    lifespan, y entrar el lifespan aca chocaria con el TestClient de
    tests/test_mcp_mount.py -- el sub-app de MCP es un singleton de modulo y su
    session manager revienta si se lo entra dos veces en la misma corrida. El
    tramo que SI necesita /mcp/ vive alla, por ese mismo motivo.
    """
    return TestClient(app_module.app)


@pytest.fixture(autouse=True)
def _entorno(monkeypatch):
    store = McpKeyStore(":memory:")
    monkeypatch.setattr(app_module, "_get_mcp_key_store", lambda: store)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRETO)
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    # notify() ya es no-op sin webhook configurado; se fija explicito para que
    # esta suite no dependa del entorno de quien la corra.
    monkeypatch.delenv("LINCHPIN_NOTIFY_WEBHOOK_URL", raising=False)
    yield store


def _postear(client, cuerpo: bytes, cabecera: str | None = None):
    return client.post(
        "/webhooks/stripe",
        content=cuerpo,
        headers={"Stripe-Signature": cabecera if cabecera is not None else _firmar(cuerpo),
                 "Content-Type": "application/json"},
    )


def test_una_compra_firmada_emite_la_credencial_atada_al_pago(client, _entorno):
    """La mitad de este archivo del "cerrada cuando": el evento firmado emite.

    La otra mitad -- que esa key abra /mcp/ -- vive en tests/test_mcp_mount.py,
    que ya es duenio del TestClient con lifespan que esa ruta necesita.
    """
    cuerpo = _evento(session_id="cs_e2e", client_reference_id="Acme SpA")

    r = _postear(client, cuerpo)
    assert r.status_code == 200 and r.json()["status"] == "issued"

    # La key no viaja en la respuesta: eso va a Stripe, no al comprador.
    assert "lpk_" not in r.text

    emitidas = _entorno.list_keys()
    assert len(emitidas) == 1
    assert emitidas[0]["payment_ref"] == "cs_e2e"
    assert emitidas[0]["email"] == "compra@cliente.com"
    assert emitidas[0]["client_name"] == "Acme SpA"


def test_una_firma_invalida_no_emite_nada(client, _entorno):
    """El caso que importa del lado defensivo: 400 y CERO credenciales."""
    cuerpo = _evento(session_id="cs_falsa")
    r = _postear(client, cuerpo, cabecera=_firmar(cuerpo, secreto="whsec_del_atacante"))
    assert r.status_code == 400
    assert _entorno.list_keys() == []


def test_un_reintento_de_stripe_no_emite_una_segunda_key(client, _entorno):
    """Stripe reintenta hasta recibir un 2xx, y reenvia a mano desde su panel.
    Lo impide el indice unico parcial de McpKeyStore, en la base y no en memoria
    -- un proceso que "recuerda" lo ya atendido olvida en cada deploy."""
    cuerpo = _evento(session_id="cs_reintento")
    primera = _postear(client, cuerpo)
    segunda = _postear(client, cuerpo)

    assert primera.json()["status"] == "issued"
    assert segunda.status_code == 200 and segunda.json()["status"] == "already_issued"
    assert len(_entorno.list_keys()) == 1


def test_un_evento_que_no_es_compra_se_ignora_con_200(client, _entorno):
    """200 y no 4xx: contestarle error a Stripe por algo que no era una compra
    lo hace reintentar para siempre."""
    r = _postear(client, _evento(tipo="customer.created"))
    assert r.status_code == 200 and r.json()["status"] == "ignored"
    assert _entorno.list_keys() == []


def test_sin_secreto_en_el_entorno_la_ruta_rechaza(client, _entorno, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
    r = _postear(client, _evento(session_id="cs_sin_secreto"))
    assert r.status_code == 400
    assert _entorno.list_keys() == []
