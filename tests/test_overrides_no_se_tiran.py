"""Un hecho del cliente que el agente acepta y tira en silencio es peor que uno que rechaza.

El defecto, medido punta a punta antes de arreglarlo: `jobs/inventory_optimization.run`
acepta `lead_time_stds` -- la dispersion del plazo del proveedor -- desde que existe la
columna `lead_time_std` del CSV, y `scm_agent/tools.py::_inventory_run` **nunca la
reenviaba**. Un cliente que la declaraba en su `overrides` no recibia ni un error ni un
aviso: recibia una politica dimensionada como si el plazo no tuviera dispersion
NINGUNA, con el entregable diciendo igual "95% service level".

Lo que cuesta no es cosmetico. El periodo de riesgo es
``sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2)`` (Vandeput ec. 6.5), y el
termino que se perdia es el DOMINANTE cuando la demanda es grande frente a su propia
dispersion -- que es el caso normal de un SKU que vende todas las semanas. Medido sobre
la misma serie: llamando al job, `lead_time_stds={"A": 3.0}` mueve el buffer de 11,9 a
238,4 unidades; pasando por el orquestador quedaba en 11,9.

Se encontro midiendo el residuo de la brecha del marcador
(`documentation/KERN_POR_QUE_BAJO_EL_PISO.md`): un carril que le pasaba a Kern la
dispersion observada de sus propias recepciones salia IDENTICO al carril de control, lo
que no podia ser cierto.

Los dos tests de abajo son las dos mitades:
- el de comportamiento fija que el dato viaje y que un CERO EXPLICITO se distinga de una
  ausencia (`lead_time_std_source`), que es la regla de toda esta rama;
- el estructural recorre la firma del job y se pone rojo si aparece un parametro que el
  agente no reenvia ni declara por escrito por que no. Es el mismo patron que
  `tests/test_barrido_neteo.py`: la lista no se mantiene a mano, se deriva, y agregar un
  parametro obliga a decidir.
"""

from __future__ import annotations

import ast
import inspect
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from jobs import inventory_optimization as job
from scm_agent import tools
from scm_agent.orchestrator import Orchestrator

#: Parametros de `run_inventory` que el agente NO expone A PROPOSITO, cada uno con su
#: razon. No son hechos del cliente: son perillas del motor, y abrirlas por `overrides`
#: convertiria una compuerta en una sugerencia.
NO_EXPUESTOS_A_PROPOSITO = {
    "demand": "es el payload, no un parametro: lo arma `prepare`",
    "min_periods": (
        "es la compuerta de historia minima. Bajarla no es un arreglo -- medido, la "
        "corrida sale `qa_failed` y la politica vuelve vacia igual "
        "(documentation/KERN_POR_QUE_BAJO_EL_PISO.md, E2a) -- y exponerla dejaria que "
        "quien la baja se saltee la negativa del motor desde afuera"
    ),
    "oos_origins": (
        "es el presupuesto de origenes del backtest del pronostico, una perilla de "
        "costo/precision del motor. No es un hecho del cliente ni cambia lo que se le "
        "promete"
    ),
}


def _kwargs_que_el_agente_reenvia() -> set[str]:
    """Los nombres de argumento de la llamada a `run_inventory` en `_inventory_run`.

    Se leen del AST y no de una lista escrita al lado, porque una lista escrita al lado
    es exactamente lo que se desincronizo.
    """
    arbol = ast.parse(Path(inspect.getfile(tools)).read_text())
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "_inventory_run":
            for hijo in ast.walk(nodo):
                if (isinstance(hijo, ast.Call)
                        and isinstance(hijo.func, ast.Name)
                        and hijo.func.id == "run_inventory"):
                    return {kw.arg for kw in hijo.keywords if kw.arg}
    raise AssertionError("no se encontro la llamada a run_inventory en _inventory_run")


