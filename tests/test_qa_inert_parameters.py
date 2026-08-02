"""T6 - inert parameter lint, scoped by evidence.

A parameter a public function declares and never references is worse than a missing
one: it invites a caller to close a wiring gap by passing a value that does nothing.
periods_per_year on continuous_review_sq was exactly that (delta 0.0), and
tests/test_policies_no_dead_knob.py guards that one case. This generalises it.

RAW PASS FIRST (the reason this check is scoped rather than blanket): over all of
src/ the naive rule flags 24 functions, and 17 of them are Protocol / ABC / stub
declarations whose bodies are `...`, `pass` or `raise NotImplementedError`. There the
parameters ARE the interface and cannot be referenced -- flagging them is pure noise,
and a check that noisy gets silenced instead of fixed. So trivial bodies are excluded
mechanically, and the handful of concrete implementations that must match a Protocol
signature while genuinely ignoring an argument are allowlisted by name.

That left 2 real inert knobs in the quantitative layer, both verified with a measured
delta of exactly 0.0 and both removed in this commit:
  src/data_loader.py       load_demand_csv(date_col)          -- load_demand_frame
                            hardcodes "date"; a nonexistent column name was accepted
  src/multi_echelon.py     evaluate_serial_allocation(review_period)
                            -- risk_periods arrive pre-computed by serial_gsm_cases,
                            which is where review_period is actually used
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"

# (file, function, parameter) triples where a concrete implementation must match a
# Protocol signature but genuinely has no use for the argument. Keyed by name, not
# line, so ordinary edits do not churn this list. Each entry is a deliberate decision,
# not a backlog item.
ALLOWLIST: set[tuple[str, str, str]] = {
    # in-memory / stand-in connectors kept signature-compatible with the real clients
    ("src/connectors/odoo.py", "read", "entity_id"),
    ("src/connectors/shopify_inventory.py", "adjust_inventory", "reason"),
    ("src/connectors/shopify_inventory.py", "set_inventory", "reason"),
    # deterministic stubs standing in for an LLM call
    ("src/seo/geo_probe.py", "complete", "prompt"),
    ("src/voice/doc_reader.py", "extract", "prompt"),
    ("src/voice/doc_reader.py", "extract", "schema"),
    # simworld dirt injectors share one calling convention (df, rng) -> df so
    # apply_dirt's loop can call any of them uniformly; this one's transformation
    # is deterministic (renames a fixed supplier), so it has no use for rng.
    ("src/simworld/lens.py", "inject_renamed_supplier", "rng"),
    # bateria comprobaciones (T01-T20) share one tXX_...(_) signature for a uniform
    # dispatch table; a check that is fully self-contained (no external context
    # needed) still takes the parameter to match the shared call site, and names it
    # `_` to say so at the definition itself.
    ("src/bateria/comprobaciones.py", "t01_pronostico_con_banda", "_"),
    ("src/bateria/comprobaciones.py", "t02_sesgo_y_precision", "_"),
    ("src/bateria/comprobaciones.py", "t04_politica_con_ss_sensible_al_lead_time", "_"),
    ("src/bateria/comprobaciones.py", "t05_propuesta_de_oc_por_proveedor", "_"),
    ("src/bateria/comprobaciones.py", "t06_ajuste_a_las_condiciones_del_proveedor", "_"),
    ("src/bateria/comprobaciones.py", "t08_lead_time_real_por_proveedor", "_"),
    ("src/bateria/comprobaciones.py", "t09_scorecard_de_proveedores", "_"),
    ("src/bateria/comprobaciones.py", "t10_servicio_realizado_al_cliente", "_"),
    ("src/bateria/comprobaciones.py", "t12_markdown_con_unidades_y_margen", "_"),
    ("src/bateria/comprobaciones.py", "t13_exposicion_a_disrupcion", "_"),
    ("src/bateria/comprobaciones.py", "t20_flujo_de_devoluciones", "_"),
    ("src/bateria/comprobaciones.py", "t14_acta_de_sop", "_"),
}


def _body_is_trivial(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Protocol / ABC / stub declarations: the parameters are the interface."""
    stmts = [
        s for s in fn.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str))
    ]
    if not stmts:
        return True
    if len(stmts) > 1:
        return False
    only = stmts[0]
    if isinstance(only, (ast.Pass, ast.Raise)):
        return True
    return (
        isinstance(only, ast.Expr)
        and isinstance(only.value, ast.Constant)
        and only.value.value is Ellipsis
    )


def _inert_parameters() -> list[tuple[str, int, str, str]]:
    """(file, line, function, parameter) for every never-referenced public parameter."""
    found: list[tuple[str, int, str, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") and not node.name.startswith("__"):
                continue
            if _body_is_trivial(node):
                continue
            args = node.args
            names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
            names = [n for n in names if n not in ("self", "cls")]
            if not names:
                continue
            used = {c.id for c in ast.walk(node) if isinstance(c, ast.Name)}
            if "locals" in used or args.kwarg is not None:
                continue
            for name in names:
                if name not in used:
                    found.append((rel, node.lineno, node.name, name))
    return found


def test_no_inert_parameters_outside_the_allowlist():
    offenders = [
        f"{rel}:{line} {func}({param})"
        for rel, line, func, param in _inert_parameters()
        if (rel, func, param) not in ALLOWLIST
    ]
    assert not offenders, (
        "public src/ functions declaring a parameter they never reference — either "
        "use it or drop it from the signature:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_does_not_rot():
    """An allowlisted entry that is now clean must be removed from the allowlist."""
    live = {(rel, func, param) for rel, _l, func, param in _inert_parameters()}
    existing_files = {e for e in ALLOWLIST if (REPO / e[0]).exists()}
    stale = sorted(e for e in existing_files if e not in live)
    assert not stale, f"allowlist entries no longer inert, delete them: {stale}"


def test_the_two_quantitative_knobs_are_gone():
    """Both had a measured delta of exactly 0.0 before removal."""
    import inspect

    from src.data_loader import load_demand_csv
    from src.multi_echelon import evaluate_serial_allocation

    assert "date_col" not in inspect.signature(load_demand_csv).parameters
    assert "review_period" not in inspect.signature(evaluate_serial_allocation).parameters

    with pytest.raises(TypeError):
        load_demand_csv("x.csv", date_col="date")
