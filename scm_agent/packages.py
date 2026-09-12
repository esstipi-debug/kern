"""Commercial package runner - several registered tools, one QA-gated deliverable.

A *package* is a commercial bundle (see ``documentation/paquetes/``): a fixed set
of registry tools run together for one client, producing every tool's own
deliverable PLUS one consolidated package deck. The runner reuses each Tool's
``prepare -> run -> qa -> deliver`` callbacks verbatim (the same ones the
orchestrator drives) - no job logic is duplicated; only ``intent.classify`` is
skipped because the operator explicitly chose the package.

The per-tool guarantee "QA fails => no deliverable" is preserved at PACKAGE
level by running in two phases:

1. **Compute** every step (prepare/run/qa) with nothing written to the output
   directory. Derived inputs (e.g. the cycle-count list built from the ABC-XYZ
   classification) go to a throwaway scratch dir.
2. **Write** deliverables only if every step that executed passed QA. One
   failing step - required or optional - means nothing is written at all: a
   package is a single coherent deliverable, not a folder of partial results.

Optional steps whose input file is absent (or whose gate says no, e.g. Odoo
without credentials) are *skipped*, recorded in the coverage table, and do not
block the package.
"""

from __future__ import annotations

import inspect
import tempfile
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from itertools import zip_longest
from pathlib import Path

import pandas as pd

from jobs.intake import ALIASES as BASE_ALIASES
from nosilo_core.llm import LLMProvider, get_provider, narrative_rewrite
from nosilo_core.registry import ToolRegistry
from nosilo_core.types import (
    STATUS_ERROR,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobRequest,
)
from src import client_profile, vertical_profile
from src.connectors import lectura
from src.deliverable import DEFAULT_BRANDING, Branding, Deliverable

from . import citation_gate
from .knowledge import KnowledgeBase
from .narrative_critic import review_narrative
from .tools import build_default_registry

STEP_SKIPPED = "skipped"

# E5 citation grounding for a package step (see _step_citations). The same
# limit=3 shallow-pool recall bug fixed in jobs/integrated_plan.py and
# jobs/price_intelligence.py (3.0-audit finding #7) affected _run_step too:
# with a top-3 candidate pool, on-topic citations ranked below #3 never reached
# the strict 2-hop gate, so 6 tools (inventory_optimization, pricing,
# excel/odoo_replenishment, risk, reconciliation) shipped ZERO citations in
# every package that runs them, incl. inventory_optimization in every inventory
# package. Widening the pool recovers all six with on-topic citations.
# The ceiling is EMPIRICAL and must be RE-MEASURED whenever a source is added to
# the books graph -- it MOVES, in both directions. At 25 sources data_quality
# re-admitted TQM/QFD hub-noise at pool 11 and cycle_count cash-cycle noise at 12,
# so the pool was pinned at 8. Adding Khan et al. (2022) as source #27 shifted the
# ranking enough that pool 8 now STARVES four tools that previously had citations
# (odoo_replenishment, risk, digital_twin, launch_readiness), while the noise
# threshold rose: measured across all 49 anchored tools (citation_gate.TOOL_CONCEPTS
# -- every registered tool except intake_scan) on the then-3002-node graph, 16 is the
# exact ceiling -- at 17 data_quality re-admitted "Cost of Quality" and "House of
# Quality". 16 also recovers price_watch, a pre-existing zero.
# RE-MEASURED at source #28 (KERN_BRECHAS_DE_CAPACIDAD.md, 3002 -> 3019 nodes): the
# ceiling did NOT move and nothing regressed. Sweeping all 49 anchored tools at pool
# 16 on both graphs gives the SAME nine zeros (whatif, org_friction_audit,
# data_quality, dea, earned_value, learning_curve, cycle_count, slotting,
# vehicle_routing -- all pre-existing), and one tool gains: abc_xyz 3 -> 4. The
# pool-17 noise probe no longer reproduces because data_quality now keeps nothing at
# any pool 15-18, so its two hub citations are not the canary they were. Source #28
# is internal (_INTERNAL_SOURCES) and filtered inside ground_citations_detailed, but
# it is filtered AFTER search() ranks, so its nodes can still consume pool slots --
# which is why this was measured rather than assumed.
# Verify with a full 49-tool sweep, NOT just the package tools: the tools that
# silently regressed here were precisely the ones no test covered. See
# tests/test_packages_citations.py::test_no_anchored_tool_regressed_to_zero.
# Nine of the 49 anchored tools returned zero at this pool. Re-measured on the
# committed 3019-node graph (2026-08-24), that was ONE problem misfiled as another:
# four of the nine ranked their own anchor at hop 0 as a perfectly on-topic candidate
# and then lost the whole batch to the MIN_CITATIONS=2 floor -- one survivor, degraded
# to none. They were a citation short, not starved of theory, and one extra anchor each
# recovered them: whatif, dea, earned_value, learning_curve (measurements in
# citation_gate.TOOL_CONCEPTS).
# The five that stay zero do so for two DIFFERENT reasons, both written down there and
# pinned in tests/test_packages_citations.py -- pool sizing fixes neither:
#   cycle_count / data_quality / intake_scan -- every in-radius candidate is a lexical
#     false friend (this tool's "Cycle-Count Program" pulls Paycheck Protection Program
#     and Biological Nutrient Cycle; "SKU Master ... Quality" pulls Master Production
#     Schedule and Cost of Quality). Zero is the honest output.
#   slotting / vehicle_routing / org_friction_audit -- the corpus carries no
#     storage-assignment, VRP or performance-measurement-dysfunction theory at all.
#     Fixed by ingesting sources, not by a looser anchor.
_CANDIDATE_POOL = 16  # candidates grounded and offered to the strict gate
_MAX_CITATIONS = 3    # kept, on-topic survivors ultimately shown (tight display)


