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
    mentiría en la columna `moves`. El catálogo debe demostrar DIVERGENCIA:
    múltiples valores para el MISMO parámetro."""
    s = spec_for("order_cost")
    assert s is not None
    sitios = {tool_key for tool_key, _, _ in s.defaults}
    assert "simulation" in sitios
    valores = {valor for _, valor, _ in s.defaults}
    assert 100.0 in valores
    # Verifica que hay divergencia real: más de un valor distinto
    assert len(valores) > 1, f"order_cost debe tener >1 valor distinto, pero tiene {valores}"


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


def test_todas_las_citas_de_linea_son_exactas():
    """Verifica que cada cita 'archivo:línea' en defaults apunta al valor correcto.

    Este test PREVIENE la regresión de citas off-by-one (como las que ocurrieron
    en webapp/app.py:509/510). Para cada entrada en defaults con formato
    'path/to/file.py:NNN', lee esa línea (1-indexada) y verifica que contenga
    una representación del número citado con límites de palabra."""
    import re

    checked_count = 0

    for spec in CATALOG + UNDECLARED_USE:
        for tool_key, value, site in spec.defaults:
            # Saltar sitios sin número de línea (ej: "(docstring)")
            if ":" not in site:
                continue

            # Parsear archivo:línea
            if not site.endswith(")"):  # Saltar formato con parens
                parts = site.split(":")
                if len(parts) < 2:
                    continue

                filepath = ":".join(parts[:-1])  # Manejar rutas con ":"
                try:
                    line_num = int(parts[-1])
                except ValueError:
                    continue

                # Leer el archivo
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                except FileNotFoundError:
                    raise AssertionError(
                        f"{spec.name}: {site} — archivo no encontrado"
                    )

                # Verificar línea (1-indexada)
                if line_num < 1 or line_num > len(lines):
                    raise AssertionError(
                        f"{spec.name}: {site} — línea {line_num} fuera de rango "
                        f"(archivo tiene {len(lines)} líneas)"
                    )

                line_text = lines[line_num - 1]

                # Verificar que la línea contiene el valor con límites de palabra
                # Normalizar el valor: 100.0 → r"\b100\.0\b", 0.22 → r"\b0\.22\b"
                # También permitir representación sin decimal: 1.0 también matchea 1
                value_str = str(value)
                # Escapar puntos en la expresión regular
                value_pattern = re.escape(value_str)
                # Usar límites de palabra
                pattern = r"\b" + value_pattern + r"\b"

                # Si es un float que termina en .0, también probar sin el .0
                alternative_pattern = None
                if isinstance(value, float) and value == int(value):
                    # 1.0 → también probar r"\b1\b"
                    int_str = str(int(value))
                    alternative_pattern = r"\b" + re.escape(int_str) + r"\b"

                found = re.search(pattern, line_text)
                if not found and alternative_pattern:
                    found = re.search(alternative_pattern, line_text)

                if not found:
                    raise AssertionError(
                        f"{spec.name}: {site} cita valor {value}, "
                        f"pero línea {line_num} no contiene ese valor.\n"
                        f"Línea actual: {line_text.rstrip()}\n"
                        f"Patrón esperado: {pattern}"
                        + (f" o {alternative_pattern}" if alternative_pattern else "")
                    )

                checked_count += 1

    # Verificar que se verificaron la mayoría de las citas
    total_defaults = sum(
        len(s.defaults) for s in CATALOG + UNDECLARED_USE
    )
    # Permitir que (docstring) y similares no se verifiquen, pero al menos 70%
    min_checked = int(total_defaults * 0.7)
    assert checked_count >= min_checked, (
        f"Solo {checked_count}/{total_defaults} citas fueron verificadas "
        f"(mínimo {min_checked}). Demasiadas citas sin línea de número."
    )
