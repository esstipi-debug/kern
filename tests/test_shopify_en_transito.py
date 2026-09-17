"""El plan de Shopify no vuelve a comprar lo que la tienda ya tiene en camino (C1).

``jobs/shopify_job.py`` llamaba a ``plan_replenishment`` sin mapa de en-transito: cada
linea salia con ``on_order=None`` -- honesto, pero el neteo nunca podia ocurrir porque
nadie le pasaba el dato. Y el dato existia: Shopify reporta la cantidad ``incoming``
(transferencias entrantes y las OC del propio admin) en la MISMA consulta en la que el
conector ya pedia ``available``.

Misma disciplina que ``tests/test_neteo_en_transito.py`` (kern#251):

- cada caso corre DOS veces con insumos distintos y afirma que el VALOR SE MUEVE;
- el neteo alcanza al disparador, no solo a la cantidad;
- la ausencia se declara: una API que no reporta ``incoming`` deja ``None``, nunca 0,
  y el entregable lo dice.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import shopify_job  # noqa: E402
from src.connectors.shopify import (  # noqa: E402
    InMemoryShopifyCommerce,
    ShopifyConnector,
    demo_shopify,
)

LOC = "gid://shopify/Location/1"


def _variant(sku: str, cost: float = 5.0) -> dict:
    return {
        "id": f"gid://shopify/ProductVariant/{sku}",
        "sku": sku,
        "displayName": sku,
        "price": "12.00",
        "inventoryItem": {
            "id": f"gid://shopify/InventoryItem/{sku}",
            "unitCost": {"amount": f"{cost:.2f}"},
        },
    }


def _orders(sku: str, qty: int = 5, days: int = 8) -> list[dict]:
    return [
        {
            "id": f"gid://shopify/Order/{d}",
            "createdAt": f"2026-01-0{d}T10:00:00Z",
            "lineItems": {
                "edges": [
                    {"node": {"sku": sku, "quantity": qty,
                              "originalUnitPriceSet": {"shopMoney": {"amount": "12.00"}}}}
                ]
            },
        }
        for d in range(1, days + 1)
    ]


def _rpc(*, on_hand: int = 12, incoming: int | None = 0, sku: str = "SKU-3") -> InMemoryShopifyCommerce:
    """La tienda del caso: un SKU que vende parejo y esta corto.

    ``incoming=None`` modela una API que NO reporta la cantidad entrante, que es la
    ausencia que hay que declarar -- distinta de una tienda con cero en camino.
    """
    return InMemoryShopifyCommerce(
        variants=[_variant(sku)],
        orders=_orders(sku),
        stock={sku: {LOC: on_hand}},
        incoming=None if incoming is None else {sku: {LOC: incoming}},
    )


def _report(rpc: InMemoryShopifyCommerce):
    return shopify_job.run(shopify_job.prepare(None, {"shopify_rpc": rpc}))


# -- el conector: una sola consulta, dos cantidades -------------------------------


def test_el_conector_lee_estante_y_en_camino_en_una_sola_llamada_por_item():
    """Pedir la segunda cantidad no puede costar una segunda vuelta a la API: Shopify
    devuelve las dos en la misma consulta, y el conector memoiza la pasada."""
    rpc = _rpc(incoming=35)
    connector = ShopifyConnector(rpc)

    assert {lvl.sku: lvl.available for lvl in connector.inventory_levels()} == {"SKU-3": 12.0}
    assert connector.incoming_by_sku() == {"SKU-3": 35.0}
    assert rpc.quantity_calls == 1

    connector.inventory_levels()          # segunda lectura: memoizada, no vuelve a la API
    connector.incoming_by_sku()
    assert rpc.quantity_calls == 1


def test_sin_incoming_en_la_api_el_conector_devuelve_none_y_no_un_mapa_vacio():
    """``None`` y ``{}`` significan cosas distintas aguas abajo: la primera es 'nadie
    lo midio', la segunda seria 'medido, y no viene nada'."""
    assert ShopifyConnector(_rpc(incoming=None)).incoming_by_sku() is None
    assert ShopifyConnector(_rpc(incoming=0)).incoming_by_sku() == {"SKU-3": 0.0}


