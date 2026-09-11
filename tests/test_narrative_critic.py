"""Brecha 8: nada revisaba la prosa que el LLM le escribe al cliente.

`nosilo_core.llm.narrative_rewrite` le pide al modelo "Keep every number" y
despues no verifica NADA: sus unicas guardas son `provider.available()`, un
`try/except` ancho y `return text.strip() or base_summary`, un chequeo de vacio.
El gate de QA (`jobs/qa.py`) revisa numeros pero corre ANTES, y ninguna de las 57
funciones `verify` del repo toma una cadena de prosa. Entre el gate y el
entregable, el resumen se reemplaza entero sin post-chequeo.

Prueba de que el agujero es real y no hipotetico: hasta este archivo,
tests/test_scm_agent.py::test_orchestrator_narrative_upgrade_with_provider pasaba
en verde inyectando un provider que devuelve "Upgraded narrative." -- una cadena
sin una sola cifra -- sobre un base_summary que dice "CHAIN 2.0/4". El resumen sin
cifras es lo que iba al cliente, con status "ok".

El arreglo NO puede ir dentro de `narrative_rewrite`: esa funcion vive en
`nosilo_core`, dependencia externa pineada por tag, fuera de este repo. Va
envolviendo los dos sitios de llamada de Kern -- `orchestrator.py::_narrative` y
`packages.py::_run_step` -- que es lo que la ficha de la brecha ya decia.

Severidad: FILTRO, NO VETO. Igual que el gate de citas (ver el comentario de
`packages.py` sobre `_step_citations`) y que `src/seo/pdp_writer.py::
enhance_with_llm`, que post-chequea la prosa de su LLM y revierte al template.
Revertir al `base_summary` deterministico es ademas exactamente lo que
`narrative_rewrite` ya hace cuando no hay provider o la llamada falla: su
contrato dice "never load-bearing". El critico no agrega un modo de falla nuevo,
extiende el que ya existe a un caso que no estaba cubierto.
"""
from __future__ import annotations

import pytest

from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from scm_agent.narrative_critic import figures, review_narrative
from scm_agent.packages import PackageSpec, PackageStep, run_package

BASE = "CHAIN 2.0/4 - archetype: Operador invisible - priority lever: Influyente (I)."


# --- el extractor de cifras --------------------------------------------------


def test_extrae_las_cifras_del_resumen_deterministico():
    assert figures(BASE) == ("20", "4")


@pytest.mark.parametrize("texto,esperado", [
    ("1,234.5", ("12345",)),      # ingles: coma agrupa, punto decimal
    ("1.234,5", ("12345",)),      # espanol: al reves -- MISMA cifra
    ("15%", ("15",)),
    ("3 2 3 1 1", ("3", "2", "3", "1", "1")),  # una lista, no un numero de 5 digitos
    ("sin cifras", ()),
])
def test_normaliza_a_valor_y_no_a_formato(texto, esperado):
    """EL CASO QUE HACE VIABLE AL CRITICO.

    El runner de paquetes traduce al idioma del paquete (`PackageSpec.lang`), y
    el espanol invierte los separadores de miles y decimales. Un critico que
    comparara CADENAS revertiria toda traduccion legitima. Se comparan digitos.

    El espacio ASCII NO agrupa a proposito: "3 2 3 1 1" son cinco cifras, no
    32.311 -- justamente la forma en que esta suite pasa los scores de CHAIN.
    """
    assert figures(texto) == esperado


# --- el critico --------------------------------------------------------------


def test_una_cifra_borrada_revierte_al_base_summary():
    """EL TEST QUE IMPORTA: es el caso exacto que pasaba en verde hasta este
    commit, con el resumen sin cifras viajando al cliente como status ok."""
    r = review_narrative(BASE, "Upgraded narrative.")
    assert r.reverted is True
    assert r.summary == BASE
    assert set(r.missing) == {"20", "4"}


def test_una_cifra_mutada_revierte():
    r = review_narrative(BASE, "CHAIN 3.0/4 - arquetipo: Operador invisible.")
    assert r.reverted is True
    assert r.missing == ("20",)


