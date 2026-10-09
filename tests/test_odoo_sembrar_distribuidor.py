"""El sembrador del laboratorio Odoo, sin Odoo: lo que se puede fijar sin servidor.

``scripts/odoo_sembrar_distribuidor.py`` escribe en un Odoo real, y lo que
siembra lo usa ``tests/integracion/test_recorrido_cliente.py``. Aca se fija lo
que no necesita servidor y que, si se rompe, invalida esa prueba sin que nadie
lo note:

- que reproduce el laboratorio del 2026-09-19 (misma semilla, mismos pedidos,
  mismas ventas y mismo stock final que ``diseno-datos.json`` de ese dia);
- que el proveedor va en la PLANTILLA y nunca en la variante, como lo deja el
  formulario de Odoo 17. Sembrar con variante era el caso poco comun, y por eso
  la suite de integracion no vio el hallazgo F1;
- que el precio del proveedor no coincide con el costo del producto: si
  coincidieran, "la OC lleva el precio del proveedor" pasaria tambien con el
  costo, y el test no distinguiria nada;
- que se niega a sembrar una base que ya tiene el catalogo.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from scripts import odoo_sembrar_distribuidor as sembrador
from scripts.odoo_sembrar_distribuidor import (
    CATALOGO,
    SKU_INTERMITENTE,
    SKU_PLAZO_SIN_CONFIGURAR,
    SKU_SIN_PROVEEDOR,
    linea_proveedor,
    plan_de_datos,
    precio_proveedor,
    skus_ya_cargados,
)

HOY_LABORATORIO = datetime(2026, 9, 19, 12, 0, 0)

#: ``venta_6m`` y ``stock_final_disenado`` de ``diseno-datos.json``, el archivo que
#: escribio el sembrador original en kern-fase0/prueba-recorrido-2026-09-19/.
LABORATORIO = {
    "SKU-001": (1566, 60), "SKU-002": (1991, 40), "SKU-003": (336, 24), "SKU-004": (818, 180),
    "SKU-005": (541, 20), "SKU-006": (167, 24), "SKU-007": (223, 160), "SKU-008": (111, 12),
    "SKU-009": (1354, 150), "SKU-010": (420, 30), "SKU-011": (229, 50), "SKU-012": (188, 14),
    "SKU-013": (53, 12), "SKU-014": (38, 6), "SKU-015": (6, 1), "SKU-016": (666, 25),
    "SKU-017": (0, 15), "SKU-018": (986, 40), "SKU-019": (130, 5), "SKU-020": (150, 48),
}


class _OdooFalso:
    """Registra cada llamada y contesta como una base que ya tiene el catalogo."""

    def __init__(self, codigos: list[str]) -> None:
        self.codigos = codigos
        self.llamadas: list[tuple[str, str]] = []

    def x(self, modelo: str, metodo: str, *args, **kw):
        self.llamadas.append((modelo, metodo))
        if modelo == "ir.module.module":
            return [{"name": m} for m in sembrador.MODULOS_REQUERIDOS]
        if modelo == "product.product" and metodo == "search_read":
            return [{"default_code": c} for c in self.codigos]
        raise AssertionError(f"el sembrador no deberia llamar {modelo}.{metodo} sobre una base ya sembrada")


def test_con_la_fecha_del_laboratorio_reproduce_sus_pedidos_ventas_y_stock():
    plan = plan_de_datos(HOY_LABORATORIO)

    assert len(plan.pedidos) == 78
    distinto = {
        sku: (sum(plan.ventas[sku]), plan.stock_final[sku])
        for sku, esperado in LABORATORIO.items()
        if (sum(plan.ventas[sku]), plan.stock_final[sku]) != esperado
    }
    assert not distinto, f"el sembrador ya no reproduce el laboratorio del 2026-09-19: {distinto}"
    assert all(q >= 0 for q in plan.stock_inicial.values()), plan.stock_inicial


def test_la_transpaleta_vende_una_unidad_en_seis_semanas_distintas():
    serie = plan_de_datos(HOY_LABORATORIO).ventas[SKU_INTERMITENTE]

    assert len(serie) == 26
    assert sorted(set(serie)) == [0, 1]
    assert sum(serie) == 6


def test_el_proveedor_va_en_la_plantilla_y_nunca_en_la_variante():
    con_proveedor = [item for item in CATALOGO if item.proveedor is not None]

    filas = [linea_proveedor(item, tmpl_id=500 + n, proveedor_ids=[7, 8, 9])
             for n, item in enumerate(con_proveedor)]

    assert all("product_id" not in fila for fila in filas)
    assert [fila["product_tmpl_id"] for fila in filas] == [500 + n for n in range(len(con_proveedor))]


def test_el_plazo_sin_configurar_se_siembra_como_el_default_de_odoo():
    """``delay=1`` es el valor que Odoo pone solo: el test de integracion espera
    que Kern lo declare ausente en vez de leerlo como un proveedor de 1 dia."""
    item = next(i for i in CATALOGO if i.codigo == SKU_PLAZO_SIN_CONFIGURAR)

    assert linea_proveedor(item, tmpl_id=1, proveedor_ids=[7, 8, 9])["delay"] == 1


def test_hay_exactamente_un_sku_sin_proveedor():
    assert [i.codigo for i in CATALOGO if i.proveedor is None] == [SKU_SIN_PROVEEDOR]
    with pytest.raises(ValueError, match=SKU_SIN_PROVEEDOR):
        linea_proveedor(next(i for i in CATALOGO if i.codigo == SKU_SIN_PROVEEDOR), 1, [7, 8, 9])


def test_el_precio_del_proveedor_no_es_el_costo_del_producto():
    iguales = [item.codigo for item in CATALOGO if precio_proveedor(item) == item.costo]

    assert not iguales, f"precio de proveedor igual al costo en {iguales}"


def test_detecta_los_skus_del_catalogo_ya_cargados():
    assert skus_ya_cargados(_OdooFalso(["SKU-002", "SKU-001"])) == ["SKU-001", "SKU-002"]


def test_sin_servidor_no_revienta_y_dice_como_levantarlo(monkeypatch, capsys):
    def _sin_servidor(*_args, **_kwargs):
        raise ConnectionRefusedError(10061, "No se puede establecer una conexion")

    monkeypatch.setattr(sembrador, "Odoo", _sin_servidor)

    assert sembrador.main([]) == 2
    salida = capsys.readouterr().out
    assert "odoo_test_setup.py" in salida and salida.isascii()


def test_con_el_catalogo_ya_cargado_no_siembra_y_dice_como_empezar_de_cero(monkeypatch, capsys):
    falso = _OdooFalso(["SKU-001"])
    monkeypatch.setattr(sembrador, "Odoo", lambda *a, **kw: falso)

    codigo = sembrador.main(["--url", "http://localhost:8069", "--db", "kern_test"])

    assert codigo == 2
    assert not any(metodo == "create" for _modelo, metodo in falso.llamadas)
    salida = capsys.readouterr().out
    assert "down -v" in salida and "odoo_test_setup.py" in salida
    assert salida.isascii()
