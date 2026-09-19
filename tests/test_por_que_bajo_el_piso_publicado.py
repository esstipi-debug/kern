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
