"""La fecha de fin por SKU: la regla, la negativa, y que ninguna superficie se la calle.

`documentation/KERN_PALANCAS_SOBRE_25_MUNDOS.md` midio que esta es la palanca mas grande
de la rama (+0,1309 en 23/25 mundos, p=0,00001) y la unica que resiste la muestra
completa. Esto la construye, y lo que estos tests fijan no es el numero -- ese vive en su
linea base -- sino las tres propiedades sin las cuales la capacidad seria peligrosa:

1. **El tope encoge; NUNCA anula.** Una fecha mal tipeada (`2025` donde iba `2026`) es el
   modo de falla realista, y un tope de cero apaga la reposicion de un SKU que vende
   todas las semanas. Los tres verdictos que se niegan -- `expired`, `would_zero`,
   `unusable` -- existen para eso y cada uno tiene su test.
2. **Las DOS mitades deciden.** Cual es la segunda depende de la politica: `S` en un
   (R,S), `Q` en un (s,Q). Mirar solo el disparador dejaba pasar una Q mayor que todo lo
   que quedaba por vender, y ese defecto ocurrio de verdad mientras se escribia esto.
3. **La ausencia se declara, y la negativa tambien.** Sin columna el mapa es `None` y no
   `{}`; con una fecha que no se aplico, la planilla, el deck y las OPCIONES tienen que
   NOMBRARLA -- callarla se lee como si el tope la hubiera cubierto.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jobs import deliverables, intake, qa
from jobs.inventory_optimization import SkuRecommendation, run
from scm_agent.tool_options import inventory_options
from src.horizon import cap_policy, periods_until

PRIMER_LUNES = "2025-01-06"


def _archivo(tmp_path, fin_por_sku: dict[str, str], *, semanas: int = 20, media: float = 30.0):
    """Un CSV canonico con una columna de fecha de fin por SKU."""
    rng = np.random.default_rng(7)
    filas = []
    for d in pd.date_range(PRIMER_LUNES, periods=semanas, freq="W-MON"):
        for sku, fin in fin_por_sku.items():
            filas.append({
                "fecha": d.strftime("%Y-%m-%d"), "sku": sku,
                "cantidad": float(max(1.0, rng.normal(media, 5.0))),
                "costo": 10.0, "plazo_dias": 14, "fecha_fin": fin,
            })
    ruta = tmp_path / "ventas.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)
    return ruta


def _corrida(tmp_path, fin_por_sku, **kw):
    ruta = _archivo(tmp_path, fin_por_sku, **kw)
    demanda, _ = intake.prepare_tracked(ruta)
    frame = intake.sku_end_dates(ruta)
    nota = frame.attrs["horizon_note"] if frame is not None else ""
    return run(demanda, end_dates=intake.end_date_map(frame), horizon_note=nota), frame


# --------------------------------------------------------------------------- motor


def test_el_tope_NUNCA_anula_una_politica():
    """La propiedad de seguridad, y la razon por la que existen tres verdictos que se
    niegan. Una fecha ya pasada, una fecha de hoy y una media cero darian un tope <= 0;
    en los tres casos la politica sale INTACTA y el verdicto lo declara."""
    for periodos, media, esperado in (
        (-8.0, 10.0, "expired"),     # la fecha ya paso: el typo clasico (2025 por 2026)
        (0.0, 10.0, "expired"),      # es hoy
        (3.0, 0.0, "would_zero"),    # hay fecha, pero no queda demanda que citar
        (3.0, None, "unusable"),     # hay fecha y no hay media medida
    ):
        c = cap_policy(80.0, 120.0, mean_per_period=media, periods_remaining=periodos)
        assert c.verdict == esperado
        assert (c.trigger, c.target) == (80.0, 120.0), (
            f"{esperado}: el tope movio la politica en vez de negarse"
        )
        assert c.declarable, f"{esperado} tiene que ser nombrable por la superficie"
        assert not c.capped


def test_las_DOS_mitades_deciden_el_tope_no_solo_el_disparador():
    """El defecto real que este nombre existe para no repetir.

    Un (s,Q) trae `order_up_to=None` y sus dos cantidades son `s` y `Q`. Si solo el
    disparador decidiera, una Q mayor que todo lo que queda por vender saldria sin
    topear -- medido sobre un SKU con 204 de restante y Q=302.
    """
    # El disparador cabe holgado; la segunda mitad no.
    c = cap_policy(70.0, 302.0, mean_per_period=68.0, periods_remaining=3.0)  # restante 204
    assert c.verdict == "applied"
    assert c.trigger == 70.0, "el disparador cabia y no debia moverse"
    assert c.target == pytest.approx(204.0)

    # Y al reves: la segunda cabe, el disparador no.
    c2 = cap_policy(400.0, 100.0, mean_per_period=68.0, periods_remaining=3.0)
    assert c2.verdict == "applied"
    assert c2.trigger == pytest.approx(204.0)
    assert c2.target == 100.0


def test_una_fecha_lejana_es_inert_y_no_cuenta_como_topeada():
    """`inert` existe separado de `applied` para que el reporte no cuente como topeado a
    un SKU que no se movio: un conteo inflado convierte una capacidad inocua en una que
    parece estar tocando media cartera."""
    c = cap_policy(80.0, 120.0, mean_per_period=10.0, periods_remaining=52.0)
    assert c.verdict == "inert" and not c.capped and not c.declarable
    assert (c.trigger, c.target) == (80.0, 120.0)


def test_los_periodos_salen_de_la_MISMA_identidad_que_el_plazo_del_proveedor():
    """Cruza una frontera de unidades (calendario -> grilla del motor). Una segunda
    formula aca haria que dos numeros del mismo entregable midieran ventanas distintas
    sin decirlo."""
    from src.data_loader import lead_time_periods_from_days
    a, b = pd.Timestamp("2025-01-06"), pd.Timestamp("2025-07-07")
    assert periods_until(a, b, periods_per_year=52.0) == pytest.approx(
        lead_time_periods_from_days((b - a).days, periods_per_year=52.0)
    )
    assert periods_until(None, b) is None and periods_until(a, None) is None


# ----------------------------------------------------------------------- productor


def test_sin_columna_el_mapa_es_None_y_nunca_vacio(tmp_path):
    """Un mapa vacio se lee como "medido, y ningun SKU tiene fecha". Lo que pasa es que
    nadie la declaro, y las dos cosas mandan al operador a lugares distintos."""
    ruta = tmp_path / "sin.csv"
    pd.DataFrame({"fecha": ["2025-01-06", "2025-01-13"], "sku": ["A", "A"],
                  "cantidad": [10, 12]}).to_csv(ruta, index=False)
    assert intake.sku_end_dates(ruta) is None
    assert intake.end_date_map(None) is None


def test_una_fecha_ilegible_se_descarta_se_cuenta_y_no_se_reemplaza_por_hoy(tmp_path):
    """El reloj como fallback silencioso convertiria un typo en un tope sobre un SKU que
    sigue vendiendo. Una celda VACIA, en cambio, es la ausencia normal y no se cuenta
    como defecto: confundirlas llenaria el reporte de falsos problemas."""
    ruta = tmp_path / "v.csv"
    pd.DataFrame({
        "fecha": ["2025-01-06"] * 4,
        "sku": ["A", "B", "C", "D"],
        "cantidad": [10, 10, 10, 10],
        "fecha_fin": ["2026-06-30", "", "no-es-una-fecha", ""],
    }).to_csv(ruta, index=False)
    frame = intake.sku_end_dates(ruta)
    mapa = intake.end_date_map(frame)
    assert set(mapa) == {"A"}, "un SKU sin fecha legible no puede entrar al mapa"
    assert frame.attrs["unparsed"] == 1, "la ilegible se cuenta"
    assert frame.attrs["n_products"] == 4


def test_un_SKU_con_dos_fechas_distintas_se_declara(tmp_path):
    """El archivo se contradice sobre cuando muere un producto. Gana la ultima fila
    fechada -- una sola regla para todo el archivo, la misma que `stock_position` -- pero
    la contradiccion se cuenta en vez de quedarse con una callada."""
    ruta = tmp_path / "v.csv"
    pd.DataFrame({
        "fecha": ["2025-01-06", "2025-01-13"],
        "sku": ["A", "A"], "cantidad": [10, 12],
        "fecha_fin": ["2026-05-01", "2026-08-01"],
    }).to_csv(ruta, index=False)
    frame = intake.sku_end_dates(ruta)
    assert frame.attrs["conflicting"] == 1
    assert intake.end_date_map(frame)["A"] == pd.Timestamp("2026-08-01")


# ------------------------------------------------------------------------- el job


def test_sin_fechas_el_job_sale_IDENTICO_a_antes_de_la_capacidad(tmp_path):
    """El control. Una capacidad nueva que mueve un numero en una corrida que no la usa
    es una regresion silenciosa para toda la base instalada."""
    ruta = _archivo(tmp_path, {"A": "", "B": ""})
    demanda, _ = intake.prepare_tracked(ruta)
    base = sorted(run(demanda).recommendations, key=lambda r: r.product_id)
    con = sorted(run(demanda, end_dates=None).recommendations, key=lambda r: r.product_id)
    for a, b in zip(base, con, strict=True):
        assert (a.reorder_point, a.order_quantity, a.order_up_to) == (
            b.reorder_point, b.order_quantity, b.order_up_to
        )
        assert b.horizon_verdict == "no_date"
        assert b.reorder_point_before_horizon is None


def test_el_job_topea_declara_y_no_toca_a_los_demas(tmp_path):
    """Los cuatro caminos en una sola corrida: lejana, cerca, vencida y sin fecha."""
    report, frame = _corrida(tmp_path, {
        "A": "2027-01-01",   # lejana
        "B": "2025-07-07",   # cerca
        "C": "2025-03-03",   # vencida (antes del fin del historico)
        "D": "",             # sin fecha
    })
    por_sku = {r.product_id: r for r in report.recommendations}
    assert por_sku["A"].horizon_verdict == "inert"
    assert por_sku["B"].horizon_verdict == "applied"
    assert por_sku["C"].horizon_verdict == "expired"
    assert por_sku["D"].horizon_verdict == "no_date"

    b = por_sku["B"]
    assert b.order_quantity_before_horizon > b.order_quantity
    assert b.order_quantity == pytest.approx(b.remaining_demand)
    # La vencida sale dimensionada como si no hubiera fecha, que es la negativa segura.
    c = por_sku["C"]
    assert c.order_quantity > 0 and c.reorder_point > 0
    assert c.reorder_point_before_horizon is None

    assert report.n_with_end_date == 3
    assert report.n_horizon_capped == 1
    assert report.n_horizon_declarable == 1
    assert report.horizon_units_freed > 0
    assert report.horizon_note == frame.attrs["horizon_note"]


def test_un_before_horizon_aparece_SOLO_si_ese_numero_se_movio(tmp_path):
    """El tope puede bajar una mitad y dejar la otra igual -- es el caso comun. Escribir
    el valor viejo en las dos haria leer un cambio donde no lo hubo."""
    report, _ = _corrida(tmp_path, {"B": "2025-07-07"})
    b = report.recommendations[0]
    assert b.horizon_verdict == "applied"
    movidos = [
        nombre for nombre, antes in (
            ("s", b.reorder_point_before_horizon),
            ("S", b.order_up_to_before_horizon),
            ("Q", b.order_quantity_before_horizon),
        ) if antes is not None
    ]
    assert movidos, "un tope aplicado tiene que registrar al menos un numero movido"
    for nombre, antes, ahora in (
        ("s", b.reorder_point_before_horizon, b.reorder_point),
        ("S", b.order_up_to_before_horizon, b.order_up_to),
        ("Q", b.order_quantity_before_horizon, b.order_quantity),
    ):
        if antes is not None:
            assert ahora < antes, f"{nombre}: el 'antes' no es mayor que el 'despues'"


def test_el_as_of_es_el_fin_del_HISTORICO_y_no_el_reloj(tmp_path):
    """Un archivo que termina hace tres meses se mediria contra un horizonte que no es el
    suyo, y con una fecha de fin intermedia TODO el catalogo saldria `expired`. Es la
    misma regla que `tools._fecha_param`: el reloj como fallback fecha el reporte donde
    no corresponde."""
    # El historico termina el 2025-05-19 (20 semanas desde el 2025-01-06). Una fecha de
    # fin POSTERIOR a eso pero ANTERIOR a hoy tiene que seguir siendo un horizonte vivo.
    report, _ = _corrida(tmp_path, {"A": "2025-08-01"})
    assert report.recommendations[0].horizon_verdict in ("applied", "inert"), (
        "se midio contra el reloj: una fecha posterior al historico salio vencida"
    )


# ----------------------------------------------------------------------- compuerta


def _registro(**kw) -> SkuRecommendation:
    base = dict(
        product_id="X", method="m", intermittent=False, forecast=10.0, error_std=1.0,
        bias=0.0, mae=0.0, policy_kind="(s, Q)", order_quantity=50.0, order_up_to=None,
        reorder_point=20.0, safety_stock=5.0, z_factor=1.64, service_level=0.95,
        unit_cost=1.0, lead_periods=1.0, cycle_investment=25.0, ss_investment=5.0,
        investment=30.0, status="ok",
    )
    return SkuRecommendation(**{**base, **kw})


def test_la_compuerta_rechaza_un_tope_que_anula():
    """Si un cambio futuro dejara pasar un tope de cero, QA lo para antes del
    entregable: "QA falla => no sale nada"."""
    malo = _registro(
        horizon_verdict="applied", remaining_demand=0.0,
        order_quantity=0.0, order_quantity_before_horizon=50.0,
    )
    problemas = qa._horizon_issues(malo)
    assert any("zeroed" in p for p in problemas), problemas


def test_la_compuerta_rechaza_un_antes_que_no_es_mayor_y_un_verdicto_que_no_topea():
    """Dos direcciones. Un "antes" igual o menor al "despues" seria un tope que subio la
    politica; un `*_before_horizon` en una linea no topeada hace leer un cambio inexistente."""
    subio = _registro(
        horizon_verdict="applied", remaining_demand=80.0,
        order_quantity=50.0, order_quantity_before_horizon=40.0,
    )
    assert any("not above" in p for p in qa._horizon_issues(subio))

    intacto_mentiroso = _registro(
        horizon_verdict="expired", order_quantity_before_horizon=90.0,
    )
    assert any("does not cap" in p for p in qa._horizon_issues(intacto_mentiroso))

    sano = _registro(
        horizon_verdict="applied", remaining_demand=40.0,
        order_quantity=40.0, order_quantity_before_horizon=50.0,
    )
    assert qa._horizon_issues(sano) == []


# ---------------------------------------------------------------------- superficies


def test_las_OPCIONES_nombran_el_tope_y_la_fecha_que_NO_se_aplico(tmp_path):
    """Las dos mitades. Callar la segunda es peor que callar la primera: el cliente
    creeria que el tope cubrio un SKU cuya fecha este plan se nego a usar."""
    report, _ = _corrida(tmp_path, {"B": "2025-07-07", "C": "2025-03-03"})
    guiado = inventory_options(report)

    opciones = [o.label for o in guiado.options]
    assert any("end date" in o for o in opciones), opciones
    tope = next(o for o in guiado.options if "end date" in o.label)
    assert "unit(s) of purchase" in tope.summary and "left to sell" in tope.summary

    residuales = [r.description for r in guiado.residuals]
    assert any("did NOT apply" in d for d in residuales), residuales
    negado = next(r for r in guiado.residuals if "did NOT apply" in r.description)
    assert "discontinuation" in negado.description
    assert negado.risk_if_skipped


def test_la_planilla_del_cliente_lleva_la_fecha_y_el_verdicto(tmp_path):
    """El comprador que abre el Excel tiene que ver, EN LA FILA DEL SKU, que el archivo
    traia una fecha que no se aplico -- no solo en un residual del resumen."""
    import csv

    report, _ = _corrida(tmp_path, {"B": "2025-07-07", "C": "2025-03-03"})
    salida = tmp_path / "entregable"
    rutas = deliverables.write_all(report, salida, client="T")
    csvs = [p for p in rutas.values() if str(p).endswith(".csv")]
    hoja = next(p for p in csvs if "end_date" in p.read_text(encoding="utf-8").splitlines()[0])
    filas = {f["product_id"]: f for f in csv.DictReader(hoja.open(encoding="utf-8"))}

    assert filas["B"]["horizon_verdict"] == "applied"
    assert filas["B"]["end_date"] == "2025-07-07"
    assert float(filas["B"]["order_qty_before_horizon"]) > float(filas["B"]["order_qty_Q"])
    assert float(filas["B"]["remaining_demand"]) > 0

    assert filas["C"]["horizon_verdict"] == "expired"
    assert filas["C"]["end_date"] == "2025-03-03"
    # Nunca un cero donde no hubo cambio: un cero afirmaria un tope que no ocurrio.
    assert filas["C"]["order_qty_before_horizon"] == ""
    assert filas["C"]["remaining_demand"] == ""


def test_el_deck_publica_las_dos_cosas(tmp_path):
    """Un plan que topea sin decirlo le pide al lector que confie, y uno que se calla una
    fecha negada se lee como si la hubiera cubierto."""
    from jobs.inventory_deliverable import _horizon_findings
    report, _ = _corrida(tmp_path, {"B": "2025-07-07", "C": "2025-03-03"})
    titulos = [f.title for f in _horizon_findings(report)]
    assert any("capped by their end-of-life date" in t for t in titulos), titulos
    assert any("NOT applied" in t for t in titulos), titulos


# ------------------------------------------- revision 2026-10-09: tope + lote y tope + cobertura


def test_un_tope_sobre_un_lote_del_proveedor_sigue_siendo_un_lote_legal(tmp_path):
    """El tope corria DESPUES del redondeo a lote y dejaba la Q en el restante crudo
    (133,8 con multiplo 12): `qa.verify` la rechazaba por los terminos del proveedor y
    la corrida entera salia sin entregable. La Q topeada es el lote legal mas grande que
    no supera lo que queda por vender."""
    ruta = _archivo(tmp_path, {"SKU-A": "2025-05-29"})
    frame = pd.read_csv(ruta)
    frame["multiplo"] = 12
    frame.to_csv(ruta, index=False)
    demanda, _ = intake.prepare_tracked(ruta)
    fines = intake.sku_end_dates(ruta)
    reporte = run(demanda, end_dates=intake.end_date_map(fines), horizon_note=fines.attrs["horizon_note"])

    (rec,) = reporte.recommendations
    assert rec.horizon_verdict == "applied" and rec.order_quantity_before_horizon is not None
    assert rec.order_quantity % 12 == 0
    assert rec.order_quantity <= rec.remaining_demand
    assert qa.verify(reporte) == []


def test_con_restante_bajo_el_minimo_del_proveedor_la_q_queda_en_el_minimo_legal():
    """Si lo que queda por vender no llega ni al lote minimo, comprar menos que el minimo
    no es una opcion que Kern pueda imprimir: la Q baja hasta el minimo legal, y la
    compuerta lo acepta aunque supere el restante."""
    malo = _registro(
        policy_kind="(s, Q)", horizon_verdict="applied", remaining_demand=20.0,
        order_quantity=48.0, order_quantity_before_horizon=120.0,
        lot_rule="moq 48", lot_moq=48.0, lot_multiple=0.0,
    )
    assert not any("exceeds the remaining" in p for p in qa._horizon_issues(malo))
    por_encima = _registro(
        policy_kind="(s, Q)", horizon_verdict="applied", remaining_demand=20.0,
        order_quantity=60.0, order_quantity_before_horizon=120.0,
        lot_rule="moq 48", lot_moq=48.0, lot_multiple=0.0,
    )
    assert any("exceeds the remaining" in p for p in qa._horizon_issues(por_encima))


def test_una_cobertura_provisional_topeada_pasa_la_compuerta(tmp_path):
    """Tres semanas de historia, plazo de 28 dias y una fecha de fin a 7 dias: la
    cobertura topeada ya no es media x periodos de riesgo y la compuerta tumbaba la
    corrida entera. La cuenta sigue cerrando en dos pasos: la cobertura de antes del
    tope es media x tau, y la de despues es el minimo entre esa y el restante."""
    filas = [{"fecha": d.strftime("%Y-%m-%d"), "sku": "SKU-Z", "cantidad": q, "costo": 20.0,
              "plazo_dias": 28, "fecha_fin": "2025-01-27"}
             for d, q in zip(pd.date_range(PRIMER_LUNES, periods=3, freq="W-MON"), (40, 55, 47))]
    ruta = tmp_path / "corta.csv"
    pd.DataFrame(filas).to_csv(ruta, index=False)
    demanda, _ = intake.prepare_tracked(ruta)
    fines = intake.sku_end_dates(ruta)
    reporte = run(demanda, end_dates=intake.end_date_map(fines), horizon_note=fines.attrs["horizon_note"])

    (rec,) = reporte.recommendations
    assert rec.policy_kind == "provisional_cover" and rec.horizon_verdict == "applied"
    assert rec.reorder_point < rec.reorder_point_before_horizon
    assert rec.reorder_point_before_horizon == pytest.approx(
        rec.observed_mean_per_period * rec.cover_periods)
    assert qa.verify(reporte) == []
