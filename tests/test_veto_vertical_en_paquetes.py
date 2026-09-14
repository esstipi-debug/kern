"""El veto del rubro tambien manda en el camino de paquetes (kern#253).

`scm_agent/orchestrator.py` ya lo hacia cumplir para una capacidad suelta. El
camino comercial -- `scm_agent/packages.py` -- resolvia el vertical para
`merge_params` y los aliases, y **nunca preguntaba si el paso estaba prohibido**.

El resultado concreto: un cliente de alimentos corriendo un paquete ejecutaba
igual `inventory_optimization` (paso REQUERIDO en `package_specs.py`), que es
exactamente la capacidad que su pack existe para impedir. Y como los parametros
del rubro SI llegaban -- `holding_rate: 0.40` en vez del generico -- el numero
salia menos equivocado y con la misma cara de correcto, que es peor.

Los tests corren contra los packs REALES de `config/verticals/`, no contra
YAMLs sinteticos: lo que hay que probar es que el pack que esta en el repo
manda de verdad.
"""

from __future__ import annotations

import pytest

from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from nosilo_core.types import STATUS_NEEDS_CLARIFICATION
from scm_agent.packages import PackageSpec, PackageStep, run_package
from src import client_profile, vertical_profile

#: Las dos capacidades que `config/verticals/alimentos.yaml` prohibe.
VETADAS = ("inventory_optimization", "multi_echelon")


class _SinConocimiento:
    """Doble sin citas, mismo contrato que `_NoKnowledge` de test_packages.py:
    ningun test de este archivo carga el grafo de libros."""

    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


def _tool(key: str) -> Tool:
    """Tool minima que registra que efectivamente corrio."""
    return Tool(
        key=key, title=key, description="doble de prueba", intent_keywords=(),
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report={"corrio": True}, summary="ok"),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    )


def _registro(*keys: str) -> ToolRegistry:
    reg = ToolRegistry()
    for k in keys:
        reg.register(_tool(k))
    return reg


def _cliente(tmp_path, rubro: str) -> str:
    """Perfil de cliente que DECLARA un rubro, como lo haria el analista."""
    client_profile.upsert_profile("acme", "Acme", root=tmp_path, vertical=rubro)
    return "Acme"


def _spec(*steps: PackageStep) -> PackageSpec:
    return PackageSpec(key="sintetico", title="Paquete Sintetico", price="n/a",
                       cadence="n/a", audience="n/a", inputs=(), steps=steps)


def _correr(tmp_path, rubro, spec, registry, **kw):
    return run_package(spec, None, registry=registry, client=_cliente(tmp_path, rubro),
                       clients_root=tmp_path, params={}, knowledge=_SinConocimiento(),
                       out_dir=tmp_path / "out", **kw)


# -- el pack real, primero ----------------------------------------------------


@pytest.mark.parametrize("capacidad", VETADAS)
def test_el_pack_de_alimentos_del_repo_prohibe_las_dos_capacidades(capacidad):
    """Si alguien vacia `forbidden_methods` del YAML, esto se pone rojo antes que
    cualquier test de comportamiento -- y el mensaje dice cual falta."""
    veto = vertical_profile.load_vertical_profile("alimentos").forbids(capacidad)
    assert veto is not None, f"alimentos dejo de prohibir {capacidad}"
    assert veto.reason.strip(), "una negativa sin razon no es una negativa"


# -- paso requerido: el paquete no se entrega a medias ------------------------


@pytest.mark.parametrize("capacidad", VETADAS)
def test_paso_requerido_vetado_no_entrega_el_paquete(tmp_path, capacidad):
    """Un paquete cuyo paso REQUERIDO no aplica al rubro es el paquete
    equivocado para ese cliente, no un paquete al que le falta un pedazo."""
    r = _correr(tmp_path, "alimentos",
                _spec(PackageStep(capacidad, None)), _registro(capacidad))

    assert r.status == STATUS_NEEDS_CLARIFICATION, r.summary
    assert r.deliverables == {}, "no se escribe nada cuando el analisis central no aplica"


@pytest.mark.parametrize("capacidad", VETADAS)
def test_la_negativa_lleva_la_razon_y_el_que_correr_en_su_lugar(tmp_path, capacidad):
    """La razon sale del YAML, no de una cadena escrita en Python: si el pack
    cambia el texto, el mensaje cambia solo."""
    veto = vertical_profile.load_vertical_profile("alimentos").forbids(capacidad)
    r = _correr(tmp_path, "alimentos",
                _spec(PackageStep(capacidad, None)), _registro(capacidad))

    assert veto.reason.strip()[:40] in r.summary, r.summary
    assert veto.instead.strip()[:40] in r.summary, "falta el que correr en su lugar"


