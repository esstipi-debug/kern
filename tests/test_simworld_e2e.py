"""E2E: la grilla completa - piso, Kern y oraculo sobre el mismo mundo.

Escalas deliberadamente chicas (ver el modulo bajo prueba y el reporte de la
Task 11): `kern_policy` corre el orquestador real (~0.7-2s por llamada a esta
escala) y `oracle_orders` resuelve un MILP por SKU por semana (~50-60ms). Los
fixtures de este archivo se eligieron midiendo tiempos reales, no adivinando.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from examples.run_world_sim import run_grid, simulate, simulate_oracle
from src.simworld import runners as runners_mod
from src.simworld.runners import Policy, naive_policy, oracle_orders
from src.simworld.scorer import capture
from src.simworld.scorer import degradation as real_degradation
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
    solo lo que se mide contra el.

    Parchea `lens.history_frame` (lo que `export_history` llama por dentro),
    no `export_history` por nombre: cualquier binding de `export_history`,
    sin importar donde se importe (`runners.export_history`,
    `lens.export_history`, o incluso un `from src.simworld.lens import
    export_history as _leak` a nivel de modulo dentro de
    `examples/run_world_sim.py`), termina resolviendo `history_frame` contra
    el namespace propio de `lens.py` en tiempo de llamada -- asi que
    parchear ahi intercepta la fuga sin importar como `export_history` haya
    llegado a estar ligado en otro lado. Un mutante real con ese import a
    nivel de modulo pasaba las 18 pruebas existentes SIN DETECTARSE, incluida
    la version anterior de esta prueba (que parcheaba `export_history` por
    nombre en `runners_mod`) -- confirmado en el reporte de la tarea
    (finding Important #2)."""
    from src.simworld import lens as lens_mod

    w = generate_world(seed=6, n_skus=2, n_suppliers=1, weeks=6, regime="seasonal_trend")
    original = lens_mod.history_frame
    with patch.object(lens_mod, "history_frame", wraps=original) as spy:
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


