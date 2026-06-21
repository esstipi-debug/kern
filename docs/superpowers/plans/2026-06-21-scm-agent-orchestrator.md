# scm_agent Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing supply-chain toolkit into an agent — one entry point (`scm_agent`) that takes a free-form brief + optional data and drives it to a finished deliverable, routing to the right capability.

**Architecture:** A registry-based orchestrator. A rules-first intent classifier picks a capability; each capability is a `Tool` with four stages (`prepare → run → qa → deliver`) the orchestrator drives, enforcing the "QA fails ⇒ no deliverable" gate centrally. Quant tools (`inventory_optimization`, `pricing`) wrap the existing `jobs/` playbooks; the new `leadership_chain` tool wraps a CHAIN scoring core in `jobs/leadership.py` (score + radar chart + active directives). A pluggable `LLMProvider` (Claude when `ANTHROPIC_API_KEY` is set, else a no-op `RulesFallback`) only *improves* intent parsing and narrative — the deterministic core runs and is fully testable without any LLM.

**Tech Stack:** Python ≥3.11, pandas, numpy, scipy, matplotlib, openpyxl (all already deps); FastAPI (optional, webapp only); `anthropic` SDK (optional, LLM only); pytest + ruff.

## Global Constraints

These apply to **every** task. Copy them verbatim into your working memory.

- **Python ≥ 3.11.** Use `from __future__ import annotations` at the top of every new module (matches the codebase).
- **Interpreter:** the `py` launcher has all deps (Python 3.13 + pandas/scipy/fastapi/anthropic-if-installed). The `uv` graphify interpreter does NOT. Run everything with `py`.
- **Immutability / types:** public DTOs are `@dataclass(frozen=True)`; type-annotate every function signature (PEP 8). Prefer many small focused modules.
- **ruff:** `target-version = py311`, `line-length = 120`, `select = ["E","F","I"]`, `ignore = ["E501"]`. Keep imports isort-clean. New top-level package `scm_agent` must be added to the lint command.
- **Coverage gate:** `[tool.coverage.run] source = ["src"]`, `fail_under = 80`. New code lives in `scm_agent/`, `jobs/`, `webapp/`, `examples/` — none measured — so the gate stays green as long as you don't break `src`. Do not modify `src/`.
- **Imports work via `pythonpath = "."`** (pytest config). `scm_agent`, `jobs`, `webapp` are top-level dirs imported by name, exactly like the existing `jobs`/`webapp`. Do NOT add them to `[tool.setuptools] packages` (leave packaging as-is).
- **Runs with AND without `ANTHROPIC_API_KEY`.** Never hard-import `anthropic` at module top level; import it lazily inside `ClaudeProvider`. An LLM is never required for any deterministic path.
- **CHAIN attribution string (verbatim, required wherever the CHAIN model is described in shipped output/docs):**
  `Síntesis original inspirada en el modelo CHAIN de "From Source to Sold" (Palamariu & Alicke, 2022); no reproduce el texto del libro.`
