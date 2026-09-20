"""La explicacion publicada no puede despegarse de la medicion que la sostiene.

`documentation/KERN_POR_QUE_BAJO_EL_PISO.md` contesta la pregunta que
`KERN_CAPTURA_BAJO_DISRUPCION.md` dejo escrita en su seccion de "que no dice": la
captura limpia da -40,6% y la linea base no explica por que. Los numeros salen de
`documentation/lineas_base/por_que_bajo_el_piso.json`, y nada obligaba a que siguieran
coincidiendo -- mismo mecanismo que `tests/test_captura_bajo_disrupcion_publicada.py`
y que `reflect_lessons --check` sobre GRAPH_LESSONS: el documento se compara contra su
fuente, no contra si mismo.

POR QUE NO SE RE-MIDE ACA
-------------------------
Cada semilla corre el piso, el oraculo y TRES carriles de Kern completos: ~18 pasadas
del orquestador. Sobre cinco semillas son decenas de minutos. Un gate que nadie corre
es peor que no tenerlo, asi que lo que se verifica con lo commiteado es que el
documento cite lo que la medicion dice Y que la medicion sea de verdad el experimento
que el documento afirma haber hecho -- que es la parte que puede hacer que el numero
signifique otra cosa.

LAS TRES FORMAS DE QUE ESTO MIENTA SIN QUE NADIE SE DE CUENTA
-------------------------------------------------------------
1. Que la medicion no sea del MISMO experimento que produjo el -40,6%. Si cambian los
   SKU, las semanas, el replan o el nivel de suciedad, estos carriles explican otra
   cosa y el documento igual se lee como si explicara aquel numero.
2. Que un contrafactico no sea un contrafactico. Un carril identico al base no midio
   nada, y su "resultado" seria la captura de Kern con otra etiqueta -- exactamente el
   defecto que `ShockImpact.reached` existe para declarar en el marcador.
3. Que el documento presente como CONFIRMADA una hipotesis que la medicion refuto. La
   del buffer chico es la lectura natural de "Kern compra de menos", y la medicion dice
   lo contrario; un documento que se olvide de decirlo deja a alguien agrandando
   buffers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
LINEA_BASE = RAIZ / "documentation" / "lineas_base" / "por_que_bajo_el_piso.json"
LINEA_BASE_LIMPIA = RAIZ / "documentation" / "lineas_base" / "captura_limpia.json"
DOC = RAIZ / "documentation" / "KERN_POR_QUE_BAJO_EL_PISO.md"

COMO_SE_REGRABA = (
    "  PYTHONPATH=. python scripts/por_que_bajo_el_piso.py "
    "--json documentation/lineas_base/por_que_bajo_el_piso.json --markdown"
)


@pytest.fixture(scope="module")
def datos() -> dict:
    assert LINEA_BASE.is_file(), (
        f"falta {LINEA_BASE.relative_to(RAIZ)}. Se regraba con:\n{COMO_SE_REGRABA}"
    )
    return json.loads(LINEA_BASE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def limpia() -> dict:
    assert LINEA_BASE_LIMPIA.is_file(), f"falta {LINEA_BASE_LIMPIA.relative_to(RAIZ)}"
    return json.loads(LINEA_BASE_LIMPIA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def texto() -> str:
    assert DOC.is_file(), f"falta {DOC.relative_to(RAIZ)}"
    return DOC.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Es el mismo experimento que produjo el numero que dice explicar
# ---------------------------------------------------------------------------


def test_la_medicion_corre_el_MISMO_mundo_que_la_linea_base_limpia(datos, limpia):
    """Si no, estos carriles explican otra corrida y el documento igual se lee como si
    explicara el -40,6% publicado."""
    a, b = datos["contexto"], limpia["contexto"]
    for aqui, alla in (("n_skus", "n_skus"), ("n_proveedores", "n_proveedores"),
                       ("semanas", "semanas"), ("replan_cada", "replan_cada")):
        assert a[aqui] == b[alla], (
            f"la medicion usa {aqui}={a[aqui]!r} y la linea base limpia {b[alla]!r}: "
            "no explica ese numero, explica otro experimento"
        )
    assert a["nivel"] == 0, "el -40,6% es de L0; medir en otro nivel explica otra cosa"
    assert b["niveles"][0] == 0
    assert a["carril"] == "forced", "la linea base limpia se grabo en el carril forced"


def test_las_semillas_son_un_subconjunto_de_las_de_la_linea_base(datos, limpia):
    """Explicar un promedio con mundos que no estaban adentro es cambiar de tema."""
    ctx, base = datos["contexto"], limpia["contexto"]
    primera = base["semilla_inicial"]
    del_barrido = set(range(primera, primera + base["mundos"]))
    fuera = sorted(set(ctx["semillas"]) - del_barrido)
    assert not fuera, (
        f"las semillas {fuera} no estan en la linea base limpia "
        f"({primera}..{primera + base['mundos'] - 1})"
    )


def test_el_documento_DECLARA_que_mide_un_subconjunto(datos, limpia, texto):
    """El promedio de estos mundos NO es el publicado, y la diferencia es material:
    entre los cinco esta el peor mundo del barrido. Un documento que no lo diga deja
    leer su numero como si fuera el de 25 mundos."""
    n, total = datos["contexto"]["mundos"], limpia["contexto"]["mundos"]
    assert n < total, "si ya son todos los mundos, este test y su salvedad sobran"
    assert str(n) in texto and str(total) in texto, (
        f"el documento tiene que nombrar los dos tamanos ({n} medidos de {total} "
        "publicados) para que su promedio no se lea como el publicado"
    )


# ---------------------------------------------------------------------------
# 2. Cada contrafactico es de verdad un contrafactico
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("carril", ["arranque_piso", "cobertura_piso"])
def test_el_contrafactico_movio_el_mundo(datos, carril):
    """Un carril identico al base no midio nada: su resultado seria la captura de Kern
    con otra etiqueta. Es el mismo defecto que `ShockImpact.reached` declara en el
    marcador, aca en el experimento que lo interroga."""
    for mundo in datos["por_mundo"]:
        base, otro = mundo["kern"], mundo[carril]
        assert (base["unidades_pedidas"] != otro["unidades_pedidas"]
                or base["capture"] != otro["capture"]), (
            f"semilla {mundo['seed']}: el carril {carril!r} produjo exactamente la "
            "misma corrida que el base, asi que no midio su causa"
        )


def test_el_carril_de_cobertura_de_verdad_le_dio_la_cobertura_del_piso(datos):
    """El experimento afirma "con la cobertura exacta del piso". Si ese carril igual
    compra menos que el piso, no probo esa hipotesis y su refutacion no vale."""
    p = datos["promedios"]
    assert p["cobertura_piso"]["unidades_pedidas"] >= p["piso"]["unidades_pedidas"], (
        "el carril de cobertura compra menos que el piso: no es el experimento que el "
        "documento dice haber corrido"
    )
    assert datos["contexto"]["cobertura_del_piso_periodos"] == 5.0, (
        "naive_policy fija R = 5 x la demanda semanal; otro numero no es la cobertura "
        "del piso"
    )


def test_la_hipotesis_del_buffer_sale_REFUTADA_y_el_documento_lo_dice(datos, texto):
    """La lectura natural de "Kern compra de menos" es "agrandale el buffer", y la
    medicion dice lo contrario. Si el documento se olvida de decirlo, alguien va y
    agranda buffers."""
    p = datos["promedios"]
    assert p["cobertura_piso"]["capture"] <= p["kern"]["capture"], (
        "con la cobertura del piso la captura MEJORO: la hipotesis ya no esta refutada "
        "y el documento no puede seguir diciendo que si"
    )
    assert "refutad" in texto.lower(), (
        "el documento no dice en ninguna parte que la hipotesis quedo refutada"
    )


def test_el_stock_terminal_explica_por_que_comprar_mas_no_alcanza(datos):
    """El numero que sostiene la refutacion: el dinero extra del carril de cobertura no
    se convierte en venta sino en stock que nadie compra al cierre. Sin esta relacion,
    "compro mas y capturo peor" queda sin mecanismo."""
    p = datos["promedios"]
    assert p["cobertura_piso"]["stock_terminal_usd"] > p["piso"]["stock_terminal_usd"]
    assert p["techo"]["stock_terminal_usd"] == pytest.approx(0.0, abs=1e-6), (
        "el oraculo tiene que cerrar en cero: es lo que hace que el stock terminal sea "
        "una comparacion y no un numero suelto"
    )


# ---------------------------------------------------------------------------
# 3. El documento cita lo que la medicion dice
# ---------------------------------------------------------------------------


def test_el_documento_cita_la_frase_reconstruida_desde_la_medicion(datos, texto):
    from scripts.por_que_bajo_el_piso import frase

    esperada = frase(datos)
    assert esperada in texto, (
        "el documento no cita la frase de la medicion. Regenerala con:\n"
        f"{COMO_SE_REGRABA}\nEsperada:\n  {esperada}"
    )


def test_el_documento_nombra_como_reproducirlo(texto):
    """Un hallazgo que no se puede volver a correr es una opinion."""
    assert "scripts/por_que_bajo_el_piso.py" in texto


def test_el_deficit_entero_es_quiebre_y_el_documento_no_lo_reparte(datos, texto):
    """El hallazgo de la descomposicion: Kern gasta MENOS que el piso en las otras tres
    lineas y pierde mas que eso en venta perdida. Si algun dia el costo se reparte, el
    documento pasa a describir otra cosa."""
    p = datos["promedios"]
    for componente in ("procurement", "holding", "fixed"):
        assert p["kern"][componente] <= p["piso"][componente], (
            f"Kern ya no gasta menos que el piso en {componente}: la descomposicion "
            "publicada cambio de forma"
        )
    assert p["kern"]["shortage"] > p["piso"]["shortage"]
    assert p["kern"]["total"] > p["piso"]["total"]


def test_el_arranque_muerto_mejora_en_TODOS_los_mundos(datos):
    """Un promedio que mejora puede esconder mundos que empeoran; el hallazgo se
    publica como causa principal, asi que tiene que valer en cada uno."""
    peores = [m["seed"] for m in datos["por_mundo"]
              if m["arranque_piso"]["capture"] <= m["kern"]["capture"]]
    assert not peores, (
        f"en las semillas {peores} reemplazar las semanas 4-7 NO mejoro la captura; "
        "el arranque muerto no puede publicarse como la causa principal"
    )


# ---------------------------------------------------------------------------
# 4. El DESPUES: el arreglo se mide contra el mismo experimento, o no se mide
# ---------------------------------------------------------------------------
#
# El documento publica un antes y un despues. Esa lectura solo vale si las dos corridas
# son el MISMO experimento y difieren unicamente en el codigo -- si alguien regraba una
# con otras semillas, otra escala u otro nivel, la resta de 19,4 puntos deja de medir el
# arreglo y pasa a medir dos cosas distintas. Es la misma disciplina que
# `test_la_linea_base_limpia_es_comparable_con_la_de_shock` aplica a las dos capturas.

DESPUES = RAIZ / "documentation" / "lineas_base" / "por_que_bajo_el_piso_con_cobertura.json"


@pytest.fixture(scope="module")
def despues() -> dict:
    assert DESPUES.is_file(), (
        f"falta {DESPUES.relative_to(RAIZ)}. Se regraba con:\n"
        "  PYTHONPATH=. python scripts/por_que_bajo_el_piso.py "
        "--json documentation/lineas_base/por_que_bajo_el_piso_con_cobertura.json"
    )
    return json.loads(DESPUES.read_text(encoding="utf-8"))


def test_las_dos_corridas_son_el_mismo_experimento(datos, despues):
    for clave in ("mundos", "semillas", "n_skus", "n_proveedores", "semanas",
                  "replan_cada", "nivel", "carril", "cobertura_del_piso_periodos"):
        assert datos["contexto"][clave] == despues["contexto"][clave], (
            f"el antes y el despues difieren en {clave!r}: restarlos mediria eso, no "
            "el arreglo"
        )


@pytest.mark.parametrize("carril", ["piso", "techo", "arranque_piso"])
def test_los_carriles_de_CONTROL_salen_identicos(datos, despues, carril):
    """Los tres no pasan por el codigo que cambio -- el piso y el techo nunca, y
    `arranque_piso` usa la politica del piso en las semanas 4-7 y ya tiene seis
    periodos en las 8+, asi que nunca ve una cobertura. Que salgan identicos es lo que
    prueba que las dos mediciones son la misma; si se mueven, algo mas cambio y la
    resta no es atribuible al arreglo."""
    a, b = datos["promedios"][carril], despues["promedios"][carril]
    for campo in ("capture", "total", "fill", "unidades_pedidas"):
        assert a[campo] == pytest.approx(b[campo], rel=1e-9, abs=1e-9), (
            f"el carril de control {carril!r} movio {campo}: {a[campo]} -> {b[campo]}"
        )


def test_el_arreglo_mejoro_en_TODOS_los_mundos(datos, despues):
    """Un promedio que mejora puede esconder mundos que empeoran, y esto se publica
    como el arreglo de la causa principal."""
    antes = {m["seed"]: m["kern"]["capture"] for m in datos["por_mundo"]}
    peores = [m["seed"] for m in despues["por_mundo"]
              if m["kern"]["capture"] <= antes[m["seed"]]]
    assert not peores, f"en las semillas {peores} la cobertura NO mejoro la captura"


def test_el_contrafactico_que_midio_el_defecto_SE_ENCOGIO(datos, despues):
    """La firma interna de que el arreglo cierra la causa diagnosticada y no otra cosa.
    `arranque_piso` medía cuanto costaba el silencio de las semanas 4-7; si la cobertura
    lo llena, esa medicion tiene que valer MENOS que antes. Si no se encoge, el arreglo
    mejoro la captura por alguna otra via y el documento esta contando un cuento."""
    def porcion(d: dict) -> float:
        base = d["promedios"]["kern"]["capture"]
        return (d["promedios"]["arranque_piso"]["capture"] - base) / (0.0 - base)

    assert porcion(despues) < porcion(datos), (
        f"el contrafactico explica {porcion(despues):.0%} despues contra "
        f"{porcion(datos):.0%} antes: no se encogio"
    )


def test_el_documento_cita_las_dos_frases(datos, despues, texto):
    from scripts.por_que_bajo_el_piso import frase

    faltan = [frase(d) for d in (datos, despues) if frase(d) not in texto]
    assert not faltan, (
        "el documento no cita estas frases de sus lineas base:\n  " + "\n  ".join(faltan)
    )


def test_el_documento_declara_que_el_arreglo_NO_cierra_la_brecha(datos, despues, texto):
    """Kern sigue por debajo del piso. Un documento que publique "+19,4 puntos" sin eso
    se lee como que el problema esta resuelto."""
    assert despues["promedios"]["kern"]["capture"] < 0.0, (
        "Kern ya no esta por debajo del piso; esta salvedad y este test sobran"
    )
    assert "no cierra la brecha" in texto


def test_la_cobertura_compro_donde_antes_no_compraba_nada(datos, despues):
    """El mecanismo publicado: el primer tercio del horizonte pasa de CERO a algo. Si
    esto deja de valer, el documento explica la mejora con una causa que ya no ocurre."""
    assert datos["unidades_por_tercio"]["kern"][0] == pytest.approx(0.0, abs=1e-9)
    assert despues["unidades_por_tercio"]["kern"][0] > 0.0
