"""Twin sembrado con el catalogo real: carga con tres estados, payloads por grupo,
consolidacion valorizada. Fixture: 6 productos reales de Mariscos De La Concha
(2026-08-28), 13 variantes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobs import twin_catalogo as tc

FIXTURE = Path(__file__).parent / "fixtures" / "twin_catalogo" / "catalogo_mdlc_6.json"


def test_cargar_catalogo_cuenta_los_tres_estados_del_sku():
    """MAFI vive en dos variantes de distinto peso -> las dos son 'contradicho'
    y llevan clave MAFI#<variant_id>. Sin SKU -> 'no_declarado' con clave
    id:<variant_id>. Con SKU unico -> 'declarado' con clave = sku."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert len(variantes) == 13
    assert cob.sku_contradicho == 2
    assert cob.sku_no_declarado == 8
    assert cob.sku_declarado == 3
    claves = {v.clave for v in variantes}
    assert "MAFI#47628018876704" in claves and "MAFI#47628018909472" in claves
    assert "SAS454" in claves and "ZCCC" in claves and "PINB" in claves
    assert "id:52204278513952" in claves
    assert all(v.estado_sku in (tc.ESTADO_DECLARADO, tc.ESTADO_NO_DECLARADO, tc.ESTADO_CONTRADICHO)
               for v in variantes)


def test_cargar_catalogo_funde_los_grupos_chicos_en_otros():
    """Pescados tiene 10 variantes y queda; Mariscos (1), Cerdo (1) y Otros (1)
    no llegan al minimo de 5 y se funden en 'Otros'."""
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert dict(cob.grupos) == {"Pescados": 10, tc.GRUPO_OTROS: 3}
    assert {v.grupo for v in variantes if v.sku == "ZCCC"} == {tc.GRUPO_OTROS}


def test_cargar_catalogo_declara_precio_y_quiebre_de_hoy():
    variantes, cob = tc.cargar_catalogo(FIXTURE)
    assert cob.precio_declarado == 13 and cob.precio_excluido == 0
    assert cob.quiebre_hoy == 7   # MAFI 0.4/0.5/0.6 kg + Salmon filete x4
    mafi_1kg = next(v for v in variantes if v.clave == "MAFI#47628018909472")
    assert mafi_1kg.precio == 14450.0 and mafi_1kg.disponible is True


def test_cargar_catalogo_excluye_y_cuenta_la_variante_sin_precio(tmp_path):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data[0]["variants"][0]["price"] = "0"
    p = tmp_path / "cat.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    variantes, cob = tc.cargar_catalogo(p)
    assert len(variantes) == 12 and cob.precio_excluido == 1


def test_cargar_catalogo_levanta_valueerror_nombrando_el_campo_que_falta(tmp_path):
    p = tmp_path / "malo.json"
    p.write_text(json.dumps([{"id": 1, "title": "x", "product_type": "Pescados"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="variants"):
        tc.cargar_catalogo(p)
