"""El harness de las brechas de lanzamiento: mide, no cree.

`documentation/KERN_BRECHAS_DE_LANZAMIENTO.md` declara cinco items y su estado.
Este archivo MIDE cada uno contra el codigo y falla si lo medido no coincide con
lo declarado -- en las dos direcciones:

  * cerrar una brecha y no actualizar el documento  -> falla
  * el documento diciendo CERRADA algo que sigue abierto -> falla
  * una regresion que reabre una cerrada            -> falla

Cerrar una brecha rompe este test A PROPOSITO. Es la unica forma de que la tabla
del documento siga siendo verdad sin que nadie se acuerde de mantenerla: el PR
que cierra el trabajo tiene que tocar las dos cosas o no pasa.

POR QUE NO ES UN TEST QUE FALLA HASTA QUE SE ARREGLE. Un gate que esta rojo por
diseno se aprende a ignorar -- es exactamente el argumento por el que
`reflect_lessons.py --check` no se cableo como paso suelto de la CI. Este esta
verde mientras el estado declarado sea cierto, y sensible a que deje de serlo.

Las sondas son funciones puras sobre el TEXTO de cada archivo, no sobre imports:
asi se pueden ejercitar en los dos sentidos (con el codigo de hoy y con el que
cerraria la brecha) sin montar medio webapp. Un gate solo acredita si la salida
cambia entre dos insumos.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
DOC = RAIZ / "documentation" / "KERN_BRECHAS_DE_LANZAMIENTO.md"
APP = RAIZ / "webapp" / "app.py"
BILLING = RAIZ / "webapp" / "billing.py"
SHOPIFY = RAIZ / "src" / "connectors" / "shopify_connect.py"
MANIFEST = RAIZ / "odoo_addon" / "linchpin_dry_run" / "__manifest__.py"


# --- las sondas, puras sobre texto -------------------------------------------


def l1_abierta(texto_app: str) -> bool:
    """El ledger de leads se pierde si LEADS_DIR no acepta override de entorno.

    En la imagen de Fly la ruta por defecto cae sobre el filesystem efimero de
    la Machine, que se borra en cada deploy; sin variable de entorno no hay
    forma de apuntarla al volumen /data.
    """
    linea = next((x for x in texto_app.splitlines() if x.startswith("LEADS_DIR")), "")
    assert linea, "webapp/app.py ya no asigna LEADS_DIR; esta sonda quedo vieja"
    return "environ" not in linea and "getenv" not in linea


#: Lo que una app del App Store necesita y una custom app no: OAuth, el callback,
#: los webhooks (incluidos los tres obligatorios de GDPR) y el alta.
_RUTAS_DE_APP = re.compile(r"@app\.(?:get|post)\(\s*[\"'][^\"']*(oauth|webhook|auth/callback|install)",
                           re.IGNORECASE)


def l2_abierta(texto_app: str) -> bool:
    """Sin ninguna ruta de OAuth/webhook/instalacion, no hay app del App Store."""
    return not _RUTAS_DE_APP.search(texto_app)


def l3_abierta(texto_billing: str, texto_app: str) -> bool:
    """El billing es un stub mientras declare que no llama a Stripe y no exista
    una ruta de webhook que reciba el evento de pago."""
    stub = "does NOT call the Stripe API" in texto_billing
    sin_webhook = "webhook" not in texto_app.lower()
    return stub or sin_webhook


def l4_abierta(texto_manifest: str, serie_soportada: str = "19.0") -> bool:
    """Dos techos: la serie del manifest y la incompatibilidad con Odoo Online.

    Alcanza con que UNO siga en pie para que el canal Odoo siga bloqueado, y son
    independientes: portar de version no toca la restriccion de la plataforma
    para modulos con codigo Python.
    """
    version = re.search(r'"version"\s*:\s*"([\d.]+)"', texto_manifest)
    assert version, "el manifest del addon ya no declara version; esta sonda quedo vieja"
    serie_vieja = not version.group(1).startswith(serie_soportada)
    sin_online = "not\ncompatible with Odoo Online" in texto_manifest or \
                 "compatible with Odoo Online" in texto_manifest
    return serie_vieja or sin_online


def f1_intacta(texto_shopify: str) -> bool:
    """La FRONTERA: el modulo sigue declarando ausente el plazo en vez de
    rellenarlo. Esto no se cierra nunca; se rompe."""
    return "PLAZO_AUSENTE" in texto_shopify


# --- el estado declarado sale del documento, no de una constante de acá -------


def _estado_declarado() -> dict[str, str]:
    """La columna Estado de la tabla resumen del documento.

    Se lee del .md a proposito: si el estado viviera tambien aca, cerrar una
    brecha se podria "arreglar" tocando solo el test y el documento quedaria
    mintiendo -- que es el modo de falla que este archivo existe para impedir.
    """
    filas = re.findall(r"^\|\s*(L\d|F1)\s*\|[^|]*\|\s*\*{0,2}([A-ZÁ-Ú]+)\*{0,2}\s*\|",
                       DOC.read_text(encoding="utf-8"), re.MULTILINE)
    estados = dict(filas)
    assert len(estados) == 5, f"se esperaban 5 items en la tabla del documento, hay {len(estados)}: {estados}"
    return estados


def _medido() -> dict[str, str]:
    app = APP.read_text(encoding="utf-8")
    return {
        "L1": "ABIERTA" if l1_abierta(app) else "CERRADA",
        "L2": "ABIERTA" if l2_abierta(app) else "CERRADA",
        "L3": "ABIERTA" if l3_abierta(BILLING.read_text(encoding="utf-8"), app) else "CERRADA",
        "L4": "ABIERTA" if l4_abierta(MANIFEST.read_text(encoding="utf-8")) else "CERRADA",
        "F1": "FRONTERA" if f1_intacta(SHOPIFY.read_text(encoding="utf-8")) else "ROTA",
    }


@pytest.mark.parametrize("clave", ["L1", "L2", "L3", "L4", "F1"])
def test_el_estado_declarado_coincide_con_el_medido(clave):
    """EL TEST QUE IMPORTA.

    Si esto falla en un PR que cierra una brecha, no es un bug del test: es el
    recordatorio de actualizar la tabla del documento en ese mismo PR.
    """
    declarado, medido = _estado_declarado()[clave], _medido()[clave]
    assert declarado == medido, (
        f"{clave}: el documento dice {declarado} y el codigo mide {medido}. "
        f"Actualizar documentation/KERN_BRECHAS_DE_LANZAMIENTO.md, o revisar la sonda."
    )


def test_la_frontera_no_esta_declarada_como_brecha():
    """F1 no es trabajo pendiente y no debe aparecer como tal: el plazo de
    proveedor no existe en la Admin API de Shopify, y ninguna cantidad de
    trabajo de este lado lo crea."""
    assert _estado_declarado()["F1"] == "FRONTERA"


# --- las contrapruebas: cada sonda tiene que MOVERSE --------------------------


def test_la_sonda_del_ledger_muerde():
    assert l1_abierta('LEADS_DIR = _REPO_ROOT / "webapp" / "_leads"')
    assert not l1_abierta('LEADS_DIR = Path(os.environ.get("KERN_LEADS_DIR", _DEFECTO))')
    assert not l1_abierta('LEADS_DIR = Path(os.getenv("KERN_LEADS_DIR", _DEFECTO))')


def test_la_sonda_del_app_store_muerde():
    assert l2_abierta('@app.get("/api/health")\n@app.post("/api/jobs")')
    assert not l2_abierta('@app.get("/shopify/oauth/start")')
    assert not l2_abierta('@app.post("/webhooks/customers/redact")')


def test_la_sonda_del_billing_muerde():
    stub, listo = "it does NOT call the Stripe API", "crea una Checkout Session real"
    assert l3_abierta(stub, "@app.post('/webhooks/stripe')")      # el stub alcanza
    assert l3_abierta(listo, "@app.get('/api/health')")           # falta el webhook
    assert not l3_abierta(listo, "@app.post('/webhooks/stripe')")  # las dos mitades


def test_la_sonda_del_addon_muerde():
    viejo = '"version": "17.0.1.0.0",\nnot compatible with Odoo Online'
    portado = '"version": "19.0.1.0.0",\nnot compatible with Odoo Online'
    listo = '"version": "19.0.1.0.0",\ncorre en cualquier despliegue'
    assert l4_abierta(viejo)
    assert l4_abierta(portado), "portar la serie no levanta el techo de Odoo Online"
    assert not l4_abierta(listo)


def test_la_sonda_de_la_frontera_muerde():
    assert f1_intacta("notes=(PLAZO_AUSENTE,)")
    assert not f1_intacta("notes=(f'plazo estimado: {promedio} dias',)")


def test_toda_ficha_del_documento_nombra_su_sitio_de_codigo():
    """Una ficha sin `archivo::simbolo` no se puede medir, y en dos semanas nadie
    sabe contra que estaba escrita."""
    texto = DOC.read_text(encoding="utf-8")
    for archivo in ("webapp/app.py::LEADS_DIR",
                    "src/connectors/shopify_connect.py::check_connection",
                    "webapp/billing.py::create_checkout_session",
                    "odoo_addon/linchpin_dry_run/__manifest__.py",
                    "src/connectors/shopify_connect.py::PLAZO_AUSENTE"):
        assert archivo in texto, f"el documento ya no nombra {archivo}"
        ruta = RAIZ / archivo.split("::")[0]
        assert ruta.exists(), f"el documento apunta a {ruta}, que no existe"
