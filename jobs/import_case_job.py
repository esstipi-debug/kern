"""Import-case job: a folder (or JSON bundle) of extracted docs -> unified pack.

Reads structured extractions (JSON), not PDFs — the voice doc-reader can fill
those JSON files first. Pandas is not used: the input is a case graph, not a
demand table. QA checks the report's internal consistency, never the
completeness of the client's paperwork: an incomplete case IS the deliverable.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.import_case import (
    REGIME_CHILE_IMPORT,
    DocumentSlice,
    ImportCase,
    merge_case,
)
from src.voice.doc_schemas import DOC_TYPES


def _load_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def _slices_from_bundle(data: dict, *, default_source: str) -> list[DocumentSlice]:
    regime_docs = data.get("documents")
    if not isinstance(regime_docs, list):
        raise ValueError("JSON bundle needs a 'documents' list")
    slices = []
    for i, item in enumerate(regime_docs):
        if not isinstance(item, dict):
            raise ValueError(f"documents[{i}] is not an object")
        doc_type = str(item.get("doc_type") or "")
        fields = item.get("fields")
        if not doc_type or not isinstance(fields, dict):
            raise ValueError(f"documents[{i}] needs doc_type and fields")
        src = str(item.get("source") or default_source)
        slices.append(DocumentSlice(doc_type, dict(fields), src))
    return slices


def _slices_from_folder(folder: Path) -> tuple[list[DocumentSlice], str]:
    bundle = folder / "documents.json"
    if bundle.is_file():
        data = _load_json(bundle)
        if not isinstance(data, dict):
            raise ValueError("documents.json must be an object")
        regime = str(data.get("regime") or REGIME_CHILE_IMPORT)
        return _slices_from_bundle(data, default_source=str(bundle)), regime

    slices: list[DocumentSlice] = []
    for path in sorted(folder.glob("*.json")):
        stem = path.stem
        data = _load_json(path)
        if not isinstance(data, dict):
            continue
        if "documents" in data:
            slices.extend(_slices_from_bundle(data, default_source=str(path)))
            continue
        doc_type = str(data.get("doc_type") or stem)
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else data
        fields = {k: v for k, v in fields.items() if k not in ("doc_type", "source")}
        slices.append(DocumentSlice(doc_type, fields, str(path)))
    return slices, REGIME_CHILE_IMPORT


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Load extracted documents from a folder or a documents.json file."""
    params = params or {}
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if path.is_file():
        data = _load_json(path)
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object with a documents list")
        regime = str(params.get("regime") or data.get("regime") or REGIME_CHILE_IMPORT)
        slices = _slices_from_bundle(data, default_source=str(path))
    else:
        slices, folder_regime = _slices_from_folder(path)
        regime = str(params.get("regime") or folder_regime)
    if not slices:
        raise ValueError("no extracted documents found (need documents.json or per-type JSON)")
    unknown = sorted({s.doc_type for s in slices if s.doc_type not in DOC_TYPES})
    return {
        "regime": regime,
        "slices": [
            {"doc_type": s.doc_type, "fields": s.fields, "source": s.source}
            for s in slices
        ],
        "unknown_types": unknown,
    }


def run(payload: object, params: dict | None = None) -> ImportCase:
    params = params or {}
    data = payload if isinstance(payload, dict) else {}
    regime = str(params.get("regime") or data.get("regime") or REGIME_CHILE_IMPORT)
    slices = [
        DocumentSlice(str(row["doc_type"]), dict(row["fields"]), str(row.get("source") or ""))
        for row in data.get("slices") or []
    ]
    return merge_case(slices, regime=regime)


