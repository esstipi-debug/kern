"""Wiring tests for the Espejo job: pandas-only prepare, no intake.py, no socket."""

from pathlib import Path

import pandas as pd
import pytest
from nosilo_core.guided import OPTIONS, passed_guided

from jobs import prospect_diagnostic_job as espejo
from src.prospect_diagnostic import render_report

FIXTURE = Path("tests/fixtures/espejo/prospect_sales.csv")


def test_prepare_does_not_import_intake():
    """The recipe: this job reads its own CSV. A stray intake import is a regression."""
    source = Path("jobs/prospect_diagnostic_job.py").read_text(encoding="utf-8")
    assert "from jobs.intake" not in source
    assert "from jobs import intake" not in source
    assert "import jobs.intake" not in source


def test_example_fixture_exists_and_has_spanish_headers():
    assert FIXTURE.exists(), "committed real-shaped fixture is part of the product"
    header = FIXTURE.read_text(encoding="utf-8").splitlines()[0]
    assert header == "fecha,sku,cantidad,precio"


def test_prepare_reads_the_example_fixture():
    rows = espejo.prepare(str(FIXTURE), {})
    assert len(rows) == 124
    skus = {r["product_id"] for r in rows}
    assert skus == {"ESTRELLA-1", "ESTRELLA-2", "COLA-01", "MUERTO-01"}
    assert all(r["date"] is not None for r in rows)
    assert all(r["unit_cost"] and r["unit_cost"] > 0 for r in rows)


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="required column"):
        espejo.prepare(str(csv), {})


def test_prepare_recovers_unit_price_from_line_importe(tmp_path):
    """Importe de linea / cantidad, same recovery the engine already does."""
    filas = []
    for i in range(12):
        filas.append({"fecha": f"2026-01-{i + 1:02d}", "sku": "CARO", "cantidad": 1, "importe": 100.0})
        filas.append({"fecha": f"2026-01-{i + 1:02d}", "sku": "BARATO", "cantidad": 10, "importe": 10.0})
    ruta = tmp_path / "importe.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)

    rows = espejo.prepare(str(ruta), {})
    by = {}
    for r in rows:
        by.setdefault(r["product_id"], []).append(r["unit_cost"])
    assert by["CARO"][0] == pytest.approx(100.0)
    assert by["BARATO"][0] == pytest.approx(1.0)


def test_prepare_reads_latin1_instead_of_crashing(tmp_path):
    ruta = tmp_path / "export_windows.csv"
    lineas = ["fecha,sku,cantidad,precio"]
    for i in range(12):
        lineas.append(f"2026-01-{i + 1:02d},ANO-X,10,25.5")
    texto = "\n".join(lineas).replace("ANO-X", "PEQUE\xa0O")
    ruta.write_bytes(texto.encode("latin-1"))

    rows = espejo.prepare(str(ruta), {})
    assert len(rows) == 12
    assert rows[0]["product_id"]


def test_run_on_the_example_fixture_emits_guided_options():
    report = espejo.run(espejo.prepare(str(FIXTURE), {}))

    assert report.diagnostic.concentration.skus_total == 4
    assert report.diagnostic.dead_catalog.dead_skus == 1
    assert report.diagnostic.recoverable.low is not None
    assert report.diagnostic.recoverable.high is not None
    assert report.diagnostic.service.provenance == "not_measured"
    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) >= 2
    assert sum(1 for o in report.outcome.options if o.recommended) == 1
    assert passed_guided(report.outcome)
    assert espejo.verify(report) == []
    assert report.recommended_action == "hand_the_report"


def test_build_deck_is_ascii():
    report = espejo.run(espejo.prepare(str(FIXTURE), {}))
    md = espejo.build_deck(report, client="Acme").to_markdown()
    md.encode("ascii")
    assert "not measured" in md.lower() or "range" in md.lower()


def test_write_operational_emits_ascii_report_and_csv(tmp_path):
    report = espejo.run(espejo.prepare(str(FIXTURE), {}))
    out = espejo.write_operational(report, tmp_path, "Acme")

    assert out["report"].exists()
    texto = out["report"].read_text(encoding="utf-8")
    texto.encode("ascii")
    assert "no medido" in texto.lower()
    assert out["csv"].exists()
    df = pd.read_csv(out["csv"])
    assert "product_id" in df.columns


def test_job_prepare_and_run_do_not_open_a_socket(monkeypatch):
    import socket

    def prohibido(*_a, **_k):
        raise AssertionError("el espejo abrio un socket: la promesa local se rompio")

    monkeypatch.setattr(socket, "socket", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)

    report = espejo.run(espejo.prepare(str(FIXTURE), {}))
    render_report(report.diagnostic)
    assert report.diagnostic.concentration.skus_total == 4


def test_cli_ejemplo_prints_ascii_report(capsys):
    from examples.run_espejo import main

    rc = main(["--ejemplo"])
    assert rc == 0
    out = capsys.readouterr().out
    out.encode("ascii")
    assert "DIAGNOSTICO" in out
    assert "no medido" in out.lower()
