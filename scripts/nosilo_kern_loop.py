#!/usr/bin/env python
"""nosilo / kern loop - bucle continuo de integracion.

Mantiene viva la integracion entre nosilo_core (el plano de control agnostico)
y kern (el agente de cadena de suministro): detecta desvios, los puntua contra
medidas reales y propone arreglos mecanicos.

Uso:
    PYTHONPATH=. python scripts/nosilo_kern_loop.py --check   # DETECT solo, segundos
    PYTHONPATH=. python scripts/nosilo_kern_loop.py           # + bateria, ~2 min
    PYTHONPATH=. python scripts/nosilo_kern_loop.py --full    # + capacidades + simworld
    PYTHONPATH=. python scripts/nosilo_kern_loop.py --fix     # propone arreglos mecanicos

Fases: DETECT -> SCORE -> DIAGNOSE -> PROPOSE -> REPORT.

La regla que ordena todo el archivo: **un componente que no corrio no puntua, y
un puntaje al que le falta un componente se publica como PARCIAL, con su escala
a la vista**. La version anterior publicaba 10.0/10 sin correr una sola medida:
el smoke apuntaba a un `tests/test_smoke.py` que no existe, el simworld corria
`pytest` sobre un script que no colecta tests, y la bateria nunca se invocaba.
El puntaje subia por omision -- que es exactamente lo que un loop de integracion
no puede hacer, porque el 10/10 se lee como "todo bien" y significaba "no medi".

Por eso tampoco hay tendencia entre corridas con distinto conjunto de
componentes: comparar un `--check` contra un `--full` da un delta inventado.

Principios:
  - Nunca auto-mergea, nunca push a main.
  - Fixes mecanicos via TDD: test reproduce -> fix -> verify.
  - Zonas excluidas (writeback, connectors, secrets): se reportan, nunca se tocan.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_LOOPS = REPO_ROOT / "data" / "loops"
REPORT_PATH = REPO_ROOT / "documentation" / "loops" / "NO_SILO_KERN_REPORT.md"
STATE_PATH = DATA_LOOPS / "nosilo_kern_state.json"

#: Salida del simworld. Cae bajo `deliverables/`, que esta en .gitignore: la
#: corrida es reproducible desde la semilla, no hay que versionar el artefacto.
SIMWORLD_OUT = REPO_ROOT / "deliverables" / "simworld-loop"

STALE_IMPORT_PATTERNS = {
    "src.guided": "nosilo_core.guided",
    "src.escalation": "nosilo_core.escalation",
    "src.writeback_store": "nosilo_core.writeback_store",
    "src.writeback": "nosilo_core.writeback",
    "scm_agent.registry": "nosilo_core.registry",
    "scm_agent.types": "nosilo_core.types",
    "scm_agent.llm": "nosilo_core.llm",
    "scm_agent.guided": "nosilo_core.guided",
    "scm_agent.escalation": "nosilo_core.escalation",
    "scm_agent.writeback": "nosilo_core.writeback",
}

#: Rutas que el loop reporta pero nunca reescribe. La exclusion vive en la fase
#: PROPOSE, no en DETECT: queremos *ver* el desvio en writeback o en un conector,
#: solo no queremos que un arreglo automatico lo toque.
EXCLUSION_PREFIXES = (
    "src/writeback.py",
    "src/writeback_store.py",
    "src/connectors/",
    "nosilo_core/",
    "src/mcp_keys.py",
    "webapp/security.py",
    "webapp/principal.py",
    "webapp/mcp_auth.py",
    "pyproject.toml",
    "requirements",
    "Dockerfile",
)

CORE_CONSUMERS = (
    "scm_agent/orchestrator.py",
    "scm_agent/packages.py",
    "scm_agent/guided_bridge.py",
    "scm_agent/tool_options.py",
    "scm_agent/intent.py",
    "scm_agent/modes.py",
    "scm_agent/__init__.py",
    "jobs/abc_xyz_job.py",
    "jobs/forecast_job.py",
    "jobs/flow_map_job.py",
    "jobs/intake_scan_job.py",
    "jobs/launch_readiness_job.py",
    "jobs/cross_impact_job.py",
    "jobs/odoo_job.py",
    "jobs/data_quality_job.py",
    "jobs/returns_job.py",
    "jobs/risk_job.py",
    "jobs/disruption_scan_job.py",
    "jobs/hats_job.py",
    "jobs/qa.py",
    "src/alerting.py",
)

#: Escala chica y con semilla fija. No es representativa (a 8 SKUs la captura
#: oscila decenas de puntos entre niveles) pero SI es reproducible, y para lo
#: que se usa -- detectar que el camino de politica cambio -- alcanza.
SIMWORLD_BASE = {
    "seed": 4,
    "n_skus": 8,
    "n_suppliers": 2,
    "weeks": 12,
    "levels": (0, 1),
    "lanes": ("forced",),
}

#: Cuanto puede moverse la captura sin que se considere regresion.
TOLERANCIA_CAPTURA = 0.01

PESO_BATERIA = 6.0
PESO_CAPACIDADES = 3.0
PESO_HIGIENE = 1.0

TEST_CAPACIDADES = "tests/test_capability_smoke.py"


@dataclass
class DetectIssue:
    kind: str
    severity: str
    message: str
    file: str = ""
    line: int = 0
    suggested_fix: str | None = None


@dataclass
class Componente:
    """Un componente del puntaje. ``obtenido is None`` significa que no corrio,
    y un componente que no corrio nunca aporta puntos ni escala."""

    nombre: str
    peso: float
    obtenido: float | None
    detalle: str
    #: La medida cruda detras del puntaje (p.ej. 45.0 = % de bateria), para que
    #: el estado guarde la cifra y no solo su traduccion a puntos.
    dato: float | None = None

    @property
    def corrio(self) -> bool:
        return self.obtenido is not None


@dataclass
class Puntaje:
    componentes: tuple[Componente, ...]

    @property
    def corridos(self) -> tuple[Componente, ...]:
        return tuple(c for c in self.componentes if c.corrio)

    @property
    def obtenido(self) -> float:
        return round(sum(c.obtenido or 0.0 for c in self.corridos), 2)

    @property
    def escala(self) -> float:
        """El denominador real: solo el peso de lo que efectivamente corrio."""
        return round(sum(c.peso for c in self.corridos), 2)

    @property
    def parcial(self) -> bool:
        return len(self.corridos) != len(self.componentes)

    @property
    def firma(self) -> str:
        """Que componentes entraron. Dos corridas con firma distinta no se comparan."""
        return "+".join(sorted(c.nombre for c in self.corridos)) or "vacio"


@dataclass
class LoopResult:
    run_id: str
    timestamp: str
    puntaje: Puntaje
    previo: dict | None
    issues: list[DetectIssue] = field(default_factory=list)
    capturas: dict[str, float] = field(default_factory=dict)
    main_commit: str = ""
    duration_ms: int = 0


#: Codigos de salida de pytest que significan "no se midio nada". Se separan de
#: "midio y fallo" a proposito: son el modo de falla que tenia la version previa
#: de este script (apuntaba a un test inexistente -> 4) y el que se lleva un
#: `-k` mal escrito (-> 5). Los dos son silenciosos si uno solo mira el texto.
PYTEST_NO_MIDIO = {4: "error de uso (ruta inexistente?)", 5: "no colecto ningun test"}


def run_cmd(args: list[str], timeout: int = 30) -> tuple[int, str]:
    """Devuelve ``(returncode, salida)``.

    El codigo importa mas que el texto: la salida de pytest arrastra warnings de
    pandas con literales como ``errors="coerce"``, asi que buscar `" error"` en
    el cuerpo da falsos positivos (verificado: 5 passed marcados como falla).
    """
    try:
        r = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except FileNotFoundError:
        return -2, "COMMAND_NOT_FOUND"


def commit_de_main() -> str:
    rc, out = run_cmd(["git", "rev-parse", "--short", "main"])
    out = out.strip()
    return out if rc == 0 and 0 < len(out) <= 12 else "desconocido"


def load_state() -> dict:
    """Estado previo, o uno vacio. Un archivo corrupto no puede voltear el loop:
    se archiva al lado y se arranca de cero, porque la alternativa es que una
    escritura interrumpida deje el bucle muerto hasta que alguien lo note."""
    if not STATE_PATH.exists():
        return {"runs": []}
    try:
        estado = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        STATE_PATH.replace(STATE_PATH.with_suffix(".corrupto.json"))
        print(f"[loop] estado ilegible; archivado en {STATE_PATH.with_suffix('.corrupto.json').name}")
        return {"runs": []}
    return estado if isinstance(estado, dict) else {"runs": []}


def save_state(state: dict) -> None:
    DATA_LOOPS.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def es_zona_excluida(ruta: str) -> bool:
    return any(ruta.startswith(p) for p in EXCLUSION_PREFIXES)


# --------------------------------------------------------------------------
# DETECT
# --------------------------------------------------------------------------

def detectar_imports_viejos() -> list[DetectIssue]:
    issues: list[DetectIssue] = []
    for consumer in CORE_CONSUMERS:
        fpath = REPO_ROOT / consumer
        if not fpath.exists():
            issues.append(DetectIssue(
                kind="consumidor_faltante", severity="high",
                message=f"Consumidor de core ausente: {consumer}", file=consumer,
            ))
            continue
        contenido = fpath.read_text(encoding="utf-8")
        for viejo, nuevo in STALE_IMPORT_PATTERNS.items():
            if viejo not in contenido or nuevo in contenido:
                continue
            for i, linea in enumerate(contenido.splitlines(), 1):
                if viejo in linea and "import" in linea:
                    issues.append(DetectIssue(
                        kind="import_viejo", severity="high",
                        message=f"Import viejo: {viejo} deberia ser {nuevo}",
                        file=consumer, line=i,
                        suggested_fix=f"Reemplazar {viejo} por {nuevo}",
                    ))
    return issues


# --------------------------------------------------------------------------
# SCORE
# --------------------------------------------------------------------------

def puntuar_higiene(issues: list[DetectIssue]) -> Componente:
    penalidad = 0.0
    for issue in issues:
        penalidad += {"critical": 1.5, "high": 1.0, "medium": 0.5}.get(issue.severity, 0.1)
    obtenido = max(0.0, PESO_HIGIENE - penalidad)
    return Componente(
        nombre="higiene", peso=PESO_HIGIENE, obtenido=round(obtenido, 2),
        detalle=f"{len(issues)} desvios estaticos",
    )


def puntuar_bateria(issues: list[DetectIssue]) -> Componente:
    """La bateria de 20 tareas, normalizada contra su techo estructural (75%).

    Normaliza contra el techo y no contra 100 a proposito: 5 de las 20 tareas
    estan marcadas TAREA HUMANA, asi que ningun software pasa de 75%. Puntuar
    sobre 100 dejaria al loop con un maximo alcanzable de 7,5/10 para siempre y
    el numero no querria decir nada.
    """
    try:
        import src.bateria.comprobaciones  # noqa: F401  registra las comprobaciones
        from src.bateria.scorer import puntuar
    except Exception as exc:  # dependencia ausente (p.ej. nosilo_core sin token)
        issues.append(DetectIssue(
            kind="bateria_no_importa", severity="critical",
            message=f"No se pudo importar la bateria: {type(exc).__name__}: {exc}",
            file="src/bateria/scorer.py",
            suggested_fix="Verificar que nosilo_core este instalado en este entorno",
        ))
        return Componente("bateria", PESO_BATERIA, None, "no corrio: fallo el import")

    try:
        p = puntuar()
    except Exception as exc:
        issues.append(DetectIssue(
            kind="bateria_crashea", severity="critical",
            message=f"La bateria crasheo: {type(exc).__name__}: {exc}",
            file="src/bateria/scorer.py",
        ))
        return Componente("bateria", PESO_BATERIA, None, "no corrio: crasheo")

    techo = p.techo_segun_fuente or 100.0
    obtenido = PESO_BATERIA * min(1.0, p.porcentaje / techo)
    if p.cobertura < 1.0:
        issues.append(DetectIssue(
            kind="bateria_sin_cobertura", severity="medium",
            message=f"Cobertura de comprobaciones {p.cobertura:.0%}: hay tareas PENDIENTE",
            file="src/bateria/comprobaciones.py",
        ))
    return Componente(
        nombre="bateria", peso=PESO_BATERIA, obtenido=round(obtenido, 2),
        detalle=f"{p.porcentaje:.1f}% ({p.puntos}/20) sobre techo {techo:.0f}%",
        dato=round(p.porcentaje, 1),
    )


def puntuar_capacidades(issues: list[DetectIssue]) -> Componente:
    """El capability smoke real: 5 tests sobre los 49 tools registrados.

    Binario a proposito. El test no tiene un piso numerico sino una tabla de
    bucket esperado por tool, asi que "casi pasa" no es un estado que exista.
    """
    if not (REPO_ROOT / TEST_CAPACIDADES).exists():
        issues.append(DetectIssue(
            kind="smoke_ausente", severity="critical",
            message=f"El smoke de capacidades no existe: {TEST_CAPACIDADES}",
            file=TEST_CAPACIDADES,
        ))
        return Componente("capacidades", PESO_CAPACIDADES, None, "no corrio: falta el archivo")

    rc, salida = run_cmd(
        [sys.executable, "-m", "pytest", TEST_CAPACIDADES, "-q", "--tb=line"], timeout=600)
    resumen = next(
        (ln.strip() for ln in reversed(salida.splitlines()) if " passed" in ln or " failed" in ln),
        salida.strip().splitlines()[-1] if salida.strip() else "sin salida",
    )

    if rc < 0:
        issues.append(DetectIssue(
            kind="smoke_no_corre", severity="high",
            message=f"El smoke de capacidades no pudo correr: {salida}", file=TEST_CAPACIDADES,
        ))
        return Componente("capacidades", PESO_CAPACIDADES, None, f"no corrio: {salida}")

    if rc in PYTEST_NO_MIDIO:
        issues.append(DetectIssue(
            kind="smoke_no_midio", severity="critical",
            message=f"El smoke de capacidades no midio nada: {PYTEST_NO_MIDIO[rc]} (pytest rc={rc})",
            file=TEST_CAPACIDADES,
            suggested_fix="Verificar que la ruta del test exista y coleccione casos",
        ))
        return Componente("capacidades", PESO_CAPACIDADES, None, f"no corrio: {PYTEST_NO_MIDIO[rc]}")

    if rc != 0:
        issues.append(DetectIssue(
            kind="smoke_falla", severity="critical",
            message=f"El smoke de capacidades falla (pytest rc={rc}): {resumen}",
            file=TEST_CAPACIDADES,
        ))
        return Componente("capacidades", PESO_CAPACIDADES, 0.0, f"falla: {resumen}")
    return Componente("capacidades", PESO_CAPACIDADES, PESO_CAPACIDADES, resumen)


# --------------------------------------------------------------------------
# DIAGNOSE: simworld como alarma de regresion, no como puntaje
# --------------------------------------------------------------------------

def correr_simworld(issues: list[DetectIssue], base: dict[str, float]) -> dict[str, float]:
    """Corre la grilla chica y compara contra la captura registrada.

    No entra al puntaje. A esta escala la captura oscila decenas de puntos entre
    niveles (medido: -44,6% en L0 y +43,7% en L1 sobre el mismo mundo), asi que
    su nivel absoluto no dice si Kern esta bien. Lo que si dice, por ser una
    corrida con semilla fija, es si el camino de politica cambio: mismo mundo y
    otra captura significa que algo se movio aguas arriba.
    """
    try:
        from examples.run_world_sim import run_grid
    except Exception as exc:
        issues.append(DetectIssue(
            kind="simworld_no_importa", severity="high",
            message=f"No se pudo importar el simworld: {type(exc).__name__}: {exc}",
            file="examples/run_world_sim.py",
        ))
        return {}

    try:
        reporte = run_grid(out_dir=SIMWORLD_OUT, **SIMWORLD_BASE)
    except Exception as exc:
        issues.append(DetectIssue(
            kind="simworld_crashea", severity="high",
            message=f"El simworld crasheo: {type(exc).__name__}: {exc}",
            file="examples/run_world_sim.py",
        ))
        return {}

    capturas = {
        nombre: entrada["capture"]
        for nombre, entrada in reporte.get("kern", {}).items()
        if entrada.get("capture") is not None
    }

    for nombre, valor in capturas.items():
        anterior = base.get(nombre)
        if anterior is None:
            continue
        delta = valor - anterior
        if abs(delta) > TOLERANCIA_CAPTURA:
            issues.append(DetectIssue(
                kind="simworld_regresion", severity="medium",
                message=(f"Captura de {nombre} se movio {delta:+.3f} "
                         f"({anterior:.3f} -> {valor:.3f}) con la misma semilla"),
                file="src/simworld/runners.py",
                suggested_fix="Revisar que cambio en el camino de politica desde la corrida anterior",
            ))
    return capturas


# --------------------------------------------------------------------------
# PROPOSE
# --------------------------------------------------------------------------

def proponer_arreglos(issues: list[DetectIssue]) -> None:
    mecanicos = [i for i in issues if i.kind == "import_viejo" and i.suggested_fix]
    if not mecanicos:
        print("[fix] no hay arreglos mecanicos que proponer")
        return
    for issue in mecanicos:
        if es_zona_excluida(issue.file):
            print(f"[fix] OMITIDO (zona excluida) {issue.file}:{issue.line} - {issue.message}")
            continue
        print(f"[fix] {issue.file}:{issue.line} - {issue.suggested_fix}")
    print("[fix] propuestas solamente: este script nunca reescribe archivos.")


# --------------------------------------------------------------------------
# REPORT
# --------------------------------------------------------------------------

def generar_reporte(r: LoopResult) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    p = r.puntaje
    L = [
        "# Nosilo KERN Loop Report",
        "",
        f"**Corrida:** {r.run_id}",
        f"**Timestamp:** {r.timestamp}",
        f"**main:** `{r.main_commit}`",
        f"**Duracion:** {r.duration_ms} ms",
        "",
        "## Puntaje",
        "",
    ]
    if p.parcial:
        no_corrieron = ", ".join(c.nombre for c in p.componentes if not c.corrio)
        L += [f"> **PARCIAL** - no corrieron: {no_corrieron}. "
              f"El puntaje va sobre {p.escala:.1f}, no sobre 10.", ""]
    L += [f"- **{p.obtenido:.2f} / {p.escala:.1f}**" + ("" if p.parcial else "  (completo)"), ""]

    trend = "sin base comparable"
    if r.previo and r.previo.get("firma") == p.firma and r.previo.get("obtenido") is not None:
        trend = f"{p.obtenido - r.previo['obtenido']:+.2f} vs {r.previo['run_id']}"
    elif r.previo:
        trend = f"no comparable (corrida previa: {r.previo.get('firma', '?')})"
    L += [f"- Tendencia: {trend}", ""]

    L += ["| Componente | Peso | Obtenido | Detalle |", "|---|---|---|---|"]
    for c in p.componentes:
        obt = f"{c.obtenido:.2f}" if c.corrio else "— no corrio"
        L.append(f"| {c.nombre} | {c.peso:.1f} | {obt} | {c.detalle} |")
    L.append("")

    if r.capturas:
        L += ["## Simworld (alarma de regresion, no puntua)", ""]
        for nombre, valor in sorted(r.capturas.items()):
            L.append(f"- `{nombre}`: captura {valor:+.3f}")
        L += ["", f"Config con semilla fija: {json.dumps(SIMWORLD_BASE, default=list)}", ""]

    altas = [i for i in r.issues if i.severity in ("critical", "high")]
    L += ["## Desvios", "", f"Total {len(r.issues)} — criticos/altos {len(altas)}", ""]
    for issue in r.issues:
        L.append(f"- **[{issue.severity.upper()}]** `{issue.kind}`: {issue.message}")
        if issue.file:
            ubic = f"`{issue.file}`" + (f":{issue.line}" if issue.line else "")
            L.append(f"  - Donde: {ubic}" + ("  _(zona excluida: solo se reporta)_" if es_zona_excluida(issue.file) else ""))
        if issue.suggested_fix:
            L.append(f"  - Arreglo: {issue.suggested_fix}")
    if not r.issues:
        L.append("Ninguno.")
    L.append("")
    REPORT_PATH.write_text("\n".join(L), encoding="utf-8")


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="loop de integracion nosilo / kern")
    ap.add_argument("--check", action="store_true", help="DETECT solo (segundos), sin bateria")
    ap.add_argument("--fix", action="store_true", help="proponer arreglos mecanicos (no escribe)")
    ap.add_argument("--full", action="store_true", help="agrega capability smoke y simworld")
    args = ap.parse_args()

    inicio = time.time()
    estado = load_state()
    previo = estado["runs"][-1] if estado.get("runs") else None
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    issues = detectar_imports_viejos()

    componentes = [puntuar_higiene(issues)]
    if args.check:
        componentes += [
            Componente("bateria", PESO_BATERIA, None, "no corrio: modo --check"),
            Componente("capacidades", PESO_CAPACIDADES, None, "no corrio: modo --check"),
        ]
    else:
        componentes.append(puntuar_bateria(issues))
        if args.full:
            componentes.append(puntuar_capacidades(issues))
        else:
            componentes.append(
                Componente("capacidades", PESO_CAPACIDADES, None, "no corrio: requiere --full"))

    capturas = correr_simworld(issues, estado.get("last_capturas") or {}) if args.full else {}

    if args.fix:
        proponer_arreglos(issues)

    puntaje = Puntaje(componentes=tuple(componentes))
    resultado = LoopResult(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        puntaje=puntaje,
        previo=previo,
        issues=issues,
        capturas=capturas,
        main_commit=commit_de_main(),
        duration_ms=int((time.time() - inicio) * 1000),
    )
    generar_reporte(resultado)

    marca = " (PARCIAL)" if puntaje.parcial else ""
    altas = [i for i in issues if i.severity in ("critical", "high")]
    print(f"[loop] Puntaje: {puntaje.obtenido:.2f}/{puntaje.escala:.1f}{marca}  "
          f"desvios: {len(issues)} (criticos/altos {len(altas)})  "
          f"{resultado.duration_ms} ms")
    for c in puntaje.componentes:
        marca_c = "--     " if not c.corrio else f"{c.obtenido:.2f}/{c.peso:.1f}"
        print(f"[loop]   {c.nombre:<12} {marca_c}  {c.detalle}")
    for nombre, valor in sorted(capturas.items()):
        print(f"[loop]   simworld {nombre:<12} captura {valor:+.3f}  (no puntua)")
    print(f"[loop] Reporte: {REPORT_PATH}")

    bateria = next((c for c in componentes if c.nombre == "bateria" and c.corrio), None)
    estado.setdefault("runs", []).append({
        "run_id": run_id,
        "ts": resultado.timestamp,
        "firma": puntaje.firma,
        "obtenido": puntaje.obtenido,
        "escala": puntaje.escala,
        "parcial": puntaje.parcial,
        "main_commit": resultado.main_commit,
        "bateria_pct": bateria.dato if bateria else None,
        "bateria_detalle": bateria.detalle if bateria else None,
        "capturas": capturas,
        "issues": len(issues),
    })
    estado["last_run_at"] = resultado.timestamp
    estado["last_main_commit"] = resultado.main_commit
    #: Solo se pisan cuando la medida efectivamente corrio: un `--check` no debe
    #: borrar la ultima bateria conocida ni la ultima captura registrada.
    if bateria:
        estado["last_bateria_pct"] = bateria.dato
    if capturas:
        estado["last_capturas"] = capturas
    save_state(estado)

    sys.exit(1 if altas else 0)


if __name__ == "__main__":
    main()
