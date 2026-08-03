"""Tests del motor puro de discovery_brief. Nada aquí toca disco."""
from dataclasses import dataclass

from src.discovery_brief import (
    CATALOG,
    GREEN,
    NO_PROFILE,
    RED,
    UNDECLARED,
    UNDECLARED_USE,
    YELLOW,
    build_report,
    rank_gaps,
    spec_for,
    verdicts_for,
)


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
    una representación del número citado con límites de palabra.

    No hay excepción silenciosa: un sitio que contiene '.py:' pero no se deja
    parsear a un número de línea usable es un FALLO del test, no un `continue`.
    Solo los sitios legítimamente sin línea (ej. "(docstring)", sin ".py:") se
    cuentan como saltados a propósito, y se nombran si el conteo verificado
    queda corto."""
    import re

    checked_count = 0
    skipped_sites: list[str] = []

    for spec in CATALOG + UNDECLARED_USE:
        for tool_key, value, site in spec.defaults:
            if ".py:" not in site:
                # Sitio legítimamente sin línea (ej. "src/client_profile.py (docstring)").
                skipped_sites.append(f"{spec.name}: {site}")
                continue

            # Parsear archivo:línea. Cualquier fallo de parseo es un FALLO del
            # test -- no un `continue` silencioso -- porque un sitio con ".py:"
            # que no parsea es exactamente el hueco que dejaba pasar citas
            # sin verificar (ver Fix Round 4 en task-1-report.md).
            parts = site.split(":")
            if len(parts) < 2:
                raise AssertionError(
                    f"{spec.name}: {site} — contiene '.py:' pero no se pudo "
                    f"separar en archivo:línea"
                )

            filepath = ":".join(parts[:-1])  # Manejar rutas con ":"
            try:
                line_num = int(parts[-1])
            except ValueError:
                raise AssertionError(
                    f"{spec.name}: {site} — el segmento final {parts[-1]!r} "
                    f"no es un número de línea entero"
                )

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

    # Con la excepción silenciosa eliminada, toda cita "archivo.py:línea" es
    # verificable -- el unico salto legitimo es la ausencia de ".py:" en el
    # sitio. El piso ahora es 100% de las citas parseables, no un 70% que
    # dejaba espacio para que un hueco de parseo pasara desapercibido.
    total_defaults = sum(len(s.defaults) for s in CATALOG + UNDECLARED_USE)
    expected_checked = total_defaults - len(skipped_sites)
    assert checked_count == expected_checked, (
        f"Se verificaron {checked_count} citas mecanicas de "
        f"{expected_checked} esperadas ({total_defaults} citas totales, "
        f"{len(skipped_sites)} saltadas a proposito: {skipped_sites})"
    )


@dataclass(frozen=True)
class _FakeTool:
    """Lo mínimo del contrato de nosilo_core.registry.Tool que este motor lee."""
    key: str
    required_client_params: tuple[str, ...] = ()


def test_una_tool_sin_declaracion_sale_no_declarado():
    """No podemos hablar por ella. Nunca 'verde' por omisión."""
    (v,) = verdicts_for([_FakeTool("forecast")], {"holding_rate": 0.3})
    assert v.state == UNDECLARED


def test_todos_los_parametros_presentes_y_en_rango_sale_verde():
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"holding_rate": 0.28, "service_level": 0.97})
    assert v.state == GREEN
    assert v.missing == ()


def test_un_parametro_ausente_sale_rojo_y_lo_nombra():
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"service_level": 0.97})
    assert v.state == RED
    assert v.missing == ("holding_rate",)


def test_fuera_de_rango_es_rojo_pero_distinguible_de_ausente():
    """holding_rate=25.0 -- porcentaje tipeado donde va una fracción -- pasa la
    validación de ClientProfile y multiplica por cien el costo de mantener.
    Pedirlo de nuevo no sirve: hay que corregirlo, y por eso viaja aparte."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"holding_rate": 25.0, "service_level": 0.97})
    assert v.state == RED
    assert v.missing == ()
    assert v.out_of_range == (("holding_rate", 25.0),)