def test_el_agente_reenvia_TODO_parametro_del_job_o_declara_por_que_no():
    """El guard estructural: agregar un parametro al job obliga a decidir.

    Si aparece uno nuevo y nadie lo reenvia, este test nombra cual. Si de verdad no
    corresponde exponerlo, se agrega a `NO_EXPUESTOS_A_PROPOSITO` con su razon -- que es
    el punto: la decision queda escrita, no olvidada.
    """
    del_job = set(inspect.signature(job.run).parameters)
    reenviados = _kwargs_que_el_agente_reenvia()

    faltantes = del_job - reenviados - set(NO_EXPUESTOS_A_PROPOSITO)
    assert not faltantes, (
        f"`jobs.inventory_optimization.run` acepta {sorted(faltantes)} y "
        "`scm_agent/tools.py::_inventory_run` no los reenvia. Un hecho del cliente que "
        "el agente acepta y tira en silencio es peor que uno que rechaza: no hay error "
        "y el entregable promete igual. Reenvialo, o agregalo a "
        "NO_EXPUESTOS_A_PROPOSITO con la razon."
    )

    # Y al reves: la lista de exenciones no puede tener fantasmas. Un nombre que el job
    # ya no acepta es una razon escrita para algo que no existe.
    fantasmas = set(NO_EXPUESTOS_A_PROPOSITO) - del_job
    assert not fantasmas, f"NO_EXPUESTOS_A_PROPOSITO nombra parametros inexistentes: {sorted(fantasmas)}"


def _serie(tmp: Path) -> str:
    fechas = pd.date_range("2026-01-05", periods=24, freq="W")
    pd.DataFrame([
        {"date": f, "product_id": "A", "quantity": 40.0 + (i % 7) * 3,
         "unit_cost": 20.0, "lead_time_days": 7.0}
        for i, f in enumerate(fechas)
    ]).to_csv(tmp / "v.csv", index=False)
    return str(tmp / "v.csv")


def _por_el_agente(ruta: str, salida: Path, **overrides):
    res = Orchestrator(clients_root=None).run(
        "reorder point y stock de seguridad por SKU", data_path=ruta,
        job_type="inventory_optimization", out_dir=str(salida), overrides=overrides,
    )
    assert res.status == "ok", res.summary
    return res.report.recommendations[0]


def test_la_dispersion_del_plazo_LLEGA_al_motor_por_el_camino_del_agente():
    """El caso de cabecera. Sin esto, el buffer sale como si el proveedor no fallara nunca."""
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        ruta = _serie(raiz)

        base = _por_el_agente(ruta, raiz / "base")
        con = _por_el_agente(ruta, raiz / "con", lead_time_stds={"A": 3.0})

        assert base.lead_time_std == 0.0 and base.lead_time_std_source == "defaulted_zero"
        assert con.lead_time_std == 3.0, "la dispersion declarada no llego al registro"
        assert con.lead_time_std_source == "client_file"
        assert con.safety_stock > base.safety_stock * 5, (
            f"el buffer casi no se movio ({base.safety_stock:.3f} -> {con.safety_stock:.3f}); "
            "el termino sigma_L^2 * mu_d^2 del periodo de riesgo es el dominante aca, "
            "asi que si no se mueve es que el dato sigue sin llegar"
        )
        # Y el disparador se mueve con el, porque es `mu_x + buffer`.
        assert con.reorder_point > base.reorder_point


def test_una_dispersion_declarada_en_CERO_no_es_lo_mismo_que_ninguna():
    """La regla de toda esta rama, en este campo.

    Un proveedor que de verdad nunca falla tiene sigma_L = 0 MEDIDO, y eso es un hecho
    que el entregable puede citar. Que el numero coincida con el default no lo vuelve un
    default: lo que los separa es `lead_time_std_source`, y sin esa distincion el
    reporte no puede decir si el 95% descansa en una medicion o en una suposicion.
    """
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        ruta = _serie(raiz)

        sin = _por_el_agente(ruta, raiz / "sin")
        cero = _por_el_agente(ruta, raiz / "cero", lead_time_stds={"A": 0.0})

        assert sin.lead_time_std == cero.lead_time_std == 0.0
        assert sin.safety_stock == pytest.approx(cero.safety_stock), (
            "el NUMERO tiene que ser el mismo: cero dispersion es cero dispersion"
        )
        assert sin.lead_time_std_source == "defaulted_zero"
        assert cero.lead_time_std_source == "client_file", (
            "una dispersion medida en cero no puede reportarse como un default; es la "
            "misma regla por la que `skipped_zero_demand` conserva sus ceros"
        )
