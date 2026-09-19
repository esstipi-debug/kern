"""Una negativa del motor no puede salir vestida de numero.

El defecto, medido punta a punta antes de arreglarlo: un SKU con cinco semanas de
ventas REALES (~42 u/semana) y 120 unidades en el estante llegaba al operador como
un evento ``dead_stock`` -- *"120 units on hand with no demand"*. La cadena era
entera y cada eslabon estaba bien escrito:

1. ``jobs/inventory_optimization.py`` se niega a dimensionar con menos de
   ``MIN_PERIODS_FOR_POLICY`` periodos -- correcto, y el comentario lo dice:
   *"Refuse rather than degrade"*.
2. ...pero emitia la negativa con ``forecast=0.0``, ``reorder_point=0.0``,
   ``safety_stock=0.0`` y ``z_factor=0.0``, con ``status="ok"`` y
   ``qa_issues=[]``. El aviso viajaba por otro canal (``policy_kind``, un residual
   de ``scm_agent/tool_options.py``), o sea por la prosa.
3. ``jobs/state_bridge.py::_usable`` existe justamente para omitir esa linea. Su
   docstring lo dice literal: *"un disparador ausente no es cero (pediria siempre)
   y una demanda diaria ausente no es cero (daria cobertura infinita justo sobre el
   SKU del que no se sabe nada)"*. El 0.0 del productor la DERROTABA: 0.0 no es
   ``None``, asi que la linea pasaba.
4. ``src/alerting.py`` ve ``avg_daily_demand == 0`` con stock en mano y emite
   ``dead_stock``.

Un 0 significa "medido, y la respuesta es ninguno". Una negativa no midio nada.

Estos tests fijan las DOS direcciones, porque arreglar solo una cambia el defecto
de lugar: una linea sin politica no puede traer un numero, y una linea con politica
no puede traer un ``None``.
"""

from __future__ import annotations

import types

import pandas as pd
import pytest

from jobs import deliverables, qa
from jobs.inventory_optimization import run
from jobs.state_bridge import stock_lines_from_inventory
from src.alerting import detect_events
from src.forecasting import MIN_PERIODS_FOR_POLICY

_PERIOD = "W"
_CAMPOS = ("forecast", "reorder_point", "safety_stock", "z_factor")


def _archivo(product_id: str, cantidades, *, unit_cost: float = 10.0,
             on_hand: float | None = None) -> pd.DataFrame:
    fechas = pd.date_range("2026-01-04", periods=len(cantidades), freq=_PERIOD)
    filas = []
    for f, q in zip(fechas, cantidades, strict=True):
        fila = {"date": f, "product_id": product_id, "quantity": float(q),
                "unit_cost": unit_cost, "lead_time_days": 14.0}
        if on_hand is not None:
            fila["on_hand"] = on_hand
        filas.append(fila)
    return pd.DataFrame(filas)


def _rec(df: pd.DataFrame, pid: str, *, on_hand: float | None = None, **kw):
    """``run()`` recibe la posicion por argumento, no por columna del frame: la
    columna la lee ``jobs/intake.py::stock_position`` en el ``prepare`` del tool."""
    if on_hand is not None:
        kw["on_hand_by_sku"] = {pid: on_hand}
    reporte = run(df, period_freq=_PERIOD, **kw)
    return reporte, {r.product_id: r for r in reporte.recommendations}[pid]


# ---------------------------------------------------------------------------
# 1. El productor: ausencia es None, medicion es 0
# ---------------------------------------------------------------------------


def test_historia_insuficiente_anula_los_cuatro_campos_dimensionados():
    """La negativa por conocimiento: el SKU vende, no sabemos cuanto."""
    _, r = _rec(_archivo("NUEVO", [40, 44, 38, 47, 41]), "NUEVO")

    assert r.policy_kind == "insufficient_history"
    assert r.n_periods < MIN_PERIODS_FOR_POLICY
    for campo in _CAMPOS:
        assert getattr(r, campo) is None, f"{campo} salio {getattr(r, campo)!r}, no None"
    # Los HECHOS medidos no se anulan: solo lo que el motor no calculo.
    assert r.n_periods == 5
    assert r.unit_cost == 10.0
    assert r.investment == 0.0   # no se estaciona plata: eso SI es un cero medido


def test_sin_costo_usable_anula_lo_mismo_porque_la_demanda_es_real():
    """La otra negativa: hay demanda de sobra, lo que falta es el costo.

    Un 0 aca diria que el SKU no vende, y vende todas las semanas.
    """
    _, r = _rec(_archivo("SIN-COSTO", [30] * 12, unit_cost=0.0), "SIN-COSTO")

    assert r.policy_kind == "skipped_zero_cost"
    for campo in _CAMPOS:
        assert getattr(r, campo) is None, f"{campo} salio {getattr(r, campo)!r}, no None"


