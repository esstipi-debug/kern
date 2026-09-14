"""Tests del resolvedor de anclajes por simbolo (``tests/_citation_anchor``).

Hermeticos a proposito: cada test escribe su propio modulo en ``tmp_path`` en
vez de asertar contra archivos del repo. Un test que dependiera de
``jobs/simulation_job.py`` volveria a atar el guard a la forma de un archivo
ajeno -- que es exactamente la fragilidad que este modulo existe para eliminar.
"""
import pytest

# ``tests/`` esta en sys.path via conftest (misma convencion que _no_core).
from _citation_anchor import (  # noqa: E402
    AnchorError,
    holds_value,
    is_anchor,
    resolve_constants,
)


def _write(tmp_path, source: str, name: str = "modulo.py"):
    (tmp_path / name).write_text(source, encoding="utf-8")
    return name


# --- reconocer un anclaje -------------------------------------------------

def test_un_sitio_con_doble_dos_puntos_es_anclaje():
    assert is_anchor("jobs/simulation_job.py::_DEFAULT_ORDER")


def test_un_sitio_sin_doble_dos_puntos_no_es_anclaje():
    """El '(docstring)' de client_profile sigue siendo un sitio legitimo sin anclar."""
    assert not is_anchor("src/client_profile.py (docstring)")


def test_una_cita_por_numero_de_linea_ya_no_es_anclaje():
    """El formato viejo no se resuelve en silencio: o es simbolo, o no es anclaje."""
    assert not is_anchor("jobs/inventory_optimization.py:229")


# --- las cuatro formas que existen en el repo -----------------------------

def test_resuelve_el_default_de_un_parametro(tmp_path):
    mod = _write(tmp_path, "def run(*, holding_rate: float = 0.25):\n    return holding_rate\n")
    assert resolve_constants(f"{mod}::run.holding_rate", root=tmp_path) == (0.25,)


def test_resuelve_un_campo_de_dataclass(tmp_path):
    mod = _write(tmp_path, "class BatchConfig:\n    fixed_order_cost: float = 1000.0\n")
    assert resolve_constants(f"{mod}::BatchConfig.fixed_order_cost", root=tmp_path) == (1000.0,)


def test_resuelve_una_constante_de_modulo(tmp_path):
    mod = _write(tmp_path, "_DEFAULT_ORDER = 100.0\n")
    assert resolve_constants(f"{mod}::_DEFAULT_ORDER", root=tmp_path) == (100.0,)


def test_resuelve_un_keyword_arg_de_una_llamada(tmp_path):
    """El literal de flow_map_job no tiene nombre propio; lo tiene el keyword."""
    mod = _write(tmp_path, 'def run(params):\n    return f(cycle_service_level=float(params.get("sl", 0.95)))\n')
    assert 0.95 in resolve_constants(f"{mod}::run.cycle_service_level", root=tmp_path)


def test_resuelve_una_clave_string_de_un_dict_literal(tmp_path):
    """El 0.0 de newsvendor vive como valor de la clave 'goodwill'."""
    mod = _write(tmp_path, 'def prepare_records(rows):\n    return [{"goodwill": 0.0} for r in rows]\n')
    assert 0.0 in resolve_constants(f"{mod}::prepare_records.goodwill", root=tmp_path)


# --- LA PROPIEDAD A CONSERVAR: rompe cuando el valor citado ya no esta -----

def test_rompe_cuando_el_valor_cambio(tmp_path):
    """Si el default pasa de 0.25 a 0.30, la cita ya no lo encuentra."""
    mod = _write(tmp_path, "def run(*, holding_rate: float = 0.30):\n    return holding_rate\n")
    constantes = resolve_constants(f"{mod}::run.holding_rate", root=tmp_path)
    assert not holds_value(constantes, 0.25)


def test_rompe_cuando_el_parametro_se_renombro(tmp_path):
    mod = _write(tmp_path, "def run(*, carrying_rate: float = 0.25):\n    return carrying_rate\n")
    with pytest.raises(AnchorError, match="holding_rate"):
        resolve_constants(f"{mod}::run.holding_rate", root=tmp_path)


def test_rompe_cuando_la_funcion_desaparecio(tmp_path):
    mod = _write(tmp_path, "def otra(*, holding_rate: float = 0.25):\n    return holding_rate\n")
    with pytest.raises(AnchorError, match="run"):
        resolve_constants(f"{mod}::run.holding_rate", root=tmp_path)


