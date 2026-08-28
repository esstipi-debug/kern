"""Brecha 5: `run_package` corria 35 pasos estrictamente en serie.

No habia una sola primitiva de fan-out en `scm_agent/`, `jobs/` ni `src/`: el
unico ThreadPoolExecutor del repo esta en `webapp/app.py` con `max_workers=1`,
que es un serializador, no un fan-out.

Lo que gobierna la paralelizabilidad NO es diferir escrituras -- la fase 1 ya
"compute everything; write nothing to out_root" -- sino el grafo de dependencias
entre pasos, y ese grafo NO ESTABA DECLARADO: `derive` y `derive_params` son
callables y su dependencia real vive dentro de un closure
(`_cycle_count_from_abc` lee reports["abc_xyz"]). Un ThreadPool ingenuo sobre
`spec.steps` rompe `cycle_count` con un KeyError. Por eso `PackageStep` gana un
`depends_on` declarado y estos tests exigen que se declare.

La semantica elegida es REPRODUCIR EL SERIAL EXACTO. Bajo fan-out no se puede
des-ejecutar un paso que ya arranco, pero si truncar: el serial corta en el
primer paso requerido que falla, asi que aplicar esa misma regla sobre lo que
alcanzo a correr devuelve un PackageResult identico. Activar W>1 no cambia nada
observable; solo el makespan.
"""
from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry

from examples.run_package import DEMO_PARAMS, build_demo_intake
from scm_agent.package_specs import PACKAGES, SCALE
from scm_agent.packages import PackageSpec, PackageStep, run_package


class _SinCitas:
    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


@pytest.fixture(scope="module")
def demo_intake(tmp_path_factory) -> Path:
    return build_demo_intake(tmp_path_factory.mktemp("intake_fanout"))


def _correr(spec, intake, out, **kwargs):
    kwargs.setdefault("params", dict(DEMO_PARAMS))
    kwargs.setdefault("knowledge", _SinCitas())
    kwargs.setdefault("clients_root", None)
    return run_package(spec, intake, out_dir=out, **kwargs)


# --- 1. el grafo tiene que estar declarado -----------------------------------


def test_todo_paso_con_derive_declara_de_donde_deriva():
    """EL PRERREQUISITO. Sin esto cualquier scheduler adivina.

    Un paso que lee `reports` dentro de un closure y no lo declara corre bien en
    serie por accidente -- porque todos los pasos anteriores ya corrieron -- y
    revienta en cuanto el orden deja de estar garantizado.
    """
    sin_declarar = [
        (nombre, s.tool_key)
        for nombre, spec in PACKAGES.items()
        for s in spec.steps
        if (s.derive is not None or s.derive_params is not None) and not s.depends_on
    ]
    assert not sin_declarar, (
        f"estos pasos derivan de un report y no declaran depends_on: {sin_declarar}"
    )


def test_toda_dependencia_declarada_existe_antes_en_el_mismo_spec():
    """Una dependencia hacia un paso que no esta, o que esta despues, cuelga al
    scheduler o cambia el orden respecto del serial."""
    for nombre, spec in PACKAGES.items():
        claves = [s.tool_key for s in spec.steps]
        for i, s in enumerate(spec.steps):
            for dep in s.depends_on:
                assert dep in claves, f"{nombre}/{s.tool_key} depende de {dep!r}, que no esta en el spec"
                assert claves.index(dep) < i, (
                    f"{nombre}/{s.tool_key} depende de {dep!r}, que va DESPUES en el spec"
                )


def test_las_dos_aristas_reales_estan_declaradas():
    """Contraprueba de que el test de arriba no pasa por vacuidad."""
    pasos = {s.tool_key: s for s in SCALE.steps}
    assert pasos["cycle_count"].depends_on == ("abc_xyz",)
    assert pasos["excel_replenishment"].depends_on == ("inventory_optimization",)


# --- 2. equivalencia con el serial, sobre SCALE ------------------------------


