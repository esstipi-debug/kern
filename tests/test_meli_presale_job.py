"""Tests for jobs/meli_presale_job.py and examples/run_meli_presale.py."""

from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd
import pytest

from jobs.meli_presale_job import (
    build_deliverable,
    prepare,
    run,
    verify,
    write_report_csv,
)
from src.connectors.meli_catalog import demo_meli_catalog
from src.meli_presale import PresaleQuery

_REPO = Path(__file__).resolve().parents[1]


def test_prepare_reads_q_and_gtin_columns(tmp_path: Path):
    csv = tmp_path / "q.csv"
    pd.DataFrame([
        {"title": "Auriculares inalambricos demo", "sku": "A"},
        {"gtin": "7790000000003", "sku": "B"},
    ]).to_csv(csv, index=False)
    queries = prepare(csv)
    assert len(queries) == 2
    assert queries[0].q.startswith("Auriculares")
    assert queries[1].product_identifier == "7790000000003"


def test_prepare_rejects_empty_csv(tmp_path: Path):
    csv = tmp_path / "empty.csv"
    pd.DataFrame([{"notes": "nada"}]).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="q/title or gtin"):
        prepare(csv)


def test_run_and_verify_demo_catalog(tmp_path: Path):
    queries = [
        PresaleQuery(q="Auriculares inalambricos demo", sku="SKU-PRE-1"),
        PresaleQuery(q="Cable USB-C demo"),
        PresaleQuery(product_identifier="7790000000003"),
        PresaleQuery(q="Cargador ya publicado demo"),
        PresaleQuery(q="producto que no existe en el catalogo"),
    ]
    report = run(queries, demo_meli_catalog(), demo=True)
    assert report.n_queries == 5
    assert report.n_presale_ready == 1
    assert report.n_catalog_only == 1
    assert report.n_already_listed == 1
    assert report.n_no_match == 1
    assert verify(report) == []
    assert "No listing was published" in report.summary

    csv_path = write_report_csv(report, tmp_path / "out.csv")
    table = pd.read_csv(csv_path)
    assert "presale_ready" in set(table["status"])
    assert int((table["status"] == "presale_ready").sum()) == 1

    deck = build_deliverable(report, client="Marcos")
    md = deck.to_markdown()
    assert "Preventa" in deck.title or "preventa" in deck.title.lower()
    assert "0" in [k.value for k in deck.kpis if k.name == "Published"]
    assert "DEMO" in deck.residual
    assert "Marcos" in md or "Marcos" == deck.client


def test_verify_rejects_a_posted_draft():
    report = run(
        [PresaleQuery(q="Auriculares inalambricos demo")],
        demo_meli_catalog(),
        demo=True,
    )
    posted = report.scan.candidates[0].draft
    assert posted is not None
    # Frozen candidate -- rebuild a report-shaped object by mutating via replace
    from dataclasses import replace

    from src.meli_presale import PresaleScan

    dirty = replace(report.scan.candidates[0], draft={**posted, "_posted": True})
    dirty_scan = PresaleScan(candidates=(dirty,), listed_checked=True)
    dirty_report = replace(report, scan=dirty_scan)
    problems = verify(dirty_report)
    assert any("posted" in p for p in problems)


def test_job_and_cli_never_import_writeback():
    for rel in (
        "jobs/meli_presale_job.py",
        "examples/run_meli_presale.py",
        "src/meli_presale.py",
        "src/connectors/meli_catalog.py",
    ):
        text = (_REPO / rel).read_text(encoding="utf-8")
        assert "from nosilo_core" not in text, rel
        assert "import nosilo_core" not in text, rel


def test_cli_demo_writes_csv_and_never_publishes(tmp_path: Path, capsys):
    cli = importlib.import_module("examples.run_meli_presale")
    rc = cli.main(["--demo", "--out", str(tmp_path), "--client", "Demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "presale_ready:  1" in out
    assert "published:      0" in out
    assert (tmp_path / "meli_presale.csv").is_file()
    assert (tmp_path / "meli_presale.md").is_file()
    md = (tmp_path / "meli_presale.md").read_text(encoding="utf-8")
    assert "never POSTs" in md or "No listing was published" in md or "DEMO" in md


def test_cli_without_demo_or_csv_exits_2():
    cli = importlib.import_module("examples.run_meli_presale")
    rc = cli.main([])
    assert rc == 2
