"""E2E: la grilla completa - piso, Kern y oraculo sobre el mismo mundo.

Escalas deliberadamente chicas (ver el modulo bajo prueba y el reporte de la
Task 11): `kern_policy` corre el orquestador real (~0.7-2s por llamada a esta
escala) y `oracle_orders` resuelve un MILP por SKU por semana (~50-60ms). Los
fixtures de este archivo se eligieron midiendo tiempos reales, no adivinando.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from examples.run_world_sim import run_grid, simulate, simulate_oracle
from src.simworld import runners as runners_mod
from src.simworld.runners import Policy, naive_policy, oracle_orders
from src.simworld.scorer import capture
from src.simworld.world import generate_world


def _naive_plan(world, outcomes, week):
    return naive_policy(world, outcomes), False


# ---------------------------------------------------------------------------
# simulate()
# ---------------------------------------------------------------------------


def test_simulate_returns_one_outcome_and_one_flag_per_week():
    w = generate_world(seed=3, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    outs, flags = simulate(w, _naive_plan, replan_every=4)
    assert len(outs) == 10
    assert len(flags) == 10


def test_replan_every_controls_how_often_the_policy_is_recomputed():
    w = generate_world(seed=3, n_skus=3, n_suppliers=1, weeks=12, regime="seasonal_trend")
    calls = []

    def plan(world, outcomes, week):
        calls.append(week)
        return naive_policy(world, outcomes), False

    simulate(w, plan, replan_every=4)
    assert calls == [0, 4, 8]


def test_the_held_policy_is_reapplied_against_the_current_position_each_week():
    """Entre replanificaciones la politica se aplica de nuevo contra la
    posicion actual, no se repite la orden a ciegas: si ya hay stock en
    camino, esta semana se pide menos."""
    w = generate_world(seed=3, n_skus=2, n_suppliers=1, weeks=6, regime="seasonal_trend")
    fixed = Policy(
        reorder_point={s.sku: 400.0 for s in w.skus},
        order_up_to={s.sku: 400.0 for s in w.skus},
        announced=False, note="", status="ok",
    )
    outs, _ = simulate(w, lambda wo, o, wk: (fixed, False), replan_every=99)
    assert outs[0].ordered.sum() > outs[1].ordered.sum()


def test_replan_every_below_one_raises():
    w = generate_world(seed=3, n_skus=2, n_suppliers=1, weeks=4, regime="seasonal_trend")
    with pytest.raises(ValueError, match="replan_every"):
        simulate(w, _naive_plan, replan_every=0)


# ---------------------------------------------------------------------------
# simulate_oracle()
# ---------------------------------------------------------------------------


def test_simulate_oracle_runs_the_full_horizon():
    w = generate_world(seed=3, n_skus=4, n_suppliers=2, weeks=10, regime="seasonal_trend")
    assert len(simulate_oracle(w)) == 10


def test_simulate_oracle_never_touches_the_lens():
    """El oraculo es clarividente, no un corredor de politica: no debe pasar
    nunca por `export_history` (la lente sucia que Kern lee). Discrimina
    contra una version futura que, por accidente, enrutara al oraculo a
    traves de la misma lente que Kern -- eso corromperia el techo mismo, no
    solo lo que se mide contra el."""
    w = generate_world(seed=6, n_skus=2, n_suppliers=1, weeks=6, regime="seasonal_trend")
    original = runners_mod.export_history
    with patch.object(runners_mod, "export_history", wraps=original) as spy:
        simulate_oracle(w)
    assert spy.call_count == 0


# ---------------------------------------------------------------------------
# run_grid() sin Kern: piso vs techo, escritura de archivos
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def floor_ceiling_report(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("floor_ceiling")
    report = run_grid(
        seed=4, n_skus=6, n_suppliers=2, weeks=20, levels=(0,),
        out_dir=out_dir, include_kern=False,
    )
    return report, out_dir


def test_oracle_beats_naive_and_the_grid_reports_it(floor_ceiling_report):
    report, _ = floor_ceiling_report
    assert report["floor"]["total_cost"] > report["ceiling"]["total_cost"]


def test_grid_writes_a_json_and_a_markdown_report(floor_ceiling_report):
    report, out_dir = floor_ceiling_report
    assert (out_dir / "report.json").exists()
    assert (out_dir / "report.md").exists()
    payload = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
    assert payload["seed"] == 4


# Fijado corriendo run_grid(seed=4, n_skus=6, n_suppliers=2, weeks=20,
# levels=(0,), out_dir=..., include_kern=False) una vez y anotando los
# numeros reales (ver task-11-report.md, Step 5). Si esto cambia, cambio la
# fisica o la generacion del mundo -- y eso tiene que ser una decision, no
# un accidente.
GOLDEN_FLOOR_COST = 173353.2065594952
GOLDEN_CEILING_COST = 125825.87317307692


def test_golden_floor_and_ceiling_costs_are_stable(floor_ceiling_report):
    """No se pin-ea `ceiling.fill_rate > floor.fill_rate` a proposito: el
    brief original (stale) lo asumia, pero la revision post-hoc del oraculo
    (Task 7) documenta que un oraculo optimo en COSTO gana en costo mucho
    mas seguido de lo que gana en unidades perdidas -- a veces acepta un
    quiebre porque es mas barato que evitarlo. Medido directamente en este
    fixture: ceiling.fill_rate (0.565) < floor.fill_rate (0.628) mientras
    ceiling.total_cost sigue siendo estrictamente menor. Afirmar lo
    contrario aca haria un test flakey/falso contra el comportamiento
    correcto y documentado del oraculo."""
    report, _ = floor_ceiling_report
    assert report["floor"]["total_cost"] == pytest.approx(GOLDEN_FLOOR_COST, rel=1e-6)
    assert report["ceiling"]["total_cost"] == pytest.approx(GOLDEN_CEILING_COST, rel=1e-6)


# ---------------------------------------------------------------------------
# run_grid() con Kern: identidad de mundo/semilla, la lente solo para Kern,
# la grilla realmente varia lo que dice variar, consistencia de captura.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def kern_grid_report(tmp_path_factory):
    """Escala minima (2 SKUs, 2 proveedores, 12 semanas, niveles 0 y 3) donde
    ya se verifico empiricamente (no solo se asumio) que Kern escala a
    'escalated' en el nivel 3 y se queda en 'ok' en el nivel 0 -- ver el
    reporte de la tarea para la corrida de verificacion. ~4-6s en total."""
    out_dir = tmp_path_factory.mktemp("kern_grid")
    original_export = runners_mod.export_history
    original_generate = None
    import examples.run_world_sim as cli_mod

    original_generate = cli_mod.generate_world
    calls = {"generate_world": 0, "export_history": []}

    def counting_generate_world(*args, **kwargs):
        calls["generate_world"] += 1
        return original_generate(*args, **kwargs)

    def spying_export_history(world, outcomes, path, *, level, seed):
        calls["export_history"].append({"world": world, "level": level})
        return original_export(world, outcomes, path, level=level, seed=seed)

    with patch.object(cli_mod, "generate_world", counting_generate_world), \
         patch.object(runners_mod, "export_history", spying_export_history):
        report = run_grid(
            seed=4, n_skus=2, n_suppliers=2, weeks=12, levels=(0, 3),
            replan_every=4, out_dir=out_dir, include_kern=True,
        )
    return report, out_dir, calls


def test_run_grid_generates_the_world_exactly_once(kern_grid_report):
    """Discrimina contra un `run_grid` que regenera el mundo (con una
    semilla fresca o distinta) en cada nivel del grid -- eso invalidaria en
    silencio la comparacion entre niveles, que depende de que todos vean la
    MISMA realizacion de demanda/lead time.

    Replay: cambiar `world = generate_world(...)` a que viva DENTRO del loop
    de niveles hace que este assert falle (2 llamadas en vez de 1) --
    confirmado abajo en el reporte de la tarea."""
    _, _, calls = kern_grid_report
    assert calls["generate_world"] == 1


def test_kern_policy_always_sees_the_same_world_object_run_grid_used(kern_grid_report):
    """Discrimina contra un bug donde `kern_policy` recibe una copia o una
    reconstruccion del mundo en vez del mismo objeto que gobierna el piso y
    el techo -- identidad de objeto (`is`), no solo igualdad de campos."""
    report, _, calls = kern_grid_report
    export_calls = calls["export_history"]
    assert len(export_calls) >= 2  # al menos un replan real por nivel
    first_world = export_calls[0]["world"]
    assert all(c["world"] is first_world for c in export_calls)


def test_run_grid_only_calls_the_lens_for_kern_never_for_the_floor_or_oracle():
    """Discrimina contra un `run_grid` que, por error, enruta el piso o el
    oraculo tambien por `export_history` -- contariamos el techo (o el piso)
    contra datos que nunca deberian haber visto.

    Cuenta las llamadas reales: con 2 niveles x 1 replan minimo cada uno,
    debe haber EXACTAMENTE ese numero de llamadas a la lente, ni una mas.

    Parchea **las dos** referencias posibles a `export_history` --
    `src.simworld.runners.export_history` (la que `kern_policy` ya trae
    ligada desde su propio `from ... import`) Y `src.simworld.lens.export_history`
    (el origen, por si algun codigo futuro la importa de nuevo directo desde
    ahi). Un primer intento de este test que solo parcheaba la primera NO
    detecto un mutante que agregaba un `from src.simworld.lens import
    export_history` fresco dentro de `run_grid` -- ese import nuevo tomaba
    la version SIN parchear porque el parche vive en el namespace de
    `runners`, no en el de `lens`. Confirmado con el mutante real, ver el
    reporte de la tarea."""
    from src.simworld import lens as lens_mod

    original = lens_mod.export_history
    call_count = {"n": 0}

    def counting(world, outcomes, path, *, level, seed):
        call_count["n"] += 1
        return original(world, outcomes, path, level=level, seed=seed)

    with patch.object(runners_mod, "export_history", counting), \
         patch.object(lens_mod, "export_history", counting):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            report = run_grid(
                seed=7, n_skus=2, n_suppliers=2, weeks=8, levels=(0,),
                replan_every=4, out_dir=tmp, include_kern=True,
            )
    # weeks=8, replan_every=4 -> replans en semana 0 (sin historia, no
    # llama a Kern) y semana 4 (si llama) = exactamente 1 llamada real.
    assert call_count["n"] == 1
    assert "forced-L0" in report["kern"]


def test_dirt_levels_genuinely_change_kerns_recommendation_not_a_cached_reuse(kern_grid_report):
    """Discrimina contra un bug de memoizacion/plumbing que reutiliza el
    resultado del nivel 0 para cualquier nivel pedido (o que hardcodea
    `level=0` en la clausura de `kern_plan`): si eso pasara, el nivel 3
    tendria el MISMO costo, los MISMOS conteos de estado y la MISMA
    politica que el nivel 0. Aca no: en este fixture, el nivel 3 escala
    (degrada la calidad de datos por debajo del umbral propio de Kern) y el
    nivel 0 no."""
    report, _, _ = kern_grid_report
    l0 = report["kern"]["forced-L0"]
    l3 = report["kern"]["forced-L3"]
    assert l0["kpis"]["total_cost"] != pytest.approx(l3["kpis"]["total_cost"])
    assert l0["replan_status_counts"] != l3["replan_status_counts"]


def test_report_capture_matches_calling_capture_directly(kern_grid_report):
    """El numero de captura que termina en el reporte tiene que ser
    exactamente el que produce `capture()` llamado a mano con los mismos
    tres costos -- no un valor recalculado con otro orden de argumentos o
    con un redondeo distinto en el camino."""
    report, _, _ = kern_grid_report
    for entry in report["kern"].values():
        if entry.get("capture") is None:
            continue
        expected = capture(
            floor_cost=report["floor"]["total_cost"],
            kern_cost=entry["kpis"]["total_cost"],
            oracle_cost=report["ceiling"]["total_cost"],
        )
        assert entry["capture"] == pytest.approx(expected)


def test_degradation_is_reported_only_for_dirty_levels_not_the_clean_baseline(kern_grid_report):
    report, _, _ = kern_grid_report
    assert "degradation" not in report["kern"]["forced-L0"]
    assert "degradation" in report["kern"]["forced-L3"]
    deg = report["kern"]["forced-L3"]["degradation"]
    assert deg["level"] == 3
    assert deg["silent"] == pytest.approx(deg["drop"] - deg["announced"])


def test_degradation_largest_week_is_present_and_bounded(kern_grid_report):
    report, _, _ = kern_grid_report
    largest = report["kern"]["forced-L3"]["degradation_largest_week"]
    assert 0 <= largest["week"] < 12
    assert isinstance(largest["value"], float)


# ---------------------------------------------------------------------------
# Mundo real, oraculo real: reconciliacion de escalas y grid vacio de Kern
# ---------------------------------------------------------------------------


def test_run_grid_lets_a_capture_information_leak_crash_instead_of_hiding_it():
    """`run_grid` decidio, a proposito, NO envolver la llamada a `capture()`
    en un try/except generico quo silenciaria una fuga de informacion real
    (Kern superando al oraculo clarividente) detras de un inocuo
    `capture: null` en el reporte -- ver el reporte de la tarea.

    Se fuerza el escenario parcheando `capture` (tal como quedo importado en
    `examples.run_world_sim`) para que levante como si hubiera fuga real, en
    vez de esperar a que aparezca sola: a esta escala, con Kern
    comportandose normal, nunca se da la condicion de fuga -- forzarla es la
    unica forma de ejercitar esta rama sin inventar un mundo patologico.

    Replay: envolver la asignacion de `entry['capture']` en `run_grid` con
    `try/except ValueError: entry['capture'] = None` (el patron exacto del
    brief stale) hace que este test, que espera que la excepcion SALGA de
    `run_grid`, falle -- confirmado abajo en el reporte de la tarea.
    """
    import examples.run_world_sim as cli_mod

    def fake_capture(**kwargs):
        raise ValueError("captura 1.5 > 1: fuga de informacion (forzado por el test)")

    with patch.object(cli_mod, "capture", fake_capture):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(ValueError, match="fuga de informacion"):
                run_grid(
                    seed=9, n_skus=2, n_suppliers=1, weeks=8, levels=(0,),
                    replan_every=4, out_dir=tmp, include_kern=True,
                )


def test_simulate_oracle_calls_oracle_orders_with_only_world_and_state():
    """Discrimina contra una version futura de `simulate_oracle` que le
    pasara `level`/`seed` a `oracle_orders` (por ejemplo, para 'simplificar'
    enrutandolo tambien por la lente) -- `oracle_orders` no tiene esos
    parametros; un mutante que se los pasara igual (via **kwargs mal
    propagados) fallaria en runtime con un `TypeError` mucho menos legible
    que este assert dirigido."""
    import examples.run_world_sim as cli_mod

    original = oracle_orders
    calls = []

    def spying(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    w = generate_world(seed=6, n_skus=2, n_suppliers=1, weeks=5, regime="seasonal_trend")
    with patch.object(cli_mod, "oracle_orders", spying):
        cli_mod.simulate_oracle(w)

    assert len(calls) == 5
    for args, kwargs in calls:
        assert len(args) == 2  # (world, state) posicionales, nada mas
        assert kwargs == {}
