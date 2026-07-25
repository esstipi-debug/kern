"""La invariante: ninguna capa afirma algo que el titulo no dice.

Cuatro correcciones seguidas sobre el prototipo de la radiografia resultaron
ser el mismo error cuatro veces -- origen, elaborados, variante y region --
y las cuatro tenian la misma forma: rellenar un campo ausente con una
suposicion. Este archivo convierte esa regla en algo que se pone rojo.

El test central es ``test_todo_atributo_afirmado_esta_literalmente_en_el_titulo``:
es una propiedad sobre TODO el corpus, no una lista de casos, asi que cubre
tambien las capas que todavia no existen. Cualquier atributo futuro que se
sume a ``VedaAssessment`` queda bajo la misma exigencia con solo agregarlo a
``ATRIBUTOS``.

El corpus son titulos reales, leidos de los feeds publicos de
puntomarinochile.cl y rapa-nui.cl el 2026-07-25. Se fijan aqui a proposito
en vez de descargarlos: un test no debe depender de la red ni cambiar de
resultado porque un comerciante edito una ficha.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.regulatory import veda_calendar as vc

CONFIG = str(Path(__file__).resolve().parents[1] / "config")
HOY = date(2026, 7, 25)

ATRIBUTOS = ("especie", "origen", "variante", "region", "elaborado")

CORPUS = (
    # -- sin ninguna declaracion: el caso que rompio el prototipo cuatro veces
    "Merluza Apanada 1 Kg",
    "Ostion B con Coral 1 Kg",
    "Ostion Media Concha 1 Kg",
    "Locos Cocidos Conserva",
    "Machas Media Concha Jumbo 12 u",
    "Chorito IQF Congelado 1 Kg",
    "Centolla Carne",
    "Langostino entero u15 2kg. (caja x 12 kilos)",
    "Empanada Camaron Queso 10 U",
    "Gyozas de Camaron 30 U",
    # -- con origen declarado
    "Ostion Sin Coral Importado 1 Kg",
    "Merluza importada Filete Con Piel 1 Kg",
    "Camaron 36/40 Ecuatoriano Crudo Pelado 1 Kg",
    "Camaron 36/40 Asiatico Cocido pelado 1 Kg",
    # -- con variante declarada
    "Merluza Austral Filete Con Piel",
    # -- con calibre, que implica origen sin nombrarlo
    "Camaron 21/25 crudo con cascara",
    "Camaron 100/200 Cocido Pelado 500 G",
    # -- el titulo se contradice a si mismo: el tercer estado
    "Camaron 36/40 nacional 1 Kg",
    "Merluza austral importada 1 Kg",
    # -- de Rapa-Nui
    "Camaron nacional primera 1kg",
    "Filete de Merluza Austral 1kg",
    "Locos al natural 1lt",
    "Filete de congrio nacional 1kg",
    "Almeja importada sin concha 1kg",
)


@pytest.fixture(scope="module")
def registry():
    return vc.load_registry(CONFIG)


def _assess(title, registry):
    lexicon, vedas = registry
    return vc.assess(title, lexicon, vedas, HOY)


# --------------------------------------------------------------------------
# La propiedad. Si algo se rompe, empieza por aca.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title", CORPUS)
def test_todo_atributo_afirmado_esta_literalmente_en_el_titulo(title, registry):
    """Para cada atributo que la evaluacion declara, su evidencia debe ser un
    fragmento literal del titulo. Es la unica forma de que 'declarado' no
    degenere en 'inferido con confianza'.

    Vale igual para las senales en conflicto: una contradiccion que se apoya
    en evidencia inventada no es mejor que una afirmacion inventada."""
    assessment = _assess(title, registry)
    if assessment is None:
        return
    folded = vc.fold(title)
    for name in ATRIBUTOS:
        attr = getattr(assessment, name)
        for value, evidence in attr.conflicts:
            assert evidence in folded, (
                f"{name}: la senal en conflicto {value!r} dice apoyarse en "
                f"{evidence!r}, que NO aparece en {folded!r}"
            )
        if not attr.declared:
            assert attr.value is None, f"{name}: valor sin evidencia -> {attr.value!r}"
            continue
        assert attr.evidence, f"{name}: declarado sin evidencia"
        assert attr.evidence in folded, (
            f"{name}={attr.value!r} dice apoyarse en {attr.evidence!r}, "
            f"que NO aparece en {folded!r}"
        )


@pytest.mark.parametrize("title", CORPUS)
def test_un_atributo_no_declarado_nunca_trae_valor(title, registry):
    assessment = _assess(title, registry)
    if assessment is None:
        return
    for name in ATRIBUTOS:
        attr = getattr(assessment, name)
        assert attr.declared or attr.value is None


@pytest.mark.parametrize("title", CORPUS)
def test_los_tres_estados_son_exhaustivos_y_excluyentes(title, registry):
    """Tres estados, nunca dos y nunca cuatro. Si alguien agrega un cuarto o
    colapsa dos, esto se cae."""
    assessment = _assess(title, registry)
    if assessment is None:
        return
    for name in ATRIBUTOS:
        attr = getattr(assessment, name)
        assert attr.state in ("declarado", "no_declarado", "contradicho")
        assert sum((attr.declared, attr.contradicted, attr.state == "no_declarado")) == 1


# --------------------------------------------------------------------------
# Los cuatro errores concretos, cada uno con su regresion
# --------------------------------------------------------------------------

def test_origen_no_declarado_nunca_alcanza_critico(registry):
    """CRITICO afirma que la prohibicion alcanza al producto. Sin saber si el
    recurso es chileno no se puede afirmar eso: el techo es ALTO."""
    a = _assess("Locos Cocidos Conserva", registry)
    assert a.origen.declared is False
    assert a.severidad == "ALTO"
    assert a.tipo == "extractiva"


def test_importado_sale_del_alcance_de_la_veda_chilena(registry):
    a = _assess("Ostion Sin Coral Importado 1 Kg", registry)
    assert (a.origen.value, a.origen.evidence) == ("importado", "importado")
    assert a.severidad == "BAJO"


def test_calibre_implica_importado_y_su_evidencia_es_el_calibre(registry):
    """La regla de formato debe citar lo que capturo, no el patron."""
    a = _assess("Camaron 21/25 crudo con cascara", registry)
    assert a.origen.value == "importado"
    assert a.origen.evidence == "21/25"


def test_grado_comercial_implica_camaron_nacional(registry):
    a = _assess("Camaron nacional primera 1kg", registry)
    assert a.origen.value == "nacional"


def test_origen_contradicho_no_elige_ganador_ni_sube_la_severidad(registry):
    """"Camaron 36/40 nacional" afirma origen nacional mientras su calibre es
    la convencion del camaron de cultivo importado. Elegir uno en silencio
    seria relleno; el resultado correcto es el tercer estado."""
    a = _assess("Camaron 36/40 nacional 1 Kg", registry)
    assert a.origen.state == "contradicho"
    assert a.origen.value is None
    assert {value for value, _ in a.origen.conflicts} == {"nacional", "importado"}
    # mismo techo que un origen ausente: sin base para afirmar exposicion
    assert a.severidad == "INFORMATIVO"
    assert "origen" in a.contradichos
    assert "origen" not in a.sin_declarar


def test_la_contradiccion_se_reporta_como_defecto_de_ficha(registry):
    """Un dato que falta se le pregunta al cliente; uno contradicho se le
    muestra. Por eso la nota los distingue en vez de decir 'desconocido'."""
    a = _assess("Merluza austral importada 1 Kg", registry)
    nota = next(n for n in a.notas if n.startswith("ORIGEN CONTRADICHO"))
    assert "austral" in nota and "importada" in nota
    assert "defecto en su propia ficha" in nota


def test_un_solo_origen_no_es_contradiccion(registry):
    for title in ("Ostion Sin Coral Importado 1 Kg", "Camaron nacional primera 1kg"):
        a = _assess(title, registry)
        assert a.origen.state == "declarado", title
        assert a.origen.conflicts == ()


def test_elaborado_solo_pesa_en_veda_extractiva(registry):
    """Una conserva de loco entra en el alcance de la extractiva; una empanada
    de camaron no cambia nada porque su veda es biologica."""
    loco = _assess("Locos Cocidos Conserva", registry)
    assert loco.elaborado.declared
    assert any("derivados" in n for n in loco.notas)

    empanada = _assess("Empanada Camaron Queso 10 U", registry)
    assert empanada.elaborado.declared
    assert empanada.tipo == "biologica"
    assert not any("derivados" in n for n in empanada.notas)


def test_variante_sin_declarar_reporta_todas_las_ventanas(registry):
    """'Merluza Apanada' puede ser austral (7d) o comun (38d). Elegir una en
    silencio fue el bug; reportar ambas es el arreglo."""
    a = _assess("Merluza Apanada 1 Kg", registry)
    assert a.variante.declared is False
    nota = next(n for n in a.notas if n.startswith("VARIANTE SIN DECLARAR"))
    assert "austral" in nota and "comun" in nota


def test_variante_declarada_no_arrastra_la_nota(registry):
    a = _assess("Merluza Austral Filete Con Piel", registry)
    assert (a.variante.value, a.variante.evidence) == ("austral", "austral")
    assert not any(n.startswith("VARIANTE SIN DECLARAR") for n in a.notas)


def test_region_sin_declarar_reporta_cobertura_y_no_afirma_apertura(registry):
    """Lo que no esta cerrado por decreto verificado es DESCONOCIDO. Un
    informe que lo llame 'abierto' esta inventando."""
    a = _assess("Machas Media Concha Jumbo 12 u", registry)
    assert a.region.declared is False
    nota = next(n for n in a.notas if n.startswith("REGION SIN DECLARAR"))
    assert "desconocido, no abierto" in nota
    assert "abierta" not in nota
    assert a.regiones_cerradas < frozenset(vc.REGIONES_COSTERAS)


def test_ruido_suprimido_cuando_el_origen_ya_saco_la_fila(registry):
    """Si ninguna veda chilena toca el producto, da igual de que region o
    variante sea: esas notas serian ruido."""
    a = _assess("Merluza importada Filete Con Piel 1 Kg", registry)
    assert a.severidad == "NULO"
    assert a.notas == ()


# --------------------------------------------------------------------------
# El registro tiene que sostener sus propias promesas
# --------------------------------------------------------------------------

def test_toda_veda_declara_regiones_validas(registry):
    _, vedas = registry
    for veda in vedas["vedas"]:
        assert veda["regiones"], f"{veda['decreto']}: sin regiones"
        desconocidas = set(veda["regiones"]) - set(vc.REGIONES_COSTERAS)
        assert not desconocidas, f"{veda['decreto']}: regiones invalidas {desconocidas}"


def test_alcanza_derivados_es_explicito_y_coherente_con_el_tipo(registry):
    _, vedas = registry
    for veda in vedas["vedas"]:
        assert "alcanza_derivados" in veda, f"{veda['decreto']}: falta alcanza_derivados"
        assert veda["alcanza_derivados"] == (veda["tipo"] == "extractiva"), (
            f"{veda['decreto']}: alcanza_derivados contradice el tipo. Si es "
            "deliberado, documentalo en la fila y ajusta este test."
        )


def test_toda_fila_lleva_decreto_fuente_y_fecha_de_verificacion(registry):
    _, vedas = registry
    for veda in vedas["vedas"]:
        for field in ("decreto", "fuente", "verificado_el"):
            assert veda.get(field), f"{veda.get('decreto', '?')}: falta {field}"


def test_sin_veda_vigente_no_es_lo_mismo_que_no_encontrada(registry):
    """El bloque existe para poder afirmar 'verifique que no tiene veda'.
    Sin razon y sin fecha esa afirmacion no vale nada."""
    _, vedas = registry
    assert vedas["sin_veda_vigente"]
    for row in vedas["sin_veda_vigente"]:
        assert row.get("razon"), f"{row['especie']}: sin razon"
        assert row.get("verificado_el"), f"{row['especie']}: sin verificado_el"
