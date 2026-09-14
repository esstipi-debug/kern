"""La memoria de ciclo tiene duenio: cada cliente nombra su particion, y solo la suya.

Este archivo cubre el agujero que quedo despues de hacer la memoria opt-in. Cerrar
la fuga fue correcto -- ``forecast_job.run`` sin ``store`` ya no resuelve la
particion compartida -- pero NINGUN llamador de produccion pasaba ``store=`` ni lo
exponia por ``params``, asi que toda corrida real imprimia "no store: cycle memory
off" y no congelaba nada. La capacidad existia, estaba testeada, y no era
alcanzable.

Lo que se prueba aca:

- los TRES llamadores de produccion (``jobs/integrated_plan.py``,
  ``jobs/seo_priority.py``, ``scm_agent/tools.py``) llegan de verdad a la memoria
  cuando el caller nombra su particion por ``params['state_client_id']``;
- DOS clientes distintos, cada uno con su store nombrado, corriendo en el MISMO
  proceso, no se ven el ciclo del otro. Las corridas se intercalan (A1, B1, A2,
  B2) justamente para que compartir particion no pueda pasar el test por
  casualidad: sobre una particion unica, el "ciclo anterior" de A2 seria B1;
- sin la clave la memoria sigue apagada y NO cae en la particion compartida: una
  clave ausente que resolviera a un directorio global seria el mismo bug, mudado
  de lugar;
- un ``client_id`` inservible se rechaza, nunca se reescribe ni se degrada a
  "no store" (que seria decirle al cliente una frase falsa: si nombro un store).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import forecast_cycle as fc
from jobs import integrated_plan as ip
from jobs import seo_priority as sp
from nosilo_core.types import JobRequest
from scm_agent import tools as agent_tools
from src.state.store import ClientIdError, StateStore, store_for

_ESTABLE = [100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 97.0, 100.0,
            101.0, 99.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0]
_VOLATIL = [60.0, 140.0, 55.0, 150.0, 70.0, 130.0, 45.0, 160.0,
            80.0, 120.0, 50.0, 155.0, 65.0, 145.0, 75.0, 125.0]

#: MAPE medio que produce cada serie. Muy separados a proposito: son la huella
#: con la que un test distingue "compare contra MI ciclo anterior" de "compare
#: contra el del otro cliente".
_MAPE_ESTABLE = 0.005
_MAPE_VOLATIL = 0.35


def _demand_csv(path: Path, sku: str, serie: list[float]) -> str:
    """Historia de demanda con ``unit_cost``, que es lo que ABC-XYZ pide de mas
    que el pronostico (``jobs.seo_priority`` corre los dos sobre el mismo CSV)."""
    pd.DataFrame([
        {"sku": sku, "period": t, "demand": q, "unit_cost": 5.0}
        for t, q in enumerate(serie)
    ]).to_csv(path, index=False)
    return str(path)


def _stock_csv(path: Path, sku: str) -> str:
    """Un stock que sirve a los dos playbooks: compras (integrated_plan) y E&O
    (seo_priority)."""
    pd.DataFrame([{
        "product_id": sku, "on_hand": 200.0, "unit_cost": 5.0,
        "reorder_point": 20.0, "incoming_po": 0.0,
        "daily_demand": 3.0, "days_since_last_sale": 3.0,
    }]).to_csv(path, index=False)
    return str(path)


@pytest.fixture
def base(tmp_path) -> Path:
    """Raiz de particiones bajo tmp_path: ningun test toca el ``data/state`` real."""
    return tmp_path / "state"


def _params(tmp_path: Path, base: Path, sku: str, client_id: str | None) -> dict:
    params = {"stock_path": _stock_csv(tmp_path / f"stock-{sku}.csv", sku),
              fc.PARAM_BASE_PATH: str(base)}
    if client_id is not None:
        params[fc.PARAM_CLIENT_ID] = client_id
    return params


# ---- el requisito duro: dos clientes en un proceso, sin verse -----------------


def test_dos_clientes_nombrados_no_se_ven_el_ciclo_en_el_mismo_proceso(tmp_path, base):
    """El test que la ronda 1 no tenia, y por eso el bug entro.

    Las cuatro corridas van INTERCALADAS (acme, globex, acme, globex) sobre el
    mismo proceso y la misma raiz de particiones. Si las dos compartieran
    directorio, el ciclo anterior de la segunda corrida de acme seria el de
    globex -- y la huella lo delata, porque acme corre sobre la serie estable
    (MAPE ~0.5%) y globex sobre la volatil (~35%).
    """
    acme = _params(tmp_path, base, "SKU-ACME", "acme")
    globex = _params(tmp_path, base, "SKU-GLOBEX", "globex")
    acme_csv = _demand_csv(tmp_path / "acme.csv", "SKU-ACME", _ESTABLE)
    globex_csv = _demand_csv(tmp_path / "globex.csv", "SKU-GLOBEX", _VOLATIL)

    acme_1 = ip.prepare(acme_csv, acme)["forecast_report"]
    globex_1 = ip.prepare(globex_csv, globex)["forecast_report"]
    acme_2 = ip.prepare(acme_csv, acme)["forecast_report"]
    globex_2 = ip.prepare(globex_csv, globex)["forecast_report"]

    # Cada uno congelo un ciclo propio, y ninguno vio nada en su primera corrida.
    assert acme_1.cycle_id is not None and globex_1.cycle_id is not None
    assert acme_1.previous_cycle_id is None and acme_1.no_baseline_reason == "first cycle"
    assert globex_1.previous_cycle_id is None and globex_1.no_baseline_reason == "first cycle"

    # Y cada segundo ciclo compara contra el PROPIO anterior, no contra el del otro.
    assert acme_2.previous_cycle_id == acme_1.cycle_id
    assert globex_2.previous_cycle_id == globex_1.cycle_id
    assert acme_2.previous_mean_mape == pytest.approx(_MAPE_ESTABLE, abs=0.05)
    assert globex_2.previous_mean_mape == pytest.approx(_MAPE_VOLATIL, abs=0.05)

    # La comparacion per-SKU tampoco cruza: cada report solo conoce su propio SKU.
    assert {s.name for s in acme_2.skus} == {"SKU-ACME"}
    assert acme_2.skus[0].previous_mape is not None
    assert "SKU-GLOBEX" not in acme_2.cycle_comparison
    assert globex_2.skus[0].previous_mape is not None
    assert "SKU-ACME" not in globex_2.cycle_comparison


def test_las_dos_particiones_son_dos_archivos_distintos_en_disco(tmp_path, base):
    """El aislamiento es fisico (un SQLite por cliente), no un WHERE que alguien
    pueda olvidarse de escribir -- es el motivo por el que ``store_for`` parte por
    directorio. Se comprueba sobre el store, no sobre el nombre del directorio."""
    acme_csv = _demand_csv(tmp_path / "acme.csv", "SKU-ACME", _ESTABLE)
    globex_csv = _demand_csv(tmp_path / "globex.csv", "SKU-GLOBEX", _VOLATIL)

    ip.prepare(acme_csv, _params(tmp_path, base, "SKU-ACME", "acme"))
    ip.prepare(globex_csv, _params(tmp_path, base, "SKU-GLOBEX", "globex"))

    filas_acme = store_for("acme", base_path=base).list_records("forecast")
    filas_globex = store_for("globex", base_path=base).list_records("forecast")

    assert len(filas_acme) == 1 and len(filas_globex) == 1
    assert filas_acme[0].cycle_id != filas_globex[0].cycle_id
    # Y la particion sin nombre (la raiz) quedo sin una sola fila de nadie.
    assert StateStore(base).list_records("forecast") == []


# ---- los tres llamadores de produccion llegan a la memoria --------------------


def test_integrated_plan_congela_y_compara_cuando_el_caller_nombra_su_store(tmp_path, base):
    """Antes de este arreglo ``prepare`` no pasaba ``store=`` ni lo exponia, asi
    que toda corrida real reportaba "no store: cycle memory off"."""
    params = _params(tmp_path, base, "SKU-A", "acme")
    csv = _demand_csv(tmp_path / "demand.csv", "SKU-A", _ESTABLE)

    primera = ip.prepare(csv, params)["forecast_report"]
    segunda = ip.prepare(_demand_csv(tmp_path / "d2.csv", "SKU-A", _VOLATIL), params)["forecast_report"]

    assert primera.cycle_id is not None
    assert segunda.previous_cycle_id == primera.cycle_id
    assert segunda.mean_mape_vs_previous > 0.0          # el error subio: empeoro
    assert "no store" not in segunda.cycle_comparison
    assert "vs cycle" in segunda.cycle_comparison


def test_seo_priority_congela_y_compara_cuando_el_caller_nombra_su_store(tmp_path, base):
    """``seo_priority`` no devuelve el report entero, pero la comparacion viaja
    per-SKU en ``SkuForecast.previous_mape``, que es lo que el plan de accion
    cruza."""
    params = _params(tmp_path, base, "SKU-A", "acme")
    csv = _demand_csv(tmp_path / "demand.csv", "SKU-A", _ESTABLE)

    sp.prepare(csv, params)
    segunda = sp.prepare(csv, params)

    assert store_for("acme", base_path=base).list_records("outcomes")
    assert segunda["forecasts"][0].previous_mape is not None
    assert segunda["forecasts"][0].mape_change_vs_previous is not None


def test_la_tool_del_agente_congela_y_compara_cuando_params_nombra_el_store(tmp_path, base):
    """El tercer llamador. Su contrato es ``run(payload, params)``, asi que
    ``params`` es el UNICO canal por el que puede llegar una identidad -- por eso
    el opt-in se declara ahi y no como argumento de la funcion."""
    tool = agent_tools.forecast_tool()
    csv = _demand_csv(tmp_path / "demand.csv", "SKU-A", _ESTABLE)
    params = {fc.PARAM_CLIENT_ID: "acme", fc.PARAM_BASE_PATH: str(base)}
    request = JobRequest(brief="forecast", data_path=csv, params=params)

    primera = tool.run(tool.prepare(request, None).payload, params).report
    segunda = tool.run(tool.prepare(request, None).payload, params).report

    assert primera.cycle_id is not None
    assert segunda.previous_cycle_id == primera.cycle_id
    assert segunda.no_baseline_reason == ""
    assert agent_tools.forecast_job.verify(segunda) == []


# ---- sin la clave: apagado, y NUNCA la particion compartida -------------------


def test_sin_la_clave_la_memoria_sigue_apagada_y_no_escribe_nada(tmp_path, base):
    """Una clave ausente NO puede resolver a un directorio global: seria el mismo
    bug mudado desde el argumento por defecto de ``run()`` hasta aca."""
    csv = _demand_csv(tmp_path / "demand.csv", "SKU-A", _ESTABLE)

    report = ip.prepare(csv, _params(tmp_path, base, "SKU-A", None))["forecast_report"]

    assert report.no_baseline_reason == "no store: cycle memory off"
    assert report.cycle_id is None
    assert not base.exists()                            # ni siquiera se creo la raiz


def test_ninguna_corrida_sin_clave_resuelve_la_particion_compartida(tmp_path, base, monkeypatch):
    """El filo explicito: ``default_store()`` no se llama nunca desde este camino,
    ni con la clave puesta ni sin ella."""
    import src.state.store as store_module

    monkeypatch.setattr(
        store_module, "default_store",
        lambda: pytest.fail("un llamador de produccion resolvio default_store()"),
    )
    csv = _demand_csv(tmp_path / "demand.csv", "SKU-A", _ESTABLE)

    assert ip.prepare(csv, _params(tmp_path, base, "SKU-A", None))["forecast_report"].cycle_id is None
    assert ip.prepare(csv, _params(tmp_path, base, "SKU-A", "acme"))["forecast_report"].cycle_id is not None


# ---- el resolvedor, sin correr el pronostico ----------------------------------


@pytest.mark.parametrize("params", [None, {}, {"stock_path": "x.csv"}])
def test_params_sin_la_clave_no_resuelve_ningun_store(params):
    assert fc.store_from_params(params) is None


def test_un_client_id_explicitamente_nulo_apaga_la_memoria_en_vez_de_compartirla(base):
    """``store_for(None)`` SI resuelve la particion sin nombre -- ese es su
    contrato. Este canal no la alcanza: por ``params``, ``None`` es apagado. La
    compartida sigue siendo una decision escrita en codigo, no un valor faltante
    en un dict que alguien lleno a mano."""
    assert fc.store_from_params({fc.PARAM_CLIENT_ID: None, fc.PARAM_BASE_PATH: str(base)}) is None
    assert not base.exists()


def test_cada_cliente_nombrado_cae_en_su_propio_subdirectorio(base):
    acme = fc.store_from_params({fc.PARAM_CLIENT_ID: "acme", fc.PARAM_BASE_PATH: str(base)})
    globex = fc.store_from_params({fc.PARAM_CLIENT_ID: "globex", fc.PARAM_BASE_PATH: str(base)})

    assert acme.base_path == base / "acme"
    assert globex.base_path == base / "globex"


@pytest.mark.parametrize("client_id", ["", "   ", "a/b", "..", "acme/../globex"])
def test_un_client_id_inservible_se_rechaza_no_se_degrada_a_sin_memoria(client_id, base):
    """Degradarlo a ``None`` haria que el entregable imprima "no store was named"
    sobre una corrida donde SI se nombro uno. Es la frase falsa que este modulo
    existe para no decir; y reescribirlo mapearia dos clientes a un directorio,
    que es la fuga que la particion existe para evitar."""
    with pytest.raises(ClientIdError):
        fc.store_from_params({fc.PARAM_CLIENT_ID: client_id, fc.PARAM_BASE_PATH: str(base)})

    assert not base.exists()


def test_sin_state_path_las_particiones_cuelgan_del_default_configurado(monkeypatch, tmp_path):
    """``state_path`` es opcional: sin el, la raiz es ``DEFAULT_BASE_PATH``
    (``LINCHPIN_STATE_PATH`` o ``data/state``). Se comprueba con el default
    parcheado, para no crear nada en el arbol real."""
    monkeypatch.setattr(fc, "DEFAULT_BASE_PATH", str(tmp_path / "configurado"))

    store = fc.store_from_params({fc.PARAM_CLIENT_ID: "acme"})

    assert store.base_path == tmp_path / "configurado" / "acme"
