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

Los tres tests de la seccion 5 son los unicos que hablan con el indice de verdad: van
marcados `integracion`, que `pyproject.toml` deselecciona por defecto
(`addopts = "-m 'not integracion'"`). El segundo consulta a proposito el libro con el
glyph-mapping del PDF destruido, que es lo que motivo el piso de legibilidad del
conector. El tercero es el banco de medicion del cupo de palabras clave: re-corre contra
el indice vivo la evidencia de que ensanchar la unica consulta trae indices de libro en
vez de texto, que es lo que sostiene que 12 sea 12.
"""

from __future__ import annotations

import asyncio
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


def test_ni_un_grupo_de_excepciones_deja_la_carpeta_a_medias(monkeypatch):
    """El paquete es UN entregable coherente, no una carpeta de resultados parciales.

    `_con_lecturas` corre DESPUES de que los entregables por tool ya se escribieron a
    disco, asi que lo que se escape de aca aborta la corrida con los archivos por tool
    puestos y sin el deck del paquete. `read_passages` promete no levantar, pero su
    guarda es `except Exception` y anyio levanta `BaseExceptionGroup`, que esa clausula
    NO ve. El invariante lo tiene que sostener este lado, no la promesa del otro.

    QUE EL HIJO SEA UNA `CancelledError` ES TODO EL TEST. Python elige la clase del grupo
    por sus hijos: con hijos que son todos `Exception` construye un `ExceptionGroup`, que
    `except Exception` atrapa perfecto -- o sea que un grupo asi no ejercita el agujero y
    esta guarda pasaria igual escrita `except Exception`. El grupo que anyio levanta
    cuando adentro hubo una cancelacion lleva una `asyncio.CancelledError`, que es
    `BaseException` y NO `Exception`: ese es el que se escapa.
    """
    grupo = BaseExceptionGroup("unhandled errors in a TaskGroup",
                               [asyncio.CancelledError()])
    assert not isinstance(grupo, Exception)   # si esto falla, el test no prueba nada

    def _grupo(*a, **kw):
        raise grupo

    monkeypatch.setattr(lectura, "read_passages", _grupo)
    spec = _spec("uno")
    reg = _registry(_tool("uno", "safety stock"))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="mensual", source="x", required=True),)
    deck = package_deliverable.build(spec, outcomes, client="Client")

    assert packages._con_lecturas(deck, reg, outcomes) is deck


def test_un_ctrl_c_durante_la_lectura_sigue_cortando_la_corrida(monkeypatch):
    """La guarda de arriba no puede convertirse en un tragador de Ctrl-C."""
    def _interrumpido(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setattr(lectura, "read_passages", _interrumpido)
    spec = _spec("uno")
    reg = _registry(_tool("uno", "safety stock"))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="mensual", source="x", required=True),)
    deck = package_deliverable.build(spec, outcomes, client="Client")

    with pytest.raises(KeyboardInterrupt):
        packages._con_lecturas(deck, reg, outcomes)


def test_el_tope_de_cantidad_lo_pone_kern_y_no_el_otro_repo(monkeypatch):
    """`k` es un PEDIDO, no un contrato: el tope de lo que se publica es de este lado.

    Es el mismo argumento que ya sostiene el tope de extracto (320) y el del slug: son
    texto ajeno y Kern no tiene ningun tope propio sobre lo que renderiza. La cantidad
    era el unico eje sin tope local, y el builder hermano ya capea las citas a 10.
    """
    de_mas = tuple(
        Reading(f"libro-{i}", f"PDF p.{i}", "texto") for i in range(packages._LECTURAS_POR_PAQUETE + 7)
    )
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: ReadingLookup(True, de_mas, ""))
    spec = _spec("uno")
    reg = _registry(_tool("uno", "safety stock"))
    outcomes = (packages.StepOutcome(tool_key="uno", title="uno", status="ok",
                                     cadence="mensual", source="x", required=True),)
    deck = package_deliverable.build(spec, outcomes, client="Client")

    salida = packages._con_lecturas(deck, reg, outcomes)
    assert len(salida.readings) == packages._LECTURAS_POR_PAQUETE


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


def test_con_menos_pasos_que_cupo_todos_hablan_y_el_cupo_igual_corta(tmp_path, monkeypatch):
    """El regimen POR DEBAJO del cupo (8 pasos, cupo 12): ronda a ronda, la primera
    palabra de CADA paso entra antes que la segunda de ninguno, y recien despues el
    sobrante se reparte hasta llenar el cupo.

    El 12 va LITERAL, no como ``packages._MAX_KEYWORDS_LECTURA``. Esa es la correccion:
    escrito contra la constante, el test se mueve solo con ella y no puede fallar --
    bajarla a 8 lo dejaba igual de verde, que es exactamente el test que pasa con el
    codigo roto. El cupo es una MEDICION contra el indice vivo (ver el comentario de la
    constante), asi que tocarlo tiene que romper un test y obligar a volver a medir."""
    llamadas: list[tuple] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(tuple(keywords))
                                                or _disponible(keywords, **kw)))
    claves = [f"t{i}" for i in range(8)]
    reg = _registry(*[_tool(k, f"primera {k}", f"segunda {k}") for k in claves])

    run_package(_spec(*claves), None, registry=reg, knowledge=_NoKnowledge(),
                clients_root=None, out_dir=tmp_path, params={})

    pedidas = llamadas[0]
    assert len(pedidas) == 12                                   # el cupo, literal
    assert all(f"primera t{i}" in pedidas for i in range(8))     # los 8 pasos, con voz
    assert sum(1 for p in pedidas if p.startswith("primera t")) == 8
    # y el sobrante del cupo son segundas palabras, no un noveno paso inventado
    assert sum(1 for p in pedidas if p.startswith("segunda t")) == 4


def test_con_mas_pasos_que_cupo_los_ultimos_quedan_mudos(tmp_path, monkeypatch):
    """El regimen POR ENCIMA del cupo, que es el de TODOS los paquetes que se venden:
    starter tiene 15 pasos, growth 26, scale y retainer_ejecutivo 35, contra un cupo de
    12. Este es el caso que ningun test cubria, y el que decide que ven las lecturas.

    Lo que se afirma no es que "esta bien" sino QUE PASA, exactamente: entran 12 palabras,
    una canonica por cada uno de los primeros 12 pasos, y del paso 13 en adelante no
    entra nada -- ni su canonica, ni sus sinonimos. Ese silencio es el precio declarado de
    gastar un solo arranque de servidor por corrida, y subir el cupo para levantarlo esta
    REFUTADO por medicion (a 26 terminos los 5 pasajes que vuelven son 5 indices de libro;
    ver la constante). Que este escrito aca es lo que lo vuelve una decision y no un
    descuido."""
    llamadas: list[tuple] = []
    monkeypatch.setattr(lectura, "read_passages",
                        lambda keywords, **kw: (llamadas.append(tuple(keywords))
                                                or _disponible(keywords, **kw)))
    claves = [f"t{i:02d}" for i in range(20)]
    reg = _registry(*[_tool(k, f"primera {k}", f"segunda {k}", f"tercera {k}")
                      for k in claves])

    run_package(_spec(*claves), None, registry=reg, knowledge=_NoKnowledge(),
                clients_root=None, out_dir=tmp_path, params={})

    pedidas = list(llamadas[0])
    assert pedidas == [f"primera t{i:02d}" for i in range(12)]
    # los 8 pasos de la cola no aportan NADA, ni siquiera su termino canonico
    mudos = claves[12:]
    assert not [p for p in pedidas if any(k in p for k in mudos)]


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


@pytest.mark.integracion
@pytest.mark.skipif(not INDICE_REAL.is_file(),
                    reason=f"el indice de lectura no esta instalado en {INDICE_REAL}")
def test_integracion_ensanchar_la_consulta_trae_indices_de_libro_en_vez_de_texto(monkeypatch):
    """El banco de medicion del cupo ``_MAX_KEYWORDS_LECTURA``, re-corrible.

    El cupo deja mudos a 23 de los 35 pasos de ``scale``, y el arreglo que parece obvio
    -- subirlo hasta que entren todos -- esta refutado. Esto es la refutacion, contra el
    indice DE VERDAD: mandando los terminos canonicos de los primeros N pasos de scale se
    cuenta cuantos de los 5 pasajes que vuelven son back-matter (indice alfabetico, tabla
    de contenidos, lista de simbolos) en vez de texto del libro. Medido 2026-09-11:

        n=4 -> 0/5,  n=8 -> 1/5,  n=12 -> 1/5,  n=20 -> 3/5,  n=26 -> 5/5,  n=35 -> 5/5

    La causa es mecanica: la busqueda es por vector, asi que una consulta de 35 temas es
    un centroide sin tema, y la pagina del corpus que mas se le parece es la que menciona
    muchos temas sin desarrollar ninguno. A 26 terminos, las "lecturas de referencia" del
    cliente son 5 indices de libro con su numero de pagina.

    Se afirma la DIRECCION, no los numeros exactos: el indice puede crecer y el ranking
    moverse, pero si ensanchar la consulta dejara de degradar, el cupo habria que
    re-discutirlo y este test es el que lo avisa.

        PYTHONPATH=. ./.venv/Scripts/python.exe -m pytest tests/test_packages_lecturas.py -m integracion -q
    """
    from scm_agent.package_specs import SCALE

    monkeypatch.setenv(ENV_INTERPRETE, str(INDICE_REAL))
    reg = build_default_registry()
    canonicas: list[str] = []
    for paso in SCALE.steps:
        palabras = [str(k).strip() for k in (reg.get(paso.tool_key).intent_keywords or ())
                    if str(k).strip()]
        if palabras and palabras[0] not in canonicas:
            canonicas.append(palabras[0])
    assert len(canonicas) > packages._MAX_KEYWORDS_LECTURA  # si no, no hay nada que medir

    marcas = ("index", "table of contents", "list of symbols", "contents")

    def _back_matter(n: int) -> int:
        r = lectura.read_passages(canonicas[:n], k=5, timeout_s=180.0)
        assert r.available is True, r.note
        assert r.readings
        return sum(1 for lec in r.readings
                   if any(m in lec.excerpt.lower()[:90] for m in marcas))

    en_el_cupo = _back_matter(packages._MAX_KEYWORDS_LECTURA)
    con_todos = _back_matter(len(canonicas))

    assert con_todos > en_el_cupo, (
        f"ensanchar la consulta de {packages._MAX_KEYWORDS_LECTURA} a {len(canonicas)} "
        f"terminos ya no degrada ({en_el_cupo} -> {con_todos} pasajes de back-matter "
        "sobre 5): re-medir el cupo antes de darlo por bueno")
    assert con_todos >= 3   # y la degradacion es grosera, no marginal