def verify(report: ImportCase) -> list[str]:
    """QA: the report has to describe itself honestly.

    Deliberately NOT a completeness gate. A QA issue wipes every deliverable
    (``scm_agent/orchestrator.py``), and a case missing its B/L is exactly when
    the client needs ``missing_items.csv`` and a pack marked contested. Gaps
    travel inside the deliverable; only a self-contradicting report fails QA.
    """
    issues: list[str] = []
    if report.n_documents < 1:
        issues.append("no documents in the import case")
    if report.n_documents != len(report.documents):
        issues.append(
            f"n_documents says {report.n_documents}, case holds {len(report.documents)} document(s)",
        )
    if report.n_conflicts != len(report.conflicts):
        issues.append(
            f"n_conflicts says {report.n_conflicts}, case holds {len(report.conflicts)} conflict(s)",
        )
    if report.n_missing != len(report.missing):
        issues.append(
            f"n_missing says {report.n_missing}, case holds {len(report.missing)} missing item(s)",
        )
    if not 0.0 <= report.copy_reduction_pct <= 100.0:
        issues.append(f"copy_reduction_pct out of range: {report.copy_reduction_pct}")

    # The contested flag is the only conflict signal the broker sees on the pack
    # CSV, so a conflict that does not reach its row is a silent clean number.
    contested = {row.canonical for row in report.pack if row.contested}
    packed = {row.canonical for row in report.pack}
    conflicted = {c.field for c in report.conflicts}
    for conflict in report.conflicts:
        if conflict.field in packed and conflict.field not in contested:
            issues.append(f"conflict on '{conflict.field}' is not marked contested in the pack")
        if not conflict.reason.strip():
            issues.append(f"conflict on '{conflict.field}' carries no reason")
    for row in report.pack:
        if row.value and not row.sources:
            issues.append(f"pack row '{row.canonical}' has a value with no source document")
        if row.contested and row.canonical not in conflicted:
            issues.append(f"pack row '{row.canonical}' is marked contested without a conflict")
    return issues