def test_demanda_medida_en_cero_CONSERVA_sus_ceros():
    """La mitad de la regla que no cambia, y el caso que prueba que la regla es
    sobre la ausencia y no sobre la rama.

    Aca la demanda agregada SI se midio y dio cero. "El pronostico es 0 y el
    disparador es 0" es una medicion -- exactamente lo que un 0 debe significar.
    """
    _, r = _rec(_archivo("SIN-VENTA", [0] * 12), "SIN-VENTA")

    assert r.policy_kind == "skipped_zero_demand"
    for campo in _CAMPOS:
        assert getattr(r, campo) == 0.0, f"{campo} salio {getattr(r, campo)!r}, no 0.0"


# ---------------------------------------------------------------------------
# 2. La regresion punta a punta: el falso dead_stock
# ---------------------------------------------------------------------------


def _eventos_del_tower(reporte):
    lineas = stock_lines_from_inventory(reporte)
    return lineas, detect_events([
        {"product_id": ln.product_id, "on_hand": ln.on_hand,
         "reorder_point": ln.reorder_point, "avg_daily_demand": ln.avg_daily_demand}
        for ln in lineas
    ])


def test_un_sku_que_vende_todas_las_semanas_NO_sale_como_stock_muerto():
    """El caso de cabecera, con el mismo archivo con el que se reprodujo el defecto."""
    reporte, r = _rec(_archivo("NUEVO", [40, 44, 38, 47, 41]), "NUEVO", on_hand=120.0)
    assert r.policy_kind == "insufficient_history"
    assert r.on_hand == 120.0, "el estante es un hecho medido y tiene que seguir ahi"

    lineas, eventos = _eventos_del_tower(reporte)
    assert lineas == [], "un SKU sin tasa de demanda no se puede monitorear; se omite"
    assert [e.kind for e in eventos] == []


def test_el_dead_stock_VERDADERO_sigue_saliendo():
    """El freno del arreglo anterior: anular de mas apagaria una alarma correcta.

    Un SKU con demanda medida en cero y stock parado SI es stock muerto, y ese
    evento tiene que sobrevivir -- por eso `skipped_zero_demand` conserva sus ceros.
    """
    reporte, r = _rec(_archivo("SIN-VENTA", [0] * 12), "SIN-VENTA", on_hand=90.0)
    assert r.policy_kind == "skipped_zero_demand"

    lineas, eventos = _eventos_del_tower(reporte)
    assert len(lineas) == 1
    assert [e.kind for e in eventos] == ["dead_stock"]


# ---------------------------------------------------------------------------
# 3. La compuerta de QA, en las dos direcciones
# ---------------------------------------------------------------------------


def test_qa_pasa_sobre_una_negativa_bien_declarada():
    reporte, _ = _rec(_archivo("NUEVO", [40, 44, 38, 47, 41]), "NUEVO")
    assert qa.verify(reporte) == []


@pytest.mark.parametrize("campo", _CAMPOS)
def test_qa_rechaza_una_negativa_que_trae_un_numero(campo):
    """Por mutacion: se vuelve a poner el 0.0 de antes, campo por campo."""
    from dataclasses import replace

    reporte, r = _rec(_archivo("NUEVO", [40, 44, 38, 47, 41]), "NUEVO")
    mutado = replace(reporte, recommendations=[replace(r, **{campo: 0.0})])

    issues = qa.verify(mutado)
    assert any("declined to size" in i for i in issues), issues
    assert any(campo in i for i in issues), issues


@pytest.mark.parametrize("campo", _CAMPOS)
def test_qa_rechaza_una_politica_con_precio_a_la_que_le_falta_un_numero(campo):
    """La direccion contraria: anularlo TODO tambien seria mentir.

    Sin este test, "arreglar" el defecto poniendo None en todas partes pasaria la
    compuerta y dejaria al operador sin politica en los SKU que si se dimensionaron.
    """
    from dataclasses import replace

    reporte, r = _rec(_archivo("LARGO", [20 + (i % 4) for i in range(24)]), "LARGO")
    assert r.policy_kind in {"(s, Q)", "(R, S)"}
    mutado = replace(reporte, recommendations=[replace(r, **{campo: None})])

    issues = qa.verify(mutado)
    assert any(f"no {campo}" in i for i in issues), issues


