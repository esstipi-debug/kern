"""El indice de lectura deja de ser codigo muerto: una corrida de paquete lo consulta.

QUE SE FIJA ACA, y por que cada cosa es de producto antes que de codigo.

1. **UNA sola consulta por corrida, no una por paso.** Medido: cada `read_passages`
   levanta y baja el servidor entero -- 16,8 s con el indice frio, ~1,6 GB de RSS.
   Un paquete Growth son 26 pasos; pagar eso 26 veces convierte un entregable de
   minutos en uno de horas. El cableado engancha en el UNICO Deliverable que la
   corrida tiene armado en la mano (el deck consolidado del paquete), asi que una
   consulta cubre el paquete entero.

2. **Inerte cuando el indice no esta**, que es SIEMPRE en produccion (la VM tiene
   512 MB y el indice pide 1,6 GB). Sin lecturas el entregable sale byte a byte como
   lo arma el builder puro, sin seccion nueva y sin nota de que falto nada.

3. **No toca las citas.** Un pasaje no tiene `node_id` y `filter_citations` lo
   rechazaria con razon: esto vive RIO ABAJO del gate. El test corre un paso REAL
   contra el grafo L3 committeado y compara las citas con y sin lecturas.

4. **No puede colgar la corrida.** Un servidor que no contesta nunca degrada a
   `available=False` en segundos, y el paquete se entrega igual.

Los dos tests de la seccion 5 son los unicos que hablan con el indice de verdad: van
marcados `integracion`, que `pyproject.toml` deselecciona por defecto
(`addopts = "-m 'not integracion'"`). El segundo consulta a proposito el libro con el
glyph-mapping del PDF destruido, que es lo que motivo el piso de legibilidad del
conector.
"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest

from examples.run_package import DEMO_PARAMS, build_demo_intake
from jobs import package_deliverable
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from scm_agent import packages
from scm_agent.knowledge import KnowledgeBase
from scm_agent.package_specs import DIAGNOSTICO
from scm_agent.packages import PackageSpec, PackageStep, run_package
from scm_agent.tools import build_default_registry
from src.connectors import lectura
from src.connectors.lectura import ENV_INTERPRETE, Reading, ReadingLookup

# La ruta del indice real, que vive en OTRO repo. Solo la usa el test de integracion.
INDICE_REAL = Path("C:/Users/Gamer/Music/scm/kern-lectura/.venv/Scripts/python.exe")

#: El libro del indice con el glyph-mapping del PDF destruido, que es lo que motivo el
#: piso de legibilidad del conector (`lectura.MAX_RELLENO_EXCERPT`).
_LIBRO_ROTO = "vandeput-data-science-supply-chain-forecasting"


class _NoKnowledge:
    """Sin citas: los tests que no miran citas no cargan el grafo de libros."""

    def ground_citations(self, keywords, brief, limit=5):
        return []

    def ground_citations_detailed(self, keywords, brief, limit=5):
        return []

    def warnings(self):
        return []


def _tool(key: str, *keywords: str) -> Tool:
    """Un Tool registrable minimo, con las intent_keywords que el cableado lee."""
    return Tool(
        key=key, title=key, description="test double", intent_keywords=keywords,
        requires_data=False,
        prepare=lambda request, provider: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report={"key": key}, summary=f"{key} ok"),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    )


def _spec(*tool_keys: str) -> PackageSpec:
    return PackageSpec(
        key="lecturas", title="Paquete de prueba", price="n/a", cadence="n/a",
        audience="n/a", inputs=(),
        steps=tuple(PackageStep(k, None) for k in tool_keys),
    )


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


_LECTURAS = (
    Reading(source="vandeput-inventory-optimization",
            page_label="PDF p.83-84",
            excerpt="Safety stock sizing depends on the risk period."),
    Reading(source="liu-supply-chain-analytics",
            page_label="PDF p.12",
            excerpt="Analytics without a decision attached is reporting."),
)


def _disponible(keywords, **kwargs) -> ReadingLookup:
    return ReadingLookup(available=True, readings=_LECTURAS, note="2 pasaje(s)")


def _no_disponible(keywords, **kwargs) -> ReadingLookup:
    return ReadingLookup(available=False, readings=(), note="no esta el indice")


@pytest.fixture(autouse=True)
def _sin_indice_por_defecto(monkeypatch):
    """Ningun test de este archivo depende de como esta la maquina que lo corre."""
    monkeypatch.delenv(ENV_INTERPRETE, raising=False)


# --- 1. inerte: sin lecturas, el entregable sale como lo arma el builder puro ---------


def test_sin_indice_el_deck_sale_byte_a_byte_como_el_builder_puro(tmp_path, monkeypatch):
    """El caso de PRODUCCION. No se compara contra un golden a mano: se compara contra
    ``package_deliverable.build``, que es el builder que este cableado NO toca. Si el
    cableado dejara cualquier rastro (una seccion vacia, una nota de "no habia indice"),
    los dos textos dejan de ser iguales."""
    monkeypatch.setattr(lectura, "read_passages", _no_disponible)
    spec = _spec("uno", "dos")
    reg = _registry(_tool("uno", "safety stock"), _tool("dos", "abc analysis"))

    res = run_package(spec, None, registry=reg, knowledge=_NoKnowledge(),
                      clients_root=None, out_dir=tmp_path, params={})

    assert res.status == "ok"
    escrito = Path(res.deliverables["paquete_report"]).read_text(encoding="utf-8")
    esperado = package_deliverable.build(spec, res.steps, client="Client").to_markdown()
    assert escrito == esperado


def test_el_deck_no_se_reemplaza_cuando_no_hay_lecturas(monkeypatch):
    """Inerte de verdad: el mismo OBJETO, no una copia equivalente."""
    monkeypatch.setattr(lectura, "read_passages", _no_disponible)
    spec = _spec("uno")
    reg = _registry(_tool("uno", "safety stock"))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="mensual", source="x", required=True),)
    deck = package_deliverable.build(spec, outcomes, client="Client")

    assert packages._con_lecturas(deck, reg, outcomes) is deck


def test_un_indice_disponible_pero_vacio_tambien_es_inerte(monkeypatch):
    """`available=True` con cero pasajes no puede abrir una seccion vacia."""
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: ReadingLookup(True, (), "cero"))
    spec = _spec("uno")
    reg = _registry(_tool("uno", "safety stock"))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="mensual", source="x", required=True),)
    deck = package_deliverable.build(spec, outcomes, client="Client")

    assert packages._con_lecturas(deck, reg, outcomes) is deck


def test_con_lecturas_el_deck_las_lleva_en_su_propio_campo(tmp_path, monkeypatch):
    monkeypatch.setattr(lectura, "read_passages", _disponible)
    spec = _spec("uno")
    reg = _registry(_tool("uno", "safety stock"))

    res = run_package(spec, None, registry=reg, knowledge=_NoKnowledge(),
                      clients_root=None, out_dir=tmp_path, params={})

    md = Path(res.deliverables["paquete_report"]).read_text(encoding="utf-8")
    assert "PDF p.83-84" in md          # la pagina viaja TEXTUAL, no como "p.83"
    assert "vandeput-inventory-optimization" in md


# --- 2. una sola consulta por corrida ------------------------------------------------


def test_una_sola_consulta_por_corrida_aunque_el_paquete_tenga_muchos_pasos(tmp_path, monkeypatch):
    """La restriccion de costo, fijada por un test en vez de por un comentario."""
    llamadas: list[tuple] = []

    def _espia(keywords, **kwargs):
        llamadas.append((tuple(keywords), kwargs))
        return _disponible(keywords, **kwargs)

    monkeypatch.setattr(lectura, "read_passages", _espia)
    claves = [f"t{i}" for i in range(8)]
    reg = _registry(*[_tool(k, f"tema {k}", f"otro {k}") for k in claves])

    run_package(_spec(*claves), None, registry=reg, knowledge=_NoKnowledge(),
                clients_root=None, out_dir=tmp_path, params={})

    assert len(llamadas) == 1


def test_la_consulta_cubre_todos_los_pasos_y_no_se_desborda(tmp_path, monkeypatch):
    """Ronda a ronda: la primera palabra de CADA paso entra antes que la segunda de
    ninguno. Sin eso, un paquete de 26 pasos gastaria el cupo en los tres primeros y
    las lecturas hablarian de un tercio del entregable."""
    llamadas: list[tuple] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(tuple(keywords))
                                                or _disponible(keywords, **kw)))
    claves = [f"t{i}" for i in range(8)]
    reg = _registry(*[_tool(k, f"primera {k}", f"segunda {k}") for k in claves])

    run_package(_spec(*claves), None, registry=reg, knowledge=_NoKnowledge(),
                clients_root=None, out_dir=tmp_path, params={})

    pedidas = llamadas[0]
    assert len(pedidas) <= packages._MAX_KEYWORDS_LECTURA
    assert all(f"primera t{i}" in pedidas for i in range(8))
    # y ningun paso mete mas de lo que le toca
    assert sum(1 for p in pedidas if p.startswith("primera t")) == 8


def test_un_solo_paso_manda_sus_canonicas_y_no_doce_sinonimos(monkeypatch):
    """El tope POR paso, medido contra el indice vivo (ver el comentario de la
    constante): con las doce variantes del mismo tema, 1 de 4 pasajes servia; con las
    tres canonicas, 4 de 4. Un paquete de un solo paso es el caso donde la diferencia
    se paga entera."""
    reg = _registry(_tool("uno", *[f"palabra {i}" for i in range(10)]))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="m", source="x", required=True),)

    pedidas = packages._keywords_de_lectura(reg, outcomes)

    assert pedidas == ["palabra 0", "palabra 1", "palabra 2"]


def test_un_paso_salteado_no_aporta_palabras(monkeypatch):
    """Las lecturas acompanian lo que se ENTREGO, no lo que se quiso entregar."""
    reg = _registry(_tool("uno", "safety stock"), _tool("dos", "abc analysis"))
    outcomes = (
        packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                             cadence="m", source="x", required=True),
        packages.StepOutcome(tool_key="dos", title="dos", status="skipped",
                             cadence="m", source="x", required=False),
    )

    assert packages._keywords_de_lectura(reg, outcomes) == ["safety stock"]


def test_sin_palabras_clave_no_se_consulta_nada(monkeypatch):
    """Un tool sin intent_keywords no justifica levantar un servidor de 1,6 GB."""
    llamadas: list[int] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(1) or _disponible(keywords)))
    spec = _spec("uno")
    reg = _registry(_tool("uno"))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="m", source="x", required=True),)
    deck = package_deliverable.build(spec, outcomes, client="Client")

    assert packages._con_lecturas(deck, reg, outcomes) is deck
    assert llamadas == []


# --- 3. las citas no se mueven -------------------------------------------------------


def _sin_seccion_lecturas(md: str) -> str:
    """El markdown sin el bloque de lecturas, para comparar todo lo demas."""
    lineas = md.splitlines(keepends=True)
    salida, dentro = [], False
    for linea in lineas:
        if linea.startswith("## ") and "Lecturas de referencia" in linea:
            dentro = True
            continue
        if dentro and linea.startswith("## "):
            dentro = False
        if not dentro:
            salida.append(linea)
    return "".join(salida)


@pytest.fixture(scope="module")
def _kb() -> KnowledgeBase:
    return KnowledgeBase()


@pytest.fixture(scope="module")
def _intake(tmp_path_factory) -> Path:
    return build_demo_intake(tmp_path_factory.mktemp("intake_lecturas"))


def test_las_citations_son_identicas_con_y_sin_lecturas(tmp_path, monkeypatch, _kb, _intake):
    """Un paso REAL (abc_xyz) contra el grafo L3 committeado, corrido dos veces.

    Es la garantia que el producto entero cuelga: un pasaje no es una cita, asi que
    encender las lecturas no puede mover ni una sola de las citas gateadas. Se compara
    la tupla de citas del paso Y el markdown del deck sin el bloque nuevo."""
    paso = next(s for s in DIAGNOSTICO.steps if s.tool_key == "abc_xyz")
    spec = replace(DIAGNOSTICO, key="lecturas_citas", steps=(paso,))
    reg = build_default_registry()

    def _correr(lector, destino: Path):
        monkeypatch.setattr(lectura, "read_passages", lector)
        return run_package(spec, _intake, registry=reg, knowledge=_kb,
                           clients_root=None, out_dir=destino,
                           params=dict(DEMO_PARAMS))

    con = _correr(_disponible, tmp_path / "con")
    sin = _correr(_no_disponible, tmp_path / "sin")

    assert con.status == sin.status == "ok"
    # el test no puede pasar por vacio: el paso TIENE citas
    assert con.steps[0].citations
    assert con.steps[0].citations == sin.steps[0].citations

    md_con = Path(con.deliverables["paquete_report"]).read_text(encoding="utf-8")
    md_sin = Path(sin.deliverables["paquete_report"]).read_text(encoding="utf-8")
    assert "Lecturas de referencia" in md_con and "Lecturas de referencia" not in md_sin
    assert _sin_seccion_lecturas(md_con) == md_sin
    # y ningun pasaje se colo en la seccion de citas
    metodologia = md_con.split("## ")[1:]
    citas = next(b for b in metodologia if b.startswith("Metodologia") or "cita" in b.lower())
    assert "PDF p.83-84" not in citas


# --- 4. no puede colgar una corrida --------------------------------------------------


def test_la_consulta_viaja_con_un_timeout_acotado(tmp_path, monkeypatch):
    vistos: list[dict] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (vistos.append(kw) or _disponible(keywords)))
    reg = _registry(_tool("uno", "safety stock"))

    run_package(_spec("uno"), None, registry=reg, knowledge=_NoKnowledge(),
                clients_root=None, out_dir=tmp_path, params={})

    assert vistos[0]["timeout_s"] == packages._TIMEOUT_LECTURA_S
    assert 0 < packages._TIMEOUT_LECTURA_S <= 30


def test_un_servidor_que_no_contesta_no_cuelga_el_paquete(tmp_path, monkeypatch):
    """Un servidor REAL que arranca y jamas responde, con proceso y todo.

    No hay monkeypatch del transporte: se planta un modulo `kern_lectura.mcp_server`
    falso que duerme, y `python -m` lo encuentra porque para `-m` el cwd entra a
    `sys.path`. El indice real se comporta igual cuando la maquina esta al limite de
    memoria -- que es el modo de falla que importa -- y ahi la corrida tiene que salir
    igual, sin lecturas, en segundos.

    Cuesta ~5 s: el timeout mas la baja del subproceso. No pide 1,6 GB de nada."""
    import sys

    falso = tmp_path / "servidor_lento"
    (falso / "kern_lectura").mkdir(parents=True)
    (falso / "kern_lectura" / "__init__.py").write_text("", encoding="utf-8")
    (falso / "kern_lectura" / "mcp_server.py").write_text(
        "import time\ntime.sleep(600)\n", encoding="utf-8")
    monkeypatch.chdir(falso)
    monkeypatch.setenv(ENV_INTERPRETE, sys.executable)
    monkeypatch.setattr(packages, "_TIMEOUT_LECTURA_S", 1.0)
    reg = _registry(_tool("uno", "safety stock"))
    salida = tmp_path / "salida"

    t0 = time.monotonic()
    res = run_package(_spec("uno"), None, registry=reg, knowledge=_NoKnowledge(),
                      clients_root=None, out_dir=salida, params={})
    tardo = time.monotonic() - t0

    assert res.status == "ok"                       # el paquete se entrega igual
    assert tardo < 60                               # y no espera para siempre
    md = Path(res.deliverables["paquete_report"]).read_text(encoding="utf-8")
    assert "Lecturas de referencia" not in md


# --- 5. contra el indice de verdad ---------------------------------------------------


@pytest.mark.integracion
@pytest.mark.skipif(not INDICE_REAL.is_file(),
                    reason=f"el indice de lectura no esta instalado en {INDICE_REAL}")
def test_integracion_el_indice_real_devuelve_pasajes_con_pagina(monkeypatch):
    """El unico test que levanta el indice de verdad (44 libros, 6.929 pasajes).

    Deseleccionado por defecto (`addopts = "-m 'not integracion'"` en pyproject.toml):
    tarda ~17 s con el indice frio y pide ~1,6 GB de RSS. Correrlo a mano:

        PYTHONPATH=. ./.venv/Scripts/python.exe -m pytest tests/test_packages_lecturas.py -m integracion -q
    """
    monkeypatch.setenv(ENV_INTERPRETE, str(INDICE_REAL))

    r = lectura.read_passages(["safety stock", "reorder point", "inventory"],
                              k=3, timeout_s=120.0)

    assert r.available is True, r.note
    assert r.readings
    for lec in r.readings:
        assert lec.source and lec.source != "?"
        assert lec.page_label.startswith("PDF p.") or lec.page_label == "sin pagina"
        assert lec.source.isascii() and "\n" not in lec.excerpt
@pytest.mark.integracion
@pytest.mark.skipif(not INDICE_REAL.is_file(),
                    reason=f"el indice de lectura no esta instalado en {INDICE_REAL}")
def test_integracion_el_libro_con_el_glyph_mapping_roto_no_publica_sus_extractos(monkeypatch):
    """El piso de legibilidad, contra el indice DE VERDAD y contra el libro que lo motivo.

    `vandeput-data-science-supply-chain-forecasting` tiene el glyph-mapping del PDF
    destruido: medido sobre 25 de sus pasajes, el relleno que `_sanear` fabrica va de
    0,028 a 0,206, contra un p95 de 0,019 en los otros 14 libros. Lo que se afirma aca no
    es un numero sino la conducta: de ese libro sale procedencia (libro + pagina) y no
    salen extractos ilegibles. Un test con un cable fabricado no puede probar esto,
    porque el cable fabricado lo escribi yo.

        PYTHONPATH=. ./.venv/Scripts/python.exe -m pytest tests/test_packages_lecturas.py -m integracion -q
    """
    monkeypatch.setenv(ENV_INTERPRETE, str(INDICE_REAL))

    r = lectura.read_passages(["python pandas dataframe", "machine learning forecast"],
                              k=8, sources=[_LIBRO_ROTO], timeout_s=120.0)

    assert r.available is True, r.note
    assert r.readings
    assert all(lec.source == _LIBRO_ROTO for lec in r.readings)
    # La invariante, sobre TODO lo que el indice real devuelva: nada de lo que se publica
    # puede estar por encima del piso. Si alguna vez ese libro se re-extrae bien, esto
    # sigue pasando -- mide la regla, no el estado del PDF.
    for lec in r.readings:
        assert not lectura._es_ilegible(lec.excerpt), (lec.page_label, lec.excerpt)
        assert lec.page_label.startswith("PDF p.") or lec.page_label == "sin pagina"
    # y hoy, con el PDF como esta, la mayoria queda sin extracto en vez de con basura
    assert sum(1 for lec in r.readings if not lec.excerpt) >= len(r.readings) // 2
