"""Un plazo que el ERP nunca registro no puede salir como cero.

`lead_times()` leia `product.supplierinfo.delay` con `or 0.0`, asi que una linea
de proveedor sin plazo cargado entraba al diccionario como **0,0 dias** -- y de
ahi al entregable, donde el cliente lee "0.0" y no tiene forma de saber que nadie
lo midio. Un plazo de compra corto no es una lectura conservadora: es la mas
favorable que existe, porque dice que el proveedor entrega casi en el acto.

El piso no es cero sino `_DECLARED_DELAY_FLOOR`, porque el campo viene con
`default=1` en Odoo y un 1 es el campo sin configurar. Es la misma constante que
ya aplicaba `receipts()` sobre el mismo campo: el primer intento de este arreglo
uso `> 0` y dejo dos umbrales distintos para el mismo hecho en el mismo archivo.

Es el mismo defecto que `src/order_fulfillment.py` y `src/supplier_scorecard.py`
ya nombran con todas sus letras -- *"una ausencia renderizada como medicion"* --
y que `src/deliverable.not_measured` existe para evitar. El camino del CSV
(`jobs/intake.py::_fill`) cuenta cada celda que inventa; este camino no contaba
ninguna.

Lo que estos tests fijan es la PROPIEDAD, no el nombre de la funcion que la
cumple: la salida tiene que **cambiar** entre un ERP que trae el plazo y uno que
no. Un test que solo mire que `declared_lead_times()` existe no acredita nada.

El defecto tiene dos mitades y cada una fabrica el cero por su cuenta: el conector
(`src/connectors/odoo.py`) y el renderer del entregable
(`jobs/odoo_job.py::write_operational`, que hacia `report.lead_times.get(sku, 0.0)`).
Arreglar solo una deja el mismo numero en la pantalla del cliente, asi que las dos
estan cubiertas aca.
"""
from __future__ import annotations

import pandas as pd

from jobs.odoo_job import (
    OdooReplenishmentReport,
    _build_outcome,
    build_deck,
    prepare,
    run,
    verify,
    write_operational,
)
from src.connectors.odoo import (
    _DECLARED_DELAY_FLOOR,
    InMemoryOdoo,
    OdooConnector,
    demo_odoo,
)
from src.connectors.replenish import ReplenishmentLine

#: Sentinela: `lineas={}` es un caso de prueba legitimo (un Odoo sin ninguna linea
#: de proveedor), asi que no puede distinguirse del default con un `or`.
_SIN_ESPECIFICAR = object()


def _odoo(lineas=_SIN_ESPECIFICAR) -> InMemoryOdoo:
    """Dos SKU, un proveedor cada uno. `lineas` reemplaza las lineas de plazo."""
    if lineas is _SIN_ESPECIFICAR:
        lineas = {
            400: {"product_id": [1, "Rodamiento"], "partner_id": [70, "Acme"], "sequence": 1, "delay": 7.0},
            401: {"product_id": [2, "Sello"], "partner_id": [71, "Globex"], "sequence": 1, "delay": 14.0},
        }
    return InMemoryOdoo(
        {
            "product.product": {
                1: {"default_code": "SKU-1", "name": "Rodamiento", "list_price": 20.0, "standard_price": 12.0},
                2: {"default_code": "SKU-2", "name": "Sello", "list_price": 50.0, "standard_price": 30.0},
            },
            "product.supplierinfo": lineas,
        }
    )


def test_el_plazo_ausente_cambia_la_salida_en_vez_de_volverse_cero():
    """La propiedad central: dos Odoo identicos salvo el `delay` de SKU-2.

    Si el arreglo funciona, la salida de los dos NO puede ser igual, y la
    diferencia no puede ser "0.0" -- tiene que ser la ausencia del SKU.
    """
    con_plazo = OdooConnector(_odoo()).lead_times()
    sin_plazo = OdooConnector(
        _odoo(lineas={
            400: {"product_id": [1, "Rodamiento"], "partner_id": [70, "Acme"], "sequence": 1, "delay": 7.0},
            # misma linea de proveedor, sin el campo de plazo cargado:
            401: {"product_id": [2, "Sello"], "partner_id": [71, "Globex"], "sequence": 1},
        })
    ).lead_times()

    assert con_plazo == {"SKU-1": 7.0, "SKU-2": 14.0}
    # SKU-2 sale del diccionario. No entra con 0.0, que es lo que hacia antes.
    assert sin_plazo == {"SKU-1": 7.0}
    assert "SKU-2" not in sin_plazo


