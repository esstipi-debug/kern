"""Tests del motor puro de discovery_brief. Nada aquí toca disco.

La excepción es el guard de citas, que por definición tiene que leer los
archivos citados: vive en ``tests/_citation_anchor.py`` para que el módulo puro
no gane un importador que toque disco.
"""
import re
from dataclasses import dataclass

# ``tests/`` está en sys.path via conftest (misma convención que _no_core).
from _citation_anchor import AnchorError, holds_value, is_anchor, resolve_constants

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
    assert s.subkeys == ("comprador", "planner", "cfo", "comercial", "transporte", "merma")


def test_spec_for_devuelve_none_para_un_desconocido():
    assert spec_for("no_existe_este_parametro") is None


def test_las_areas_copiadas_no_derivaron_de_hats():
    """El motor puro copia HAT_KEYS en vez de importarlo (src.hats arrastra numpy
    y scipy). Esta aserción es lo único que impide que la copia derive."""
    from src.hats import HAT_KEYS

    assert spec_for("objection_thresholds").subkeys == HAT_KEYS


_CITA_POR_LINEA = re.compile(r"\.py:\d+")


def test_ninguna_cita_quedo_anclada_a_un_numero_de_linea():
    """El formato viejo no puede volver por la puerta de atras.

    Sin este test una cita reintroducida como 'archivo.py:229' caeria en la
    rama de "sitio sin anclar" del test de abajo y quedaria SIN VERIFICAR, en
    verde -- el mismo hueco de salto silencioso que el Fix Round 4 cerro para
    los sitios que no parseaban.

    El anclaje por linea se abandono porque se rompia sin que el valor citado
    cambiara: agregar `service_note` a JobReport el 2026-08-31 corrio seis
    lineas las tres citas a jobs/inventory_optimization.py. Es la tercera vez
    (ver PRs #160/#162/#164 y #200).
    """
    ofensores = [
        f"{spec.name}: {site}"
        for spec in CATALOG + UNDECLARED_USE
        for _tool_key, _valor, site in spec.defaults
        if _CITA_POR_LINEA.search(site)
    ]
    assert not ofensores, (
        "Estas citas volvieron al numero de linea; anclarlas al simbolo "
        f"(archivo.py::funcion.parametro): {ofensores}"
    )


def test_todas_las_citas_de_simbolo_apuntan_al_valor_real():
    """Cada cita 'archivo.py::simbolo' resuelve al valor que el codigo tiene hoy.

    LA PROPIEDAD: el test falla cuando el valor citado ya no esta en el codigo
    -- porque cambio, porque renombraron el simbolo, o porque borraron la
    funcion entera. Lo que ya NO lo hace fallar es insertar lineas arriba del
    punto citado ni reformatear la linea, que era la fragilidad del anclaje por
    numero.

    El anclaje por simbolo ademas distingue cosas que el texto no podia:
    src/powerbi_export.py declara `order_cost: float = 1000.0,` en DOS lugares
    byte-identicos (analyze_product y build_powerbi_dataset). Con una busqueda
    por texto, borrar una de las dos dejaba el guard en verde.

    No hay excepcion silenciosa: un sitio que no se deja resolver es un FALLO,
    nunca un `continue`. Solo los sitios legitimamente sin simbolo (hoy los dos
    "(docstring)" de client_profile, que citan prosa y no codigo) se cuentan
    como saltados, y se nombran en el mensaje.
    """
    verificadas = 0
    sin_anclar: list[str] = []

    for spec in CATALOG + UNDECLARED_USE:
        for _tool_key, valor, site in spec.defaults:
            if not is_anchor(site):
                sin_anclar.append(f"{spec.name}: {site}")
                continue
            try:
                constantes = resolve_constants(site)
            except AnchorError as exc:
                raise AssertionError(f"{spec.name}: {exc}") from None
            if not holds_value(constantes, valor):
                raise AssertionError(
                    f"{spec.name}: {site} cita el valor {valor!r}, pero el "
                    f"simbolo hoy tiene {list(constantes)!r}"
                )
            verificadas += 1

    # El unico salto legitimo es citar PROSA en vez de codigo. Cualquier otro
    # sitio sin anclar seria una cita que se cuenta como saltada y queda sin
    # verificar -- el hueco que este test existe para no tener.
    assert all(s.endswith("(docstring)") for s in sin_anclar), (
        f"Sitios sin anclar que no citan un docstring: {sin_anclar}"
    )
    assert verificadas, (
        "Ninguna cita quedo anclada a un simbolo: el guard estaria pasando en "
        f"vacio. Sitios sin anclar: {sin_anclar}"
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
    assert v.incomplete == (
        ("objection_thresholds", ("comercial", "comprador", "merma", "transporte")),)


def test_objection_thresholds_completo_es_verde():
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    completo = {k: 1.0 for k in ("comprador", "planner", "cfo", "comercial", "transporte", "merma")}
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
    assert v.incomplete == (
        ("objection_thresholds", ("comercial", "comprador", "merma", "planner", "transporte")),)


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


# --- Fix round 1: la auditoría no puede reclamar más cobertura de la que logró. ---


def test_summary_sin_perfil_no_dice_nada_que_preguntar():
    """El summary manda 'Evaluadas X de Y' como campo siempre-cierto; si termina
    en 'nada que preguntar' mientras el outcome ofrece crear el perfil, el
    operador que solo lee el summary se queda sin acción."""
    r = build_report(tools=[_FakeTool("a", ("holding_rate",))], params=None, client_id="acme")
    assert "nada que preguntar" not in r.summary
    assert "crear" in r.summary.lower()
    assert "Evaluadas" in r.summary


def test_declarados_completos_pero_granular_incompleto_no_dice_cubre_todo():
    """objection_thresholds con 2 de 4 areas es amarillo -- 'cubre todo' mentiria
    sobre las dos areas que van a salir no_evaluable rio abajo."""
    tool = _FakeTool("cross_impact", ("objection_thresholds",))
    r = build_report(
        tools=[tool],
        params={"objection_thresholds": {"cfo": 1000.0, "planner": 500.0}},
    )
    assert r.gaps == ()
    assert r.outcome.status == "executed"
    assert "cubre todo" not in r.outcome.summary.lower()
    assert "objection_thresholds" in r.outcome.summary


def test_perfil_totalmente_verde_sigue_diciendo_que_cubre_todo():
    """Regresion: sin amarillos ni huecos, la afirmacion de cobertura completa
    sigue siendo honesta y el fix no debe volverse pesimista de mas."""
    tool = _FakeTool("inventory_optimization", ("holding_rate", "service_level"))
    r = build_report(
        tools=[tool],
        params={"holding_rate": 0.28, "service_level": 0.97},
    )
    assert r.gaps == ()
    assert r.outcome.status == "executed"
    assert "cubre todo" in r.outcome.summary.lower()


def test_cinco_huecos_trunca_a_top_3_pero_lo_dice_en_el_summary():
    """El cap de 3 esta bien; ocultar que hay 2 mas no lo esta."""
    tools = [
        _FakeTool("a", ("holding_rate",)),
        _FakeTool("b", ("service_level",)),
        _FakeTool("c", ("order_cost",)),
        _FakeTool("d", ("goodwill",)),
        _FakeTool("e", ("objection_thresholds",)),
    ]
    r = build_report(tools=tools, params={})
    assert len(r.gaps) == 5
    assert len(r.outcome.options) == 3
    assert "3" in r.outcome.summary
    assert "5" in r.outcome.summary