def test_sin_perfil_no_se_colapsa_en_rojo():
    """La acción es distinta: crear el perfil, no pedirle un número al cliente."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], None)
    assert v.state == NO_PROFILE


def test_objection_thresholds_parcial_es_amarillo_no_rojo():
    """Un dict parcial es legal a propósito: cross_impact devuelve NO_EVALUABLE
    en voz alta para el área sin umbral. NO toma un default en silencio, así que
    no es rojo -- corre y cubre menos, que es exactamente lo que amarillo dice."""
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"objection_thresholds": {"cfo": 1000.0, "planner": 500.0}})
    assert v.state == YELLOW
    assert v.incomplete == (("objection_thresholds", ("comercial", "comprador")),)


def test_objection_thresholds_completo_es_verde():
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    completo = {k: 1.0 for k in ("comprador", "planner", "cfo", "comercial")}
    (v,) = verdicts_for([tool], {"objection_thresholds": completo})
    assert v.state == GREEN


def test_objection_thresholds_ausente_es_rojo():
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"holding_rate": 0.25})
    assert v.state == RED
    assert v.missing == ("objection_thresholds",)


def test_los_veredictos_salen_ordenados_por_key():
    tools = [_FakeTool("zeta"), _FakeTool("alfa"), _FakeTool("media")]
    keys = [v.tool_key for v in verdicts_for(tools, {})]
    assert keys == ["alfa", "media", "zeta"]


def test_bool_en_escalar_es_rojo_y_malformado_no_fuera_de_rango():
    """holding_rate=True -- un bool es un int en Python y pasaría el chequeo de
    rango silenciosamente (True == 1, dentro de [0.05, 0.60] no, pero incluso
    False == 0 podría colar). El fix trata cualquier bool como dato roto, no
    como un número: viaja en `malformed`, nunca en `out_of_range` ni `missing`."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"holding_rate": True, "service_level": 0.97})
    assert v.state == RED
    assert v.missing == ()
    assert v.out_of_range == ()
    assert v.malformed == (("holding_rate", "True"),)