def test_el_default_de_odoo_no_es_una_promesa():
    """`delay` viene con `default=1` en Odoo: un 1 es el campo sin configurar.

    Y desde XML-RPC no se distingue del 1 que alguien escribio a mano, asi que
    ante la duda se elige el lado seguro -- tratar el 1 como dato baja la
    proteccion recomendada, tratarlo como ausencia la sube.

    El piso es `_DECLARED_DELAY_FLOOR`, la MISMA constante que usa `receipts()`
    sobre el mismo campo. Este test la importa en vez de escribir el numero para
    que subirla mueva las dos lecturas a la vez: dos umbrales para el mismo hecho
    es como empiezan a divergir.
    """
    connector = OdooConnector(_odoo(lineas={
        400: {"product_id": [1, "Rodamiento"], "partner_id": [70, "Acme"], "sequence": 1,
              "delay": float(_DECLARED_DELAY_FLOOR)},
        401: {"product_id": [2, "Sello"], "partner_id": [71, "Globex"], "sequence": 1, "delay": 14.0},
    }))

    assert connector.lead_times() == {"SKU-2": 14.0}
    assert connector.declared_lead_times().missing == ("SKU-1",)


def test_el_cero_tambien_queda_afuera_y_el_primer_valor_real_entra():
    """Los dos bordes del piso, para que el `>` no se vuelva `>=` sin que nadie note.

    Cero es el caso degenerado (nadie compra con entrega instantanea) y el
    primer entero por encima del piso es el valor mas chico que SI es una
    promesa. Si el umbral se relaja, este test cae; si se endurece, cae tambien.
    """
    connector = OdooConnector(_odoo(lineas={
        400: {"product_id": [1, "Rodamiento"], "partner_id": [70, "Acme"], "sequence": 1, "delay": 0.0},
        401: {"product_id": [2, "Sello"], "partner_id": [71, "Globex"], "sequence": 1,
              "delay": float(_DECLARED_DELAY_FLOOR + 1)},
    }))

    assert connector.lead_times() == {"SKU-2": float(_DECLARED_DELAY_FLOOR + 1)}
    assert connector.declared_lead_times().missing == ("SKU-1",)


def test_los_plazos_ausentes_se_cuentan_y_se_nombran():
    """Omitir en silencio seguiria siendo silencio.

    El camino del CSV registra cuantas celdas invento (`IntakeQuality.synthesized`)
    y el de recepciones cuantos movimientos descarto y por que (`OdooReceipts.dropped`).
    Este tiene que decir lo mismo, o el hueco no llega a ninguna pantalla.
    """
    resultado = OdooConnector(_odoo(lineas={
        400: {"product_id": [1, "Rodamiento"], "partner_id": [70, "Acme"], "sequence": 1},
        401: {"product_id": [2, "Sello"], "partner_id": [71, "Globex"], "sequence": 1, "delay": 14.0},
    })).declared_lead_times()

    assert resultado.declared == {"SKU-2": 14.0}
    assert resultado.missing == ("SKU-1",)
    # El aviso nombra el SKU: "1 de 2" sin decir cual no es accionable.
    assert any("SKU-1" in aviso for aviso in resultado.qa_issues)


def test_sin_ninguna_linea_de_proveedor_no_hay_aviso_que_dar():
    """Un Odoo sin `product.supplierinfo` no tiene plazos ausentes: no tiene plazos.

    La distincion importa porque el aviso existe para que alguien lo lea, y un
    aviso que aparece siempre deja de leerse.
    """
    resultado = OdooConnector(_odoo(lineas={})).declared_lead_times()

    assert resultado.declared == {} and resultado.missing == ()
    assert resultado.qa_issues == ()