def test_el_paso_vetado_no_llega_a_correr(tmp_path):
    """La prueba que importa: no basta con que el resultado diga que no aplica.
    El tool no tiene que haber sido invocado."""
    corridas: list[str] = []

    def _espia(key: str) -> Tool:
        t = _tool(key)
        return Tool(
            key=t.key, title=t.title, description=t.description,
            intent_keywords=(), requires_data=False,
            prepare=t.prepare,
            run=lambda payload, params: (corridas.append(key),
                                         Produced(report={}, summary="ok"))[1],
            qa=t.qa, deliver=t.deliver,
        )

    reg = ToolRegistry()
    reg.register(_espia("inventory_optimization"))
    _correr(tmp_path, "alimentos",
            _spec(PackageStep("inventory_optimization", None)), reg)

    assert corridas == [], "el paso vetado se ejecuto igual"


# -- paso opcional: se salta con motivo, el resto sigue -----------------------


def test_paso_opcional_vetado_se_salta_con_motivo_y_el_paquete_sigue(tmp_path):
    r = _correr(
        tmp_path, "alimentos",
        _spec(PackageStep("multi_echelon", None, required=False),
              PackageStep("fefo", None)),
        _registro("multi_echelon", "fefo"),
    )

    vetado = next(s for s in r.steps if s.tool_key == "multi_echelon")
    assert vetado.status == "skipped"
    assert vetado.skip_reason.strip(), "un salto sin motivo se lee como 'no habia datos'"
    assert vetado.source == "(no aplica al rubro)"
    assert any("fefo" in m for m in vetado.messages), "falta el que correr en su lugar"

    otro = next(s for s in r.steps if s.tool_key == "fefo")
    assert otro.status == "ok", "vetar un paso opcional no puede frenar los demas"


# -- el porton no puede ser demasiado ancho -----------------------------------


@pytest.mark.parametrize("capacidad", VETADAS)
def test_otro_rubro_corre_las_mismas_capacidades_sin_problema(tmp_path, capacidad):
    """Sin este test un porton demasiado ancho apaga el producto entero y nadie
    se entera: todos los clientes verian 'no aplica a tu rubro'."""
    r = _correr(tmp_path, "repuestos-industriales",
                _spec(PackageStep(capacidad, None)), _registro(capacidad))

    assert r.status == "ok", r.summary
    assert r.steps[0].status == "ok"


def test_cliente_sin_rubro_declarado_corre_todo(tmp_path):
    """La capa esta simplemente ausente: asi es como esto queda neutral para todo
    perfil escrito antes de que el campo existiera."""
    client_profile.upsert_profile("acme", "Acme", root=tmp_path)
    r = run_package(_spec(PackageStep("inventory_optimization", None)), None,
                    registry=_registro("inventory_optimization"), client="Acme",
                    clients_root=tmp_path, params={}, knowledge=_SinConocimiento(),
                    out_dir=tmp_path / "out")
    assert r.status == "ok", r.summary


def test_una_capacidad_no_prohibida_corre_aunque_el_rubro_prohiba_otras(tmp_path):
    """El veto es por capacidad, no por cliente: alimentos no queda apagado."""
    r = _correr(tmp_path, "alimentos",
                _spec(PackageStep("fefo", None)), _registro("fefo"))
    assert r.status == "ok", r.summary


# -- el porton lee DATO, no tiene ramas por rubro -----------------------------


def test_no_hay_ninguna_rama_por_nombre_de_rubro_en_el_motor():
    """La regla de control del repo: UN VERTICAL NUEVO = 0 LINEAS EN EL MOTOR.

    El dia que alguien escriba `if vertical == "alimentos"` en src/ o
    scm_agent/, es personalizacion con otro nombre y este test lo dice.
    """
    import pathlib
    import re

    raiz = pathlib.Path(__file__).resolve().parents[1]
    patron = re.compile(r'vertical[\w.]*\s*==\s*[\'"]', re.IGNORECASE)
    culpables = [
        f"{ruta.relative_to(raiz)}:{n}"
        for carpeta in ("src", "scm_agent", "jobs")
        for ruta in (raiz / carpeta).rglob("*.py")
        for n, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if patron.search(linea) and "vertical_profile.py" not in str(ruta)
    ]
    assert culpables == [], f"ramas por nombre de rubro en el motor: {culpables}"