def _step_citations(knowledge: KnowledgeBase, tool, tool_key: str) -> tuple[str, ...]:
    """The E5-gated L3 citations for one package step (golden rule 7 + the
    citation gate). Grounds on the fixed ``tool.title`` -- the method
    identifier -- NOT the ``f"{spec.title}: {tool.title}"`` request brief: the
    package-name prefix injected cross-domain noise (e.g. "Market Growth" into
    reconciliation) and made a tool's citations differ between packages. Keeping
    ``tool.title`` (rather than dropping the query entirely, as the two sibling
    fixes do) preserves tools like ``fefo`` whose on-topic tokens live in the
    title. Wider pool, tight capped display; the gate (anchors/MAX_HOPS/
    MIN_CITATIONS) is untouched."""
    candidates = knowledge.ground_citations_detailed(tool.intent_keywords, tool.title, limit=_CANDIDATE_POOL)
    return citation_gate.filter_citations(knowledge, tool_key, candidates).kept[:_MAX_CITATIONS]


# --- procedencia de libro: RIO ABAJO del gate de citas -------------------------------
#
# QUE ES ESTO Y DONDE ENGANCHA. El indice de lectura (``src/connectors/lectura.py``:
# 44 libros de supply chain partidos en 6.929 pasajes con su pagina) acompania el deck
# CONSOLIDADO del paquete con una seccion propia. No es una cita: un pasaje no tiene
# ``node_id`` y ``filter_citations`` lo rechazaria con razon, asi que nada de esto entra
# al pool de candidatas de ``_step_citations`` ni al campo ``citations``. El grafo L3
# sigue siendo la unica fuente de citas; esto es una segunda clase de respaldo, mas
# debil y declarada como tal (``tests/test_packages_lecturas.py`` lo fija corriendo un
# paso real dos veces y comparando las citas).
#
# POR QUE UNA SOLA CONSULTA, Y POR QUE EN EL DECK DEL PAQUETE. Esta medido: cada
# ``read_passages`` levanta y baja el servidor entero -- 16,8 s con el indice frio, y el
# proceso pide ~1,6 GB de RSS. Las dos alternativas se descartaron por eso:
#
#   * una consulta POR PASO -- un paquete Growth son 26 pasos, o sea 26 arranques del
#     servidor para llenar una seccion de a lo sumo cinco lineas;
#   * una sesion abierta reusada entre pasos -- un solo arranque, si, pero exige sacar
#     la sesion afuera del conector (hoy ``read_passages`` la abre y la cierra adentro,
#     que es lo que le permite no levantar NUNCA) y, sobre todo, no tendria a donde
#     dejar cada resultado: los ~37 decks por tool se arman y se escriben dentro de
#     ``tool.deck``, que devuelve rutas, no un ``Deliverable``. El unico Deliverable que
#     una corrida tiene ARMADO en la mano es el del paquete, y es uno solo.
#
# Se paga entonces un unico arranque por corrida, y el precio de esa eleccion se declara:
# la consulta es un promedio del paquete, no del paso, asi que en un paquete ancho las
# lecturas hablan del tema comun y no del detalle de cada tool.

#: Cuantas palabras clave entran a esa unica consulta, y cuantas puede aportar UN paso.
#: Los dos numeros salen de medir contra el indice vivo (k=4, familia abc/inventario),
#: no de estimar. El servidor busca por VECTOR (``search_passages`` -> LightRAG en modo
#: naive), asi que la consulta es un centroide y los extremos fallan de los dos lados:
#:
#:   1 termino  ("abc-xyz")                       -> 0 de 4 utiles: un token corto y
#:                                                   guionado cae en paginas de OCR roto
#:                                                   que contienen literalmente "-xyz";
#:   3 canonicos ("abc-xyz, abc analysis, pareto") -> 4 de 4 utiles, el mejor medido;
#:   12 sinonimos del MISMO tema                   -> 1 de 4: "classify"/"classification"
#:                                                   arrastran capitulos de clustering y
#:                                                   de Python, no de inventario;
#:   8 terminos de 4 temas (DIAGNOSTICO)           -> 3 de 4 utiles.
#:
#: De ahi el tope POR PASO: un paquete de un solo tool manda 3 terminos canonicos (el
#: mejor caso medido) en vez de 12 variantes del mismo, y uno de cuatro pasos llena los
#: 12 con vocabulario de cuatro temas (el caso decente).
#:
#: QUE PASA EN UN PAQUETE ANCHO, Y POR QUE EL CUPO NO SE SUBE. Medido con el registry
#: real: starter tiene 15 pasos, growth 26, scale y retainer_ejecutivo 35, y el cupo de
#: 12 deja con voz a 12 de ellos. En scale eso son 23 tools que no aportan NI UNA palabra
#: (landed_cost, returns, risk, cost_to_serve, fefo, multi_echelon, ddmrp, simulation,
#: drp, odoo_replenishment, sourcing, acceptance_sampling, learning_curve, dea, sop,
#: facility_location, transportation, warehouse_layout, slotting, queuing, scheduling,
#: earned_value, leadership_chain). Suena a que el arreglo es subir el cupo hasta que
#: entren todos. Esta REFUTADO, y se refuto midiendo contra el indice vivo, no discutiendo:
#: mandando los terminos canonicos de los primeros N pasos de scale (k=5 pasajes por
#: consulta) se cuenta cuantos de los 5 pasajes que vuelven son BACK-MATTER -- indice
#: alfabetico, tabla de contenidos, lista de simbolos -- en vez de texto del libro:
#:
#:      n=4  -> 0 de 5      n=12 -> 1 de 5      n=26 -> 5 de 5
#:      n=6  -> 0 de 5      n=15 -> 2 de 5      n=35 -> 5 de 5
#:      n=8  -> 1 de 5      n=20 -> 3 de 5
#:
#: La curva tiene una explicacion mecanica, no es ruido: la busqueda es por VECTOR, asi
#: que una consulta de 35 temas distintos es un centroide sin tema, y las paginas del
#: corpus que mas se le parecen son exactamente las que mencionan muchos temas sin
#: desarrollar ninguno -- el indice del final del libro. A 26 terminos, los 5 pasajes que
#: acompanan el deck del cliente son 5 indices de libro con su numero de pagina: peor que
#: no citar. De 8 a 12 el ruido es el MISMO (1 de 5), asi que 12 domina a 8 -- misma
#: precision, el doble de tools con voz -- y arriba de 12 degrada monotono. El cupo esta
#: en el ultimo escalon de la meseta, medido de los dos lados.
#:
#: Bajar :data:`_MAX_KEYWORDS_POR_PASO` a 1 cuando hay mas pasos que cupo NO cambia nada:
#: la seleccion es ronda a ronda, asi que con 12 pasos o mas la primera ronda ya llena el
#: cupo sola y ningun paso llega a aportar su segundo termino. Verificado sobre scale: los
#: 12 terminos que salen hoy son un canonico de cada uno de los primeros 12 pasos.
#:
#: Queda entonces un precio, y se declara en vez de disimularse: en un paquete ancho las
#: lecturas respaldan la espina del paquete (los primeros pasos del spec, que es donde
#: package_specs pone el nucleo de inventario del que cuelga el resto) y no el detalle de
#: cada tool. Lo fija `tests/test_packages_lecturas.py`, que corre un paquete con MAS
#: pasos que cupo y afirma cuales quedan mudos; los dos numeros de abajo estan escritos
#: literales en ese test a proposito, para que moverlos rompa un test y obligue a volver
#: a medir contra el indice en vez de estimar.
_MAX_KEYWORDS_LECTURA = 12
_MAX_KEYWORDS_POR_PASO = 3

