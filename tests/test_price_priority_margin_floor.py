"""El piso de margen sobre `igualar_precio`.

`_assign_action` leía exactamente dos variables: la clase ABC y el índice de
posición. Ni costo, ni piso, ni techo. Con eso, `igualar_precio` —que significa
«bajá a mercado y defendé volumen»— podía recomendar bajar **a través del
costo**, y el único chequeo económico del sistema vivía en
`repricing.stage_repricing`, o sea recién al escribir el precio.

Este gate corre al recomendar. El piso `landed_cost × (1 + min_margin_pct)` ya
existía en `price_optimizer.py`, en el módulo de al lado.

Lo que este gate NO hace, y está anotado a propósito: no corre
`pricing_guardrails.gate_price_changeset`. Ese gate filtra *un cambio de
precio* —necesita un precio objetivo para evaluar Omnibus 30-day-lowest o MAP—
y este módulo deliberadamente nunca produce un número. La brecha legal no se
cierra acá; se cierra cuando alguien propone un precio.
"""

from __future__ import annotations

import pytest

from jobs.price_priority import (
    IGNORAR_BAJO_VALOR,
    IGUALAR_PRECIO,
    OPORTUNIDAD_SUBIR,
    REVISAR_MARGEN,
    SkuPricePosition,
    run,
    verify,
)
from src.classification import SkuClassification


def _cls(pid: str, abc: str = "A", xyz: str = "X") -> SkuClassification:
    return SkuClassification(
        product_id=pid, annual_value=1000.0, cumulative_share=0.5, abc=abc,
        mean_demand=100.0, cv=0.2, xyz=xyz, cell=f"{abc}{xyz}",
        service_level=0.95, policy="(s,Q)", buffer_distribution="normal",
    )


def _pos(pid: str, idx: float | None, *, avg: float | None = None) -> SkuPricePosition:
    return SkuPricePosition(
        product_id=pid,
        position_index=idx,
        competitor_read="confirmed" if idx is not None else "insufficient_signal",
        competitor_avg=avg,
    )


def _run(positions, *, landed_costs=None, min_margin_pct=0.0):
    payload = {
        "classifications": [_cls(p.product_id) for p in positions],
        "price_positions": list(positions),
        "band": 0.05,
        "landed_costs": landed_costs or {},
        "min_margin_pct": min_margin_pct,
    }
    return run(payload)


class TestRetrocompatible:
    def test_sin_costos_el_comportamiento_no_cambia(self) -> None:
        """El piso es opt-in. Un cliente que no aporta costos recibe
        exactamente lo que recibía antes — no un `revisar_margen` por defecto,
        que sería inventar una restricción que nadie declaró."""
        report = _run([_pos("SKU-1", 1.20, avg=100.0)])
        assert report.actions[0].action == IGUALAR_PRECIO
        assert report.n_revisar_margen == 0

    def test_sku_sin_costo_declarado_pasa_de_largo(self) -> None:
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"OTRO-SKU": 90.0}, min_margin_pct=0.10)
        assert report.actions[0].action == IGUALAR_PRECIO