def test_las_dos_listas_de_politicas_sin_precio_coinciden():
    """`jobs.qa` tenia las tres clases y `src.verify.coherencia` solo dos, asi que
    `insufficient_history` entraba al analisis de coherencia con sus ceros y pasaba
    toda invariante trivialmente. Dos listas que significan lo mismo no pueden
    separarse otra vez sin ponerse rojo.
    """
    from src.verify.coherencia import POLITICAS_SALTADAS

    assert POLITICAS_SALTADAS == qa.UNPRICED_POLICY_KINDS


# ---------------------------------------------------------------------------
# 4. Lo que el operador lee
# ---------------------------------------------------------------------------


def test_la_planilla_deja_la_celda_vacia_y_el_markdown_una_raya():
    _, r = _rec(_archivo("NUEVO", [40, 44, 38, 47, 41]), "NUEVO")
    fila = deliverables._row(r)

    assert fila["reorder_point_s"] is None
    assert fila["safety_stock"] is None
    assert fila["forecast_per_period"] is None
    assert deliverables._celda(None, 0) == "—"
    assert deliverables._celda(12.0, 0) == "12"


def test_el_markdown_no_imprime_un_cero_en_el_disparador(tmp_path):
    reporte, _ = _rec(_archivo("NUEVO", [40, 44, 38, 47, 41]), "NUEVO")
    destino = deliverables.write_report_md(reporte, tmp_path / "r.md")
    fila = [ln for ln in destino.read_text().splitlines()
            if ln.startswith("| NUEVO |")]

    assert fila, "el SKU refusado tiene que aparecer en la tabla, no desaparecer"
    assert "insufficient_history" in fila[0]
    assert "—" in fila[0]
    assert " 0 |" not in fila[0] and " 0.0 |" not in fila[0]


def test_el_resumen_nombra_la_negativa_en_vez_de_recomendar_cero(tmp_path):
    """Punta a punta por el orquestador, que es como lo lee el operador."""
    from scm_agent.orchestrator import Orchestrator

    destino = tmp_path / "v.csv"
    _archivo("NUEVO", [40, 44, 38, 47, 41], on_hand=120.0).to_csv(destino, index=False)
    res = Orchestrator(clients_root=None).run(
        "punto de reorden y stock de seguridad por SKU",
        data_path=str(destino), job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )

    assert res.status == "ok"
    assert "No policy was sized" in res.summary
    assert "NOT a recommendation" in res.summary
    assert "recommended inventory investment $0" not in res.summary


# ---------------------------------------------------------------------------
# 5. El marcador: la cantidad emitida no cambia, la trazabilidad si
# ---------------------------------------------------------------------------


def test_el_corredor_omite_el_sku_sin_politica_en_vez_de_ponerle_cero(monkeypatch):
    from src.simworld import runners

    rec_ok = types.SimpleNamespace(product_id="A", reorder_point=30.0, forecast=6.0,
                                   order_up_to=50.0, order_quantity=20.0)
    rec_no = types.SimpleNamespace(product_id="B", reorder_point=None, forecast=None,
                                   order_up_to=None, order_quantity=None)
    resultado = types.SimpleNamespace(
        status="ok", summary="s", qa_issues=[], guided=None, confidence=1.0,
        report=types.SimpleNamespace(recommendations=[rec_ok, rec_no],
                                     intake_quality=None, intake_quality_threshold=0.2),
    )
    monkeypatch.setattr(runners, "export_history", lambda *a, **k: a[2])
    monkeypatch.setattr(runners, "Orchestrator",
                        lambda **k: types.SimpleNamespace(run=lambda *a, **k2: resultado))

    pol = runners.kern_policy(None, [], level=0, seed=0, out_dir="/tmp/x", forced=True)

    assert "A" in pol.reorder_point
    assert "B" not in pol.reorder_point, "un SKU sin politica no entra al plan con un 0"
    assert "B" not in pol.order_up_to
    assert "B" not in pol.forecast


def test_omitir_y_poner_cero_emiten_la_MISMA_orden():
    """Por que el arreglo no mueve la captura del marcador, dicho como test.

    `apply_policy` ya trata un SKU ausente igual que uno con disparador 0: no pide.
    O sea que el numero publicado (-40,6% limpia) no estaba inflado por esto -- el
    carril se comportaba bien y lo REPORTABA mal. Lo que cambia es que ahora se
    puede distinguir "me negue" de "decidi no reponer".
    """
    import numpy as np

    from src.simworld.runners import Policy, apply_policy
    from src.simworld.world import WorldState, generate_world

    mundo = generate_world(seed=7, n_skus=3, n_suppliers=1, weeks=6)
    estado = WorldState(week=0, on_hand=np.array([5.0, 5.0, 5.0]), in_transit=())
    ausente = Policy(reorder_point={}, order_up_to={})
    en_cero = Policy(reorder_point={s.sku: 0.0 for s in mundo.skus},
                     order_up_to={s.sku: 0.0 for s in mundo.skus})

    assert apply_policy(mundo, estado, ausente) == apply_policy(mundo, estado, en_cero)