#: Cuantos pasajes se piden. Es una seccion de respaldo al pie de un deck, no un capitulo.
_LECTURAS_POR_PAQUETE = 5

#: Segundos por request del conector (initialize y tool call cuentan por separado, ver
#: ``lectura._stdio_caller``). Acotado a proposito: una corrida de paquete puede durar
#: minutos y esto es lo ULTIMO que hace antes de escribir, asi que un indice que no
#: contesta tiene que degradar a "sin lecturas" y dejar salir el entregable.
_TIMEOUT_LECTURA_S = 20.0


def _keywords_de_lectura(registry: ToolRegistry, outcomes) -> list[str]:
    """Las palabras clave de los pasos EJECUTADOS, una ronda por vez.

    Ronda a ronda -- la primera palabra de cada paso antes que la segunda de ninguno --
    porque ``intent_keywords`` es una lista de sinonimos de intencion ordenada de mas a
    menos canonica (``"abc-xyz", "abc xyz", "abc analysis", ...``). Tomandolas en orden
    plano, un paquete de 26 pasos gastaria el cupo entero en variantes del primer tool.
    Ojo con lo que esto SI y NO arregla: cambia QUE pasos entran al cupo, no CUANTOS. Con
    mas pasos que cupo siguen entrando :data:`_MAX_KEYWORDS_LECTURA` y el resto queda
    mudo; lo que se gana es que el cupo se gaste en un tool distinto por palabra en vez
    de en cuatro sinonimos del primero. Por que el cupo no sube para que entren todos
    esta medido arriba, en el comentario de la constante. Y cada paso aporta a lo sumo
    :data:`_MAX_KEYWORDS_POR_PASO`, que es lo que la medicion contra el indice vivo dejo
    claro: pasada la tercera, lo que entra son sinonimos genericos ("classify",
    "classification") que traen capitulos de otra cosa.

    Solo los pasos ``ok``: un paso salteado no aporta nada al deck, y una lectura sobre
    un analisis que no se entrego es procedencia de algo que el cliente no recibio.
    """
    por_paso = [
        [p for p in (str(k).strip() for k in (registry.get(o.tool_key).intent_keywords or ())) if p
         ][:_MAX_KEYWORDS_POR_PASO]
        for o in outcomes if o.status == STATUS_OK
    ]
    elegidas: list[str] = []
    vistas: set[str] = set()
    for ronda in zip_longest(*por_paso):
        for palabra in ronda:
            if palabra is None or palabra.lower() in vistas:
                continue
            vistas.add(palabra.lower())
            elegidas.append(palabra)
            if len(elegidas) >= _MAX_KEYWORDS_LECTURA:
                return elegidas
    return elegidas