def test_string_en_escalar_es_rojo_y_malformado():
    """holding_rate="0.25" -- un string en un campo numérico. No es "ausente"
    (el cliente sí mandó algo) ni "fuera de rango" (no es ni siquiera un
    número): hay que reemplazar el valor, no pedirlo de nuevo ni corregirlo."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    (v,) = verdicts_for([tool], {"holding_rate": "0.25", "service_level": 0.97})
    assert v.state == RED
    assert v.missing == ()
    assert v.malformed == (("holding_rate", "'0.25'"),)


def test_objection_thresholds_none_es_rojo_no_amarillo():
    """None no es un dict parcial legítimo -- es la ausencia de estructura.
    No colapsa en el bucket de amarillo (parcial honesto)."""
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"objection_thresholds": None})
    assert v.state == RED
    assert v.incomplete == ()
    assert v.malformed == (("objection_thresholds", "None"),)


def test_objection_thresholds_string_es_rojo_no_amarillo():
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"objection_thresholds": "texto"})
    assert v.state == RED
    assert v.incomplete == ()
    assert v.malformed == (("objection_thresholds", "'texto'"),)


def test_objection_thresholds_parcial_dict_sigue_siendo_amarillo():
    """Guarda de regresión: el fix de malformed no debe sobre-alcanzar y volver
    rojo un dict genuinamente parcial -- eso sigue siendo el caso amarillo
    ya cubierto por test_objection_thresholds_parcial_es_amarillo_no_rojo."""
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    (v,) = verdicts_for([tool], {"objection_thresholds": {"cfo": 1.0}})
    assert v.state == YELLOW
    assert v.malformed == ()
    assert v.incomplete == (("objection_thresholds", ("comercial", "comprador", "planner")),)


def test_declaracion_huerfana_con_valor_presente_no_bloquea():
    """Cuando una tool declara un required_client_param que el catalogo no
    conoce (spec_for devuelve None) y la clave SI esta en params, el motor no
    adivina: lo trata como presente y sigue de largo, sin agregarlo a ningun
    campo de falla. El veredicto puede salir verde aunque el valor nunca haya
    sido validado. Es a proposito -- la capa de job (discovery_brief_job.verify,
    en una tarea posterior) es quien reporta esto como fallo duro de QA, no
    este motor puro."""
    tool = _FakeTool("forecast", ("un_parametro_que_el_catalogo_no_conoce",))
    (v,) = verdicts_for([tool], {"un_parametro_que_el_catalogo_no_conoce": 42})
    assert v.state == GREEN
    assert v.missing == ()
    assert v.malformed == ()


def test_ranking_por_cuantas_tools_desbloquea():
    tools = [
        _FakeTool("a", ("holding_rate",)),
        _FakeTool("b", ("holding_rate",)),
        _FakeTool("c", ("order_cost",)),
    ]
    gaps = rank_gaps(verdicts_for(tools, {}))
    assert [(g.param, g.unlocks) for g in gaps] == [("holding_rate", 2), ("order_cost", 1)]
    assert gaps[0].tools == ("a", "b")


def test_causa_unica_dos_faltantes_no_acreditan_a_ninguno():
    """Pedir UNO deja la herramienta roja igual, así que acreditarle el
    desbloqueo prometería algo que no va a ocurrir. Es la regla que en
    intake_scan quedó reimplementada por separado en cuatro rankers."""
    tools = [
        _FakeTool("dos_faltantes", ("holding_rate", "order_cost")),
        _FakeTool("uno_solo", ("holding_rate",)),
    ]
    gaps = rank_gaps(verdicts_for(tools, {}))
    assert [(g.param, g.unlocks) for g in gaps] == [("holding_rate", 1)]
    assert gaps[0].tools == ("uno_solo",)


def test_fuera_de_rango_tambien_bloquea_la_acreditacion():
    """Una tool con un parámetro faltante Y otro fuera de rango no se desbloquea
    pidiendo el faltante: el otro sigue corrompido."""
    tools = [_FakeTool("mixta", ("holding_rate", "order_cost"))]
    gaps = rank_gaps(verdicts_for(tools, {"holding_rate": 25.0}))
    assert gaps == ()


def test_malformado_tambien_bloquea_la_acreditacion():
    """Mismo razonamiento: pedir el faltante deja el valor basura donde estaba."""
    tools = [_FakeTool("mixta", ("holding_rate", "order_cost"))]
    gaps = rank_gaps(verdicts_for(tools, {"holding_rate": True}))
    assert gaps == ()


def test_sin_perfil_no_produce_ranking_de_parametros():
    """La acción es crear el perfil; rankear preguntas sería el consejo equivocado."""
    tools = [_FakeTool("a", ("holding_rate",))]
    assert rank_gaps(verdicts_for(tools, None)) == ()


def test_empates_se_rompen_por_nombre_de_parametro():
    tools = [_FakeTool("x", ("order_cost",)), _FakeTool("y", ("holding_rate",))]
    gaps = rank_gaps(verdicts_for(tools, {}))
    assert [g.param for g in gaps] == ["holding_rate", "order_cost"]


def test_el_summary_siempre_lleva_numerador_y_denominador():
    tools = [_FakeTool("a", ("holding_rate",)), _FakeTool("b"), _FakeTool("c")]
    r = build_report(tools=tools, params={})
    assert "1 de 3" in r.summary
    assert r.n_tools_evaluated == 1
    assert r.n_tools_total == 3


def test_sin_ninguna_declaracion_no_dice_que_esta_todo_bien():
    """Cero evaluadas no es 'el perfil alcanza para todo'. No se evaluó nada."""
    r = build_report(tools=[_FakeTool("a"), _FakeTool("b")], params={"holding_rate": 0.25})
    assert r.n_tools_evaluated == 0
    assert r.outcome.status == "executed"
    assert "no se evaluó" in r.outcome.summary.lower()


def test_con_huecos_el_outcome_es_un_menu_rankeado():
    tools = [_FakeTool("a", ("holding_rate",)), _FakeTool("b", ("holding_rate",))]
    r = build_report(tools=tools, params={})
    assert r.outcome.status == "options"
    assert r.outcome.options[0].action == "ask_param:holding_rate"
    assert r.outcome.options[0].recommended is True


def test_la_opcion_dice_que_cifra_se_mueve():
    """Sin eso la entrevista es una lista de nombres sin razón para el cliente."""
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    assert "costo de mantener" in r.outcome.options[0].summary


def test_sin_perfil_propone_crear_el_perfil_no_preguntar_numeros():
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params=None, client_id="acme")
    assert r.has_profile is False
    assert r.outcome.status == "options"
    assert r.outcome.options[0].action == "create_profile:acme"


def test_el_residual_declara_que_no_juzga_la_verdad():
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    texto = " ".join(res.description for res in r.outcome.residuals).lower()
    assert "verdad" in texto


def test_el_reporte_lleva_el_backlog_de_no_declarados():
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params={})
    assert "review_period" in [s.name for s in r.undeclared_use]


def test_build_report_es_determinista():
    tools = [_FakeTool("b", ("order_cost",)), _FakeTool("a", ("holding_rate",))]
    uno = build_report(tools=tools, params={})
    dos = build_report(tools=list(reversed(tools)), params={})
    assert uno.summary == dos.summary
    assert [g.param for g in uno.gaps] == [g.param for g in dos.gaps]
