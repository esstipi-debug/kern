"""The orchestrator: brief + optional data -> routed, QA-gated deliverable."""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from pathlib import Path

from jobs import intake
from nosilo_core.llm import DecisionProvider, LLMProvider, get_decider, get_provider, narrative_rewrite
from nosilo_core.registry import Tool, ToolRegistry
from nosilo_core.types import (
    STATUS_ERROR,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobRequest,
    JobResult,
)
from src import client_profile, vertical_profile
from src.connectors import lectura
from src.connectors.lectura import Reading

from . import metrics
from .guided_bridge import to_guided_outcome
from .intent import classify
from .knowledge import KnowledgeBase
from .narrative_critic import review_narrative
from .packages import deck_accepts_readings
from .tools import build_default_registry

logger = logging.getLogger(__name__)


# --- procedencia de libro en el camino SIN GATE --------------------------------------
#
# QUE ES ESTO. El mismo indice de lectura que acompania el deck consolidado del paquete
# (``scm_agent/packages.py``, ver el comentario largo de ``_con_lecturas``) acompania
# tambien el deck de una corrida single-tool. No es una cita y no entra a ``citations``:
# un pasaje no tiene ``node_id``, el grafo L3 sigue siendo la unica fuente de citas.
#
# EN QUE SE DIFERENCIA DEL CAMINO DE PAQUETE, que es lo unico que importa para leer las
# decisiones de abajo: aquel corre al lado de ``citation_gate.filter_citations`` y lo
# arma un operador; este NO tiene ningun gate -- ``_ground`` llama a
# ``ground_citations`` derecho -- y es el que atienden la web, el MCP, la CLI y los
# eventos. Lo que salga mal aca sale para el cliente sin escala. Por eso el cableado es
# el mas conservador posible: no consulta si no hay quien renderice, no toca nada de lo
# que ya se decidio, y no puede dejar una corrida a medias.

#: Cuantos pasajes acompanan el deck de UNA corrida. Menos que los 5 del paquete a
#: proposito: aquel deck consolida hasta 35 analisis y este es uno solo, asi que la
#: seccion es un pie de pagina de procedencia y no un capitulo. Es un presupuesto de
#: RENDER, no un numero medido -- lo medido es el cupo de palabras clave de abajo.
_LECTURAS_POR_DECK = 3

#: Cuantas palabras clave entran a la unica consulta. El numero sale de la medicion
#: contra el indice vivo que esta escrita en ``scm_agent/packages.py``
#: (``_MAX_KEYWORDS_POR_PASO``) y no se vuelve a discutir aca: la busqueda es por VECTOR,
#: asi que la consulta es un centroide -- con 3 terminos canonicos servian 4 de 4
#: pasajes, con 12 sinonimos del mismo tema 1 de 4. Y ``intent_keywords`` es exactamente
#: una lista de sinonimos ordenada de mas a menos canonica ("abc-xyz", "abc xyz",
#: "abc analysis", ...), o sea el peor caso si se manda entera.
_MAX_KEYWORDS_LECTURA = 3