def _comparable(res):
    return (
        res.status,
        tuple((s.tool_key, s.status, tuple(s.qa_issues)) for s in res.steps),
        tuple(sorted(res.deliverables)),
    )


def test_scale_en_paralelo_da_el_mismo_resultado_que_en_serie(demo_intake, tmp_path):
    """EL "CERRADA CUANDO" DE LA FICHA, sobre el paquete de 35 pasos."""
    serie = _correr(SCALE, demo_intake, tmp_path / "serie")
    paralelo = _correr(SCALE, demo_intake, tmp_path / "par", workers=4)
    assert _comparable(paralelo) == _comparable(serie)


def test_abc_xyz_sigue_corriendo_antes_que_cycle_count(demo_intake, tmp_path):
    """La arista que un fan-out ingenuo rompe."""
    res = _correr(SCALE, demo_intake, tmp_path / "orden", workers=4)
    claves = [s.tool_key for s in res.steps]
    if "cycle_count" in claves and "abc_xyz" in claves:
        assert claves.index("abc_xyz") < claves.index("cycle_count")
    ciclo = next(s for s in res.steps if s.tool_key == "cycle_count")
    assert ciclo.status != "error", f"cycle_count fallo bajo fan-out: {ciclo.messages}"


# --- 3. el makespan, con pasos sinteticos y lentos ---------------------------


def _tool_lento(key: str, segundos: float) -> Tool:
    def _run(payload, params):
        time.sleep(segundos)
        return Produced(report={"k": key}, summary=f"{key} 1 unidad")

    return Tool(
        key=key, title=key, description="test double", intent_keywords=(),
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=_run, qa=lambda report: [], deliver=lambda report, out_dir, client: {},
    )


def _spec_sintetico(claves, **kw):
    return PackageSpec(
        key="p", title="P", price="-", cadence="-", audience="-", inputs=(),
        steps=tuple(PackageStep(k, None, **kw.get(k, {})) for k in claves),
    )


def test_el_makespan_baja_de_la_suma_hacia_el_maximo(tmp_path):
    """Sin aristas de precedencia, cuatro pasos de 0,4 s tardan ~1,6 s en serie
    y ~0,4 s con W=4. Se exige menos de la mitad de la suma: suficiente para
    distinguir paralelo de serial sin volverse flaky por el ruido del scheduler.
    """
    claves = ["a", "b", "c", "d"]
    reg = ToolRegistry()
    for k in claves:
        reg.register(_tool_lento(k, 0.4))
    spec = _spec_sintetico(claves)

    t0 = time.perf_counter()
    serie = run_package(spec, None, registry=reg, out_dir=tmp_path / "s", params={},
                        knowledge=_SinCitas(), clients_root=None)
    t_serie = time.perf_counter() - t0

    t0 = time.perf_counter()
    par = run_package(spec, None, registry=reg, out_dir=tmp_path / "p", params={},
                      knowledge=_SinCitas(), clients_root=None, workers=4)
    t_par = time.perf_counter() - t0

    assert [s.status for s in par.steps] == [s.status for s in serie.steps]
    assert t_serie > 1.2, f"el serial tardo {t_serie:.2f}s; el test no esta midiendo nada"
    assert t_par < t_serie / 2, f"serie {t_serie:.2f}s vs paralelo {t_par:.2f}s"


def test_una_arista_declarada_serializa_ese_par(tmp_path):
    """Contraprueba del test de arriba: con b dependiendo de a, esos dos no
    pueden solaparse y el makespan no baja de la suma de los dos."""
    reg = ToolRegistry()
    for k in ("a", "b"):
        reg.register(_tool_lento(k, 0.4))
    spec = _spec_sintetico(["a", "b"], b={"depends_on": ("a",)})

    t0 = time.perf_counter()
    res = run_package(spec, None, registry=reg, out_dir=tmp_path / "dep", params={},
                      knowledge=_SinCitas(), clients_root=None, workers=4)
    transcurrido = time.perf_counter() - t0

    assert [s.tool_key for s in res.steps] == ["a", "b"]
    assert transcurrido > 0.8, f"a y b se solaparon pese a la arista: {transcurrido:.2f}s"