def _con_lecturas(deck: Deliverable, registry: ToolRegistry, outcomes) -> Deliverable:
    """El deck del paquete con su seccion de lecturas, si hubo indice que consultar.

    INERTE cuando no lo hay, que es SIEMPRE en produccion: la VM tiene 512 MB y el
    indice pide 1,6 GB, asi que ``KERN_LECTURA_PYTHON`` no esta seteada y el conector
    contesta ``available=False`` sin intentar levantar nada. En ese caso vuelve el MISMO
    objeto, no una copia equivalente: el entregable sale byte a byte como lo arma
    ``jobs/package_deliverable.py``, sin seccion vacia y sin una nota contandole al
    cliente que falto algo que nunca se le prometio.

    ``Deliverable`` es frozen: ``dataclasses.replace``, nunca mutacion. Y nada de lo que
    vuelve puede tirar la corrida -- ``read_passages`` no levanta, por contrato propio.
    """
    keywords = _keywords_de_lectura(registry, outcomes)
    if not keywords:
        return deck
    try:
        lecturas = lectura.read_passages(
            keywords, k=_LECTURAS_POR_PAQUETE, timeout_s=_TIMEOUT_LECTURA_S
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 - ver abajo: el invariante lo sostiene este lado
        # `read_passages` promete no levantar, pero su guarda es `except Exception` y
        # anyio levanta `BaseExceptionGroup`, que esa clausula NO ve. Este punto corre
        # DESPUES de que los entregables por tool ya se escribieron, asi que lo que se
        # escape deja la carpeta de resultados parciales que el docstring de este modulo
        # promete que nunca ocurre. Una lectura que falta no puede costar el paquete.
        return deck
    if not lecturas.available or not lecturas.readings:
        return deck
    # El tope de CANTIDAD lo pone Kern, no el otro repo: `k` viaja como pedido y nadie
    # garantiza que se respete. Mismo argumento que el tope de extracto y el del slug --
    # es texto ajeno y aca no hay ningun tope propio sobre lo que se renderiza.
    return replace(deck, readings=lecturas.readings[:_LECTURAS_POR_PAQUETE])


@dataclass(frozen=True)
class PackageInput:
    """One intake file the client (or operator) supplies, by well-known name."""

    slot: str            # short id, e.g. "ventas"
    filename: str        # expected file inside the intake dir, e.g. "ventas.csv"
    description: str     # what it is, for the operator and needs_data messages
    columns: str         # human summary of the required columns


@dataclass(frozen=True)
class PackageStep:
    """One tool of the package: which registry tool, fed from which intake slot."""

    tool_key: str
    input_slot: str | None = None          # None => the tool needs no file (e.g. odoo)
    required: bool = True
    cadence: str = "mensual"               # shown in the coverage table
    params: dict = field(default_factory=dict)  # step defaults; caller params win
    # Synthesizes this step's input from earlier reports (dict tool_key -> report).
    # The frame is written to a scratch CSV and fed through tool.prepare unchanged.
    derive: Callable[[dict[str, object]], pd.DataFrame] | None = None
    # Fallback input when the slot file is absent: the step still runs, on a
    # standard template, and the coverage table says so.
    fallback: Callable[[], pd.DataFrame] | None = None
    fallback_note: str = ""
    # Returns "" to run, or a human skip-reason (e.g. no Odoo credentials).
    gate: Callable[[dict], str] | None = None
    # For tools whose real input is a PARAMETER, not a CSV column (e.g.
    # leadership_chain takes params["scores"], not a data_path): given the
    # resolved input file, returns the params overrides to merge in before
    # calling tool.prepare. None => the file is passed through as data_path,
    # unchanged, the normal way.
    params_from_input: Callable[[Path], dict] | None = None
    # For a tool that optionally reads a SECOND file from a DIFFERENT intake
    # slot as a params path (e.g. markdown_liquidation's own data_path is the
    # "stock" slot, but it also optionally reads params["price_history_path"]
    # from the "ventas" slot): {param_key: slot_name}. Resolved against the
    # SAME intake dir as the step's own input, before tool.prepare runs; the
    # param is simply omitted (never a hard error) when that slot's file is
    # absent, matching every other optional-input degrade in this runner.
    extra_input_params: dict[str, str] = field(default_factory=dict)
    # The report-sourced sibling of params_from_input (which sources params
    # from a FILE): given the reports of every step that already ran (dict
    # tool_key -> report), returns params overrides to merge in before
    # tool.prepare/run - e.g. excel_replenishment preferring a prior
    # inventory_optimization step's fresh (R,S) policy over the client's own
    # sheet column (see package_specs.py::_optimized_targets_from_inventory).
    # Return {} when the source step didn't run or has nothing to contribute -
    # the step then degrades to its file-only behavior, never a hard error.
    derive_params: Callable[[dict[str, object]], dict] | None = None
    # The tool_keys this step reads out of `reports`. DECLARED, not inferred:
    # `derive` and `derive_params` are callables, so their real dependency lives
    # inside a closure (`_cycle_count_from_abc` reads reports["abc_xyz"]) where
    # nothing can see it. That was harmless while run_package was strictly
    # serial and every earlier step had already run; it stops being harmless the
    # moment steps can run out of order -- a naive fan-out over spec.steps
    # breaks cycle_count with a KeyError. run_package(workers=N) schedules on
    # this field, and tests/test_packages_fanout.py fails any step that has a
    # derive and does not declare where it derives FROM.
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageSpec:
    """A sellable package: fixed scope, fixed inputs, fixed price anchor.

    ``price`` and scope mirror documentation/MONETIZATION_BRIEF.md (the source
    of truth) - if they diverge, fix the brief or this spec in the same PR.
    """

    key: str
    title: str
    price: str
    cadence: str
    audience: str
    inputs: tuple[PackageInput, ...]
    steps: tuple[PackageStep, ...]
    # Deck language (see src/i18n.py) - "title"/"price"/"cadence"/"audience"
    # above are brand/commercial copy and stay as-is regardless; this controls
    # the deck's own generated labels/headers and each step's tool title.
    # PackageSpec instances are shared module-level singletons (one per
    # package, reused for every client) - select a client's language with
    # ``dataclasses.replace(spec, lang="en")`` before calling run_package,
    # never by mutating the shared constant.
    lang: str = "es"

    def input_for(self, slot: str) -> PackageInput:
        match = next((i for i in self.inputs if i.slot == slot), None)
        if match is None:
            raise KeyError(f"package {self.key}: unknown input slot '{slot}'")
        return match

    def tool_keys(self) -> tuple[str, ...]:
        return tuple(s.tool_key for s in self.steps)


@dataclass(frozen=True)
class StepOutcome:
    """What happened to one step - fed to the coverage table of the package deck."""

    tool_key: str
    title: str
    status: str                 # ok | skipped | needs_data | needs_clarification | qa_failed | error
    cadence: str
    source: str                 # where its input came from (file / derived / connector)
    required: bool
    summary: str = ""
    qa_issues: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    skip_reason: str = ""
    report: object | None = None
    guided: object | None = None       # GuidedOutcome when the tool ranks options
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageResult:
    """The package-level outcome. ``deliverables`` is empty unless status is ok."""

    status: str
    package: str
    client: str
    steps: tuple[StepOutcome, ...]
    deliverables: dict[str, str]
    summary: str
    missing_inputs: tuple[str, ...] = ()
    qa_issues: tuple[str, ...] = ()


def _resolve_input(
    spec: PackageSpec, step: PackageStep, intake: Path | None, scratch: Path,
    reports: dict[str, object],
) -> tuple[str | None, str, str]:
    """Resolve a step's data file: (path or None, source label, note). Raises
    FileNotFoundError with the intake checklist line when a required file is absent."""
    if step.derive is not None:
        frame = step.derive(reports)
        path = scratch / f"derived_{step.tool_key}.csv"
        frame.to_csv(path, index=False)
        return str(path), "derivado de un paso anterior del paquete", ""
    if step.input_slot is None:
        return None, "conector (sin archivo)", ""
    slot = spec.input_for(step.input_slot)
    candidate = (intake / slot.filename) if intake is not None else None
    if candidate is not None and candidate.exists():
        return str(candidate), slot.filename, ""
    if step.fallback is not None:
        frame = step.fallback()
        path = scratch / f"fallback_{step.tool_key}.csv"
        frame.to_csv(path, index=False)
        return str(path), f"plantilla estandar ({slot.filename} no provisto)", step.fallback_note
    raise FileNotFoundError(
        f"{slot.filename} - {slot.description}. Columnas: {slot.columns}"
    )


def missing_required_inputs(spec: PackageSpec, intake_dir: str | Path | None) -> list[str]:
    """The intake checklist lines still unmet - what to ask the client for."""
    intake = Path(intake_dir) if intake_dir is not None else None
    missing: list[str] = []
    for step in spec.steps:
        if not step.required or step.input_slot is None or step.derive or step.fallback:
            continue
        slot = spec.input_for(step.input_slot)
        present = intake is not None and (intake / slot.filename).exists()
        line = f"{slot.filename} - {slot.description}. Columnas: {slot.columns}"
        if not present and line not in missing:
            missing.append(line)
    return missing


def _deck_accepts(deck: Callable | None, parametro: str) -> bool:
    """Whether this tool's deck takes ``parametro``, asked of the callable itself.

    The shared mechanism behind :func:`deck_accepts_lang` and
    :func:`deck_accepts_readings`. Generalised rather than copied because it is literally
    the same question twice, and a second hand-written ``inspect.signature`` is a second
    place to get the ``None`` case wrong. The two public wrappers stay separate: each one
    documents *why its own* parameter is asked instead of declared, which is the part a
    reader needs and the part a shared docstring could not carry.
    """
    if deck is None:
        return False
    return parametro in inspect.signature(deck).parameters


def deck_accepts_lang(deck: Callable | None) -> bool:
    """True when this tool's deck can be told which language the package sells in.

    Asked of the callable rather than declared on the Tool on purpose. A flag would have
    to be remembered the day a ``build_deck`` *gains* ``lang``, and the cost of forgetting
    is a deck that silently keeps rendering English inside a Spanish package - the same
    shape of silent default the rest of this work has been removing. Introspection cannot
    drift; ``tests/test_package_threads_lang_to_tool_decks.py`` keeps the inventory visible.
    """
    return _deck_accepts(deck, "lang")


def deck_accepts_readings(deck: Callable | None) -> bool:
    """True when this tool's deck knows how to carry book passages (``Deliverable.readings``).

    Misma pregunta que :func:`deck_accepts_lang` y por la misma razon: se le pregunta al
    callable en vez de declararlo, porque un flag hay que acordarse de actualizarlo el dia
    que un ``build_deck`` GANA el parametro. Aca ademas la respuesta decide un COSTO, no
    solo un render: el Orchestrator solo consulta el indice cuando hay quien renderice lo
    que vuelva, y cada consulta levanta y baja el servidor de lectura entero (16,8 s con
    el indice frio, ~1,6 GB de RSS). Un flag desactualizado falla de los dos lados -- en
    False sobre un deck que ya sabe recibirlas es una seccion que nunca aparece, en True
    sobre uno que no, un arranque de servidor por corrida que nadie mira.

    Vive al lado de su hermana a proposito: los 53 decks registrados tienen UNA forma
    (``scm_agent/tools.py``), y las preguntas sobre esa forma se hacen todas desde el
    mismo lugar. Hoy los 53 contestan True -- el parametro se enhebra en el mismo
    ``replace(...)`` que ya armaba ``options``, sin tocar ningun ``build_deck`` --, asi
    que el unico False vivo es el de un tool sin deck. Eso NO vuelve inutil la pregunta:
    es lo que hace que el deck 54, si naciera con la firma vieja, no arrastre un arranque
    de servidor por corrida. ``tests/test_tools_threads_readings_to_decks.py`` mantiene el
    inventario a la vista y falla si alguno se queda mudo.
    """
    return _deck_accepts(deck, "readings")


def _truncar_como_el_serial(
    spec: PackageSpec, hechos: dict[str, StepOutcome],
) -> tuple[list[StepOutcome], int | None]:
    """Los outcomes en orden de ``spec.steps``, cortados donde el serial cortaba.

    ES LO QUE HACE EQUIVALENTES A LOS DOS CAMINOS, y por eso lo comparten. El
    runner serial recorre los pasos en orden y sale en el PRIMER paso requerido
    que no quedo ok ni salteado, devolviendo los outcomes hasta ese inclusive.
    Con fan-out no se puede des-ejecutar lo que ya arranco, pero si truncar:
    aplicando la misma regla sobre lo que haya en ``hechos``, el PackageResult
    sale identico al serial haya corrido lo que haya corrido. Cancelar lo no
    arrancado queda entonces como ahorro de tokens, no como requisito de
    correctitud -- que es un lugar mucho mejor para ponerlo.

    Un paso ausente de ``hechos`` corta la lista igual: es exactamente el caso
    en que el serial nunca habria llegado a el.
    """
    outcomes: list[StepOutcome] = []
    for step in spec.steps:
        outcome = hechos.get(step.tool_key)
        if outcome is None:
            break
        outcomes.append(outcome)
        if step.required and outcome.status not in (STATUS_OK, STEP_SKIPPED):
            return outcomes, len(outcomes) - 1
    return outcomes, None


def _fan_out(
    spec: PackageSpec,
    ejecutar: Callable[[PackageStep, dict[str, object]], StepOutcome],
    workers: int,
    reports: dict[str, object],
) -> dict[str, StepOutcome]:
    """Corre los pasos en paralelo respetando ``PackageStep.depends_on``.

    Un paso es lanzable cuando sus dependencias TERMINARON, no cuando quedaron
    ok: si una dependencia no requerida falla, el serial sigue igual y el
    dependiente corre con ``reports`` sin esa clave -- su ``derive`` revienta y
    ``_run_step`` lo convierte en STATUS_ERROR. Reproducir eso es parte de la
    equivalencia, no un bug a arreglar.

    Cada paso recibe una INSTANTANEA de ``reports``, no el dict vivo: sus
    dependencias ya terminaron, asi que la instantanea trae todo lo que
    necesita, y de paso desaparece la carrera entre un hilo que lee y el
    principal que escribe.

    Los CSV de scratch no chocan: ``_resolve_input`` los nombra por tool_key
    (derived_<key>.csv / fallback_<key>.csv) y test_no_duplicate_steps ya
    garantiza que esa clave es unica dentro de un spec.

    Al primer paso requerido que falla se deja de LANZAR trabajo nuevo, lo que
    ahorra tokens de LLM y, en odoo_replenishment, llamadas a un Odoo vivo. Lo
    que ya estaba en vuelo se drena y despues lo trunca
    ``_truncar_como_el_serial``.
    """
    pendientes = {s.tool_key: s for s in spec.steps}
    hechos: dict[str, StepOutcome] = {}
    corte_visto = False
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kern_pkg") as pool:
        en_vuelo: dict[Future, PackageStep] = {}
        while True:
            if not corte_visto:
                listos = [k for k, s in pendientes.items()
                          if all(d in hechos for d in s.depends_on)]
                for clave in listos:
                    step = pendientes.pop(clave)
                    en_vuelo[pool.submit(ejecutar, step, dict(reports))] = step
            if not en_vuelo:
                # Ni trabajo en vuelo ni nada lanzable: o terminamos, o quedan
                # pasos cuyas dependencias no van a llegar nunca (un depends_on
                # que no existe en el spec). Se sale en vez de colgarse; el test
                # de tests/test_packages_fanout.py impide que eso llegue a main.
                break
            terminados, _ = wait(en_vuelo, return_when=FIRST_COMPLETED)
            for fut in terminados:
                step = en_vuelo.pop(fut)
                outcome = fut.result()
                hechos[step.tool_key] = outcome
                if outcome.status == STATUS_OK:
                    reports[step.tool_key] = outcome.report
                elif outcome.status != STEP_SKIPPED and step.required:
                    corte_visto = True
    return hechos


def run_package(
    spec: PackageSpec,
    intake_dir: str | Path | None,
    *,
    client: str = "Client",
    params: dict | None = None,
    out_dir: str | Path = "deliverables/paquetes",
    registry: ToolRegistry | None = None,
    provider: LLMProvider | None = None,
    knowledge: KnowledgeBase | None = None,
    clients_root: Path | str | None = client_profile.DEFAULT_CLIENTS_ROOT,
    strict_params: bool = False,
    prepared: str = "",
    branding: Branding | None = None,
    workers: int = 1,
) -> PackageResult:
    """Run every step of ``spec`` and, only if all executed steps pass QA, write
    the per-tool deliverables plus the consolidated package deck under
    ``out_dir/<spec.key>``.

    ``branding`` (see ``src/deliverable.py``) resolves explicit call-site
    override > the loaded client's ``profile.branding`` > Kern's own
    default - only the CONSOLIDATED package deck carries it, a deliberate,
    narrow integration point rather than every deck in the bundle.

    ``spec.lang`` is scoped WIDER than that: it reaches the consolidated deck
    AND every per-tool deck whose ``build_deck`` accepts a language (see
    :func:`deck_accepts_lang`). A package sold in Spanish that hands back
    English tool decks is not a Spanish deliverable. Decks that cannot take a
    language are written unchanged, not skipped."""
    registry = registry if registry is not None else build_default_registry()
    provider = provider if provider is not None else get_provider()
    knowledge = knowledge if knowledge is not None else KnowledgeBase()
    params = dict(params or {})
    intake = Path(intake_dir) if intake_dir is not None else None
    out_root = Path(out_dir) / spec.key

    profile, profile_error = _load_profile(client, clients_root)
    if profile_error:
        return _failed(spec, client, STATUS_ERROR, profile_error)

    # El camino comercial no resolvia el vertical: `merge_params` recibia dos
    # argumentos, asi que `holding_rate: 0.22` y `order_cost: 1400` del pack de
    # repuestos no llegaban a una corrida de paquete. Se resuelve UNA vez, antes
    # del bucle, con la misma negativa a caer al generico que el orquestador: un
    # cliente que DECLARO un rubro y recibe los defaults de libro es peor que un
    # error, porque el numero equivocado viaja con cara de correcto.
    vertical = None
    if profile is not None and profile.vertical:
        try:
            vertical = vertical_profile.load_vertical_profile(profile.vertical)
        except (vertical_profile.VerticalNotConfiguredError, ValueError) as exc:
            return _failed(spec, client, STATUS_ERROR, str(exc))
    pack_aliases = (
        vertical.merged_aliases(BASE_ALIASES)
        if vertical is not None and vertical.aliases
        else None
    )

    missing = missing_required_inputs(spec, intake_dir)
    if missing:
        return PackageResult(
            status=STATUS_NEEDS_DATA, package=spec.key, client=client, steps=(),
            deliverables={}, missing_inputs=tuple(missing),
            summary=(f"{spec.title}: faltan {len(missing)} archivo(s) de intake; "
                     "no se escribio ningun entregable."),
        )

    # ---- Phase 1: compute everything; write nothing to out_root. -------------
    reports: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="linchpin_pkg_") as scratch_str:
        scratch = Path(scratch_str)

        def _ejecutar(step: PackageStep, vistos: dict[str, object]) -> StepOutcome:
            tool = registry.get(step.tool_key)
            merged = client_profile.merge_params({**step.params, **params}, profile, vertical)
            if pack_aliases is not None:
                merged = {**merged, "column_aliases": pack_aliases}
            return _run_step(spec, step, tool, intake, scratch, vistos,
                             merged, client, provider, knowledge, strict_params)

        if workers and workers > 1:
            hechos = _fan_out(spec, _ejecutar, workers, reports)
        else:
            hechos = {}
            for step in spec.steps:
                outcome = _ejecutar(step, reports)
                hechos[step.tool_key] = outcome
                if outcome.status == STATUS_OK:
                    reports[step.tool_key] = outcome.report
                elif outcome.status != STEP_SKIPPED and step.required:
                    break

        outcomes, corte = _truncar_como_el_serial(spec, hechos)
        if corte is not None:
            outcome = outcomes[corte]
            return PackageResult(
                status=outcome.status, package=spec.key, client=client,
                steps=tuple(outcomes), deliverables={},
                qa_issues=tuple(f"{outcome.title}: {i}" for i in outcome.qa_issues),
                summary=(f"{spec.title}: el paso requerido '{outcome.title}' fallo "
                         f"({outcome.status}); no se escribio ningun entregable."),
            )

        # ---- Package QA gate: any executed step failing QA blocks everything.
        failed = [o for o in outcomes if o.status == STATUS_QA_FAILED]
        if failed:
            issues = tuple(f"{o.title}: {i}" for o in failed for i in o.qa_issues)
            return PackageResult(
                status=STATUS_QA_FAILED, package=spec.key, client=client,
                steps=tuple(outcomes), deliverables={}, qa_issues=issues,
                summary=(f"{spec.title}: QA fallo en {len(failed)} paso(s); "
                         "no se escribio ningun entregable."),
            )
        errored = [o for o in outcomes if o.status not in (STATUS_OK, STEP_SKIPPED)]
        if errored:
            return PackageResult(
                status=STATUS_ERROR, package=spec.key, client=client,
                steps=tuple(outcomes), deliverables={},
                summary=(f"{spec.title}: {len(errored)} paso(s) opcional(es) fallaron; "
                         "quitalos del intake o corregi el dato - no se escribio "
                         "ningun entregable."),
            )

    # ---- Phase 2: every executed step passed QA - now (and only now) write. --
    from jobs import package_deliverable  # local import: jobs also imports scm_agent-free code

    written: dict[str, str] = {}
    for outcome in outcomes:
        if outcome.status != STATUS_OK:
            continue
        tool = registry.get(outcome.tool_key)
        step_dir = out_root / outcome.tool_key
        files = tool.deliver(outcome.report, step_dir, client)
        if tool.deck is not None:
            deck_options = list(outcome.guided.options) if outcome.guided is not None else []
            deck_lang = {"lang": spec.lang} if deck_accepts_lang(tool.deck) else {}
            deck_files = tool.deck(outcome.report, step_dir, client,
                                   list(outcome.citations), 1.0, deck_options, **deck_lang)
            files.update({f"deck_{name}": path for name, path in deck_files.items()})
        written.update({f"{outcome.tool_key}_{name}": str(path) for name, path in files.items()})

    resolved_branding = branding
    if resolved_branding is None:
        resolved_branding = profile.branding if profile is not None and profile.branding is not None else DEFAULT_BRANDING
    deck = package_deliverable.build(spec, outcomes, client=client, prepared=prepared, branding=resolved_branding)
    # Lo ULTIMO antes de escribir, y a proposito: la procedencia de libro va rio abajo de
    # todo -- del gate de QA, del gate de citas y del deck ya compuesto -- asi que no
    # puede cambiar nada de lo que se decidio arriba. Ver `_con_lecturas`.
    deck = _con_lecturas(deck, registry, outcomes)
    package_files = deck.write_all(out_root)
    written.update({f"paquete_{name}": str(path) for name, path in package_files.items()})

    executed = sum(1 for o in outcomes if o.status == STATUS_OK)
    skipped = sum(1 for o in outcomes if o.status == STEP_SKIPPED)
    return PackageResult(
        status=STATUS_OK, package=spec.key, client=client, steps=tuple(outcomes),
        deliverables=written,
        summary=(f"{spec.title}: {executed} analisis ejecutados y QA-aprobados"
                 + (f", {skipped} omitidos por falta de insumo" if skipped else "")
                 + f"; {len(written)} archivo(s) entregados."),
    )