#: Segundos por request del conector (initialize y tool call cuentan por separado, ver
#: ``lectura._stdio_caller``). Acotado a proposito: esto es decoracion sobre un
#: entregable que YA paso QA, asi que un indice que no contesta tiene que degradar a
#: "sin lecturas" en segundos y dejar salir el resultado.
_TIMEOUT_LECTURA_S = 20.0


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        provider: LLMProvider | None = None,
        knowledge: KnowledgeBase | None = None,
        persona: str = "",
        clients_root: Path | str | None = client_profile.DEFAULT_CLIENTS_ROOT,
        lang: str | None = None,
        decider: DecisionProvider | None = None,
    ) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        self.provider = provider if provider is not None else get_provider()
        # Typed router for ambiguous briefs (Jev). Like the provider: on when its key
        # is in the environment, off otherwise, and never on the deterministic path.
        self.decider = decider if decider is not None else get_decider()
        # L3 domain knowledge. Loads the books graph (committed) + code graph
        # (gitignored). Absent graphs degrade gracefully to no citations.
        self.knowledge = knowledge if knowledge is not None else KnowledgeBase()
        # The operating mode's voice for client-facing narration. Empty => the
        # narrative prompt is unchanged (the deterministic output never depends on it).
        self.persona = persona
        # Where durable per-client parameter profiles live (see src/client_profile.py).
        # None disables profile lookup entirely — the right setting for multi-tenant
        # surfaces (webapp/MCP) where `client` is a caller-supplied display label, not
        # an authenticated identity: honoring it would let one tenant pull another's
        # real cost parameters just by naming them.
        self.clients_root = None if clients_root is None else Path(clients_root)
        # Target language for the LLM narrative rewrite below (src/i18n.py's
        # static labels cover the deterministic path; this is the LLM path).
        # None (default) omits the language clause entirely - preserves this
        # class's exact pre-E4 prompt wording for its existing production
        # callers (webapp/app.py, webapp/mcp_server.py, examples/run_agent.py),
        # none of which have a way to opt into a language yet.
        self.lang = lang

    def run(
        self,
        brief: str,
        *,
        data_path: str | None = None,
        overrides: dict | None = None,
        job_type: str | None = None,
        client: str = "Client",
        strict_params: bool = False,
        out_dir: str | Path = "deliverables/agent",
    ) -> JobResult:
        overrides = overrides or {}
        request = JobRequest(brief=brief, data_path=data_path, job_type=job_type,
                             params=dict(overrides), client=client, strict_params=strict_params)
        started = time.monotonic()
        try:
            result = self._run(request, Path(out_dir))
        except Exception:  # never crash the caller — surface as error status
            logger.error("orchestrator.run failed", exc_info=True)
            result = JobResult(status=STATUS_ERROR, tool=None, confidence=0.0,
                               deliverables={}, summary="An internal error occurred.")
        # Counted here, at the single boundary every path already funnels through,
        # so a new capability is measured without touching this file. Volatile and
        # per-process by design (see scm_agent/metrics.py); it can never raise.
        metrics.record_run(result.tool, result.status, (time.monotonic() - started) * 1000.0)
        # Single boundary: every result leaves with a protected, executable path. A tool may
        # supply its own ranked-options outcome on success (set in _run); otherwise derive the
        # protected fallback. Either way, no result is a dead end.
        return replace(result, guided=result.guided or to_guided_outcome(result))

    def _run(self, request: JobRequest, out_dir: Path) -> JobResult:
        intent = classify(
            request.brief, self.registry, self.provider,
            job_type_override=request.job_type, decider=self.decider,
        )
        if intent.job_type is None:
            return JobResult(
                status=STATUS_NEEDS_CLARIFICATION, tool=None, confidence=intent.confidence,
                deliverables={}, summary="Ambiguous request — pick a capability.",
                clarifications=intent.candidates,
            )

        tool = self.registry.get(intent.job_type)
        params = {**intent.params, **request.params}
        # Client profile fills param gaps only — it never overrides an explicit
        # params/override value (merge_params puts the profile first, params last).
        profile = None
        if self.clients_root is not None:
            try:
                slug = client_profile.slugify_client_id(request.client)
            except ValueError:
                slug = None  # unslugifiable label (punctuation-only, non-Latin) => no profile;
                # the label stays what it always was — display copy on the deliverable.
            if slug is not None:
                try:
                    profile = client_profile.load_profile(slug, root=self.clients_root)
                except ValueError as exc:
                    # A corrupt profile.json must fail loudly and actionably: it is
                    # hand-answered, non-regenerable data, and silently falling back to
                    # generic defaults would give this client wrong numbers.
                    return JobResult(
                        status=STATUS_ERROR, tool=tool.key, confidence=intent.confidence,
                        deliverables={}, summary=str(exc),
                        clarifications=[
                            f"fix or delete clients/{slug}/profile.json, or rewrite it with "
                            "client_profile.upsert_profile(...)",
                        ],
                    )
        vertical = None
        if profile is not None and profile.vertical:
            try:
                vertical = vertical_profile.load_vertical_profile(profile.vertical)
            except (vertical_profile.VerticalNotConfiguredError, ValueError) as exc:
                # Same rule as the corrupt-profile branch above, for the same
                # reason: this client DECLARED an industry, and the engine's
                # generic defaults (0.95 service level, 0.25 holding rate) are
                # exactly what that industry's pack exists to correct. Falling
                # back would hand them textbook numbers with a confident face.
                # A client with no declared vertical is untouched — the layer is
                # simply absent, which is how this stays behaviour-neutral for
                # every profile written before the field existed.
                return JobResult(
                    status=STATUS_ERROR, tool=tool.key, confidence=intent.confidence,
                    deliverables={}, summary=str(exc),
                    clarifications=[
                        f"add config/verticals/{profile.vertical}.yaml, or clear the "
                        f"'vertical' field on clients/{profile.client_id}/profile.json",
                    ],
                )
        veto = vertical.forbids(intent.job_type) if vertical is not None else None
        if veto is not None:
            # No corre, y dice por que. El caso que obliga a esto es alimentos:
            # inventory_optimization y multi_echelon dimensionan buffer sin
            # ninguna nocion de vida util, asi que sobre un catalogo perecible
            # "sube el buffer" convierte capital en merma. El motor SI sabe de
            # vida util (src/lots/expiry.py, jobs/fefo_job.py) pero lo sabe en
            # otra capacidad, desde una columna que estas dos no leen -- no es un
            # parametro que falte, es un calculo que no aplica.
            #
            # Sin pasar por strict_params a proposito: ese interruptor es correcto
            # para "te falto un numero" y equivocado para "este calculo es
            # invalido sobre tu catalogo". Un veto opcional no es un veto.
            return JobResult(
                status=STATUS_NEEDS_CLARIFICATION, tool=tool.key,
                confidence=intent.confidence, deliverables={},
                summary=veto.reason.strip(),
                clarifications=[c for c in (veto.instead.strip(),) if c],
            )
        params = client_profile.merge_params(params, profile, vertical)
        if vertical is not None and vertical.aliases:
            # DESPUES de merge_params a proposito. Esta es una tabla que ESTE
            # proceso resolvio del rubro declarado por el cliente, no una perilla
            # que el llamador ajusta, asi que un params["column_aliases"] que
            # llegue por /api/jobs (JSON crudo) no puede ganarle: merge_params
            # pone los params del llamador ultimos, y por lo tanto mas altos.
            params = {**params, "column_aliases": vertical.merged_aliases(intake.ALIASES)}
        # prepare() must see the same merged params run() will: several tools bake
        # params into their payload at prepare time (multi_echelon service_level,
        # simulation order_cost, inventory lead_time_days).
        request = replace(request, params=params)

        if tool.requires_data and not request.data_path:
            return JobResult(
                status=STATUS_NEEDS_DATA, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title} needs a data file.",
                clarifications=[f"provide a data file for {tool.title}"],
            )

        if request.strict_params and tool.required_client_params:
            missing = [key for key in tool.required_client_params if key not in params]
            if missing:
                return JobResult(
                    status=STATUS_NEEDS_CLARIFICATION, tool=tool.key, confidence=intent.confidence,
                    deliverables={}, summary=f"{tool.title} needs client-specific parameters before it can run.",
                    clarifications=[
                        f"missing '{key}' for client '{request.client}' — provide it once; "
                        "it will be remembered for every future run" for key in missing
                    ],
                )

        prepared = tool.prepare(request, self.provider)
        if prepared.status != STATUS_OK:
            return JobResult(
                status=prepared.status, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title}: {prepared.status}.",
                clarifications=prepared.messages,
            )

        produced = tool.run(prepared.payload, params)
        issues = tool.qa(produced.report)
        if issues:
            return JobResult(
                status=STATUS_QA_FAILED, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title}: QA failed; no deliverables written.",
                qa_issues=issues,
            )

        # Ground first: the premium deck weaves the L3 citations in, so they must
        # be resolved before the deliver path runs.
        citations = self._ground(tool, request.brief)
        # Las lecturas, aca y no despues: es el ultimo punto de la corrida con CERO
        # archivos escritos, y de esta llamada hay excepciones que salen a proposito
        # (Ctrl-C). Ver `_lecturas`.
        readings = self._lecturas(tool)
        # Compute the ranked options once: they become JobResult.guided AND the deck's
        # action menu, so the sellable artifact carries the same choices the agent returns.
        guided = tool.options(produced.report) if tool.options else None
        deck_options = list(guided.options) if guided is not None else []
        written = tool.deliver(produced.report, out_dir / tool.key, request.client)
        if tool.deck is not None:
            # Mismo patron que `deck_lang` en `packages.py`: el kwarg SOLO viaja cuando
            # hay algo que mandar, asi que un deck que no lo espera recibe exactamente la
            # llamada que recibia antes de que este campo existiera.
            deck_readings = {"readings": readings} if readings else {}
            deck_files = tool.deck(
                produced.report, out_dir / tool.key, request.client, citations,
                intent.confidence, deck_options, **deck_readings,
            )
            written.update({f"deck_{name}": path for name, path in deck_files.items()})
        # `readings` NO viaja al narrador, y es una decision: ver `_lecturas`.
        summary = self._narrative(produced.summary, tool.title, citations)
        return JobResult(
            status=STATUS_OK, tool=tool.key, confidence=intent.confidence,
            deliverables={name: str(path) for name, path in written.items()}, summary=summary,
            citations=citations, kb_warnings=self.knowledge.warnings(),
            guided=guided, report=produced.report,
        )

    def _ground(self, tool: Tool, brief: str = "") -> list[str]:
        """Cite domain knowledge for the tool's topic, bridged to the implementing code.

        Uses tool keywords, the client brief, and method-advice hits from the L3
        graph. For each cited concept the bridge also resolves the implementing
        source when the code graph is present.
        """
        return self.knowledge.ground_citations(tool.intent_keywords, brief, limit=5)

    def _lecturas(self, tool: Tool) -> tuple[Reading, ...]:
        """Pasajes de libro para el deck de ESTA corrida, o vacio. Nunca levanta.

        SOLO SI HAY QUIEN LOS RENDERICE. Se le pregunta al deck (ver
        ``packages.deck_accepts_readings``) antes de consultar nada, porque la consulta
        no es gratis: levanta y baja el servidor de lectura entero -- 16,8 s con el
        indice frio, ~1,6 GB de RSS. Los 53 decks registrados hoy contestan que si
        (``tests/test_tools_threads_readings_to_decks.py`` lo mantiene asi), asi que la
        pregunta ya no apaga esta rama; la sigue apagando para un tool sin deck, y volveria
        a apagar un tool cuyo deck naciera con la firma vieja. En produccion igual no se
        paga nada: sin ``KERN_LECTURA_PYTHON``, ``read_passages`` contesta
        ``available=False`` sin levantar ningun proceso y el entregable sale byte a byte
        como antes de este cableado.

        UNA SOLA CONSULTA POR CORRIDA, que es todo lo que hace falta: una corrida
        single-tool produce un solo deck. Con las palabras canonicas del tool -- las
        mismas que usa :meth:`_ground` -- pero capadas a :data:`_MAX_KEYWORDS_LECTURA`,
        porque ``intent_keywords`` es una lista de SINONIMOS y mandarla entera es el peor
        caso medido de una busqueda por vector (ver la constante).

        DONDE VA, Y POR QUE ACA. Se llama antes de ``tool.deliver``, con cero archivos
        escritos todavia. El camino de paquete pone su consulta al final, justo antes de
        escribir el deck consolidado, y ahi eso es correcto porque el paquete escribe
        todo junto; aca los archivos los escriben ``deliver`` y ``deck``, asi que la
        UNICA ventana sin archivos puestos es esta. Importa porque la guarda de abajo
        re-lanza ``KeyboardInterrupt``/``SystemExit`` a proposito: hay excepciones que SI
        salen de este metodo, y si salieran despues de escribir dejarian la carpeta del
        cliente a medias y ningun ``JobResult`` devuelto. Y no puede ir mas arriba: corre
        despues de ``tool.qa()``, de modo que una lectura jamas decide si hay entregable.

        LA GUARDA. ``read_passages`` promete no levantar, pero su guarda es
        ``except Exception`` y anyio levanta ``BaseExceptionGroup``, que esa clausula NO
        ve (mismo caso que ``packages._con_lecturas``). El invariante lo sostiene este
        lado, no la promesa del otro repo.

        LO QUE NO HACE, y es deliberado: no toca ``citations`` -- un pasaje no tiene
        ``node_id`` y este camino no pasa por ``filter_citations``, asi que si se colara
        al pool no habria nada rio abajo que lo sacara -- y NO entra al prompt del
        modelo. Esto ultimo no es un olvido y no hay que "arreglarlo":
        ``narrative_rewrite`` concatena crudo lo que se le pasa, asi que un pasaje que
        vuelve de otro proceso seria texto ajeno inyectado en el prompt del unico camino
        sin gate; el resumen dejaria de ser identico con y sin indice (la inercia que
        este cableado entero defiende); y un pasaje vale por su procedencia -- libro y
        pagina fisica --, que parafraseada adentro de una frase se pierde y queda como
        afirmacion nuestra. Su lugar es la seccion de lecturas del deck, con el libro y
        la pagina al lado. Lo fija ``tests/test_orchestrator_lecturas.py``.
        """
        if not deck_accepts_readings(tool.deck):
            return ()
        keywords = [
            p for p in (str(k).strip() for k in (tool.intent_keywords or ())) if p
        ][:_MAX_KEYWORDS_LECTURA]
        if not keywords:
            return ()
        try:
            lecturas = lectura.read_passages(
                keywords, k=_LECTURAS_POR_DECK, timeout_s=_TIMEOUT_LECTURA_S
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:  # noqa: BLE001 - ver "LA GUARDA" arriba
            logger.warning("lectura: el indice fallo; el entregable sale sin lecturas",
                           exc_info=True)
            return ()
        if not lecturas.available:
            return ()
        # El tope de CANTIDAD lo pone Kern, no el otro repo: `k` viaja como pedido y
        # nadie garantiza que se respete. Mismo argumento que el tope de extracto (320) y
        # el del slug -- es texto ajeno y el render no aplica ningun tope propio.
        return tuple(lecturas.readings[:_LECTURAS_POR_DECK])

    def _narrative(self, base_summary: str, tool_title: str, citations: list[str] | None = None) -> str:
        """Optional LLM polish, grounded in the L3 citations when present, in
        ``self.lang``.

        The returned summary is untrusted display text (it echoes the brief and any
        LLM output); escape it at the render site if it is ever shown as HTML.

        Post-checked by ``narrative_critic.review_narrative``: the rewrite only
        ships if every figure of ``base_summary`` survived it, otherwise the
        deterministic summary does. ``narrative_rewrite`` asks the model to
        "Keep every number" and verifies nothing, and it lives in the pinned
        external ``nosilo_core``, so the check belongs here -- see
        scm_agent/narrative_critic.py for the full rationale and the severity
        model (a filter, not a QA veto).
        """
        rewritten = narrative_rewrite(
            self.provider, base_summary, tool_title,
            lang=self.lang, citations=citations, persona=self.persona,
        )
        return review_narrative(base_summary, rewritten, label=tool_title).summary