def test_un_rpc_viejo_sin_el_metodo_sigue_leyendo_el_estante():
    """Un adaptador inyectado que solo implementa ``get_inventory_level`` no puede
    romperse por una capacidad que no tiene."""

    class _SoloEstante:
        def list_variants(self, limit=250):
            return [_variant("SKU-3")]

        def list_orders(self, since=None, limit=250):
            return _orders("SKU-3")

        def get_inventory_level(self, inventory_item_id, location_id):
            return 12

    connector = ShopifyConnector(_SoloEstante())
    assert {lvl.sku: lvl.available for lvl in connector.inventory_levels()} == {"SKU-3": 12.0}
    assert connector.incoming_by_sku() is None


# -- el job: el valor se mueve ----------------------------------------------------


def test_el_job_descuenta_lo_que_viene_en_camino(capsys=None):
    """Dos corridas sobre la MISMA tienda, cambiando solo la cantidad entrante."""
    sin = _report(_rpc(incoming=0))
    con = _report(_rpc(incoming=10))

    assert sin.restock == {"SKU-3": 28.0}
    assert con.restock == {"SKU-3": 18.0}                  # el valor SE MUEVE, exactamente 10
    assert con.restock["SKU-3"] == pytest.approx(sin.restock["SKU-3"] - 10)
    assert con.lines[0].on_order == 10.0
    assert con.lines[0].position == 22.0                   # 12 en estante + 10 en camino
    assert con.restock_value == pytest.approx(18.0 * 5.0)  # el valor del plan baja con el


def test_lo_que_ya_viene_puede_apagar_el_disparador_entero():
    """El neteo alcanza al DISPARADOR, no solo a la cantidad: con lo entrante por
    encima del objetivo no hay nada que pedir."""
    report = _report(_rpc(incoming=40))
    assert report.restock == {}
    assert report.n_restock == 0
    assert report.lines[0].position == 52.0


def test_incoming_cero_es_una_medicion_y_no_una_ausencia():
    report = _report(_rpc(incoming=0))
    assert report.on_order_by_sku == {"SKU-3": 0.0}
    assert report.lines[0].on_order == 0.0                 # medido, nada en camino
    assert "net of stock already inbound" in report.netting_note


def test_sin_incoming_el_job_lo_declara_en_todas_sus_superficies(tmp_path):
    report = _report(_rpc(incoming=None))

    assert report.on_order_by_sku is None
    assert report.lines[0].on_order is None, "sin dato, None y nunca 0.0"
    assert report.lines[0].position == report.lines[0].on_hand
    assert "NOT netted" in report.netting_note and "NOT netted" in report.summary

    deck = shopify_job.build_deck(report, client="Demo", confidence=0.85)
    assert any(f.title == "Quantities do NOT net stock already inbound" for f in deck.findings)
    fuente = next(s for s in deck.data_sources if s.field == "Stock already inbound")
    assert fuente.cadence == "absent"

    ruta = shopify_job.write_operational(report, tmp_path)["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        fila = next(csv.DictReader(fh))
    # ``on_order`` en blanco (no medido) y la posicion igual al estante: la linea SI se
    # dimensiono, contra el estante solo. Es la misma convencion de la planilla y de Odoo.
    assert fila["on_order"] == ""
    assert float(fila["position"]) == float(fila["on_hand"]) == 12.0


def test_con_incoming_el_deck_dice_que_ya_esta_descontado_y_el_csv_lo_muestra(tmp_path):
    report = _report(_rpc(incoming=10))

    deck = shopify_job.build_deck(report, client="Demo", confidence=0.85)
    titulos = [f.title for f in deck.findings]
    assert "Quantities are net of stock already inbound" in titulos
    assert "Quantities do NOT net stock already inbound" not in titulos
    fuente = next(s for s in deck.data_sources if s.field == "Stock already inbound")
    assert fuente.cadence == "live"

    ruta = shopify_job.write_operational(report, tmp_path)["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        fila = next(csv.DictReader(fh))
    assert float(fila["on_order"]) == 10.0 and float(fila["position"]) == 22.0


def test_la_tienda_demo_reporta_la_cantidad_entrante():
    """El stand-in tiene que parecerse a una tienda real: Shopify SI reporta
    ``incoming``, asi que la demo mide cero en camino en vez de callar."""
    report = _report(demo_shopify())
    assert report.on_order_by_sku == {"SKU-1": 0.0, "SKU-2": 0.0, "SKU-3": 0.0}
    assert report.restock == {"SKU-3": 28.0}               # el plan de siempre, intacto
    assert shopify_job.verify(report) == []
