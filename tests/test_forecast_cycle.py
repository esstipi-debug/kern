"""El pronostico tiene que acordarse del ciclo anterior, y decirlo cuando no hay.

Lo que se prueba aca es la memoria entre corridas de ``jobs/forecast_job.run``:
congela el ciclo en ``src/state`` y compara el MAPE y el sesgo firmado contra el
ciclo previo. Dos filos que no son negociables:

- la primera corrida NO devuelve ceros ni ``None`` mudos: dice con todas las
  letras que no hay contra que comparar;
- congelar es un efecto lateral del entregable, no el entregable: un store que
  rechaza la escritura, o un parquet que alguien borro a mano, cuesta la
  comparacion y nunca la corrida.

Cada test arma su propio ``StateStore`` sobre ``tmp_path`` y se lo pasa explicito
a ``run()``, para no leer ni escribir el ``data/state`` real (es la disciplina
que documenta ``src/state/store.default_store``).
"""

from __future__ import annotations

import math

import pytest

from jobs import forecast_cycle as fc
from jobs import forecast_job as fj
from src.state.store import StateStore
from src.state.system_state import history

_ESTABLE = [100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 97.0, 100.0,
            101.0, 99.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0]
_VOLATIL = [60.0, 140.0, 55.0, 150.0, 70.0, 130.0, 45.0, 160.0,
            80.0, 120.0, 50.0, 155.0, 65.0, 145.0, 75.0, 125.0]


@pytest.fixture
def store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state")


# -- primera corrida: lo dice, no lo inventa ----------------------------------


def test_first_cycle_says_there_is_no_previous_one(store):
    report = fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")

    assert report.previous_cycle_id is None
    assert report.previous_mean_mape is None
    assert report.previous_mean_bias is None
    assert report.mean_mape_vs_previous is None
    assert report.mean_bias_vs_previous is None
    assert "no previous cycle" in report.cycle_comparison
    assert report.cycle_id == "c1"
    assert fj.verify(report) == []


# -- segunda corrida: compara, y el signo dice si mejoro o empeoro -------------


def test_second_cycle_compares_against_the_first_and_the_sign_is_right(store):
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")
    worse = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c2")

    assert worse.previous_cycle_id == "c1"
    assert worse.previous_mean_mape == pytest.approx(0.005, abs=0.01)
    assert worse.mean_mape_vs_previous > 0.0            # el error subio -> empeoro
    assert worse.mean_mape_vs_previous == pytest.approx(
        worse.mean_mape - worse.previous_mean_mape
    )
    assert worse.mean_bias_vs_previous is not None
    assert "worsened" in worse.cycle_comparison
    assert fj.verify(worse) == []


def test_a_better_cycle_reads_as_improved(store):
    fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c1")
    better = fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c2")

    assert better.mean_mape_vs_previous < 0.0
    assert "improved" in better.cycle_comparison


def test_previous_mape_travels_per_sku_and_is_none_for_a_new_sku(store):
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")
    second = fj.run({"A": list(_VOLATIL), "NEW": list(_ESTABLE)}, store=store, cycle_id="c2")

    by = {s.name: s for s in second.skus}
    assert by["A"].previous_mape == pytest.approx(0.005, abs=0.01)
    assert by["A"].mape_change_vs_previous == pytest.approx(by["A"].mape - by["A"].previous_mape)
    assert by["NEW"].previous_mape is None              # sin historial, nunca un 0.0 fabricado
    assert by["NEW"].mape_change_vs_previous is None


# -- lo que queda escrito, bajo el contrato de esquema del dominio ------------


def test_freezing_writes_the_forecast_and_the_outcome_domains(store):
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")

    forecasts = history("forecast", store=store)
    assert len(forecasts) == 1
    assert set(forecasts[0].payload.columns) >= {"product_id", "period", "forecast_qty"}
    assert forecasts[0].payload["period"].tolist() == ["c1"]

    outcomes = history("outcomes", store=store)[0].payload
    assert set(outcomes.columns) >= {"product_id", "tool", "metric", "value"}
    assert set(outcomes["tool"]) == {fc.TOOL}
    assert set(outcomes["metric"]) == {fc.METRIC_MAPE, fc.METRIC_BIAS}


def test_a_sku_that_could_not_be_backtested_records_no_mape(store):
    """MASE/MAPE infinitos significan 'no se pudo medir'. Escribirlos como numero
    seria inventar una medicion que nadie tomo."""
    report = fj.run({"SHORT": [5.0, 6.0, 4.0]}, store=store, cycle_id="c1")

    assert not math.isfinite(report.skus[0].mape)
    outcomes = history("outcomes", store=store)[0].payload
    assert fc.METRIC_MAPE not in set(outcomes[outcomes["product_id"] == "SHORT"]["metric"])