def test_rompe_cuando_el_archivo_no_existe(tmp_path):
    with pytest.raises(AnchorError, match="no existe"):
        resolve_constants("fantasma.py::run.holding_rate", root=tmp_path)


# --- LA PROPIEDAD NUEVA: no rompe por lineas ni por formato ---------------

def test_no_rompe_al_insertar_lineas_arriba(tmp_path):
    """La regresion concreta del 2026-08-31: un campo nuevo corrio 6 lineas."""
    mod = _write(tmp_path, (
        "import os\n" * 20
        + "\n\nclass JobReport:\n    service_note: str = ''\n\n\n"
        + "def run(*, holding_rate: float = 0.25):\n    return holding_rate\n"
    ))
    assert resolve_constants(f"{mod}::run.holding_rate", root=tmp_path) == (0.25,)


def test_no_rompe_si_el_default_se_parte_en_varias_lineas(tmp_path):
    """El AST no ve formato: black puede reformatear sin romper la cita."""
    mod = _write(tmp_path, "def run(\n    *,\n    holding_rate: float = (\n        0.25\n    ),\n):\n    return holding_rate\n")
    assert resolve_constants(f"{mod}::run.holding_rate", root=tmp_path) == (0.25,)


def test_no_rompe_si_la_linea_gana_un_comentario(tmp_path):
    mod = _write(tmp_path, "def run(*, holding_rate: float = 0.25):  # h_total, incluye capital\n    return holding_rate\n")
    assert resolve_constants(f"{mod}::run.holding_rate", root=tmp_path) == (0.25,)


# --- precision: lo que el anclaje por TEXTO no podia hacer -----------------

def test_distingue_dos_funciones_con_defaults_identicos(tmp_path):
    """El caso powerbi_export: dos lineas byte-identicas en el mismo archivo.

    Con anclaje por texto, borrar una de las dos dejaba el guard en verde
    porque la otra segui­a conteniendo la misma linea. Con anclaje por simbolo,
    cada una se resuelve por su cuenta y borrar una rompe SU cita.
    """
    mod = _write(tmp_path, (
        "def analyze_product(*, order_cost: float = 1000.0):\n    return order_cost\n\n\n"
        "def build_powerbi_dataset(*, order_cost: float = 2000.0):\n    return order_cost\n"
    ))
    assert resolve_constants(f"{mod}::analyze_product.order_cost", root=tmp_path) == (1000.0,)
    assert resolve_constants(f"{mod}::build_powerbi_dataset.order_cost", root=tmp_path) == (2000.0,)


def test_no_cruza_a_un_scope_anidado(tmp_path):
    """Un nombre de una funcion interna no se resuelve desde la externa."""
    mod = _write(tmp_path, "def outer(a=1.0):\n    def inner(b=2.0):\n        return b\n    return a\n")
    with pytest.raises(AnchorError, match="b"):
        resolve_constants(f"{mod}::outer.b", root=tmp_path)
    assert resolve_constants(f"{mod}::outer.inner.b", root=tmp_path) == (2.0,)


def test_recorre_el_valor_entero_de_un_default_envuelto(tmp_path):
    """webapp usa Query(0.22, gt=0.0, le=2.0): el 0.22 esta adentro de la llamada."""
    mod = _write(tmp_path, "def api_portfolio(holding_rate: float = Query(0.22, gt=0.0, le=2.0)):\n    return holding_rate\n")
    assert 0.22 in resolve_constants(f"{mod}::api_portfolio.holding_rate", root=tmp_path)


# --- comparacion de valores ------------------------------------------------

def test_un_entero_y_un_float_del_mismo_numero_son_el_mismo_valor():
    """_DEFAULT_REVIEW = 1 citado como 1 o como 1.0 es la misma constante."""
    assert holds_value((1,), 1.0)
    assert holds_value((1.0,), 1)


def test_un_bool_no_cuenta_como_el_numero_que_vale():
    """True == 1 en Python; una cita de 1 no puede quedar verde por un True."""
    assert not holds_value((True,), 1)
    assert not holds_value((False,), 0.0)


def test_un_string_con_el_numero_adentro_no_cuenta():
    """El regex viejo matcheaba '0.25' dentro de un comentario o un string."""
    assert not holds_value(("0.25",), 0.25)