# -- la otra mitad: el renderer del entregable -------------------------------


def _reporte(lead_times: dict[str, float]) -> OdooReplenishmentReport:
    """Dos lineas identicas; entre corridas solo cambia que plazos trae el reporte."""
    lines = (
        ReplenishmentLine(sku="SKU-1", on_hand=10.0, forecast_per_period=2.0, target=16.0, restock_qty=6.0),
        ReplenishmentLine(sku="SKU-2", on_hand=5.0, forecast_per_period=1.0, target=8.0, restock_qty=3.0),
    )
    return OdooReplenishmentReport(
        source="InMemoryOdoo",
        lines=lines,
        restock={"SKU-1": 6.0, "SKU-2": 3.0},
        lead_times=lead_times,
        n_skus=2,
        n_restock=2,
        total_restock=9.0,
        cover_periods=8.0,
        outcome=_build_outcome(2, 9.0),
        summary="",
    )


def test_el_entregable_deja_la_celda_vacia_en_vez_de_escribir_cero(tmp_path):
    """El CSV es la superficie donde el cliente ve el numero.

    Arreglar el conector y dejar el renderer con su propio `.get(sku, 0.0)`
    fabrica el mismo cero un paso mas abajo. En un CSV la ausencia se escribe
    vacia -- pandas la lee como NaN -- y no con la palabra "no medido", que
    rompe la columna numerica para cualquiera que la parsee. La palabra va en el
    deck, que lo lee una persona.
    """
    con_plazo = pd.read_csv(
        write_operational(_reporte({"SKU-1": 7.0, "SKU-2": 14.0}), tmp_path / "con")["csv"]
    )
    sin_plazo = pd.read_csv(
        write_operational(_reporte({"SKU-1": 7.0}), tmp_path / "sin")["csv"]
    )

    assert list(con_plazo["lead_time_days"]) == [7.0, 14.0]
    # SKU-1 no se mueve: el arreglo es dirigido, no un blanqueo de la columna.
    assert sin_plazo.loc[sin_plazo["sku"] == "SKU-1", "lead_time_days"].item() == 7.0
    assert pd.isna(sin_plazo.loc[sin_plazo["sku"] == "SKU-2", "lead_time_days"].item())


# -- que el hueco llegue a la pantalla, no solo al diccionario ---------------


def test_el_deck_nombra_los_plazos_que_el_erp_no_registra():
    """Omitir en silencio es la mitad del arreglo; la otra es decirlo.

    Una celda vacia en el CSV la ve quien abre el CSV. El deck lo lee la persona
    que decide, y para ella la ausencia del plazo es lo que explica por que su
    proveedor no aparece en el analisis -- si no se nombra, lee la omision como
    que ese proveedor esta bien.

    Va como Finding y NO como issue de `verify()`: `verify()` es el veto de QA y
    bloquea el entregable entero. El plan de reposicion no usa el plazo
    declarado para nada (`plan_replenishment` no lo toca), asi que bloquearlo
    seria negarse a entregar un numero que es correcto.
    """
    con_plazo = demo_odoo()
    sin_plazo = demo_odoo()
    del sin_plazo.records("product.supplierinfo")[400]["delay"]

    def _deck(odoo):
        return build_deck(run(prepare(params={"odoo_rpc": odoo})))

    completo, incompleto = _deck(con_plazo), _deck(sin_plazo)

    def _menciona_plazo_ausente(deck) -> bool:
        # El deck escribe en su propio idioma a partir de los SKU que el conector
        # reporta, en vez de reenviar la prosa del conector: cada capa habla como
        # hablan sus hermanas.
        return any("no declared lead time" in f.detail for f in deck.findings)

    assert not _menciona_plazo_ausente(completo)
    assert _menciona_plazo_ausente(incompleto)
    # Y sigue siendo entregable: el hueco se declara, no bloquea.
    assert verify(run(prepare(params={"odoo_rpc": sin_plazo}))) == []
