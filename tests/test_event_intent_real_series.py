"""Los dos monitores huerfanos se rutean con la serie REAL (D2 / N1a).

``forecast_error_out_of_band`` y ``lead_time_drift`` se emitian desde PR-5 y no los
consumia nadie. La razon escrita en el encabezado de ``config/event_routing.yaml`` era la
correcta: ni el snapshot de ``forecast`` ni el de ``outcomes`` traian una serie historica
real, y fabricar una con uno o dos numeros agregados para poder correr un tool viola la
regla 7 (procedencia total) y la 14 (ningun cap silencioso).

Lo que cambio no es el criterio, es el dato: desde D1 los jobs de conector escriben la
demanda REALIZADA como ``outcomes``/``actual_qty``, con la FECHA de cada pedido. Este
archivo fija que los dos constructores la leen y no la inventan, y que se NIEGAN cuando
no alcanza:

1. con historia suficiente, ``handle_event`` corre el tool de punta a punta por el
   orquestador real (como el criterio de aceptacion F0 con ``stock_below_rop``);
2. con historia corta, ``EventRoutingError`` nombrando la regla 7;
3. sin ``state_client_id``, ``EventRoutingError`` -- nunca la particion compartida;
4. en ``lead_time_drift``, el plazo medido entra como override y **la politica se mueve**.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import inventory_optimization as io  # noqa: E402
from jobs import state_bridge  # noqa: E402
from scm_agent.event_intent import (  # noqa: E402
    PARAM_BUILDERS,
    EventRoutingError,
    forecast_from_outcomes_event,
    inventory_from_lead_time_drift_event,
    load_routing,
    resolve_route,
)
from scm_agent.events import Event  # noqa: E402
from scm_agent.monitors import lead_time_drift_monitor, run_all_monitors  # noqa: E402
from src.data_loader import lead_time_periods_from_days  # noqa: E402
from src.state import latest, snapshot  # noqa: E402
from src.state.store import store_for  # noqa: E402

SKU = "SKU-1"


def _sembrar(tmp_path, *, puntos: int = 12, cliente: str = "acme", sku: str = SKU):
    """Escribe ``puntos`` semanas de demanda realizada, como lo haria un job de conector."""
    store = store_for(cliente, base_path=str(tmp_path))
    fechas = pd.date_range("2026-01-05", periods=puntos, freq="W-MON")
    state_bridge.record_outcomes(
        store, tool="shopify_replenishment",
        points_by_sku={sku: [(f.date().isoformat(), 10.0 + i) for i, f in enumerate(fechas)]},
    )
    return store


def _evento(tipo: str, payload: dict, sku: str = SKU) -> Event:
    return Event(type=tipo, severity="medium", source="monitors",
                 dedup_key=f"{sku}:{tipo}", sku=sku, payload=payload)


# -- 1. la tabla de ruteo --------------------------------------------------------


def test_los_dos_tipos_huerfanos_ahora_tienen_ruta():
    rutas = load_routing()
    assert len(rutas) == 9
    forecast = rutas["forecast_error_out_of_band"]
    drift = rutas["lead_time_drift"]
    assert (forecast.tool, forecast.param_builder, forecast.autonomy_tier) == (
        "forecast", "forecast_from_outcomes_event", "T2")
    assert (drift.tool, drift.param_builder, drift.autonomy_tier) == (
        "inventory_optimization", "inventory_from_lead_time_drift_event", "T2")


def test_los_dos_constructores_estan_registrados():
    """Una ruta que nombra un builder inexistente es un error de arranque, no de corrida."""
    for ruta in load_routing().values():
        assert ruta.param_builder in PARAM_BUILDERS, ruta.event_type


def test_el_evento_resuelve_su_ruta():
    evento = _evento("forecast_error_out_of_band", {"relative_error": 0.5})
    assert resolve_route(evento, load_routing()).tool == "forecast"


# -- 2. la serie es real, y sale del estado --------------------------------------


def test_el_csv_lleva_un_punto_por_semana_con_su_fecha_real(tmp_path):
    _sembrar(tmp_path, puntos=12)
    params = forecast_from_outcomes_event(
        _evento("forecast_error_out_of_band",
                {"relative_error": 0.5, "state_client_id": "acme", "state_path": str(tmp_path)}))
    df = pd.read_csv(params["data_path"])
    assert list(df.columns) == ["product_id", "date", "quantity"]
    assert len(df) == 12
    assert set(df["product_id"]) == {SKU}
    # Las fechas son las de los pedidos, no un ordinal ni un rango sintetico que
    # terminara hoy: se pueden reconciliar contra el ERP.
    assert df["date"].tolist()[0] == "2026-01-05"
    assert df["quantity"].tolist() == [10.0 + i for i in range(12)]


def test_dos_ciclos_que_se_solapan_no_duplican_puntos(tmp_path):
    """Un ciclo escribe la ventana que leyo, asi que dos consecutivos se pisan. El punto
    mas nuevo gana: es una correccion, no un duplicado."""
    store = _sembrar(tmp_path, puntos=12)
    state_bridge.record_outcomes(
        store, tool="shopify_replenishment",
        points_by_sku={SKU: [("2026-01-05", 99.0)]},
    )
    params = forecast_from_outcomes_event(
        _evento("forecast_error_out_of_band",
                {"state_client_id": "acme", "state_path": str(tmp_path)}))
    df = pd.read_csv(params["data_path"])
    assert len(df) == 12
    assert df.loc[df["date"] == "2026-01-05", "quantity"].item() == 99.0


def test_una_serie_corta_se_niega_nombrando_la_regla(tmp_path):
    """Regla 7: una serie corta no se completa con nada. El constructor de eventos de
    stock SI sintetiza historia y lo declara; aca no hace falta, porque la serie o esta
    o no esta."""
    _sembrar(tmp_path, puntos=3)
    with pytest.raises(EventRoutingError) as exc:
        forecast_from_outcomes_event(
            _evento("forecast_error_out_of_band",
                    {"state_client_id": "acme", "state_path": str(tmp_path)}))
    assert "regla 7" in str(exc.value)
    assert "SKU-1 (3)" in str(exc.value)


def test_un_sku_sin_ningun_punto_tambien_se_niega(tmp_path):
    _sembrar(tmp_path, puntos=12)
    with pytest.raises(EventRoutingError) as exc:
        forecast_from_outcomes_event(
            _evento("forecast_error_out_of_band",
                    {"state_client_id": "acme", "state_path": str(tmp_path)}, sku="OTRO"))
    assert "OTRO (0)" in str(exc.value)


def test_sin_state_client_id_no_se_cae_a_la_particion_compartida(tmp_path):
    _sembrar(tmp_path, puntos=12, cliente=None or "acme")
    with pytest.raises(EventRoutingError) as exc:
        forecast_from_outcomes_event(_evento("forecast_error_out_of_band", {}))
    mensaje = str(exc.value)
    assert "state_client_id" in mensaje and "no hay serie real que leer" in mensaje


def test_un_evento_sin_sku_no_tiene_de_quien_leer(tmp_path):
    evento = Event(type="forecast_error_out_of_band", severity="medium", source="m",
                   dedup_key="x", sku="", payload={"state_client_id": "acme"})
    with pytest.raises(EventRoutingError) as exc:
        forecast_from_outcomes_event(evento)
    assert "no sku" in str(exc.value)


# -- 3. lead_time_drift: el plazo medido mueve la politica ------------------------


def test_el_drift_entra_como_override_de_lead_time(tmp_path):
    _sembrar(tmp_path, puntos=12)
    params = inventory_from_lead_time_drift_event(
        _evento("lead_time_drift",
                {"baseline_lead_time_days": 10.0, "recent_lead_time_days": 26.0,
                 "state_client_id": "acme", "state_path": str(tmp_path)}))
    # EN PERIODOS, no en dias: `lead_times` reemplaza `lead_time_periods`. 26 dias a 52
    # periodos por anio son 3,70 periodos; pasar 26 inflaria el periodo de riesgo por
    # siete sobre una lectura correcta.
    assert params["overrides"]["lead_times"] == {SKU: pytest.approx(26.0 * 52 / 365)}
    assert params["overrides"]["periods_per_year"] == 52.0
    assert "10.0d -> 26.0d" in params["brief"]


def test_el_plazo_cruza_de_dias_a_periodos_con_la_regla_del_motor(tmp_path):
    """La conversion es la de ``src/data_loader.py``, no una copia. Si el motor cambia
    de convencion, este constructor cambia con el en vez de quedar mintiendo."""
    from src.data_loader import lead_time_periods_from_days

    _sembrar(tmp_path, puntos=12)
    params = inventory_from_lead_time_drift_event(
        _evento("lead_time_drift",
                {"recent_lead_time_days": 21.0, "periods_per_year": 12.0,
                 "state_client_id": "acme", "state_path": str(tmp_path)}))
    assert params["overrides"]["lead_times"][SKU] == lead_time_periods_from_days(21.0, 12.0)
    assert params["overrides"]["periods_per_year"] == 12.0


def test_el_override_mueve_la_politica_de_verdad(tmp_path):
    """El VALOR SE MUEVE: no alcanza con que el parametro viaje. Un plazo mas largo
    alarga el periodo de riesgo, y con el el punto de reorden y el stock de seguridad."""
    _sembrar(tmp_path, puntos=12)
    params = inventory_from_lead_time_drift_event(
        _evento("lead_time_drift",
                {"baseline_lead_time_days": 10.0, "recent_lead_time_days": 26.0,
                 "state_client_id": "acme", "state_path": str(tmp_path)}))
    demanda = pd.read_csv(params["data_path"], parse_dates=["date"])
    base = {SKU: lead_time_periods_from_days(10.0, 52.0)}
    sin = io.run(demanda, lead_times=base, periods_per_year=52.0)
    con = io.run(demanda, **params["overrides"])
    rop_sin = next(r for r in sin.recommendations if r.product_id == SKU).reorder_point
    rop_con = next(r for r in con.recommendations if r.product_id == SKU).reorder_point
    assert rop_con > rop_sin, f"{rop_sin} -> {rop_con}"


def test_un_drift_sin_el_plazo_medido_no_se_rutea(tmp_path):
    _sembrar(tmp_path, puntos=12)
    with pytest.raises(EventRoutingError) as exc:
        inventory_from_lead_time_drift_event(
            _evento("lead_time_drift",
                    {"baseline_lead_time_days": 10.0, "state_client_id": "acme",
                     "state_path": str(tmp_path)}))
    assert "recent_lead_time_days" in str(exc.value)


def test_el_destino_es_la_politica_y_no_el_plan_de_ventas():
    """La investigacion sugeria ``sop`` con un ``promo_lift`` derivado del drift. Un lift
    de promocion es DEMANDA, no plazo: usarlo de proxy seria el cap silencioso de la
    regla 14. Queda escrito en la tabla para que el dia que alguien lo mueva sea a
    proposito."""
    assert load_routing()["lead_time_drift"].tool == "inventory_optimization"


# -- 4. el monitor y el ruteo, enganchados ---------------------------------------


def test_el_monitor_pone_la_particion_en_el_evento(tmp_path):
    """Sin esto el evento llega al constructor sin poder decir de quien es su serie."""
    store = store_for("acme", base_path=str(tmp_path))
    for ciclo, dias in (("1", 10.0), ("2", 16.0)):
        snapshot("outcomes", pd.DataFrame([
            {"product_id": SKU, "tool": "odoo_replenishment", "metric": "lead_time_days",
             "value": dias},
        ]), ciclo, store=store)
    historia = [s for s in __import__("src.state", fromlist=["history"]).history(
        "outcomes", store=store)]
    eventos = lead_time_drift_monitor(historia, client_id="acme")
    assert len(eventos) == 1
    assert eventos[0].payload["state_client_id"] == "acme"
    assert eventos[0].payload["recent_lead_time_days"] == 16.0


def test_sin_client_id_el_evento_sale_igual_pero_sin_particion(tmp_path):
    """El monitor no se calla por no saber el cliente: emite, y es el ruteo el que se
    niega con la razon. Un evento perdido es peor que uno no ruteable."""
    store = store_for("acme", base_path=str(tmp_path))
    for ciclo, dias in (("1", 10.0), ("2", 16.0)):
        snapshot("outcomes", pd.DataFrame([
            {"product_id": SKU, "tool": "odoo_replenishment", "metric": "lead_time_days",
             "value": dias},
        ]), ciclo, store=store)
    from src.state import history

    eventos = lead_time_drift_monitor(history("outcomes", store=store))
    assert len(eventos) == 1
    assert "state_client_id" not in eventos[0].payload


def test_una_corrida_completa_de_monitores_propaga_la_particion(tmp_path):
    """El camino real: ``run_all_monitors(client_id=...)``."""
    store = store_for("acme", base_path=str(tmp_path))
    for ciclo in ("1", "2"):
        snapshot("outcomes", pd.DataFrame([
            {"product_id": SKU, "tool": "odoo_replenishment", "metric": "lead_time_days",
             "value": 10.0 if ciclo == "1" else 16.0},
        ]), ciclo, store=store)
    eventos = run_all_monitors(store=store, client_id="acme")
    drift = [e for e in eventos if e.type == "lead_time_drift"]
    assert drift and drift[0].payload["state_client_id"] == "acme"


def test_el_estado_escrito_por_un_job_alcanza_para_rutear(tmp_path):
    """La cadena entera, sin fixtures de estado escritos a mano: un job de conector
    escribe la demanda realizada, y el evento del monitor se rutea sobre ESA serie."""
    from nosilo_core.registry import JobRequest
    from scm_agent.tools import build_default_registry
    from src.connectors.shopify import InMemoryShopifyCommerce

    loc = "gid://shopify/Location/1"
    variante = {"id": "gid://shopify/ProductVariant/P1", "sku": "P1", "displayName": "P1",
                "price": "12.00",
                "inventoryItem": {"id": "gid://shopify/InventoryItem/P1",
                                  "unitCost": {"amount": "5.00"}}}
    ordenes = [
        {"id": f"gid://shopify/Order/{d}", "createdAt": f"2026-01-{d:02d}T10:00:00Z",
         "lineItems": {"edges": [{"node": {
             "sku": "P1", "quantity": 5,
             "originalUnitPriceSet": {"shopMoney": {"amount": "12.00"}}}}]}}
        for d in range(1, 15)
    ]
    rpc = InMemoryShopifyCommerce(variants=[variante], orders=ordenes,
                                  stock={"P1": {loc: 30}}, incoming={"P1": {loc: 0}})
    params = {"shopify_rpc": rpc, "state_client_id": "acme", "state_path": str(tmp_path)}
    tool = build_default_registry().get("shopify_replenishment")
    prep = tool.prepare(JobRequest(brief="x", params=params), None)
    tool.run(prep.payload, params)

    assert latest("outcomes", store=store_for("acme", base_path=str(tmp_path))) is not None
    construidos = forecast_from_outcomes_event(
        _evento("forecast_error_out_of_band",
                {"state_client_id": "acme", "state_path": str(tmp_path)}, sku="P1"))
    df = pd.read_csv(construidos["data_path"])
    assert len(df) == 14 and set(df["product_id"]) == {"P1"}


# -- 5. punta a punta, por el orquestador real ------------------------------------


def test_un_evento_de_error_de_pronostico_corre_el_tool_forecast_entero(tmp_path, monkeypatch):
    """El mismo criterio con el que se acepto ``stock_below_rop`` en F0: el evento
    sintetico se rutea por el ``config/event_routing.yaml`` REAL, corre el tool por el
    orquestador REAL y sale un ``JobResult`` con la compuerta de QA pasada.

    Lo que cambia respecto de F0: la serie sobre la que corre no la fabrica el
    constructor, la leyo del estado.
    """
    from nosilo_core import llm
    from nosilo_core.types import STATUS_OK
    from scm_agent import event_intent as event_intent_module
    from scm_agent import tools
    from scm_agent.event_intent import DEFAULT_ROUTING_PATH, handle_event
    from scm_agent.orchestrator import Orchestrator

    capturado = {}
    monkeypatch.setattr(event_intent_module, "notify",
                        lambda message, **kw: capturado.setdefault("message", message) or True)
    _sembrar(tmp_path, puntos=16)

    routed = handle_event(
        _evento("forecast_error_out_of_band",
                {"relative_error": 0.62, "state_client_id": "acme",
                 "state_path": str(tmp_path)}),
        routing_path=DEFAULT_ROUTING_PATH,
        orchestrator=Orchestrator(registry=tools.build_default_registry(),
                                  provider=llm.RulesFallback(), clients_root=None),
        out_dir=tmp_path / "out",
    )

    assert routed.route.tool == "forecast"
    assert routed.route.autonomy_tier == "T2"
    assert routed.result.status == STATUS_OK, routed.result.messages
    assert routed.result.qa_issues == []
    assert routed.result.deliverables
    assert "forecast_error_out_of_band" in capturado["message"]


def test_un_evento_de_drift_corre_inventory_optimization_entero(tmp_path, monkeypatch):
    from nosilo_core import llm
    from nosilo_core.types import STATUS_OK
    from scm_agent import event_intent as event_intent_module
    from scm_agent import tools
    from scm_agent.event_intent import DEFAULT_ROUTING_PATH, handle_event
    from scm_agent.orchestrator import Orchestrator

    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kw: True)
    _sembrar(tmp_path, puntos=16)

    routed = handle_event(
        _evento("lead_time_drift",
                {"baseline_lead_time_days": 10.0, "recent_lead_time_days": 26.0,
                 "state_client_id": "acme", "state_path": str(tmp_path)}),
        routing_path=DEFAULT_ROUTING_PATH,
        orchestrator=Orchestrator(registry=tools.build_default_registry(),
                                  provider=llm.RulesFallback(), clients_root=None),
        out_dir=tmp_path / "out",
    )
    assert routed.route.tool == "inventory_optimization"
    assert routed.result.status == STATUS_OK, routed.result.messages
    assert routed.result.qa_issues == []


def test_demanda_y_plazos_van_en_EL_MISMO_ciclo_de_outcomes(tmp_path):
    """La interaccion sutil que rompe al monitor si se escribe en dos ciclos.

    ``lead_time_drift_monitor`` compara los DOS snapshots mas recientes de ``outcomes``.
    Si una corrida escribiera la demanda en un ciclo y los plazos en otro, el snapshot de
    arriba seria el de demanda, su mapa de plazos saldria vacio, y el monitor no podria
    disparar nunca -- con los plazos medidos, escritos, y ahi. Es la misma forma que ya
    usa el puente del marcador, y ahora por la misma razon.
    """
    from src.state import history

    store = store_for("acme", base_path=str(tmp_path))
    state_bridge.record_outcomes(
        store, tool="odoo_replenishment",
        points_by_sku={SKU: [("2026-01-05", 10.0), ("2026-01-12", 12.0)]},
        lead_times_by_sku={SKU: [10.0, 10.0]},
    )
    state_bridge.record_outcomes(
        store, tool="odoo_replenishment",
        points_by_sku={SKU: [("2026-01-19", 11.0)]},
        lead_times_by_sku={SKU: [16.0, 16.0]},
    )
    snaps = history("outcomes", store=store)
    assert len(snaps) == 2, "una corrida = un ciclo, no dos"
    for snap in snaps:
        metricas = set(snap.payload["metric"])
        assert metricas == {"actual_qty", "lead_time_days"}, metricas

    eventos = lead_time_drift_monitor(snaps, client_id="acme")
    assert [e.payload["recent_lead_time_days"] for e in eventos] == [16.0]


def test_una_corrida_sin_plazos_medidos_escribe_igual_la_demanda(tmp_path):
    """El opt-in de plazos esta apagado por defecto (``receipts()`` es una lectura cara).
    Eso no puede impedir que la demanda se escriba."""
    store = store_for("acme", base_path=str(tmp_path))
    nota = state_bridge.record_outcomes(
        store, tool="shopify_replenishment",
        points_by_sku={SKU: [("2026-01-05", 10.0)]},
    )
    assert nota.recorded and "demand outcomes" in nota.text
    assert "lead time" not in nota.text
    assert set(latest("outcomes", store=store).payload["metric"]) == {"actual_qty"}
