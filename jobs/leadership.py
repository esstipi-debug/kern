"""CHAIN leadership playbook — qualitative capability.

Deterministic port of the `liderazgo-chain` skill's scoring core: five
dimensions (Colaborativo, Holístico, Adaptable, Influyente, Narrativo), each
scored 0–4, yielding an archetype and a single priority lever. Radar chart,
written report and active directives live alongside (see Task 5 additions).

Síntesis original inspirada en el modelo CHAIN de "From Source to Sold"
(Palamariu & Alicke, 2022); no reproduce el texto del libro.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DIMS: list[tuple[str, str]] = [
    ("C", "Colaborativo"),
    ("H", "Holístico"),
    ("A", "Adaptable"),
    ("I", "Influyente"),
    ("N", "Narrativo"),
]
LEVELS: dict[int, str] = {0: "Ausente", 1: "Incipiente", 2: "Funcional", 3: "Sólido", 4: "Distintivo"}
_CODES = [code for code, _ in DIMS]
_NAME = dict(DIMS)
_IMPACT_ORDER = ["I", "N", "A", "H", "C"]

PRACTICES: dict[str, list[str]] = {
    "C": [
        "Mapa de la sala antes de decidir. Antes de cerrar una decisión de peso, listá quién se "
        "ve afectado aguas arriba y abajo, y meté a uno o dos en la conversación antes de decidir.",
        "Disenso explícito. En reuniones pedí activamente la objeción: \"¿qué estoy sin ver acá?\" "
        "y esperá en silencio. Si nadie te contradice nunca, no es que tengas razón siempre.",
        "Banco de favores con proveedores. Construí relación fuera de la negociación: una llamada "
        "cuando no necesitás nada. El día que necesites flexibilidad, ese banco existe o no.",
    ],
    "H": [
        "Recorrido end-to-end. Una vez por trimestre seguí un pedido del origen al cliente final y "
        "anotá cada handoff. Donde hay un traspaso hay un punto ciego potencial.",
        "Dieta de aprendizaje fuera del silo. Reservá tiempo fijo para una disciplina ajena a tu "
        "expertise. El objetivo es ampliar el campo de visión, no volverte experto.",
        "Contratar diferente a propósito. En la próxima incorporación buscá a alguien que no piense "
        "como vos. Un equipo de clones tiene un solo punto ciego, compartido.",
    ],
    "A": [
        "Cacería de fragilidad. En régimen normal buscá los single points of failure: un proveedor "
        "crítico único, un cuello sin alternativa. Listalos y asigná un plan B a los tres peores.",
        "Pre-mortem. Antes de un plan importante imaginá que ya fracasó y escribí por qué. Mata "
        "supuestos optimistas antes de que cuesten caro.",
        "Aprendizaje al sistema, no a la anécdota. Después de cada crisis preguntá: ¿qué cambia en "
        "el sistema para que esto no nos agarre igual la próxima? \"Estaremos más atentos\" no sirve.",
    ],
    "I": [
        "El \"por qué\" antes que el \"qué\". Cada vez que asignes algo, agregá por qué importa y "
        "cómo encaja en algo mayor. Quien entiende el propósito resuelve los casos borde solo.",
        "Traducir hacia arriba. Antes de presentar a dirección eliminá la jerga y buscá una analogía "
        "o una historia. El board no compra planillas; compra implicancias que entiende.",
        "Delegar marco, no pasos. Definí el resultado y los límites, y dejá libre la ejecución. Si "
        "dictás el cómo, micromanageás; si desaparecés, abandonaste. El punto está en el medio.",
    ],
    "N": [
        "La visión en una frase. Escribí hacia dónde va tu área y por qué le importaría a alguien de "
        "afuera, en una sola frase. Si no te sale, tu equipo tampoco la tiene. Refinala hasta repetible.",
        "Test del eco. Una buena narrativa la repiten otros cuando no estás. Preguntale a alguien del "
        "equipo hacia dónde va el área: si se parece a la tuya, el relato prendió; si no, es solo tuyo.",
        "De número a sentido. Cuando comuniques un resultado, conectalo con la misión del negocio. "
        "\"Bajamos el lead time 12%\" es un dato; \"le llegamos al cliente antes que nadie\" es historia.",
    ],
}

QUESTIONS: dict[str, list[str]] = {
    "C": [
        "¿Quién más estuvo en la sala antes de tu última decisión importante?",
        "¿Cuándo fue la última vez que tu equipo te hizo cambiar de opinión?",
        "¿A qué proveedor podrías llamar hoy a pedirle un favor fuera de contrato?",
    ],
    "H": [
        "Si bajás el costo de tu área 10%, ¿qué se rompe aguas abajo?",
        "¿Qué disciplina fuera de tu expertise estudiaste este año?",
        "¿En qué se diferencia de vos la última persona que contrataste?",
    ],
    "A": [
        "¿Cuál es tu single point of failure hoy y qué plan B tenés?",
        "¿Qué aprendiste de la última disrupción que ya esté incorporado al sistema?",
        "¿Qué te quita el sueño que todavía no pasó?",
    ],
    "I": [
        "¿Tu equipo sabe explicar por qué importa lo que hace?",
        "Tu última presentación a dirección, ¿planilla o historia?",
        "¿Cuándo conseguiste que arriba dijera que sí a algo de supply chain que no querían?",
    ],
    "N": [
        "En una frase, ¿hacia dónde va tu área y por qué le importaría a alguien?",
        "¿Tu gente puede contar esa historia sin vos?",
        "¿Cómo conectás lo de hoy con algo más grande que el número del mes?",
    ],
}


def coerce_scores(value: object) -> dict[str, int] | None:
    """Parse 5 ints (0..4) from a list/tuple or a space/comma-separated string."""
    if value is None:
        return None
    if isinstance(value, str):
        parts: list = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return None
    if len(parts) != 5:
        return None
    try:
        nums = [int(x) for x in parts]
    except (TypeError, ValueError):
        return None
    if not all(0 <= n <= 4 for n in nums):
        return None
    return {code: n for code, n in zip(_CODES, nums)}


def _validate(scores: dict[str, int]) -> None:
    if set(scores) != set(_CODES):
        raise ValueError(f"scores must have exactly the dims {_CODES}, got {sorted(scores)}")
    for code in _CODES:
        v = scores[code]
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 4:
            raise ValueError(f"{code} must be an int in 0..4, got {v!r}")


def archetype(scores: dict[str, int]) -> tuple[str, str]:
    """(name, description). Rules in priority order — ports the skill's score.py."""
    C, H, A, I, N = scores["C"], scores["H"], scores["A"], scores["I"], scores["N"]  # noqa: E741

    if all(v >= 3 for v in scores.values()):
        return (
            "Líder integral",
            "Las cinco dimensiones sólidas o más. Perfil listo para roles de mayor "
            "alcance; el foco pasa de cubrir huecos a profundizar fortalezas.",
        )
    if all(v <= 1 for v in scores.values()):
        return (
            "En formación",
            "Falta base transversal. No repartir el esfuerzo: elegí UNA dimensión y "
            "construí consistencia ahí antes de abrir frentes.",
        )
    if I <= 1 and N <= 1 and min(C, H, A) >= 2:
        return (
            "Operador invisible",
            "Hace que todo funcione, pero no se ve ni inspira. Es el patrón exacto que "
            "frena el salto a director/CEO: competencia real, sin influencia ni relato.",
        )
    if A <= 1 and min(C, H, I, N) >= 2:
        return (
            "Optimizador frágil",
            "Excelente en régimen estable, expuesto en la próxima disrupción. El riesgo "
            "no se ve hasta que algo se rompe.",
        )
    if H <= 1 and min(C, A, I, N) >= 2:
        return (
            "Especialista de silo",
            "Fuerte en su función, ciego al end-to-end. Optimiza su tramo sin ver el "
            "costo aguas abajo.",
        )
    if C <= 1 and min(H, A, I, N) >= 2:
        return (
            "Llanero solitario",
            "Resuelve solo. Capaz, pero no construye red ni confianza, así que no "
            "escala más allá de lo que toca con sus manos.",
        )
    minimo = min(scores.values())
    flojas = [name for code, name in DIMS if scores[code] == minimo]
    return (
        "Perfil mixto",
        f"Sin un patrón único. La(s) dimensión(es) de menor desarrollo: "
        f"{', '.join(flojas)}. Priorizá la de mayor retorno en tu contexto.",
    )


