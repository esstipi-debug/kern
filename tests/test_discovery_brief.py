"""Tests del motor puro de discovery_brief. Nada aquí toca disco."""
from src.discovery_brief import CATALOG, UNDECLARED_USE, ParamSpec, spec_for


def test_el_catalogo_no_repite_nombres():
    """Indexado POR PARAMETRO: un nombre canónico aparece una sola vez.

    Duplicar un parámetro por herramienta es exactamente la repetición que
    produjo las cinco instancias del mismo defecto en intake_scan.
    """
    nombres = [s.name for s in CATALOG + UNDECLARED_USE]
    assert len(nombres) == len(set(nombres)), f"nombres repetidos: {nombres}"


def test_todo_parametro_declara_que_cifra_mueve():
    """`moves` es el producto real: sin él el catálogo es una lista de nombres."""
    sin_moves = [s.name for s in CATALOG + UNDECLARED_USE if not s.moves.strip()]
    assert sin_moves == []


def test_todo_parametro_tiene_rango_plausible():
    for s in CATALOG + UNDECLARED_USE:
        assert s.low < s.high, f"{s.name}: rango vacío o invertido"


def test_order_cost_registra_su_default_por_sitio():
    """Los defaults son una TUPLA a propósito: order_cost vale 100.0 en
    simulation_job mientras client_profile documenta 75. Un default único
    mentiría en la columna `moves`."""
    s = spec_for("order_cost")
    assert s is not None
    sitios = {tool_key for tool_key, _, _ in s.defaults}
    assert "simulation" in sitios
    valores = {valor for _, valor, _ in s.defaults}
    assert 100.0 in valores


def test_objection_thresholds_es_granular():
    s = spec_for("objection_thresholds")
    assert s is not None
    assert s.subkeys == ("comprador", "planner", "cfo", "comercial")


def test_spec_for_devuelve_none_para_un_desconocido():
    assert spec_for("no_existe_este_parametro") is None


def test_las_areas_copiadas_no_derivaron_de_hats():
    """El motor puro copia HAT_KEYS en vez de importarlo (src.hats arrastra numpy
    y scipy). Esta aserción es lo único que impide que la copia derive."""
    from src.hats import HAT_KEYS

    assert spec_for("objection_thresholds").subkeys == HAT_KEYS