def test_una_reescritura_que_conserva_las_cifras_se_acepta():
    bueno = "El diagnostico CHAIN da 2.0 de 4: arquetipo Operador invisible."
    r = review_narrative(BASE, bueno)
    assert r.reverted is False
    assert r.summary == bueno
    assert r.missing == ()


def test_la_traduccion_al_espanol_no_se_revierte():
    """Contraprueba del caso de arriba: mismas cifras, otro formato."""
    r = review_narrative("Ahorro de 1,234.5 USD en 15% de los SKU.",
                         "Ahorro de 1.234,5 USD en el 15% de los SKU.")
    assert r.reverted is False


def test_una_cifra_de_menos_en_una_lista_repetida_se_detecta():
    """Multiset, no conjunto: perder UNO de los dos 3 tambien es perder una cifra."""
    r = review_narrative("scores 3 2 3 1 1", "scores 3 2 1 1")
    assert r.reverted is True
    assert r.missing == ("3",)


def test_un_base_sin_cifras_no_bloquea_nada():
    r = review_narrative("Sin hallazgos accionables.", "No actionable findings.")
    assert r.reverted is False
    assert r.summary == "No actionable findings."


def test_cifras_de_mas_no_revierten_y_se_reportan():
    """Direccion deliberadamente NO bloqueante, y medida.

    El prompt de `narrative_rewrite` incluye "Ground it in these sources where
    relevant" con las citas L3, y esas citas traen cifras legitimas ("Ch.9",
    "PDF p.163", "Khan et al. (2022)"). Bloquear por cifra nueva revertiria
    justamente el comportamiento que el prompt pide. Se reporta en `added` para
    que quede inspeccionable, sin vetear.
    """
    r = review_narrative(BASE, "CHAIN 2.0/4 segun Christopher Ch.9.")
    assert r.reverted is False
    assert r.added == ("9",)


def test_el_critico_nunca_levanta_y_nunca_devuelve_vacio():
    """Un critico que rompe la corrida es peor que la prosa que vigila."""
    for reescrito in ("", "   ", BASE):
        r = review_narrative(BASE, reescrito)
        assert r.summary.strip()


# --- el segundo sitio de llamada: el runner de paquetes ----------------------


class _SinCitas:
    """Stand-in libre de citas: estos tests no cargan el grafo de libros."""

    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


class _Provider:
    def __init__(self, texto):
        self._texto = texto

    def available(self):
        return True

    def complete(self, prompt):
        return self._texto

    def extract(self, prompt, schema):
        return {}


BASE_PASO = "EOQ 1234 unidades, punto de reorden 56."


def _correr_un_paso(texto, tmp_path):
    tool = Tool(
        key="fake_num", title="Fake", description="test double", intent_keywords=(),
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report={}, summary=BASE_PASO),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    )
    reg = ToolRegistry()
    reg.register(tool)
    spec = PackageSpec(
        key="p", title="P", price="-", cadence="-", audience="-",
        inputs=(), steps=(PackageStep("fake_num", None),),
    )
    res = run_package(spec, None, registry=reg, out_dir=tmp_path, params={},
                      knowledge=_SinCitas(), clients_root=None, provider=_Provider(texto))
    return res.steps[0]


def test_el_runner_de_paquetes_tambien_revierte(tmp_path):
    """EL OTRO SITIO, y no es redundante.

    Un critico cableado solo en el orquestador dejaria sin revisar la ruta que un
    cliente que paga efectivamente recibe. Es la leccion que ya dejo escrita
    filter_citations: cablearlo en una sola de las dos superficies deja la otra
    sin gate y se parece a estar cubierto.
    """
    paso = _correr_un_paso("Resumen mejorado, sin una sola cifra.", tmp_path)
    assert paso.status == "ok"          # filtro, no veto: el paso igual entrega
    assert paso.summary == BASE_PASO    # pero entrega la prosa deterministica


def test_el_runner_de_paquetes_acepta_la_reescritura_que_conserva(tmp_path):
    bueno = "EOQ de 1234 unidades con reorden en 56."
    paso = _correr_un_paso(bueno, tmp_path)
    assert paso.status == "ok"
    assert paso.summary == bueno