def priority_lever(scores: dict[str, int]) -> tuple[str, str, int]:
    """The weakest, highest-return dimension: lowest score; ties broken by the
    career-jump impact order I, N, A, H, C."""
    minimo = min(scores.values())
    code = next(c for c in _IMPACT_ORDER if scores[c] == minimo)
    return code, _NAME[code], minimo


@dataclass(frozen=True)
class ChainProfile:
    scores: dict[str, int]
    evidence: dict[str, str]
    name: str | None
    average: float
    gap: int
    archetype: str
    archetype_desc: str
    lever_code: str
    lever_name: str
    lever_level: int
    directives: list[str] = field(default_factory=list)


def score_profile(
    scores: dict[str, int],
    *,
    evidence: dict[str, str] | None = None,
    name: str | None = None,
) -> ChainProfile:
    """Build a full CHAIN profile from validated scores."""
    _validate(scores)
    ev = {code: (evidence or {}).get(code, "") for code in _CODES}
    average = sum(scores.values()) / len(scores)
    gap = max(scores.values()) - min(scores.values())
    arch_name, arch_desc = archetype(scores)
    lever_code, lever_name, lever_level = priority_lever(scores)
    directives = list(PRACTICES.get(lever_code, []))
    return ChainProfile(
        scores=dict(scores),
        evidence=ev,
        name=name,
        average=average,
        gap=gap,
        archetype=arch_name,
        archetype_desc=arch_desc,
        lever_code=lever_code,
        lever_name=lever_name,
        lever_level=lever_level,
        directives=directives,
    )


