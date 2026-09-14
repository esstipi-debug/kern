"""Resolver una cita 'archivo.py::simbolo' al valor que el codigo tiene HOY.

Reemplaza el anclaje por NUMERO DE LINEA, que se rompia con cualquier insercion
arriba del punto citado aunque el valor no hubiera cambiado (tres veces en la
historia de este repo: PRs #160/#162/#164, #200, y el campo `service_note` del
2026-08-31, que corrio seis lineas las tres citas a
`jobs/inventory_optimization.py`).

La propiedad que importa NO cambia: la cita se rompe cuando el valor citado ya
no esta en el codigo. Lo que cambia es que ahora se rompe SOLO por eso.

    rompe                     no rompe
    -----                     --------
    cambio el valor           se insertaron lineas arriba
    renombraron el simbolo    black reformateo / partio la linea
    borraron la funcion       la linea gano un comentario
    movieron el archivo

## La gramatica

    <ruta relativa al repo>.py::<segmento>[.<segmento>...]

Cada segmento resuelve contra cualquier LIGADURA de ese nombre en el scope
actual. "Ligadura" es una sola regla que cubre las cuatro formas que este repo
usa para declarar un default:

    def / class            jobs/flow_map_job.py::run
    asignacion             jobs/simulation_job.py::_DEFAULT_ORDER
    parametro con default  jobs/inventory_optimization.py::run.holding_rate
    campo de dataclass     src/batch.py::BatchConfig.fixed_order_cost
    keyword-arg            jobs/flow_map_job.py::run.cycle_service_level
    clave string de dict   jobs/newsvendor_job.py::prepare_records.goodwill

Las dos ultimas existen porque no todo valor tiene nombre propio: el 0.95 de
flow_map vive dentro de `params.get("service_level", 0.95)`, y el unico nombre
a su alcance es el del keyword que lo recibe.

Los segmentos intermedios tienen que resolver a un scope (def/class); el ultimo
puede resolver a cualquier expresion, y se recogen TODAS las constantes de su
subarbol -- `Query(0.22, gt=0.0, le=2.0)` aporta 0.22 junto con sus validadores,
y con eso alcanza para afirmar que el default sigue ahi.

## Por que vive en tests/ y no en src/

Lee disco. `src/discovery_brief.py` declara "nada toca disco" en su primer
parrafo, y un modulo que lee archivos al lado suyo invita a que alguien lo
importe desde el motor puro. Ademas hoy tiene un solo consumidor. Si aparece un
segundo, se promueve a `src/`.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable

ANCHOR_SEP = "::"

# Nodos que abren un scope propio. `_walk_scope` no los cruza: un nombre
# definido dentro de una funcion anidada NO es visible desde la de afuera, y
# colapsar esa distincion volveria a aflojar el guard.
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

_REPO_ROOT = Path(__file__).resolve().parent.parent


class AnchorError(Exception):
    """La cita no se pudo resolver: falta el archivo, el scope o el nombre."""


def is_anchor(site: str) -> bool:
    """El sitio esta anclado a un simbolo (y no es un '(docstring)' sin anclar)."""
    return ANCHOR_SEP in site


def _walk_scope(node: ast.AST) -> Iterable[ast.AST]:
    """El subarbol de `node` SIN entrar a un scope anidado.

    Los scopes anidados se emiten (para poder matchearlos por nombre) pero no se
    recorren. Una comprension NO cuenta como scope aca a proposito: el dict de
    `prepare_records` vive dentro de un list-comp y tiene que ser alcanzable.
    """
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(child, _SCOPES):
            yield from _walk_scope(child)


def _param_defaults(fn: ast.AST, name: str) -> list[ast.AST]:
    """El default del parametro `name`, si `fn` es una funcion y lo declara."""
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    args = fn.args
    positional = args.posonlyargs + args.args
    # Los defaults se alinean por DERECHA: los primeros parametros no tienen.
    padded = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, padded)) + list(zip(args.kwonlyargs, args.kw_defaults))
    return [default for arg, default in pairs if arg.arg == name and default is not None]


def _bindings(scope: ast.AST, name: str) -> list[ast.AST]:
    """Todo nodo ligado a `name` dentro de `scope`, sin cruzar scopes.

    Se devuelven TODAS las ligaduras, no la primera: en `jobs/newsvendor_job.py`
    el nombre `goodwill` es a la vez una variable local y una clave del dict que
    se construye, y el valor citado esta en la segunda. Unir es honesto -- "el
    nombre esta ligado a algo que contiene este valor" -- y degrada bien: si la
    clave desaparece, la variable local sigue ahi pero ya no aporta el 0.0, y la
    cita se rompe como corresponde.
    """
    found: list[ast.AST] = _param_defaults(scope, name)
    for node in _walk_scope(scope):
        if isinstance(node, _SCOPES) and getattr(node, "name", None) == name:
            found.append(node)
        elif isinstance(node, ast.Assign):
            found += [node.value for target in node.targets
                      if isinstance(target, ast.Name) and target.id == name]
        elif isinstance(node, ast.AnnAssign):
            if (node.value is not None and isinstance(node.target, ast.Name)
                    and node.target.id == name):
                found.append(node.value)
        elif isinstance(node, ast.keyword) and node.arg == name:
            found.append(node.value)
        elif isinstance(node, ast.Dict):
            found += [value for key, value in zip(node.keys, node.values)
                      if isinstance(key, ast.Constant) and key.value == name]
    return found


def resolve_constants(site: str, *, root: Path | None = None) -> tuple[object, ...]:
    """Las constantes que el codigo tiene HOY en el simbolo que cita `site`.

    Levanta `AnchorError` -- nunca devuelve vacio en silencio -- cuando el
    archivo, un scope intermedio o el nombre final no existen. Ese es el modo de
    falla que el guard necesita conservar.
    """
    root = _REPO_ROOT if root is None else root
    relpath, _, dotted = site.partition(ANCHOR_SEP)
    try:
        source = (root / relpath).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise AnchorError(f"{site} -- el archivo {relpath} no existe") from None
    tree = ast.parse(source, filename=relpath)

    *outer, last = dotted.split(".")
    scopes: list[ast.AST] = [tree]
    walked: list[str] = []
    for segment in outer:
        nxt = [b for s in scopes for b in _bindings(s, segment) if isinstance(b, _SCOPES)]
        if not nxt:
            raise AnchorError(
                f"{site} -- no hay un scope {segment!r} en {'.'.join(walked) or relpath}"
            )
        scopes, walked = nxt, walked + [segment]

    values = [b for s in scopes for b in _bindings(s, last)]
    if not values:
        raise AnchorError(
            f"{site} -- no hay una ligadura {last!r} en {'.'.join(walked) or relpath}"
        )
    return tuple(
        node.value
        for value in values
        for node in ast.walk(value)
        if isinstance(node, ast.Constant)
    )


def holds_value(constants: Iterable[object], value: float) -> bool:
    """Alguna constante ES el numero citado.

    Compara numericamente (`1` y `1.0` son el mismo default) pero rechaza los
    bool: `True == 1` en Python, y una cita de 1 no puede quedar verde porque el
    codigo tenga un `True`. Tambien rechaza los strings, que es como el regex
    anterior podia matchear un '0.25' dentro de un comentario.
    """
    return any(
        isinstance(c, (int, float)) and not isinstance(c, bool) and c == value
        for c in constants
    )
