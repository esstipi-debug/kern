"""Merge SOP / document-control theory into the books graph as source #30.

El corpus no traia NADA de control documental ni de trabajo estandarizado: medido
el 2026-09-04 sobre los 3032 nodos committeados, ``standard work``, ``work
instruction``, ``job instruction``, ``job breakdown``, ``layered``, ``control
plan``, ``document control``, ``documented information``, ``leader standard`` y
``iso 9001`` dan CERO coincidencias de label, y no hay nodo de autor para Liker,
Mann, Imai, Rother, Ohno, Graupp ni AIAG. Es el caso exacto de la doctrina de
``scm_agent/packages.py``: "the corpus carries no ... theory at all. Fixed by
ingesting sources, not by a looser anchor."

QUE LO HACE DISTINTO DE LAS FUENTES #28 Y #29
---------------------------------------------
Aquellas ingieren un documento interno de Kern y quedan para siempre fuera de las
citas a cliente via ``scm_agent.knowledge._INTERNAL_SOURCES``. Esta fuente es
**externa y citable**, porque su proposito es justamente que ``procedure_draft``
pueda citar la teoria de su propio tema. Copiar aquel patron habria sido lo peor
de los dos mundos: los nodos igual consumen slots del pool (el filtro de fuentes
internas corre DESPUES de que ``search()`` rankee), pero nunca se podrian citar.

CADA NODO LLEVA SU PROPIA PROCEDENCIA
-------------------------------------
A diferencia de los merges anteriores, aca no hay un ``SOURCE_FILE`` unico: cada
concepto se verifico contra un artefacto distinto, y el ``source_file`` nombra
**el artefacto que se leyo**, no la obra que ese artefacto describe. Leer la
pagina de producto del CQI-8 no es haber leido el CQI-8, y el nombre del archivo
tiene que dejarlo ver, porque el cliente solo ve ``label - source_file
source_location``. El papel de trabajo esta en
``documentation/SOP_DOCUMENT_CONTROL_SOURCES.md``, que tambien registra los TRES
conceptos que se cayeron por no poder llenar la ficha (los seis procedimientos
del 2008, con dos citas que no existian en sus paginas; el estado FDIS de la
revision 2026, que caduca; y SDCA, con atribucion disputada).

SIN HUB
-------
Las fuentes #28 y #29 cuelgan todo de un ``HUB_ID`` con ``part_of``. Aca no, y
por una razon medida: con ``citation_gate.MAX_HOPS = 2``, un hub que toca los 10
conceptos deja CUALQUIER par a dos saltos, que es la self-validation loophole que
``EXCLUDED_CONCEPTS`` existe para tapar. Sin hub, cada concepto es citable solo
por su propia vecindad.

TODA ancla es DURABLE: las 5 pertenecen a ``heizer-render-operations-management.pdf``,
que no esta en ``scripts/merge_l3_rebuild.REPLACE_STEMS``, asi que un rebuild real
de las fuentes no borra el nodo ni la arista. Prohibidas como ancla:
``continuous_improvement`` (palamariu) y ``supplier_audit`` (grant), ambas en
REPLACE_STEMS; y ``procedure_model``, falso amigo documentado (el modelo de
reconfiguracion organizacional de Kuhn, no un procedimiento).

Re-correr esta prohibido por defecto: el merge es aditivo y una segunda pasada
duplicaria cada nodo y cada arista mientras la guardia de id-set seguiria diciendo
"purely additive". Restaurar el grafo previo
(``git checkout HEAD -- knowledge/scm-books/graph.json``) y re-correr, o pasar --force.

Usage:
    python scripts/merge_sop_document_control.py [--force] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.sopdc.bak"
SOURCE_DOC = REPO / "documentation" / "SOP_DOCUMENT_CONTROL_SOURCES.md"

REPO_TAG = "knowledge"

#: Comunidad propia, id fresco en vez de un re-cluster completo: re-correr Louvain
#: reasignaria miles de nodos e invalidaria toda medicion de citas previa.
#: 0..203 ocupadas (204 distintas) al 2026-09-04.
COMMUNITY = 204

#: Fecha de acceso de todo artefacto de esta fuente.
CAPTURED_AT = "2026-09-03"

#: Ningun nodo nuevo puede superar este grado. Un nodo de alto grado es un iman en
#: el cierre de 2 saltos, y es el mecanismo por el que las fuentes #26 y #27
#: dejaron en cero las citas de otras tools.
MAX_DEGREE = 4

# --- Artefactos realmente leidos -------------------------------------------------
# El sufijo del nombre dice QUE es cada uno. Ver el papel de trabajo para la ficha
# completa de cada artefacto (URL, ubicacion exacta, fragmento y tipo de evidencia).
ASQ_BOK = "asq-cssgb-body-of-knowledge-2022-map.pdf"
ASQ_BOK_URL = "https://www.asq.org/cert/resource/pdf/certification/2022-CSSGB-BoK-Map.pdf"

ISOTRACKER = "isotracker-iso-9001-document-review-frequency-blog.html"
ISOTRACKER_URL = "https://www.isotracker.com/blog/iso-9001-how-often-to-review-documents/"

BSI_10013 = "bsi-iso-10013-2021-catalogue-page.html"
BSI_10013_URL = (
    "https://knowledge.bsigroup.com/products/"
    "quality-management-systems-guidance-for-documented-information"
)

COMPLIANT = "compliant-ltd-controlled-documents-iso-9001-article.html"
COMPLIANT_URL = "https://compliantltd.com/insights/managing-controlled-documents-iso-9001/"

MANN_INTERVIEW = "leanblog-podcast-212-david-mann-interview-2014.html"
MANN_INTERVIEW_URL = (
    "https://www.leanblog.org/2014/12/podcast-212-david-mann-creating-a-lean-culture-3rd-ed/"
)

AIAG_CQI8 = "aiag-cqi-8-layered-process-audit-product-page.html"
AIAG_CQI8_URL = "https://www.aiag.org/store/publications/details?ProductCode=CQI-8"

TWI_JI = "bilas-group-twi-job-instruction-learning-center.html"
TWI_JI_URL = "https://www.thebilasgroup.com/twi-learning-center/job-instruction/"

#: Todo `source_file` que introduce esta fuente. Lo usa la guardia de idempotencia
#: y lo espeja tests/test_sop_document_control_graph.py contra el papel de trabajo.
SOURCE_FILES: frozenset[str] = frozenset(
    {ASQ_BOK, ISOTRACKER, BSI_10013, COMPLIANT, MANN_INTERVIEW, AIAG_CQI8, TWI_JI}
)

#: Obras que NADIE leyo. Un `source_file` con cualquiera de estos nombres seria una
#: sobre-afirmacion de procedencia; el test lo prohibe explicitamente.
FORBIDDEN_SOURCE_FILES: frozenset[str] = frozenset(
    {
        "iso-9001-2015.pdf",
        "iso-10013-2021.pdf",
        "aiag-cqi-8.pdf",
        "twi-job-instruction-manual.pdf",
        "mann-creating-a-lean-culture.pdf",
    }
)


def _node(
    node_id: str,
    label: str,
    *,
    file_type: str,
    source_file: str,
    source_url: str,
    location: str,
    author: str,
    rationale: str,
) -> dict:
    """Un nodo del grafo de libros. Misma forma que las fuentes #28/#29, pero con
    source_url / captured_at / author POBLADOS: son fuente externa citable y su
    procedencia tiene que poder auditarse sin depender del nombre de archivo."""
    return {
        "label": label,
        "file_type": file_type,
        "source_file": source_file,
        "source_location": location,
        "source_url": source_url,
        "captured_at": CAPTURED_AT,
        "author": author,
        "contributor": None,
        "id": node_id,
        "community": COMMUNITY,
        "norm_label": label.lower(),
        "repo": REPO_TAG,
        "rationale": rationale,
    }


def _edge(source: str, target: str, relation: str, source_file: str, location: str) -> dict:
    """Una arista EXTRACTED: cada una la afirma el artefacto citado."""
    return {
        "relation": relation,
        "confidence": "EXTRACTED",
        "confidence_score": 1.0,
        "source_file": source_file,
        "source_location": location,
        "weight": 1.0,
        "source": source,
        "target": target,
    }


def build_payload() -> tuple[list[dict], list[dict]]:
    """Los 12 nodos y sus aristas. Puro, sin I/O."""
    n = "knowledge::"
    nodes: list[dict] = [
        _node(
            f"{n}documented_information",
            "Documented Information (ISO 9001:2015 clause 7.5)",
            file_type="concept",
            source_file=ISOTRACKER,
            source_url=ISOTRACKER_URL,
            location="cuerpo del articulo",
            author="isoTracker",
            rationale=(
                "El termino paraguas de ISO 9001:2015 para los documentos y registros "
                "controlados de un sistema de gestion. La clausula 7.5.2(c) exige "
                "'review and approval for suitability and adequacy' ANTES de usar el "
                "documento: es el unico control de ciclo de vida que la norma obliga. "
                "PROCEDENCIA: el texto de la clausula se reconfirmo textual en tres "
                "fuentes practicantes independientes; ningun ente certificador (BSI, NQA, "
                "DNV, ASQ, iso.org) resulto alcanzable. Nadie leyo la norma."
            ),
        ),
        _node(
            f"{n}document_review_cadence",
            "Document Review Cadence (organization-determined, not ISO-fixed)",
            file_type="concept",
            source_file=ISOTRACKER,
            source_url=ISOTRACKER_URL,
            location="cuerpo del articulo",
            author="isoTracker",
            rationale=(
                "ISO 9001:2015 NO fija ningun intervalo de revision documental: la "
                "cadencia la determina la organizacion. Decir que 'la norma exige "
                "revision anual' es un error factico frecuente. Los 365 dias que usan "
                "las herramientas del oficio son CONVENCION (GDI, SweetProcess), no "
                "requisito. La ausencia se verifico por el mismo camino de tres fuentes "
                "practicantes independientes que confirma el texto de 7.5.2(c)."
            ),
        ),
        _node(
            f"{n}iso_10013_documented_information_guidance",
            "ISO 10013:2021 (Guidance for Documented Information)",
            file_type="concept",
            source_file=BSI_10013,
            source_url=BSI_10013_URL,
            location="alcance / abstract de la ficha de catalogo",
            author="BSI",
            rationale=(
                "La norma ISO escrita especificamente para guiar como desarrollar y "
                "mantener la informacion documentada de un sistema de gestion de calidad "
                "-- mas precisa que citar ISO 9001 a secas cuando la pregunta es como "
                "ESCRIBIR un procedimiento. Alcance verificado textual en la ficha de "
                "catalogo de BSI (ente certificador, miembro de ISO): 'guidance for the "
                "development and maintenance of the documented information...tailored to "
                "the specific needs of the organization'; publicada el 31-mar-2021. "
                "PROCEDENCIA: ficha de catalogo, no el texto de la norma; el indice a "
                "nivel de clausula no se leyo (iso.org bloqueado, texto completo pago)."
            ),
        ),
        _node(
            f"{n}document_owner_approver_segregation",
            "Document Owner vs Approver Segregation",
            file_type="concept",
            source_file=COMPLIANT,
            source_url=COMPLIANT_URL,
            location="cuerpo del articulo",
            author="Compliant Ltd",
            rationale=(
                "Quien redacta o mantiene un procedimiento no deberia ser tambien su "
                "unico aprobador; la aprobacion se liga a competencia, no a jerarquia. "
                "PROCEDENCIA Y LIMITE: es CONVENCION DE PRACTICA, no un requisito de "
                "clausula. Ambas citas se reconfirmaron textuales, pero la fuente es "
                "contenido de consultora y ningun ente certificador corroboro pese a "
                "mas de diez intentos. Nunca se verifico que ISO 9001 lo exija."
            ),
        ),
        _node(
            f"{n}control_plan",
            "Control Plan (Six Sigma Control phase)",
            file_type="concept",
            source_file=ASQ_BOK,
            source_url=ASQ_BOK_URL,
            location="Table 2, pp. 10-11",
            author="ASQ",
            rationale=(
                "Artefacto de la fase Control de DMAIC que liga cada paso del proceso a "
                "una caracteristica o KPI, una especificacion, un metodo de medicion, una "
                "frecuencia, un metodo de control y un plan de reaccion con su dueno. "
                "Verificado textual en el PDF propio de ASQ: la fase Control enumera "
                "control plan, document control, training plans, audits y PDCA. "
                "NO SE INGIERE la lista de campos de IATF 16949 clausula 8.5.1.1: solo se "
                "corroboro en un blog de terceros, nunca contra el texto pago de la norma."
            ),
        ),
        _node(
            f"{n}reaction_plan",
            "Reaction Plan (control plan field)",
            file_type="concept",
            source_file=ASQ_BOK,
            source_url=ASQ_BOK_URL,
            location="Table 2, pp. 10-11",
            author="ASQ",
            rationale=(
                "La respuesta nombrada -- quien, que, cuando, donde -- que se dispara "
                "cuando un paso monitoreado se desvia de su especificacion. Es un campo "
                "del control plan, y es el que un procedimiento casero practicamente "
                "nunca escribe: sin el, un SOP dice como se hace la tarea pero no que "
                "hacer cuando sale mal."
            ),
        ),
        _node(
            f"{n}leader_standard_work",
            "Leader Standard Work",
            file_type="concept",
            source_file=MANN_INTERVIEW,
            source_url=MANN_INTERVIEW_URL,
            location="entrevista grabada, Lean Blog podcast 212 (2014)",
            author="David Mann",
            rationale=(
                "El checklist escrito del propio lider sobre que verificar y cada cuanto, "
                "aplicado capa por capa: convierte gestionar el estandar en un estandar. "
                "Verificado textual en dos entrevistas grabadas al autor de 'Creating a "
                "Lean Culture' (2006 y 2014): la estructura de chequeo por capas, y "
                "'initial the hour-by-hour chart at least four times a day' -- una firma "
                "fisica que sirve de prueba de cadencia sin ningun software. "
                "PROCEDENCIA: testimonial (el autor en su propia voz); el libro no se leyo. "
                "Ver el nodo de contradiccion sobre el reparto de tiempo."
            ),
        ),
        _node(
            f"{n}layered_process_audit",
            "Layered Process Audit (LPA)",
            file_type="concept",
            source_file=AIAG_CQI8,
            source_url=AIAG_CQI8_URL,
            location="descripcion de producto, aiag.org",
            author="AIAG",
            rationale=(
                "Calendario de auditoria en varias capas de gestion que chequea el APEGO "
                "al estandar escrito, no la calidad del resultado. La pagina de producto "
                "de AIAG confirma que CQI-8 'Recommends Roles and Responsibilities for LPA "
                "(RASIC chart)' y 'Expands ownership beyond the Quality function': los "
                "roles se asignan por matriz RASIC, no por puestos fijos. El patron de "
                "auditoria diaria en primera linea se corroboro en cuatro fuentes "
                "independientes, incluidos dos SOP reales de proveedores automotrices. "
                "PROCEDENCIA: pagina de producto; el CQI-8 es pago y no se leyo. "
                "Ver el nodo de contradiccion sobre la cadencia del nivel superior."
            ),
        ),
        _node(
            f"{n}job_breakdown_sheet",
            "Job Breakdown Sheet (TWI Job Instruction)",
            file_type="concept",
            source_file=TWI_JI,
            source_url=TWI_JI_URL,
            location="seccion Job Instruction del centro de aprendizaje TWI",
            author="The Bilas Group",
            rationale=(
                "Hoja que descompone una tarea en Pasos Importantes, Puntos Clave y la "
                "Razon de cada punto clave -- la estructura del metodo Job Instruction de "
                "TWI, y el formato que da a un procedimiento el 'por que' que un listado "
                "de pasos no tiene. Verificado textual, junto con su paralelo en la 'work "
                "element sheet' de Toyota. PROCEDENCIA: proveedor de formacion TWI; la "
                "pagina del propio TWI Institute quedo inalcanzable por fallo SSL."
            ),
        ),
        _node(
            f"{n}ji_training_timetable",
            "JI Training Timetable",
            file_type="concept",
            source_file=TWI_JI,
            source_url=TWI_JI_URL,
            location="seccion Job Instruction del centro de aprendizaje TWI",
            author="The Bilas Group",
            rationale=(
                "Registro que liga una persona nombrada con una tarea nombrada y una fecha "
                "objetivo, segun su nivel de habilidad actual. CORRECCION EXPLICITA: no es "
                "un vinculo version-a-entrenamiento. Rastrea QUIEN fue entrenado en QUE "
                "tarea y PARA CUANDO -- nunca un numero de version de documento. Sirve "
                "para probar competencia, no control documental."
            ),
        ),
        _node(
            f"{n}layered_process_audit_cadence_dispute",
            "Layered Process Audit: top-tier cadence is disputed",
            file_type="contradiction",
            source_file=AIAG_CQI8,
            source_url=AIAG_CQI8_URL,
            location="comparacion de 4 fuentes; ver documentation/SOP_DOCUMENT_CONTROL_SOURCES.md",
            author="AIAG",
            rationale=(
                "CUATRO fuentes reales dan CUATRO cadencias distintas para el nivel "
                "superior de una LPA, y CQI-8 no la fija: (1) Nexteer Automotive, SOP "
                "propio: auditorias de planta y gerencia general SEMANALES; (2) PSA "
                "Groupe, material de formacion propio: cuatro niveles diario / semanal / "
                "mensual / trimestral, con un nivel de CEO; (3) y (4) dos blogs de "
                "vendedores de software: MENSUAL. Lo unico corroborado en las cuatro es la "
                "auditoria DIARIA de primera linea. Conclusion citable: la cadencia del "
                "nivel superior la fija cada organizacion; quien afirme un numero unico "
                "esta citando a un vendedor, no a la guia."
            ),
        ),
        _node(
            f"{n}leader_standard_work_time_allocation_dispute",
            "Leader Standard Work: the time-allocation figures are disputed",
            file_type="contradiction",
            source_file=MANN_INTERVIEW,
            source_url=MANN_INTERVIEW_URL,
            location="entrevista 2006 contra resena de IISE del mismo libro",
            author="David Mann",
            rationale=(
                "Cuanto del tiempo de un lider debe cubrir su standard work tiene DOS "
                "respuestas incompatibles sobre el MISMO libro: la entrevista de 2006 al "
                "autor dice supervisor 50% y value-stream manager 25%; una resena de IISE "
                "de 'Creating a Lean Culture' dice 60% y 40%. Ninguna de las dos debe "
                "citarse como zanjada sin la pagina del libro en la mano. El mecanismo "
                "(estructura por capas, firma hora por hora) si esta confirmado."
            ),
        ),
    ]

    edges: list[dict] = [
        # Control documental -> ISO 9000 (Heizer & Render, durable)
        _edge(f"{n}documented_information", f"{n}iso_9000", "conceptually_related_to", ISOTRACKER, "cuerpo del articulo"),
        _edge(f"{n}iso_10013_documented_information_guidance", f"{n}iso_9000", "conceptually_related_to", BSI_10013, "alcance"),
        _edge(f"{n}document_owner_approver_segregation", f"{n}iso_9000", "conceptually_related_to", COMPLIANT, "cuerpo del articulo"),
        # Cadencia -> PDCA (el lazo de revision)
        _edge(f"{n}document_review_cadence", f"{n}pdca_cycle", "conceptually_related_to", ISOTRACKER, "cuerpo del articulo"),
        _edge(f"{n}document_review_cadence", f"{n}documented_information", "conceptually_related_to", ISOTRACKER, "cuerpo del articulo"),
        # Control plan -> control chart + poka-yoke (que mide y que previene)
        _edge(f"{n}control_plan", f"{n}control_chart", "conceptually_related_to", ASQ_BOK, "Table 2, pp. 10-11"),
        _edge(f"{n}control_plan", f"{n}poka_yoke", "conceptually_related_to", ASQ_BOK, "Table 2, pp. 10-11"),
        _edge(f"{n}reaction_plan", f"{n}poka_yoke", "conceptually_related_to", ASQ_BOK, "Table 2, pp. 10-11"),
        _edge(f"{n}reaction_plan", f"{n}control_plan", "part_of", ASQ_BOK, "Table 2, pp. 10-11"),
        # Trabajo estandar del lider -> kaizen
        _edge(f"{n}leader_standard_work", f"{n}kaizen", "conceptually_related_to", MANN_INTERVIEW, "entrevista 2014"),
        # Auditoria por capas -> TQM
        _edge(f"{n}layered_process_audit", f"{n}total_quality_management", "conceptually_related_to", AIAG_CQI8, "descripcion de producto"),
        # TWI -> checklist
        _edge(f"{n}job_breakdown_sheet", f"{n}checklist", "conceptually_related_to", TWI_JI, "seccion Job Instruction"),
        _edge(f"{n}ji_training_timetable", f"{n}checklist", "conceptually_related_to", TWI_JI, "seccion Job Instruction"),
        _edge(f"{n}ji_training_timetable", f"{n}job_breakdown_sheet", "conceptually_related_to", TWI_JI, "seccion Job Instruction"),
        # Contradicciones -> lo que contradicen
        _edge(f"{n}layered_process_audit_cadence_dispute", f"{n}layered_process_audit", "references", AIAG_CQI8, "comparacion de 4 fuentes"),
        _edge(f"{n}leader_standard_work_time_allocation_dispute", f"{n}leader_standard_work", "references", MANN_INTERVIEW, "entrevista 2006 vs resena IISE"),
    ]
    return nodes, edges


def edge_key(source: str, target: str, relation: str | None) -> tuple:
    """Clave de arista para la guardia de aristas paralelas: el grafo declara
    multigraph=false, asi que dos links con los mismos extremos y la misma relacion
    colapsan silenciosamente al cargar."""
    return (frozenset((source, target)), relation)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-apply even if already merged")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    if not SOURCE_DOC.is_file():
        print(f"missing provenance document {SOURCE_DOC}", file=sys.stderr)
        return 1
    if not COMMITTED.is_file():
        print(f"missing {COMMITTED}", file=sys.stderr)
        return 1

    graph = json.loads(COMMITTED.read_text(encoding="utf-8"))
    nodes: list[dict] = graph["nodes"]
    links: list[dict] = graph.setdefault("links", graph.get("edges", []))
    committed_ids = {n["id"] for n in nodes if "id" in n}

    already = [n for n in nodes if n.get("source_file") in SOURCE_FILES]
    if already and not args.force:
        print(
            f"refusing: {len(already)} nodes from this source are already in the graph.\n"
            "  git checkout HEAD -- knowledge/scm-books/graph.json  (then re-run)\n"
            "  or pass --force if you genuinely intend to re-apply.",
            file=sys.stderr,
        )
        return 1

    new_nodes, new_edges = build_payload()
    new_ids = {n["id"] for n in new_nodes}

    # Guardia 1: ancla colgante. Una ancla que no existe es el modo de falla que
    # este ejercicio entero existe para prevenir (inventar un nodo de teoria), asi
    # que es un error duro, nunca un warning.
    dangling = sorted({e["target"] for e in new_edges if e["target"] not in committed_ids | new_ids})
    if dangling:
        print(f"refusing: theory anchors not found in the graph: {dangling}", file=sys.stderr)
        return 1

    # Guardia 2: colision de ids.
    collisions = sorted(committed_ids & new_ids)
    if collisions and not args.force:
        print(f"refusing: node id collision with the committed graph: {collisions}", file=sys.stderr)
        return 1

    # Guardia 3: procedencia completa. Es la que hace cumplir "nunca inventar
    # procedencia" en el codigo y no solo en la revision.
    incomplete = sorted(
        n["id"]
        for n in new_nodes
        if not all(n.get(f) for f in ("source_file", "source_location", "source_url", "captured_at", "author"))
    )
    if incomplete:
        print(f"refusing: nodes without complete provenance: {incomplete}", file=sys.stderr)
        return 1

    overclaimed = sorted({n["source_file"] for n in new_nodes} & FORBIDDEN_SOURCE_FILES)
    if overclaimed:
        print(f"refusing: source_file names a work nobody read: {overclaimed}", file=sys.stderr)
        return 1

    # Guardia 4: aristas paralelas. El grafo declara multigraph=false.
    merged_links = links + new_edges
    keys = [edge_key(e["source"], e["target"], e.get("relation")) for e in merged_links]
    dupes = len(keys) - len(set(keys))
    if dupes:
        print(f"refusing: {dupes} duplicate (endpoints, relation) links would collapse on load", file=sys.stderr)
        return 1

    # Guardia 5: iman. Un nodo de alto grado pone en radio de 2 saltos a todo lo que
    # toca, y es el mecanismo por el que las fuentes #26 y #27 zeroearon otras tools.
    degree: dict[str, int] = {}
    for e in new_edges:
        for endpoint in (e["source"], e["target"]):
            if endpoint in new_ids:
                degree[endpoint] = degree.get(endpoint, 0) + 1
    magnets = sorted(k for k, v in degree.items() if v > MAX_DEGREE)
    if magnets:
        print(f"refusing: new nodes exceed MAX_DEGREE={MAX_DEGREE}: {magnets}", file=sys.stderr)
        return 1

    # Guardia 6: id-set. El merge es puramente aditivo; un rename silencioso es la
    # leccion de la fuente #25, donde los conteos solos lo escondieron.
    merged_ids = committed_ids | new_ids
    lost = committed_ids - merged_ids
    if lost:
        print(f"refusing: pre-existing ids lost in merge: {sorted(lost)[:20]}", file=sys.stderr)
        return 1

    print(
        f"nodes {len(nodes)} -> {len(nodes) + len(new_nodes)}  "
        f"links {len(links)} -> {len(merged_links)}  "
        f"(+{len(new_nodes)} nodes, +{len(new_edges)} edges, community {COMMUNITY})"
    )
    print(f"  {len(SOURCE_FILES)} artefactos distintos, cada nodo con su propia procedencia")
    print(f"  grado maximo de un nodo nuevo: {max(degree.values()) if degree else 0} (tope {MAX_DEGREE})")
    print("  OK - ancla colgante, colision, procedencia, aristas paralelas, iman e id-set")
    if args.dry_run:
        print("dry run - nothing written")
        return 0

    shutil.copy2(COMMITTED, BACKUP)
    graph["nodes"] = nodes + new_nodes
    graph["links"] = merged_links

    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(COMMITTED.parent), prefix=".merge_sopdc_")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, COMMITTED)
    print(f"merged into {COMMITTED.relative_to(REPO)} (backup at {BACKUP.relative_to(REPO)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