def diagnostic_questions() -> list[str]:
    """Flat list of the diagnostic questions, prefixed by dimension — what the
    orchestrator returns when there isn't enough evidence to score (Mode A)."""
    out: list[str] = []
    for code, name in DIMS:
        for q in QUESTIONS[code]:
            out.append(f"[{code} · {name}] {q}")
    return out


def radar_chart(profile: ChainProfile, path: str | Path) -> Path:
    """Write a 5-axis radar PNG of the CHAIN profile (matplotlib, headless Agg)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    labels = [name for _, name in DIMS]
    values = [profile.scores[code] for code, _ in DIMS]
    angles = np.linspace(0, 2 * np.pi, len(labels), endpoint=False).tolist()
    values_closed = values + values[:1]
    angles_closed = angles + angles[:1]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={"polar": True})
    ax.plot(angles_closed, values_closed, color="#1F2A44", linewidth=2)
    ax.fill(angles_closed, values_closed, color="#1F2A44", alpha=0.25)
    ax.set_xticks(angles)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 4)
    ax.set_yticks([1, 2, 3, 4])
    ax.set_yticklabels(["1", "2", "3", "4"], fontsize=8)
    title = "CHAIN profile" + (f" — {profile.name}" if profile.name else "")
    ax.set_title(title, pad=20)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return out


def write_leadership_report_md(profile: ChainProfile, path: str | Path, *, client: str = "Client") -> Path:
    """Active leadership report. BILINGUAL: English scaffolding (headings, table
    headers, connective prose); Spanish CHAIN substance kept verbatim (dimension
    names, level labels, archetype, directives, evidence, attribution)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    who = profile.name or client

    lines: list[str] = []
    lines.append(f"# Leadership diagnosis — CHAIN — {who}\n")
    lines.append("## Profile\n")
    lines.append(
        f"Average **{profile.average:.1f}/4** ({profile.average / 4 * 100:.0f}%) · "
        f"gap **{profile.gap}** · archetype: **{profile.archetype}**.\n"
    )
    lines.append("![CHAIN radar](chain_profile.png)\n")

    lines.append("## Score by dimension\n")
    lines.append("| Dimension | Level | | Evidence |")
    lines.append("|---|---|---|---|")
    for code, name in DIMS:
        v = profile.scores[code]
        ev = profile.evidence.get(code) or "—"
        lines.append(f"| {code} · {name} | {v}/4 | {LEVELS[v]} | {ev} |")
    lines.append("")

    lines.append("## Archetype\n")
    lines.append(f"**{profile.archetype}.** {profile.archetype_desc}\n")

    lines.append("## Priority lever\n")
    lines.append(
        f"**{profile.lever_name} ({profile.lever_code})** — currently {profile.lever_level}/4. "
        "One lever at a time: real change lands one at a time.\n"
    )
    lines.append("### Active directives\n")
    if profile.directives:
        for d in profile.directives[:3]:
            lines.append(f"- {d}")
    else:
        lines.append("- (no directives for this dimension)")
    lines.append("")

    lines.append("## Note\n")
    lines.append(
        "Original synthesis inspired by the CHAIN model — decision support, evidence over "
        "impression. Síntesis original inspirada en el modelo CHAIN de \"From Source to Sold\" "
        "(Palamariu & Alicke, 2022); no reproduce el texto del libro.\n"
    )

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_all(profile: ChainProfile, out_dir: str | Path, *, client: str = "Client") -> dict[str, Path]:
    """Write the leadership deliverable set: a radar chart + a written report."""
    d = Path(out_dir)
    chart = radar_chart(profile, d / "chain_profile.png")
    report = write_leadership_report_md(profile, d / "leadership_report.md", client=client)
    return {"chart": chart, "report": report}