- **Leadership output language — BILINGUAL (decided 2026-06-21).** The `leadership_chain` deliverable uses **English** report *scaffolding* (markdown headings, table column headers, connective/summary prose) with **Spanish** CHAIN *substance* kept verbatim: dimension names (`Colaborativo`…`Narrativo`), `LEVELS` rubric labels (`Ausente`…`Distintivo`), archetype names + descriptions, directives (`PRACTICES`), diagnostic questions (`QUESTIONS`), evidence text, and the attribution string above. Do **not** translate CHAIN substance; do **not** leave scaffolding in Spanish. (Task 1's standalone skill files are the original Spanish and stay unchanged.)
- **Version target:** bump project to **2.8.0** in the final task.
- **Commands:**
  - Single test: `py -m pytest tests/test_scm_agent.py::test_name -v`
  - Leadership tests: `py -m pytest tests/test_leadership.py -v`
  - Full gate: `py -m pytest -q --cov=src --cov-fail-under=80`
  - Lint: `py -m ruff check src jobs tests examples scripts webapp scm_agent`
- **Repo root** for all relative paths below: `supply-chain-optimization/`.
- **Commit after every task** (conventional commits: `feat:`, `test:`, `docs:`, `chore:`). Attribution is disabled globally — do not add Co-Authored-By.

---

## File map (what gets created/modified)

**New package `scm_agent/`:**
- `scm_agent/__init__.py` — public exports.
- `scm_agent/types.py` — `JobRequest`, `JobResult` DTOs.
- `scm_agent/llm.py` — `LLMProvider` Protocol, `RulesFallback`, `ClaudeProvider`, `get_provider`, `parse_json_object`.
- `scm_agent/registry.py` — `Tool`, `Prepared`, `Produced`, `ToolRegistry`.
- `scm_agent/tools.py` — the 3 tool factories wrapping `jobs/`; `build_default_registry`.
- `scm_agent/intent.py` — `IntentResult`, `classify`.
- `scm_agent/orchestrator.py` — `Orchestrator`.
- `scm_agent/README.md` — package docs.

**New in `jobs/`:**
- `jobs/leadership.py` — CHAIN scoring core, radar chart, report, directives, diagnostic questions.

**Modified:**
- `jobs/qa.py` — add `verify_leadership`, `leadership_passed`.
- `webapp/app.py` — add `POST /api/jobs` + a static mount for job outputs.
- `examples/run_agent.py` — new CLI (created).
- `pyproject.toml` — `anthropic`/`fastapi`/`python-multipart` optional extras; version → 2.8.0.
- `README.md`, `CHANGELOG.md` — document the agent + version bump.

**New tests:**
- `tests/test_leadership.py` — CHAIN core, chart, report, QA.
- `tests/test_scm_agent.py` — types, llm, registry, tools, intent, orchestrator, CLI.
- `tests/test_webapp.py` — extended with `POST /api/jobs` cases.

**Outside the repo (Task 1 only):** `~/.claude/skills/liderazgo-chain/{SKILL.md, references/practicas.md, scripts/score.py}`.

---

### Task 1: Install the `liderazgo-chain` Claude Code skill + add `--chart` to its `score.py`

This installs the standalone leadership skill so `/liderazgo-chain` works in Claude Code, and adds a radar-chart export to its script. It is environment setup outside the repo; its "test" is running the installed script. The repo's `jobs/leadership.py` (Task 4–5) is a *separate* port — this script stays stdlib-only except for the lazy matplotlib import behind `--chart`.

**Files:**
- Create: `~/.claude/skills/liderazgo-chain/SKILL.md` (copy of source)
- Create: `~/.claude/skills/liderazgo-chain/references/practicas.md` (copy of source)
- Create: `~/.claude/skills/liderazgo-chain/scripts/score.py` (source + `--chart`)
- Source: `C:\Users\Gamer\Downloads\ANTROPIC\sfs-skill-extracted\`

**Interfaces:**
- Produces: an installed skill (no repo code consumes it). Independent of all other tasks.

- [ ] **Step 1: Create the skill directory layout and copy the unchanged docs**

```bash
SKILL_DIR="$HOME/.claude/skills/liderazgo-chain"
SRC="/c/Users/Gamer/Downloads/ANTROPIC/sfs-skill-extracted"
mkdir -p "$SKILL_DIR/references" "$SKILL_DIR/scripts"
cp "$SRC/SKILL.md" "$SKILL_DIR/SKILL.md"
cp "$SRC/practicas.md" "$SKILL_DIR/references/practicas.md"
```

- [ ] **Step 2: Write `scripts/score.py` — the source script plus a `--chart PATH` option**

Write `~/.claude/skills/liderazgo-chain/scripts/score.py` (the original logic, unchanged, with a new `--chart` flag and a lazily-imported `radar_chart`):

```python
#!/usr/bin/env python3
"""
Perfil CHAIN — puntuación determinista de las 5 dimensiones de liderazgo.

Uso:
    python score.py C H A I N
    python score.py 3 2 3 1 1
    python score.py 3 2 3 1 1 --nombre "Equipo logística"
    python score.py 3 2 3 1 1 --chart perfil.png

Cada valor es un entero 0-4 (ver rúbrica en SKILL.md). Devuelve barras por
dimensión, promedio, brecha máx-mín y un arquetipo detectado por regla.
Solo usa la librería estándar (matplotlib se importa solo con --chart).
"""

import argparse
import sys

DIMS = [
    ("C", "Colaborativo"),
    ("H", "Holístico"),
    ("A", "Adaptable"),
    ("I", "Influyente"),
    ("N", "Narrativo"),
]
NIVELES = {0: "Ausente", 1: "Incipiente", 2: "Funcional", 3: "Sólido", 4: "Distintivo"}


def barra(score, ancho=10):
    llenos = round(score / 4 * ancho)
    return "█" * llenos + "░" * (ancho - llenos)


def arquetipo(s):
    """s: dict {C,H,A,I,N -> int}. Reglas en orden de prioridad."""
    C, H, A, I, N = s["C"], s["H"], s["A"], s["I"], s["N"]
    resto = lambda *xs: min(xs)

    if all(v >= 3 for v in s.values()):
        return ("Líder integral",
                "Las cinco dimensiones sólidas o más. Perfil listo para roles de mayor "
                "alcance; el foco pasa de cubrir huecos a profundizar fortalezas.")
    if all(v <= 1 for v in s.values()):
        return ("En formación",
                "Falta base transversal. No repartir el esfuerzo: elegí UNA dimensión y "
                "construí consistencia ahí antes de abrir frentes.")
    if I <= 1 and N <= 1 and resto(C, H, A) >= 2:
        return ("Operador invisible",
                "Hace que todo funcione, pero no se ve ni inspira. Es el patrón exacto que "
                "frena el salto a director/CEO: competencia real, sin influencia ni relato.")
    if A <= 1 and resto(C, H, I, N) >= 2:
        return ("Optimizador frágil",
                "Excelente en régimen estable, expuesto en la próxima disrupción. El riesgo "
                "no se ve hasta que algo se rompe.")
    if H <= 1 and resto(C, A, I, N) >= 2:
        return ("Especialista de silo",
                "Fuerte en su función, ciego al end-to-end. Optimiza su tramo sin ver el "
                "costo aguas abajo.")
    if C <= 1 and resto(H, A, I, N) >= 2:
        return ("Llanero solitario",
                "Resuelve solo. Capaz, pero no construye red ni confianza, así que no "
                "escala más allá de lo que toca con sus manos.")
    minimo = min(s.values())
    flojas = [nombre for code, nombre in DIMS if s[code] == minimo]
    return ("Perfil mixto",
            f"Sin un patrón único. La(s) dimensión(es) de menor desarrollo: "
            f"{', '.join(flojas)}. Priorizá la de mayor retorno en tu contexto.")


def palanca_prioritaria(s):
    """La dimensión floja de mayor retorno: la de menor score; si empatan,
    se prioriza por impacto en el salto de carrera (I, N, A, H, C)."""
    orden_impacto = ["I", "N", "A", "H", "C"]
    minimo = min(s.values())
    candidatas = [c for c in orden_impacto if s[c] == minimo]
    code = candidatas[0]
    nombre = dict(DIMS)[code]
    return code, nombre, minimo


def radar_chart(s, path, nombre=None):
    """Exporta un radar PNG de las 5 dimensiones. Importa matplotlib de forma
    perezosa para que el resto del script siga siendo stdlib-only."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [nombre_dim for _, nombre_dim in DIMS]
    values = [s[code] for code, _ in DIMS]
    n = len(labels)
    angles = [i / n * 2 * 3.141592653589793 for i in range(n)]
    values_closed = values + values[:1]
    angles_closed = angles + angles[:1]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={"polar": True})
    ax.plot(angles_closed, values_closed, color="#1F2A44", linewidth=2)
    ax.fill(angles_closed, values_closed, color="#1F2A44", alpha=0.25)
    ax.set_xticks(angles)
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 4)
    ax.set_yticks([1, 2, 3, 4])
    titulo = "Perfil CHAIN" + (f" — {nombre}" if nombre else "")
    ax.set_title(titulo, pad=20)
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return path


def main():
    p = argparse.ArgumentParser(description="Perfil CHAIN de liderazgo.")
    p.add_argument("scores", nargs=5, type=int, metavar=("C", "H", "A", "I", "N"),
                   help="5 enteros 0-4 en orden C H A I N")
    p.add_argument("--nombre", default=None, help="A quién/qué se evalúa (opcional)")
    p.add_argument("--chart", default=None, metavar="PATH",
                   help="Exporta un radar PNG a PATH (requiere matplotlib)")
    args = p.parse_args()

    for v in args.scores:
        if not 0 <= v <= 4:
            p.error(f"Cada score debe estar entre 0 y 4 (recibido: {v}).")

    s = {code: val for (code, _), val in zip(DIMS, args.scores)}

    titulo = "PERFIL CHAIN" + (f" — {args.nombre}" if args.nombre else "")
    print(titulo)
    print("=" * max(len(titulo), 44))
    for code, nombre in DIMS:
        v = s[code]
        print(f"{code} {nombre:<13} [{barra(v)}] {v}/4  {NIVELES[v]}")

    prom = sum(s.values()) / len(s)
    mx_code = max(s, key=s.get)
    mn_code = min(s, key=s.get)
    brecha = s[mx_code] - s[mn_code]
    print("-" * 44)
    print(f"Promedio: {prom:.1f}/4 ({prom/4*100:.0f}%)   Brecha: {brecha} "
          f"({dict(DIMS)[mx_code]} ↔ {dict(DIMS)[mn_code]})")

    nombre_arq, desc = arquetipo(s)
    print(f"\nArquetipo: {nombre_arq}")
    print(f"  {desc}")

    code, nombre, val = palanca_prioritaria(s)
    print(f"\nPalanca prioritaria: {nombre} ({code}) — está en {val}/4.")
    print("  Trabajá 1-2 prácticas de esta dimensión (ver references/practicas.md).")
    print("  Una palanca a la vez: el cambio real entra de a una.")

    if args.chart:
        radar_chart(s, args.chart, nombre=args.nombre)
        print(f"\nRadar exportado a: {args.chart}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify the script runs and the chart exports**

Run:
```bash
cd "$HOME/.claude/skills/liderazgo-chain/scripts"
py score.py 3 2 3 1 1 --nombre "Test" --chart /tmp/chain_test.png
ls -la /tmp/chain_test.png
```
Expected: prints the CHAIN profile (archetype "Operador invisible" for `3 2 3 1 1`), then "Radar exportado a:", and the PNG exists (non-zero size).

- [ ] **Step 4: Verify the skill is discoverable**

Run:
```bash
ls "$HOME/.claude/skills/liderazgo-chain/" "$HOME/.claude/skills/liderazgo-chain/references" "$HOME/.claude/skills/liderazgo-chain/scripts"
head -5 "$HOME/.claude/skills/liderazgo-chain/SKILL.md"
```
Expected: the three files are present; SKILL.md frontmatter shows `name: liderazgo-chain`. (The skill is picked up on the next Claude Code session start.)

- [ ] **Step 5: Commit**

This task touches only `~/.claude/` (outside the repo) — nothing to commit in the repo. Record completion in your task tracker and move on. (If you keep a dotfiles repo for `~/.claude`, commit there: `feat: add liderazgo-chain skill with radar chart export`.)

---

### Task 2: `scm_agent` package + `types.py` DTOs

**Files:**
- Create: `scm_agent/__init__.py`
- Create: `scm_agent/types.py`
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Produces:
  - `JobRequest(brief: str, data_path: str | None = None, job_type: str | None = None, params: dict = {}, client: str = "Client")` (frozen).
  - `JobResult(status: str, tool: str | None, confidence: float, deliverables: dict[str, str], summary: str, qa_issues: list[str] = [], clarifications: list[str] = [])` (frozen).
  - `STATUS_OK = "ok"`, `STATUS_NEEDS_CLARIFICATION = "needs_clarification"`, `STATUS_NEEDS_DATA = "needs_data"`, `STATUS_QA_FAILED = "qa_failed"`, `STATUS_ERROR = "error"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scm_agent.py`:

```python
"""Tests for the scm_agent orchestrator package."""

from scm_agent.types import JobRequest, JobResult


def test_job_request_defaults():
    req = JobRequest(brief="set up reorder points")
    assert req.brief == "set up reorder points"
    assert req.data_path is None
    assert req.job_type is None
    assert req.params == {}
    assert req.client == "Client"


def test_job_result_holds_status_and_deliverables():
    res = JobResult(
        status="ok",
        tool="inventory_optimization",
        confidence=0.9,
        deliverables={"report": "out/report.md"},
        summary="done",
    )
    assert res.status == "ok"
    assert res.qa_issues == []
    assert res.clarifications == []
    assert res.deliverables["report"].endswith("report.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -m pytest tests/test_scm_agent.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scm_agent'`.

- [ ] **Step 3: Create the package and DTOs**

Create `scm_agent/__init__.py`:
```python
"""scm_agent — the orchestrator spine: brief + data -> routed deliverable."""
```

Create `scm_agent/types.py`:
```python
"""Request/result DTOs for the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_OK = "ok"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_NEEDS_DATA = "needs_data"
STATUS_QA_FAILED = "qa_failed"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class JobRequest:
    """A unit of work: a free-form brief, optional data, optional explicit routing."""

    brief: str
    data_path: str | None = None
    job_type: str | None = None
    params: dict = field(default_factory=dict)
    client: str = "Client"


@dataclass(frozen=True)
class JobResult:
    """The outcome the orchestrator returns for a request."""

    status: str
    tool: str | None
    confidence: float
    deliverables: dict[str, str]
    summary: str
    qa_issues: list[str] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `py -m pytest tests/test_scm_agent.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add scm_agent/__init__.py scm_agent/types.py tests/test_scm_agent.py
git commit -m "feat: add scm_agent package with request/result DTOs"
```

---

### Task 3: `scm_agent/llm.py` — pluggable LLM provider

**Files:**
- Create: `scm_agent/llm.py`
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `LLMProvider` (runtime-checkable Protocol): `available() -> bool`, `complete(prompt: str) -> str`, `extract(prompt: str, schema: dict) -> dict`.
  - `RulesFallback()` — `available()` returns `False`; `complete`/`extract` return empty.
  - `ClaudeProvider(api_key: str, model: str = "claude-opus-4-8")` — `available()` returns `True`; lazy-imports `anthropic`.
  - `get_provider(api_key: str | None = None, model: str | None = None) -> LLMProvider` — returns `ClaudeProvider` when a key exists and `anthropic` is importable, else `RulesFallback`.
  - `parse_json_object(text: str) -> dict` — extracts the first balanced `{...}` JSON object from text; `{}` on failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scm_agent.py`:
```python
from scm_agent import llm


def test_rules_fallback_is_unavailable_and_inert():
    p = llm.RulesFallback()
    assert p.available() is False
    assert p.complete("anything") == ""
    assert p.extract("anything", {}) == {}


def test_get_provider_without_key_returns_rules_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = llm.get_provider()
    assert isinstance(p, llm.RulesFallback)
    assert p.available() is False


def test_parse_json_object_extracts_embedded_object():
    text = 'Sure! Here it is:\n```json\n{"job_type": "pricing", "n": 3}\n```\nThanks'
    obj = llm.parse_json_object(text)
    assert obj == {"job_type": "pricing", "n": 3}


def test_parse_json_object_returns_empty_on_garbage():
    assert llm.parse_json_object("no json here") == {}
    assert llm.parse_json_object("") == {}


def test_claude_provider_reports_available_without_network():
    # available() must not require the SDK or a network call
    p = llm.ClaudeProvider(api_key="sk-test", model="claude-opus-4-8")
    assert p.available() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_scm_agent.py -k "fallback or provider or parse_json" -v`
Expected: FAIL — `AttributeError`/`ImportError` (no `scm_agent.llm`).

- [ ] **Step 3: Implement `scm_agent/llm.py`**

```python
"""Pluggable LLM layer. Claude when a key is available; an inert rules fallback
otherwise. The deterministic core never requires a provider."""

from __future__ import annotations

import json
import os
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "claude-opus-4-8"


@runtime_checkable
class LLMProvider(Protocol):
    def available(self) -> bool: ...
    def complete(self, prompt: str) -> str: ...
    def extract(self, prompt: str, schema: dict) -> dict: ...


def parse_json_object(text: str) -> dict:
    """Return the first balanced top-level JSON object in `text`, or {}.

    Tolerant of code fences and surrounding prose — scans for the first '{'
    and matches braces (ignoring those inside strings)."""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return {}
                return obj if isinstance(obj, dict) else {}
    return {}


class RulesFallback:
    """Always-available no-op provider. `available()` is False so callers take
    the deterministic path."""

    def available(self) -> bool:
        return False

    def complete(self, prompt: str) -> str:
        return ""

    def extract(self, prompt: str, schema: dict) -> dict:
        return {}


class ClaudeProvider:
    """Anthropic-backed provider. Imports the SDK lazily on first network use."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def available(self) -> bool:
        return True

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # lazy: optional dependency

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, prompt: str) -> str:
        client = self._ensure_client()
        msg = client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        )

    def extract(self, prompt: str, schema: dict) -> dict:
        instruction = (
            f"{prompt}\n\nRespond with ONLY a single JSON object matching this schema "
            f"(no prose, no code fence):\n{json.dumps(schema)}"
        )
        return parse_json_object(self.complete(instruction))


def get_provider(api_key: str | None = None, model: str | None = None) -> LLMProvider:
    """Factory: ClaudeProvider when a key + SDK are present, else RulesFallback."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return RulesFallback()
    try:
        import anthropic  # noqa: F401  (probe only)
    except ImportError:
        return RulesFallback()
    return ClaudeProvider(key, model=model or DEFAULT_MODEL)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_scm_agent.py -k "fallback or provider or parse_json" -v`
Expected: PASS.

- [ ] **Step 5: Confirm the Anthropic SDK call shape**

The `ClaudeProvider.complete` network path is intentionally not unit-tested. Before relying on it at runtime, confirm `client.messages.create(model=..., max_tokens=..., messages=[...])` and the `.content[].text` response shape against the installed SDK using the `claude-api` skill (or `py -c "import anthropic; help(anthropic.Anthropic().messages.create)"` if a key is present). The model id `claude-opus-4-8` is correct for Opus 4.8.

- [ ] **Step 6: Commit**

```bash
git add scm_agent/llm.py tests/test_scm_agent.py
git commit -m "feat: add pluggable LLM provider (Claude + rules fallback)"
```

---

### Task 4: `jobs/leadership.py` CHAIN scoring core + `qa.verify_leadership`

Port the deterministic CHAIN logic from the skill's `score.py` into a structured, typed module, and add its QA gate. (Chart/report/directives come in Task 5.)

**Files:**
- Create: `jobs/leadership.py`
- Modify: `jobs/qa.py` (add `verify_leadership`, `leadership_passed`)
- Test: `tests/test_leadership.py`

**Interfaces:**
- Produces:
  - `DIMS: list[tuple[str, str]]` = `[("C","Colaborativo"),("H","Holístico"),("A","Adaptable"),("I","Influyente"),("N","Narrativo")]`
  - `LEVELS: dict[int, str]`
  - `coerce_scores(value) -> dict[str, int] | None` — accepts a list/tuple of 5 ints, or a space/comma string `"3 2 3 1 1"`; returns `{C,H,A,I,N->int}` or `None` if not exactly 5 ints in 0..4.
  - `archetype(scores: dict[str, int]) -> tuple[str, str]` — `(name, description)`.
  - `priority_lever(scores: dict[str, int]) -> tuple[str, str, int]` — `(code, name, level)`.
  - `ChainProfile` (frozen): `scores: dict[str,int]`, `evidence: dict[str,str]`, `name: str | None`, `average: float`, `gap: int`, `archetype: str`, `archetype_desc: str`, `lever_code: str`, `lever_name: str`, `lever_level: int`, `directives: list[str]`.
  - `score_profile(scores: dict[str,int], *, evidence: dict[str,str] | None = None, name: str | None = None) -> ChainProfile` — raises `ValueError` if scores aren't the 5 dims each in 0..4.
- Produces (in `jobs/qa.py`): `verify_leadership(profile: ChainProfile) -> list[str]`, `leadership_passed(profile) -> bool`.
- Note: `score_profile` reads `directives` from `PRACTICES` (added in Task 5). For this task, define `PRACTICES: dict[str, list[str]] = {c: [] for c, _ in DIMS}` as a placeholder-free empty mapping; Task 5 fills it. `directives` will therefore be `[]` until Task 5 — the Task-4 tests do not assert on directives.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_leadership.py`:
```python
"""Tests for the CHAIN leadership playbook (jobs/leadership.py)."""

import pytest

from jobs import leadership as ld
from jobs import qa


def test_coerce_scores_from_string_and_list():
    assert ld.coerce_scores("3 2 3 1 1") == {"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}
    assert ld.coerce_scores([0, 1, 2, 3, 4]) == {"C": 0, "H": 1, "A": 2, "I": 3, "N": 4}
    assert ld.coerce_scores("3,2,3,1,1") == {"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}


def test_coerce_scores_rejects_bad_input():
    assert ld.coerce_scores("1 2 3") is None        # too few
    assert ld.coerce_scores([1, 2, 3, 4, 9]) is None  # out of range
    assert ld.coerce_scores("a b c d e") is None     # non-int
    assert ld.coerce_scores(None) is None


def test_archetype_operador_invisible():
    # strong C/H/A, weak I/N -> the signature "invisible operator"
    name, _ = ld.archetype({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    assert name == "Operador invisible"


def test_archetype_lider_integral_and_en_formacion():
    assert ld.archetype({"C": 3, "H": 3, "A": 3, "I": 3, "N": 3})[0] == "Líder integral"
    assert ld.archetype({"C": 1, "H": 0, "A": 1, "I": 1, "N": 0})[0] == "En formación"


def test_priority_lever_breaks_ties_by_impact_order():
    # I and N both lowest at 1 -> I wins (impact order I,N,A,H,C)
    code, name, level = ld.priority_lever({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    assert code == "I" and level == 1 and name == "Influyente"


def test_score_profile_computes_average_gap_and_archetype():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}, name="Equipo X")
    assert p.name == "Equipo X"
    assert p.average == pytest.approx(2.0)
    assert p.gap == 2  # max 3 - min 1
    assert p.archetype == "Operador invisible"
    assert p.lever_code == "I"


def test_score_profile_rejects_invalid_scores():
    with pytest.raises(ValueError):
        ld.score_profile({"C": 3, "H": 2})            # missing dims
    with pytest.raises(ValueError):
        ld.score_profile({"C": 9, "H": 2, "A": 3, "I": 1, "N": 1})  # out of range


def test_qa_verify_leadership_passes_clean_profile():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    assert qa.verify_leadership(p) == []
    assert qa.leadership_passed(p) is True


def test_qa_verify_leadership_catches_tampered_profile():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})
    object.__setattr__(p, "average", 9.9)  # corrupt
    assert any("average" in i for i in qa.verify_leadership(p))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_leadership.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jobs.leadership'`.

- [ ] **Step 3: Implement the CHAIN core in `jobs/leadership.py`**

```python
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

# Filled in Task 5; empty here so directives are [] until then.
PRACTICES: dict[str, list[str]] = {code: [] for code in _CODES}
QUESTIONS: dict[str, list[str]] = {code: [] for code in _CODES}


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
    C, H, A, I, N = scores["C"], scores["H"], scores["A"], scores["I"], scores["N"]

    if all(v >= 3 for v in scores.values()):
        return ("Líder integral",
                "Las cinco dimensiones sólidas o más. Perfil listo para roles de mayor "
                "alcance; el foco pasa de cubrir huecos a profundizar fortalezas.")
    if all(v <= 1 for v in scores.values()):
        return ("En formación",
                "Falta base transversal. No repartir el esfuerzo: elegí UNA dimensión y "
                "construí consistencia ahí antes de abrir frentes.")
    if I <= 1 and N <= 1 and min(C, H, A) >= 2:
        return ("Operador invisible",
                "Hace que todo funcione, pero no se ve ni inspira. Es el patrón exacto que "
                "frena el salto a director/CEO: competencia real, sin influencia ni relato.")
    if A <= 1 and min(C, H, I, N) >= 2:
        return ("Optimizador frágil",
                "Excelente en régimen estable, expuesto en la próxima disrupción. El riesgo "
                "no se ve hasta que algo se rompe.")
    if H <= 1 and min(C, A, I, N) >= 2:
        return ("Especialista de silo",
                "Fuerte en su función, ciego al end-to-end. Optimiza su tramo sin ver el "
                "costo aguas abajo.")
    if C <= 1 and min(H, A, I, N) >= 2:
        return ("Llanero solitario",
                "Resuelve solo. Capaz, pero no construye red ni confianza, así que no "
                "escala más allá de lo que toca con sus manos.")
    minimo = min(scores.values())
    flojas = [name for code, name in DIMS if scores[code] == minimo]
    return ("Perfil mixto",
            f"Sin un patrón único. La(s) dimensión(es) de menor desarrollo: "
            f"{', '.join(flojas)}. Priorizá la de mayor retorno en tu contexto.")


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
        scores=dict(scores), evidence=ev, name=name, average=average, gap=gap,
        archetype=arch_name, archetype_desc=arch_desc,
        lever_code=lever_code, lever_name=lever_name, lever_level=lever_level,
        directives=directives,
    )
```

- [ ] **Step 4: Add the QA gate to `jobs/qa.py`**

At the top of `jobs/qa.py`, add the import alongside the existing `from .inventory_optimization ...` / `from .pricing ...` imports (no circular import — `jobs/leadership.py` does not import `qa`):
```python
from .leadership import DIMS, ChainProfile
```
Then append to `jobs/qa.py`:
```python
def verify_leadership(profile: ChainProfile) -> list[str]:
    """Return a list of QA issues for a CHAIN profile. Empty list = passed."""
    issues: list[str] = []
    codes = {code for code, _ in DIMS}

    if set(profile.scores) != codes:
        issues.append("profile is missing CHAIN dimensions")
    for code, val in profile.scores.items():
        if not 0 <= val <= 4:
            issues.append(f"{code}: score out of 0..4")

    expected_avg = sum(profile.scores.values()) / len(profile.scores) if profile.scores else 0.0
    if abs(profile.average - expected_avg) > 1e-9:
        issues.append("average does not match scores")

    if profile.scores:
        expected_gap = max(profile.scores.values()) - min(profile.scores.values())
        if profile.gap != expected_gap:
            issues.append("gap does not match scores")
        if profile.lever_level != min(profile.scores.values()):
            issues.append("priority lever is not the lowest-scoring dimension")

    if not profile.archetype:
        issues.append("missing archetype")
    return issues


def leadership_passed(profile: ChainProfile) -> bool:
    return not verify_leadership(profile)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_leadership.py -v`
Expected: PASS (all 9 tests).

- [ ] **Step 6: Lint**

Run: `py -m ruff check jobs/leadership.py jobs/qa.py tests/test_leadership.py`
Expected: clean. (Fix `lambda`/unused-import findings if any.)

- [ ] **Step 7: Commit**

```bash
git add jobs/leadership.py jobs/qa.py tests/test_leadership.py
git commit -m "feat: add CHAIN leadership scoring core + QA gate"
```

---

### Task 5: Leadership directives, diagnostic questions, radar chart, report + `write_all`

Fill `PRACTICES` and `QUESTIONS` (the "active directives" deliverable), and add the radar chart, the written report, and `write_all`.

**Files:**
- Modify: `jobs/leadership.py`
- Test: `tests/test_leadership.py`

**Interfaces:**
- Consumes: `ChainProfile`, `DIMS`, `LEVELS`, `score_profile` (Task 4).
- Produces:
  - `PRACTICES: dict[str, list[str]]` — 3 active practices per dimension (filled).
  - `QUESTIONS: dict[str, list[str]]` — 3 diagnostic questions per dimension (filled).
  - `diagnostic_questions() -> list[str]` — flat, prefixed-by-dimension list for `needs_clarification`.
  - `radar_chart(profile: ChainProfile, path: str | Path) -> Path` — writes a PNG (matplotlib, Agg).
  - `write_leadership_report_md(profile: ChainProfile, path: str | Path, *, client: str = "Client") -> Path`.
  - `write_all(profile: ChainProfile, out_dir: str | Path, *, client: str = "Client") -> dict[str, Path]` — keys `"chart"` and `"report"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_leadership.py`:
```python
def test_practices_and_questions_filled_for_every_dimension():
    for code, _ in ld.DIMS:
        assert len(ld.PRACTICES[code]) >= 2
        assert len(ld.QUESTIONS[code]) >= 2


def test_score_profile_attaches_lever_directives():
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1})  # lever = I
    assert p.directives == ld.PRACTICES["I"]
    assert len(p.directives) >= 2


def test_diagnostic_questions_cover_all_dimensions():
    qs = ld.diagnostic_questions()
    assert len(qs) >= 10
    assert any(q.startswith("C ") or q.startswith("[C]") or "Colaborativo" in q for q in qs)


def test_radar_chart_writes_png(tmp_path):
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}, name="Equipo X")
    out = ld.radar_chart(p, tmp_path / "chain.png")
    assert out.exists() and out.stat().st_size > 0


def test_write_all_writes_report_and_chart(tmp_path):
    p = ld.score_profile({"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}, name="Equipo X")
    written = ld.write_all(p, tmp_path, client="Acme")
    assert written["chart"].exists()
    assert written["report"].exists()
    md = written["report"].read_text(encoding="utf-8")
    # English scaffolding
    assert "## Score by dimension" in md
    assert "## Archetype" in md
    assert "## Priority lever" in md
    # Spanish CHAIN substance kept verbatim
    assert "Operador invisible" in md          # archetype (Spanish)
    assert "Influyente" in md                   # dimension / lever name (Spanish)
    assert "Palamariu" in md                    # attribution present (Spanish)
    assert p.directives[0].split(".")[0][:8] in md  # at least one directive rendered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_leadership.py -k "practices or directives or diagnostic or radar or write_all" -v`
Expected: FAIL — `PRACTICES` empty / `radar_chart`/`write_all`/`diagnostic_questions` undefined.

- [ ] **Step 3: Fill `PRACTICES` and `QUESTIONS`**

In `jobs/leadership.py`, replace the placeholder `PRACTICES`/`QUESTIONS` definitions with the filled mappings (ported from the skill's `references/practicas.md` and `SKILL.md`):

```python
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
```

- [ ] **Step 4: Add `diagnostic_questions`, `radar_chart`, the report, and `write_all`**

Add `from pathlib import Path` to the imports at the top of `jobs/leadership.py`, then append:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_leadership.py -v`
Expected: PASS (all, including Task-4 tests — `directives` is now populated).

- [ ] **Step 6: Lint**

Run: `py -m ruff check jobs/leadership.py tests/test_leadership.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add jobs/leadership.py tests/test_leadership.py
git commit -m "feat: add CHAIN directives, radar chart and active report"
```

---

### Task 6: `scm_agent/registry.py` — Tool + ToolRegistry

**Files:**
- Create: `scm_agent/registry.py`
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Consumes: `JobRequest` (types), `LLMProvider` (llm) — only as type hints.
- Produces:
  - `Prepared(status: str, payload: object = None, messages: list[str] = [])` (frozen).
  - `Produced(report: object, summary: str)` (frozen).
  - `Tool` (frozen): `key: str`, `title: str`, `description: str`, `intent_keywords: tuple[str, ...]`, `requires_data: bool`, `prepare: Callable[[JobRequest, LLMProvider], Prepared]`, `run: Callable[[object, dict], Produced]`, `qa: Callable[[object], list[str]]`, `deliver: Callable[[object, Path, str], dict[str, Path]]`.
  - `ToolRegistry`: `register(tool) -> None` (raises `ValueError` on dup key), `get(key) -> Tool` (raises `KeyError`), `list() -> list[Tool]`, `match(brief: str) -> list[tuple[Tool, float]]` (sorted by descending keyword-hit score; score = count of `intent_keywords` appearing as substrings in the lowercased brief).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scm_agent.py`:
```python
from scm_agent.registry import Prepared, Produced, Tool, ToolRegistry


def _dummy_tool(key, keywords, requires_data=True):
    return Tool(
        key=key, title=key.title(), description=f"{key} tool",
        intent_keywords=tuple(keywords), requires_data=requires_data,
        prepare=lambda req, prov: Prepared(status="ok", payload=None),
        run=lambda payload, params: Produced(report=None, summary="ok"),
        qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    )


def test_registry_register_get_list():
    reg = ToolRegistry()
    t = _dummy_tool("inventory_optimization", ["reorder", "inventory"])
    reg.register(t)
    assert reg.get("inventory_optimization") is t
    assert [x.key for x in reg.list()] == ["inventory_optimization"]


def test_registry_rejects_duplicate_key():
    reg = ToolRegistry()
    reg.register(_dummy_tool("pricing", ["price"]))
    with pytest.raises(ValueError):
        reg.register(_dummy_tool("pricing", ["price"]))


def test_registry_get_unknown_raises_keyerror():
    reg = ToolRegistry()
    with pytest.raises(KeyError):
        reg.get("nope")


def test_registry_match_scores_by_keyword_hits():
    reg = ToolRegistry()
    reg.register(_dummy_tool("inventory_optimization", ["reorder", "safety stock", "inventory"]))
    reg.register(_dummy_tool("pricing", ["price", "elasticity", "margin"]))
    ranked = reg.match("set up reorder points and safety stock for my inventory")
    assert ranked[0][0].key == "inventory_optimization"
    assert ranked[0][1] >= 3  # three keyword hits
    assert ranked[1][1] == 0  # pricing has no hits
```

Add `import pytest` at the top of `tests/test_scm_agent.py` if not already present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_scm_agent.py -k "registry" -v`
Expected: FAIL — no `scm_agent.registry`.

- [ ] **Step 3: Implement `scm_agent/registry.py`**

```python
"""Capability registry — tools self-describe and the orchestrator drives their
four stages (prepare -> run -> qa -> deliver). Adding a capability = registering
a Tool; no routing edits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .llm import LLMProvider
from .types import JobRequest


@dataclass(frozen=True)
class Prepared:
    """Output of Tool.prepare. status 'ok' lets the orchestrator proceed to run;
    'needs_data'/'needs_clarification' short-circuit with `messages`."""

    status: str
    payload: object = None
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Produced:
    """Output of Tool.run: the report object + a human summary line."""

    report: object
    summary: str


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    description: str
    intent_keywords: tuple[str, ...]
    requires_data: bool
    prepare: Callable[[JobRequest, LLMProvider], Prepared]
    run: Callable[[object, dict], Produced]
    qa: Callable[[object], list[str]]
    deliver: Callable[[object, Path, str], dict[str, Path]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.key in self._tools:
            raise ValueError(f"tool already registered: {tool.key}")
        self._tools[tool.key] = tool

    def get(self, key: str) -> Tool:
        return self._tools[key]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def match(self, brief: str) -> list[tuple[Tool, float]]:
        """Rank tools by keyword-hit count against the lowercased brief."""
        text = brief.lower()
        scored = [
            (tool, float(sum(1 for kw in tool.intent_keywords if kw.lower() in text)))
            for tool in self._tools.values()
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_scm_agent.py -k "registry" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scm_agent/registry.py tests/test_scm_agent.py
git commit -m "feat: add capability registry (Tool + ToolRegistry)"
```

---

### Task 7: `scm_agent/tools.py` — the 3 capabilities + `build_default_registry`

Wrap the existing `jobs/` playbooks (and the leadership core) as `Tool`s.

**Files:**
- Create: `scm_agent/tools.py`
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Consumes: `Tool`, `Prepared`, `Produced`, `ToolRegistry` (registry); `JobRequest` (types); `LLMProvider` (llm); `jobs.intake`, `jobs.inventory_optimization`, `jobs.pricing`, `jobs.qa`, `jobs.deliverables`, `jobs.leadership`.
- Produces:
  - `inventory_tool() -> Tool`, `pricing_tool() -> Tool`, `leadership_tool() -> Tool`.
  - `build_default_registry() -> ToolRegistry` — registers all three (keys `inventory_optimization`, `pricing`, `leadership_chain`).
  - Tool keys/keywords:
    - `inventory_optimization`: `("reorder", "safety stock", "stock level", "inventory", "replenish", "eoq", "service level", "reorder point", "order quantity")`, `requires_data=True`.
    - `pricing`: `("price", "pricing", "elasticity", "margin", "markdown", "optimal price", "what price", "profit")`, `requires_data=True`.
    - `leadership_chain`: `("leadership", "liderazgo", "líder", "ceo", "director", "chain model", "manager", "team")`, `requires_data=False`. (Deliberately NO bare `"chain"` — it would match `"supply chain"` in almost any brief and mis-route.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scm_agent.py`:
```python
from scm_agent import tools
from scm_agent.types import JobRequest

PORTFOLIO = "data/sample_demand_portfolio.csv"
PRICING_CSV = "data/sample_pricing.csv"


def test_build_default_registry_has_three_tools():
    reg = tools.build_default_registry()
    keys = {t.key for t in reg.list()}
    assert keys == {"inventory_optimization", "pricing", "leadership_chain"}
    assert reg.get("leadership_chain").requires_data is False
    assert reg.get("inventory_optimization").requires_data is True


def test_inventory_tool_pipeline_on_sample(tmp_path):
    from scm_agent import llm
    t = tools.inventory_tool()
    req = JobRequest(brief="reorder points", data_path=PORTFOLIO)
    prep = t.prepare(req, llm.RulesFallback())
    assert prep.status == "ok"
    produced = t.run(prep.payload, {})
    assert t.qa(produced.report) == []
    written = t.deliver(produced.report, tmp_path, "Acme")
    assert written["excel"].exists() and written["report"].exists()


def test_inventory_tool_reports_needs_data_when_columns_undetectable(tmp_path):
    from scm_agent import llm
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    t = tools.inventory_tool()
    prep = t.prepare(JobRequest(brief="reorder", data_path=str(bad)), llm.RulesFallback())
    assert prep.status == "needs_data"
    assert prep.messages


def test_pricing_tool_pipeline_on_sample(tmp_path):
    from scm_agent import llm
    t = tools.pricing_tool()
    prep = t.prepare(JobRequest(brief="optimal price", data_path=PRICING_CSV), llm.RulesFallback())
    assert prep.status == "ok"
    produced = t.run(prep.payload, {})
    assert t.qa(produced.report) == []
    written = t.deliver(produced.report, tmp_path, "Acme")
    assert written["excel"].exists()


def test_leadership_tool_with_scores_in_params(tmp_path):
    from scm_agent import llm
    t = tools.leadership_tool()
    req = JobRequest(brief="evaluate our SC leadership", params={"scores": "3 2 3 1 1", "name": "Equipo X"})
    prep = t.prepare(req, llm.RulesFallback())
    assert prep.status == "ok"
    produced = t.run(prep.payload, {})
    assert t.qa(produced.report) == []
    written = t.deliver(produced.report, tmp_path, "Acme")
    assert written["chart"].exists() and written["report"].exists()


def test_leadership_tool_needs_clarification_without_scores_or_llm():
    from scm_agent import llm
    t = tools.leadership_tool()
    prep = t.prepare(JobRequest(brief="how is my leadership?"), llm.RulesFallback())
    assert prep.status == "needs_clarification"
    assert len(prep.messages) >= 10  # the diagnostic questions
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_scm_agent.py -k "tool or registry_has" -v`
Expected: FAIL — no `scm_agent.tools`.

- [ ] **Step 3: Implement `scm_agent/tools.py`**

```python
"""The three MVP capabilities, each wrapping existing job machinery as a Tool."""

from __future__ import annotations

from pathlib import Path

from jobs import deliverables, intake, leadership, qa
from jobs.inventory_optimization import run as run_inventory
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing

from .llm import LLMProvider
from .registry import Prepared, Produced, Tool, ToolRegistry
from .types import JobRequest

LEADERSHIP_SCHEMA = {
    "type": "object",
    "properties": {
        "C": {"type": "integer", "minimum": 0, "maximum": 4},
        "H": {"type": "integer", "minimum": 0, "maximum": 4},
        "A": {"type": "integer", "minimum": 0, "maximum": 4},
        "I": {"type": "integer", "minimum": 0, "maximum": 4},
        "N": {"type": "integer", "minimum": 0, "maximum": 4},
        "evidence": {"type": "object"},
    },
    "required": ["C", "H", "A", "I", "N"],
}


# ---- inventory_optimization --------------------------------------------------

def _inventory_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a demand CSV/Excel file is required"])
    try:
        demand = intake.prepare(request.data_path, period=request.params.get("period", "W"))
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=demand)


def _inventory_run(payload: object, params: dict) -> Produced:
    report = run_inventory(
        payload,
        service_level=params.get("service_level", 0.95),
        holding_rate=params.get("holding_rate", 0.25),
        order_cost=params.get("order_cost", 75.0),
        budget=params.get("budget"),
        periods_per_year=params.get("periods_per_year", 52.0),
    )
    summary = (
        f"Analyzed {report.n_skus} SKUs; recommended inventory investment "
        f"${report.final_investment:,.0f} at {report.params['service_level'] * 100:.0f}% service level."
    )
    return Produced(report=report, summary=summary)


def inventory_tool() -> Tool:
    return Tool(
        key="inventory_optimization",
        title="Inventory Optimization",
        description="Forecast demand, set (s,Q)/(R,S) policies and allocate an inventory budget.",
        intent_keywords=(
            "reorder", "safety stock", "stock level", "inventory", "replenish",
            "eoq", "service level", "reorder point", "order quantity",
        ),
        requires_data=True,
        prepare=_inventory_prepare,
        run=_inventory_run,
        qa=lambda report: qa.verify(report),
        deliver=lambda report, out_dir, client: deliverables.write_all(report, out_dir, client=client),
    )


# ---- pricing -----------------------------------------------------------------

def _pricing_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a price/quantity CSV/Excel file is required"])
    try:
        demand = prepare_pricing(request.data_path, period=request.params.get("period", "W"))
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    return Prepared(status="ok", payload=demand)


def _pricing_run(payload: object, params: dict) -> Produced:
    report = run_pricing(payload, cost_ratio=params.get("cost_ratio", 0.6))
    summary = (
        f"Analyzed {report.n_skus} SKUs; {report.n_actionable} with a confident price move "
        f"({report.n_inelastic} inelastic, {report.n_insufficient} insufficient data)."
    )
    return Produced(report=report, summary=summary)


def pricing_tool() -> Tool:
    return Tool(
        key="pricing",
        title="Price Optimization",
        description="Estimate per-SKU elasticity and recommend a margin-maximizing price.",
        intent_keywords=(
            "price", "pricing", "elasticity", "margin", "markdown",
            "optimal price", "what price", "profit",
        ),
        requires_data=True,
        prepare=_pricing_prepare,
        run=_pricing_run,
        qa=lambda report: qa.verify_pricing(report),
        deliver=lambda report, out_dir, client: deliverables.write_pricing_all(report, out_dir, client=client),
    )


# ---- leadership_chain --------------------------------------------------------

def _llm_leadership_scores(provider: LLMProvider, brief: str) -> tuple[dict[str, int], dict[str, str]] | None:
    prompt = (
        "You are scoring supply-chain leadership on the CHAIN model (C Colaborativo, "
        "H Holístico, A Adaptable, I Influyente, N Narrativo), each 0-4, with one short "
        "evidence phrase per dimension drawn from the brief. Evidence over impression: if "
        "the brief gives no observable example for a dimension, cap it at 1.\n\n"
        f"Brief:\n{brief}"
    )
    obj = provider.extract(prompt, LEADERSHIP_SCHEMA)
    scores = leadership.coerce_scores([obj.get(c) for c, _ in leadership.DIMS])
    if scores is None:
        return None
    raw_evidence = obj.get("evidence") or {}
    evidence = {c: str(raw_evidence.get(c, "")) for c, _ in leadership.DIMS}
    return scores, evidence


def _leadership_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    scores = leadership.coerce_scores(request.params.get("scores"))
    evidence: dict[str, str] = {}
    if scores is None and provider.available():
        extracted = _llm_leadership_scores(provider, request.brief)
        if extracted is not None:
            scores, evidence = extracted
    if scores is None:
        return Prepared(status="needs_clarification", messages=leadership.diagnostic_questions())
    profile = leadership.score_profile(scores, evidence=evidence, name=request.params.get("name"))
    return Prepared(status="ok", payload=profile)


def _leadership_run(payload: object, params: dict) -> Produced:
    profile = payload
    summary = (
        f"CHAIN {profile.average:.1f}/4 · archetype: {profile.archetype} · "
        f"priority lever: {profile.lever_name} ({profile.lever_code})."
    )
    return Produced(report=profile, summary=summary)


def leadership_tool() -> Tool:
    return Tool(
        key="leadership_chain",
        title="Leadership (CHAIN)",
        description="Score supply-chain leadership on the CHAIN model: profile, archetype, "
                    "priority lever and active directives.",
        intent_keywords=(
            # NOTE: no bare "chain" — it matches "supply chain" in nearly every
            # brief in this domain and would mis-route. Use "chain model" instead.
            "leadership", "liderazgo", "líder", "ceo", "director",
            "chain model", "manager", "team",
        ),
        requires_data=False,
        prepare=_leadership_prepare,
        run=_leadership_run,
        qa=lambda profile: qa.verify_leadership(profile),
        deliver=lambda profile, out_dir, client: leadership.write_all(profile, out_dir, client=client),
    )


def build_default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(inventory_tool())
    reg.register(pricing_tool())
    reg.register(leadership_tool())
    return reg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_scm_agent.py -k "tool or registry_has" -v`
Expected: PASS (all tool tests).

- [ ] **Step 5: Lint**

Run: `py -m ruff check scm_agent/tools.py tests/test_scm_agent.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add scm_agent/tools.py tests/test_scm_agent.py
git commit -m "feat: wrap inventory, pricing and leadership as agent tools"
```

---

### Task 8: `scm_agent/intent.py` — intent classifier

**Files:**
- Create: `scm_agent/intent.py`
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Consumes: `ToolRegistry` (registry), `LLMProvider` (llm).
- Produces:
  - `IntentResult(job_type: str | None, confidence: float, params: dict, candidates: list[str])` (frozen).
  - `classify(brief: str, registry: ToolRegistry, provider: LLMProvider, *, job_type_override: str | None = None) -> IntentResult`.
    - Override wins (`confidence=1.0`).
    - Else rank via `registry.match`. If the top score ≥ 1 and strictly beats the second, route to it (confidence = top / total hits).
    - Else, if `provider.available()`, ask the LLM to pick a registered key; accept only a valid key (confidence `0.6`).
    - Else return `job_type=None` with `candidates` = up to the top 3 tools with any hits, else all tool keys.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scm_agent.py`:
```python
from scm_agent import intent
from scm_agent.intent import IntentResult


class _FakeProvider:
    def __init__(self, *, extract_obj=None, complete_text="", available=True):
        self._extract = extract_obj or {}
        self._complete = complete_text
        self._available = available

    def available(self):
        return self._available

    def complete(self, prompt):
        return self._complete

    def extract(self, prompt, schema):
        return dict(self._extract)


def test_classify_routes_inventory_brief():
    reg = tools.build_default_registry()
    res = intent.classify("set up reorder points and safety stock", reg, _FakeProvider(available=False))
    assert res.job_type == "inventory_optimization"
    assert res.confidence > 0


def test_classify_routes_pricing_and_leadership():
    reg = tools.build_default_registry()
    p = _FakeProvider(available=False)
    assert intent.classify("what price maximizes profit", reg, p).job_type == "pricing"
    assert intent.classify("evaluate our supply chain leadership (CHAIN)", reg, p).job_type == "leadership_chain"


def test_classify_override_wins():
    reg = tools.build_default_registry()
    res = intent.classify("anything", reg, _FakeProvider(available=False), job_type_override="pricing")
    assert res.job_type == "pricing" and res.confidence == 1.0


def test_classify_ambiguous_without_llm_returns_candidates():
    reg = tools.build_default_registry()
    res = intent.classify("help me with my supply chain", reg, _FakeProvider(available=False))
    assert res.job_type is None
    assert res.candidates  # something to disambiguate


def test_classify_uses_llm_when_rules_are_ambiguous():
    reg = tools.build_default_registry()
    prov = _FakeProvider(extract_obj={"job_type": "pricing"}, available=True)
    res = intent.classify("help me with my supply chain", reg, prov)
    assert res.job_type == "pricing"
    assert res.confidence == pytest.approx(0.6)


def test_leadership_tool_scores_via_llm_provider():
    # the leadership LLM-extraction branch in tools.py, exercised deterministically
    t = tools.leadership_tool()
    prov = _FakeProvider(
        extract_obj={"C": 3, "H": 2, "A": 3, "I": 1, "N": 1, "evidence": {"C": "convoca a otras áreas"}},
        available=True,
    )
    prep = t.prepare(JobRequest(brief="great ops, never presents to the board"), prov)
    assert prep.status == "ok"
    assert prep.payload.scores == {"C": 3, "H": 2, "A": 3, "I": 1, "N": 1}
    assert prep.payload.evidence["C"] == "convoca a otras áreas"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_scm_agent.py -k "classify" -v`
Expected: FAIL — no `scm_agent.intent`.

- [ ] **Step 3: Implement `scm_agent/intent.py`**

```python
"""Intent classification — rules first, LLM only when rules are ambiguous."""

from __future__ import annotations

from dataclasses import dataclass, field

from .llm import LLMProvider
from .registry import ToolRegistry

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {"job_type": {"type": "string"}},
    "required": ["job_type"],
}


@dataclass(frozen=True)
class IntentResult:
    job_type: str | None
    confidence: float
    params: dict = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)


def _llm_classify(provider: LLMProvider, brief: str, registry: ToolRegistry) -> str | None:
    keys = [t.key for t in registry.list()]
    catalog = "\n".join(f"- {t.key}: {t.description}" for t in registry.list())
    prompt = (
        "Pick the single best capability for this request. Respond with the exact key only.\n\n"
        f"Capabilities:\n{catalog}\n\nRequest:\n{brief}"
    )
    obj = provider.extract(prompt, _INTENT_SCHEMA)
    guess = str(obj.get("job_type", "")).strip()
    return guess if guess in keys else None


def classify(
    brief: str,
    registry: ToolRegistry,
    provider: LLMProvider,
    *,
    job_type_override: str | None = None,
) -> IntentResult:
    if job_type_override:
        return IntentResult(job_type=job_type_override, confidence=1.0)

    ranked = registry.match(brief)
    top_tool, top_score = ranked[0] if ranked else (None, 0.0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    total = sum(score for _, score in ranked)

    if top_tool is not None and top_score >= 1 and top_score > second_score:
        confidence = top_score / total if total else 0.0
        return IntentResult(job_type=top_tool.key, confidence=confidence)

    if provider.available():
        guess = _llm_classify(provider, brief, registry)
        if guess:
            return IntentResult(job_type=guess, confidence=0.6)

    candidates = [t.key for t, score in ranked if score > 0][:3] or [t.key for t in registry.list()]
    confidence = top_score / total if total else 0.0
    return IntentResult(job_type=None, confidence=confidence, candidates=candidates)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_scm_agent.py -k "classify" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scm_agent/intent.py tests/test_scm_agent.py
git commit -m "feat: add rules-first intent classifier with LLM fallback"
```

---

### Task 9: `scm_agent/orchestrator.py` — the spine

**Files:**
- Create: `scm_agent/orchestrator.py`
- Modify: `scm_agent/__init__.py` (public exports)
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `Orchestrator(registry: ToolRegistry | None = None, provider: LLMProvider | None = None)` — defaults to `build_default_registry()` and `get_provider()`.
  - `Orchestrator.run(brief: str, *, data_path: str | None = None, overrides: dict | None = None, job_type: str | None = None, client: str = "Client", out_dir: str | Path = "deliverables/agent") -> JobResult`.
  - `scm_agent.__init__` re-exports: `Orchestrator`, `JobRequest`, `JobResult`, `build_default_registry`, `get_provider`.
- Orchestrator flow: classify → (None ⇒ `needs_clarification`) → get tool → (`requires_data` & no `data_path` ⇒ `needs_data`) → `prepare` (non-ok ⇒ that status) → `run` → `qa` (non-empty ⇒ `qa_failed`, **no deliverables**) → `deliver` → optional narrative upgrade → `ok`. Any unexpected exception ⇒ `error`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scm_agent.py`:
```python
from scm_agent.orchestrator import Orchestrator


def _rules_orch():
    from scm_agent import llm
    return Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())


def test_orchestrator_inventory_end_to_end(tmp_path):
    res = _rules_orch().run("set up reorder points and safety stock", data_path=PORTFOLIO,
                            client="Acme", out_dir=tmp_path)
    assert res.status == "ok"
    assert res.tool == "inventory_optimization"
    assert "excel" in res.deliverables and Path(res.deliverables["excel"]).exists()


def test_orchestrator_pricing_end_to_end(tmp_path):
    res = _rules_orch().run("what price maximizes profit", data_path=PRICING_CSV, out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "pricing"
    assert Path(res.deliverables["report"]).exists()


def test_orchestrator_leadership_via_params(tmp_path):
    res = _rules_orch().run("evaluate our SC leadership", overrides={"scores": "3 2 3 1 1", "name": "Equipo X"},
                            out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "leadership_chain"
    assert Path(res.deliverables["chart"]).exists()
    assert Path(res.deliverables["report"]).exists()


def test_orchestrator_needs_data_when_required_file_missing(tmp_path):
    res = _rules_orch().run("set up reorder points", out_dir=tmp_path)
    assert res.status == "needs_data" and res.tool == "inventory_optimization"


def test_orchestrator_needs_clarification_on_ambiguous_brief(tmp_path):
    res = _rules_orch().run("help me with my supply chain", out_dir=tmp_path)
    assert res.status == "needs_clarification"
    assert res.clarifications


def test_orchestrator_leadership_needs_clarification_without_scores(tmp_path):
    res = _rules_orch().run("how strong is our leadership?", out_dir=tmp_path)
    assert res.status == "needs_clarification"
    assert len(res.clarifications) >= 10


def test_orchestrator_qa_failed_writes_no_deliverables(tmp_path):
    orch = _rules_orch()
    tool = orch.registry.get("leadership_chain")
    # Tool is a frozen dataclass; bypass __setattr__ to force a QA failure
    # (same trick the existing jobs tests use on frozen records).
    object.__setattr__(tool, "qa", lambda report: ["forced issue"])
    res = orch.run("evaluate leadership", overrides={"scores": "3 2 3 1 1"}, out_dir=tmp_path)
    assert res.status == "qa_failed"
    assert res.qa_issues == ["forced issue"]
    assert res.deliverables == {}


def test_orchestrator_narrative_upgrade_with_provider(tmp_path):
    prov = _FakeProvider(complete_text="Upgraded narrative.", available=True)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=prov)
    res = orch.run("evaluate leadership", overrides={"scores": "3 2 3 1 1"}, job_type="leadership_chain",
                   out_dir=tmp_path)
    assert res.status == "ok"
    assert res.summary == "Upgraded narrative."


def test_package_exports():
    import scm_agent
    assert hasattr(scm_agent, "Orchestrator")
    assert hasattr(scm_agent, "JobRequest")
    assert hasattr(scm_agent, "JobResult")
    assert hasattr(scm_agent, "build_default_registry")
    assert hasattr(scm_agent, "get_provider")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_scm_agent.py -k "orchestrator or package_exports" -v`
Expected: FAIL — no `scm_agent.orchestrator`.

- [ ] **Step 3: Implement `scm_agent/orchestrator.py`**

```python
"""The orchestrator: brief + optional data -> routed, QA-gated deliverable."""

from __future__ import annotations

from pathlib import Path

from .intent import classify
from .llm import LLMProvider, get_provider
from .registry import ToolRegistry
from .tools import build_default_registry
from .types import (
    STATUS_ERROR,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobRequest,
    JobResult,
)


class Orchestrator:
    def __init__(self, registry: ToolRegistry | None = None, provider: LLMProvider | None = None) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        self.provider = provider if provider is not None else get_provider()

    def run(
        self,
        brief: str,
        *,
        data_path: str | None = None,
        overrides: dict | None = None,
        job_type: str | None = None,
        client: str = "Client",
        out_dir: str | Path = "deliverables/agent",
    ) -> JobResult:
        overrides = overrides or {}
        request = JobRequest(brief=brief, data_path=data_path, job_type=job_type,
                             params=dict(overrides), client=client)
        try:
            return self._run(request, Path(out_dir))
        except Exception as exc:  # never crash the caller — surface as error status
            return JobResult(status=STATUS_ERROR, tool=None, confidence=0.0,
                            deliverables={}, summary=f"{type(exc).__name__}: {exc}")

    def _run(self, request: JobRequest, out_dir: Path) -> JobResult:
        intent = classify(request.brief, self.registry, self.provider, job_type_override=request.job_type)
        if intent.job_type is None:
            return JobResult(
                status=STATUS_NEEDS_CLARIFICATION, tool=None, confidence=intent.confidence,
                deliverables={}, summary="Ambiguous request — pick a capability.",
                clarifications=intent.candidates,
            )

        tool = self.registry.get(intent.job_type)
        params = {**intent.params, **request.params}

        if tool.requires_data and not request.data_path:
            return JobResult(
                status=STATUS_NEEDS_DATA, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title} needs a data file.",
                clarifications=[f"provide a data file for {tool.title}"],
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

        written = tool.deliver(produced.report, out_dir / tool.key, request.client)
        summary = self._narrative(produced.summary, tool.title)
        return JobResult(
            status=STATUS_OK, tool=tool.key, confidence=intent.confidence,
            deliverables={name: str(path) for name, path in written.items()}, summary=summary,
        )

    def _narrative(self, base_summary: str, tool_title: str) -> str:
        """Optional LLM polish. Falls back silently to the deterministic summary."""
        if not self.provider.available():
            return base_summary
        try:
            text = self.provider.complete(
                f"Rewrite this {tool_title} result summary in one clear, client-ready sentence. "
                f"Keep every number. Return only the sentence.\n\n{base_summary}"
            )
        except Exception:
            return base_summary
        return text.strip() or base_summary
```

- [ ] **Step 4: Wire public exports in `scm_agent/__init__.py`**

Replace `scm_agent/__init__.py` with:
```python
"""scm_agent — the orchestrator spine: brief + data -> routed deliverable."""

from .llm import get_provider
from .orchestrator import Orchestrator
from .tools import build_default_registry
from .types import JobRequest, JobResult

__all__ = ["Orchestrator", "JobRequest", "JobResult", "build_default_registry", "get_provider"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_scm_agent.py -v`
Expected: PASS (entire file). The `qa_failed` test patches the frozen `Tool` via `object.__setattr__` (frozen dataclasses keep an instance `__dict__`, so this works — the same trick `tests/test_jobs.py` uses).

- [ ] **Step 6: Lint**

Run: `py -m ruff check scm_agent tests/test_scm_agent.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add scm_agent/orchestrator.py scm_agent/__init__.py tests/test_scm_agent.py
git commit -m "feat: add orchestrator spine with QA gate and narrative upgrade"
```

---

### Task 10: `examples/run_agent.py` — CLI

**Files:**
- Create: `examples/run_agent.py`
- Test: `tests/test_scm_agent.py`

**Interfaces:**
- Consumes: `Orchestrator` (package).
- Produces:
  - `build_parser() -> argparse.ArgumentParser`.
  - `main(argv: list[str] | None = None) -> int` — runs the orchestrator, prints a human summary, returns `0` on `ok`, else `1`. Imported in tests as `examples.run_agent`.
  - Flags: `--brief` (required), `--data`, `--job`, `--out`, `--client`, `--period`, `--service-level`, `--holding-rate`, `--order-cost`, `--budget`, `--cost-ratio`, `--scores`, `--name`. Non-None flag values become `overrides`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scm_agent.py`:
```python
import importlib


def test_cli_leadership_happy_path(tmp_path, capsys):
    run_agent = importlib.import_module("examples.run_agent")
    code = run_agent.main([
        "--brief", "evaluate our SC leadership", "--job", "leadership_chain",
        "--scores", "3 2 3 1 1", "--name", "Equipo X", "--out", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "leadership_chain" in out
    assert (tmp_path / "leadership_chain" / "chain_profile.png").exists()


def test_cli_needs_data_returns_nonzero(tmp_path):
    run_agent = importlib.import_module("examples.run_agent")
    code = run_agent.main(["--brief", "set up reorder points", "--out", str(tmp_path)])
    assert code == 1


def test_cli_inventory_happy_path(tmp_path):
    run_agent = importlib.import_module("examples.run_agent")
    code = run_agent.main(["--brief", "reorder points and safety stock",
                           "--data", PORTFOLIO, "--out", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "inventory_optimization" / "inventory_plan.xlsx").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_scm_agent.py -k "cli" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'examples.run_agent'`.

- [ ] **Step 3: Implement `examples/run_agent.py`**

```python
"""Run the SCM agent: a free-form brief (+ optional data) -> routed deliverable.

    python examples/run_agent.py --brief "set up reorder points" --data demand.csv
    python examples/run_agent.py --brief "what price maximizes profit" --data prices.csv
    python examples/run_agent.py --brief "evaluate our SC leadership" --scores "3 2 3 1 1" --name "Team"

Routing is automatic (override with --job). Runs with or without ANTHROPIC_API_KEY:
the deterministic core always works; an LLM only sharpens parsing and the summary.
"""

from __future__ import annotations

import argparse
import sys

from scm_agent import Orchestrator

# CLI flag -> overrides key, with the type to coerce to.
_PARAM_FLAGS: dict[str, type] = {
    "service_level": float,
    "holding_rate": float,
    "order_cost": float,
    "budget": float,
    "cost_ratio": float,
    "period": str,
    "scores": str,
    "name": str,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SCM agent — brief + optional data -> deliverable.")
    p.add_argument("--brief", required=True, help="free-form request")
    p.add_argument("--data", default=None, help="client CSV/Excel (for quantitative jobs)")
    p.add_argument("--job", default=None, help="force a capability key (skips classification)")
    p.add_argument("--out", default="deliverables/agent", help="output directory")
    p.add_argument("--client", default="Client", help="client name for the report")
    p.add_argument("--period", default=None, help="bucketing period (W/D/MS)")
    p.add_argument("--service-level", type=float, default=None)
    p.add_argument("--holding-rate", type=float, default=None)
    p.add_argument("--order-cost", type=float, default=None)
    p.add_argument("--budget", type=float, default=None)
    p.add_argument("--cost-ratio", type=float, default=None)
    p.add_argument("--scores", default=None, help="leadership scores 'C H A I N', e.g. '3 2 3 1 1'")
    p.add_argument("--name", default=None, help="who/what is evaluated (leadership)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {key: getattr(args, key) for key in _PARAM_FLAGS if getattr(args, key) is not None}

    result = Orchestrator().run(
        args.brief, data_path=args.data, overrides=overrides,
        job_type=args.job, client=args.client, out_dir=args.out,
    )

    print(f"[{result.status}] tool={result.tool} confidence={result.confidence:.2f}")
    print(result.summary)
    if result.deliverables:
        for name, path in result.deliverables.items():
            print(f"  {name:8s} -> {path}")
    if result.qa_issues:
        print("QA issues:", file=sys.stderr)
        for issue in result.qa_issues:
            print("  - " + issue, file=sys.stderr)
    if result.clarifications:
        print("Need more detail:")
        for c in result.clarifications:
            print("  - " + c)

    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -m pytest tests/test_scm_agent.py -k "cli" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add examples/run_agent.py tests/test_scm_agent.py
git commit -m "feat: add run_agent CLI for the orchestrator"
```

---

### Task 11: `webapp/app.py` — `POST /api/jobs`

Add a thin HTTP entry point over the orchestrator with downloadable deliverables. Multipart + file upload need `python-multipart`; tests guard for it.

**Files:**
- Modify: `webapp/app.py`
- Test: `tests/test_webapp.py`

**Interfaces:**
- Consumes: `Orchestrator` (package).
- Produces:
  - `POST /api/jobs` — multipart form: `brief` (required), `client` (default `"Client"`), `job_type` (optional), `params` (JSON string, default `"{}"`), `file` (optional upload). Returns the `JobResult` as JSON plus a `download_urls: dict[str, str]` mapping each deliverable to a `/jobs-output/...` URL.
  - A static mount `/jobs-output` over `webapp/_jobs_output/` (created at import; git-ignored).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_webapp.py`. **Do not** call `pytest.importorskip` at module level here — it would skip the existing 13 webapp tests when `python-multipart` is absent. Use a per-test `skipif` marker instead:
```python
try:
    import multipart  # noqa: F401  (python-multipart, classic import name)
    _HAS_MULTIPART = True
except ImportError:
    try:
        import python_multipart  # noqa: F401  (renamed in newer releases)
        _HAS_MULTIPART = True
    except ImportError:
        _HAS_MULTIPART = False

requires_multipart = pytest.mark.skipif(not _HAS_MULTIPART, reason="python-multipart not installed")


@requires_multipart
def test_jobs_leadership_via_params_no_file():
    r = client.post("/api/jobs", data={
        "brief": "evaluate our SC leadership",
        "job_type": "leadership_chain",
        "params": '{"scores": "3 2 3 1 1", "name": "Equipo X"}',
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["tool"] == "leadership_chain"
    assert "chart" in body["deliverables"]
    assert body["download_urls"]["chart"].startswith("/jobs-output/")


@requires_multipart
def test_jobs_inventory_with_file_upload():
    with open("data/sample_demand_portfolio.csv", "rb") as fh:
        r = client.post(
            "/api/jobs",
            data={"brief": "set up reorder points and safety stock"},
            files={"file": ("demand.csv", fh, "text/csv")},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["tool"] == "inventory_optimization"
    assert "excel" in body["download_urls"]


@requires_multipart
def test_jobs_needs_data_status():
    r = client.post("/api/jobs", data={"brief": "set up reorder points"})
    assert r.status_code == 200
    assert r.json()["status"] == "needs_data"


@requires_multipart
def test_jobs_downloaded_file_is_served():
    r = client.post("/api/jobs", data={
        "brief": "evaluate leadership", "job_type": "leadership_chain",
        "params": '{"scores": "3 3 3 3 3"}',
    }).json()
    url = r["download_urls"]["report"]
    got = client.get(url)
    assert got.status_code == 200
    assert "CHAIN" in got.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -m pytest tests/test_webapp.py -k "jobs" -v`
Expected: FAIL (404 on `/api/jobs`) — or SKIP if `python-multipart` isn't installed. If skipped, `py -m pip install python-multipart` first, then re-run to see the real failure.

- [ ] **Step 3: Extend `webapp/app.py`**

Add these imports to the existing `noqa: E402` import block (after the FastAPI imports):
```python
from fastapi import File, Form, UploadFile  # noqa: E402

from scm_agent import Orchestrator  # noqa: E402
```

Add near the other module constants (after `STATIC_DIR`):
```python
JOBS_OUTPUT_DIR = _REPO_ROOT / "webapp" / "_jobs_output"
JOBS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_ORCHESTRATOR: Orchestrator | None = None


def _get_orchestrator() -> Orchestrator:
    global _ORCHESTRATOR
    if _ORCHESTRATOR is None:
        _ORCHESTRATOR = Orchestrator()
    return _ORCHESTRATOR
```

Add the endpoint (before the `@app.get("/")` index route):
```python
@app.post("/api/jobs")
async def api_jobs(
    brief: str = Form(...),
    client: str = Form("Client"),
    job_type: str | None = Form(None),
    params: str = Form("{}"),
    file: UploadFile | None = File(None),
) -> dict:
    try:
        parsed_params = json.loads(params) if params else {}
        if not isinstance(parsed_params, dict):
            raise ValueError("params must be a JSON object")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid params JSON: {exc}") from exc

    import tempfile

    job_dir = Path(tempfile.mkdtemp(dir=JOBS_OUTPUT_DIR))
    data_path: str | None = None
    if file is not None and file.filename:
        upload = job_dir / file.filename
        upload.write_bytes(await file.read())
        data_path = str(upload)

    result = _get_orchestrator().run(
        brief, data_path=data_path, overrides=parsed_params,
        job_type=job_type or None, client=client, out_dir=job_dir,
    )

    download_urls: dict[str, str] = {}
    for name, path in result.deliverables.items():
        rel = Path(path).resolve().relative_to(JOBS_OUTPUT_DIR.resolve())
        download_urls[name] = "/jobs-output/" + rel.as_posix()

    return {
        "status": result.status,
        "tool": result.tool,
        "confidence": result.confidence,
        "summary": result.summary,
        "deliverables": result.deliverables,
        "download_urls": download_urls,
        "qa_issues": result.qa_issues,
        "clarifications": result.clarifications,
    }
```

Add the static mount next to the existing `/static` mount at the bottom:
```python
app.mount("/jobs-output", StaticFiles(directory=str(JOBS_OUTPUT_DIR)), name="jobs-output")
```

- [ ] **Step 4: Ignore the runtime output dir**

Append to `.gitignore` (create the line if missing):
```
webapp/_jobs_output/
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `py -m pytest tests/test_webapp.py -v`
Expected: PASS (existing portfolio tests + new job tests). Confirm the existing 13 webapp tests still pass.

- [ ] **Step 6: Lint**

Run: `py -m ruff check webapp/app.py tests/test_webapp.py`
Expected: clean. (`import tempfile` is inside the function deliberately — keep module top-level imports unchanged otherwise.)

- [ ] **Step 7: Commit**

```bash
git add webapp/app.py tests/test_webapp.py .gitignore
git commit -m "feat: add POST /api/jobs orchestrator endpoint with downloads"
```

---

### Task 12: Optional extras, docs, version bump, full gate, push

**Files:**
- Modify: `pyproject.toml`, `requirements.txt` (optional note), `README.md`, `CHANGELOG.md`
- Create: `scm_agent/README.md`

**Interfaces:** none (release task).

- [ ] **Step 1: Declare optional dependencies + bump version in `pyproject.toml`**

Set `version = "2.8.0"` in `[project]`. Add LLM/web extras to `[project.optional-dependencies]` (keep the existing `dev` block):
```toml
[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
    "ruff>=0.4",
]
llm = [
    "anthropic>=0.39",
]
web = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "python-multipart>=0.0.9",
]
```

- [ ] **Step 2: Write `scm_agent/README.md`**

```markdown
# scm_agent — the orchestrator

One entry point that turns a free-form brief (+ optional data) into a finished
deliverable, routing to the right capability.

## Capabilities

| Key | Type | Input | Deliverable |
|---|---|---|---|
| `inventory_optimization` | quantitative | demand CSV/Excel | Excel + report + CSV |
| `pricing` | quantitative | price/quantity CSV/Excel | Excel + report + CSV |
| `leadership_chain` | qualitative | brief / `scores` | radar chart PNG + active report |

## CLI

```bash
py examples/run_agent.py --brief "set up reorder points" --data data/sample_demand_portfolio.csv
py examples/run_agent.py --brief "what price maximizes profit" --data data/sample_pricing.csv
py examples/run_agent.py --brief "evaluate our SC leadership" --scores "3 2 3 1 1" --name "Team"
```

## HTTP

`POST /api/jobs` (multipart: `brief`, optional `file`, `params` JSON) → `JobResult`
JSON + `download_urls`. Needs the `web` extra (`pip install -e ".[web]"`).

## LLM (optional)

Set `ANTHROPIC_API_KEY` and install the `llm` extra to enable Claude-assisted intent
parsing and narrative polish. Without it the deterministic core runs unchanged.

## Design

Registry-based: each capability is a `Tool` with four stages
(`prepare → run → qa → deliver`) the `Orchestrator` drives, enforcing
"QA fails ⇒ no deliverable" centrally. Spec:
`docs/superpowers/specs/2026-06-21-scm-agent-orchestrator-design.md`.

The `leadership_chain` capability wraps the CHAIN model. *Síntesis original
inspirada en el modelo CHAIN de "From Source to Sold" (Palamariu & Alicke, 2022);
no reproduce el texto del libro.*
```

- [ ] **Step 3: Update `README.md` and `CHANGELOG.md`**

In `README.md`, add an "Agent (scm_agent)" section near the jobs/webapp sections pointing at `scm_agent/README.md` and the three CLI commands from Step 2.

In `CHANGELOG.md`, add a `## [2.8.0] - 2026-06-21` entry:
```markdown
## [2.8.0] - 2026-06-21

### Added
- `scm_agent` orchestrator: routes a free-form brief (+ optional data) to a capability and drives prepare → run → QA → deliver.
- `leadership_chain` capability (CHAIN model): score + radar chart + active directives; `jobs/leadership.py`.
- Pluggable `LLMProvider` (Claude when `ANTHROPIC_API_KEY` is set, rules fallback otherwise).
- CLI `examples/run_agent.py` and `POST /api/jobs` HTTP endpoint with downloadable deliverables.
- Optional `llm` and `web` dependency extras.
```

- [ ] **Step 4: Full gate — tests, coverage, lint**

Run:
```bash
py -m pytest -q --cov=src --cov-fail-under=80
py -m ruff check src jobs tests examples scripts webapp scm_agent
```
Expected: all tests pass (the original 132 + the new leadership/scm_agent/webapp tests); `src` coverage ≥ 80%; ruff clean. Fix anything red before committing.

- [ ] **Step 5: Acceptance smoke test (matches spec §10)**

Run (with and, if a key is configured, without `ANTHROPIC_API_KEY`):
```bash
py examples/run_agent.py --brief "set up reorder points" --data data/sample_demand_portfolio.csv --out /tmp/agent1
py examples/run_agent.py --brief "what price maximizes profit" --data data/sample_pricing.csv --out /tmp/agent2
py examples/run_agent.py --brief "evaluate our SC leadership" --scores "3 2 3 1 1" --out /tmp/agent3
py examples/run_agent.py --brief "evaluate our SC leadership"  # expect needs_clarification (no scores, no LLM)
```
Expected: first three print `[ok]` with deliverable paths (inventory Excel+report; pricing Excel+report; leadership `chain_profile.png` + report); the fourth prints `[needs_clarification]` with the diagnostic questions and exits non-zero.

- [ ] **Step 6: Commit and push**

```bash
git add pyproject.toml README.md CHANGELOG.md scm_agent/README.md
git commit -m "chore: release 2.8.0 — scm_agent orchestrator + leadership_chain"
git push
```

(If on `main` and your workflow forbids direct pushes, branch first: `git switch -c feat/scm-agent-orchestrator` before pushing, then open a PR.)

---

## Self-review notes (author check)

- **Spec coverage:** §2 decisions → hybrid LLM (Task 3), registry routing (Task 6), entry points CLI/API (Tasks 10–11); §4 components → types/llm/registry/intent/orchestrator (Tasks 2,3,6,8,9), tools (Task 7); §5 three capabilities + leadership Mode A score/chart/directives (Tasks 4,5,7); skill install + `--chart` (Task 1); §6 statuses `ok`/`needs_clarification`/`needs_data`/`qa_failed`/`error` (Task 9, types Task 2); §7 testing without a real LLM via `RulesFallback`/`_FakeProvider` (Tasks 3,7,8,9); §8 scope honored (single-tool routing, no memory/multi-step); §9 build order followed (with tool builders split into `tools.py` — a clean refinement of "register in registry.py"); §10 acceptance → Task 12 Step 5.
- **Type consistency:** `Prepared.status`/`Produced`/`Tool` callable signatures are used identically in tools (Task 7) and orchestrator (Task 9); `coerce_scores`/`score_profile`/`write_all` names match across Tasks 4,5,7; `JobResult` field names match across Tasks 2,9,10,11.
- **LLM branches under test (§7):** intent fallback (Task 8 `test_classify_uses_llm_*`), leadership scoring (Task 8 `test_leadership_tool_scores_via_llm_provider`), and narrative upgrade (Task 9 `test_orchestrator_narrative_upgrade_*`) — all via `_FakeProvider`, no network. `_FakeProvider` is defined in Task 8; tests that use it live in Tasks 8–9 (never earlier), so each task's suite passes with only the code written by that point.
- **Intent keyword collision (fixed during review):** bare `"chain"` was removed from `leadership_chain` keywords because it matches `"supply chain"` in nearly every brief in this domain; routing relies on `"leadership"`/`"liderazgo"`/`"chain model"` etc. instead. The ambiguous-brief tests use a keyword-free brief.
- **Deferred-fill note:** Task 4 ships `PRACTICES`/`QUESTIONS` as non-placeholder empty maps and Task 5 fills them; Task-4 tests intentionally don't assert on directives. This is a real, compiling intermediate state, not a TODO.
- **LLM network path** (`ClaudeProvider.complete`) is unverified by tests by design; Task 3 Step 5 adds an explicit SDK-shape confirmation step.