def test_floor_reorder_point_is_frozen_after_first_replan_not_recomputed_every_time():
    """El piso ('punto de reorden fijo, congelado') calcula `naive_policy`
    UNA sola vez -- en la primera replanificacion con WARMUP_WEEKS de
    historia -- y reutiliza esa MISMA `Policy` en cada replanificacion
    posterior, sin volver a mirar la historia mas reciente. Con
    weeks=20/replan_every=4 hay 5 replanificaciones (semanas 0, 4, 8, 12,
    16): la de la semana 0 no tiene historia todavia (Policy vacia), y las 4
    restantes deben devolver EXACTAMENTE el mismo `reorder_point` -- no solo
    parecido, identico campo a campo -- porque la ventana de
    NAIVE_LOOKBACK_WEEKS que `naive_policy` mira se desplaza en cada replan
    si el piso no esta genuinamente congelado, y eso cambia el promedio.

    Replay: revertir la clausura `naive_plan` (en `run_grid`) a `return
    naive_policy(w, outs), False` incondicional -- el bug real, transcrito
    tal cual del brief stale (ver `docs/superpowers/plans/2026-07-27-simworld-marcador.md`
    alrededor de la linea 2357) -- hace que esta prueba falle: confirmado
    (`4 == 1`, con los 4 reorder_point reales distintos entre si) en el
    reporte de la tarea antes de aplicar el fix, y vuelve a pasar una vez
    restaurada la clausura con memoizacion."""
    import examples.run_world_sim as cli_mod

    original_simulate = cli_mod.simulate
    plan_returns: list[dict[str, float]] = []

    def spying_simulate(world, plan, *, replan_every=4):
        def wrapped_plan(w, outs, week):
            pol, announced = plan(w, outs, week)
            plan_returns.append(dict(pol.reorder_point))
            return pol, announced
        return original_simulate(world, wrapped_plan, replan_every=replan_every)

    with patch.object(cli_mod, "simulate", spying_simulate):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cli_mod.run_grid(
                seed=4, n_skus=6, n_suppliers=2, weeks=20, levels=(0,),
                out_dir=tmp, include_kern=False,
            )

    # Semana 0: sin historia todavia -> Policy vacia ({}); semanas 4/8/12/16:
    # historia suficiente -> Policy real. Se filtra la vacia para comparar
    # solo las replanificaciones "reales" entre si.
    real_replans = [d for d in plan_returns if d]
    assert len(real_replans) == 4  # semanas 4, 8, 12, 16
    assert all(d == real_replans[0] for d in real_replans)


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
#
# Re-pineado en la revision de Task 11 (finding CRITICAL #1): el piso NO
# estaba genuinamente congelado -- `naive_plan` recalculaba `naive_policy`
# en cada replanificacion en vez de una sola vez -- asi que el numero de
# abajo (173,353.21) media un piso que replanifica cada 4 semanas, no el
# piso "punto de reorden fijo" que el modulo dice implementar. Con la
# clausura corregida (memoiza tras el primer calculo con WARMUP_WEEKS de
# historia, ver `run_grid`), el costo real y estable del piso GENUINAMENTE
# congelado en este fixture es 185,472.66 (medido directamente, ver
# task-11-report.md).
GOLDEN_FLOOR_COST = 185472.66449519232
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

    def spying_export_history(world, outcomes, path, *, level, seed, **kwargs):
        # `**kwargs` reenvia lo que la lente acepte hoy y manana (p.ej. `omit`,
        # que descompone un nivel de suciedad). Este espia observa `world` y
        # `level`; pinear la firma entera lo convertiria en un test de firmas
        # disfrazado de test de comportamiento.
        calls["export_history"].append({"world": world, "level": level})
        return original_export(world, outcomes, path, level=level, seed=seed, **kwargs)

    with patch.object(cli_mod, "generate_world", counting_generate_world), \
         patch.object(runners_mod, "export_history", spying_export_history):
        report = run_grid(
            # weeks=16, no 12: Kern opera como el piso hasta KERN_MIN_HISTORY_WEEKS
            # (8), asi que sus dos replanificaciones reales caen en las semanas 8 y
            # 12; la de la semana 4 lleva PRE_KERN_STATUS y no es suya.
            seed=4, n_skus=2, n_suppliers=2, weeks=16, levels=(0, 3),
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

    Historial de este test (por que ya no parchea `export_history` por
    nombre): la primera version solo parcheaba
    `src.simworld.runners.export_history`, y no detecto un mutante que
    agregaba un `from src.simworld.lens import export_history` fresco
    dentro de `run_grid` (ese import nuevo tomaba la version SIN parchear
    porque el parche vivia en el namespace de `runners`, no en el de
    `lens`). La segunda version parcheo **ambas** referencias
    (`runners.export_history` Y `lens.export_history`) -- pero una revision
    posterior (finding Important #2 del reporte de la tarea) encontro que
    incluso esa version se salta con un import a nivel de MODULO dentro de
    `examples/run_world_sim.py` mismo (p.ej. `from src.simworld.lens import
    export_history as _leak` al principio del archivo): ese `_leak` queda
    ligado al objeto funcion ORIGINAL en el momento del import del modulo,
    antes de que el test corra ningun `patch.object`, y nunca vuelve a
    resolverse contra el namespace de `lens` en tiempo de llamada -- ninguna
    cantidad de parches por nombre a `export_history`, sin importar en que
    modulo, puede cubrir ese caso. Confirmado con el mutante real: paso las
    18 pruebas existentes (incluida la version de 2 parches de este test)
    sin detectarse, escribiendo igual un CSV sucio real a disco.

    Por eso ahora se parchea `lens.history_frame` -- la primera llamada que
    `export_history` hace POR DENTRO de su propio cuerpo. Sin importar como
    `export_history` haya llegado a estar ligada en otro lado (import
    directo, alias, wrapper), su codigo siempre resuelve `history_frame`
    contra el namespace propio de `lens.py` en tiempo de ejecucion -- eso es
    simplemente como Python resuelve nombres globales dentro de una funcion:
    contra el modulo donde fue DEFINIDA, no el modulo desde el que se llamo
    o importo. Parchear ahi intercepta la fuga sin importar el binding."""
    from src.simworld import lens as lens_mod

    original = lens_mod.history_frame
    call_count = {"n": 0}

    def counting(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    with patch.object(lens_mod, "history_frame", counting):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            report = run_grid(
                seed=7, n_skus=2, n_suppliers=2, weeks=12, levels=(0,),
                replan_every=4, out_dir=tmp, include_kern=True,
            )
    # weeks=12, replan_every=4 -> replans en semana 0 (sin historia, no
    # llama a Kern), semana 4 (Kern opera como el piso, sin lente) y semana 8
    # (si llama) = exactamente 1 llamada real.
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


def test_capture_is_null_with_a_note_when_kern_never_placed_a_real_order(kern_grid_report):
    """Task 11 review (finding Important #4): `capture()` compara costos
    entre corredores que efectivamente ordenan algo. En este fixture,
    forced-L3 escala en las DOS replanificaciones reales que llega a correr
    (ver `replan_status_counts` mas abajo) -- ningun estado no-"ok" de
    `kern_policy` devuelve nunca una politica con cantidades reales, asi
    que esta corrida jamas emite una orden. Publicar un `capture` numerico
    aca se leeria como un resultado real (p.ej. "recupero 60% del hueco
    piso-techo") cuando en realidad mide un corredor que no actuo contra un
    piso y un techo que si ordenan -- exactamente el hallazgo real del
    revisor. Debe salir `capture: None` con una nota explicando por que.

    forced-L0, en el mismo fixture, SI llega a "ok" en sus dos replans
    reales -- su `capture` debe seguir siendo un numero normal, confirmando
    que este fix no rompio el caso "Kern si actuo"."""
    report, _, _ = kern_grid_report

    l3 = report["kern"]["forced-L3"]
    assert l3["replan_status_counts"] == {"escalated": 2, "piso_sin_historia_minima": 1}
    assert "ok" not in l3["replan_status_counts"]
    assert l3["capture"] is None
    assert "capture_note" in l3
    assert "no emitio ninguna orden" in l3["capture_note"]

    l0 = report["kern"]["forced-L0"]
    assert l0["replan_status_counts"] == {"ok": 2, "piso_sin_historia_minima": 1}
    assert l0["capture"] is not None
    assert isinstance(l0["capture"], float)
    assert "capture_note" not in l0


def test_capture_is_null_when_kern_reports_ok_on_every_replan_but_orders_nothing():
    """Second review wave, Finding 2 (Minor): the guard above
    (`test_capture_is_null_with_a_note_when_kern_never_placed_a_real_order`)
    only covers the case where Kern *blocks* (a non-"ok" status on every
    real replan). The reviewer found a second, distinct way to reach the
    exact same misleading outcome: a lane can report status="ok" on EVERY
    replan -- Kern "succeeded" in its own vocabulary -- while its policy's
    `reorder_point`/`order_up_to` are all zero for every SKU, so
    `apply_policy` never crosses the reorder trigger and not a single unit
    is ever ordered. The OLD guard (`status_counts.get("ok", 0) == 0`) does
    NOT fire here (status was "ok" on every replan), so it printed a fully
    misleading numeric `capture` (the reviewer measured 0.941 -- "captured
    94% of the piso-techo gap" -- from a lane that placed zero real
    orders). The fix checks the PHYSICAL outcome
    (`WeekOutcome.ordered`, summed across the whole run) instead of the
    self-reported status, so this shape is now caught too.

    A fully mocked `kern_policy` reproduces this for ~0.03s (no real
    orchestrator call): same world/scale as `kern_grid_report` (guarantees
    `span > 0`, i.e. this branch -- not the degenerate-span one -- is what
    actually fires), but every replan returns a real "ok" `Policy` whose
    reorder_point/order_up_to are 0.0 for every SKU."""
    import tempfile

    import examples.run_world_sim as cli_mod

    def zero_reorder_kern_policy(w, outs, *, level, seed, out_dir, forced, **kwargs):
        del outs, level, seed, out_dir, forced, kwargs
        return Policy(
            reorder_point={s.sku: 0.0 for s in w.skus},
            order_up_to={s.sku: 0.0 for s in w.skus},
            announced=False, note="fake: nunca cruza el punto de reorden",
            status="ok",
        )

    with patch.object(cli_mod, "kern_policy", zero_reorder_kern_policy):
        with tempfile.TemporaryDirectory() as tmp:
            report = cli_mod.run_grid(
                seed=4, n_skus=2, n_suppliers=2, weeks=16, levels=(0,),
                replan_every=4, out_dir=tmp, include_kern=True,
            )

    l0 = report["kern"]["forced-L0"]
    # Mismos dos replans reales que en `kern_grid_report` (semanas 8 y 12;
    # la semana 0 cae antes de WARMUP_WEEKS y la 4 antes de
    # KERN_MIN_HISTORY_WEEKS -- esa la opera el piso y nunca llama a
    # `kern_policy`), todos "ok" -- el status NUNCA bloquea en este
    # escenario, a proposito: es justo el caso que el guard viejo no cubria.
    # Las ordenes del piso en las semanas 4-7 NO cuentan como de Kern.
    assert l0["replan_status_counts"] == {"ok": 2, "piso_sin_historia_minima": 1}
    assert l0["capture"] is None
    assert "capture_note" in l0
    assert "no emitio ninguna orden" in l0["capture_note"]


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
    assert 0 <= largest["week"] < 16
    assert isinstance(largest["value"], float)


def test_degradation_pins_exact_drop_announced_silent_for_a_synthetic_clean_dirty_pair():
    """Task 11 review (finding Important #3): la sospecha era que `run_grid`
    pudiera llamar a `degradation(clean, dirty, flags, level=...)` con
    `clean_weekly` pasado dos veces (``drop`` siempre 0) o con
    clean/dirty INTERCAMBIADOS (signo invertido) y ninguna de las 18 pruebas
    existentes lo notaria, porque todas comparan contra corridas reales
    donde nunca se conoce a mano el numero "correcto" de antemano.

    Se reemplaza `weekly_cost` por un par sintetico de arreglos clean/dirty
    (no viene de una simulacion real) y se reemplaza el `flags` de la
    replanificacion del nivel sucio por un patron de aviso conocido -- asi
    el valor esperado de ``drop``/``announced``/``silent`` se puede calcular
    a mano, sin depender de que el motor real produzca tal o cual numero.

    excess = dirty - clean = [0, 50, -20, 20]; flags = [F, T, F, T] ->
    drop = 50, announced = excess[1]+excess[3] = 70, silent = 50-70 = -20.
    Un mutante que pase `clean_weekly` dos veces dejaria drop/announced/
    silent en 0/0/0; uno que intercambie clean/dirty dejaria -50/-70/20
    (signo invertido) -- ambos claramente distintos de 50/70/-20."""
    import tempfile

    import examples.run_world_sim as cli_mod

    clean_synth = np.array([100.0, 100.0, 100.0, 100.0])
    dirty_synth = np.array([100.0, 150.0, 80.0, 120.0])
    flags_synth = [False, True, False, True]

    weekly_cost_calls = {"n": 0}

    def fake_weekly_cost(world, outs):
        weekly_cost_calls["n"] += 1
        # 1ra llamada de la seccion Kern = nivel 0 (limpio); 2da = nivel 1
        # (sucio). La firma real se preserva pero el contenido es sintetico
        # a proposito -- no derivado de `outs`.
        del world, outs
        return clean_synth if weekly_cost_calls["n"] == 1 else dirty_synth

    original_simulate = cli_mod.simulate
    simulate_calls = {"n": 0}

    def fake_simulate(world, plan, *, replan_every=4):
        simulate_calls["n"] += 1
        outs, real_flags = original_simulate(world, plan, replan_every=replan_every)
        # Llamada #1 = piso, #2 = Kern nivel 0, #3 = Kern nivel 1: solo la
        # #3 (el nivel sucio, el unico que alimenta `degradation`) recibe el
        # patron de aviso sintetico y conocido.
        if simulate_calls["n"] == 3:
            return outs, flags_synth
        return outs, real_flags

    with patch.object(cli_mod, "weekly_cost", fake_weekly_cost), \
         patch.object(cli_mod, "simulate", fake_simulate):
        with tempfile.TemporaryDirectory() as tmp:
            report = cli_mod.run_grid(
                seed=5, n_skus=2, n_suppliers=1, weeks=4, levels=(0, 1),
                replan_every=4, out_dir=tmp, include_kern=True,
            )

    deg = report["kern"]["forced-L1"]["degradation"]
    expected = real_degradation(clean_synth, dirty_synth, flags_synth, level=1)
    assert deg["drop"] == pytest.approx(expected.drop)
    assert deg["announced"] == pytest.approx(expected.announced)
    assert deg["silent"] == pytest.approx(expected.silent)
    # Numeros concretos calculados a mano (ver el docstring), no solo
    # "coincide con lo que devuelve `degradation()`" -- defensa en
    # profundidad si algun dia `degradation()` misma se rompe.
    assert deg["drop"] == pytest.approx(50.0)
    assert deg["announced"] == pytest.approx(70.0)
    assert deg["silent"] == pytest.approx(-20.0)


def test_degradation_flags_reflect_this_levels_own_weekly_announcements_not_a_constant(
    kern_grid_report,
):
    """Discrimina contra un `run_grid` que, en vez de reenviar a
    `degradation()` los flags reales que `simulate()` devolvio para ESTE
    nivel/carril (una senal por semana, en principio distinta semana a
    semana), pasara una lista constante -- todo False (aviso hardcodeado a
    False) o todo True (aviso hardcodeado a True) -- sin importar lo que
    Kern haya hecho de verdad.

    En este fixture (2 SKUs, 16 semanas, replan_every=4), forced-L3
    replanifica en las semanas 0, 4, 8 y 12: la de la semana 0 cae antes de
    WARMUP_WEEKS de historia y SIEMPRE es sin aviso (Policy vacia,
    independiente de cualquier mutante en la rama de Kern real); la de la
    semana 4 la opera el piso (PRE_KERN_STATUS, sin aviso); las de las
    semanas 8 y 12 SI corren a Kern de verdad y ambas escalan (ver
    `replan_status_counts`), asi que ambas llevan aviso -- exactamente 2
    semanas de aviso sobre un horizonte de 16. Ese 2 es un valor
    intermedio: ni 0 (mutante 'siempre False') ni 16 (mutante 'siempre
    True', que ademas convertiria en avisadas las semanas SIN
    replanificacion, algo que ningun corredor real puede hacer)."""
    report, _, _ = kern_grid_report
    deg = report["kern"]["forced-L3"]["degradation"]
    assert deg["weeks_announced"] == 2
    assert 0 < deg["weeks_announced"] < 16


def test_weeks_announced_reflects_a_genuine_alternating_pattern_not_a_per_call_constant():
    """Second review wave, Finding 1 (Important): the test above
    (`test_degradation_flags_reflect_...`) already covers a real-Kern
    fixture, but every real replan in THAT fixture happens to have
    `announced=True` -- so a mutant that hardcodes `kern_plan`'s `return
    pol, pol.announced` to `return pol, True` unconditionally is invisible
    against it (nothing observable changes: the correct value was already
    True everywhere real replans occur). The same mutant hardcoded to
    `return pol, False` IS caught by that fixture (2 != 0), but that is
    only half the mutant space.

    A fully mocked `kern_policy` sidesteps the real orchestrator entirely
    (no LLM, no lens, no QA -- just a `Policy` object), so this test is
    nearly free (~0.03s) while forcing a genuine MIX of True/False
    `announced` values across real replans: the fake alternates by replan
    ordinal, parsed from `out_dir`'s `w{week:03d}` component (independent
    of `level`/`lane`, so it does not depend on cross-level call
    ordering). With weeks=24/replan_every=4, real replans (>=
    KERN_MIN_HISTORY_WEEKS of history; week 4 is the floor's) land on weeks
    8/12/16/20 -> ordinals 2/3/4/5 -> the fake announces on odd ordinals
    (3, 5) and stays quiet on even ordinals (2, 4): a genuine 2-of-4 mix,
    an intermediate value neither an always-True nor an always-False
    mutant can reproduce (see the task report for the replay against
    both)."""
    import tempfile

    import examples.run_world_sim as cli_mod

    replan_every = 4

    def fake_kern_policy(w, outs, *, level, seed, out_dir, forced, **kwargs):
        del outs, level, seed, forced, kwargs
        week = int(Path(out_dir).name.lstrip("w"))
        ordinal = week // replan_every
        announced = ordinal % 2 == 1  # alterna: impar -> True, par -> False
        return Policy(
            reorder_point={s.sku: 5.0 for s in w.skus},
            order_up_to={s.sku: 50.0 for s in w.skus},
            announced=announced, note="fake kern lane", status="ok",
        )

    with patch.object(cli_mod, "kern_policy", fake_kern_policy):
        with tempfile.TemporaryDirectory() as tmp:
            report = cli_mod.run_grid(
                seed=5, n_skus=2, n_suppliers=1, weeks=24, levels=(0, 1),
                replan_every=replan_every, out_dir=tmp, include_kern=True,
            )

    deg = report["kern"]["forced-L1"]["degradation"]
    # Replans reales en las semanas 8, 12, 16, 20 (ordinales 2-5); anunciado
    # en los ordinales impares (3 y 5) = 2 de 4 replans reales -- ni 0
    # (mutante 'siempre False') ni 4 (mutante 'siempre True').
    assert deg["weeks_announced"] == 2
    assert 0 < deg["weeks_announced"] < 4


def test_report_markdown_shows_the_real_capture_value_and_the_bias_disclosure_sentence(
    kern_grid_report,
):
    """Discrimina contra un `_markdown(report)` reemplazado por un string
    de relleno estatico (mutante real probado por la revision de Task 11,
    finding Important #3c/e): si el reporte no vuelca ni la captura real
    calculada para cada carril-nivel ni la frase de sesgo declarado contra
    Kern, ninguna de las dos aserciones de abajo puede pasar contra un texto
    generico."""
    report, out_dir, _ = kern_grid_report
    md = (out_dir / "report.md").read_text(encoding="utf-8")

    numeric_captures = [
        entry["capture"] for entry in report["kern"].values()
        if entry.get("capture") is not None
    ]
    assert numeric_captures  # el fixture realmente produce captura numerica
    for cap in numeric_captures:
        assert f"{cap:.3f}" in md

    # La frase de sesgo declarado, deliberado, en contra de Kern (ver
    # `_markdown`): "silencio" cuenta como sin-aviso cualquier semana
    # gobernada por un aviso de una replanificacion anterior, un sesgo que
    # perjudica a Kern, nunca lo favorece. Debe seguir presente palabra por
    # palabra.
    assert "en contra de Kern" in md


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
                    # weeks=16, no 8: con 8 semanas y replan cada 4, el primer
                    # replan de Kern ve menos de MIN_PERIODS_FOR_POLICY periodos
                    # por SKU y la corrida se rechaza entera, con lo cual nunca se
                    # llega a `capture()` y esta rama -- que es lo unico que el
                    # test mide -- deja de ejercitarse. La escala aqui es
                    # incidental (ver el docstring): lo que importa es que Kern
                    # corra normal para que la excepcion forzada tenga por donde
                    # salir.
                    seed=9, n_skus=2, n_suppliers=1, weeks=16, levels=(0,),
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
