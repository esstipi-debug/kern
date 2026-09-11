"""ISO operations job: detectar + tareas de piso + evidencia de orden, never certify."""

from __future__ import annotations

from pathlib import Path

from nosilo_core.guided import HANDOFF, OPTIONS

from jobs import iso_ops_job
from src.iso_catalog import HUECO_14083_TKM, texto_prohibido

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "iso"
_JSON = _FIXTURES / "detectar.ejemplo.json"
_CSV = _FIXTURES / "planta.csv"
_FORBIDDEN = ("cumple iso", "esta certificado", "planta certificada")


def test_prepare_json_oem_detects_iatf_and_14083_gap(tmp_path):
    prepared = iso_ops_job.prepare(str(_JSON), {})
    report = iso_ops_job.run(prepared)
    assert report.n_detectadas > 0
    ids = {row["id"] for row in report.detectadas}
    assert "iatf-16949" in ids
    iso_14083 = next(r for r in report.detectadas if r["id"] == "iso-14083")
    assert HUECO_14083_TKM in iso_14083["huecos"]
    assert iso_14083["limite"]
    assert iso_14083["claimPermitido"]
    assert iso_14083["tareas"]
    assert report.outcome.status == OPTIONS
    assert iso_ops_job.verify(report) == []
    for blob in (report.summary, *(r["claimPermitido"] for r in report.detectadas)):
        assert not texto_prohibido(blob)
        lowered = blob.lower()
        assert all(token not in lowered for token in _FORBIDDEN)


def test_prepare_csv_matches_json_triggers():
    from_json = iso_ops_job.run(iso_ops_job.prepare(str(_JSON), {}))
    from_csv = iso_ops_job.run(iso_ops_job.prepare(str(_CSV), {}))
    assert {r["id"] for r in from_csv.detectadas} == {r["id"] for r in from_json.detectadas}
    # Solo 9001 puede encender una tarjeta: 50001 y 14067 quedaron fuera de alcance
    # cuando se verifico que KERN no ingesta kWh ni factores de emision.
    assert from_csv.n_nativas_orden == 1
    assert from_json.n_nativas_orden == 0  # ejemplo JSON has no order signals


def test_empty_profile_detects_nothing_and_handoffs():
    prepared = iso_ops_job.prepare(None, {
        "sectores": [], "clientePide": [], "logistica": [], "planta": [], "kern": [], "texto": "",
    })
    report = iso_ops_job.run(prepared)
    assert report.perfil_vacio
    assert report.detectadas == ()
    assert report.n_detectadas == 0
    assert report.outcome.status == HANDOFF
    assert "cero normas" in report.summary.lower() or "ficha vacia" in report.summary.lower()
    assert iso_ops_job.verify(report) == []


def test_params_override_file_tkm_clears_14083_gap():
    prepared = iso_ops_job.prepare(str(_CSV), {"tkm": True})
    report = iso_ops_job.run(prepared)
    iso_14083 = next(r for r in report.detectadas if r["id"] == "iso-14083")
    assert iso_14083["huecos"] == []


def test_unknown_kern_module_raises():
    try:
        iso_ops_job.prepare(None, {"kern": ["not-a-module"], "sectores": ["automotriz"]})
    except ValueError as exc:
        assert "modulo kern desconocido" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown kern module")


def test_write_operational_and_deck_never_claim_certificate(tmp_path):
    report = iso_ops_job.run(iso_ops_job.prepare(str(_CSV), {}))
    paths = iso_ops_job.write_operational(report, tmp_path, "Planta")
    assert paths["csv"].exists()
    assert paths["tareas_csv"].exists()
    assert paths["excel"].exists()
    assert paths["orden_csv"].exists()
    deck = iso_ops_job.build_deck(report, client="Planta")
    text = deck.to_markdown().lower()
    assert "cumple iso" not in text
    assert "esta certificado" not in text
    assert "planta certificada" not in text
    assert deck.findings
    written = deck.write_all(tmp_path)
    assert written
