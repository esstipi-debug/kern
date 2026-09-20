"""Una clave de idempotencia constante convierte una escritura legitima en un no-op mudo.

`nosilo_core.writeback` garantiza, y lo dice en su propio encabezado, que *"the same
idempotency_key never lands twice"*. Esa garantia es lo que hace seguro reintentar: un
timeout seguido de un retry no puede aplicar dos veces. Pero pide la contraparte, y es
la que se olvida: **la clave tiene que ser distinta para una operacion distinta**.

El defecto, reproducido punta a punta antes de arreglarlo. `channel_replenishment_job`
traia la constante ``"channel-replenish-1"``, que el agente nunca pisaba. Con el
conector Odoo de demo:

    semana 1: stage_restock({SKU-1: +50})   -> applied=True   skip=False
    semana 2: stage_restock({SKU-2: +999})  -> applied=False  skip=True

Contenido distinto (`content_hash` distinto), misma clave, y la segunda se descarta
**en silencio** -- con `idempotent_skip=True`, que es la senal que significa "esto era
un reintento, todo bien". El punto de reorden del segundo SKU nunca aterrizaba.
`repricing_job` tenia el mismo defecto con ``f"repricing-{channel}-1"``, una constante
POR CANAL, y ahi lo que no aterriza es un precio en un marketplace vivo.

No era ignorancia del repo: `excel_replenishment_job` ya derivaba su clave del contenido
estacionado, `shopify_stock_sync` tiene una funcion dedicada para eso, y
`scm_agent/autonomy_promotion.py` lleva el comentario que lo explica entero --
*"Unique per occurrence (NOT a fixed `degrade-<event_type>` key): ... a reused
idempotency_key would make writeback.apply() treat that second, genuinely different
degradation as an idempotent skip of the first"*. Tres lugares con la regla escrita y
dos que no la seguian: exactamente la forma de defecto que este archivo existe para
volver imposible.

Tres tests, tres afirmaciones distintas:
- **estructural**: ninguna clave puede ser un literal. Una constante siempre esta mal,
  sin importar el emisor, y eso se puede decidir leyendo el AST.
- **de comportamiento**: sobre los emisores que se pueden sondear, la MISMA operacion
  da la MISMA clave (o se rompe la idempotencia) y una operacion DISTINTA da una
  clave distinta (o se rompe la garantia de que aterrice).
- **de clasificacion**: todo emisor que construya una clave tiene que estar sondeado o
  declarado por escrito, como `NO_EMISORES` del barrido de neteo.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nosilo_core import writeback

RAIZ = Path(__file__).resolve().parents[1]

#: Donde vive codigo que puede originar una clave. `tests/` queda afuera a proposito:
#: un test puede usar una constante porque controla el store entero.
ARBOLES = ("jobs", "src", "scm_agent", "webapp", "examples")

#: Emisores que construyen su clave y NO se sondean aca, con la razon. Sondear cuesta
#: un fixture; declarar cuesta una linea -- lo que no puede pasar es que un emisor
#: nuevo no haga ninguna de las dos.
NO_SONDEADOS = {
    "jobs/excel_replenishment_job.py": (
        "deriva de un hash del contenido estacionado MAS las celdas guarda que leyo "
        "del archivo vivo, asi que un plan nuevo contra un archivo movido ya cambia de "
        "clave. Sondearlo pide un .xlsx real; el criterio esta verificado por lectura "
        "y por sus propios tests"
    ),
    "scm_agent/autonomy_promotion.py": (
        "dos claves, las dos unicas por ocurrencia: `promote-{record.id}` y "
        "`degrade-{event_type}-{timestamp}`. La segunda lleva en el codigo el "
        "comentario que explica por que una fija estaria mal, y es la fuente de la "
        "regla que este archivo generaliza"
    ),
    "src/voice/followup.py": (
        "usa `outcome.call_id`, que identifica la llamada -- la clave natural de la "
        "operacion -- y cae a `voice:{status}:{entity}` cuando el canal no lo da"
    ),
    # -- los que RECIBEN la clave y no la deciden. Acusarlos seria acusar al cartero,
    #    pero tienen que estar nombrados: el dia que uno empiece a fabricarla, la
    #    razon deja de ser cierta y hay que venir a cambiarla.
    "jobs/repricing.py": "recibe `idempotency_key` como argumento y lo reenvia a `writeback.stage`",
    "src/connectors/odoo.py": "recibe la clave en `stage_restock`; la del PO la deriva del content_hash",
    "src/connectors/shopify.py": "recibe la clave y la reenvia",
    "src/connectors/simulator.py": "recibe la clave y la reenvia",
    "src/connectors/emulator.py": "recibe la clave por `payload['idempotency_key']` y la reenvia",
    "src/connectors/replenish.py": "recibe la clave y la reenvia al conector que corresponda",
    # -- los CLI de ejemplo. Derivan desde el 2026-09-20; antes eran tres constantes,
    #    y son la plantilla de la que se copia, asi que valen igual que produccion.
    "examples/run_shopify.py": "deriva del conteo con `derive_idempotency_key`",
    "examples/run_connector_sim.py": "deriva de la reposicion con `derive_idempotency_key`",
    "examples/run_repricing.py": "deriva del canal Y de los precios con `derive_idempotency_key`",
}


def _archivos_py() -> list[Path]:
    return [p for arbol in ARBOLES for p in (RAIZ / arbol).rglob("*.py")]


#: Las funciones por las que un changeset nace. Quien llama a una de estas Y pasa
#: `idempotency_key=` esta en el circuito, y tiene que estar sondeado o declarado.
#: Derivarlo asi -- y no de una heuristica sobre la forma de la expresion -- es lo que
#: lo vuelve decidible: la derivacion puede estar una linea mas arriba, en un `+`, en
#: una f-string o en una variable, y ninguna regla sintactica las cubre todas.
ENTRADAS_DE_STAGING = {"stage", "stage_restock", "stage_repricing", "stage_reconciliation",
                       "Changeset"}


def _nombre(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _rekeyea(fn: ast.AST) -> bool:
    """La funcion vuelve a poner la clave despues de estacionar.

    `src/connectors/odoo.py` lo hace y esta bien: la clave que quiere es el
    `content_hash` del changeset, y para tenerlo hay que construir el changeset
    primero, asi que pasa un placeholder y lo reemplaza en la linea siguiente. La
    clave FINAL esta derivada; el literal es un andamio. Reconocerlo es lo que deja
    que "ninguna constante sobrevive" siga siendo una afirmacion exacta en vez de una
    con excepciones escritas.
    """
    for hijo in ast.walk(fn):
        if isinstance(hijo, ast.Call) and _nombre(hijo.func) in {"replace", "_dc_replace"}:
            if any(kw.arg == "idempotency_key" for kw in hijo.keywords):
                return True
    return False


def _sitios_con_clave() -> list[tuple[str, int, ast.AST]]:
    """``(archivo, linea, expresion)`` de cada ``idempotency_key=`` que llega a una
    entrada de staging, saltando los andamios que se re-keyean."""
    fuera: list[tuple[str, int, ast.AST]] = []
    for ruta in _archivos_py():
        try:
            arbol = ast.parse(ruta.read_text())
        except SyntaxError:      # pragma: no cover - un .py que no parsea no es nuestro
            continue
        envolvente: dict[int, ast.AST] = {}
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.FunctionDef | ast.AsyncFunctionDef):
                for hijo in ast.walk(nodo):
                    envolvente.setdefault(id(hijo), nodo)
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call) or _nombre(nodo.func) not in ENTRADAS_DE_STAGING:
                continue
            fn = envolvente.get(id(nodo))
            if fn is not None and _rekeyea(fn):
                continue
            for kw in nodo.keywords:
                if kw.arg == "idempotency_key":
                    fuera.append((str(ruta.relative_to(RAIZ)), nodo.lineno, kw.value))
    return fuera


# ---------------------------------------------------------------------------
# 1. Una constante siempre esta mal, y eso se decide leyendo
# ---------------------------------------------------------------------------


def test_ninguna_clave_de_idempotencia_es_un_literal():
    """El defecto en su forma pura.

    Un literal no puede variar con la operacion por definicion, asi que el segundo uso
    SIEMPRE es un falso duplicado. No hay emisor para el que esto sea correcto: incluso
    una operacion que ocurre una sola vez en la vida del proceso se reintenta entre
    procesos.
    """
    literales = [
        f"{archivo}:{linea} -> {expr.value!r}"
        for archivo, linea, expr in _sitios_con_clave()
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str)
    ]
    assert not literales, (
        "clave de idempotencia constante en " + "; ".join(literales) + ". "
        "`nosilo_core.writeback` garantiza que la misma clave nunca aterriza dos "
        "veces, asi que una constante hace que la SEGUNDA operacion legitima se "
        "descarte con `idempotent_skip=True` -- en silencio, y con la senal que "
        "significa 'esto era un reintento'. Derivala con "
        "`writeback.derive_idempotency_key(prefijo, edits)`."
    )


def test_todo_emisor_esta_sondeado_o_declarado():
    """La mitad de clasificacion, con el patron de `NO_EMISORES` del barrido de neteo:
    la lista no se mantiene a mano, se deriva, y un emisor nuevo obliga a decidir."""
    archivos = {archivo for archivo, _, _ in _sitios_con_clave()}
    sondeados = {"jobs/channel_replenishment_job.py", "jobs/repricing_job.py",
                 "jobs/shopify_stock_sync.py"}
    sin_clasificar = sorted(archivos - sondeados - set(NO_SONDEADOS))
    assert not sin_clasificar, (
        f"estos emisores construyen una clave y no estan ni sondeados ni declarados: "
        f"{sin_clasificar}. Sondealo, o agregalo a NO_SONDEADOS con la razon."
    )
    fantasmas = sorted(set(NO_SONDEADOS) - archivos)
    assert not fantasmas, f"NO_SONDEADOS nombra archivos que ya no originan clave: {fantasmas}"


# ---------------------------------------------------------------------------
# 2. La derivacion canonica cumple LAS DOS mitades
# ---------------------------------------------------------------------------


def test_la_derivacion_es_estable_para_la_misma_operacion():
    edits = {"SKU-1": {"qty": 50.0}, "SKU-2": {"qty": 10.0}}
    # Y el orden de las claves del dict no puede cambiarla: es la misma operacion.
    revuelto = {"SKU-2": {"qty": 10.0}, "SKU-1": {"qty": 50.0}}
    assert (writeback.derive_idempotency_key("x", edits)
            == writeback.derive_idempotency_key("x", revuelto))


@pytest.mark.parametrize("otros", [
    {"SKU-1": {"qty": 51.0}},                       # otra cantidad
    {"SKU-2": {"qty": 50.0}},                       # otro SKU
    {"SKU-1": {"otro_campo": 50.0}},                # otro campo
    {"SKU-1": {"qty": 50.0}, "SKU-3": {"qty": 1.0}},  # un SKU mas
])
def test_la_derivacion_cambia_cuando_la_operacion_cambia(otros):
    base = {"SKU-1": {"qty": 50.0}}
    assert writeback.derive_idempotency_key("x", base) != writeback.derive_idempotency_key("x", otros)


def test_dos_emisores_distintos_no_comparten_clave():
    """El prefijo no es decoracion: dos emisores con el mismo conjunto de ediciones
    siguen siendo dos operaciones, y ademas un audit log tiene que poder leerse."""
    edits = {"SKU-1": {"qty": 50.0}}
    a = writeback.derive_idempotency_key("channel-replenish", edits)
    b = writeback.derive_idempotency_key("repricing-meli", edits)
    assert a != b
    assert a.startswith("channel-replenish-") and b.startswith("repricing-meli-")


# ---------------------------------------------------------------------------
# 3. Los emisores sondeados, por su camino real
# ---------------------------------------------------------------------------


def _clave_de_canal(extra: dict | None = None, *, restock_extra: dict | None = None) -> str:
    from nosilo_core.types import JobRequest
    from scm_agent.tools import build_default_registry
    from src.connectors.odoo import demo_odoo
    from src.connectors.shopify import demo_shopify

    tool = build_default_registry().get("channel_replenishment")
    params = {"shopify_rpc": demo_shopify(), "odoo_rpc": demo_odoo(), **(extra or {})}
    prep = tool.prepare(JobRequest(brief="x", data_path=None, params=params), None)
    assert prep.status == "ok", prep.messages
    return tool.run(prep.payload, params).report.changeset.idempotency_key


def test_la_reposicion_de_canal_deriva_su_clave_y_la_repite():
    """Dos corridas del MISMO plan tienen que dar la MISMA clave -- si no, se rompe la
    idempotencia y un reintento aplica dos veces."""
    assert _clave_de_canal() == _clave_de_canal()
    assert _clave_de_canal().startswith("channel-replenish-")
    assert _clave_de_canal() != "channel-replenish-1", (
        "volvio la constante: la segunda reposicion legitima de este cliente se va a "
        "descartar como duplicado"
    )
    # Y el llamador sigue pudiendo declararla, que es lo que deja replanificar a mano.
    assert _clave_de_canal({"idempotency_key": "restock-2026-W38"}) == "restock-2026-W38"


def test_dos_reposiciones_DISTINTAS_aterrizan_las_dos():
    """La regresion, punta a punta contra el writeback de verdad.

    Es el caso que se reprodujo: dos reposiciones genuinamente distintas del mismo
    cliente. Con la constante, la segunda salia `applied=False, idempotent_skip=True`.
    """
    from src.connectors.odoo import OdooConnector, demo_odoo

    odoo = OdooConnector(demo_odoo())
    niveles = {lvl.sku: lvl.available for lvl in odoo.inventory_levels()}
    skus = sorted(niveles)[:2]

    def reponer(sku: str, qty: float):
        clave = writeback.derive_idempotency_key(
            "channel-replenish", {sku: {"restock_qty": qty, "erp_on_hand": niveles[sku]}}
        )
        return odoo.apply_restock(odoo.stage_restock({sku: qty}, idempotency_key=clave))

    primera = reponer(skus[0], 50.0)
    segunda = reponer(skus[1], 999.0)

    assert primera.applied and not primera.idempotent_skip
    assert segunda.applied and not segunda.idempotent_skip, (
        "la segunda reposicion se descarto como duplicado: las claves volvieron a "
        "colisionar"
    )

    # Y el reintento de la PRIMERA sigue deduplicando, que es la otra mitad.
    reintento = reponer(skus[0], 50.0)
    assert not reintento.applied and reintento.idempotent_skip


def test_la_repactacion_deriva_su_clave_por_canal_y_por_precios():
    """El segundo emisor que traia una constante, y el que mas duele: lo que no
    aterriza es un precio en un marketplace vivo."""
    import tempfile

    import pandas as pd

    from jobs.repricing_job import prepare

    raiz = Path(tempfile.mkdtemp())

    def plan(precios: dict[str, float], canal: str = "meli"):
        ruta = raiz / f"{canal}-{sorted(precios.items())}.csv"
        pd.DataFrame([
            {"sku": sku, "current_price": 100.0, "proposed_price": p, "landed_cost": 40.0}
            for sku, p in precios.items()
        ]).to_csv(ruta, index=False)
        return prepare(ruta, {"channel": canal, "reason": "ajuste"}).idempotency_key

    base = plan({"SKU-1": 120.0})
    assert base.startswith("repricing-meli-")
    assert base != "repricing-meli-1", "volvio la constante por canal"
    assert base == plan({"SKU-1": 120.0})               # mismo plan, misma clave
    assert base != plan({"SKU-1": 130.0})               # otro precio, otra clave
    assert base != plan({"SKU-1": 120.0}, canal="shopify")   # otro canal, otra clave