# ---------------------------------------------------------------------------
# 6. El consumidor que el mapa encontro y la suite no: la pantalla de brecha
# ---------------------------------------------------------------------------
#
# `webapp/demo_gap_value.py` RESTA dos dimensionamientos del mismo SKU (el del ERP
# contra el de la realidad) y arma `SkuGap`, un dataclass de floats. No filtraba por
# `policy_kind`: filtraba por tener plazo declarado, observado y dispersion. Un SKU
# con recepciones de sobra pero poca historia de demanda pasaba ese filtro y llegaba
# con los dos buffers en 0.0 -> `delta_value` 0,00, o sea "el ERP y la realidad
# coinciden en este SKU" sobre uno que nadie dimensiono. Con `None` ese mismo camino
# revienta con TypeError en una ruta de produccion (`webapp/app.py`), asi que la
# unica salida honesta es excluirlo Y CONTARLO.

_RECEIPTS = "data/sample_receipts_portfolio.csv"
_DEMAND = "data/sample_demand_portfolio.csv"


def _con_sku_sin_dimensionar(rec: pd.DataFrame, dem: pd.DataFrame):
    """Un SKU con recepciones de sobra y menos historia que el minimo.

    Proveedor PROPIO a proposito: colgarlo de uno existente mueve el scorecard de
    plazo de ese proveedor y con el las brechas de todos sus SKU, y entonces el test
    no podria afirmar que el `net` no se movio.
    """
    nuevas_rec = pd.DataFrame([
        {"po_id": f"OC-N{i}", "supplier": "Proveedor Nuevo SA", "product_id": "SKU-SIN-DIM",
         "order_date": f"2024-0{(i % 9) + 1}-01", "receipt_date": f"2024-0{(i % 9) + 1}-1{i % 8}",
         "declared_lead_time_days": 20}
        for i in range(6)
    ])
    ultimas = sorted(dem["date"].unique())[-4:]   # 4 periodos < MIN_PERIODS_FOR_POLICY
    nuevas_dem = pd.DataFrame([
        {"date": f, "product_id": "SKU-SIN-DIM", "quantity": 42.0,
         "unit_cost": 10.0, "lead_time_days": 20}
        for f in ultimas
    ])
    return (pd.concat([rec, nuevas_rec], ignore_index=True),
            pd.concat([dem, nuevas_dem], ignore_index=True))


def test_la_pantalla_de_brecha_excluye_y_CUENTA_el_sku_sin_dimensionar():
    pytest.importorskip("openpyxl")
    from webapp.demo_gap_value import run_gap_value

    rec, dem = pd.read_csv(_RECEIPTS), pd.read_csv(_DEMAND)
    base = run_gap_value(rec, dem)
    con = run_gap_value(*_con_sku_sin_dimensionar(rec, dem))

    # No revienta -- es una ruta de produccion del webapp.
    assert con.n_unsized_skus == 1
    assert "SKU-SIN-DIM" not in {g.product_id for g in con.gaps}
    # Y no se cuela como una brecha de cero, que era el sintoma viejo.
    assert len(con.gaps) == len(base.gaps)
    assert con.net == pytest.approx(base.net)
    assert base.n_unsized_skus == 0


def test_el_libro_de_brecha_declara_la_exclusion_en_el_embudo(tmp_path):
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    from webapp.demo_gap_value import run_gap_value
    from webapp.demo_gap_workbook import write_gap_value_workbook

    rec, dem = pd.read_csv(_RECEIPTS), pd.read_csv(_DEMAND)

    def texto(valor, nombre):
        ruta = write_gap_value_workbook(valor, tmp_path / nombre,
                                        dataset_label="d.csv", ts="T")
        wb = load_workbook(ruta)
        return "\n".join(
            str(c) for hoja in wb.sheetnames
            for fila in wb[hoja].iter_rows(values_only=True) for c in fila if c
        )

    con = texto(run_gap_value(*_con_sku_sin_dimensionar(rec, dem)), "con.xlsx")
    assert "NO los dimensiono" in con

    # Y una corrida sin ninguno no gana un renglon que no dice nada.
    sin = texto(run_gap_value(rec, dem), "sin.xlsx")
    assert "NO los dimensiono" not in sin
