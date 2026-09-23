"""`KERN_VENTAS_CENSURADAS.md` no puede despegarse de lo que se midio.

Es el primer documento de la rama que mide una palanca y la publica como **no
desplegable**, y una refutacion se cita mal de una forma particular: alcanza con que un
carril deje de medir lo que dice medir para que "no paga" se vuelva "lo medimos mal". Asi
que estos tests no fijan solo el numero -- fijan las tres propiedades que hacen que el
numero signifique algo:

1. **el diagnostico ve la verdad EXACTA**, no una aproximacion de ella. Si
   `sin_censura` fuera un estimador, su resultado no seria una cota superior de nada;
2. **los desplegables NO miran la respuesta.** `out.lost` es justo lo que un cliente no
   puede ver. Se prueba MUTANDO la columna de la verdad y exigiendo que su salida no se
   mueva -- un grep se puede esquivar, esto no;
3. **el control es el mismo experimento.** El archivo que lee el carril de control tiene
   que salir byte a byte igual al de `lens.export_history`, y sus numeros iguales a los
   de `horizonte_por_sku.json`, o la comparacion contra las otras palancas no vale.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from examples.run_world_sim import EMPTY_POLICY, WARMUP_WEEKS, simulate
from scripts.ventas_censuradas import (
    _censura_corregida,
    _censura_omitida,
    _marcas,
    _sin_censura,
    _vista,
)
from src.simworld.lens import export_history, history_frame
from src.simworld.runners import naive_policy
from src.simworld.world import generate_world

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "documentation" / "lineas_base" / "ventas_censuradas.json"
DOC = RAIZ / "documentation" / "KERN_VENTAS_CENSURADAS.md"
HORIZONTE = RAIZ / "documentation" / "lineas_base" / "horizonte_por_sku.json"


def _plano(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.replace("−", "-").replace("**", "").replace("*", ""))


@pytest.fixture(scope="module")
def datos() -> dict:
    return json.loads(BASE.read_text())


@pytest.fixture(scope="module")
def doc() -> str:
    return _plano(DOC.read_text())


@pytest.fixture(scope="module")
def corrida():
    """Un mundo chico corrido por el PISO: barato (no toca el orquestador) y con
    quiebres de sobra para que haya filas censuradas que mirar."""
    world = generate_world(seed=1000, n_skus=8, n_suppliers=2, weeks=24)
    congelado = None

    def piso(w, outs, _week):
        nonlocal congelado
        if congelado is not None:
            return congelado, False
        if len(outs) < WARMUP_WEEKS:
            return EMPTY_POLICY, False
        congelado = naive_policy(w, outs)
        return congelado, False

    outs, _ = simulate(world, piso, replan_every=4)
    return world, outs


def test_el_carril_de_diagnostico_devuelve_la_demanda_EXACTA(corrida):
    """`sin_censura` no estima: pone la demanda del mundo. Es lo que lo vuelve una cota
    superior y no un carril mas."""
    world, outs = corrida
    df = history_frame(world, outs)
    verdad = _sin_censura(df, _marcas(world, outs))

    esperado = float(np.stack([o.demand for o in outs]).sum())
    assert verdad["quantity"].sum() == pytest.approx(esperado)
    assert df["quantity"].sum() < esperado, "el mundo no censuro nada; el fixture no sirve"
    assert list(verdad.columns) == list(df.columns)
    assert len(verdad) == len(df)


def test_los_carriles_desplegables_NO_miran_la_venta_perdida(corrida):
    """La prueba de que son desplegables, y se hace mutando en vez de grepeando.

    Si un carril desplegable leyera `verdad` -- la demanda del mundo, que el cliente no
    tiene -- entonces reemplazarla por basura cambiaria su salida. No se mueve ni una
    celda. El de diagnostico, que SI la lee, se mueve entero: sin ese contraste el test
    pasaria igual con una mutacion que no muta nada.
    """
    world, outs = corrida
    df = history_frame(world, outs)
    marcas = _marcas(world, outs)
    mutadas = marcas.assign(verdad=-12345.0)

    for carril in (_censura_omitida, _censura_corregida):
        antes, despues = carril(df, marcas), carril(df, mutadas)
        pd.testing.assert_frame_equal(
            antes, despues, obj=f"{carril.__name__} leyo la columna de la verdad"
        )

    antes, despues = _sin_censura(df, marcas), _sin_censura(df, mutadas)
    assert not antes["quantity"].equals(despues["quantity"]), (
        "el carril de diagnostico tampoco se movio: la mutacion no muta nada y el "
        "contraste de este test es vacio"
    )


def test_una_fila_NO_censurada_trae_la_demanda_exacta_y_por_eso_omitir_es_seleccion(
    corrida,
):
    """El mecanismo que separa a `censura_omitida` de un promedio ruidoso.

    `advance` hace `sold = min(disponible, demanda)`, asi que una semana que NO cerro en
    cero vendio EXACTAMENTE lo que se le pidio. Las filas que sobreviven al borrado no
    son aproximaciones: son mediciones exactas. El problema de ese carril es de
    SELECCION (que filas quedan), no de medicion -- y por eso el documento lo diagnostica
    como sesgo y no como ruido.
    """
    world, outs = corrida
    estante = np.stack([o.on_hand_end for o in outs], axis=1)
    vendido = np.stack([o.sold for o in outs], axis=1)
    demanda = np.stack([o.demand for o in outs], axis=1)
    limpio = estante > 0.0

    assert limpio.any() and not limpio.all(), "el fixture no tiene las dos clases de fila"
    assert (vendido[limpio] == demanda[limpio]).all(), (
        "una fila sin quiebre dejo de traer la demanda exacta"
    )

    # Y la seleccion sesga: la media de las supervivientes queda por debajo de la
    # verdadera, aunque cada una de ellas sea exacta.
    por_sku = [demanda[i][limpio[i]].mean() / demanda[i].mean() for i in range(len(world.skus))]
    assert float(np.mean(por_sku)) < 1.0


def test_corregir_nunca_BAJA_un_valor_y_deja_intacto_al_SKU_sin_filas_limpias():
    """`max(vendido, media de las limpias)`, y la ausencia declarada.

    Lo vendido en una semana que se agoto es una COTA INFERIOR de la demanda: bajarla a
    la media tiraria informacion que si se tiene. Y un SKU cuyas filas son TODAS
    censuradas no tiene media que citar -- rellenarlo con el promedio de otro SKU
    mezclaria escalas, asi que se deja como esta.
    """
    df = pd.DataFrame(
        {
            "date": ["2025-01-06", "2025-01-13", "2025-01-20"] * 2,
            "product_id": ["A"] * 3 + ["B"] * 3,
            "quantity": [10.0, 100.0, 2.0, 7.0, 8.0, 9.0],
        }
    )
    marcas = pd.DataFrame(
        {
            "date": ["2025-01-06", "2025-01-13", "2025-01-20"] * 2,
            "product_id": ["A"] * 3 + ["B"] * 3,
            # A: la 2da limpia (media 100); la 1ra y la 3ra agotadas.
            # B: las TRES agotadas -> no hay con que estimar.
            "agotado": [True, False, True, True, True, True],
            "verdad": [0.0] * 6,
        }
    )
    out = _censura_corregida(df, marcas).set_index(["product_id", "date"])["quantity"]

    assert out[("A", "2025-01-06")] == 100.0   # 10 -> la media de las limpias
    assert out[("A", "2025-01-13")] == 100.0   # limpia, intacta
    assert out[("A", "2025-01-20")] == 100.0   # 2 -> la media
    assert list(out.loc["B"]) == [7.0, 8.0, 9.0], "un SKU sin filas limpias se toco"

    # Y nunca baja: con una media chica gana lo vendido.
    marcas_b = marcas.assign(agotado=[False, True, False, False, False, False])
    out_b = _censura_corregida(df, marcas_b).set_index(["product_id", "date"])["quantity"]
    assert out_b[("A", "2025-01-13")] == 100.0, "la fila censurada bajo a la media"


def test_el_carril_de_control_escribe_EXACTAMENTE_el_archivo_de_siempre(corrida, tmp_path):
    """Sin esto, el control no es un control: cualquier diferencia de formato -- una
    coma, un orden de columnas -- se leeria como efecto de la palanca."""
    world, outs = corrida
    mio = _vista(world, outs, None, tmp_path / "mio.csv")
    suyo = export_history(world, outs, tmp_path / "suyo.csv", level=0, seed=0)
    assert mio.read_bytes() == suyo.read_bytes()


def test_el_control_reproduce_la_linea_base_de_horizonte_por_sku(datos):
    """La prueba de que esto es el MISMO experimento que las otras palancas de la rama.

    Las cinco semillas canonicas corren aca el mismo camino que en
    `scripts/horizonte_por_sku.py`, asi que su carril de control tiene que salir igual al
    ultimo digito. Si se separan, los deltas de esta palanca dejan de ser comparables
    contra el +16,2 de aquella.
    """
    otro = json.loads(HORIZONTE.read_text())
    suyas = {f["seed"]: f for f in otro["por_semilla"]}
    mias = {f["seed"]: f for f in datos["por_semilla"]}
    assert set(suyas) <= set(mias), "faltan las semillas canonicas en esta linea base"

    for seed, fila in suyas.items():
        for campo in ("capture", "fill", "total", "unidades_pedidas", "shortage"):
            assert mias[seed]["kern"][campo] == pytest.approx(
                fila["kern"][campo], rel=1e-12
            ), f"semilla {seed}: el control se separo en {campo}"
        for vara in ("piso", "techo"):
            assert mias[seed][vara]["total"] == pytest.approx(
                fila[vara]["total"], rel=1e-12
            ), f"semilla {seed}: la vara {vara} se movio"


def test_el_techo_de_la_palanca_NO_se_distingue_de_cero(datos, doc):
    """El titular, y la unica forma de volverlo falso: que la palanca empiece a pagar.

    `sin_censura` es la cota superior -- le da a Kern la demanda verdadera, que nadie
    tiene. Si ese carril despegara, el documento estaria equivocado en su conclusion
    central y habria que reabrir la construccion. Se fija el tamano Y la dispersion: una
    palanca que mejora en poco mas de la mitad de los mundos no es una palanca.
    """
    r = datos["resumen"]
    techo = r["sin_censura"]
    n = techo["mundos"]
    assert n == 25, f"el titular se publica con 25 mundos, no con {n}"
    assert techo["mejora_vs_kern"] < 0.08, (
        f"el techo de la palanca subio a {techo['mejora_vs_kern']:+.4f}; el documento "
        "publica +0,0394 y concluye que no paga"
    )
    assert techo["mundos_que_mejoran"] <= 16, (
        f"mejora en {techo['mundos_que_mejoran']}/{n}: dejo de ser una moneda"
    )
    # Y contra la palanca que SI pago, en la misma rama: un orden de magnitud.
    horizonte = json.loads(HORIZONTE.read_text())
    assert techo["mejora_vs_kern"] < 0.5 * horizonte["resumen"]["horizonte_estimado"][
        "mejora_vs_kern"
    ], "el techo de esta palanca alcanzo a la fecha de fin por SKU"
    assert "no se distingue de cero" in doc


def test_el_mecanismo_es_un_empate_entre_compra_y_quiebre(datos, doc):
    """H2. Descensurar FUNCIONA -- Kern compra mas y sirve mas -- y aun asi no paga,
    porque compra casi un dolar de mercaderia por cada dolar de quiebre que evita. Si ese
    intercambio se desbalanceara, la explicacion del documento dejaria de ser la correcta
    aunque el numero de arriba no se moviera."""
    r = datos["resumen"]
    k, s = r["kern"], r["sin_censura"]

    # Funciona: ve mas demanda, compra mas, sirve mas.
    assert s["unidades_pedidas"] > k["unidades_pedidas"]
    assert s["fill"] > k["fill"]
    assert s["fraccion_demanda_oculta"] < k["fraccion_demanda_oculta"]

    # Y es un empate: lo que gasta de mas en comprar es lo que deja de perder en quiebre.
    compra_de_mas = s["procurement"] - k["procurement"]
    quiebre_de_menos = k["shortage"] - s["shortage"]
    assert abs(compra_de_mas - quiebre_de_menos) < 0.20 * quiebre_de_menos, (
        f"el intercambio dejo de ser un empate: compra +{compra_de_mas:,.0f} contra "
        f"quiebre -{quiebre_de_menos:,.0f}"
    )
    # Y el neto es una astilla de la brecha que la separa del techo.
    ahorro = k["total"] - s["total"]
    brecha = k["total"] - r["techo"]["total"]
    assert 0 < ahorro < 0.06 * brecha, (
        f"el ahorro neto ({ahorro:,.0f}) dejo de ser marginal contra la brecha "
        f"({brecha:,.0f})"
    )
    # Y deja MAS capital inmovilizado, que es la firma de la familia "compra mas".
    assert s["pagado_no_vendido_usd"] > k["pagado_no_vendido_usd"]
    assert "un empate" in doc


def test_cinco_mundos_habrian_dicho_que_si(datos, doc):
    """H3, y la razon por la que este documento se midio con 25.

    Si el subconjunto canonico dejara de exagerar la palanca, el documento estaria
    publicando una advertencia metodologica que ya no se sostiene -- y peor, seguiria
    diciendo que cinco no alcanzaban sobre una evidencia que desaparecio.
    """
    canonico = datos["resumen_canonico"]
    assert canonico is not None, "la corrida no incluyo las cinco semillas canonicas"
    completo = datos["resumen"]
    for carril in ("sin_censura", "censura_corregida"):
        assert canonico[carril]["mejora_vs_kern"] > 2.5 * completo[carril][
            "mejora_vs_kern"
        ], (
            f"{carril}: el subconjunto de cinco ya no exagera la palanca "
            f"({canonico[carril]['mejora_vs_kern']:+.4f} contra "
            f"{completo[carril]['mejora_vs_kern']:+.4f} en 25)"
        )

    # Y la causa que el documento da: tres de las cinco estan entre los ocho peores.
    peores = {
        f["seed"]
        for f in sorted(datos["por_semilla"], key=lambda f: f["kern"]["capture"])[:8]
    }
    assert len({1000, 1003, 1004} & peores) == 3, (
        f"las canonicas dejaron de estar entre los peores mundos: {sorted(peores)}"
    )
    assert "cinco no alcanzaban" in doc


def test_borrar_la_fila_censurada_es_PEOR_que_no_hacer_nada(datos, doc):
    """H4. La respuesta que esta rama aplica en todas partes -- declarar la ausencia --
    aca pierde, y el documento saca de ahi una regla nueva. Si el carril que borra
    empezara a ganar, esa regla seria falsa."""
    r = datos["resumen"]
    k, o = r["kern"], r["censura_omitida"]
    assert o["mejora_vs_kern"] < 0.0, (
        f"borrar la fila censurada pasa a pagar ({o['mejora_vs_kern']:+.4f}); el "
        "documento concluye lo contrario"
    )
    assert o["fill"] < k["fill"], "el carril que borra dejo de servir peor"
    # Y el bucle corre para atras: se censura a si mismo MAS que el control.
    assert o["fraccion_filas_agotadas"] > k["fraccion_filas_agotadas"], (
        "el carril que borra dejo de quebrar mas que el control; el documento publica "
        "42,7% contra 34,8% como la prueba de que el bucle corre para atras"
    )
    assert "cambia la distribución de la que estima" in DOC.read_text()


def test_el_documento_declara_los_limites_del_titular(doc):
    """Una refutacion se cita de mas igual que un hallazgo. Estos son los cuatro limites
    sin los cuales 'no paga' se leeria como 'la censura no importa en ningun lado'."""
    assert "No dice que la censura no exista" in doc
    assert "está en otro régimen" in doc          # el limite de regimen
    assert "No mide una implementación" in doc
    assert "cinco no alcanzaban" in doc
    assert "No mide bajo disrupción ni sobre datos sucios" in doc


def test_el_experimento_tenia_potencia_para_ver_una_palanca_que_valiera(datos, doc):
    """La diferencia entre "no lo detectamos" y "no esta ahi", que es lo que vuelve
    accionable un resultado nulo.

    Se recalcula la potencia desde la linea base en vez de confiar en la tabla del
    documento: si la dispersion entre mundos creciera, el mismo `p` pasaria a significar
    lo contrario -- que la medicion se quedo corta -- y el documento estaria sacando una
    decision de producto de un experimento que ya no la sostiene.
    """
    import math
    import statistics

    from scipy import stats

    delta = [
        f["sin_censura"]["capture"] - f["kern"]["capture"] for f in datos["por_semilla"]
    ]
    n = len(delta)
    se = statistics.stdev(delta) / math.sqrt(n)
    gl = n - 1
    critico = stats.t.ppf(0.975, gl)

    mde = (critico + stats.t.ppf(0.80, gl)) * se
    assert mde < 0.08, f"el efecto minimo detectable subio a {mde:+.4f}; el documento publica +0,072"

    # Y la potencia contra la palanca que SI pago en esta rama, medida sobre los mismos
    # mundos: si esto cayera, "no paga" pasaria a ser "no se vio".
    horizonte = json.loads(HORIZONTE.read_text())
    efecto = horizonte["resumen"]["horizonte_estimado"]["mejora_vs_kern"]
    ncp = efecto / se
    potencia = (
        1 - stats.nct.cdf(critico, gl, ncp) + stats.nct.cdf(-critico, gl, ncp)
    )
    assert potencia > 0.95, (
        f"la potencia para detectar un efecto de {efecto:+.4f} cayo a {potencia:.1%}; "
        "el resultado nulo dejaria de ser una conclusion"
    )
    assert "potencia de sobra" in doc