# -- congelar es efecto lateral: cuando falla, cuesta la memoria, no la corrida -


def test_a_rejected_write_does_not_break_the_job_and_says_why(store):
    """``cycle_id`` es estrictamente creciente por dominio: repetirlo lo rechaza."""
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c2")
    repeated = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c1")  # c1 < c2 -> rechazado

    assert repeated.cycle_id is None
    assert "NOT frozen" in repeated.cycle_comparison
    assert repeated.previous_cycle_id == "c2"           # leer si funciono
    assert fj.verify(repeated) == []


def test_a_hand_written_naive_cycle_id_freezes_instead_of_exploding(store):
    """``cycle_id`` es parametro publico y documentado ("keep a run isolated and
    deterministic"), asi que el valor natural que escribe un llamador es una fecha
    sin offset. El ciclo anterior lo emitio ``now_cycle_id()``, que trae offset, y
    comparar naive contra aware levantaba ``TypeError`` -- una excepcion que
    ``freeze`` no atrapa (atrapa CycleOrderError/Duplicate/Schema/OSError), asi
    que se llevaba puesta la corrida entera, no solo la memoria."""
    primero = fj.run({"A": list(_ESTABLE)}, store=store)
    assert primero.cycle_id is not None and "+00:00" in primero.cycle_id

    despues = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="2099-08-01T00:00:00")

    assert despues.cycle_id == "2099-08-01T00:00:00"     # congelo de verdad
    assert despues.previous_cycle_id == primero.cycle_id
    assert fj.verify(despues) == []


def test_a_naive_cycle_id_before_the_stored_one_is_rejected_not_crashed(store):
    """La otra direccion del mismo cruce: fuera de orden se rechaza y cuesta la
    memoria, como cualquier otro ``cycle_id`` repetido o viejo."""
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="2099-08-01T00:00:00+00:00")

    viejo = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="2026-01-01T00:00:00")

    assert viejo.cycle_id is None
    assert "NOT frozen" in viejo.cycle_comparison
    assert fj.verify(viejo) == []


def test_an_unreadable_stored_payload_costs_the_comparison_not_the_run(store):
    """El contrato del store admite una fila indexada sin su archivo (crash a mitad
    de una poda, o limpieza a mano). No puede voltear el pronostico de hoy.

    La condicion tiene que NOMBRARSE en la nota: ese nombre es lo unico que
    distingue "el parquet no esta" de cualquier otra cosa que rompa la lectura.
    """
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")
    for record in store.list_records("outcomes"):
        (store.base_path / record.file_path).unlink()

    survived = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c2")

    assert survived.previous_cycle_id is None
    assert "could not be read" in survived.cycle_comparison
    assert "MissingPayloadError" in survived.cycle_comparison
    assert fj.verify(survived) == []


def test_a_permission_error_is_not_the_named_condition_and_takes_the_run_down(store, monkeypatch):
    """La otra mitad de "degrada sobre esta condicion SOLA", y la que faltaba.

    ``MissingPayloadError`` hereda de ``FileNotFoundError`` -> ``OSError``, asi que
    el ``except (OSError, ValueError)` anterior atrapaba el parquet borrado igual de
    bien: por eso el test de arriba pasaba con y sin fix, y no probaba nada. Lo que
    NO distinguia es un permiso denegado -- la maquina fallando, no un estado que el
    contrato del store alcance -- que se lee bien en el reintento siguiente. Taparlo
    hace que el entregable diga "no previous cycle" mientras el disco se cae, que es
    la frase que la memoria de ciclo existe para no decir.

    Antes del fix: la corrida terminaba OK con cycle_comparison = "no previous cycle
    to compare against (1 stored cycle(s) could not be read: c1 (PermissionError))".
    Despues: propaga.
    """
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")

    def denied(self, record):
        raise PermissionError(13, "Permission denied", record.file_path)

    monkeypatch.setattr(StateStore, "load_payload", denied)

    with pytest.raises(PermissionError):
        fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c2")


# -- sin store no hay memoria de ciclo, y el entregable lo dice ---------------