class TestPiso:
    def test_igualar_que_respeta_el_piso_sigue_siendo_igualar(self) -> None:
        # piso = 50 * 1,10 = 55. Mercado en 100, muy por encima.
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 50.0}, min_margin_pct=0.10)
        assert report.actions[0].action == IGUALAR_PRECIO

    def test_igualar_que_rompe_el_piso_pasa_a_revisar_margen(self) -> None:
        # piso = 95 * 1,10 = 104,5. Mercado en 100: igualar vende bajo el piso.
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 95.0}, min_margin_pct=0.10)
        accion = report.actions[0]
        assert accion.action == REVISAR_MARGEN
        assert report.n_revisar_margen == 1
        assert report.n_igualar_precio == 0

    def test_la_razon_cita_los_dos_numeros(self) -> None:
        """Golden rule 7: la razón tiene que traer el piso y el mercado, o el
        humano no puede discutir la recomendación sin recalcularla."""
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 95.0}, min_margin_pct=0.10)
        reason = report.actions[0].reason
        assert "104.5" in reason or "104,5" in reason
        assert "100" in reason

    def test_el_borde_exacto_no_rompe_el_piso(self) -> None:
        # piso = 100 exacto, mercado 100. Igualar deja el margen justo, no bajo.
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 100.0}, min_margin_pct=0.0)
        assert report.actions[0].action == IGUALAR_PRECIO

    def test_subir_precio_nunca_lo_toca_el_piso(self) -> None:
        """`oportunidad_subir` mueve el precio hacia arriba. Un piso es una
        restricción por abajo; aplicarlo acá sería una condición que no puede
        activarse, o sea código muerto disfrazado de control."""
        report = _run([_pos("SKU-1", 0.80, avg=100.0)], landed_costs={"SKU-1": 999.0}, min_margin_pct=0.50)
        assert report.actions[0].action == OPORTUNIDAD_SUBIR

    def test_clase_C_sigue_ganando_primero(self) -> None:
        payload = {
            "classifications": [_cls("SKU-1", abc="C")],
            "price_positions": [_pos("SKU-1", 1.20, avg=100.0)],
            "band": 0.05,
            "landed_costs": {"SKU-1": 95.0},
            "min_margin_pct": 0.10,
        }
        assert run(payload).actions[0].action == IGNORAR_BAJO_VALOR

    def test_sin_promedio_de_mercado_no_se_puede_evaluar_el_piso(self) -> None:
        """Sin `competitor_avg` el piso es incomparable. La salida honesta es
        dejar la acción como estaba, no inventar un `revisar_margen` sobre un
        número que no existe."""
        report = _run([_pos("SKU-1", 1.20, avg=None)], landed_costs={"SKU-1": 95.0}, min_margin_pct=0.10)
        assert report.actions[0].action == IGUALAR_PRECIO


class TestVerify:
    def test_verify_acepta_la_accion_nueva(self) -> None:
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 95.0}, min_margin_pct=0.10)
        assert verify(report) == []

    def test_los_conteos_cierran_con_la_quinta_accion(self) -> None:
        report = _run(
            [_pos("SKU-1", 1.20, avg=100.0), _pos("SKU-2", 1.20, avg=100.0), _pos("SKU-3", 1.0, avg=100.0)],
            landed_costs={"SKU-1": 95.0}, min_margin_pct=0.10,
        )
        total = (report.n_igualar_precio + report.n_oportunidad_subir + report.n_vigilar
                 + report.n_ignorar_bajo_valor + report.n_revisar_margen)
        assert total == len(report.actions) == 3
        assert verify(report) == []

    def test_revisar_margen_sin_lectura_confirmada_es_una_falla(self) -> None:
        """`revisar_margen` se deriva de `igualar_precio`, que exige lectura
        confirmada. Fabricarlo sin señal es el mismo defecto que el invariante
        original previene, y `verify` tiene que verlo igual."""
        report = _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 95.0}, min_margin_pct=0.10)
        roto = report.actions[0].__class__(
            **{**report.actions[0].__dict__, "competitor_read": "insufficient_signal", "position_index": None}
        )
        con_falla = report.__class__(**{**report.__dict__, "actions": (roto,)})
        issues = verify(con_falla)
        assert any("revisar_margen" in i for i in issues)


class TestPrepare:
    def test_min_margin_negativo_es_un_error(self) -> None:
        """Un piso negativo significa «vendé bajo costo a propósito». Si alguien
        lo quiere, que sea explícito por otro camino; como parámetro suelto es
        casi siempre un signo invertido."""
        with pytest.raises(ValueError, match="min_margin_pct"):
            _run([_pos("SKU-1", 1.20, avg=100.0)], landed_costs={"SKU-1": 50.0}, min_margin_pct=-0.10)
