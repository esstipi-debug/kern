"""Los 25 casos filosos de la especificacion, todos titulos literales.

El corpus salio de una cosecha real sobre 1279 SKUs en 10 catalogos
chilenos de congelados. Cada caso fue elegido porque DISCRIMINA entre
implementaciones plausibles: un titulo que toda implementacion razonable
resuelve bien no esta aca.

La invariante bajo prueba es la gemela de la de
``tests/test_veda_calendar_declared_only.py``: si el peso no se deriva con
certeza de evidencia literal, se devuelve cuarentena. Nunca 1 kg por
defecto, nunca el promedio de un rango, nunca volumen convertido a masa.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.pricing_intel.weight_format import weight_kg

D = Decimal


# (titulo, kg esperado o None, motivo esperado o None, que rompe si se hace mal)
CASOS = [
    ("Langostino entero u15 2kg. (caja x 12 kilos)", D("2"), None,
     "ultimo match o match mayor -> 12 kg, seis veces el peso real"),
    ("Camaron 100/200 Cocido Pelado 500 G", D("0.5"), None,
     "primer numero -> 100; discrimina calibre antes de peso"),
    ("Camaron 21/25 crudo con cascara", None, "SIN_FORMATO",
     "aceptar el calibre como peso, o asumir 1 kg"),
    ("Salmon Ahumado Laminado 1,130 Kg", D("1.130"), None,
     "coma como separador de miles -> 1130 kg"),
    ("Queso Crema Nacional 1,36kg", D("1.36"), None,
     "regex de un solo decimal captura 1,3 y descarta el 6: 4,4% de error mudo"),
    ("Bocaditos Papa Y Queso Bembrazil Bolsa De 1.05 Kg", D("1.05"), None,
     "locale es-CL sobre el punto -> 105 kg"),
    ("LOCOS COCIDOS CONSERVA FRASCO 1.000CC UNIDAD", None, "SIN_FORMATO",
     "float('1.000') -> 1 cc, o cc convertido a gramos: peso drenado inventado"),
    ("QUESO T10 LLIFEN COLUN 10 KG APP", None, "PESO_VARIABLE",
     "'l' dentro de LLIFEN -> 10 litros en un queso; y APP es peso variable"),
    ("Azucar Blanca 10 Kg (Pallet 100 Mangas, 10 Kg Por Manga)", None, "MULTIPLE_EMPAQUE",
     "el vendible puede ser pallet, manga o unidad"),
    ("Arroz Klick 1 Kg - Manga de 10 Kg (10 Unidades por Caja)", None, "MULTIPLE_EMPAQUE",
     "ultimo match -> 10 kg"),
    ("Papas Prefritas 13x13mm 4X2,5KG Global Frozen", D("10"), None,
     "las dos x significan cosas opuestas en el mismo titulo: corte y multiplicador"),
    ("Papas Prefritas 6X6 Bolsa De 2.5 Kg", D("2.5"), None,
     r"\d+\s*[A-Za-z]+ captura literalmente '6 Bolsa'"),
    ("Demi &#8211; Baguette", None, "SIN_FORMATO",
     "regex sobre el JSON crudo -> 8211 g de un pan que no declara peso"),
    ("Salmon Atlantico Porcion Sin Piel 1&#215;5 IVP Congelado Caja 5 kg", D("5"), None,
     "sin decodificar la entidad se ve 215 -> '215 g'"),
    ("CARNE MOLIDA 4% MATERIA GRASA CONGELADA CAJA 5KG", D("5"), None,
     "primer numero -> 4 kg; discrimina heuristica posicional"),
    ("Salmon Slice Carpaccio Ahumado en Frio Laminado 250g 10kg.", None, "MASAS_EN_CONFLICTO",
     "cualquier desempate inventa una decision con 40x de diferencia"),
    ("ATUN STEAK 170-220G IVP UNIDAD", None, "RANGO_INDETERMINADO",
     "un mapa fragmento->formato colapsa este caso con su gemelo, que difiere 25x"),
    ("ATUN STEAK 170-220G IVP CAJA 5KG", D("5"), None,
     "si el rango gana, se pierde la caja"),
    ("Ostion Sin Coral 1KG", D("1"), None,
     "regex sensible a mayusculas lo pierde entero y el feed no trae weight"),
    ("Congrio Dorado HGT 3 Kg UP", None, "PISO_ABIERTO",
     "3 Kg parsea limpio pero es el calibre minimo por pieza, sin techo"),
    ("CAMARON ECUATORIANO APANADO PRECOCIDO 1LB REAL", D("0.45359237"), None,
     r"regex de volumen \d+\s*L -> 1 litro"),
    ("Volcan Chocolate 2 unid*90gr.", D("0.18"), None,
     "leer solo 90 g entrega la mitad del contenido"),
    ("Champinon  Laminado *1 Kilo", D("1"), None,
     "el mismo asterisco que en el caso anterior es ruido decorativo"),
    ("Papas Pre-fritas 2.5  kilos 12*12", D("2.5"), None,
     "orden invertido y doble espacio entre numero y unidad"),
    ("Ostion Carne del Norte con Coral Congelado (500g / 1kg)", None, "MASAS_EN_CONFLICTO",
     "son dos SKUs reales; 'ultimo match gana' asigna 1 kg a algo que tambien se vende en 500 g"),
]


@pytest.mark.parametrize("title,kg,motivo,rompe", CASOS, ids=[c[0][:38] for c in CASOS])
def test_casos_filosos_de_la_cosecha(title, kg, motivo, rompe):
    out = weight_kg(title)
    if kg is not None:
        assert out.kg == kg, f"{title!r}: {rompe}"
        assert out.quarantine is None
    else:
        assert out.kg is None, f"{title!r}: {rompe}"
        assert out.quarantine == motivo, f"{title!r}: {rompe}"


# --------------------------------------------------------------------------
# La invariante, como propiedad sobre todo el corpus
# --------------------------------------------------------------------------

TODOS = [c[0] for c in CASOS]


@pytest.mark.parametrize("title", TODOS)
def test_los_tres_estados_son_exhaustivos_y_excluyentes(title):
    out = weight_kg(title)
    assert out.state in ("kg", "per_kg", "cuarentena")
    assert sum((out.kg is not None, out.per_kg, out.quarantine is not None)) == 1


@pytest.mark.parametrize("title", TODOS)
def test_nunca_se_devuelve_un_peso_fuera_de_lo_plausible(title):
    """Ningun alimento congelado de catalogo pesa 5 gramos ni 25 kilos. Un
    resultado fuera de esa banda es siempre un error de parseo, no un
    producto raro."""
    out = weight_kg(title)
    if out.kg is not None:
        assert Decimal("0.005") <= out.kg <= Decimal("25"), f"{title!r} -> {out.kg}"


@pytest.mark.parametrize("title", TODOS)
def test_toda_cuarentena_trae_motivo_tipado(title):
    out = weight_kg(title)
    if out.quarantine is not None:
        assert out.quarantine.isupper()
        assert out.kg is None and out.per_kg is False


# --------------------------------------------------------------------------
# Rechazos que no se negocian
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Locos al natural 1lt",
    "Pulpo en conserva 1lt",
    "Locos Cocidos - Frasco 700 cc",
    "Aceite Vegetal Generico 900 Cc",
    "Helados Bravissimo 900 ml.",
    "Degustador 1.0 oz 100 un",
    "Vaso Domo 235 ml",
])
def test_el_volumen_nunca_se_convierte_a_masa(title):
    """El peso drenado de una conserva no esta publicado en ningun campo de
    ningun catalogo. Convertir 1 lt en 1 kg es inventar el dato."""
    assert weight_kg(title).kg is None


@pytest.mark.parametrize("title", [
    "Empanada Camaron Queso 10 U",
    "48 empanaditas de queso",
    "Machas media concha (20 unid)",
    "Alga Nori Blue Bolsa 100 Hojas",
    "Toalla Jumbo 250 mts 2 UN",
])
def test_el_conteo_y_la_longitud_nunca_son_masa(title):
    assert weight_kg(title).kg is None


@pytest.mark.parametrize("title", [
    "Gyozas de Camaron 30 U",       # 'oz' dentro de Gyozas
    "Albacora Steack",              # 'lb' dentro de Albacora
    "Salmon Carpaccio Laminado",    # 'cc' dentro de Carpaccio
])
def test_falsos_amigos_de_unidad_por_substring(title):
    """28 SKUs reales que una busqueda de unidad sin frontera convierte en
    unidades falsas. La regla del run alfabetico completo los elimina."""
    assert weight_kg(title).kg is None


# --------------------------------------------------------------------------
# Precio por kilo: el 84% del catalogo ancla
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Esparrago kilo",
    "Reineta Filete  KILO",
    "Trucha Trozos al Natural kg",
    "Esparrago Kgr",
    "Camaron 36/40 Crudo Pelado desvenado  Imp.kilo",
])
def test_unidad_desnuda_es_base_de_precio_no_peso_de_envase(title):
    """El ultimo caso es el que obligo a evaluar el basis DESPUES del
    mascarado: mientras 36/40 siga siendo un numero visible, la regla de
    'unidad sin numero' no puede dispararse. La espec lo ordenaba al reves y
    se contradecia a si misma."""
    out = weight_kg(title)
    assert out.per_kg is True, title
    assert out.kg is None


def test_nota_del_vendedor_declara_la_base_de_precio():
    """81 de los 96 SKUs de rapa-nui.cl. Leer su '1kg' como envase es la
    trampa mas cara del universo cosechado."""
    out = weight_kg("Albacora Steack 1kg", description="Nota: Valor publicado por kilo.")
    assert out.per_kg is True
    assert out.kg is None


# --------------------------------------------------------------------------
# Contraste con el campo estructurado del feed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("title,feed", [
    ("Salmon salar ahumado laminado 1kg", Decimal("0.5")),
    ("Aros de cebolla apanados 1kg", Decimal("0.6")),
    ("Porciones de salmon salar 1kg", Decimal("1.4")),
])
def test_titulo_perfecto_que_contradice_al_feed_va_a_cuarentena(title, feed):
    """El titulo parsea limpio y esta mal. Solo el contraste lo detecta."""
    assert weight_kg(title, feed_weight_kg=feed).quarantine == "CONTRADICCION_FUENTE"


def test_weight_vacio_es_ausencia_de_evidencia_no_cero_kilos():
    """mariscosdelaconcha publica grams=0 en 209 de 212 variantes. Tratar ese
    cero como un peso convierte todo su catalogo en cuarentena falsa."""
    out = weight_kg("Ostion Sin Coral 1KG", feed_weight_kg=Decimal("0"))
    assert out.kg == Decimal("1")
