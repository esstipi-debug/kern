"""El espejo: el diagnostico gratuito sobre el archivo de alguien que no compro.

Todo lo demas del repo mejora el producto para quien YA es cliente. Esto es lo
unico que se le muestra a un desconocido, y por eso las reglas son otras: cada
numero tiene que sobrevivir a que lo mire alguien que hace veinte anios que
maneja ese deposito y piensa que el software es ingenuo.

**La trampa ya pagada.** Una version anterior de este diagnostico se valido el
2026-07-18 contra cuatro vendedores de Olist (Brasil, R$) y Superstore (EE.UU.,
1.862 SKUs), y el patron aguanto cruzando paises y monedas: concentracion 20-44%
de los SKU sobre el 80% de la venta, catalogo muerto 23-60%, venta recuperable
4-29%. Pero el baseline ingenuo ``z=0`` -- suponer que el prospecto tiene
inventario igual a su demanda media, o sea 50% de servicio -- daba **43% de la
venta**. Ese numero no es creible y quema la conversacion en el primer contacto.

La aritmetica de por que, medida con la propia ``src.fill_rate``:

    servicio supuesto   z        L_N(z)    exceso vs. 95%   veces
    50% (el ingenuo)    0.0000   0.3989    0.3780           14,3x
    80%                 0.8416   0.1116    0.0907            3,4x
    90%                 1.2816   0.0473    0.0265            1,0x

Catorce veces. De ahi sale el 43%. El arreglo no es afinar el numero: es
**suponer un servicio actual de 80-90% y devolver un rango**, nunca un punto.

**Y la regla que gobierna el modulo entero:** decir ``no medido`` cuando no se
puede medir. Ese silencio no es una falla del informe, es el argumento de venta
-- "tu sistema no sabe si cumpliste". Se hereda de ``src/order_fulfillment.py``,
que ya distingue lo observado de lo que un modelo implica.
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.prospect_diagnostic import (
    REFUSED_FEW_PERIODS,
    REFUSED_INTERMITTENT,
    REFUSED_NO_DEMAND,
    Diagnostic,
    concentration,
    dead_catalog,
    diagnose,
    recoverable_sales,
    render_report,
)

# --------------------------------------------------------------------------
# Constructores de archivos de prospecto
# --------------------------------------------------------------------------

def _filas(
    sku: str,
    *,
    semanas: int,
    cantidades: list[float] | None = None,
    precio: float = 10.0,
    desde: date = date(2026, 1, 5),
) -> list[dict]:
    """Una serie semanal para un SKU, en el esquema canonico del intake."""
    qs = cantidades if cantidades is not None else [10.0] * semanas
    return [
        {
            "date": desde + timedelta(weeks=i),
            "product_id": sku,
            "quantity": q,
            "unit_cost": precio,
        }
        for i, q in enumerate(qs)
    ]


def _csv(tmp_path: Path, filas: list[dict], *, nombre: str = "prospecto.csv") -> Path:
    ruta = tmp_path / nombre
    pd.DataFrame(filas).to_csv(ruta, index=False)
    return ruta


# --------------------------------------------------------------------------
# 1. Concentracion ABC / Pareto
# --------------------------------------------------------------------------

def test_la_concentracion_cuenta_cuantos_sku_hacen_el_ochenta_por_ciento():
    """El hallazgo de portada: pocos SKU explican casi toda la venta."""
    filas = (
        _filas("GRANDE", semanas=12, cantidades=[100.0] * 12, precio=10.0)
        + _filas("CHICO-1", semanas=12, cantidades=[1.0] * 12, precio=10.0)
        + _filas("CHICO-2", semanas=12, cantidades=[1.0] * 12, precio=10.0)
        + _filas("CHICO-3", semanas=12, cantidades=[1.0] * 12, precio=10.0)
    )
    conc = concentration(filas)

    assert conc.skus_total == 4
    # GRANDE solo ya pasa el 80% (12.000 de 12.360).
    assert conc.skus_for_80pct == 1
    assert conc.share_of_skus_for_80pct == pytest.approx(0.25)


def test_la_concentracion_declara_si_ranquea_por_valor_o_solo_por_unidades():
    """Si todos los precios son iguales, el ABC es un ranking de UNIDADES.

    Es aritmetica correcta para un catalogo de precio uniforme y una mentira
    silenciosa cuando la columna de precio era un relleno. ``value_basis`` de
    ``src/classification.py`` ya hace esta distincion; el espejo tiene que
    propagarla al informe en vez de tragarsela.
    """
    iguales = (
        _filas("A", semanas=12, cantidades=[50.0] * 12, precio=1.0)
        + _filas("B", semanas=12, cantidades=[10.0] * 12, precio=1.0)
    )
    assert concentration(iguales).value_basis == "units"

    distintos = (
        _filas("A", semanas=12, cantidades=[50.0] * 12, precio=1.0)
        + _filas("B", semanas=12, cantidades=[10.0] * 12, precio=99.0)
    )
    assert concentration(distintos).value_basis == "value"


# --------------------------------------------------------------------------
# 2. Catalogo muerto
# --------------------------------------------------------------------------

def test_el_catalogo_muerto_se_ancla_a_la_ultima_fecha_DEL_ARCHIVO_no_a_hoy():
    """El export del prospecto puede tener tres anios. Anclar a ``today`` marca
    el catalogo entero como muerto y el informe se vuelve una acusacion absurda.
    """
    viejo = date(2023, 1, 4)
    filas = (
        _filas("VIVO", semanas=52, desde=viejo)
        + _filas("MUERTO", semanas=4, desde=viejo)
    )
    muerto = dead_catalog(filas, window_days=180)

    assert muerto.anchor_date == max(f["date"] for f in filas)
    assert muerto.dead_skus == 1
    assert muerto.skus_total == 2


def test_un_sku_que_vendio_dentro_de_la_ventana_no_esta_muerto():
    hoy = date(2026, 6, 1)
    filas = (
        _filas("VIVO", semanas=8, desde=hoy - timedelta(weeks=8))
        + _filas("TAMBIEN-VIVO", semanas=2, desde=hoy - timedelta(weeks=2))
    )
    assert dead_catalog(filas, window_days=180).dead_skus == 0


# --------------------------------------------------------------------------
# 3. Venta recuperable -- la trampa del 43%
# --------------------------------------------------------------------------

def test_la_venta_recuperable_sale_en_RANGO_nunca_en_un_punto():
    filas = _filas("A", semanas=52, cantidades=[10.0, 20.0] * 26, precio=10.0)
    rec = recoverable_sales(filas)

    assert rec.low is not None and rec.high is not None
    assert rec.low < rec.high, "un rango degenerado es un punto disfrazado"
    assert rec.assumed_service_high == 0.80  # el supuesto pesimista da el techo
    assert rec.assumed_service_low == 0.90   # el optimista da el piso


def test_el_rango_nunca_alcanza_el_artefacto_del_baseline_ingenuo():
    """El guard central del modulo, medido y no declarado.

    Con servicio actual supuesto en 80-90%, el techo del rango tiene que quedar
    MUY por debajo de lo que daria suponer 50% (z=0). Si alguien cambia los
    supuestos y el techo se acerca a la cifra ingenua, este test cae.
    """
    filas = _filas("A", semanas=52, cantidades=[10.0, 20.0] * 26, precio=10.0)
    honesto = recoverable_sales(filas)
    ingenuo = recoverable_sales(filas, assumed_service=(0.50, 0.50))

    assert ingenuo.high > honesto.high * 3, "el ingenuo tiene que ser mucho peor"
    # 14,3x contra el piso; el techo honesto (80%) queda en ~4,2x del ingenuo.
    assert honesto.high < ingenuo.high / 3


def test_la_recuperable_nunca_supera_la_venta_total():
    """Ningun supuesto puede hacer que se recupere mas de lo que se factura.

    La serie es de timing REGULAR (vende todas las semanas) pero de tamanio
    salvaje, para que el guard de intermitencia la deje pasar y el que se
    ejercite sea el tope por SKU. Con una serie de ceros y picos el rechazo
    llegaria antes y este test no probaria nada.
    """
    filas = _filas("SALVAJE", semanas=52, cantidades=[1.0, 400.0] * 26, precio=10.0)
    rec = recoverable_sales(filas)
    total = sum(f["quantity"] * f["unit_cost"] for f in filas)

    assert rec.skus_measured == 1, "la serie tenia que sobrevivir al guard de timing"
    assert rec.high is not None
    assert rec.high <= total


def test_un_sku_con_pocos_periodos_se_RECHAZA_y_se_cuenta():
    """Un sigma sobre tres puntos es ruido, y produce una cifra enorme y falsa.

    No se estima igual: se rechaza, y el rechazo aparece en el informe -- que es
    la diferencia entre "no lo medimos" y "te falta poco".
    """
    filas = _filas("NUEVO", semanas=3, precio=10.0)
    rec = recoverable_sales(filas, min_periods=8)

    assert rec.skus_measured == 0
    assert rec.skus_refused == 1
    assert rec.refused_reasons[REFUSED_FEW_PERIODS] == 1
    assert rec.low is None and rec.high is None, "sin SKU medido no hay cifra"


def test_un_sku_sin_demanda_se_rechaza_en_vez_de_dividir_por_cero():
    filas = _filas("CERO", semanas=12, cantidades=[0.0] * 12, precio=10.0)
    rec = recoverable_sales(filas, min_periods=8)

    assert rec.refused_reasons[REFUSED_NO_DEMAND] == 1
    assert rec.skus_measured == 0


def test_una_demanda_perfectamente_estable_no_recupera_nada():
    """Sigma cero no es un error: es un SKU que nunca se quiebra por variabilidad."""
    filas = _filas("ESTABLE", semanas=52, cantidades=[10.0] * 52, precio=10.0)
    rec = recoverable_sales(filas, min_periods=8)

    assert rec.skus_measured == 1
    assert rec.high == pytest.approx(0.0)


def test_el_rango_escala_con_la_variabilidad_y_no_con_el_volumen():
    """Dos SKU con la misma media y distinto CV no pueden recuperar lo mismo."""
    estable = _filas("E", semanas=52, cantidades=[10.0] * 52, precio=10.0)
    erratico = _filas("R", semanas=52, cantidades=[2.0, 18.0] * 26, precio=10.0)

    assert recoverable_sales(erratico).high > recoverable_sales(estable).high


# --------------------------------------------------------------------------
# 4. OTIF / fill rate real -- y el `no medido` que vende
# --------------------------------------------------------------------------

def test_sin_columnas_de_servicio_el_OTIF_sale_NO_MEDIDO_y_nunca_cero():
    """El silencio ES el argumento: "tu sistema no sabe si cumpliste".

    Un cero seria una acusacion falsa; un ``None`` es un hallazgo verdadero.
    """
    filas = _filas("A", semanas=12)
    diag = diagnose(filas)

    assert diag.service.otif is None
    assert diag.service.fill_rate is None
    assert diag.service.provenance == "not_measured"


def test_con_columnas_de_servicio_el_OTIF_se_MIDE_de_verdad():
    """Lo que ningun competidor puede dar: no proyecta, mide lo ya incumplido."""
    filas = [
        {
            "date": date(2026, 1, 5),
            "product_id": "A",
            "quantity": 10.0,
            "unit_cost": 10.0,
            "ordered_qty": 10.0,
            "delivered_qty": 8.0,
            "promised_date": date(2026, 1, 6),
            "delivered_date": date(2026, 1, 9),
        },
        {
            "date": date(2026, 1, 12),
            "product_id": "A",
            "quantity": 10.0,
            "unit_cost": 10.0,
            "ordered_qty": 10.0,
            "delivered_qty": 10.0,
            "promised_date": date(2026, 1, 13),
            "delivered_date": date(2026, 1, 13),
        },
    ]
    diag = diagnose(filas)

    assert diag.service.provenance == "observed"
    assert diag.service.fill_rate == pytest.approx(18.0 / 20.0)
    assert diag.service.otif == pytest.approx(0.5)


# --------------------------------------------------------------------------
# 5. El archivo real del prospecto
# --------------------------------------------------------------------------

def test_diagnose_lee_un_csv_del_prospecto_de_punta_a_punta(tmp_path: Path):
    filas = (
        _filas("GRANDE", semanas=52, cantidades=[100.0, 60.0] * 26, precio=25.0)
        + _filas("CHICO", semanas=52, cantidades=[2.0, 1.0] * 26, precio=5.0)
    )
    diag = diagnose(_csv(tmp_path, filas))

    assert isinstance(diag, Diagnostic)
    assert diag.concentration.skus_total == 2
    assert diag.recoverable.high is not None


def test_diagnose_declara_lo_que_le_saco_a_la_planilla(tmp_path: Path):
    """La higiene de Excel viaja al informe: el prospecto se entera de que su
    propia planilla traia una fila de totales, en vez de que se la borren callados.
    """
    openpyxl = pytest.importorskip("openpyxl")
    ruta = tmp_path / "prospecto.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["fecha", "sku", "cantidad", "precio"])
    for i in range(12):
        ws.append([date(2026, 1, 5) + timedelta(weeks=i), "A", 10, 10])
    ws.append(["TOTAL", None, 120, None])
    wb.save(ruta)

    diag = diagnose(ruta)

    assert diag.hygiene is not None
    assert diag.hygiene.rows_dropped >= 1


def test_un_archivo_vacio_no_revienta_sino_que_no_mide_nada(tmp_path: Path):
    ruta = tmp_path / "vacio.csv"
    pd.DataFrame(columns=["date", "product_id", "quantity", "unit_cost"]).to_csv(ruta, index=False)

    diag = diagnose(ruta)

    assert diag.concentration.skus_total == 0
    assert diag.recoverable.low is None
    assert diag.service.provenance == "not_measured"


# --------------------------------------------------------------------------
# 6. La garantia de que corre LOCAL -- fijada por un test, no declarada
# --------------------------------------------------------------------------

def test_el_diagnostico_completo_corre_sin_tocar_la_red(tmp_path: Path, monkeypatch):
    """La objecion "no subo mis datos a tu nube" es real, y esquivarla es parte
    del producto. Con el socket clausurado, el diagnostico entero tiene que
    correr igual. Si manana alguien agrega una llamada de red, esto cae aca y no
    en la maquina de un prospecto.
    """
    import socket

    def prohibido(*_a, **_k):
        raise AssertionError("el espejo abrio un socket: la promesa local se rompio")

    monkeypatch.setattr(socket, "socket", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)

    filas = _filas("A", semanas=52, cantidades=[10.0, 20.0] * 26, precio=10.0)
    diag = diagnose(_csv(tmp_path, filas))

    assert diag.recoverable.high is not None


# --------------------------------------------------------------------------
# 7. El informe que el prospecto realmente lee
# --------------------------------------------------------------------------

def test_el_informe_muestra_la_recuperable_como_RANGO_y_nunca_como_un_punto():
    """Si el informe imprimiera un solo numero, el rango del motor no serviria
    de nada: la mentira con precision entraria igual por el renderizador.
    """
    filas = _filas("A", semanas=52, cantidades=[10.0, 20.0] * 26, precio=10.0)
    texto = render_report(diagnose(filas))

    rec = recoverable_sales(filas)
    assert f"{rec.low:,.0f}" in texto
    assert f"{rec.high:,.0f}" in texto


def test_el_informe_dice_NO_MEDIDO_para_el_servicio_cuando_no_hay_datos():
    """El silencio impreso: es el argumento de venta, tiene que verse."""
    texto = render_report(diagnose(_filas("A", semanas=12)))

    assert "no medido" in texto.lower()
    assert "0%" not in texto.split("Servicio")[-1][:200], "un cero seria una acusacion falsa"


def test_el_informe_declara_cuando_el_ABC_ranquea_solo_por_unidades():
    """Un ABC sobre precios uniformes es un ranking de unidades, y el prospecto
    tiene que enterarse por el informe y no por una sorpresa despues.
    """
    iguales = (
        _filas("A", semanas=12, cantidades=[50.0] * 12, precio=1.0)
        + _filas("B", semanas=12, cantidades=[10.0] * 12, precio=1.0)
    )
    assert "unidades" in render_report(diagnose(iguales)).lower()


def test_el_informe_declara_los_sku_que_no_pudo_medir():
    """Numerador y denominador siempre: "medimos 3 de 40" no es lo mismo que
    "no hay nada que recuperar", y el informe no puede confundirlos.
    """
    filas = _filas("MEDIBLE", semanas=52, cantidades=[10.0, 20.0] * 26)
    for i in range(5):
        filas += _filas(f"NUEVO-{i}", semanas=2)
    texto = render_report(diagnose(filas))

    assert "5" in texto
    assert "no se pudo" in texto.lower() or "sin evidencia" in texto.lower()


def test_el_informe_es_ascii_puro():
    """Los prints de consola en Windows revientan con cp1252 (ver CLAUDE.md).

    El informe se imprime por consola en la maquina del prospecto, que es
    justamente donde no se puede permitir un UnicodeEncodeError.
    """
    filas = _filas("A", semanas=52, cantidades=[10.0, 20.0] * 26)
    texto = render_report(diagnose(filas))

    texto.encode("ascii")  # revienta si se colo un guion largo o un acento


def test_un_importe_de_linea_se_convierte_en_precio_unitario(tmp_path: Path):
    """Superstore trae ``Sales`` (importe de la LINEA), no precio unitario.

    Sin recuperar el precio (importe/cantidad) todos los SKU se rechazarian por
    "sin precio", y el ranking de valor colapsaria sobre unidades -- que es la
    confusion clasica entre precio unitario e importe de linea. ``jobs/intake.py``
    ya hace esta recuperacion para su propio camino; el espejo tiene que hacerla
    tambien, con el mismo guard de que ``sales`` es alias de las dos cosas.
    """
    filas = []
    for i in range(12):
        cuando = date(2026, 1, 5) + timedelta(weeks=i)
        # CARO: 1 unidad a 100. BARATO: 10 unidades a 1.
        filas.append({"date": cuando, "sku": "CARO", "quantity": 1, "sales": 100.0})
        filas.append({"date": cuando, "sku": "BARATO", "quantity": 10, "sales": 10.0})
    ruta = tmp_path / "superstore_like.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)

    diag = diagnose(ruta)

    assert diag.concentration.value_basis == "value", "el precio se recupero"
    assert diag.concentration.top_skus[0][0] == "CARO"
    assert diag.recoverable.refused_reasons.get("sin_precio", 0) == 0


def test_un_csv_en_latin1_se_lee_en_vez_de_reventar(tmp_path: Path):
    """El archivo real del prospecto no viene en UTF-8.

    El Superstore de verdad (data/kaggle/superstore.csv) esta en latin-1, y un
    export de Windows en castellano lo esta casi siempre: basta una enie, un
    acento o un espacio duro (0xa0) para que ``pd.read_csv`` pelado tire un
    ``UnicodeDecodeError`` crudo. Reventar con un stack trace en la maquina del
    prospecto es la peor primera impresion posible, y ademas es evitable: si
    UTF-8 no entra, se reintenta con cp1252 y despues con latin-1, que no puede
    fallar. La mugre del archivo del cliente es el problema que el espejo existe
    para absorber.
    """
    ruta = tmp_path / "export_windows.csv"
    lineas = ["fecha,sku,cantidad,precio"]
    for i in range(12):
        cuando = date(2026, 1, 5) + timedelta(weeks=i)
        lineas.append(f"{cuando},ANO-PEQUENO,10,25.5")
    # 0xa0 (espacio duro de cp1252) es exactamente el byte que rompe el Superstore.
    texto = "\n".join(lineas).replace("PEQUENO", "PEQUE\xa0O")
    ruta.write_bytes(texto.encode("latin-1"))

    diag = diagnose(ruta)

    assert diag.concentration.skus_total == 1
    assert diag.rows_used == 12


# --------------------------------------------------------------------------
# 8. Donde la funcion de perdida normal deja de ser legitima
# --------------------------------------------------------------------------

def test_un_sku_de_demanda_grumosa_se_rechaza_en_vez_de_modelarse_mal():
    """La segunda via al artefacto del 43%, y la que casi se cuela.

    ``normal_loss`` supone que la demanda durante el plazo es aproximadamente
    normal. Un SKU que vende cinco veces en tres anios no cumple eso ni de
    lejos: su sigma sobre una serie casi toda de ceros es enorme comparado con
    su media, y la funcion de perdida devuelve una recuperable que puede superar
    el 30% de lo que ese SKU factura. Con mil SKU de cola larga -- que es como se
    ve un catalogo real -- eso solo reconstruye el 43% por otro camino.

    Syntetos-Boylan ya separa esos casos, y ``src/forecastability.py`` ya lo
    implementa. Aca se usa para NO responder, que es la unica respuesta honesta:
    ese SKU necesita Croston/TSB, no un stock de seguridad normal.
    """
    grumoso = []
    for semana in range(150):
        # Cinco ventas en 150 semanas: ADI 30, la definicion de intermitente.
        cantidad = 3.0 if semana % 30 == 0 else 0.0
        grumoso.append({
            "date": date(2024, 1, 1) + timedelta(weeks=semana),
            "product_id": "COLA-LARGA",
            "quantity": cantidad,
            "unit_cost": 10.0,
        })

    rec = recoverable_sales(grumoso)

    assert rec.skus_measured == 0
    assert rec.refused_reasons[REFUSED_INTERMITTENT] == 1
    assert rec.low is None and rec.high is None


def test_un_sku_de_demanda_pareja_si_se_mide():
    """El limite del guard de arriba: una serie sin huecos es normal-legitima."""
    parejo = _filas("PAREJO", semanas=52, cantidades=[10.0, 20.0] * 26, precio=10.0)
    rec = recoverable_sales(parejo)

    assert rec.skus_measured == 1
    assert rec.refused_reasons.get(REFUSED_INTERMITTENT, 0) == 0


def test_un_log_de_transacciones_disperso_se_declara_NO_MEDIBLE():
    """Superstore no puede sostener este calculo, y decirlo es la respuesta.

    Son 9.994 transacciones repartidas en 1.862 SKU sobre cuatro anios: 5,4
    ventas por SKU. Reindexada por semana, la serie de casi todos es ceros con
    algun pico -- 1.767 de 1.862 caen en intermittent/lumpy, y a nivel mensual
    tampoco mejora. Un stock de seguridad normal ahi no significa nada.

    Esto importa mas de lo que parece: **la banda "4-29%" que quedo registrada
    para Superstore el 2026-07-18 salio de correr el calculo SIN este guard.**
    Sin el, este mismo archivo devuelve 15,8%-54,4% de la venta anual, que roza
    el 43% historico por un camino distinto -- no por el z=0, sino por modelar
    con una normal una demanda que no lo es. Reproducir aquella banda seria
    reproducir el artefacto.
    """
    ruta = Path("data/kaggle/superstore.csv")
    if not ruta.exists():
        pytest.skip("el dataset publico de Superstore no esta en esta maquina")

    diag = diagnose(ruta)

    assert diag.concentration.skus_total == 1862, "el archivo publico cambio"
    assert diag.recoverable.high is None, "no habia con que medir; un numero seria inventado"
    assert diag.recoverable.refused_reasons[REFUSED_INTERMITTENT] > 1000
    assert "no medido" in render_report(diag).lower()


def test_una_historia_de_demanda_real_si_se_mide_y_queda_lejos_del_43():
    """El contrapunto del test de arriba, sobre datos que si sostienen el modelo.

    El portafolio de Olist es historia de demanda agregada, no un log de
    transacciones: sus ocho series son regulares y las ocho se miden. El techo
    tiene que quedar MUY por debajo del 43% que hacia inservible al informe.
    """
    ruta = Path("data/sample_demand_portfolio_olist.csv")
    if not ruta.exists():
        pytest.skip("el portafolio de Olist no esta en esta maquina")

    diag = diagnose(ruta)
    rec, venta = diag.recoverable, diag.concentration.revenue_total

    assert rec.skus_measured == 8
    assert rec.high is not None
    assert rec.high / venta < 0.29, f"el techo se escapo a {rec.high / venta:.1%}"