def test_two_datasets_without_a_store_do_not_see_each_other(store):
    """El cruce entre clientes, que es el motivo de que la memoria sea opt-in.

    Con ``store=None`` resolviendo a ``default_store()``, estas dos corridas
    compartian la particion sin nombre: el deck del cliente B decia "vs cycle
    <ciclo de A>: MAPE worsened 0.5% -> 35.0%", o sea la precision de A citada
    como la linea de base de B. Ningun llamador de ``run()`` en el arbol pasa
    store, asi que era el camino normal, no el raro.
    """
    cliente_a = fj.run({"A-SKU": list(_ESTABLE)})
    cliente_b = fj.run({"B-SKU": list(_VOLATIL)})

    assert cliente_a.previous_cycle_id is None
    assert cliente_b.previous_cycle_id is None          # no ve el ciclo de A
    assert cliente_b.previous_mean_mape is None
    assert cliente_b.mean_mape_vs_previous is None
    assert "A-SKU" not in cliente_b.cycle_comparison
    assert fj.verify(cliente_b) == []


def test_without_a_store_the_report_says_it_was_never_given_one(store):
    """Ni un cero ni un ``None`` mudo: la razon, con todas las letras, y distinta
    de la de una primera corrida CON store (ahi si se busco y no habia nada)."""
    sin_store = fj.run({"A": list(_ESTABLE)})
    primera_con_store = fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")

    assert sin_store.no_baseline_reason == "no store: cycle memory off"
    assert "no store was named" in sin_store.cycle_comparison
    assert "NOT frozen" in sin_store.cycle_comparison    # tampoco congelo nada
    assert sin_store.cycle_id is None

    assert primera_con_store.no_baseline_reason == "first cycle"
    assert "no store was named" not in primera_con_store.cycle_comparison


def test_verify_catches_a_report_that_has_no_previous_cycle_and_does_not_say_why(store):
    """El ``None`` mudo es justamente lo que la memoria de ciclo existe para no
    hacer: sin ciclo previo, el QA exige que el entregable diga cual de los tres
    motivos es."""
    import dataclasses

    mudo = dataclasses.replace(fj.run({"A": list(_ESTABLE)}), no_baseline_reason="")

    assert "does not say why" in " ".join(fj.verify(mudo))


def test_without_a_store_nothing_is_written_anywhere(store):
    """Congelar es una escritura. Sin store no hay donde, y no se inventa uno."""
    fj.run({"A": list(_ESTABLE)})

    assert store.list_records("forecast") == []
    assert store.list_records("outcomes") == []


def test_the_deck_without_a_store_does_not_claim_it_is_the_first_cycle(store):
    """El KPI decia "n/a (first cycle)" fijo. Sin store nadie miro el store, asi
    que afirmar que es la primera corrida es una afirmacion que nadie verifico."""
    md = fj.build_deck(fj.run({"A": list(_ESTABLE)}), client="Acme").to_markdown()

    assert md.isascii()
    assert "n/a (first cycle)" not in md
    assert "no store" in md


# -- aislamiento: NINGUNA corrida toca la particion compartida ----------------


def test_no_run_ever_reaches_the_shared_default_partition(store, monkeypatch):
    """Ni con store ni sin el. La version anterior solo cubria el caso con store,
    y el caso SIN store era justamente el que resolvia ``default_store()``."""
    import src.state.store as store_module

    monkeypatch.setattr(
        store_module, "default_store", lambda: pytest.fail("run() resolved default_store()")
    )

    assert fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1").cycle_id == "c1"
    assert fj.run({"A": list(_ESTABLE)}).cycle_id is None


# -- el entregable, no solo el dataclass --------------------------------------


def test_the_deck_states_the_comparison_against_the_previous_cycle(store):
    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")
    report = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c2")

    md = fj.build_deck(report, client="Acme").to_markdown()

    assert md.isascii()
    assert "Forecast accuracy vs the previous cycle" in md
    assert "MAPE vs previous cycle" in md
    assert "vs cycle c1" in md


def test_the_first_cycle_deck_says_so_instead_of_showing_a_fake_delta(store):
    report = fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")

    md = fj.build_deck(report, client="Acme").to_markdown()

    assert "no previous cycle" in md
    assert "n/a (first cycle)" in md


def test_the_operational_csv_carries_the_per_sku_comparison(store, tmp_path):
    import pandas as pd

    fj.run({"A": list(_ESTABLE)}, store=store, cycle_id="c1")
    report = fj.run({"A": list(_VOLATIL)}, store=store, cycle_id="c2")

    out = fj.write_operational(report, tmp_path / "out", "Acme")

    df = pd.read_csv(out["csv"])
    assert {"previous_mape", "mape_change_vs_previous"} <= set(df.columns)
    assert df["mape_change_vs_previous"].iloc[0] > 0
