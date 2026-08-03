"""Tests for supplier scorecards — OTIF / quality (capability M8)."""

import pytest

from src.supplier_scorecard import (
    LEAD_TIME_DECLARED,
    LEAD_TIME_MEASURED,
    SupplierScore,
    lead_time_stds_by_supplier,
    lead_time_stds_for_skus,
    score_supplier,
)


def _deliveries():
    return [
        {"on_time": True, "in_full": True, "lead_time_days": 30, "units": 1000, "defects": 2},
        {"on_time": True, "in_full": True, "lead_time_days": 32, "units": 1000, "defects": 1},
        {"on_time": True, "in_full": False, "lead_time_days": 28, "units": 1000, "defects": 0},
        {"on_time": False, "in_full": True, "lead_time_days": 40, "units": 1000, "defects": 2},
    ]


def test_otif_requires_both_on_time_and_in_full():
    s = score_supplier("Acme", _deliveries())
    assert isinstance(s, SupplierScore)
    assert s.otif == pytest.approx(0.5)        # 2 of 4 are both
    assert s.on_time_rate == pytest.approx(0.75)
    assert s.in_full_rate == pytest.approx(0.75)


def test_avg_lead_time():
    s = score_supplier("Acme", _deliveries())
    assert s.avg_lead_time == pytest.approx((30 + 32 + 28 + 40) / 4)


def test_quality_ppm():
    s = score_supplier("Acme", _deliveries())
    # 5 defects / 4000 units * 1e6 = 1250 ppm
    assert s.ppm == pytest.approx(1250.0)


def test_no_deliveries_measures_nothing():
    """Zero deliveries is an absence of evidence, not a 0% on-time-in-full record."""
    s = score_supplier("Empty", [])
    assert s.otif is None and s.deliveries == 0
    assert s.evaluable.otif == 0


# --- Lead time: medido vs declarado, y el productor de sigma_L ----------------


def _entrega(lead, **extra):
    base = {"on_time": True, "in_full": True, "lead_time_days": lead,
            "units": 100.0, "defects": 0.0, "lead_time_source": LEAD_TIME_MEASURED}
    return {**base, **extra}


def test_el_scorecard_reporta_desvio_y_p90_del_lead_time():
    card = score_supplier("ACME", [_entrega(10.0), _entrega(20.0), _entrega(30.0)])
    assert card.avg_lead_time == 20.0
    assert card.lead_time_std == pytest.approx(10.0)
    assert card.lead_time_p90 == pytest.approx(28.0)


def test_una_sola_entrega_no_tiene_desvio_y_no_se_inventa_un_cero():
    """Un sigma_L de 0.0 que parece medido borra la mitad del safety stock."""
    card = score_supplier("ACME", [_entrega(10.0)])
    assert card.lead_time_std is None
    assert card.lead_time_p90 == 10.0


def test_la_brecha_contra_el_lt_declarado_es_reportada_no_pisada():
    card = score_supplier("ACME", [
        _entrega(20.0, declared_lead_time_days=7.0),
        _entrega(24.0, declared_lead_time_days=7.0),
    ])
    assert card.avg_lead_time == 22.0        # lo medido manda
    assert card.declared_lead_time == 7.0
    assert card.lead_time_gap == 15.0
    assert card.lead_time_source == LEAD_TIME_MEASURED


def test_sigma_l_solo_sale_de_lead_times_medidos():
    """El productor que le faltaba a inventory_optimization(lead_time_stds=...).

    Un proveedor cuyo LT es DECLARADO no aporta sigma: es una promesa, no una
    observacion, y su varianza es cero por construccion.
    """
    medido = score_supplier("MEDIDO", [_entrega(10.0), _entrega(20.0)])
    declarado = score_supplier("DECLARADO", [
        _entrega(7.0, lead_time_source=LEAD_TIME_DECLARED),
        _entrega(7.0, lead_time_source=LEAD_TIME_DECLARED),
    ])
    stds = lead_time_stds_by_supplier([medido, declarado])
    assert set(stds) == {"MEDIDO"}
    assert lead_time_stds_for_skus([medido, declarado], {"SKU-1": "MEDIDO", "SKU-2": "DECLARADO"}) == {
        "SKU-1": stds["MEDIDO"]
    }


def test_una_brecha_material_nombra_los_sku_cuyo_ss_rop_quedo_viejo():
    """La observacion sin la lista de SKU no es una accion: nadie sabe que recalcular."""
    card = score_supplier("ACME", [
        _entrega(20.0, declared_lead_time_days=7.0, product_id="SKU-B"),
        _entrega(24.0, declared_lead_time_days=7.0, product_id="SKU-A"),
        _entrega(22.0, declared_lead_time_days=7.0, product_id="SKU-A"),
    ])
    assert card.lead_time_gap == 15.0
    assert card.skus_to_recalculate == ("SKU-A", "SKU-B")


def test_una_brecha_inmaterial_no_manda_a_recalcular_nada():
    """22 vs 21 dias no justifica redimensionar: la lista tiene que quedar vacia."""
    card = score_supplier("ACME", [
        _entrega(22.0, declared_lead_time_days=21.0, product_id="SKU-A"),
        _entrega(22.0, declared_lead_time_days=21.0, product_id="SKU-B"),
    ])
    assert card.lead_time_gap == 1.0
    assert card.skus_to_recalculate == ()