def write_operational(report: ImportCase, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    pack_rows = [
        {
            "label": row.label,
            "canonical": row.canonical,
            "value": row.value,
            "sources": ";".join(row.sources),
            "contested": "yes" if row.contested else "no",
        }
        for row in report.pack
    ]
    conflict_rows = [
        {
            "field": c.field,
            "values": " | ".join(f"{src}={val}" for src, val in c.values),
            "reason": c.reason,
        }
        for c in report.conflicts
    ]
    missing_rows = [
        {"kind": m.kind, "name": m.name, "detail": m.detail}
        for m in report.missing
    ]
    case_json = {
        "client": client,
        "regime": report.regime,
        "summary": report.summary,
        "unified": report.unified,
        "copy_reduction_pct": report.copy_reduction_pct,
        "n_conflicts": report.n_conflicts,
        "n_missing": report.n_missing,
        "complete_enough": report.complete_enough,
        "disclaimer": (
            "Field pack is a draft for the broker/importer. Not a filed customs "
            "declaration. HS codes are proposed, not legal advice."
        ),
    }
    json_path = d / "import_case.json"
    json_path.write_text(json.dumps(case_json, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    out = {
        "json": json_path,
        "pack_csv": write_summary_csv(pack_rows, d / "chile_din_field_pack.csv"),
    }
    if conflict_rows:
        out["conflicts_csv"] = write_summary_csv(conflict_rows, d / "document_conflicts.csv")
    if missing_rows:
        out["missing_csv"] = write_summary_csv(missing_rows, d / "missing_items.csv")
    return out


#: Un expediente completo y sin disputas: los seis documentos presentes o
#: proyectados y ninguna fila contestada. Nunca 1.0 -- el pack sigue siendo una
#: proyeccion para el despachante, no una DIN presentada.
_DECK_BASE_CONFIDENCE = 0.80
#: Escala con la porcion del set de seis que falta (``copy_reduction_pct``).
_MISSING_DOC_PENALTY = 0.25
#: El conflicto se castiga en dos partes porque es un fenomeno de conteo chico
#: (~16 campos), no de volumen: puramente proporcional, UN peso peleado entre
#: packing y B/L movia la confianza 0,01 -- y ese es exactamente el hecho que
#: impide presentar. El escalon dice "hay algo sin resolver"; la cola dice
#: "cuanto". El denominador de la cola son las filas CON VALOR, no las 16
#: ranuras del pack: una ranura vacia no es un hecho en disputa y ya la castiga
#: _MISSING_DOC_PENALTY.
_CONFLICT_BASE_PENALTY = 0.12
_CONFLICT_SHARE_PENALTY = 0.13
_CONFIDENCE_FLOOR = 0.10


def deck_confidence(report: ImportCase) -> float:
    """La confianza del deck, derivada de lo que este expediente pudo reunir.

    ``verify()`` deliberadamente NO es una puerta de completitud: un caso al que
    le falta el B/L es exactamente cuando el cliente necesita el pack. Pero ese
    hueco tiene que verse en algun lado, y la confianza es ese lugar -- un
    literal en la firma decia lo mismo para un expediente de seis documentos sin
    disputas que para uno de dos con la factura y el packing peleados. Espejo de
    ``jobs/meli_presale_job.py::deck_confidence``.
    """
    confianza = _DECK_BASE_CONFIDENCE
    confianza -= _MISSING_DOC_PENALTY * (1.0 - report.copy_reduction_pct / 100.0)
    populated = [row for row in report.pack if row.value or row.contested]
    contested = sum(1 for row in populated if row.contested)
    if contested:
        confianza -= _CONFLICT_BASE_PENALTY
        confianza -= _CONFLICT_SHARE_PENALTY * (contested / len(populated))
    return max(_CONFIDENCE_FLOOR, round(confianza, 4))


def build_deck(
    report: ImportCase,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
    lang: str = "en",
) -> Deliverable:
    if confidence is None:
        confidence = deck_confidence(report)
    findings = [
        Finding(
            "One case, many projections",
            report.summary,
            impact="stop re-typing the same shipment into six forms; edit the case once",
        ),
    ]
    if report.conflicts:
        names = ", ".join(sorted({c.field for c in report.conflicts}))
        why = " ".join(sorted({c.reason for c in report.conflicts if c.reason}))
        findings.append(Finding(
            "Document conflicts need a human pick",
            f"Contested fields: {names}. Pack rows stay marked contested. {why}".strip(),
            impact="choose packing vs invoice before the broker files",
        ))
    if report.missing:
        docs = [m.name for m in report.missing if m.kind == "document"]
        fields = [m.name for m in report.missing if m.kind == "field"]
        findings.append(Finding(
            "Gaps in the pack",
            "Missing documents: " + (", ".join(docs) if docs else "none")
            + "; missing required fields: " + (", ".join(fields) if fields else "none") + ".",
            impact="do not present; complete the case first",
        ))
    findings.append(Finding(
        "Not a customs filing",
        "The Chile DIN CSV is a field map for the licensed broker. Kern does not submit.",
        impact="handoff the pack; keep legal responsibility with the importer/broker",
    ))

    kpis = (
        Kpi("Documents ingested", str(report.n_documents), rationale="Extracted slices in the case"),
        Kpi(
            "Conflicts", str(report.n_conflicts), target="0",
            rationale="Facts the documents cannot settle: disagreement, ambiguous "
            "number format, or an identifier that fails its own check digit",
        ),
        Kpi("Missing items", str(report.n_missing), target="0", rationale="Required docs/fields still empty"),
        Kpi(
            "Copy-reduction (est.)",
            f"{report.copy_reduction_pct:.0f}%",
            rationale="Share of the six-doc set present or projected; not clearance automation",
        ),
    )
    return Deliverable(
        title="Import Case / Document Pack",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=(
            DataSource("Extracted logistics documents (JSON)", "invoice / packing / B/L / AWB", "per shipment"),
            DataSource("Chile DIN field map", "projection of the case, not SICEX", "per run"),
        ),
        recommendations=(
            "Send chile_din_field_pack.csv to the licensed broker; do not treat it as filed.",
            "Resolve contested rows (packing weight vs invoice value) before presentation.",
            "Run landed_cost on the same shipment lines for the money view.",
        ),
        citations=tuple(citations),
        confidence=confidence,
        lang=lang,
    )