def _run_step(
    spec: PackageSpec, step: PackageStep, tool, intake: Path | None, scratch: Path,
    reports: dict[str, object], params: dict, client: str,
    provider: LLMProvider, knowledge: KnowledgeBase, strict_params: bool,
) -> StepOutcome:
    base = dict(tool_key=step.tool_key, title=tool.title, cadence=step.cadence,
                required=step.required)
    if step.gate is not None:
        reason = step.gate(params)
        if reason:
            return StepOutcome(**base, status=STEP_SKIPPED, source="conector",
                               skip_reason=reason)
    try:
        data_path, source, note = _resolve_input(spec, step, intake, scratch, reports)
    except FileNotFoundError as exc:
        # Required-with-file steps were pre-checked; this is an optional step.
        return StepOutcome(**base, status=STEP_SKIPPED, source="(no provisto)",
                           skip_reason=f"falta el archivo: {exc}")
    except Exception as exc:  # a derive over a missing/failed source report
        return StepOutcome(**base, status=STATUS_ERROR, source="derivado",
                           messages=(str(exc),))
    base["source"] = source

    for param_key, slot_name in step.extra_input_params.items():
        slot = spec.input_for(slot_name)
        candidate = (intake / slot.filename) if intake is not None else None
        if candidate is not None and candidate.exists():
            params = {**params, param_key: str(candidate)}

    if step.derive_params is not None:
        try:
            params = {**params, **step.derive_params(reports)}
        except Exception as exc:  # mirror params_from_input's fail-loud contract below
            return StepOutcome(**base, status=STATUS_ERROR, messages=(str(exc),))

    if step.params_from_input is not None and data_path is not None:
        try:
            params = {**params, **step.params_from_input(Path(data_path))}
        except Exception as exc:
            return StepOutcome(**base, status=STATUS_ERROR, messages=(str(exc),))

    if strict_params and tool.required_client_params:
        missing = [k for k in tool.required_client_params if k not in params]
        if missing:
            return StepOutcome(
                **base, status=STATUS_NEEDS_CLARIFICATION,
                messages=tuple(f"falta el parametro '{k}' del cliente" for k in missing),
            )

    request = JobRequest(brief=f"{spec.title}: {tool.title}", data_path=data_path,
                         job_type=step.tool_key, params=params, client=client)
    try:
        prep = tool.prepare(request, provider)
        if prep.status != STATUS_OK:
            return StepOutcome(**base, status=prep.status, messages=tuple(prep.messages))
        produced = tool.run(prep.payload, params)
        qa_issues = tool.qa(produced.report)
    except Exception as exc:
        return StepOutcome(**base, status=STATUS_ERROR, messages=(str(exc),))
    if qa_issues:
        return StepOutcome(**base, status=STATUS_QA_FAILED, qa_issues=tuple(qa_issues))

    guided = tool.options(produced.report) if tool.options else None
    # Citation-grounding gate (E5): a ranked candidate must resolve to a real
    # graph node within citation_gate.MAX_HOPS of this tool's own anchor
    # concepts before it reaches the deck - see scm_agent/citation_gate.py and
    # _step_citations above. This is a content filter, not a QA veto: a step with
    # zero surviving citations still ships (its methodology section just has
    # none), the package's only hard veto stays the data QA gate above.
    citations = _step_citations(knowledge, tool, step.tool_key)
    # Optional LLM polish in the package's target language (src/i18n.py's
    # static labels cover the deterministic path around this; this is the
    # only place a package step's own narrative gets translated - see
    # nosilo_core.llm.narrative_rewrite). Skipped without a provider, matching
    # the Orchestrator's equivalent single-tool behavior exactly.
    rewritten = narrative_rewrite(
        provider, produced.summary, tool.title, lang=spec.lang, citations=list(citations),
    )
    # Narrative post-check (brecha 8): the rewrite ships only if every figure of
    # the deterministic summary survived it. Same severity model as the citation
    # gate right above -- a content filter, not a QA veto: a reverted narrative
    # still ships the step, it just ships the engine's own prose. This call is
    # NOT redundant with the Orchestrator's: filter_citations taught the lesson
    # that a gate wired into only one of the two surfaces leaves the other one
    # unreviewed, and packages/ is the surface a paying client actually receives.
    reviewed = review_narrative(produced.summary, rewritten, label=tool.title)
    summary = reviewed.summary + (f" [{note}]" if note else "")
    return StepOutcome(**base, status=STATUS_OK, summary=summary,
                       report=produced.report, guided=guided, citations=citations)


def _load_profile(client: str, clients_root: Path | str | None):
    """Mirror the orchestrator's profile semantics: fill gaps only; corrupt fails loudly."""
    if clients_root is None:
        return None, ""
    try:
        slug = client_profile.slugify_client_id(client)
    except ValueError:
        return None, ""
    try:
        return client_profile.load_profile(slug, root=Path(clients_root)), ""
    except ValueError as exc:
        return None, (f"perfil de cliente corrupto: {exc} - corregi o borra "
                      f"clients/{slug}/profile.json")


def _failed(spec: PackageSpec, client: str, status: str, summary: str) -> PackageResult:
    return PackageResult(status=status, package=spec.key, client=client,
                         steps=(), deliverables={}, summary=summary)