# --- 4. la falla de un paso requerido -----------------------------------------


def _tool_que_falla(key: str) -> Tool:
    return Tool(
        key=key, title=key, description="test double", intent_keywords=(),
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=lambda payload, params: (_ for _ in ()).throw(RuntimeError("boom")),
        qa=lambda report: [], deliver=lambda report, out_dir, client: {},
    )


def test_un_paso_requerido_que_falla_da_el_mismo_resultado_en_los_dos_caminos(tmp_path):
    """EL TEST QUE IMPORTA de la semantica elegida.

    El serial corta en el paso que fallo y devuelve los outcomes hasta ese. Bajo
    fan-out los posteriores pueden haber corrido igual -- no se puede
    des-ejecutar un hilo -- pero el PackageResult sale identico porque se trunca
    con la misma regla.
    """
    reg = ToolRegistry()
    reg.register(_tool_lento("a", 0.05))
    reg.register(_tool_que_falla("b"))
    reg.register(_tool_lento("c", 0.05))
    spec = _spec_sintetico(["a", "b", "c"])

    serie = run_package(spec, None, registry=reg, out_dir=tmp_path / "s", params={},
                        knowledge=_SinCitas(), clients_root=None)
    par = run_package(spec, None, registry=reg, out_dir=tmp_path / "p", params={},
                      knowledge=_SinCitas(), clients_root=None, workers=4)

    assert _comparable(par) == _comparable(serie)
    assert [s.tool_key for s in serie.steps] == ["a", "b"]  # c nunca aparece
    assert serie.deliverables == {} and par.deliverables == {}


def test_una_dependencia_que_falla_no_frena_al_dependiente(tmp_path):
    """Un paso es lanzable cuando sus dependencias TERMINARON, no cuando
    quedaron ok. Si la dependencia no era requerida, el serial sigue igual y el
    dependiente corre sin su report -- reproducir eso es parte de la
    equivalencia, no un bug."""
    reg = ToolRegistry()
    reg.register(_tool_que_falla("a"))
    reg.register(_tool_lento("b", 0.05))
    spec = _spec_sintetico(["a", "b"], a={"required": False}, b={"depends_on": ("a",)})

    serie = run_package(spec, None, registry=reg, out_dir=tmp_path / "s", params={},
                        knowledge=_SinCitas(), clients_root=None)
    par = run_package(spec, None, registry=reg, out_dir=tmp_path / "p", params={},
                      knowledge=_SinCitas(), clients_root=None, workers=4)

    assert _comparable(par) == _comparable(serie)
    assert [s.tool_key for s in par.steps] == ["a", "b"]


def test_workers_uno_es_el_camino_serial(tmp_path):
    """W=1 no debe siquiera entrar al scheduler: es el default y la ruta de
    produccion hasta que alguien decida lo contrario."""
    reg = ToolRegistry()
    for k in ("a", "b"):
        reg.register(_tool_lento(k, 0.02))
    spec = _spec_sintetico(["a", "b"])
    uno = run_package(spec, None, registry=reg, out_dir=tmp_path / "1", params={},
                      knowledge=_SinCitas(), clients_root=None, workers=1)
    cero = run_package(spec, None, registry=reg, out_dir=tmp_path / "0", params={},
                       knowledge=_SinCitas(), clients_root=None)
    assert _comparable(uno) == _comparable(cero)


def test_replace_sobre_un_spec_conserva_las_dependencias():
    """`dataclasses.replace(spec, lang=...)` es como el runner elige idioma por
    cliente; si perdiera depends_on, el fan-out se romperia solo en ingles."""
    en = replace(SCALE, lang="en")
    pasos = {s.tool_key: s for s in en.steps}
    assert pasos["cycle_count"].depends_on == ("abc_xyz",)
