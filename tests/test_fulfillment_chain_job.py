"""Intake and QA tests for the fulfillment_chain job."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import fulfillment_chain_job as job
from nosilo_core.guided import EXECUTED


def _long_csv(tmp_path: Path, rows: list[dict], name: str = "hitos.csv") -> Path:
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_prepare_long_maps_aliases(tmp_path):
    path = _long_csv(tmp_path, [
        {"pedido": "SO-1", "hito": "fin_packing", "fecha_hora": "2024-04-01 16:00:00",
         "sistema": "wms"},
        {"pedido": "SO-1", "hito": "fin_carga", "fecha_hora": "2024-04-01 18:00:00",
         "sistema": "wms"},
    ])
    prepared = job.prepare(str(path))
    ids = {e.milestone_id for e in prepared.events}
    assert ids == {"pack_end", "load_end"}
    scan = job.run(prepared)
    hop = next(h for h in scan.hops if h.from_id == "pack_end")
    assert hop.p50 == 2.0
    assert hop.unit == "hours"
    assert job.verify(scan) == []


def test_prepare_wide_empty_cell_is_not_zero_dwell(tmp_path):
    path = tmp_path / "wide.csv"
    pd.DataFrame([{
        "order_id": "SO-1",
        "pack_end": "2024-04-01T16:00:00",
        "load_end": "",
        "carrier_pickup": "2024-04-01T19:00:00",
        "promised_date": "2024-04-02",
    }]).to_csv(path, index=False)
    prepared = job.prepare(str(path))
    mids = {e.milestone_id for e in prepared.events}
    assert "load_end" not in mids
    assert prepared.coverage.load_end.reports is False
    scan = job.run(prepared)
    pack_load = next(h for h in scan.hops if h.from_id == "pack_end")
    assert pack_load.p50 is None
    assert scan.uninstrumented_late == 0
    assert scan.uninstrumented_late_scored == 0


def test_unknown_milestone_token_is_needs_data(tmp_path):
    path = _long_csv(tmp_path, [
        {"order_id": "SO-1", "milestone_id": "airport_on_time", "occurred_at": "2024-04-01"},
    ])
    with pytest.raises(ValueError, match="airport_on_time"):
        job.prepare(str(path))


def test_inverted_timestamps_fail_qa(tmp_path):
    path = _long_csv(tmp_path, [
        {"order_id": "SO-1", "milestone_id": "pack_end", "occurred_at": "2024-04-01T21:00:00"},
        {"order_id": "SO-1", "milestone_id": "load_end", "occurred_at": "2024-04-01T19:00:00"},
    ])
    scan = job.run(job.prepare(str(path)))
    issues = job.verify(scan)
    assert issues
    assert "inversion" in issues[0]


@pytest.mark.skip(
    reason=(
        "El motor de esta rama esta entero y cableado, pero la tool NO se registra: el "
        "registro esta congelado en 58 (tests/test_registry_frozen.py, "
        "documentation/ADR-2026-09-operador.md seccion 3) y descongelarlo es una decision "
        "de producto, no de codigo. Mismo precedente que meli_presale, que tiene el motor "
        "vivo y la registracion pendiente. El dia que se descongele: registrar "
        "fulfillment_chain_tool() en los 7 lugares de siempre y BORRAR este skip -- es la "
        "unica linea que separa este job de ser ruteable."
    )
)
def test_brief_routes_to_fulfillment_chain():
    from nosilo_core import llm
    from scm_agent import intent, tools

    reg = tools.build_default_registry()
    res = intent.classify(
        "cadena de hitos y dwell por etapa en timestamps wms tms",
        reg,
        llm.RulesFallback(),
    )
    assert res.job_type == "fulfillment_chain"


def test_fde_shaped_export_maps_opaque_names(tmp_path):
    path = tmp_path / "planning.csv"
    pd.DataFrame([{
        "PLNORD": "1001",
        "pack_end": "2024-04-01T16:00:00",
        "load_end": "2024-04-01T18:00:00",
        "promised_date": "2024-04-03",
        "arrived": "2024-04-05T09:00:00",
    }]).to_csv(path, index=False)
    prepared = job.prepare(str(path))
    assert {e.order_id for e in prepared.events} == {"1001"}
    scan = job.run(prepared)
    assert scan.uninstrumented_late == 1
    assert scan.outcome.status == EXECUTED
    assert job.verify(scan) == []
    written = job.write_operational(scan, tmp_path / "out", "Acme")
    assert written["csv"].exists()


# ---- la confianza del deck se deriva, no se declara -------------------------

def _csv_con_hitos(tmp_path: Path, hitos: list[str], name: str = "c.csv") -> Path:
    """Un pedido que pasa por los hitos pedidos, una hora entre cada uno."""
    return _long_csv(tmp_path, [
        {"pedido": "SO-1", "hito": h, "fecha_hora": f"2024-04-01 {8 + i:02d}:00:00",
         "sistema": "wms"}
        for i, h in enumerate(hitos)
    ], name=name)


def test_una_cadena_con_huecos_vale_menos_que_una_completa(tmp_path):
    """Un 0.6 literal decia lo mismo con seis relojes que con dos.

    El deck de este job declaraba `confidence: float = 0.6` a mano, que es lo que
    el trinquete de tests/test_qa_confidence_from_coverage.py existe para cazar:
    una confianza que no se mueve no informa nada.
    """
    completa = job.run(job.prepare(str(_csv_con_hitos(tmp_path, [
        "order_created", "released_to_warehouse", "pack_end",
        "load_end", "carrier_pickup", "arrived",
    ], "completa.csv"))))
    parcial = job.run(job.prepare(str(_csv_con_hitos(tmp_path, [
        "pack_end", "load_end",
    ], "parcial.csv"))))

    assert job.build_deck(parcial).confidence < job.build_deck(completa).confidence


def test_cada_hito_ausente_se_paga_una_vez(tmp_path):
    """Perder dos relojes cuesta mas que perder uno: cada hueco parte la cadena
    en un lugar distinto, y `confidence_from_coverage` cobra por ocurrencia."""
    faltan_dos = job.run(job.prepare(str(_csv_con_hitos(tmp_path, [
        "order_created", "released_to_warehouse", "pack_end", "load_end",
    ], "faltan2.csv"))))
    faltan_tres = job.run(job.prepare(str(_csv_con_hitos(tmp_path, [
        "order_created", "released_to_warehouse", "pack_end",
    ], "faltan3.csv"))))

    assert job.build_deck(faltan_tres).confidence < job.build_deck(faltan_dos).confidence


def test_un_llamador_puede_seguir_fijando_la_confianza(tmp_path):
    """La derivacion es el default, no una imposicion."""
    scan = job.run(job.prepare(str(_csv_con_hitos(tmp_path, ["fin_packing", "fin_carga"]))))

    assert job.build_deck(scan, confidence=0.42).confidence == pytest.approx(0.42)
